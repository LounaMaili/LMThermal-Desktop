"""Offscreen Qt state checks without camera hardware or pixel-perfect captures."""

import json
import os
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QImage, QMouseEvent

from lmthermal_viewer import MainWindow
from mvp_presentation import native_to_widget
from mvp_camera_worker import CameraWorker, WorkerStopped
from radiometric_export import export_capture, snapshot_capture
from celsius_palette import CelsiusRange
from radiometric_session import FrameObservation, SessionState, inspect_frame, make_measurement
from roi_measurement import NativeROI, current_roi_statistics


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
        self.assertFalse(self.window.capture_button.isEnabled())
        self.window._on_hover((192, 144))
        self.assertEqual(self.window.cursor_label.text(), "Unavailable")
        self.assertEqual(self.window.high_label.text(), "Unavailable")

    def test_ready_values_use_matrix_and_distinct_centers_then_clear(self):
        self.send(HAND, SessionState.RADIOMETRIC_READY, self.measured)
        self.assertEqual(self.window.image_widget.image.format(), QImage.Format.Format_RGB888)
        self.assertTrue(self.window.capture_button.isEnabled())
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
        self.assertFalse(self.window.capture_button.isEnabled())
        for label in (self.window.cursor_label, self.window.high_label,
                      self.window.low_label, self.window.center_pixel_label,
                      self.window.trailer_center_label):
            self.assertEqual(label.text(), "Unavailable")
        self.send(HAND, SessionState.RADIOMETRIC_READY, self.measured)
        self.assertIn(f"{self.measured.high_c:.2f}", self.window.high_label.text())
        self.assertTrue(self.window.capture_button.isEnabled())

    def test_save_action_snapshots_current_ready_view(self):
        self.send(HAND, SessionState.RADIOMETRIC_READY, self.measured)
        self.window.image_widget.set_roi(NativeROI(10, 20, 31, 41))
        with TemporaryDirectory() as root:
            name = str(Path(root) / "ui-capture.png")

            def change_view_during_dialog(*args):
                self.window.palette_combo.setCurrentText("White hot")
                self.window.auto_range_check.setChecked(False)
                self.window.min_spin.setValue(25.0)
                self.window.max_spin.setValue(45.0)
                self.window.image_widget.set_roi(NativeROI(100, 100, 110, 110))
                return name, ""

            with patch("lmthermal_viewer.QFileDialog.getSaveFileName",
                       side_effect=change_view_during_dialog):
                self.window.capture_button.click()
            self.assertTrue((Path(root) / "ui-capture.json").exists())
            metadata = json.loads((Path(root) / "ui-capture.json").read_text())
            self.assertEqual(metadata["presentation"]["palette"], "Inferno")
            self.assertEqual(metadata["presentation"]["range_mode"], "auto")
            self.assertEqual(metadata["roi"]["geometry"], {
                "x1_px": 10, "y1_px": 20, "x2_px": 31, "y2_px": 41})
            self.assertEqual(self.window.palette_combo.currentText(), "White hot")
            self.assertIn("ui-capture.json", self.window.statusBar().currentMessage())

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

    def drag(self, start, end):
        widget = self.window.image_widget
        for event_type, position, button, buttons in (
            (QEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton),
            (QEvent.Type.MouseMove, end, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton),
            (QEvent.Type.MouseButtonRelease, end, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton),
        ):
            self.app.sendEvent(widget, QMouseEvent(event_type, QPointF(*position),
                                                 button, buttons, Qt.KeyboardModifier.NoModifier))

    def test_widget_drag_reverse_clip_margins_and_clear(self):
        self.send(HAND, SessionState.RADIOMETRIC_READY, self.measured)
        widget = self.window.image_widget
        widget.resize(800, 800)
        first = native_to_widget(10, 20, 800, 800)
        last = native_to_widget(30, 40, 800, 800)
        self.drag(first, last)
        self.assertEqual(widget.roi, NativeROI(10, 20, 31, 41))
        self.drag(last, first)
        self.assertEqual(widget.roi, NativeROI(10, 20, 31, 41))
        self.drag(first, (900, 900))
        self.assertEqual(widget.roi, NativeROI(10, 20, 384, 288))
        self.window.clear_roi_button.click()
        self.assertIsNone(widget.roi)
        self.drag((20, 10), (30, 20))
        self.assertIsNone(widget.roi)
        self.drag(first, first)
        self.assertEqual(widget.roi, NativeROI(10, 20, 11, 21))

    def test_roi_numbers_ignore_presentation_and_resume_after_invalid_frame(self):
        self.send(HAND, SessionState.RADIOMETRIC_READY, self.measured)
        widget = self.window.image_widget
        roi = NativeROI(160, 180, 220, 240)
        widget.set_roi(roi)
        values = self.window.roi_values_label.text()
        original_stats = current_roi_statistics(self.window.observation, roi)
        self.assertIn("Pixels: 3600", values)
        self.window.palette_combo.setCurrentText("Turbo")
        self.window.auto_range_check.setChecked(False)
        self.window.min_spin.setValue(25)
        self.window.max_spin.setValue(45)
        widget.resize(700, 500)
        self.assertEqual(widget.roi, roi)
        self.assertEqual(self.window.roi_values_label.text(), values)
        self.assertEqual(current_roi_statistics(self.window.observation, roi), original_stats)
        for state in (SessionState.SHUTTER_TRANSIENT, SessionState.RAW14_UNSETTLED,
                      SessionState.DISPLAY_STREAM):
            self.send(HAND, state, rejection="unavailable")
            self.assertEqual(self.window.roi_values_label.text(), "Unavailable")
            self.assertEqual(widget.roi, roi)
        self.send(HAND, SessionState.RADIOMETRIC_READY, self.measured)
        self.assertEqual(self.window.roi_values_label.text(), values)
        warmer = replace(self.measured, temperature_c=self.measured.temperature_c + 1)
        self.send(HAND, SessionState.RADIOMETRIC_READY, warmer)
        self.assertNotEqual(self.window.roi_values_label.text(), values)
        self.assertEqual(current_roi_statistics(self.window.observation, roi).mean_c,
                         float(warmer.temperature_c[180:240, 160:220].mean(dtype="float64")))

    def saved_capture(self, root, *, automatic=True):
        ready = FrameObservation(HAND, 0, SessionState.RADIOMETRIC_READY,
                                 inspect_frame(HAND), False, None, self.measured)
        export_capture(snapshot_capture(ready, "White hot", CelsiusRange(25, 45), automatic,
                                       roi=NativeROI(160, 180, 220, 240)), Path(root) / "saved")
        return Path(root) / "saved.json"

    def test_offline_opens_without_camera_restores_range_roi_and_reads_saved_matrix(self):
        with TemporaryDirectory() as root:
            path = self.saved_capture(root)
            with patch("lmthermal_viewer.CameraWorker", side_effect=AssertionError("camera started")):
                self.assertTrue(self.window.open_capture(path))
                self.app.processEvents()
            capture = self.window.offline_capture
            self.assertIs(self.window.observation, capture)
            self.assertIn("Saved capture", self.window.state_label.text())
            self.assertEqual(self.window.device_label.text(), "Offline — no camera")
            self.assertFalse(self.window.initialize_button.isEnabled())
            self.assertFalse(self.window.capture_button.isEnabled())
            self.assertTrue(self.window.render_button.isEnabled())
            self.assertTrue(self.window.metadata_button.isEnabled())
            self.assertEqual(self.window.image_widget.effective_bounds, CelsiusRange(25, 45))
            self.assertTrue(self.window.auto_range_check.isChecked())
            self.assertEqual(self.window.palette_combo.currentText(), "White hot")
            self.assertEqual(self.window.image_widget.roi, capture.roi)
            self.window._on_hover((192, 144))
            self.assertIn(str(capture.literal_center_index), self.window.cursor_label.text())
            self.assertIn(str(capture.trailer_center_index), self.window.trailer_center_label.text())
            original = self.window.roi_values_label.text()
            self.window.palette_combo.setCurrentText("Turbo")
            self.window.auto_range_check.setChecked(False)
            self.window.min_spin.setValue(20)
            self.window.image_widget.resize(800, 600)
            self.assertEqual(self.window.roi_values_label.text(), original)
            self.window.clear_roi_button.click()
            self.assertEqual(self.window.roi_values_label.text(), "Unavailable")
            self.window.image_widget.set_roi(NativeROI(10, 20, 30, 40))
            self.assertIn("Pixels: 400", self.window.roi_values_label.text())
            target = Path(root) / "rendered.png"
            with patch("lmthermal_viewer.QFileDialog.getSaveFileName", return_value=(str(target), "")):
                self.window.render_button.click()
            self.assertTrue(target.exists())
            self.assertFalse(target.with_suffix(".json").exists())
            self.window._update_fps()
            self.assertEqual(self.window.fps_label.text(), "—")
            self.window.close_capture_button.click()
            self.assertIsNone(self.window.observation)
            self.assertIsNone(self.window.image_widget.roi)
            self.assertIsNone(self.window.image_widget.image)
            self.assertEqual(self.window.cursor_label.text(), "Unavailable")
            self.assertEqual(self.window.state_label.text(), "Disconnected")
            self.assertIsNone(self.window.worker)

    def test_open_capture_cancels_pending_startup_camera_connection(self):
        with TemporaryDirectory() as root:
            path = self.saved_capture(root)
            window = MainWindow(auto_connect=True)
            try:
                with patch("lmthermal_viewer.CameraWorker", side_effect=AssertionError("camera started")):
                    self.assertTrue(window.open_capture(path))
                    window.close_capture()
                    self.app.processEvents()
                self.assertIsNone(window.worker)
            finally:
                window.close()

    def test_camera_stopped_before_load_and_queued_old_signals_cannot_overwrite_offline(self):
        with TemporaryDirectory() as root:
            path = self.saved_capture(root, automatic=False)
            with patch.object(CameraWorker, "start"):
                self.window.connect_camera()
            worker = self.window.worker
            # Emulate signals already queued at the instant the operator opens a capture.
            worker.connected.disconnect()
            worker.notice.disconnect()
            worker.failed.disconnect()
            worker.frame_available.disconnect()
            worker.connected.connect(self.window._on_connected, Qt.ConnectionType.QueuedConnection)
            worker.notice.connect(self.window._on_notice, Qt.ConnectionType.QueuedConnection)
            worker.failed.connect(self.window._on_failure, Qt.ConnectionType.QueuedConnection)
            worker.frame_available.connect(self.window._on_frame_available, Qt.ConnectionType.QueuedConnection)
            worker.connected.emit("stale device")
            worker.notice.emit("stale notice")
            worker.failed.emit("stale failure")
            worker.frame_available.emit()
            with patch.object(worker, "request_stop", wraps=worker.request_stop) as stopped:
                self.assertTrue(self.window.open_capture(path))
                stopped.assert_called_once()
            before = (self.window.state_label.text(), self.window.device_label.text(),
                      self.window.observation)
            self.app.processEvents()
            self.assertEqual((self.window.state_label.text(), self.window.device_label.text(),
                              self.window.observation), before)
            self.assertIsNone(self.window.worker)
            self.assertFalse(self.window.auto_range_check.isChecked())
            # Explicit Connect closes saved data before starting the next worker.
            with patch.object(CameraWorker, "start") as start:
                self.window.connect_button.click()
                start.assert_called_once()
            self.assertIsNone(self.window.offline_capture)
            self.assertIsNone(self.window.observation)
            self.assertIsNone(self.window.image_widget.roi)
            self.assertFalse(self.window.render_button.isEnabled())
            self.window.stop_camera()

    def test_capture_rejection_and_stop_timeout_do_not_load_or_connect(self):
        with TemporaryDirectory() as root:
            path = self.saved_capture(root)
            with patch.object(CameraWorker, "start"):
                self.window.connect_camera()
            worker = self.window.worker
            with patch.object(worker, "wait", return_value=False):
                self.assertFalse(self.window.open_capture(path))
            self.assertIs(self.window.worker, worker)
            self.assertIsNone(self.window.offline_capture)
            self.window.stop_camera()
            path.with_suffix(".npz").write_bytes(b"corrupt")
            with patch("lmthermal_viewer.QMessageBox.warning") as warning:
                self.assertFalse(self.window.open_capture(path))
                warning.assert_called_once()
            self.assertIsNone(self.window.worker)
            self.assertIsNone(self.window.observation)
            self.assertFalse(self.window.capture_button.isEnabled())
            self.assertFalse(self.window.render_button.isEnabled())

    def start_test_log(self, root):
        with patch.object(CameraWorker, "start"):
            self.window.connect_camera()
        ready = FrameObservation(HAND, 0, SessionState.RADIOMETRIC_READY,
                                 inspect_frame(HAND), False, None, self.measured)
        self.window.worker._publish(ready)
        with patch("lmthermal_viewer.QFileDialog.getSaveFileName",
                   return_value=(str(Path(root)/"measurements.csv"), "")):
            self.window.start_log_button.click()
        return self.window.logger

    def test_logging_controls_sampling_roi_and_presentation(self):
        with TemporaryDirectory() as root:
            logger = self.start_test_log(root)
            self.assertIsNotNone(logger)
            self.assertFalse(self.window.start_log_button.isEnabled())
            self.assertTrue(self.window.stop_log_button.isEnabled())
            self.assertFalse(self.window.log_rate_combo.isEnabled())
            self.window.image_widget.set_roi(NativeROI(160, 180, 220, 240))
            first = logger.sampler.sample_due(logger.started+1, "2026-09-30T12:00:00+00:00")
            self.assertEqual(first["roi_pixel_count"], 3600)
            self.window.palette_combo.setCurrentText("Turbo")
            self.window.auto_range_check.setChecked(False)
            self.window.min_spin.setValue(25)
            self.window.max_spin.setValue(45)
            self.window.resize(1200, 800)
            self.window.worker._publish(replace(self.window.observation))
            second = logger.sampler.sample_due(logger.started+2, "2026-09-30T12:00:01+00:00")
            for key in ("high_c", "low_c", "literal_center_c", "trailer_center_c", "roi_mean_c"):
                self.assertEqual(first[key], second[key])
            self.window.image_widget.clear_roi()
            self.window.worker._publish(replace(self.window.observation))
            third = logger.sampler.sample_due(logger.started+3, "2026-09-30T12:00:02+00:00")
            self.assertIsNone(third["roi_mean_c"])
            self.window.stop_log_button.click()
            self.assertIsNone(self.window.logger)
            self.assertIn("Complete", self.window.log_status_label.text())
            self.assertTrue(self.window.start_log_button.isEnabled())
            self.window.stop_camera()
            self.assertFalse(self.window.start_log_button.isEnabled())

    def test_disconnect_close_and_open_offline_finalize_logger(self):
        for action, reason in (("disconnect", "camera_disconnect"),
                               ("close", "window_closed"), ("offline", "camera_disconnect")):
            with self.subTest(action=action), TemporaryDirectory() as root:
                logger = self.start_test_log(root)
                if action == "disconnect":
                    self.window.stop_camera()
                elif action == "close":
                    self.window.close()
                else:
                    self.assertTrue(self.window.open_capture(self.saved_capture(root)))
                    with patch("lmthermal_viewer.QFileDialog.getSaveFileName") as dialog:
                        self.window._start_logging()
                        dialog.assert_not_called()
                    self.assertFalse(self.window.start_log_button.isEnabled())
                self.assertIsNone(self.window.logger)
                self.assertFalse(logger._thread.is_alive())
                metadata = json.loads((Path(root)/"measurements.json").read_text())
                self.assertEqual(metadata["completion"], "complete")
                self.assertEqual(metadata["stop_reason"], reason)
                self.window.close_capture()

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
