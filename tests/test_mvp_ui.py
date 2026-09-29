"""Offscreen Qt state checks without camera hardware or pixel-perfect captures."""

import os
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage

from lmthermal_viewer import MainWindow
from mvp_presentation import native_to_widget
from mvp_camera_worker import CameraWorker, WorkerStopped
from radiometric_session import FrameObservation, SessionState, inspect_frame, make_measurement


FIXTURES = Path(__file__).parent / "fixtures"
DISPLAY = (FIXTURES / "display-room-baseline.raw").read_bytes()
HAND = (FIXTURES / "warm-hand-settled.raw").read_bytes()


class OneFrameWorker:
    """Supply immutable observations through the window's real consumer slot."""

    def __init__(self, frame):
        self.frame = frame

    def take_latest(self):
        return self.frame


class MVPWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.measured = make_measurement(HAND, 0.0)

    def setUp(self):
        self.window = MainWindow(auto_connect=False)

    def tearDown(self):
        self.window.worker = None
        self.window.close()

    def send(self, raw, state, measurement=None, rejection=None):
        inspection = inspect_frame(raw)
        frame = FrameObservation(raw, 0.0, state, inspection, False, rejection, measurement)
        self.window.worker = OneFrameWorker(frame)
        self.window._on_frame_available()

    def test_display_has_image_but_no_temperature(self):
        self.send(DISPLAY, SessionState.DISPLAY_STREAM)
        self.assertEqual(self.window.image_widget.image.height(), 288)
        self.assertEqual(self.window.image_widget.image.width(), 384)
        self.assertEqual(self.window.image_widget.image.format(), QImage.Format.Format_Grayscale8)
        self.assertIsNone(self.window.image_widget.effective_bounds)
        self.assertIsNone(self.window.legend.bounds)
        self.assertTrue(self.window.initialize_button.isEnabled())
        self.window._on_hover((192, 144))
        self.assertEqual(self.window.cursor_label.text(), "Unavailable")
        self.assertEqual(self.window.high_label.text(), "Unavailable")

    def test_ready_values_use_matrix_and_distinct_centers_then_clear(self):
        self.send(HAND, SessionState.RADIOMETRIC_READY, self.measured)
        self.assertEqual(self.window.image_widget.image.format(), QImage.Format.Format_RGB888)
        self.assertEqual(self.window.legend.bounds, self.window.image_widget.effective_bounds)
        self.window._on_hover((191, 211))
        self.assertIn(f"raw {int(self.measured.raw14[211, 191])}",
                      self.window.cursor_label.text())
        self.assertIn(f"{self.measured.high_c:.2f}", self.window.high_label.text())
        self.assertIn(str(self.measured.high_xy), self.window.high_label.text())
        self.assertIn(str(self.measured.low_xy), self.window.low_label.text())
        self.assertIn("5742", self.window.center_pixel_label.text())
        self.assertIn("5740", self.window.trailer_center_label.text())
        self.send(HAND, SessionState.RAW14_UNSETTLED, rejection="held_image")
        self.assertIn("Temporarily invalid", self.window.state_label.text())
        self.assertEqual(self.window.image_widget.image.format(), QImage.Format.Format_Grayscale8)
        self.assertIsNone(self.window.legend.bounds)
        for label in (self.window.cursor_label, self.window.high_label,
                      self.window.low_label, self.window.center_pixel_label,
                      self.window.trailer_center_label):
            self.assertEqual(label.text(), "Unavailable")
        self.send(HAND, SessionState.RADIOMETRIC_READY, self.measured)
        self.assertIn(f"{self.measured.high_c:.2f}", self.window.high_label.text())

    def test_palette_and_manual_lock_change_color_not_measurement_or_mapping(self):
        self.send(HAND, SessionState.RADIOMETRIC_READY, self.measured)
        self.window._on_hover((191, 211))
        native_marker = native_to_widget(191, 211, 800, 600)
        original_raw = self.measured.raw14.copy()
        original_readings = (self.window.cursor_label.text(), self.window.high_label.text(),
                             self.window.low_label.text(), self.window.center_pixel_label.text())
        original_rgb = self.window.image_widget.image.copy()
        self.assertLess(self.window.legend.bounds.upper, 100)
        self.window.palette_combo.setCurrentText("White hot")
        self.assertNotEqual(self.window.image_widget.image, original_rgb)
        self.window.auto_range_check.setChecked(False)
        self.window.min_spin.setValue(15.0)
        self.window.max_spin.setValue(45.0)
        self.assertEqual((self.window.legend.bounds.lower, self.window.legend.bounds.upper),
                         (15.0, 45.0))
        self.assertEqual(self.window.range_label.text(), "Locked: 15.00 to 45.00 °C")
        self.assertEqual((self.window.cursor_label.text(), self.window.high_label.text(),
                          self.window.low_label.text(), self.window.center_pixel_label.text()),
                         original_readings)
        self.assertTrue((self.measured.raw14 == original_raw).all())
        self.assertEqual(native_to_widget(191, 211, 800, 600), native_marker)
        self.window.min_spin.setValue(50.0)
        self.assertLess(self.window.min_spin.value(), self.window.max_spin.value())
        self.window.auto_range_check.setChecked(True)
        self.assertEqual(self.window.legend.bounds, self.window.image_widget.effective_bounds)

    def test_worker_coalesces_gui_notifications_and_stops_publication(self):
        worker = CameraWorker()
        emissions = []
        worker.frame_available.connect(lambda: emissions.append(1))
        display = FrameObservation(DISPLAY, 0.0, SessionState.DISPLAY_STREAM,
                                   inspect_frame(DISPLAY), False, None, None)
        worker._publish(display)
        worker._publish(display)
        self.assertEqual(len(emissions), 1)
        self.assertIs(worker.take_latest(), display)
        worker._publish(display)
        self.assertEqual(len(emissions), 2)
        worker.request_stop()
        with self.assertRaises(WorkerStopped):
            worker._publish(display)


if __name__ == "__main__":
    unittest.main()
