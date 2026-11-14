"""Read-only indexed LMTR analysis using the pinned independent R3 validator.

Only verified source Float32 values enter analysis. Index navigation and full-file
integrity are separate claims. One decoded CHUNK is retained, never a recording's
matrices. The owned descriptor belongs to the playback worker and must be closed.
"""
from copy import deepcopy
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
import struct
import os
import time
import weakref

import numpy as np
from lmtr_reference import Reader, ContractError
from lmtr_reference.container import inflate
from lmtr_reference.metadata import sha
from offline_measurement import (Geometry, NativePlane, OfflineMeasurement,
                                 freeze, statistics)

# Local UI/navigation refusals, not changes to the frozen wire limits.
MAX_UI_ENTRIES = 1_000_000
MAX_RECOVERY_CHUNKS = 65536
MAX_CACHE_BYTES = 64 * 1024**2


@dataclass(frozen=True)
class LmtrReadLimits:
    """Local CHUNK admission, separate from wire limits and total process RSS.

    The working charge conservatively includes serialized body, all decoded and
    logical bytes, largest stored scratch and 8x metadata. Python object/graphics
    overhead and a displayed owned frame remain separate; this is not an RSS cap.
    """
    decoded_cache_bytes: int = MAX_CACHE_BYTES
    working_bytes: int = 128 * 1024**2

    def __post_init__(self):
        if (type(self.decoded_cache_bytes) is not int or
                not 1 <= self.decoded_cache_bytes <= MAX_CACHE_BYTES or
                type(self.working_bytes) is not int or not 1 <= self.working_bytes <= 512 * 1024**2):
            raise ValueError('Invalid local LMTR resource limits')


class _DesktopReader(Reader):
    """Add pre-allocation local refusals without changing the pinned R3 validator."""
    def __init__(self, path, limits):
        self.limits = limits
        try:
            super().__init__(path)
        except BaseException:
            if hasattr(self, 'file'): self.file.close()
            raise

    def envelope(self, offset):
        info = super().envelope(offset)
        if info['kind'] == 2 and info['length'] - 112 > self.limits.working_bytes:
            raise ContractError('local_resource_limit', 'Desktop CHUNK body budget before body allocation')
        return info

    def decode(self, record):
        # The original metadata checker establishes lengths/features/closure before
        # any source length is used here. Full R3 decode still follows independently.
        meta = self.metadata(record)
        decoded = sum(int(b['decoded']) for b in meta['blocks'])
        logical = sum(int(p['length']) for e in meta['entries'] for p in e.get('payloads', ()))
        stored = max((int(b['stored']) for b in meta['blocks']), default=0)
        metadata_bytes = 4 + struct.unpack_from('<I', record['body'])[0]
        charge = len(record['body']) + decoded + logical + stored + 8 * metadata_bytes
        if decoded > self.limits.decoded_cache_bytes or charge > self.limits.working_bytes:
            raise ContractError('local_resource_limit', 'Desktop CHUNK decoded/working budget before decompression')
        return super().decode(record)


@dataclass(frozen=True)
class LmtrEntry:
    """Native recorder-intake timeline event; gaps never imply synthetic frames."""
    index: int
    sequence: int
    timestamp_utc: str
    elapsed_s: float
    state: str
    reason: str
    kind: str
    roi: object = None


def _seconds_ns(value):
    """Present original integer nanoseconds without rounding them through binary64."""
    value=int(value);sign='-' if value<0 else '';whole,fraction=divmod(abs(value),1_000_000_000)
    return f'{sign}{whole}.{fraction:09d} s'


def format_timing_evidence(value):
    """Compact display of declared policy and actual event time; no inferred sensor FPS/clock mapping."""
    if value is None:return 'Timing unavailable'
    sampling=value.get('sampling')
    label='Sampling: multiple streams / see original metadata'
    if sampling is not None:
        if sampling['mode']=='periodic':
            period=int(sampling['period_ns'])
            rate=str(1_000_000_000//period) if 1_000_000_000%period==0 else f'1000000000/{period}'
            label=f'Requested: {rate} Hz periodic ({period} ns)'
        else:label='Acquisition-following; no requested periodic slots'
    schedule=value.get('scheduled')
    if schedule is not None:
        first,last=schedule['first_slot'],schedule['last_slot']
        label+=f' · slot {first}' if first==last else f' · slots {first}–{last}'
    actual='Recorder intake +'+_seconds_ns(value['timeline_ns'])
    if value.get('receipt_shared_timeline_ns') is not None:
        actual+=' · receipt '+_seconds_ns(value['receipt_shared_timeline_ns'])
    elif value.get('receipt') is not None:
        stamp=value['receipt'].get('stamp')
        actual+=' · receipt '+(str(stamp['ticks'])+' ticks, unaligned clock' if stamp else value['receipt']['status'])
    if value['kind']=='gap':detail=f"{value['category']} · count {value['count'] or 'unknown'} · {value['reason']}"
    elif value.get('measurement') is not None:
        measurement=value['measurement'];detail=measurement['status']+' · '+measurement.get('reason',measurement.get('validity',''))
    else:detail=value['kind']+' · '+value.get('reason','')
    return '\n'.join((label,actual,detail))


class _Entries:
    """Sequence facade for existing slider controls without a duration-sized list."""
    def __init__(self, owner): self.owner = weakref.proxy(owner)
    def __len__(self): return self.owner.entry_count
    def __getitem__(self, index):
        if not isinstance(index, int) or not 0 <= index < len(self): raise IndexError(index)
        return self.owner.describe(index)


class _Times:
    """Bisect-compatible lazy recorder-intake times; original sensor clocks stay metadata."""
    def __init__(self, owner): self.owner = weakref.proxy(owner)
    def __len__(self): return self.owner.entry_count
    def __getitem__(self, index): return self.owner.entries[index].elapsed_s


class LmtrPlaybackModel:
    """Single-worker lazy immutable-source analysis; no camera or thermometry dependency.

    Complete sources verify header/END/navigation on open and every selected CHUNK
    in full before publication. Explicit incomplete recovery verifies its entire
    admitted prefix first; it never converts salvage into a completed recording.
    Cache accounting excludes bounded parsing/decompression scratch and UI frame copies.
    """
    def __init__(self, path, *, recover=False, cancelled=lambda: False, limits=None):
        self.path = Path(path).absolute()
        self.limits = limits or LmtrReadLimits()
        self.reader = _DesktopReader(self.path, self.limits)
        self.cancelled = cancelled
        self._cache = None
        self._recovery_refs = []
        try:
            self._source_identity = self._descriptor_identity()
            self._assert_source_unchanged()
            self.report = deepcopy(self.reader.check(full=recover, recover=recover, cancelled=cancelled))
            self.recovered = self.report['status'] == 'recovered'
            self.summary = self.report['summary']
            self.entry_count = int(self.summary['entries'])
            if self.entry_count > MAX_UI_ENTRIES:
                raise ValueError('local_resource_limit: Desktop slider admits at most 1000000 events')
            self.first_time_ns = int(self.summary['first_timeline_ns'] or 0)
            self.last_time_ns = int(self.summary['last_timeline_ns'] or 0)
            self.reader.cancellation = cancelled
            if self.recovered:
                # R3 has verified this prefix and chronology. Retain only fixed ranges,
                # not decoded payloads or a fabricated END/index for the unknown tail.
                cursor = 0
                boundary = int(self.report['verified_prefix_bytes'])
                while cursor < boundary:
                    if cancelled(): raise InterruptedError('LMTR recovery cancelled')
                    envelope = self.reader.envelope(cursor)
                    if envelope['kind'] == 2:
                        record = self.reader.record(cursor)
                        meta = self.reader.metadata(record)
                        self._recovery_refs.append((int(meta['entries'][0]['entry_id']),
                                                    int(meta['entries'][-1]['entry_id']), cursor))
                        if len(self._recovery_refs) > MAX_RECOVERY_CHUNKS:
                            raise ValueError('local_resource_limit: recovery navigation budget')
                    cursor += envelope['length']
            self.entries = _Entries(self)
            self._times = _Times(self)
            self.index, self.playing, self.speed = 0, False, 1.0
            self._anchor_time = self._anchor_elapsed = 0.0
            # Existing metadata dialog accepts this recording-shaped immutable summary.
            self.manifest = {'header': self.reader.head, 'validation': self.report,
                             'completed': self.completed}
            self._assert_source_unchanged()
        except BaseException:
            self.reader.close()
            raise

    @classmethod
    def open(cls, path, **kwargs): return cls(path, **kwargs)
    @property
    def completed(self): return not self.recovered and self.report.get('declared_end_status') == 'completed'
    @property
    def duration_s(self): return (self.last_time_ns-self.first_time_ns)/1e9
    @property
    def cache_size(self): return int(self._cache is not None)
    @property
    def cache_bytes(self): return sum(len(b) for b in self._cache[2].values()) if self._cache else 0
    @property
    def entry(self): return self.entries[self.index] if self.entry_count else None

    def close(self):
        """Drop bounded payload ownership and release the source descriptor exactly once."""
        self._cache = None
        self._recovery_refs.clear()
        self.reader.cache.clear()
        self.reader.cache_bytes = 0
        self.reader.logical.clear()
        self.reader.selected.clear()
        self.reader.validated_records.clear()
        self.reader.close()

    def _identity(self):
        """Detect replacement/modification; the reader never repairs a changing source."""
        st = self.path.stat()
        return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns

    def _descriptor_identity(self):
        st = os.fstat(self.reader.file.fileno())
        return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns

    def _assert_source_unchanged(self):
        """Compare path and owned descriptor before AND after admitted source operations."""
        if self._identity() != self._source_identity or self._descriptor_identity() != self._source_identity:
            raise ValueError('source_changed: reopen and validate an immutable source')

    def _chunk(self, index):
        """Fully validate the selected CHUNK and navigation before exposing any bytes."""
        if self.cancelled(): raise InterruptedError('LMTR load cancelled')
        if type(index) is not int or not 0 <= index < self.entry_count: raise IndexError(index)
        self._assert_source_unchanged()
        if self._cache:
            offset, meta, _ = self._cache
            if int(meta['entries'][0]['entry_id']) <= index <= int(meta['entries'][-1]['entry_id']):
                return self._cache
        self._cache = None  # Evict before decoding a new CHUNK, not after.
        if self.recovered:
            match = next((r for r in self._recovery_refs if r[0] <= index <= r[1]), None)
            if match is None: raise ValueError('No verified prefix entry')
            record = self.reader.record(match[2])
            meta, _ = self.reader.decode(record)
        else:
            end = self.reader.terminal()
            # Exhaust the generator: its child range/stream checks follow the yield.
            matches = list(self.reader.walk(end['json']['root'], end, index))
            if len(matches) != 1: raise ValueError('invalid_container: selected CHUNK coverage')
            record, meta = matches[0]
        prefix = 4+struct.unpack_from('<I', record['body'])[0]
        blocks = {}
        total = 0
        # R3 decoded and checked these same immutable body bytes above. Reconstruct
        # owned blocks for analysis without changing that independent validation path.
        for b in meta['blocks']:
            total += int(b['decoded'])
            if total > self.limits.decoded_cache_bytes: raise ValueError('local_resource_limit: decoded cache budget')
            start = prefix+int(b['offset'])
            stored = record['body'][start:start+int(b['stored'])]
            data = stored if b['codec'] == 0 else inflate(stored, int(b['decoded']))
            if sha(data) != b['hash']: raise ValueError('committed_payload_integrity')
            blocks[b['id']] = data
        self._assert_source_unchanged()
        self._cache = record['offset'], meta, blocks
        return self._cache

    def _entry_view(self, index):
        _, meta, _ = self._chunk(index)
        return meta['entries'][index-int(meta['entries'][0]['entry_id'])]

    def original_entry(self, index):
        """Owned semantic metadata: callers cannot alter validated cached descriptors."""
        return deepcopy(self._entry_view(index))

    @property
    def header(self):
        """Return owned metadata across the worker/UI boundary."""
        return deepcopy(self.reader.head)

    def read_payloads(self, index):
        """Return exact immutable logical bytes, including observations without a grid."""
        _, meta, blocks = self._chunk(index)
        entry = self._entry_view(index)
        if entry['kind'] != 'observation': return freeze({})
        payloads = {}
        for p in entry['payloads']:
            if p['kind'] == 'materialized':
                start, length = int(p['offset']), int(p['length'])
                payloads[p['id']] = blocks[p['block_id']][start:start+length]
        for p in entry['payloads']:
            if p['kind'] == 'view':
                start, length = int(p['offset']), int(p['length'])
                payloads[p['id']] = payloads[p['parent_payload_id']][start:start+length]
        self._assert_source_unchanged()
        return freeze(payloads)

    def describe(self, index):
        entry = self._entry_view(index)
        kind = entry['kind']
        if kind == 'observation':
            kind = 'valid' if entry['measurement']['status'] == 'available' else 'unavailable'
        elif kind != 'gap': kind = 'segment'
        reason = entry.get('reason', entry.get('measurement',{}).get('reason',entry['kind']))
        if kind == 'gap':
            reason = entry['category']+': '+reason+' (missing '+(entry['count'] if entry['count'] is not None else 'unknown')+')'
        utc = entry.get('acquisition', {}).get('utc', {})
        return LmtrEntry(index, int(entry['entry_id']), utc.get('utc', 'UTC unavailable'),
                         (int(entry['timeline_ns'])-self.first_time_ns)/1e9,
                         entry['kind'], reason, kind)

    def timing_evidence(self,index):
        """Project selected validated metadata only; no scan, payload history or invented time alignment.

        Receipt is aligned to HEADER-relative time only for the identical reliable ns clock.
        A different UUID is not assumed to share an epoch merely because it uses ns units.
        Segment events with multiple streams do not get a fabricated common sampling policy.
        """
        _,metadata,_=self._chunk(index);entry=self._entry_view(index)
        segment=next(s for s in metadata['segments'] if s['id']==entry['segment_id'])
        streams=segment['streams'];stream=next((s for s in streams if s['stream_id']==entry.get('stream_id')),None)
        if stream is None and len(streams)==1:stream=streams[0]
        receipt=entry.get('receipt');shared=None
        if stream is not None and receipt is not None and receipt.get('stamp') is not None:
            stamp=receipt['stamp'];clock=next((c for c in stream['clocks'] if c['id']==stamp['clock_domain_id']),None)
            if (receipt['status']=='known' and stamp['clock_domain_id']==self.reader.head['timeline']['clock_domain_id']
                    and clock is not None and clock['kind']=='host_monotonic' and clock['quality']=='known'
                    and clock['tick_period_ns']=={'numerator':'1','denominator':'1'}):
                shared=str(int(stamp['ticks'])-int(self.reader.head['timeline']['origin_monotonic_ns']))
        return deepcopy(dict(kind=entry['kind'],timeline_ns=entry['timeline_ns'],
            sampling=None if stream is None else stream['sampling'],scheduled=entry.get('scheduled'),
            receipt=receipt,receipt_shared_timeline_ns=shared,measurement=entry.get('measurement'),
            category=entry.get('category'),count=entry.get('count'),reason=entry.get('reason','')))

    def initial_observation_index(self):
        """Find an observation in the initial validated CHUNK without scanning the recording.

        Segment/gap events remain at their original indices. This is a UI landing choice,
        not a change to timeline order or a search for favorable/valid temperatures.
        Work and cache stay bounded by the same one-CHUNK limits as an ordinary frame read.
        A control-only/empty initial CHUNK retains the normal event-zero landing.
        """
        if not self.entry_count:return None
        _, metadata, _ = self._chunk(0)
        return next((int(entry['entry_id']) for entry in metadata['entries']
                     if entry['kind']=='observation'),None)

    def frame(self, index=None):
        """Return exact stored planes or no reading, never reconstructed thermometry."""
        index = self.index if index is None else index
        if not self.entry_count: return None
        _, meta, blocks = self._chunk(index)
        entry = self._entry_view(index)
        if entry['kind'] != 'observation': return None
        segment = next(s for s in meta['segments'] if s['id'] == entry['segment_id'])
        stream = next(s for s in segment['streams'] if s['stream_id'] == entry['stream_id'])
        context = next(c['value'] for c in meta['contexts'] if c['id'] == entry['context_id'])
        if stream['geometry'] is None:
            return None  # Opaque-only observations have evidence/metadata, no invented pixel grid.
        geometry = Geometry(stream['geometry']['width_px'], stream['geometry']['height_px'])
        payloads = self.read_payloads(index)
        descriptors = {p['id']: p for p in entry['payloads']}
        measurement = entry['measurement']
        temperature = payloads.get(measurement.get('temperature_payload_id'))
        mask = payloads.get(measurement.get('mask_payload_id'))
        natives = []
        for p in entry['payloads']:
            if p['role'] == 'native_samples' and p['dtype'] != 'bytes':
                dtype = ('<' if p['byte_order'] == 'little' else '>')+{'u8':'u1','i8':'i1','u16':'u2','i16':'i2','u32':'u4','i32':'i4','u64':'u8','i64':'i8','f32':'f4','f64':'f8'}[p['dtype']]
                # A typed native plane can use a different grid. Keep its evidence,
                # but never use its indices for primary-grid hover without correspondence.
                encoding = ('ht301.raw14' if p['encoding_id'] == 'org.lmthermal.ht301.raw14'
                            and p['shape'] == list(geometry.shape) else p['encoding_id'])
                natives.append(NativePlane(p['id'], encoding, dtype, p['shape'], payloads[p['id']]))
        preview = None
        # Only explicit standardized raw visual encodings are interpreted. Opaque
        # compressed/unknown evidence stays exact bytes and is never guessed as RGB.
        for role in ('preview', 'visible'):
            p = next((p for p in entry['payloads'] if p['role'] == role), None)
            if p and p['geometry'] and p['shape'][:2] == list(geometry.shape):
                data = payloads[p['id']]
                channels = {'org.lmthermal.visual.gray8-v1': 1,
                            'org.lmthermal.visual.rgb8-v1': 3,
                            'org.lmthermal.visual.rgba8-v1': 4}.get(p['encoding_id'])
                expected = list(geometry.shape) + ([] if channels == 1 else [channels])
                if channels is not None and (p['dtype'] != 'u8' or p['byte_order'] != 'not_applicable'
                        or p['shape'] != expected or len(data) != geometry.width * geometry.height * channels):
                    # Fail on the worker, before a malformed standard visual reaches a Qt slot.
                    # This local guard does not edit or weaken the pinned R3 validator.
                    raise ContractError('invalid_payload', 'Malformed standardized visual descriptor')
                if p['encoding_id'] == 'org.lmthermal.visual.rgb8-v1': preview = data
                elif p['encoding_id'] == 'org.lmthermal.visual.gray8-v1':
                    preview = np.repeat(np.frombuffer(data,dtype='u1'),3).tobytes()
                elif p['encoding_id'] == 'org.lmthermal.visual.rgba8-v1':
                    # Composite unassociated source RGBA onto explicit black for display.
                    rgba = np.frombuffer(data,dtype='u1').reshape(-1,4).astype(np.uint16)
                    preview = ((rgba[:,:3]*rgba[:,3:4]+127)//255).astype('u1').tobytes()
                if preview is not None: break
        stats = None if temperature is None else statistics(
            np.frombuffer(temperature, dtype='<f4').reshape(geometry.shape),
            None if mask is None else np.frombuffer(mask, dtype='u1').reshape(geometry.shape))
        manifest = {'recording_id': self.reader.head['recording_id'], 'measurement': measurement, 'source': stream['source'],
                    'entry': entry, 'context': context, 'segment': segment,
                    'integrity_scope': 'selected_chunks', 'file_validation': self.report}
        self._assert_source_unchanged()
        return OfflineMeasurement(self.path, 'lmthermal-recording', geometry, temperature,
                                  mask, tuple(natives), freeze(manifest),
                                  evidence=freeze({'descriptors': descriptors}),
                                  payload_bytes=freeze(payloads), stats=stats, preview_bytes=preview,
                                  protected_paths=(self.path,))

    def verify_full(self):
        """Explicit background scan; never label merely selected access full integrity."""
        self._assert_source_unchanged()
        self._cache = None
        self.report = deepcopy(self.reader.check(full=True, recover=self.recovered, cancelled=self.cancelled))
        self._assert_source_unchanged()
        self.manifest['validation'] = self.report
        return self.report

    def seek(self, index):
        self.pause()
        if self.entry_count: self.index = max(0, min(int(index), self.entry_count-1))
        return self.entry
    def seek_time_ns(self, timeline_ns):
        """Select the first stored event at/after original intake time, without FPS inference."""
        if type(timeline_ns) is not int or timeline_ns < 0: raise ValueError('Invalid intake time')
        # Exact integers decide chronology; Float64 seconds are presentation only.
        low, high = 0, self.entry_count
        while low < high:
            mid = (low+high)//2
            if int(self._entry_view(mid)['timeline_ns']) < timeline_ns: low = mid+1
            else: high = mid
        index = low
        return None if index >= self.entry_count else self.seek(index)
    def step(self, delta): return self.seek(self.index+delta)
    def play(self, now=None):
        if not self.entry_count: return
        if self.index == self.entry_count-1: self.index = 0
        self._anchor_time = time.monotonic() if now is None else now
        self._anchor_elapsed = self.entry.elapsed_s
        self.playing = True
    def pause(self): self.playing = False
    def set_speed(self, speed, now=None):
        if speed not in (.5,1,2,4): raise ValueError('Unsupported playback speed')
        now = time.monotonic() if now is None else now
        if self.playing:
            self._anchor_elapsed += (now-self._anchor_time)*self.speed
            self._anchor_time = now
        self.speed = speed
    def advance(self, now=None):
        if self.playing:
            now = time.monotonic() if now is None else now
            target = self._anchor_elapsed+max(0,now-self._anchor_time)*self.speed
            self.index = max(0,min(bisect_right(self._times,target)-1,self.entry_count-1))
            if target >= self.duration_s: self.pause()
        return self.entry
