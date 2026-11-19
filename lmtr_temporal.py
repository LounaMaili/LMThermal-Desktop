"""Exact, read-only temporal projection of one LMTR stream and native selection."""
from bisect import bisect_left
import csv
from dataclasses import dataclass
import os
from pathlib import Path

import numpy as np

from offline_measurement import Geometry, Rectangle, Statistics, statistics

# Local analysis limits, independent of playback admission and the LMTR wire.
MAX_TEMPORAL_EVENTS = 50_000
MAX_TEMPORAL_TEXT_BYTES = 8 * 1024**2

CSV_COLUMNS = (
    'recording_id', 'stream_id', 'selected_session_id', 'selection',
    'x1', 'y1', 'x2_exclusive', 'y2_exclusive', 'width_px', 'height_px',
    'timeline_clock_domain_id', 'first_timeline_ns', 'file_status', 'end_status', 'unknown_tail',
    'entry_id', 'timeline_ns', 'elapsed_ns', 'event', 'segment_id', 'session_id',
    'status', 'measurement_status', 'reason', 'gap_category', 'gap_count',
    'gap_count_status', 'period_ns', 'scheduled_first_slot', 'scheduled_last_slot',
    'scheduled_first_deadline_ns', 'scheduled_last_deadline_ns',
    'receipt_status', 'receipt_clock_domain_id', 'receipt_ticks',
    'temperature_c', 'roi_min_c', 'roi_max_c', 'valid_pixels', 'total_pixels',
)


class TemporalLimitError(ValueError):
    """Analysis can be refused while ordinary playback remains available."""


def _native_identity(stream):
    geometry = stream['geometry']
    return (
        stream['session_id'], stream['source']['module_id'], stream['source']['model_id'],
        None if geometry is None else (geometry['width_px'], geometry['height_px']),
    )


@dataclass(frozen=True, slots=True)
class TemporalSelection:
    """A point (one pixel) or half-open rectangle in one recorded native grid."""
    stream_id: str
    identity: tuple
    kind: str
    region: Rectangle

    @classmethod
    def from_frame(cls, frame, kind, region):
        entry = frame.manifest['entry']
        stream = next(s for s in frame.manifest['segment']['streams'] if s['stream_id'] == entry['stream_id'])
        region.validate(frame.geometry)
        if kind not in ('point', 'roi') or (kind == 'point' and region.pixel_count != 1):
            raise ValueError('Select a native pixel for a point, or draw a rectangle for an ROI')
        return cls(stream['stream_id'], _native_identity(stream), kind, region)

    @property
    def label(self):
        r = self.region
        if self.kind == 'point':
            return f'Point ({r.x1}, {r.y1})'
        return f'ROI [{r.x1},{r.x2}) × [{r.y1},{r.y2})'


@dataclass(frozen=True, slots=True)
class TemporalRow:
    index: int
    time_ns: int
    event: str
    status: str
    segment_id: str
    session_id: str
    reason: str
    measurement_status: str
    gap_category: str = ''
    gap_count: str | None = None
    period_ns: str | None = None
    schedule: tuple = (None, None, None, None)
    receipt: tuple = (None, None, None)
    reading: Statistics | None = None

    @property
    def temperature(self):
        return None if self.reading is None else self.reading.mean_c


@dataclass(frozen=True, slots=True)
class TemporalSeries:
    selection: TemporalSelection
    recording_id: str
    clock_domain_id: str
    first_ns: int
    last_ns: int
    file_status: str
    end_status: str | None
    unknown_tail: bool
    rows: tuple
    indices: tuple
    times: tuple

    def at_event(self, index):
        pos = bisect_left(self.indices, index)
        if pos < len(self.rows) and self.indices[pos] == index:
            return self.rows[pos]
        return None

    def nearest_rows(self, time_ns):
        """Return all rows at the nearest represented instant, earlier on a tie."""
        if not self.rows:
            return ()
        pos = bisect_left(self.times, time_ns)
        if pos == len(self.times) or (pos > 0 and time_ns - self.times[pos-1] <= self.times[pos] - time_ns):
            time_ns = self.times[pos-1]
        else:
            time_ns = self.times[pos]
        start = bisect_left(self.times, time_ns)
        return self.rows[start:bisect_left(self.times, time_ns + 1)]


def _sample_selection(entry, blocks, selection, cancelled):
    measurement = entry['measurement']
    descriptors = {d['id']: d for d in entry['payloads']}
    width, height = selection.identity[3]
    geometry = Geometry(width, height)

    def plane(key, dtype):
        if key not in measurement:
            return None
        # The pinned validator requires both temperature and mask to be materialized.
        descriptor = descriptors[measurement[key]]
        offset = int(descriptor['offset'])
        return np.frombuffer(blocks[descriptor['block_id']], dtype=dtype,
                             count=geometry.pixel_count, offset=offset).reshape(geometry.shape)

    matrix = plane('temperature_payload_id', '<f4')
    mask = plane('mask_payload_id', 'u1')
    r = selection.region
    if selection.kind == 'roi':
        return statistics(matrix, mask, r, cancelled=cancelled)
    if mask is not None and mask[r.y1, r.x1] != 1:
        return Statistics(1, 0)
    # Preserve -0.0 and the exact binary32-to-binary64 conversion of a point.
    return Statistics(1, 1, mean_c=float(matrix[r.y1, r.x1]))


def collect_temporal_series(model, selection):
    """Keep only scalar projections of verified entries, never planes or frame objects."""
    if model.entry_count > MAX_TEMPORAL_EVENTS:
        raise TemporalLimitError(f'Temporal analysis admits up to {MAX_TEMPORAL_EVENTS:,} file events')
    rows = []
    text_bytes = 0

    def collect(entry, segment, blocks):
        nonlocal text_bytes
        if entry.get('stream_id', selection.stream_id) != selection.stream_id:
            return
        stream = next((s for s in segment['streams'] if s['stream_id'] == selection.stream_id), None)
        if stream is None:
            return
        kind = entry['kind']
        measurement = entry.get('measurement', {})
        status = kind
        reason = entry.get('reason', segment['reason'] if kind == 'segment_start' else '')
        reading = None
        if kind == 'observation':
            if _native_identity(stream) != selection.identity:
                status = 'other_native_space'
                reason = measurement.get('reason', '')
            elif measurement['status'] != 'available':
                status = 'measurement_unavailable'
                reason = measurement['reason']
            else:
                reading = _sample_selection(entry, blocks, selection, model.cancelled)
                status = 'valid' if reading.valid_pixel_count else 'no_valid_pixels'
        schedule = entry.get('scheduled') or {}
        receipt = entry.get('receipt') or {}
        stamp = receipt.get('stamp') or {}
        text_bytes += len(reason.encode('utf-8'))
        if text_bytes > MAX_TEMPORAL_TEXT_BYTES:
            raise TemporalLimitError('Temporal analysis exceeds the 8 MiB event reason budget')
        rows.append(TemporalRow(
            int(entry['entry_id']), int(entry['timeline_ns']), kind, status, entry['segment_id'],
            stream['session_id'], reason, measurement.get('status', ''),
            entry.get('category', ''), entry.get('count'), stream['sampling'].get('period_ns'),
            tuple(schedule.get(k) for k in ('first_slot', 'last_slot', 'first_deadline_ns', 'last_deadline_ns')),
            (receipt.get('status'), stamp.get('clock_domain_id'), stamp.get('ticks')), reading,
        ))

    model._visit_entries(collect)
    header = model.header
    return TemporalSeries(
        selection, header['recording_id'], header['timeline']['clock_domain_id'],
        model.first_time_ns, model.last_time_ns, model.report['status'], model.report.get('declared_end_status'),
        bool(model.report.get('unknown_tail')), tuple(rows),
        tuple(row.index for row in rows), tuple(row.time_ns for row in rows),
    )


def write_temporal_csv(series, path, *, cancelled=lambda: False):
    """Write one row per represented event, preserving blanks and round-trip values."""
    path = Path(path)
    selection = series.selection
    region = selection.region
    common = (
        series.recording_id, selection.stream_id, selection.identity[0], selection.kind,
        region.x1, region.y1, region.x2, region.y2, *selection.identity[3],
        series.clock_domain_id, series.first_ns, series.file_status, series.end_status,
        str(series.unknown_tail).lower(),
    )
    # Exclusive creation also refuses an existing symlink or hard link to the source.
    if cancelled():
        raise InterruptedError('CSV export cancelled')
    output = path.open('x', newline='', encoding='utf-8')
    identity = os.fstat(output.fileno())
    try:
        with output:
            writer = csv.writer(output)
            writer.writerow(CSV_COLUMNS)
            for row in series.rows:
                if cancelled():
                    raise InterruptedError('CSV export cancelled')
                reading = row.reading
                count_status = ('known' if row.gap_count is not None else 'unknown') if row.event == 'gap' else ''
                writer.writerow((
                    *common, row.index, row.time_ns, row.time_ns - series.first_ns, row.event,
                    row.segment_id, row.session_id, row.status, row.measurement_status,
                    row.reason, row.gap_category, row.gap_count, count_status, row.period_ns,
                    *row.schedule, *row.receipt, row.temperature,
                    reading.min_c if reading else None, reading.max_c if reading else None,
                    reading.valid_pixel_count if reading else None, reading.pixel_count if reading else None,
                ))
        if cancelled():
            raise InterruptedError('CSV export cancelled')
    except BaseException:
        # A cancelled or failed export may only clean up the file it created.
        try:
            if os.path.samestat(identity, path.lstat()):
                path.unlink()
        except FileNotFoundError:
            pass
        raise
    return len(series.rows)
