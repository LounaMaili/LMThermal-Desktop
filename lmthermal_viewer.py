#!/usr/bin/env python3
"""Minimal PyQt HT-301 viewer backed by the validated radiometric session."""

import sys
import time

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QImage, QPainter, QPen
from PyQt6.QtWidgets import (QApplication, QFormLayout, QGroupBox, QHBoxLayout,
                             QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget)

from mvp_camera_worker import CameraWorker
from mvp_presentation import (current_extrema, current_reading, display_image,
                              image_viewport, native_to_widget, widget_to_native)
from radiometric_session import SessionState


class ThermalImageWidget(QWidget):
    """Paint image and markers in widget space without transforming camera data."""

    hovered = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(384, 288)
        self.setMouseTracking(True)
        self.observation = None
        self.image = None
        self.pointer = None
        self._mouse_widget = None

    def set_observation(self, observation) -> None:
        """Replace the image; a rejected frame never inherits old measurements."""
        self.observation = observation
        if observation is None:
            self.image = None
        else:
            gray = display_image(observation)
            self.image = QImage(gray.data, gray.shape[1], gray.shape[0], gray.strides[0],
                                QImage.Format.Format_Grayscale8).copy()
        self.update()

    def _move(self, position) -> None:
        self._mouse_widget = position
        self.pointer = widget_to_native(position.x(), position.y(), self.width(), self.height())
        self.hovered.emit(self.pointer)
        self.update()

    def mouseMoveEvent(self, event) -> None:
        self._move(event.position())

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._move(event.position())

    def leaveEvent(self, event) -> None:
        self._mouse_widget = None
        self.pointer = None
        self.hovered.emit(None)
        self.update()

    def resizeEvent(self, event) -> None:
        if self._mouse_widget is not None:
            # Widget geometry changed; the old local mouse position is stale.
            self._move(QPointF(self.mapFromGlobal(QCursor.pos())))
        super().resizeEvent(event)

    def _marker(self, painter, point, color, radius=8) -> None:
        x, y = native_to_widget(*point, self.width(), self.height())
        painter.setPen(QPen(color, 2))
        painter.drawLine(QPointF(x - radius, y), QPointF(x + radius, y))
        painter.drawLine(QPointF(x, y - radius), QPointF(x, y + radius))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(14, 16, 20))
        if self.image is None:
            painter.setPen(QColor("white"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Camera disconnected")
            return
        area = image_viewport(self.width(), self.height())
        painter.drawImage(QRectF(area.left, area.top, area.width, area.height), self.image)
        self._marker(painter, (192, 144), QColor(255, 220, 0), 10)
        extrema = current_extrema(self.observation)
        if extrema is not None:
            self._marker(painter, extrema[0][0], QColor(255, 70, 70))
            self._marker(painter, extrema[1][0], QColor(70, 175, 255))
        reading = current_reading(self.observation, self.pointer)
        if reading is not None:
            self._marker(painter, (reading.x, reading.y), QColor("white"), 4)
            text = f"({reading.x},{reading.y}) raw {reading.raw14}; {reading.native_equivalent_c:.2f} °C"
            x, y = native_to_widget(reading.x, reading.y, self.width(), self.height())
            box_width = painter.fontMetrics().horizontalAdvance(text) + 12
            left = min(max(area.left, x + 12), area.left + area.width - box_width)
            top = min(max(area.top, y + 12), area.top + area.height - 24)
            painter.fillRect(QRectF(left, top, box_width, 22), QColor(0, 0, 0, 210))
            painter.setPen(QColor("white"))
            painter.drawText(QRectF(left + 6, top, box_width - 8, 22),
                             Qt.AlignmentFlag.AlignVCenter, text)


class MainWindow(QMainWindow):
    """Show session observations and native-equivalent values, never Y thermometry."""

    def __init__(self, *, auto_connect=True):
        super().__init__()
        self.setWindowTitle("LMThermal — HT-301 Radiometric MVP")
        self.resize(1100, 760)
        self.worker = None
        self.observation = None
        self.pointer = None
        self.init_requested = False
        self.session_initialized = False
        self._had_error = False
        self._view_count = 0
        self._fps_started = time.monotonic()
        self._setup_ui()
        self._fps_timer = QTimer(self)
        self._fps_timer.timeout.connect(self._update_fps)
        self._fps_timer.start(1000)
        if auto_connect:
            QTimer.singleShot(0, self.connect_camera)

    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        self.image_widget = ThermalImageWidget()
        self.image_widget.hovered.connect(self._on_hover)
        layout.addWidget(self.image_widget, 1)
        panel = QWidget()
        panel.setFixedWidth(280)
        side = QVBoxLayout(panel)
        self.connect_button = QPushButton("Connect camera")
        self.connect_button.clicked.connect(self._toggle_camera)
        side.addWidget(self.connect_button)
        self.initialize_button = QPushButton("Initialize radiometric")
        self.initialize_button.setEnabled(False)
        self.initialize_button.clicked.connect(self._initialize)
        side.addWidget(self.initialize_button)

        session_form = QFormLayout()
        self.device_label = QLabel("Disconnected")
        self.state_label = QLabel("Disconnected")
        self.state_label.setWordWrap(True)
        self.mode_label = QLabel("—")
        self.fps_label = QLabel("—")
        for title, label in (("Device", self.device_label), ("State", self.state_label),
                             ("Frame", self.mode_label), ("View FPS", self.fps_label)):
            session_form.addRow(title + ":", label)
        session_group = QGroupBox("Camera and session")
        session_group.setLayout(session_form)
        side.addWidget(session_group)

        measurement_form = QFormLayout()
        self.cursor_label = QLabel("Unavailable")
        self.high_label = QLabel("Unavailable")
        self.low_label = QLabel("Unavailable")
        self.center_pixel_label = QLabel("Unavailable")
        self.trailer_center_label = QLabel("Unavailable")
        for title, label in (("Cursor", self.cursor_label), ("High", self.high_label),
                             ("Low", self.low_label), ("Center pixel", self.center_pixel_label),
                             ("Camera center reading", self.trailer_center_label)):
            label.setWordWrap(True)
            measurement_form.addRow(title + ":", label)
        measurement_group = QGroupBox("Native-equivalent readings")
        measurement_group.setLayout(measurement_form)
        side.addWidget(measurement_group)
        side.addStretch()
        warning = QLabel("Native-equivalent temperatures; absolute physical accuracy not independently validated.")
        warning.setWordWrap(True)
        side.addWidget(warning)
        layout.addWidget(panel)

    def _toggle_camera(self) -> None:
        if self.worker is None:
            self.connect_camera()
        else:
            self.stop_camera()

    def connect_camera(self) -> None:
        """Start read-only display acquisition before any explicit control write."""
        if self.worker is not None:
            return
        self._had_error = False
        self.init_requested = False
        self.session_initialized = False
        self.state_label.setText("Connecting…")
        self.device_label.setText("Searching for Infiray HT-301")
        worker = CameraWorker(self)
        worker.frame_available.connect(self._on_frame_available)
        worker.connected.connect(self.device_label.setText)
        worker.notice.connect(self.state_label.setText)
        worker.failed.connect(self._on_failure)
        worker.finished.connect(self._on_worker_finished)
        self.worker = worker
        self.connect_button.setText("Disconnect")
        worker.start()

    def stop_camera(self) -> None:
        """Finish acquisition and close both handles before reconnecting."""
        worker = self.worker
        if worker is None:
            return
        self.initialize_button.setEnabled(False)
        self.state_label.setText("Stopping…")
        worker.request_stop()
        if not worker.wait(10000):
            self.state_label.setText("Still stopping camera; wait before reconnecting")
            return
        self.worker = None
        self.observation = None
        self.image_widget.set_observation(None)
        self._clear_measurements()
        self.device_label.setText("Disconnected")
        self.state_label.setText("Disconnected")
        self.mode_label.setText("—")
        self.connect_button.setText("Connect camera")
        worker.deleteLater()

    def _on_worker_finished(self) -> None:
        worker = self.sender()
        if worker is not self.worker:
            return
        self.worker = None
        self.observation = None
        self.image_widget.set_observation(None)
        self._clear_measurements()
        self.initialize_button.setEnabled(False)
        self.connect_button.setText("Connect camera")
        if not self._had_error:
            self.device_label.setText("Disconnected")
            self.state_label.setText("Disconnected")
        worker.deleteLater()

    def _initialize(self) -> None:
        if self.worker is None or self.init_requested:
            return
        self.init_requested = True
        self.initialize_button.setEnabled(False)
        self.state_label.setText("Initializing radiometric mode…")
        self.worker.request_initialize()

    def _on_frame_available(self) -> None:
        """Consume the newest observation; the Qt event queue stays bounded."""
        if self.worker is None:
            return
        observation = self.worker.take_latest()
        if observation is None:
            return
        self.observation = observation
        self.image_widget.set_observation(observation)
        self._view_count += 1
        state = observation.state
        if state == SessionState.RADIOMETRIC_READY:
            self.init_requested = False
            self.session_initialized = True
            label = "Radiometric ready"
        elif state == SessionState.DISPLAY_STREAM:
            label = "Initializing radiometric mode…" if self.init_requested else "Display preview"
        elif state == SessionState.SWITCHING_TO_RAW14:
            label = "Switching to raw14"
        elif state == SessionState.SHUTTER_TRANSIENT:
            label = "Shutter/calibration transient"
        elif state == SessionState.RAW14_UNSETTLED:
            label = ("Temporarily invalid/unsettled; awaiting live recovery"
                     if self.init_requested or self.session_initialized else
                     "Raw14 observed; host range unknown. Reconnect in display mode.")
        else:
            label = state.value.replace("_", " ").title()
        if observation.rejection:
            label += f" — {observation.rejection}"
        self.state_label.setText(label)
        self.mode_label.setText(f"{observation.inspection.mode}, words "
                                f"{observation.inspection.word_min}–{observation.inspection.word_max}")
        self.initialize_button.setEnabled(state == SessionState.DISPLAY_STREAM and
                                          observation.inspection.mode == "display" and
                                          not self.init_requested)
        measurement = observation.measurement
        if measurement is None:
            self._clear_measurements()
        else:
            self.high_label.setText(f"{measurement.high_c:.2f} °C at {measurement.high_xy}")
            self.low_label.setText(f"{measurement.low_c:.2f} °C at {measurement.low_xy}")
            self.center_pixel_label.setText(f"{measurement.literal_center_c:.2f} °C "
                                            f"(raw {measurement.literal_center_index}, 192,144)")
            self.trailer_center_label.setText(f"{measurement.trailer_center_c:.2f} °C "
                                             f"(raw {measurement.trailer_center_index})")
            self._update_cursor()

    def _clear_measurements(self) -> None:
        for label in (self.cursor_label, self.high_label, self.low_label,
                      self.center_pixel_label, self.trailer_center_label):
            label.setText("Unavailable")

    def _on_hover(self, point) -> None:
        self.pointer = point
        self._update_cursor()

    def _update_cursor(self) -> None:
        reading = current_reading(self.observation, self.pointer)
        self.cursor_label.setText("Unavailable" if reading is None else
                                  f"({reading.x},{reading.y}) raw {reading.raw14}; "
                                  f"{reading.native_equivalent_c:.2f} °C")

    def _update_fps(self) -> None:
        now = time.monotonic()
        elapsed = now - self._fps_started
        self.fps_label.setText(f"{self._view_count / elapsed:.1f}" if elapsed > 0 else "—")
        self._view_count = 0
        self._fps_started = now

    def _on_failure(self, message: str) -> None:
        self._had_error = True
        self.init_requested = False
        self.state_label.setText(f"Error: {message}")
        self.initialize_button.setEnabled(False)
        self.observation = None
        self.image_widget.set_observation(None)
        self._clear_measurements()

    def closeEvent(self, event) -> None:
        self.stop_camera()
        if self.worker is not None:
            event.ignore()
        else:
            event.accept()


def main() -> None:
    """Launch a display-only preview before the user requests initialization."""
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
