"""Focused regressions for cancellation, incompatible pairs and readable plot layout."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest

from lmthermal_desktop import DesktopWorkspace
from lmtr_comparison import collect_comparison
from lmtr_temporal import TemporalSelection, temporal_csv_record
from lmtr_reference.container import write_plan
from lmtr_test_support import FILES, segmented_plan
from offline_measurement import Rectangle


class AnalysisFinishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.workspace = DesktopWorkspace()
        self.workspace.show()
        self.viewer = self.workspace.open_path(FILES / 'illustration-01-stored.lmtr')
        self.wait(lambda: self.viewer.state and not self.viewer._in_flight)
        self.viewer.temporal_button.click()
        self.panel = self.viewer.temporal_panel
        for region in (Rectangle(1, 0, 2, 1), Rectangle(2, 0, 3, 1)):
            self.panel.add_selection(TemporalSelection.from_frame(self.viewer.frame, 'point', region))

    def wait(self, condition):
        end = time.monotonic() + 8
        while not condition() and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(.002)
        self.assertTrue(condition())

    def tearDown(self):
        self.workspace.close()
        self.wait(lambda: not self.workspace._documents)

    def calculate(self):
        self.panel.compute.click()
        self.wait(lambda: self.panel.comparison is not None and not self.viewer._in_flight)

    def test_cancel_calculation_retains_open_file_then_allows_a_new_calculation(self):
        entered, proceed = threading.Event(), threading.Event()
        def slow(model, selected):
            entered.set()
            if not proceed.wait(5):
                raise TimeoutError('Test did not release the active scan')
            return collect_comparison(model, selected)
        with patch('lmtr_viewer.collect_comparison', side_effect=slow):
            try:
                self.panel.compute.click()
                token = self.viewer.token
                self.assertTrue(entered.wait(5))
                self.assertTrue(self.panel.cancel_button.isEnabled())
                self.panel.cancel_button.click()
                self.assertEqual('Cancelling…', self.panel.message.text())
            finally:
                proceed.set()
            self.wait(lambda: not self.viewer._in_flight)
        self.assertEqual('Analysis cancelled', self.panel.message.text())
        self.assertIsNotNone(self.viewer.frame)
        self.assertIsNone(self.panel.comparison)
        self.calculate()
        self.assertEqual(30, self.panel.comparison.series[0].at_event(1).temperature)
        self.assertFalse(self.viewer.worker.cancel_analysis(token, self.viewer._generation))

    def test_cancel_queued_analysis_before_worker_can_take_the_request(self):
        self.calculate()
        prior = self.panel.comparison
        # The same condition guards queue ownership. Hold it on the GUI thread
        # until Cancel has run, so the worker cannot dequeue in this test.
        with patch('lmtr_viewer.collect_comparison', wraps=collect_comparison) as collect:
            with self.viewer.worker.condition:
                self.panel.compute.click()
                self.assertIsNotNone(self.viewer.worker.pending)
                self.panel.cancel_button.click()
                self.assertEqual('Cancelling…', self.panel.message.text())
            self.wait(lambda: not self.viewer._in_flight)
            collect.assert_not_called()
        self.assertEqual('Analysis cancelled', self.panel.message.text())
        self.assertIs(prior, self.panel.comparison)
        self.calculate()
        self.assertIsNot(prior, self.panel.comparison)

    def test_cancel_at_worker_handoff_cannot_publish_a_result(self):
        self.calculate()
        prior = self.panel.comparison
        worker = self.viewer.worker
        entered, proceed = threading.Event(), threading.Event()
        analyse = worker._analyse

        def pause_at_entry(*args):
            entered.set()
            if not proceed.wait(5):
                raise TimeoutError('Test did not release the worker handoff')
            return analyse(*args)

        with patch.object(worker, '_analyse', side_effect=pause_at_entry), \
                patch('lmtr_viewer.collect_comparison', wraps=collect_comparison) as collect:
            try:
                self.panel.compute.click()
                abandoned_token = self.viewer.token
                self.assertTrue(entered.wait(5))
                self.assertIsNone(worker.pending)
                self.panel.cancel_button.click()
                self.assertEqual('Cancelling…', self.panel.message.text())
            finally:
                proceed.set()
            self.wait(lambda: not self.viewer._in_flight)
            collect.assert_not_called()
        self.assertEqual('Analysis cancelled', self.panel.message.text())
        self.assertIs(prior, self.panel.comparison)
        self.calculate()
        current = self.panel.comparison
        self.assertIsNot(prior, current)
        # Even a result already queued on the Qt side must be ignored after the
        # request token has advanced.
        with patch.object(worker, 'take', return_value=(abandoned_token, None, None, None)):
            self.viewer._receive()
        self.assertIs(current, self.panel.comparison)

    def test_cancel_export_removes_partial_csv_and_preserves_playback_and_results(self):
        self.calculate()
        complete = self.panel.comparison
        entered = threading.Event()
        def delayed_record(series, row):
            if row.index == 1:
                entered.set()
                deadline = time.monotonic() + 5
                while not self.viewer.worker._analysis_cancel.is_set() and time.monotonic() < deadline:
                    time.sleep(.002)
            return temporal_csv_record(series, row)
        with TemporaryDirectory() as folder:
            target = Path(folder) / 'partial.csv'
            with patch('lmtr_comparison.temporal_csv_record', side_effect=delayed_record), patch(
                    'lmtr_viewer.QFileDialog.getSaveFileName', return_value=(str(target), 'CSV')):
                self.panel.save_csv.click()
                self.wait(entered.is_set)
                self.panel.cancel_button.click()
                self.wait(lambda: not self.viewer._in_flight)
            self.assertFalse(target.exists())
        self.assertEqual('CSV cancelled', self.panel.message.text())
        self.assertIs(self.panel.comparison, complete)
        self.viewer.next.click()
        self.wait(lambda: not self.viewer._in_flight)
        self.assertEqual(2, self.viewer.state['index'])

    def test_difference_has_independent_scale_and_full_width_plot_fits_small_window(self):
        self.calculate()
        self.workspace.resize(940, 740)
        self.app.processEvents()
        self.panel.chart.grab()
        absolute = self.panel.chart._range
        self.panel.difference_check.click()
        self.panel.chart.grab()
        self.assertEqual(absolute, self.panel.chart._range)
        self.assertNotEqual(absolute, self.panel.chart._delta_range)
        self.assertLess(self.panel.chart._plot_bounds().bottom(), self.panel.chart._plot_bounds(True).top())
        self.assertLessEqual(self.workspace.width(), 940)
        self.assertLessEqual(self.workspace.height(), 740)
        self.assertGreater(self.panel.chart.width(), 850)
        self.assertTrue(self.panel.reading.isVisible())

    def test_name_typing_updates_export_metadata_and_operand_labels_without_focus_loss(self):
        self.calculate()
        self.panel.chart.grab()
        cached_layer = self.panel.chart._layer
        data = self.panel.comparison.series
        name = self.panel.item_widgets['S1']['name']
        name.selectAll()
        QTest.keyClicks(name, 'Point A')
        self.assertEqual('Point A', self.panel.selections[0].name)
        self.assertIn('Point A', self.panel.left.currentText())
        self.assertIn('Point A', self.panel.reading.text())
        self.assertEqual('Point A', self.panel.comparison.selections[0].name)
        self.assertIs(data, self.panel.comparison.series)
        self.assertIs(cached_layer, self.panel.chart._layer)

    def test_incompatible_sessions_have_visible_refusal_and_no_difference_trace(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / 'two-sessions.lmtr'
            write_plan(segmented_plan(same_geometry=True), source)
            before = source.read_bytes()
            self.viewer.open_path(source)
            self.wait(lambda: self.viewer.state and not self.viewer._in_flight)
            self.panel.add_selection(TemporalSelection.from_frame(self.viewer.frame, 'point', Rectangle(1, 0, 2, 1)))
            self.viewer.slider.setValue(4)
            self.wait(lambda: not self.viewer._in_flight)
            self.panel.add_selection(TemporalSelection.from_frame(self.viewer.frame, 'point', Rectangle(1, 0, 2, 1)))
            self.calculate()
            self.panel.difference_check.click()
            self.assertIn('Different streams, source sessions or native grids', self.panel.difference_note.text())
            self.assertIsNone(self.panel.difference_pair())
            self.assertTrue(all(t.right is None for t in self.panel.chart.tracks))
            self.assertEqual('other_native_space', self.panel.comparison.series[0].at_event(4).status)
            self.assertEqual(before, source.read_bytes())
            self.workspace.close()
            self.wait(lambda: not self.workspace._documents)

    def test_range_changes_reuse_scalars_without_file_worker_or_seek_requests(self):
        self.calculate()
        stats = self.panel.range_panel
        self.wait(lambda: stats.result is not None)
        series = self.panel.comparison.series
        instant = series[0].at_event(1).time_ns
        with patch.object(self.viewer, '_request', wraps=self.viewer._request) as requests:
            stats.set_bounds((instant, instant))
            self.wait(lambda: stats.result is not None)
            self.assertEqual(0, requests.call_count)
        self.assertIs(series, self.panel.comparison.series)
        self.assertEqual(1, self.viewer.state['index'])

    def test_summary_csv_publishes_complete_snapshot_and_refuses_a_range_changed_in_the_chooser(self):
        self.calculate()
        stats = self.panel.range_panel
        self.wait(lambda: stats.result is not None)
        with TemporaryDirectory() as folder:
            target = Path(folder)/'summary.csv'
            with patch('lmtr_viewer.QFileDialog.getSaveFileName', return_value=(str(target), 'CSV')):
                stats.save.click()
                self.wait(lambda: not self.viewer._in_flight)
            self.assertTrue(target.exists())
            self.assertIn('Summary CSV saved', stats.message.text())
            rejected = Path(folder)/'stale.csv'
            def change_range(*args):
                stats.full_period()
                return str(rejected), 'CSV'
            with patch('lmtr_viewer.QFileDialog.getSaveFileName', side_effect=change_range):
                stats.save.click()
            self.wait(lambda: stats.result is not None)
            self.assertFalse(rejected.exists())
