"""Bounded named temporal selections and exact same-observation differences.

All values derive from stored planes through the existing temporal projector.
Presentation edits never rescan the source. Differences are scalar projections,
not another retained event history or a timestamp-joining/interpolation scheme.
"""
from dataclasses import dataclass

from lmtr_temporal import (CSV_COLUMNS, MAX_TEMPORAL_SELECTIONS, TemporalSelection, TemporalSeries,
                           collect_temporal_selections, temporal_csv_record, write_csv_records)

MAX_SELECTIONS = MAX_TEMPORAL_SELECTIONS
CURVE_COLORS = ('#67e3d2', '#ffb34b', '#89aaff', '#f695bd', '#d2e878', '#f6f6f6')
COMPARISON_COLUMNS = (
    'row_kind', 'selection_id', 'selection_name', 'color', 'visible',
    'show_roi_min', 'show_roi_max', 'left_selection_id', 'right_selection_id',
    'left_temperature_c', 'right_temperature_c', 'left_status', 'right_status',
) + CSV_COLUMNS


@dataclass(frozen=True, slots=True)
class NamedSelection:
    """Stable per-document ID and editable display properties, independent of native bounds."""
    id: str
    name: str
    color: str
    native: TemporalSelection
    visible: bool = True
    show_min: bool = False
    show_max: bool = False


@dataclass(frozen=True, slots=True)
class Comparison:
    """At most four immutable scalar series; no frame/plane owners or cross-file joins."""
    selections: tuple[NamedSelection, ...]
    series: tuple[TemporalSeries, ...]

    def __post_init__(self):
        if not 1 <= len(self.selections) <= MAX_SELECTIONS or len(self.series) != len(self.selections):
            raise ValueError('Choose between one and four selections')
        if len({s.id for s in self.selections}) != len(self.selections):
            raise ValueError('Selection identifiers must be unique')
        if any(s.native != data.selection for s, data in zip(self.selections, self.series)):
            raise ValueError('Selection does not match its stored projection')

    def by_id(self, selection_id):
        for item, series in zip(self.selections, self.series):
            if item.id == selection_id:
                return series
        raise KeyError(selection_id)

    def difference_error(self, pair):
        """Admit only the same recording/clock/stream/native space, never merely nearby times."""
        if not pair or pair[0] == pair[1]:
            return 'Choose two different selections'
        try:
            left, right = (self.by_id(key) for key in pair)
        except KeyError:
            return 'Selection no longer exists'
        if (left.recording_id, left.clock_domain_id, left.first_ns, left.last_ns) != (
                right.recording_id, right.clock_domain_id, right.first_ns, right.last_ns):
            return 'Different recordings or timeline clocks'
        if left.selection.stream_id != right.selection.stream_id or left.selection.identity != right.selection.identity:
            return 'Different streams, source sessions or native grids'
        return None

    def difference_rows(self, pair):
        """Yield exact left-event matches, including unavailable values without filling gaps.

        Entry ID is required in addition to nanoseconds: two observations at an
        equal intake time are not evidence of the same acquisition. No resampling.
        """
        error = self.difference_error(pair)
        if error:
            raise ValueError(error)
        left, right = (self.by_id(key) for key in pair)
        for a in left.rows:
            b = right.at_event(a.index)
            value, status = difference_value(a, b)
            yield a, b, value, status


def difference_value(left, right):
    """Subtract binary64 scalar readings only at the identical compatible observation."""
    if right is None or (left.index, left.time_ns, left.segment_id, left.session_id, left.event) != (
            right.index, right.time_ns, right.segment_id, right.session_id, right.event):
        return None, 'incompatible_observation'
    if left.event != 'observation' or left.status != 'valid' or right.status != 'valid':
        return None, 'operands_unavailable'
    if left.temperature is None or right.temperature is None:
        return None, 'operands_unavailable'
    return left.temperature - right.temperature, 'valid'


def collect_comparison(model, selections):
    """One reader traversal for all points/ROIs; publish only a complete result."""
    if not 1 <= len(selections) <= MAX_SELECTIONS:
        raise ValueError('Choose between one and four selections')
    return Comparison(tuple(selections), collect_temporal_selections(model, tuple(s.native for s in selections)))


def write_comparison_csv(comparison, path, *, difference=None, cancelled=lambda: False):
    """Long-form CSV: all selections (including hidden ones), then the optional A−B pair."""
    if difference and (error := comparison.difference_error(difference)):
        raise ValueError(error)

    def records():
        for selection, series in zip(comparison.selections, comparison.series):
            for row in series.rows:
                yield dict(temporal_csv_record(series, row), row_kind='selection',
                           selection_id=selection.id, selection_name=selection.name,
                           color=selection.color, visible=str(selection.visible).lower(),
                           show_roi_min=str(selection.show_min).lower(), show_roi_max=str(selection.show_max).lower())
        if difference:
            left = comparison.by_id(difference[0])
            for a, b, value, status in comparison.difference_rows(difference):
                record = temporal_csv_record(left, a)
                # A difference has no ROI geometry, pixel count or extrema of its own.
                for key in ('x1', 'y1', 'x2_exclusive', 'y2_exclusive', 'roi_min_c', 'roi_max_c', 'valid_pixels', 'total_pixels'):
                    record[key] = None
                record.update(row_kind='difference', selection='difference',
                              left_selection_id=difference[0], right_selection_id=difference[1],
                              left_temperature_c=a.temperature, right_temperature_c=b.temperature if b else None,
                              left_status=a.status, right_status=b.status if b else 'missing_event',
                              temperature_c=value, status=status)
                yield record

    return write_csv_records(COMPARISON_COLUMNS, records(), path, cancelled=cancelled)
