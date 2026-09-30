"""Stored-matrix timeline inspection, bounded chunk caching and monotonic playback."""

from bisect import bisect_right
from collections import OrderedDict, deque
from dataclasses import dataclass
import json
from pathlib import Path
import time

from radiometric_capture import save_rendered_image
from radiometric_recording import FramePayload, Recording, open_recording
from roi_measurement import NativeROI


@dataclass(frozen=True)
class TimelineEntry:
    index: int
    sequence: int
    timestamp_utc: str
    elapsed_s: float
    state: str
    reason: str
    measurement_valid: bool
    dropped: bool
    kind: str
    chunk: str | None
    frame_index: int | None
    roi: NativeROI | None


def recorded_roi(summary):
    values = [summary[k] for k in ('roi_x1_px', 'roi_y1_px', 'roi_x2_px', 'roi_y2_px')]
    return None if all(v in (None, '') for v in values) else NativeROI(*(int(v) for v in values))


@dataclass(frozen=True)
class PlaybackFrame:
    """A valid committed sample; owned read-only matrices never enter thermometry."""

    source_path: Path
    payload: FramePayload

    @property
    def raw14(self): return self.payload.raw14
    @property
    def temperature_c(self): return self.payload.temperature_c
    @property
    def metadata(self): return self.payload.metadata
    @property
    def summary(self): return self.metadata['summary']
    @property
    def roi(self): return recorded_roi(self.summary)
    @property
    def high_xy(self): return self.summary['high_x_px'], self.summary['high_y_px']
    @property
    def low_xy(self): return self.summary['low_x_px'], self.summary['low_y_px']
    @property
    def high_c(self): return self.summary['high_c']
    @property
    def low_c(self): return self.summary['low_c']
    @property
    def literal_center_index(self): return self.summary['literal_center_raw14']
    @property
    def literal_center_c(self): return self.summary['literal_center_c']
    @property
    def trailer_center_index(self): return self.summary['trailer_center_raw14']
    @property
    def trailer_center_c(self): return self.summary['trailer_center_c']


def save_playback_png(frame: PlaybackFrame, path, palette, bounds):
    """Reuse the PNG-only exporter, protecting the entire source bundle."""
    root = frame.source_path.parent.resolve()
    if Path(path).resolve().is_relative_to(root):
        raise ValueError('Rendered PNG must be saved outside the source recording')
    return save_rendered_image(frame, Path(path), palette, bounds)


class PlaybackModel:
    """Navigation is independent of loading; only frame() touches the chunk cache."""

    def __init__(self, recording: Recording, *, cache_chunks=2):
        if type(cache_chunks) is not int or not 1 <= cache_chunks <= 2:
            raise ValueError('Playback cache must hold one or two chunks')
        self.recording = recording
        self.cache_limit = cache_chunks
        self._chunks = {c['file']: c for c in recording.manifest['chunks']}
        entries = []
        for index, row in enumerate(recording.timeline):
            stored = row['stored'] == 'true'
            committed = stored and row['chunk'] in self._chunks
            dropped = row['dropped'] == 'true'
            kind = 'valid' if committed else 'drop' if dropped else 'uncommitted' if stored else 'gap'
            entries.append(TimelineEntry(index, int(row['sequence']), row['timestamp_utc'], float(row['elapsed_s']),
                                         row['state'], row['status'], row['measurement_valid'] == 'true', dropped,
                                         kind, row['chunk'] if committed else None,
                                         int(row['frame_index']) if committed else None, recorded_roi(row)))
        self.entries = tuple(entries)
        self._times = tuple(e.elapsed_s for e in entries)
        self._cache = OrderedDict()
        self.load_times_s = deque(maxlen=128)
        self.frame_times_s = deque(maxlen=128)
        self.chunk_loads = 0
        self.index = 0
        self.playing = False
        self.speed = 1.0
        self._anchor_time = self._anchor_elapsed = 0.0

    @classmethod
    def open(cls, directory, **kwargs):
        return cls(open_recording(Path(directory)), **kwargs)

    @property
    def completed(self): return self.recording.manifest['completed']
    @property
    def duration_s(self): return self._times[-1] if self._times else 0.0
    @property
    def cache_size(self): return len(self._cache)
    @property
    def cache_bytes(self):
        return sum(sum(a.nbytes for a in arrays.values()) for arrays, _ in tuple(self._cache.values()))
    @property
    def entry(self): return self.entries[self.index] if self.entries else None

    def frame(self, index=None):
        """Load on demand, rechecking integrity at each cold chunk read."""
        started = time.perf_counter()
        if not self.entries:
            return None
        entry = self.entries[self.index if index is None else index]
        if entry.kind != 'valid':
            return None
        name = entry.chunk
        if name not in self._cache:
            # Evict before loading to avoid retaining three chunks at a boundary.
            if len(self._cache) == self.cache_limit:
                self._cache.popitem(last=False)
            load_started = time.perf_counter()
            self._cache[name] = self.recording.load_chunk(name)
            self.load_times_s.append(time.perf_counter() - load_started)
            self.chunk_loads += 1
        self._cache.move_to_end(name)
        arrays, metadata = self._cache[name]
        i = entry.frame_index
        payload = FramePayload(entry.sequence, arrays['raw14'][i].tobytes(),
                               arrays['temperature_c'][i].tobytes(), json.dumps(metadata[i], allow_nan=False))
        result = PlaybackFrame(self.recording.directory/'manifest.json', payload)
        self.frame_times_s.append(time.perf_counter() - started)
        return result

    def seek(self, index):
        self.pause()
        if self.entries:
            self.index = max(0, min(int(index), len(self.entries)-1))
        return self.entry

    def step(self, delta): return self.seek(self.index + delta)

    def play(self, now=None):
        if not self.entries:
            return
        if self.index == len(self.entries)-1:
            self.index = 0
        self._anchor_time = time.monotonic() if now is None else now
        self._anchor_elapsed = self.entries[self.index].elapsed_s
        self.playing = True

    def pause(self): self.playing = False

    def set_speed(self, speed, now=None):
        if speed not in (.5, 1, 2, 4):
            raise ValueError('Playback speed must be 0.5, 1, 2 or 4')
        now = time.monotonic() if now is None else now
        if self.playing:
            self._anchor_elapsed += (now-self._anchor_time)*self.speed
            self._anchor_time = now
        self.speed = speed

    def advance(self, now=None):
        """Select the latest elapsed-time entry; never enqueue catch-up renders."""
        if self.playing:
            now = time.monotonic() if now is None else now
            target = self._anchor_elapsed + max(0, now-self._anchor_time)*self.speed
            self.index = max(0, min(bisect_right(self._times, target)-1, len(self.entries)-1))
            if target >= self.duration_s:
                self.pause()
        return self.entry
