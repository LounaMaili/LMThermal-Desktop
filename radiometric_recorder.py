"""Bounded live radiometric sampler, timeline journal and background chunk writer."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import csv
import os
from pathlib import Path
import queue
import threading
import time

from measurement_logger import create_sample, serialize_row, utc_now
from radiometric_recording import (FORMAT_ID, FORMAT_VERSION, CHUNK_FRAMES, HANDOFF_FRAMES,
                                   RATES, TIMELINE_COLUMNS, ChunkWriter, atomic_json, snapshot_frame)
from radiometric_export import ACCURACY_WARNING
from radiometric_session import FrameObservation


@dataclass(frozen=True)
class RecorderStatus:
    state: str
    duration_s: float
    requested_samples: int
    valid_frames: int
    gaps: int
    dropped: int
    queue_depth: int
    bytes_written: int
    error: str | None
    directory: Path


class RadiometricRecorder:
    """Sampler never waits for compression; a full queue becomes an explicit drop."""

    def __init__(self, directory: Path, *, rate_hz=5, observation=None, roi=None,
                 camera_identity=None, compression=True, chunk_frames=CHUNK_FRAMES):
        if rate_hz not in RATES:
            raise ValueError("Recording rate must be 1, 2, 5, 10 or 25 Hz")
        if type(chunk_frames) is not int or not 1 <= chunk_frames <= CHUNK_FRAMES:
            raise ValueError("Chunk frame count must be 1..16")
        if observation is not None and not isinstance(observation, FrameObservation):
            raise ValueError("Radiometric recording requires live observations")
        self.directory = Path(directory).absolute()
        self.directory.mkdir()  # Exclusive ownership; never overwrite an existing bundle.
        (self.directory/"chunks").mkdir()
        self.rate_hz = rate_hz
        self.started = time.monotonic()
        self._duration = 0.0
        self._lock = threading.Lock()
        self._journal_lock = threading.Lock()
        self._latest = (observation, roi)
        self._sampled = None
        self._requested = self._accepted = self._gaps = self._dropped = 0
        self._error = None
        self._reason = "operator_stop"
        self._state = "recording"
        self._stop = threading.Event()
        self._sampler_done = threading.Event()
        self.handoff = queue.Queue(maxsize=HANDOFF_FRAMES)
        self.writer = ChunkWriter(self.directory/"chunks", compression=compression, chunk_frames=chunk_frames)
        self.manifest = {"format": FORMAT_ID, "version": FORMAT_VERSION, "completed": False,
                         "created_at_utc": utc_now(), "camera_identity": camera_identity,
                         "native_width_px": 384, "native_height_px": 288,
                         "orientation": "native_camera_coordinates", "rate_hz": rate_hz,
                         "units": {"temperature": "degree_Celsius", "elapsed": "second", "coordinates": "native_pixel"},
                         "chunk_frames": chunk_frames, "handoff_frames": HANDOFF_FRAMES,
                         "compression": "npz_deflate" if compression else "npz_stored",
                         "transport_preserved": False, "accuracy_warning": ACCURACY_WARNING,
                         "requested_samples": 0, "valid_frames": 0, "gap_samples": 0,
                         "dropped_samples": 0, "uncommitted_frames": 0, "chunks": [], "duration_s": 0.0}
        atomic_json(self.directory/"manifest.json", self.manifest)
        self._timeline = (self.directory/"timeline.csv").open("x", newline="", encoding="utf-8")
        self._csv = csv.DictWriter(self._timeline, fieldnames=TIMELINE_COLUMNS)
        self._csv.writeheader(); self._timeline.flush()
        self._sampler_thread = threading.Thread(target=self._sample_loop, name="radiometric-sampler", daemon=True)
        self._writer_thread = threading.Thread(target=self._write_loop, name="radiometric-chunks", daemon=True)
        self._writer_thread.start(); self._sampler_thread.start()

    def update_latest(self, observation, roi):
        if observation is not None and not isinstance(observation, FrameObservation):
            raise ValueError("Offline captures cannot be recorded")
        with self._lock:
            self._latest = (observation, roi)

    @property
    def status(self):
        with self._lock:
            requested, gaps, dropped = self._requested, self._gaps, self._dropped
        return RecorderStatus(self._state, time.monotonic()-self.started if self._state == "recording" else self._duration,
                              requested, sum(c["frames"] for c in self.writer.chunks), gaps, dropped,
                              self.handoff.qsize(), self.writer.bytes_written, self._error, self.directory)

    def _manifest(self, completed=False):
        status = self.status
        self.manifest.update(completed=completed, requested_samples=status.requested_samples,
                             valid_frames=status.valid_frames, gap_samples=status.gaps,
                             dropped_samples=status.dropped, uncommitted_frames=self._accepted-status.valid_frames,
                             chunks=list(self.writer.chunks), duration_s=status.duration_s, error=self._error,
                             stop_reason=self._reason)
        if completed or self._sampler_done.is_set():
            self.manifest["stopped_at_utc"] = utc_now()
        atomic_json(self.directory/"manifest.json", self.manifest)

    def record_instant(self, sequence, now, *, missed=False):
        """One sampler-thread instant; exposed for deterministic cadence/backpressure tests."""
        with self._lock:
            observation, roi = self._latest
        stamp = datetime.now(timezone.utc)
        elapsed = now-self.started
        if missed:
            elapsed = sequence/self.rate_hz
            stamp -= timedelta(seconds=max(0, now-self.started-elapsed))
        row = create_sample(None if missed else observation, None if missed else roi,
                            timestamp_utc=stamp.isoformat(), elapsed_s=elapsed, sequence=sequence,
                            fresh=observation is None or observation is not self._sampled)
        if not missed:
            self._sampled = observation
        base = serialize_row(row)
        extra = {"scheduled_elapsed_s": sequence/self.rate_hz, "dropped": "false", "stored": "false",
                 "chunk": "", "frame_index": ""}
        if missed:
            base.update(state="not_observed", status="sampler_missed_deadline")
            extra["dropped"] = "true"
        elif row["measurement_valid"]:
            # This sampler is the only producer; the consumer can only free slots.
            if self.handoff.full():
                base["status"] = "writer_overload"
                extra["dropped"] = "true"
            else:
                frame = snapshot_frame(observation, row)
                extra.update(stored="true", chunk=f"chunk-{self._accepted//self.writer.chunk_frames+1:06d}.npz",
                             frame_index=self._accepted % self.writer.chunk_frames)
                self._accepted += 1
        base.update(extra)
        with self._journal_lock:
            self._csv.writerow(base)
            self._timeline.flush()
        with self._lock:
            self._requested += 1
            self._dropped += int(extra["dropped"] == "true")
            self._gaps += int(not row["measurement_valid"] and extra["dropped"] == "false")
        if extra["stored"] == "true":
            # Journal first: a published matrix always has an already-written row.
            self.handoff.put_nowait(frame)
        return base

    def _sample_loop(self):
        sequence = 1
        try:
            while not self._stop.wait(max(0, self.started+sequence/self.rate_hz-time.monotonic())):
                now = time.monotonic()
                due = max(sequence, int((now-self.started)*self.rate_hz))
                while sequence < due:
                    self.record_instant(sequence, now, missed=True)
                    sequence += 1
                self.record_instant(sequence, now)
                sequence += 1
        except Exception as exc:
            self._error = str(exc); self._reason = "sampler_error"; self._stop.set()
        finally:
            with self._journal_lock:
                try:
                    self._timeline.flush()
                    os.fsync(self._timeline.fileno())
                except OSError as exc:
                    self._error = str(exc)
                finally:
                    try:
                        self._timeline.close()
                    finally:
                        self._sampler_done.set()

    def _write_loop(self):
        try:
            while True:
                try:
                    frame = self.handoff.get(timeout=.1)
                except queue.Empty:
                    if self._sampler_done.is_set():
                        break
                    continue
                try:
                    if self.writer.append(frame) is not None:
                        # Persist the timeline before the manifest commits this chunk.
                        with self._journal_lock:
                            if not self._timeline.closed:
                                os.fsync(self._timeline.fileno())
                        self._manifest()
                finally:
                    self.handoff.task_done()
            self.writer.commit()
        except Exception as exc:
            self._error = str(exc); self._reason = "writer_error"; self._stop.set()
            self._sampler_done.wait()
        finally:
            self._duration = time.monotonic()-self.started
            try:
                self._manifest(completed=self._error is None)
            except Exception as exc:
                self._error = str(exc)
            self._state = "error" if self._error else "complete"

    def stop(self, reason="operator_stop", timeout=10):
        if not self._stop.is_set():
            self._reason = reason
        self._stop.set()
        deadline = time.monotonic()+timeout
        for thread in (self._sampler_thread, self._writer_thread):
            thread.join(max(0, deadline-time.monotonic()))
        if self._writer_thread.is_alive() or self._sampler_thread.is_alive():
            raise TimeoutError("Radiometric recording is still finalizing")
        if self._error:
            raise OSError(self._error)
        return self.status
