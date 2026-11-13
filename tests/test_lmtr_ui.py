"""Camera-free offscreen smoke/regressions; no live camera worker is imported."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication

from lmtr_viewer import LmtrWindow
from roi_measurement import NativeROI

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
    def test_timer_never_supersedes_an_inflight_load(self):
        w=self.window;w._in_flight=True
        with patch.object(w.worker,'request') as request:
            for _ in range(50):w._tick()
            request.assert_not_called()

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
