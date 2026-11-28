"""Camera-free offscreen smoke/regressions; no live camera worker is imported."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import patch
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from lmtr_reader import LmtrPlaybackModel
from lmtr_reference.container import write_plan
from lmtr_test_support import gap_plan
from lmtr_viewer import LmtrWindow
from roi_measurement import NativeROI
from mvp_presentation import native_to_widget

ROOT=Path(__file__).parent/'fixtures/lmtr/files'


class LmtrUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.window=LmtrWindow();self.window.show()
    def tearDown(self):
        self.window.close()
        self.wait(lambda:not self.window.worker.isRunning())
        self.app.processEvents()
        self.assertFalse(self.window.worker.isRunning())
    def wait(self, condition):
        deadline=time.monotonic()+10
        while not condition() and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.003)
        self.assertTrue(condition(),self.window.status.text())
    def opened(self):
        self.window.open_path(ROOT/'illustration-01-stored.lmtr')
        self.wait(lambda:self.window.state is not None)
        self.wait(lambda:self.window.frame is not None)

    def test_normal_open_publishes_initial_observation_without_seek_or_next(self):
        for filename,shape in [('illustration-01-stored.lmtr',(2,3)),('illustration-02-stored.lmtr',(288,384))]:
            self.window.open_path(ROOT/filename)
            self.wait(lambda:self.window.state is not None and not self.window._in_flight)
            self.assertEqual(1,self.window.state['index'])
            self.assertEqual(shape,self.window.frame.geometry.shape)
            self.assertIsNotNone(self.window.image.image)
            self.assertIsNotNone(self.window.legend.bounds)
            self.assertTrue(self.window.save_button.isEnabled())
            # The original segment is still truthful and navigable, never erased or painted with the observation.
            self.window.previous.click();self.wait(lambda:not self.window._in_flight)
            self.assertEqual(0,self.window.state['index']);self.assertIsNone(self.window.frame)
            self.assertIsNone(self.window.image.image)
    def test_ready_frame_hover_roi_palette_and_resize_do_not_change_measurements(self):
        self.opened();w=self.window;frame=w.frame
        w._point((1,0));self.assertIn('30',w.point.text())
        w.image.set_roi(NativeROI(0,0,2,1));self.assertIn('mean 25',w.roi_label.text())
        old=w.roi_label.text()
        w.palette.setCurrentText('Turbo');w.auto.setChecked(False)
        w.minimum.setValue(10);w.maximum.setValue(40);w.resize(900,650)
        self.app.processEvents()
        self.assertEqual(old,w.roi_label.text());self.assertIs(frame,w.frame)
        self.assertIsNotNone(w.legend.bounds)
    def test_gap_and_segment_events_clear_current_readings(self):
        self.opened();w=self.window
        w._request('seek',0);self.assertIsNone(w.frame)
        self.wait(lambda:not w._in_flight)
        self.assertIsNone(w.image.image);self.assertIsNone(w.legend.bounds)
        self.assertIn('unavailable',w.readings.text())
        self.assertFalse(w.save_button.isEnabled())
    def test_recovery_is_opt_in_and_visibly_unknown(self):
        w=self.window;w.recover.setChecked(True)
        w.open_path(ROOT/'missing-end.lmtr');self.wait(lambda:w.state is not None)
        self.assertIn('unknown tail',w.status.text());self.assertIn('full_verified_prefix',w.validation_status.text())
    def test_failed_open_clears_previous_frame_without_camera_import(self):
        self.opened();w=self.window
        w.open_path(ROOT/'unknown-required-feature.lmtr')
        self.wait(lambda:'Read failed' in w.status.text())
        self.assertIsNone(w.frame);self.assertIsNone(w.image.image)
        self.assertIsNone(w.legend.bounds);self.assertFalse(w.save_button.isEnabled())
        self.assertNotIn('mvp_camera_worker',sys.modules)
    def test_pause_and_image_tools_work_while_the_worker_is_projecting(self):
        self.opened()
        w = self.window
        w.temporal_button.click()
        entered, release = Event(), Event()
        project = LmtrPlaybackModel.project_time_ns

        def blocked(model, ns):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('Playback probe was not released')
            project(model, ns)

        def pixel(x, y):
            mapped = native_to_widget(x, y, w.image.width(), w.image.height(), **w.image._mapping())
            return QPoint(*(round(value) for value in mapped))

        with patch.object(LmtrPlaybackModel, 'project_time_ns', blocked):
            try:
                QTest.keyClick(w, Qt.Key.Key_Space)
                self.wait(entered.is_set)
                self.assertEqual('Pause', w.play.text())
                self.assertTrue(w.play.isEnabled())
                first_token = w.token
                w._tick()
                self.assertEqual(first_token, w.token)
                w.palette.setCurrentText('Turbo')
                w.auto.click()
                self.assertTrue(w.minimum.isEnabled())
                w.image_tool.setCurrentText('Point')
                QTest.mouseClick(w.image, Qt.MouseButton.LeftButton, pos=pixel(1, 0))
                self.assertEqual((1, 0), w.image.pinned)
                self.assertIn('30', w.point.text())
                self.assertEqual('Turbo', w.image.palette)
                self.assertIsNone(w.image.roi)
                self.assertTrue(w.plot_point.isEnabled())
                w.plot_point.click()
                w.image_tool.setCurrentText('ROI')
                QTest.mousePress(w.image, Qt.MouseButton.LeftButton, pos=pixel(0, 0))
                QTest.mouseMove(w.image, pixel(1, 0))
                QTest.mouseRelease(w.image, Qt.MouseButton.LeftButton, pos=pixel(1, 0))
                self.assertIn('mean 25', w.roi_label.text())
                self.assertTrue(w.plot_roi.isEnabled())
                QTest.keyClick(w, Qt.Key.Key_Space)
                self.assertEqual('Play', w.play.text())
                self.assertFalse(w._in_flight)
                self.assertEqual(1, w.state['index'])
            finally:
                release.set()
        # The pending tick projected the final event. A subsequent fine step
        # must still start from the paused frame, and expose the blank segment.
        QTest.keyClick(w, Qt.Key.Key_Left)
        self.wait(lambda: not w._in_flight)
        self.assertEqual(0, w.state['index'])
        self.assertIsNone(w.image.image)
        self.assertIn('unavailable', w.point.text())
        QTest.keyClick(w, Qt.Key.Key_Right)
        self.wait(lambda: not w._in_flight)
        self.assertEqual((1, 0), w.image.pinned)
        self.assertIn('mean 25', w.roi_label.text())
        self.assertEqual((int(w.state['timing']['timeline_ns']), 1), w.temporal_panel._cursor)
        editor = w.seconds.lineEdit()
        editor.setFocus()
        QTest.keyClick(editor, Qt.Key.Key_Left)
        self.assertFalse(w._in_flight)
        self.assertEqual(1, w.state['index'])

    def test_drag_previews_latest_timestamp_and_stops_on_a_gap(self):
        w = self.window
        with TemporaryDirectory() as path:
            source = Path(path) / 'gap.lmtr'
            write_plan(gap_plan(), source)
            w.open_path(source)
            self.wait(lambda: w.frame is not None)
            entered, release = Event(), Event()
            seek = LmtrPlaybackModel.seek_time_ns
            last_ns = w.state['last_ns']

            def blocked(model, ns):
                if ns == last_ns:
                    entered.set()
                    if not release.wait(5):
                        raise TimeoutError('Scrub probe was not released')
                return seek(model, ns)

            slider = w.slider
            y = slider.height() // 2
            with patch.object(LmtrPlaybackModel, 'seek_time_ns', blocked):
                try:
                    QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=QPoint(slider.width() - 1, y))
                    self.wait(entered.is_set)
                    QTest.mouseMove(slider, QPoint(slider.width() // 2, y))
                    self.assertTrue(slider.isSliderDown())
                    self.assertTrue(w._in_flight)
                finally:
                    release.set()
                self.wait(lambda: not w._in_flight)
                # A preview arrived while still holding the slider.
                self.assertTrue(slider.isSliderDown())
                self.assertEqual(2, w.state['index'])
                self.assertIn('Gap', w.image.empty_text)
                self.assertIsNone(w.image.image)
                self.assertIn('Recorded gaps 1', w.time_label.text())
                QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=QPoint(slider.width() // 2, y))
                self.assertEqual('Play', w.play.text())
                self.assertEqual((2_000_000, 2), w.temporal_panel._cursor)
            w.close()
            self.wait(lambda: not w.worker.isRunning())

    def test_laptop_width_keeps_navigation_and_palette_controls_inside_window(self):
        self.opened();self.window.details_button.setChecked(True)
        self.window.resize(1000,740);self.app.processEvents()
        self.assertLessEqual(self.window.width(),1000)
        for control in [self.window.open_button,self.window.metadata_button,self.window.verify_button,
                        self.window.save_button,self.window.seek_button,self.window.palette,self.window.clear]:
            rect=control.geometry();point=control.mapTo(self.window,control.rect().topLeft())
            self.assertGreaterEqual(point.x(),0)
            self.assertLessEqual(point.x()+rect.width(),self.window.width())

    def test_auto_bounds_are_visibly_read_only_and_diagnostics_are_explicit(self):
        self.opened();w=self.window
        self.assertFalse(w.minimum.isEnabled());self.assertFalse(w.maximum.isEnabled())
        self.assertFalse(w.details_panel.isVisible());w.details_button.click()
        self.assertTrue(w.details_panel.isVisible());self.assertTrue(w.verify_button.isVisible())
        self.assertIn('opened_records',w.validation_status.text())
        self.assertIn('Indexed:',w.status.text());self.assertIn('gap events',w.status.text())
        w.auto.setChecked(False);self.assertTrue(w.minimum.isEnabled());self.assertTrue(w.maximum.isEnabled())

    def test_roi_changes_keep_viewport_and_readings_inside_a_thousand_pixel_window(self):
        self.opened();w=self.window;w.resize(1000,740);self.app.processEvents()
        before=(w.width(),w.image.geometry())
        w.image.set_roi(NativeROI(0,0,3,2));self.app.processEvents()
        self.assertEqual(before,(w.width(),w.image.geometry()))
        self.assertIn('[0,3)',w.roi_label.text());self.assertIn('5/6 valid pixels',w.roi_label.text())
        w.image.clear_roi();self.app.processEvents()
        self.assertEqual(before,(w.width(),w.image.geometry()))
