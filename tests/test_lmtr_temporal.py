"""Camera-free checks for the temporal series, exact CSV and playback synchronization."""
import csv
import math
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import threading
import time
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from lmthermal_desktop import DesktopWorkspace
from lmtr_reader import LmtrPlaybackModel
from lmtr_reference.container import write_plan
from lmtr_temporal import TemporalLimitError, TemporalSelection, collect_temporal_series, write_temporal_csv
from lmtr_test_support import FILES, gap_plan, segmented_plan
from mvp_presentation import native_to_widget
from offline_measurement import Rectangle


def read_csv(path):
    with path.open(newline='', encoding='utf-8') as input_file:
        return list(csv.DictReader(input_file))


class TemporalSeriesTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def open_model(self, name, **kwargs):
        path = name if isinstance(name, Path) else FILES / (name + '.lmtr')
        model = LmtrPlaybackModel(path, **kwargs)
        self.addCleanup(model.close)
        return model

    def collect(self, model, region=Rectangle(1, 0, 2, 1), kind='point'):
        selected = TemporalSelection.from_frame(model.frame(1), kind, region)
        return collect_temporal_series(model, selected)

    def test_point_keeps_signed_zero_and_ignores_masked_positive_zero(self):
        model = self.open_model('illustration-01-deflate1')
        valid = self.collect(model, Rectangle(2, 0, 3, 1))
        invalid = self.collect(model, Rectangle(0, 1, 1, 2))
        self.assertEqual(-1.0, math.copysign(1, valid.at_event(1).temperature))
        self.assertEqual('no_valid_pixels', invalid.at_event(1).status)
        self.assertIsNone(invalid.at_event(1).temperature)
        path = self.directory / 'points.csv'
        write_temporal_csv(valid, path)
        self.assertEqual('-0.0', read_csv(path)[1]['temperature_c'])

    def test_roi_matches_inspection_with_mask_and_an_exact_mean_round_trip(self):
        model = self.open_model('illustration-01-stored')
        for region in (Rectangle(0, 0, 3, 2), Rectangle(0, 0, 3, 1)):
            expected = model.frame(1).roi_statistics(region)
            series = self.collect(model, region, 'roi')
            self.assertEqual(expected, series.at_event(1).reading)
            path = self.directory / f'roi-{region.y2}.csv'
            write_temporal_csv(series, path)
            row = read_csv(path)[1]
            self.assertEqual(expected.mean_c.hex(), float(row['temperature_c']).hex())
            self.assertEqual(expected.valid_pixel_count, int(row['valid_pixels']))
            self.assertEqual(expected.min_c, float(row['roi_min_c']))
            self.assertEqual(expected.max_c, float(row['roi_max_c']))

    def test_csv_preserves_original_intake_receipt_and_periodic_fields_as_integers(self):
        model = self.open_model('periodic-sampling')
        series = self.collect(model)
        path = self.directory / 'periodic.csv'
        write_temporal_csv(series, path)
        rows = read_csv(path)
        self.assertEqual(len(rows), model.entry_count)
        for row in rows:
            source = model.original_entry(int(row['entry_id']))
            self.assertEqual(source['timeline_ns'], row['timeline_ns'])
            self.assertEqual(int(source['timeline_ns']) - series.first_ns, int(row['elapsed_ns']))
        source = model.original_entry(1)
        self.assertEqual(source['receipt']['stamp']['ticks'], rows[1]['receipt_ticks'])
        self.assertEqual(source['scheduled']['first_slot'], rows[1]['scheduled_first_slot'])
        self.assertEqual(source['scheduled']['first_deadline_ns'], rows[1]['scheduled_first_deadline_ns'])
        self.assertEqual('completed', rows[1]['end_status'])

    def test_known_and_unknown_gaps_unavailable_and_recovery_stay_distinct(self):
        plan = gap_plan()
        gap = plan['chunks'][0]['metadata']['entries'][2]
        gap.update(count=None, observation_first=None, observation_last=None)
        gap['evidence']['basis'] = 'unknown'
        unknown_path = self.directory / 'unknown-gap.lmtr'
        write_plan(plan, unknown_path)
        examples = (
            ('illustration-06-stored', 'gap', '2', 'known', False),
            (unknown_path, 'gap', '', 'unknown', False),
            ('illustration-05-stored', 'measurement_unavailable', '', '', False),
            ('missing-end', 'valid', '', '', True),
        )
        for index, (file, status, count, count_status, recover) in enumerate(examples):
            with self.subTest(file=file):
                model = self.open_model(file, recover=recover)
                series = self.collect(model)
                path = self.directory / f'events-{index}.csv'
                write_temporal_csv(series, path)
                rows = read_csv(path)
                row = next(row for row in rows if row['status'] == status)
                self.assertEqual(count, row['gap_count'])
                self.assertEqual(count_status, row['gap_count_status'])
                self.assertEqual(str(recover).lower(), row['unknown_tail'])
                if status != 'valid':
                    self.assertEqual('', row['temperature_c'])
                if status == 'measurement_unavailable':
                    self.assertEqual('shutter_transient', row['reason'])
                self.assertEqual(model.entry_count, len(rows))

    def test_other_source_session_cannot_inherit_coordinates_even_with_same_dimensions(self):
        path = self.directory / 'different-session.lmtr'
        write_plan(segmented_plan(same_geometry=True), path)
        before = path.read_bytes()
        model = self.open_model(path)
        series = self.collect(model)
        self.assertEqual('valid', series.at_event(1).status)
        self.assertEqual('other_native_space', series.at_event(4).status)
        self.assertIsNone(series.at_event(4).temperature)
        self.assertNotEqual(series.selection.identity[0], series.at_event(4).session_id)
        self.assertEqual(25, model.frame(4).point(1, 0))
        self.assertEqual(before, path.read_bytes())

    def test_scan_isolates_reader_reporting_and_cancels_without_a_partial_series(self):
        path = self.directory / 'segments.lmtr'
        write_plan(segmented_plan(), path)
        cancelled = threading.Event()
        model = self.open_model(path, cancelled=cancelled.is_set)
        frame = model.frame(1)
        selected = TemporalSelection.from_frame(frame, 'point', Rectangle(1, 0, 2, 1))
        owner = model.reader
        prior_hashes = dict(owner.logical)
        collect_temporal_series(model, selected)
        self.assertIs(owner, model.reader)
        self.assertEqual(prior_hashes, owner.logical)
        visits = []
        orig = model._chunk

        def cancel_after_initial_chunk(index):
            chunk = orig(index)
            visits.append(index)
            if index > 0:
                cancelled.set()
            return chunk

        with patch.object(model, '_chunk', side_effect=cancel_after_initial_chunk):
            with self.assertRaises(InterruptedError):
                collect_temporal_series(model, selected)
        self.assertEqual([0, 3], visits)
        self.assertIs(owner, model.reader)
        self.assertEqual(0, model.cache_size)

    def test_refuses_oversized_series_and_exclusive_csv_preserves_the_source(self):
        model = self.open_model('illustration-01-stored')
        before = model.path.read_bytes()
        series = self.collect(model)
        with self.assertRaises(FileExistsError):
            write_temporal_csv(series, model.path)
        path = self.directory / 'cancelled.csv'
        decisions = iter((False, False, True))
        with self.assertRaises(InterruptedError):
            write_temporal_csv(series, path, cancelled=lambda: next(decisions))
        self.assertFalse(path.exists())
        self.assertEqual(before, model.path.read_bytes())
        with patch('lmtr_temporal.MAX_TEMPORAL_EVENTS', 2):
            with self.assertRaises(TemporalLimitError):
                self.collect(model)
        self.assertEqual(30, model.frame(1).point(1, 0))


class TemporalWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.workspace = DesktopWorkspace()
        self.workspace.show()

    def wait(self, condition):
        end = time.monotonic() + 8
        while not condition() and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(.003)
        self.assertTrue(condition())

    def tearDown(self):
        self.workspace.close()
        self.wait(lambda: not self.workspace._documents)

    def open(self, name):
        viewer = self.workspace.open_path(FILES / (name + '.lmtr'))
        self.wait(lambda: viewer.state and not viewer._in_flight)
        viewer.temporal_button.click()
        return viewer

    def select_point(self, viewer, x, y):
        where = native_to_widget(x, y, viewer.image.width(), viewer.image.height(),
                                 geometry=viewer.frame.geometry)
        QTest.mouseClick(viewer.image, Qt.MouseButton.LeftButton, pos=QPoint(*map(round, where)))
        viewer.plot_point.click()
        viewer.temporal_panel.compute.click()
        self.wait(lambda: viewer.chart.series is not None and not viewer._in_flight)

    def test_workspace_chart_gap_seek_csv_and_playback_cursor(self):
        viewer = self.open('illustration-06-stored')
        self.workspace.resize(940, 710)
        self.app.processEvents()
        self.assertLessEqual(self.workspace.width(), 940)
        self.assertTrue(viewer.temporal_panel.isVisible())
        self.select_point(viewer, 1, 0)
        self.assertEqual(25, viewer.chart.series.at_event(1).temperature)
        chart = viewer.chart
        point = QPoint(round(chart.time_x(chart.series.at_event(2).time_ns)),
                       round(chart._bounds().bottom() + 7))
        QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=point)
        self.wait(lambda: viewer.state['index'] == 2 and not viewer._in_flight)
        self.assertIsNone(viewer.frame)
        self.assertIn('writer_overload', viewer.temporal_reading.text())
        viewer.next.click()
        self.wait(lambda: viewer.state['index'] == 3 and not viewer._in_flight)
        self.assertEqual(3, chart._cursor_index)
        self.assertEqual(int(viewer.state['timing']['timeline_ns']), chart._cursor_ns)
        viewer.palette.setCurrentText('Turbo')
        self.assertEqual(25, chart.series.at_event(1).temperature)
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'export.csv'
            with patch('lmtr_viewer.QFileDialog.getSaveFileName', return_value=(str(path), 'CSV')):
                viewer.save_csv.click()
                self.wait(lambda: not viewer._in_flight)
            rows = read_csv(path)
            self.assertEqual('', rows[2]['temperature_c'])
            self.assertIn('CSV saved', viewer.temporal_message.text())
        viewer.play.click()
        self.wait(lambda: viewer.state['index'] == 4 and not viewer.state['playing'])
        self.assertEqual(4, chart._cursor_index)
        self.assertIsNone(self.workspace._live)

    def test_source_replacement_discards_old_chart_and_its_export(self):
        viewer = self.open('illustration-01-stored')
        self.select_point(viewer, 1, 0)
        viewer.open_path(FILES / 'illustration-05-stored.lmtr')
        self.assertIsNone(viewer.chart.series)
        self.assertFalse(viewer.save_csv.isEnabled())
        self.wait(lambda: viewer.state and not viewer._in_flight)
        self.assertIsNone(viewer.chart.series)
