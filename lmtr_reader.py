"""Read-only indexed LMTR analysis using the pinned independent R3 validator.

Only verified source Float32 values enter analysis. Index navigation and full-file
integrity are separate claims. Playback bounds both the displayed CHUNK and
speculative verification work. The worker owns the read-only descriptor.
"""
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from copy import deepcopy
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from multiprocessing import get_context
from pathlib import Path
import struct
import os
import sys
import time
import weakref

import numpy as np
from lmtr_reference import Reader, ContractError
from lmtr_reference.jsoncodec import require
from lmtr_verified_blocks import decode_verified_blocks, verify_ahead
from offline_measurement import (Geometry, NativePlane, OfflineMeasurement,
                                 freeze, statistics)

# Local UI/navigation refusals, not changes to the frozen wire limits.
MAX_UI_ENTRIES = 1_000_000
MAX_RECOVERY_CHUNKS = 65536
MAX_CACHE_BYTES = 64 * 1024**2
LOOKAHEAD_CHUNKS = 4
LOOKAHEAD_PROCESSES = 3
LOOKAHEAD_BYTES = 24 * 1024**2
RECENT_RECORD_BYTES = 1024**2


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


@dataclass
class _Ticket:
    record: dict
    future: object
    charge: int


class _DesktopReader(Reader):
    """Add pre-allocation local refusals without changing the pinned R3 validator."""
    def __init__(self, path, limits):
        self.limits = limits
        self._end = None
        self._recent = None
        self._tickets = {}
        self._scheduled = None
        self._pool = None
        self._parallel = False
        self.capture_blocks = False
        self.captured_blocks = None
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

    def terminal(self):
        # The model checks descriptor and path identity around every source
        # operation. Keep the END only after its entire closure has passed.
        if self._end is None:
            self._end = super().terminal()
        return self._end

    def record(self, offset, expected=None):
        if expected == 2:
            ticket = self._tickets.get(offset)
            if ticket is not None or (self._recent and self._recent['offset'] == offset):
                require(not self.cancellation(), 'cooperative reader cancellation', 'cancelled')
                return ticket.record if ticket else self._recent
        return super().record(offset, expected)

    def metadata(self, record):
        if '_desktop_metadata' not in record:
            # Reader.metadata sets its own field before completing every check.
            # Keep a separate marker set only after that call returns successfully.
            record['_desktop_metadata'] = super().metadata(record)
        return record['_desktop_metadata']

    def _admit(self, record, meta):
        decoded = sum(int(b['decoded']) for b in meta['blocks'])
        logical = sum(int(p['length']) for e in meta['entries'] for p in e.get('payloads', ()))
        stored = max((int(b['stored']) for b in meta['blocks']), default=0)
        metadata_bytes = 4 + struct.unpack_from('<I', record['body'])[0]
        charge = len(record['body']) + decoded + logical + stored + 8 * metadata_bytes
        if decoded > self.limits.decoded_cache_bytes or charge > self.limits.working_bytes:
            raise ContractError('local_resource_limit', 'Desktop CHUNK decoded/working budget before decompression')
        return charge

    def decode(self, record):
        meta = self.metadata(record)
        self._admit(record, meta)
        ticket = self._tickets.get(record['offset'])
        verified = None
        if ticket is not None and ticket.record is record and ticket.future.done():
            del self._tickets[record['offset']]
            try:
                verified, error = ticket.future.result()
            except BrokenProcessPool:
                self._parallel = False
            else:
                if error is not None:
                    raise ContractError(*error)
        blocks, result = verified if verified is not None else decode_verified_blocks(record['body'], meta, self.head)
        # Match Reader.decode's cumulative reporting admission even when a
        # selected CHUNK was prepared in another process.
        offset = record['offset']
        require(len(self.logical) + len(result['logical_hashes']) <= 65536 or offset in self.selected,
                'logical-hash reporting budget', 'local_resource_limit')
        self.logical.update(result['logical_hashes'])
        self.selected.add(offset)
        if self.capture_blocks:
            self.captured_blocks = blocks
        return meta, result

    def _indexed_leaf(self, end, choose):
        """Find the authenticated record on a selected index branch."""

        owner = end
        ref = end['json']['root']
        parent = None
        while ref is not None:
            page = self.reference(ref, owner, 3)
            self.index_page(page)
            data = page['json']
            if parent is not None and (
                data['depth'] != owner['json']['depth'] - 1 or
                any(data[key] != parent[key] for key in ('time_first_ns', 'time_last_ns', 'stream_ids'))
            ):
                raise ContractError('invalid_container', 'Timeline page bounds')
            child = choose(data['children'])
            if child is None:
                return None
            ref, owner = child['record'], page
            if data['depth'] == 0:
                return owner, child
            parent = child
        return None

    def time_chunk(self, end, target_ns, before):
        """Locate an exact time without decoding unrelated CHUNKs."""
        def choose(children):
            if before:
                return next((c for c in reversed(children) if int(c['time_first_ns']) <= target_ns), None)
            return next((c for c in children if int(c['time_last_ns']) >= target_ns), None)
        selected = self._indexed_leaf(end, choose)
        if selected is None:
            return None
        owner, child = selected
        ref = child['record']
        record = self.reference(ref, owner, 2)
        entries = self.metadata(record)['entries']
        first = int(ref['first'])
        times = [int(e['timeline_ns']) for e in entries]
        if (not times or times[0] != int(child['time_first_ns']) or
                times[-1] != int(child['time_last_ns']) or
                len(entries) != int(ref['last']) - first + 1 or
                any(int(e['entry_id']) != first + i for i, e in enumerate(entries)) or
                any(a > b for a, b in zip(times, times[1:]))):
            raise ContractError('invalid_container', 'Timeline CHUNK bounds')
        if len(record['body']) <= RECENT_RECORD_BYTES:
            self._recent = record
        return first, times

    def prepare(self, start, count, *, already_decoded):
        """Keep a bounded sequential verification window, independent of frame requests."""
        if not self._parallel or start >= count:
            return True
        if self._scheduled is not None and self._scheduled[0] == start:
            offset = self._scheduled[1]
            ticket = self._tickets.get(offset)
            if ticket is not None:
                return already_decoded or ticket.future.done()
        cursor = start
        window = []
        budget = min(LOOKAHEAD_BYTES, self.limits.decoded_cache_bytes, self.limits.working_bytes // 2)
        try:
            for _ in range(LOOKAHEAD_CHUNKS):
                selected = self._indexed_leaf(self.terminal(), lambda items: next(
                    (c for c in items if int(c['record']['first']) <= cursor <= int(c['record']['last'])), None))
                if selected is None:
                    break
                owner, child = selected
                ref = child['record']
                window.append((owner, ref))
                cursor = int(ref['last']) + 1
                if cursor >= count:
                    break
        except (ContractError, OSError):
            # A speculative error cannot affect the currently selected event.
            # Ordinary navigation will report it if the user selects this CHUNK.
            pass
        wanted = {int(ref['offset']) for _, ref in window}
        for offset, ticket in tuple(self._tickets.items()):
            if offset not in wanted and (ticket.future.cancel() or ticket.future.done()):
                del self._tickets[offset]
        used = sum(ticket.charge for ticket in self._tickets.values())
        for owner, ref in window:
            offset = int(ref['offset'])
            if offset in self._tickets:
                continue
            # Give speculative envelope/body reads the same working reservation
            # as their subsequent metadata and payload validation.
            if len(self._tickets) >= LOOKAHEAD_CHUNKS or used + int(ref['length']) - 112 > budget:
                break
            try:
                # Do not allocate a body from an optimistic (possibly malformed)
                # reference length. Validate the actual envelope first.
                if self.envelope(offset)['length'] != int(ref['length']):
                    break
                record = self.reference(ref, owner, 2)
                meta = self.metadata(record)
                charge = self._admit(record, meta)
            except (ContractError, OSError):
                break  # The selected path, if reached, will report the error.
            if used + charge > budget:
                break
            try:
                if self._pool is None:
                    self._pool = ProcessPoolExecutor(max_workers=LOOKAHEAD_PROCESSES,
                                                     mp_context=get_context('spawn'))
                future = self._pool.submit(verify_ahead, record['body'], meta, self.head)
            except (BrokenProcessPool, ImportError, OSError):
                self._parallel = False
                return True
            self._tickets[offset] = _Ticket(record, future, charge)
            used += charge
        if not window:
            return True
        offset = int(window[0][1]['offset'])
        ticket = self._tickets.get(offset)
        if all(int(ref['offset']) in self._tickets for _, ref in window):
            self._scheduled = (start, offset)
        # An oversized candidate or a busy pool after a large jump still has
        # a synchronous verified path.
        return already_decoded or ticket is None or ticket.future.done()

    def reset_lookahead(self):
        if self._pool is not None:
            self._pool.shutdown(wait=True, cancel_futures=True)
            self._pool = None
        self._tickets.clear()
        self._scheduled = None

    def close(self):
        try:
            self.reset_lookahead()
        finally:
            self._recent = self._end = None
            super().close()


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
            self.reader._parallel = (sys.platform == 'linux' and not self.recovered
                                     and len(os.sched_getaffinity(0)) >= LOOKAHEAD_PROCESSES + 1)
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
        return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns

    def _descriptor_identity(self):
        st = os.fstat(self.reader.file.fileno())
        return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns

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
        self.reader.capture_blocks = True
        try:
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
            blocks = self.reader.captured_blocks
        finally:
            self.reader.capture_blocks = False
            self.reader.captured_blocks = None
        self._assert_source_unchanged()
        self._cache = record['offset'], meta, blocks
        return self._cache

    def prepare_playback(self):
        """Return promptly if the displayed event is waiting on a verified CHUNK."""
        if self.cancelled():
            raise InterruptedError('LMTR load cancelled')
        self._assert_source_unchanged()
        start = self.index
        ready = False
        if self._cache is not None:
            entries = self._cache[1]['entries']
            last = int(entries[-1]['entry_id'])
            if int(entries[0]['entry_id']) <= self.index <= last:
                start = last + 1
                ready = True
        return self.reader.prepare(start, self.entry_count, already_decoded=ready)

    def _entry_view(self, index):
        _, meta, _ = self._chunk(index)
        return meta['entries'][index-int(meta['entries'][0]['entry_id'])]

    def _visit_entries(self, visit):
        """Visit verified entries on the owning worker, with one CHUNK at a time.

        The visitor may borrow descriptors and block bytes only for the call.
        It must retain only derived values and must not navigate the model.
        """
        self._assert_source_unchanged()
        self._cache = None
        playback_reader = self.reader
        # A scan has its own validation/reporting budgets. Visiting many CHUNKs
        # must not consume ordinary playback's cumulative reporting budget.
        with _DesktopReader(self.path, self.limits) as scan_reader:
            scan_reader.cancellation = self.cancelled
            self.reader = scan_reader
            try:
                index = 0
                while index < self.entry_count:
                    _, metadata, blocks = self._chunk(index)
                    segments = {segment['id']: segment for segment in metadata['segments']}
                    for entry in metadata['entries']:
                        if self.cancelled():
                            raise InterruptedError('LMTR analysis cancelled')
                        visit(entry, segments[entry['segment_id']], blocks)
                        index += 1
                    del metadata, blocks, segments, entry
                self._assert_source_unchanged()
            finally:
                self._cache = None
                self.reader = playback_reader

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
        entry = meta['entries'][index-int(meta['entries'][0]['entry_id'])]
        payloads = self._payloads(entry, blocks)
        self._assert_source_unchanged()
        return freeze(payloads)

    @staticmethod
    def _payloads(entry, blocks):
        payloads = {}
        for p in entry.get('payloads', ()):
            if p['kind'] == 'materialized':
                start, length = int(p['offset']), int(p['length'])
                payloads[p['id']] = blocks[p['block_id']][start:start+length]
        for p in entry.get('payloads', ()):
            if p['kind'] == 'view':
                start, length = int(p['offset']), int(p['length'])
                payloads[p['id']] = payloads[p['parent_payload_id']][start:start+length]
        return payloads

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
        entry = meta['entries'][index-int(meta['entries'][0]['entry_id'])]
        if entry['kind'] != 'observation': return None
        segment = next(s for s in meta['segments'] if s['id'] == entry['segment_id'])
        stream = next(s for s in segment['streams'] if s['stream_id'] == entry['stream_id'])
        context = next(c['value'] for c in meta['contexts'] if c['id'] == entry['context_id'])
        if stream['geometry'] is None:
            return None  # Opaque-only observations have evidence/metadata, no invented pixel grid.
        geometry = Geometry(stream['geometry']['width_px'], stream['geometry']['height_px'])
        payloads = self._payloads(entry, blocks)
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
                                  mask, tuple(natives), manifest,
                                  evidence={'descriptors': descriptors},
                                  payload_bytes=payloads, stats=stats, preview_bytes=preview,
                                  protected_paths=(self.path,))

    def verify_full(self):
        """Explicit background scan; never label merely selected access full integrity."""
        self._assert_source_unchanged()
        self._cache = None
        self.reader.reset_lookahead()
        self.reader._recent = self.reader._end = None
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
        index = self.index_at_time_ns(timeline_ns)
        return None if index is None else self.seek(index)

    def index_at_time_ns(self, timeline_ns, *, before=False):
        """Resolve stored timestamps exactly, including ties across CHUNKs."""
        if type(timeline_ns) is not int or timeline_ns < 0: raise ValueError('Invalid intake time')
        if not self.entry_count:
            return None
        self._assert_source_unchanged()
        search = bisect_right if before else bisect_left
        shift = int(before)
        if self._cache:
            entries = self._cache[1]['entries']
            first, last = (int(entries[i]['timeline_ns']) for i in (0,-1))
            # Strict side matters when two neighboring CHUNKs share a time.
            if (first <= timeline_ns < last if before else first < timeline_ns <= last):
                slot = search(entries, timeline_ns, key=lambda e: int(e['timeline_ns'])) - shift
                return int(entries[slot]['entry_id'])
        if self.recovered:
            # Recovery has a fixed verified prefix but no trusted terminal tree.
            index = search(range(self.entry_count), timeline_ns,
                           key=lambda i: int(self._entry_view(i)['timeline_ns'])) - shift
            return index if 0 <= index < self.entry_count else None
        target = self.reader.time_chunk(self.reader.terminal(), timeline_ns, before)
        if target is None:
            return None
        first, times = target
        slot = search(times, timeline_ns) - shift
        return first + slot if 0 <= slot < len(times) else None

    def project_time_ns(self, timeline_ns):
        """Project the playback head to a real event; keep navigation static on a pause."""
        index = self.index_at_time_ns(timeline_ns, before=True)
        if index is not None:
            self.index = index
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
            self.project_time_ns(self.first_time_ns + round(target * 1e9))
            if target >= self.duration_s: self.pause()
        return self.entry
