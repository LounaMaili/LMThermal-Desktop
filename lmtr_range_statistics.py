"""Inclusive integer-time summaries of existing immutable comparison scalars.

No file access, frame decoding, timestamp joining or resampling. ROI inputs are
per-observation means, not individual pixels or the optional spatial extrema.
The incremental interface bounds GUI work and retains at most five accumulators.
"""
from bisect import bisect_left, bisect_right
from dataclasses import dataclass

from lmtr_comparison import Comparison, difference_value
from lmtr_temporal import MAX_TEMPORAL_EVENTS, write_csv_records

BATCH_EVENTS = 256
SUMMARY_COLUMNS = (
    'range_start_ns', 'range_end_ns', 'bounds_inclusive', 'aggregation',
    'row_kind', 'selection_id', 'selection_name', 'metric', 'color', 'visible',
    'left_selection_id', 'right_selection_id', 'recording_id', 'timeline_clock_domain_id',
    'stream_id', 'selected_session_id', 'module_id', 'model_id', 'width_px', 'height_px',
    'x1', 'y1', 'x2_exclusive', 'y2_exclusive', 'right_stream_id', 'right_session_id',
    'minimum_c', 'maximum_c', 'mean_c', 'valid_observations', 'unavailable_observations',
    'incompatible_observations', 'gap_events', 'known_missing_observations', 'unknown_gap_events',
    'control_events', 'first_valid_ns', 'last_valid_ns', 'status', 'compatibility',
    'compatibility_reason', 'file_status', 'end_status', 'unknown_tail',
)


@dataclass(frozen=True, slots=True)
class RangeStatistics:
    selection_id: str
    minimum_c: float | None
    maximum_c: float | None
    mean_c: float | None
    valid_observations: int
    unavailable_observations: int
    incompatible_observations: int
    gap_events: int
    known_missing_observations: int
    unknown_gap_events: int
    control_events: int
    first_valid_ns: int | None
    last_valid_ns: int | None
    status: str
    compatibility: str
    compatibility_reason: str = ''


@dataclass(frozen=True, slots=True)
class RangeSummary:
    """Complete result and its immutable inputs, frozen together for CSV publication."""
    comparison: Comparison
    start_ns: int
    end_ns: int
    difference: tuple | None
    rows: tuple[RangeStatistics, ...]


def range_extent(comparison):
    return min(s.first_ns for s in comparison.series), max(s.last_ns for s in comparison.series)


def iter_range_summary(comparison, start_ns, end_ns, *, difference=None, cancelled=lambda: False):
    """Yield progress after at most 256 events; return a complete RangeSummary.

    Equal-time observations remain distinct. Missing observations declared by a
    gap are counted separately from stored unavailable observations; unknown gap
    counts stay unknown. Segment declarations are control events, not observations.
    Means use sequential binary64 addition in original event order divided by N,
    with no duration or pixel weighting. Cancellation never publishes partial rows.
    """
    first, last = range_extent(comparison)
    if type(start_ns) is not int or type(end_ns) is not int or not first <= start_ns <= end_ns <= last:
        raise ValueError('Choose integer nanosecond bounds inside the recording, with A ≤ B')
    if any(len(s.rows) > MAX_TEMPORAL_EVENTS for s in comparison.series):
        raise ValueError('Temporal event limit exceeded')
    targets = [(item.id, series, None, None) for item, series in zip(comparison.selections, comparison.series)]
    if difference:
        left, right = (comparison.by_id(key) for key in difference)
        targets.append(('', left, right, comparison.difference_error(difference)))
    spans = [(bisect_left(s.times, start_ns), bisect_right(s.times, end_ns)) for _, s, _, _ in targets]
    total = sum(b - a for a, b in spans)
    processed, results = 0, []
    for (key, series, right, error), (begin, end) in zip(targets, spans):
        valid = unavailable = incompatible = gaps = missing = unknown = controls = 0
        low = high = first_valid = last_valid = None
        summed = 0.0
        for position in range(begin, end):
            if cancelled():
                raise InterruptedError('Range calculation cancelled')
            row = series.rows[position]
            if row.event == 'observation':
                if error:
                    value, status = None, 'incompatible_pair'
                elif right is not None:
                    value, status = difference_value(row, right.at_event(row.index))
                else:
                    value, status = row.temperature, row.status
                if status == 'valid' and value is not None:
                    low = value if low is None or value < low else low
                    high = value if high is None or value > high else high
                    summed += value
                    valid += 1
                    first_valid = row.time_ns if first_valid is None else first_valid
                    last_valid = row.time_ns
                else:
                    unavailable += 1
                    other = right.at_event(row.index) if right is not None else None
                    incompatible += (status in ('other_native_space', 'incompatible_observation', 'incompatible_pair')
                                     or row.status == 'other_native_space'
                                     or (other is not None and other.status == 'other_native_space'))
            elif row.event == 'gap':
                gaps += 1
                if row.gap_count is None:
                    unknown += 1
                else:
                    missing += int(row.gap_count)
            else:
                controls += 1
            processed += 1
            if processed % BATCH_EVENTS == 0:
                yield processed, total
        results.append(RangeStatistics(key, low, high, summed / valid if valid else None,
            valid, unavailable, incompatible, gaps, missing, unknown, controls, first_valid, last_valid,
            'incompatible_pair' if error else 'valid' if valid else 'no_usable_data',
            'incompatible_pair' if error else 'contains_incompatible_observations' if incompatible else
            'same_observation' if right is not None else 'selected_native_space', error or ''))
    if cancelled():
        raise InterruptedError('Range calculation cancelled')
    return RangeSummary(comparison, start_ns, end_ns, difference, tuple(results))


def summarize_range(comparison, start_ns, end_ns, **kwargs):
    """Synchronous entry point for diagnostics; the GUI consumes the bounded iterator."""
    scan = iter_range_summary(comparison, start_ns, end_ns, **kwargs)
    while True:
        try:
            next(scan)
        except StopIteration as done:
            return done.value


def write_range_summary_csv(summary, path, *, cancelled=lambda: False):
    """Write up to five exact rows using the existing exclusive/cancellable CSV writer."""
    def records():
        comparison = summary.comparison
        for stats in summary.rows:
            is_delta = stats.selection_id == ''
            key = summary.difference[0] if is_delta else stats.selection_id
            item = next(s for s in comparison.selections if s.id == key)
            series = comparison.by_id(key)
            native = series.selection
            session, module, model, grid = native.identity
            right = comparison.by_id(summary.difference[1]) if is_delta else None
            record = dict(range_start_ns=summary.start_ns, range_end_ns=summary.end_ns,
                bounds_inclusive='both', aggregation='arithmetic_mean_of_valid_observations',
                row_kind='difference' if is_delta else 'selection', selection_id=stats.selection_id,
                selection_name='' if is_delta else item.name,
                metric='scalar_difference' if is_delta else 'roi_mean_temperature' if native.kind == 'roi' else 'point_temperature',
                color='#ffffff' if is_delta else item.color, visible='true' if is_delta else str(item.visible).lower(),
                left_selection_id=summary.difference[0] if is_delta else '',
                right_selection_id=summary.difference[1] if is_delta else '',
                recording_id=series.recording_id, timeline_clock_domain_id=series.clock_domain_id,
                stream_id=native.stream_id, selected_session_id=session, module_id=module, model_id=model,
                width_px=grid[0], height_px=grid[1],
                right_stream_id=right.selection.stream_id if right else '',
                right_session_id=right.selection.identity[0] if right else '',
                file_status=series.file_status, end_status=series.end_status, unknown_tail=str(series.unknown_tail).lower())
            for field in RangeStatistics.__dataclass_fields__:
                record[field] = getattr(stats, field)
            for field, value in zip(('x1', 'y1', 'x2_exclusive', 'y2_exclusive'),
                                    (native.region.x1, native.region.y1, native.region.x2, native.region.y2)):
                record[field] = None if is_delta else value
            yield record
    return write_csv_records(SUMMARY_COLUMNS, records(), path, cancelled=cancelled)
