#!/usr/bin/env python3
"""Minimal PyQt HT-301 viewer backed by the validated radiometric session."""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, QSignalBlocker, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QImage, QPainter, QPen
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
                             QDialog, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                             QLabel, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
                             QScrollArea, QVBoxLayout, QWidget)

from celsius_palette import (DEFAULT_PALETTE, PALETTES, CelsiusRange,
                             effective_range, legend_image, render_temperature)
from mvp_camera_worker import CameraWorker
from mvp_presentation import (current_extrema, current_measurement, current_reading, display_image,
                              image_viewport, native_edge_to_widget,
                              native_to_widget, widget_to_native)
from radiometric_capture import CaptureError, load_capture, save_rendered_image
from radiometric_export import ACCURACY_WARNING, export_capture, snapshot_capture
from radiometric_session import SessionState
from roi_measurement import NativeROI, current_roi_statistics, roi_from_native_pixels


class ThermalImageWidget(QWidget):
    """Paint image and markers in widget space without transforming camera data."""

    hovered = pyqtSignal(object)
    roi_changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(384, 288)
        self.setMouseTracking(True)
        self.observation = None
        self.image = None
        self.pointer = None
        self._mouse_widget = None
        self.palette = DEFAULT_PALETTE
        self.automatic_range = True
        self.locked_lower = 20.0
        self.locked_upper = 40.0
        self.effective_bounds = None
        self._restored_bounds = None
        self.roi = None
        self._roi_anchor = None

    def set_roi(self, roi: NativeROI | None) -> None:
        """Keep selected geometry in native pixels across frames and resizes."""
        self.roi = roi
        self.roi_changed.emit(roi)
        self.update()

    def clear_roi(self) -> None:
        self._roi_anchor = None
        self.set_roi(None)

    def _drag_roi(self, position) -> None:
        if self._roi_anchor is not None:
            endpoint = widget_to_native(position.x(), position.y(), self.width(),
                                         self.height(), clip=True)
            if endpoint is not None:
                self.set_roi(roi_from_native_pixels(self._roi_anchor, endpoint))

    def set_presentation(self, palette: str, automatic: bool,
                         locked_lower: float, locked_upper: float) -> None:
        """Change display colors without altering the current observation."""
        if palette not in PALETTES:
            raise ValueError("Unsupported Celsius palette")
        CelsiusRange(locked_lower, locked_upper)
        self._restored_bounds = None
        self.palette = palette
        self.automatic_range = automatic
        self.locked_lower, self.locked_upper = locked_lower, locked_upper
        self._render()

    def set_observation(self, observation, *, restored_bounds=None) -> None:
        """Replace the image; a rejected frame never inherits old measurements."""
        self.observation = observation
        self._restored_bounds = restored_bounds
        self._render()

    def _render(self) -> None:
        """Use Celsius color only for a currently valid measurement frame."""
        observation = self.observation
        self.effective_bounds = None
        if observation is None:
            self.image = None
        elif current_measurement(observation) is not None:
            matrix = current_measurement(observation).temperature_c
            bounds = self._restored_bounds or effective_range(
                matrix, self.automatic_range, self.locked_lower, self.locked_upper)
            rgb = render_temperature(matrix, bounds.lower, bounds.upper, self.palette)
            self.image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0],
                                QImage.Format.Format_RGB888).copy()
            self.effective_bounds = bounds
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
        self._drag_roi(event.position())

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._move(event.position())
            if self.image is not None and self.pointer is not None:
                self._roi_anchor = self.pointer
                self.set_roi(roi_from_native_pixels(self.pointer, self.pointer))

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_roi(event.position())
            self._roi_anchor = None

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
        if self.roi is not None:
            left, top = native_edge_to_widget(self.roi.x1, self.roi.y1,
                                               self.width(), self.height())
            right, bottom = native_edge_to_widget(self.roi.x2, self.roi.y2,
                                                   self.width(), self.height())
            rectangle = QRectF(left, top, right - left, bottom - top)
            painter.setPen(QPen(QColor("black"), 4))
            painter.drawRect(rectangle)
            painter.setPen(QPen(QColor(80, 255, 150), 2))
            painter.drawRect(rectangle)
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


class CelsiusLegend(QWidget):
    """Show the exact Celsius bounds and palette used by the current image."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(94)
        self.bounds = None
        self.palette = DEFAULT_PALETTE
        self.gradient = None
        self.setVisible(False)

    def set_presentation(self, bounds: CelsiusRange | None, palette: str) -> None:
        self.bounds = bounds
        self.palette = palette
        if bounds is None:
            self.gradient = None
            self.setVisible(False)
        else:
            rgb = legend_image(bounds.lower, bounds.upper, palette)
            self.gradient = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0],
                                   QImage.Format.Format_RGB888).copy()
            self.setVisible(True)
        self.update()

    def paintEvent(self, event) -> None:
        if self.bounds is None or self.gradient is None:
            return
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(14, 16, 20))
        top, height = 24.0, max(2.0, self.height() - 48.0)
        painter.drawImage(QRectF(8, top, 22, height), self.gradient)
        painter.setPen(QColor("white"))
        for fraction in (0.0, .25, .5, .75, 1.0):
            y = top + height * fraction
            value = self.bounds.upper - (self.bounds.upper - self.bounds.lower) * fraction
            painter.drawLine(QPointF(31, y), QPointF(36, y))
            painter.drawText(QRectF(38, y - 10, 54, 20),
                             Qt.AlignmentFlag.AlignVCenter, f"{value:.1f}°C")


class MainWindow(QMainWindow):
    """Show session observations and native-equivalent values, never Y thermometry."""

    def __init__(self, *, auto_connect=True):
        super().__init__()
        self.setWindowTitle("LMThermal — HT-301 Radiometric MVP")
        self.resize(1100, 760)
        self.worker = None
        self.offline_capture = None
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
        self._auto_connect_timer = QTimer(self)
        self._auto_connect_timer.setSingleShot(True)
        self._auto_connect_timer.timeout.connect(self.connect_camera)
        if auto_connect:
            self._auto_connect_timer.start(0)

    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        self.image_widget = ThermalImageWidget()
        self.image_widget.hovered.connect(self._on_hover)
        self.image_widget.roi_changed.connect(self._on_roi_changed)
        layout.addWidget(self.image_widget, 1)
        self.legend = CelsiusLegend()
        layout.addWidget(self.legend)
        panel = QWidget()
        panel.setMinimumWidth(280)
        side = QVBoxLayout(panel)
        self.connect_button = QPushButton("Connect camera")
        self.connect_button.clicked.connect(self._toggle_camera)
        side.addWidget(self.connect_button)
        self.open_capture_button = QPushButton("Open radiometric capture")
        self.open_capture_button.clicked.connect(self._choose_capture)
        side.addWidget(self.open_capture_button)
        self.close_capture_button = QPushButton("Close saved capture")
        self.close_capture_button.setEnabled(False)
        self.close_capture_button.clicked.connect(self.close_capture)
        side.addWidget(self.close_capture_button)
        self.render_button = QPushButton("Save rendered image")
        self.render_button.setEnabled(False)
        self.render_button.clicked.connect(self._save_rendered)
        side.addWidget(self.render_button)
        self.metadata_button = QPushButton("Saved capture metadata")
        self.metadata_button.setEnabled(False)
        self.metadata_button.clicked.connect(self._show_metadata)
        side.addWidget(self.metadata_button)
        self.initialize_button = QPushButton("Initialize radiometric")
        self.initialize_button.setEnabled(False)
        self.initialize_button.clicked.connect(self._initialize)
        side.addWidget(self.initialize_button)

        palette_group = QGroupBox("Celsius display")
        palette_form = QFormLayout(palette_group)
        self.palette_combo = QComboBox()
        self.palette_combo.addItems(PALETTES)
        self.palette_combo.setCurrentText(DEFAULT_PALETTE)
        self.auto_range_check = QCheckBox("Auto range")
        self.auto_range_check.setChecked(True)
        self.min_spin = QDoubleSpinBox()
        self.min_spin.setRange(-273.15, 999.99)
        self.min_spin.setDecimals(2)
        self.min_spin.setSuffix(" °C")
        self.min_spin.setValue(20.0)
        self.max_spin = QDoubleSpinBox()
        self.max_spin.setRange(-273.14, 1000.0)
        self.max_spin.setDecimals(2)
        self.max_spin.setSuffix(" °C")
        self.max_spin.setValue(40.0)
        self.min_spin.setMaximum(self.max_spin.value() - .01)
        self.max_spin.setMinimum(self.min_spin.value() + .01)
        self.min_spin.setEnabled(False)
        self.max_spin.setEnabled(False)
        self.range_label = QLabel("Available when radiometric-ready")
        self.range_label.setWordWrap(True)
        palette_form.addRow("Palette:", self.palette_combo)
        palette_form.addRow(self.auto_range_check)
        palette_form.addRow("Min:", self.min_spin)
        palette_form.addRow("Max:", self.max_spin)
        palette_form.addRow("Visual scale:", self.range_label)
        side.addWidget(palette_group)
        self.capture_button = QPushButton("Save radiometric capture")
        self.capture_button.setEnabled(False)
        self.capture_button.clicked.connect(self._save_capture)
        side.addWidget(self.capture_button)
        self.palette_combo.currentTextChanged.connect(self._apply_presentation)
        self.auto_range_check.toggled.connect(self._auto_changed)
        self.min_spin.valueChanged.connect(self._range_changed)
        self.max_spin.valueChanged.connect(self._range_changed)

        session_form = QFormLayout()
        self.device_label = QLabel("Disconnected")
        self.state_label = QLabel("Disconnected")
        self.state_label.setWordWrap(True)
        self.mode_label = QLabel("—")
        self.mode_label.setWordWrap(True)
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
        roi_group = QGroupBox("ROI — native pixels")
        roi_layout = QVBoxLayout(roi_group)
        self.roi_geometry_label = QLabel("Drag on the image to select a rectangle")
        self.roi_geometry_label.setWordWrap(True)
        self.roi_values_label = QLabel("Unavailable")
        self.roi_values_label.setWordWrap(True)
        self.clear_roi_button = QPushButton("Clear ROI")
        self.clear_roi_button.setEnabled(False)
        self.clear_roi_button.clicked.connect(self.image_widget.clear_roi)
        roi_layout.addWidget(self.roi_geometry_label)
        roi_layout.addWidget(self.roi_values_label)
        roi_layout.addWidget(self.clear_roi_button)
        side.addWidget(roi_group)
        side.addStretch()
        warning = QLabel(ACCURACY_WARNING)
        warning.setWordWrap(True)
        side.addWidget(warning)
        sidebar = QScrollArea()
        sidebar.setWidgetResizable(True)
        sidebar.setFixedWidth(320)
        sidebar.setWidget(panel)
        layout.addWidget(sidebar)

    def _toggle_camera(self) -> None:
        if self.worker is None:
            self.connect_camera()
        else:
            self.stop_camera()

    def connect_camera(self) -> None:
        """Start read-only display acquisition before any explicit control write."""
        if self.worker is not None:
            return
        self.close_capture()
        self._had_error = False
        self.init_requested = False
        self.session_initialized = False
        self._view_count = 0
        self._fps_started = time.monotonic()
        self.state_label.setText("Connecting…")
        self.device_label.setText("Searching for Infiray HT-301")
        worker = CameraWorker(self)
        worker.frame_available.connect(self._on_frame_available)
        worker.connected.connect(self._on_connected)
        worker.notice.connect(self._on_notice)
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
        self.capture_button.setEnabled(False)
        self.state_label.setText("Stopping…")
        worker.request_stop()
        if not worker.wait(10000):
            self.state_label.setText("Still stopping camera; wait before reconnecting")
            return
        self.worker = None
        self.observation = None
        self.image_widget.set_observation(None)
        self._sync_legend()
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
        self._sync_legend()
        self._clear_measurements()
        self.initialize_button.setEnabled(False)
        self.capture_button.setEnabled(False)
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
        if (self.worker is None or self.offline_capture is not None or
                (self.sender() is not None and self.sender() is not self.worker)):
            return
        observation = self.worker.take_latest()
        if observation is None:
            return
        self.observation = observation
        self.image_widget.set_observation(observation)
        self._sync_legend()
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
        self.capture_button.setEnabled(state == SessionState.RADIOMETRIC_READY and
                                       measurement is not None)
        self._show_measurement(measurement)

    def _show_measurement(self, measurement) -> None:
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
        self._update_roi()

    def _clear_measurements(self) -> None:
        for label in (self.cursor_label, self.high_label, self.low_label,
                      self.center_pixel_label, self.trailer_center_label):
            label.setText("Unavailable")
        self._update_roi()

    def _on_roi_changed(self, roi: NativeROI | None) -> None:
        self.clear_roi_button.setEnabled(roi is not None)
        self.roi_geometry_label.setText(
            "Drag on the image to select a rectangle" if roi is None else
            f"[{roi.x1},{roi.x2}) × [{roi.y1},{roi.y2})")
        self._update_roi()

    def _update_roi(self) -> None:
        stats = current_roi_statistics(self.observation, self.image_widget.roi)
        self.roi_values_label.setText("Unavailable" if stats is None else
                                     f"Min: {stats.min_c:.2f} °C at {stats.min_xy}\n"
                                     f"Max: {stats.max_c:.2f} °C at {stats.max_xy}\n"
                                     f"Mean: {stats.mean_c:.2f} °C\n"
                                     f"Pixels: {stats.pixel_count}")

    def _save_capture(self) -> None:
        """Freeze the displayed ready frame before opening the destination dialog."""
        if self.offline_capture is not None:
            return
        observation = self.image_widget.observation
        bounds = self.image_widget.effective_bounds
        if (observation is None or observation.state != SessionState.RADIOMETRIC_READY or
                observation.measurement is None or bounds is None):
            self.capture_button.setEnabled(False)
            return
        try:
            snapshot = snapshot_capture(observation, self.image_widget.palette,
                                        bounds, self.image_widget.automatic_range,
                                        roi=self.image_widget.roi)
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Capture unavailable", str(exc))
            return
        suggested = f"ht301-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
        selected, _ = QFileDialog.getSaveFileName(self, "Save radiometric capture",
                                                  suggested, "PNG rendering (*.png)")
        if not selected:
            return
        path = Path(selected)
        basename = path.with_suffix("") if path.suffix.lower() == ".png" else path
        try:
            saved = export_capture(snapshot, basename)
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Capture failed", str(exc))
            return
        self.statusBar().showMessage(f"Saved radiometric capture: {saved['.json']}", 10000)

    def _auto_changed(self, automatic: bool) -> None:
        self.min_spin.setEnabled(not automatic)
        self.max_spin.setEnabled(not automatic)
        self._apply_presentation()

    def _range_changed(self) -> None:
        """Keep manual Celsius bounds ordered, even during interactive edits."""
        if self.sender() is self.min_spin:
            self.max_spin.setMinimum(self.min_spin.value() + .01)
        elif self.sender() is self.max_spin:
            self.min_spin.setMaximum(self.max_spin.value() - .01)
        self._apply_presentation()

    def _apply_presentation(self) -> None:
        self.image_widget.set_presentation(self.palette_combo.currentText(),
                                           self.auto_range_check.isChecked(),
                                           self.min_spin.value(), self.max_spin.value())
        self._sync_legend()

    def _sync_legend(self) -> None:
        """Use the same effective bounds and palette as the rendered frame."""
        bounds = self.image_widget.effective_bounds
        self.legend.set_presentation(bounds, self.palette_combo.currentText())
        if bounds is None:
            self.range_label.setText("Unavailable until a valid measurement frame")
        else:
            mode = "Auto" if self.auto_range_check.isChecked() else "Locked"
            self.range_label.setText(f"{mode}: {bounds.lower:.2f} to {bounds.upper:.2f} °C")

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
        self.fps_label.setText(f"{self._view_count / elapsed:.1f}"
                               if self.worker is not None and elapsed > 0 else "—")
        self._view_count = 0
        self._fps_started = now

    def _on_failure(self, message: str) -> None:
        if (self.offline_capture is not None or
                (self.sender() is not None and self.sender() is not self.worker)):
            return
        self._had_error = True
        self.init_requested = False
        self.state_label.setText(f"Error: {message}")
        self.initialize_button.setEnabled(False)
        self.capture_button.setEnabled(False)
        self.observation = None
        self.image_widget.set_observation(None)
        self._sync_legend()
        self._clear_measurements()

    def _on_connected(self, device: str) -> None:
        if self.sender() is self.worker and self.offline_capture is None:
            self.device_label.setText(device)

    def _on_notice(self, notice: str) -> None:
        if self.sender() is self.worker and self.offline_capture is None:
            self.state_label.setText(notice)

    def _choose_capture(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self, "Open radiometric capture", "", "Capture completion marker (*.json)")
        if selected:
            self.open_capture(Path(selected))

    def open_capture(self, path: Path) -> bool:
        """Release acquisition first; load matrices without connecting a camera."""
        self._auto_connect_timer.stop()
        self.stop_camera()
        if self.worker is not None:
            return False
        self.close_capture()
        try:
            capture = load_capture(path)
        except CaptureError as exc:
            QMessageBox.warning(self, "Cannot open radiometric capture", str(exc))
            return False
        self.offline_capture = capture
        self.observation = capture
        self.pointer = None
        self.image_widget.pointer = None
        self.image_widget._mouse_widget = None
        controls = (self.palette_combo, self.auto_range_check, self.min_spin, self.max_spin)
        blockers = [QSignalBlocker(control) for control in controls]
        bounds = capture.original_bounds
        # Expand spin limits when reopening an uncommon but finite stored range.
        self.min_spin.setRange(min(-273.15, bounds.lower), max(999.99, bounds.upper))
        self.max_spin.setRange(min(-273.14, bounds.lower), max(1000.0, bounds.upper))
        self.min_spin.setValue(bounds.lower)
        self.max_spin.setValue(bounds.upper)
        self.min_spin.setMaximum(bounds.upper - .01)
        self.max_spin.setMinimum(bounds.lower + .01)
        self.palette_combo.setCurrentText(capture.original_palette)
        self.auto_range_check.setChecked(capture.automatic_range)
        self.min_spin.setEnabled(not capture.automatic_range)
        self.max_spin.setEnabled(not capture.automatic_range)
        del blockers
        self.image_widget.set_presentation(capture.original_palette, capture.automatic_range,
                                           bounds.lower, bounds.upper)
        self.image_widget.set_observation(capture, restored_bounds=bounds)
        self.image_widget.set_roi(capture.roi)
        self._sync_legend()
        self._show_measurement(capture)
        self.initialize_button.setEnabled(False)
        self.capture_button.setEnabled(False)
        for button in (self.close_capture_button, self.render_button, self.metadata_button):
            button.setEnabled(True)
        self.device_label.setText("Offline — no camera")
        self.state_label.setText(f"Saved capture: {capture.source_path.name}\n"
                                 f"{capture.metadata['captured_at_utc']}")
        self.mode_label.setText("Saved raw14 / native-equivalent matrix")
        self.fps_label.setText("—")
        return True

    def close_capture(self) -> None:
        """Return to disconnected mode; never auto-connect or retain saved readings."""
        if self.offline_capture is None:
            return
        self.offline_capture = None
        self.observation = None
        self.pointer = None
        self.image_widget.pointer = None
        self.image_widget._mouse_widget = None
        self.image_widget.set_observation(None)
        self.image_widget.clear_roi()
        self._sync_legend()
        self._clear_measurements()
        for button in (self.close_capture_button, self.render_button, self.metadata_button):
            button.setEnabled(False)
        self.device_label.setText("Disconnected")
        self.state_label.setText("Disconnected")
        self.mode_label.setText("—")
        self.fps_label.setText("—")

    def _show_metadata(self) -> None:
        if self.offline_capture is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Saved capture metadata — original")
        dialog.resize(680, 640)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(json.dumps(self.offline_capture.metadata, indent=2, sort_keys=True))
        layout.addWidget(text)
        dialog.exec()

    def _save_rendered(self) -> None:
        capture = self.offline_capture
        bounds = self.image_widget.effective_bounds
        if capture is None or bounds is None:
            return
        palette = self.image_widget.palette
        selected, _ = QFileDialog.getSaveFileName(
            self, "Save rendered image only", "rendered.png", "PNG rendering (*.png)")
        if not selected:
            return
        target = Path(selected)
        if not target.suffix:
            target = target.with_suffix(".png")
        try:
            saved = save_rendered_image(capture, target, palette, bounds)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Rendering failed", str(exc))
            return
        self.statusBar().showMessage(f"Saved rendered PNG only: {saved}", 10000)

    def closeEvent(self, event) -> None:
        self.stop_camera()
        if self.worker is not None:
            event.ignore()
        else:
            event.accept()


def main() -> None:
    """Launch live display acquisition or camera-free saved capture inspection."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, help="Open a saved capture without camera acquisition")
    args = parser.parse_args()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow(auto_connect=args.capture is None)
    if args.capture is not None:
        window.open_capture(args.capture)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
