"""Focused multi-selection, exact-difference, gap, CSV and workspace regressions."""
from dataclasses import replace
import csv
import math
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

from lmthermal_desktop import DesktopWorkspace
from lmtr_comparison import (Comparison, NamedSelection, CURVE_COLORS, collect_comparison,
                             difference_value, write_comparison_csv)
from lmtr_reader import LmtrPlaybackModel
from lmtr_reference.container import write_plan
from lmtr_temporal import TemporalSelection, TemporalLimitError
from lmtr_test_support import FILES, segmented_plan
from offline_measurement import Rectangle
from roi_measurement import NativeROI


def selections(frame):
    """Two points and two mask-aware ROIs, with stable IDs independent of labels."""
    specs = (('point', Rectangle(1, 0, 2, 1)), ('point', Rectangle(2, 0, 3, 1)),
             ('roi', Rectangle(0, 0, 3, 2)), ('roi', Rectangle(0, 0, 3, 1)))
    return tuple(NamedSelection(f'S{i+1}', f'Selection {i+1}', CURVE_COLORS[i],
                                TemporalSelection.from_frame(frame, kind, region))
                 for i, (kind, region) in enumerate(specs))


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def model(self, name='illustration-01-stored', **kwargs):
        model = LmtrPlaybackModel(name if isinstance(name, Path) else FILES / f'{name}.lmtr', **kwargs)
        self.addCleanup(model.close)
        return model

    def test_four_selections_share_one_traversal_and_roi_statistics(self):
        model = self.model()
        frame = model.frame(1)
        selected = selections(frame)
        with patch.object(model, '_visit_entries', wraps=model._visit_entries) as scan:
            comparison = collect_comparison(model, selected)
        self.assertEqual(1, scan.call_count)
        self.assertEqual(30, comparison.series[0].at_event(1).temperature)
        self.assertEqual(-1, math.copysign(1, comparison.series[1].at_event(1).temperature))
        for item, series in zip(selected[2:], comparison.series[2:]):
            self.assertEqual(frame.roi_statistics(item.native.region), series.at_event(1).reading)
        for pair in (('S1', 'S2'), ('S3', 'S4'), ('S1', 'S3')):
            rows = list(comparison.difference_rows(pair))
            a, b, value, status = rows[1]
            self.assertEqual('valid', status)
            self.assertEqual(a.temperature - b.temperature, value)
            self.assertIsNone(rows[0][2])

    def test_differences_require_same_event_time_session_geometry_recording_and_clock(self):
        model = self.model()
        comparison = collect_comparison(model, selections(model.frame(1))[:2])
        a = comparison.series[0].at_event(1)
        for changes in (dict(index=a.index+1), dict(time_ns=a.time_ns+1),
                        dict(segment_id='other'), dict(session_id='other')):
            with self.subTest(changes=changes):
                self.assertEqual((None, 'incompatible_observation'), difference_value(a, replace(a, **changes)))
        left, right = comparison.series
        for changed in (replace(right, recording_id='other'), replace(right, clock_domain_id='other'),
                        replace(right, selection=replace(right.selection, stream_id='other')),
                        replace(right, selection=replace(right.selection, identity=('other', *right.selection.identity[1:]))),
                        replace(right, selection=replace(right.selection, identity=(*right.selection.identity[:3], (7, 4))))):
            named = (comparison.selections[0], replace(comparison.selections[1], native=changed.selection))
            other = Comparison(named, (left, changed))
            self.assertIsNotNone(other.difference_error(('S1', 'S2')))
            with self.assertRaises(ValueError):
                list(other.difference_rows(('S1', 'S2')))

    def test_gaps_unavailable_and_invalid_pixels_never_become_zero_differences(self):
        for name, recover in (('illustration-06-stored', False), ('illustration-05-stored', False), ('missing-end', True)):
            with self.subTest(name=name):
                model = self.model(name, recover=recover)
                comparison = collect_comparison(model, selections(model.frame(1))[:2])
                for a, b, value, status in comparison.difference_rows(('S1', 'S2')):
                    if a.status != 'valid' or b.status != 'valid':
                        self.assertIsNone(value)
                        self.assertEqual('operands_unavailable', status)
                self.assertEqual(recover, comparison.series[0].unknown_tail)
        model = self.model()
        selected = selections(model.frame(1))[:2]
        invalid = replace(selected[1], native=TemporalSelection.from_frame(model.frame(1), 'point', Rectangle(0, 1, 1, 2)))
        comparison = collect_comparison(model, (selected[0], invalid))
        self.assertIsNone(list(comparison.difference_rows(('S1', 'S2')))[1][2])

    def test_session_change_keeps_each_selection_in_its_original_native_space(self):
        path = self.folder / 'sessions.lmtr'
        write_plan(segmented_plan(same_geometry=True), path)
        model = self.model(path)
        first = selections(model.frame(1))[0]
        later = replace(selections(model.frame(4))[1], id='later')
        comparison = collect_comparison(model, (first, later))
        self.assertEqual('other_native_space', comparison.series[0].at_event(4).status)
        self.assertEqual('other_native_space', comparison.series[1].at_event(1).status)
        self.assertIsNotNone(comparison.difference_error(('S1', 'later')))

    def test_csv_keeps_ids_precision_hidden_selections_evidence_and_blank_delta_gaps(self):
        model = self.model('periodic-sampling')
        chosen = selections(model.frame(1))
        chosen = (replace(chosen[0], name='Échantillon, "A"', visible=False), *chosen[1:])
        comparison = collect_comparison(model, chosen)
        before = model.path.read_bytes()
        path = self.folder / 'comparison.csv'
        count = write_comparison_csv(comparison, path, difference=('S1', 'S3'))
        with path.open(newline='', encoding='utf-8') as source:
            rows = list(csv.DictReader(source))
        self.assertEqual(model.entry_count * 5, count)
        self.assertEqual({'S1', 'S2', 'S3', 'S4'}, {r['selection_id'] for r in rows if r['row_kind'] == 'selection'})
        for row in rows:
            entry = model.original_entry(int(row['entry_id']))
            self.assertEqual(entry['timeline_ns'], row['timeline_ns'])
            self.assertEqual(entry.get('scheduled', {}).get('first_deadline_ns', '') if entry.get('scheduled') else '', row['scheduled_first_deadline_ns'])
            if row['row_kind'] == 'difference':
                self.assertEqual(('S1', 'S3'), (row['left_selection_id'], row['right_selection_id']))
                if row['status'] != 'valid':
                    self.assertEqual('', row['temperature_c'])
            else:
                data = comparison.by_id(row['selection_id']).at_event(int(row['entry_id']))
                if data.temperature is not None:
                    self.assertEqual(data.temperature.hex(), float(row['temperature_c']).hex())
        self.assertEqual('false', rows[0]['visible'])
        self.assertEqual('Échantillon, "A"', rows[0]['selection_name'])
        with self.assertRaises(FileExistsError):
            write_comparison_csv(comparison, model.path)
        self.assertEqual(before, model.path.read_bytes())

    def test_limits_and_cancellation_leave_no_partial_comparison_or_export(self):
        cancelled = threading.Event()
        model = self.model(cancelled=cancelled.is_set)
        selected = selections(model.frame(1))
        with self.assertRaises(ValueError):
            collect_comparison(model, selected + (replace(selected[0], id='S5'),))
        with patch('lmtr_temporal.MAX_TEMPORAL_EVENTS', 2):
            with self.assertRaises(TemporalLimitError):
                collect_comparison(model, selected)
        with patch('lmtr_temporal.MAX_TEMPORAL_TEXT_BYTES', 1):
            with self.assertRaises(TemporalLimitError):
                collect_comparison(model, selected)
        comparison = collect_comparison(model, selected)
        cancelled.set()
        with self.assertRaises(InterruptedError):
            collect_comparison(model, selected)
        self.assertEqual(0, model.cache_size)
        path = self.folder / 'partial.csv'
        calls = iter((False, False, True))
        with self.assertRaises(InterruptedError):
            write_comparison_csv(comparison, path, cancelled=lambda: next(calls))
        self.assertFalse(path.exists())


class ComparisonWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.workspace = DesktopWorkspace()
        self.workspace.resize(1000, 800)
        self.workspace.show()
        self.viewer = self.workspace.open_path(FILES / 'illustration-01-stored.lmtr')
        self.wait(lambda: self.viewer.state and not self.viewer._in_flight)
        self.viewer.temporal_button.click()
        self.panel = self.viewer.temporal_panel

    def wait(self, condition):
        deadline = time.monotonic() + 8
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.003)
        self.assertTrue(condition())

    def tearDown(self):
        self.workspace.close()
        self.wait(lambda: not self.workspace._documents)

    def add_four(self):
        for item in selections(self.viewer.frame):
            r = item.native.region
            self.viewer.image.roi = NativeROI(r.x1, r.y1, r.x2, r.y2)
            self.viewer._roi(self.viewer.image.roi)
            (self.panel.plot_point if item.native.kind == 'point' else self.panel.plot_roi).click()

    def test_four_editable_curves_extrema_difference_removal_and_export_without_rescanning(self):
        self.add_four()
        self.assertEqual(4, len(self.panel.selections))
        self.assertFalse(self.panel.plot_roi.isEnabled())
        self.assertIsNone(self.panel.comparison)
        with patch('lmtr_viewer.collect_comparison', wraps=collect_comparison) as scan:
            self.panel.compute.click()
            self.wait(lambda: self.panel.comparison and not self.viewer._in_flight)
            self.panel.item_widgets['S1']['name'].setText('Hot target')
            self.panel.item_widgets['S1']['name'].editingFinished.emit()
            self.panel.item_widgets['S3']['minimum'].click()
            self.panel.item_widgets['S3']['maximum'].click()
            self.assertEqual(6, len(self.panel.chart.tracks))
            self.panel.item_widgets['S2']['visible'].click()
            self.panel.difference_check.click()
            self.assertEqual(('S1', 'S2'), self.panel.difference_pair())
            self.assertEqual(6, len(self.panel.chart.tracks))
            self.assertIn('Δ: 30', self.panel.reading.text())
            with TemporaryDirectory() as folder:
                path = Path(folder) / 'visible-comparison.csv'
                with patch('lmtr_viewer.QFileDialog.getSaveFileName', return_value=(str(path), 'CSV')):
                    self.panel.save_csv.click()
                    self.wait(lambda: not self.viewer._in_flight)
                self.assertTrue(path.exists())
            self.panel.item_widgets['S2']['remove'].click()
            self.assertIsNone(self.panel.difference_pair())
            self.assertEqual(('S1', 'S3', 'S4'), tuple(s.id for s in self.panel.selections))
            self.assertEqual(1, scan.call_count)
        self.assertIsNone(self.workspace._live)
        self.assertLessEqual(self.workspace.width(), 1000)

    def test_replacing_source_while_scanning_cannot_publish_old_comparison(self):
        self.add_four()
        entered, release = threading.Event(), threading.Event()
        original = collect_comparison

        def delayed(model, selected):
            entered.set()
            release.wait(5)
            return original(model, selected)

        with patch('lmtr_viewer.collect_comparison', side_effect=delayed):
            self.panel.compute.click()
            self.wait(entered.is_set)
            self.viewer.open_path(FILES / 'illustration-06-stored.lmtr')
            release.set()
            self.wait(lambda: self.viewer.state and not self.viewer._in_flight)
        self.assertIsNone(self.panel.comparison)
        self.assertEqual((), self.panel.selections)
        self.assertFalse(self.panel.save_csv.isEnabled())
