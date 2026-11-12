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
        self.window.close();self.app.processEvents()
        self.assertFalse(self.window.worker.isRunning())
    def wait(self, condition):
        deadline=time.monotonic()+10
        while not condition() and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.003)
        self.assertTrue(condition(),self.window.status.text())
    def opened(self):
        self.window.open_path(ROOT/'illustration-01-stored.lmtr')
        self.wait(lambda:self.window.state is not None)
        self.window.slider.setValue(1)
        self.wait(lambda:self.window.frame is not None)
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
        self.assertIn('unknown tail',w.status.text());self.assertIn('full_verified_prefix',w.status.text())
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
