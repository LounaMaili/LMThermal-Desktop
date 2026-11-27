"""Exact inclusive ranges, explicit gaps and cooperation on immutable scalars."""
import csv
from dataclasses import replace
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication
from lmtr_comparison import Comparison, collect_comparison
from lmtr_comparison_panel import ComparisonPanel
from lmtr_range_statistics import summarize_range, write_range_summary_csv
from lmtr_reader import LmtrPlaybackModel
from lmtr_test_support import FILES
from test_lmtr_comparison import selections

START = 16_000_000_000_000_000_000


def known_comparison():
    """Irregular and duplicate times above binary64's integer precision; no wire edits."""
    model = LmtrPlaybackModel(FILES / 'illustration-01-stored.lmtr')
    try:
        original = collect_comparison(model, selections(model.frame(1)))
    finally:
        model.close()
    series = []
    specs = ((0, 'segment_start', None, None), (1, 'observation', 10., None),
             (2, 'observation', 30., None), (2, 'observation', 50., None),
             (3, 'gap', None, '7'), (4, 'gap', None, None),
             (100_000_000, 'observation', None, None), (100_000_001, 'observation', None, None),
             (100_000_007, 'segment_end', None, None))
    for number, data in enumerate(original.series):
        template = data.at_event(1)
        rows = []
        for index, (ns, event, value, missing) in enumerate(specs):
            reading = None if value is None else replace(template.reading, mean_c=value + number * 2,
                min_c=value - 100, max_c=value + 100)
            status = ('other_native_space' if index == 7 else 'measurement_unavailable') if event == 'observation' and reading is None else 'valid' if reading else event
            rows.append(replace(template, index=index, time_ns=START + ns, event=event, status=status,
                                reading=reading, gap_count=missing))
        series.append(replace(data, first_ns=START, last_ns=START + specs[-1][0], rows=tuple(rows),
                              indices=tuple(r.index for r in rows), times=tuple(r.time_ns for r in rows)))
    return Comparison(original.selections, tuple(series))


class RangeStatisticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = known_comparison()

    def test_inclusive_ns_duplicate_events_and_arithmetic_not_duration_or_pixel_weighting(self):
        result = summarize_range(self.data, START, START + 100_000_007, difference=('S1', 'S2'))
        row = result.rows[0]
        self.assertEqual((10., 50., 30., 3, 2), (row.minimum_c, row.maximum_c, row.mean_c, row.valid_observations, row.unavailable_observations))
        self.assertEqual((2, 7, 1, 2, 1), (row.gap_events, row.known_missing_observations, row.unknown_gap_events, row.control_events, row.incompatible_observations))
        self.assertEqual((START + 1, START + 2), (row.first_valid_ns, row.last_valid_ns))
        self.assertEqual((14., 54., 34.), (result.rows[2].minimum_c, result.rows[2].maximum_c, result.rows[2].mean_c))
        self.assertEqual((-2., -2., -2.), (result.rows[-1].minimum_c, result.rows[-1].maximum_c, result.rows[-1].mean_c))
        at_equal_time = summarize_range(self.data, START + 2, START + 2).rows[0]
        self.assertEqual((2, 40.), (at_equal_time.valid_observations, at_equal_time.mean_c))

    def test_gap_only_empty_and_control_only_ranges_have_no_fabricated_temperatures(self):
        for ns, gaps, controls in ((3, 1, 0), (5, 0, 0), (0, 0, 1)):
            with self.subTest(ns=ns):
                row = summarize_range(self.data, START + ns, START + ns, difference=('S1', 'S2')).rows[-1]
                self.assertEqual('no_usable_data', row.status)
                self.assertIsNone(row.mean_c)
                self.assertEqual((0, 0, gaps, controls), (row.valid_observations, row.unavailable_observations, row.gap_events, row.control_events))

    def test_incompatible_sessions_and_same_time_different_events_cannot_produce_a_delta(self):
        right = self.data.series[1]
        native = replace(right.selection, identity=('another-session', *right.selection.identity[1:]))
        changed = Comparison((self.data.selections[0], replace(self.data.selections[1], native=native)),
                             (self.data.series[0], replace(right, selection=native)))
        row = summarize_range(changed, START + 1, START + 2, difference=('S1', 'S2')).rows[-1]
        self.assertEqual('incompatible_pair', row.status)
        self.assertEqual((0, 3, None), (row.valid_observations, row.incompatible_observations, row.mean_c))
        shifted = replace(right, rows=tuple(replace(r, index=r.index+100) for r in right.rows), indices=tuple(i+100 for i in right.indices))
        changed = Comparison(self.data.selections[:2], (self.data.series[0], shifted))
        row = summarize_range(changed, START + 1, START + 2, difference=('S1', 'S2')).rows[-1]
        self.assertEqual((0, 3, None), (row.valid_observations, row.incompatible_observations, row.mean_c))

    def test_invalid_bounds_limits_and_cooperative_cancellation(self):
        for bounds in ((float(START), START+1), (START+2, START+1), (START-1, START), (START, START+100_000_008)):
            with self.assertRaises(ValueError):
                summarize_range(self.data, *bounds)
        with patch('lmtr_range_statistics.MAX_TEMPORAL_EVENTS', 8), self.assertRaises(ValueError):
            summarize_range(self.data, START, START+1)
        with self.assertRaises(InterruptedError):
            summarize_range(self.data, START, START+1, cancelled=lambda: True)

    def test_summary_csv_retains_bounds_identity_exact_values_and_exclusive_cancelled_publication(self):
        result = summarize_range(self.data, START+1, START+4, difference=('S1', 'S2'))
        with TemporaryDirectory() as folder:
            path = Path(folder)/'summary.csv'
            self.assertEqual(5, write_range_summary_csv(result, path))
            with path.open(newline='') as data:
                rows = list(csv.DictReader(data))
            self.assertEqual(str(START+1), rows[0]['range_start_ns'])
            self.assertEqual('arithmetic_mean_of_valid_observations', rows[0]['aggregation'])
            self.assertEqual('roi_mean_temperature', rows[2]['metric'])
            self.assertEqual(result.rows[2].mean_c.hex(), float(rows[2]['mean_c']).hex())
            self.assertEqual(('S1', 'S2', '7', '1'), tuple(rows[-1][k] for k in ('left_selection_id', 'right_selection_id', 'known_missing_observations', 'unknown_gap_events')))
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                write_range_summary_csv(result, path)
            self.assertEqual(before, path.read_bytes())
            checks = []
            def cancel():
                checks.append(True)
                return len(checks) > 3
            partial = Path(folder)/'partial.csv'
            with self.assertRaises(InterruptedError):
                write_range_summary_csv(result, partial, cancelled=cancel)
            self.assertFalse(partial.exists())


class RangeWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = ComparisonPanel()
        self.data = known_comparison()
        self.panel.selections = self.data.selections
        self.panel.set_result(self.data)
        self.chart = self.panel.chart
        self.chart.resize(900, 200)
        self.chart.show()
        self.stats = self.panel.range_panel
        self.wait(lambda: self.stats.result is not None)

    def tearDown(self):
        self.panel.reset()
        self.panel.plot_panel.close()
        self.stats.close()
        self.panel.close()

    def wait(self, condition):
        deadline = time.monotonic()+5
        while not condition() and time.monotonic()<deadline:
            self.app.processEvents()
        self.assertTrue(condition())

    def mouse(self, kind, x, y, buttons):
        self.app.sendEvent(self.chart, QMouseEvent(kind, QPointF(x,y), Qt.MouseButton.LeftButton,
                                                  buttons, Qt.KeyboardModifier.NoModifier))

    def test_range_gesture_handles_and_cursor_never_seek_or_rescan_playback(self):
        self.panel.set_cursor(START+1, 1)
        seek = QSignalSpy(self.chart.seek_event)
        data = self.panel.comparison.series
        self.chart.set_range_mode(True)
        y = self.chart._bounds().center().y()
        a, b = START+4, START+100_000_000
        self.mouse(QEvent.Type.MouseButtonPress, self.chart.time_x(a)+20, y, Qt.MouseButton.LeftButton)
        self.mouse(QEvent.Type.MouseButtonRelease, self.chart.time_x(b), y, Qt.MouseButton.NoButton)
        self.wait(lambda: self.stats.result is not None)
        self.assertEqual((a, b), self.stats.bounds)
        self.assertEqual(0, len(seek))
        self.assertEqual(1, self.chart._cursor_index)
        self.assertIs(data, self.panel.comparison.series)
        self.chart.set_range_mode(False)
        self.mouse(QEvent.Type.MouseButtonPress, self.chart.time_x(b), self.chart._bounds().top()-3, Qt.MouseButton.LeftButton)
        self.mouse(QEvent.Type.MouseButtonRelease, self.chart.time_x(START+4), y, Qt.MouseButton.NoButton)
        self.wait(lambda: self.stats.result is not None)
        self.assertIn(self.stats.bounds[1], self.data.series[0].times)
        self.assertEqual(0, len(seek))
        self.stats.full.click()
        self.wait(lambda: self.stats.result is not None)
        self.assertEqual((START, START+100_000_007), self.stats.bounds)
        self.mouse(QEvent.Type.MouseButtonPress, self.chart.time_x(START+2), y, Qt.MouseButton.LeftButton)
        self.assertEqual(1, len(seek))

    def test_latest_range_cancel_reset_and_metadata_edits_do_not_publish_stale_statistics(self):
        with patch('lmtr_range_statistics.BATCH_EVENTS', 1):
            self.stats.set_bounds((START, START+100_000_007))
            self.stats._advance()
            self.assertIsNone(self.stats.result)
            self.stats.cancel.click()
            self.assertFalse(self.stats.timer.isActive())
            self.stats.set_bounds((START, START+2))
            self.stats.set_bounds((START+2, START+2))
            self.wait(lambda: self.stats.result is not None)
        self.assertEqual(40., self.stats.result.rows[0].mean_c)
        rows = self.stats.result.rows
        self.panel._apply_name('S1', 'New label')
        self.assertIs(rows, self.stats.result.rows)
        self.assertIn('New label', self.stats.table.item(0,0).text())
        self.stats.set_bounds((START, START+100_000_007))
        self.panel.reset()
        self.app.processEvents()
        self.assertIsNone(self.stats.result)
        self.assertIsNone(self.chart.analysis_bounds)
        self.assertFalse(self.stats.timer.isActive())

    def test_coincident_handles_can_expand_in_either_direction_with_exact_exported_bounds(self):
        first, last = self.data.series[0].first_ns, self.data.series[0].last_ns
        instant = START + 100_000_000
        seek = QSignalSpy(self.chart.seek_event)
        y = self.chart._bounds().top() - 3

        def drag(source, x):
            self.mouse(QEvent.Type.MouseButtonPress, self.chart.time_x(source), y, Qt.MouseButton.LeftButton)
            self.mouse(QEvent.Type.MouseMove, x, y, Qt.MouseButton.LeftButton)
            self.mouse(QEvent.Type.MouseButtonRelease, x, y, Qt.MouseButton.NoButton)
            self.wait(lambda: self.stats.result is not None)

        def check(bounds, valid, mean):
            self.assertEqual(bounds, self.chart.analysis_bounds)
            self.assertEqual(bounds, self.stats.bounds)
            summary = self.stats.result
            self.assertEqual(bounds, (summary.start_ns, summary.end_ns))
            self.assertEqual((valid, mean), (summary.rows[0].valid_observations, summary.rows[0].mean_c))
            with TemporaryDirectory() as folder:
                target = Path(folder) / 'range.csv'
                write_range_summary_csv(summary, target)
                with target.open(newline='') as output:
                    rows = list(csv.DictReader(output))
                self.assertEqual({tuple(map(str, bounds))},
                                 {(r['range_start_ns'], r['range_end_ns']) for r in rows})

        self.stats.set_bounds((instant, instant))
        drag(instant, self.chart.time_x(instant))
        check((instant, instant), 0, None)
        drag(instant, self.chart.time_x(first) - 30)
        check((first, instant), 3, 30.0)
        # Bring A back onto B. A remains selected throughout this drag even
        # when it crosses B, so it clamps rather than moving the other bound.
        drag(first, self.chart.time_x(last) + 30)
        check((instant, instant), 0, None)
        drag(instant, self.chart.time_x(last) + 30)
        check((instant, last), 0, None)
        drag(last, self.chart.time_x(first) - 30)
        check((instant, instant), 0, None)
        self.assertEqual(0, len(seek))
