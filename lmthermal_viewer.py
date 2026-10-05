#!/usr/bin/env python3
"""Minimal PyQt HT-301 viewer backed by the validated radiometric session."""

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, QSignalBlocker, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QImage, QPainter, QPen
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
                             QDialog, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                             QLabel, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
                             QScrollArea, QSlider, QVBoxLayout, QWidget)

from celsius_palette import (DEFAULT_PALETTE, PALETTES, CelsiusRange,
                             effective_range, legend_image, render_temperature)
from measurement_logger import MeasurementLogger, SUPPORTED_RATES
from radiometric_recorder import RadiometricRecorder
from radiometric_recording import RATES as RECORDING_RATES
# Acquisition modules (including Linux-only ioctl code) are loaded only on Connect.
def CameraWorker(*args, **kwargs):
    from mvp_camera_worker import CameraWorker as Worker
    return Worker(*args, **kwargs)

from offline_measurement import OfflineMeasurement, Geometry, Rectangle, Transform, adapt_legacy, save_offline_png
from offline_load_worker import OfflineLoadWorker
from lmtx_json import dumps as metadata_text
from playback_worker import PlaybackWorker
from mvp_presentation import (current_extrema, current_measurement, current_reading, display_image,
                              image_viewport, native_edge_to_widget,
                              native_to_widget, widget_to_native)
from radiometric_capture import CaptureError, load_capture
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
        self.empty_text = "Camera disconnected"
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

    def _geometry(self):
        return self.observation.geometry if isinstance(self.observation, OfflineMeasurement) else Geometry(384, 288)

    def _mapping(self):
        return {'geometry': self._geometry(),
                'transform': self.observation.transform if isinstance(self.observation, OfflineMeasurement) else Transform()}

    def _rectangle(self, first, last):
        if not isinstance(self.observation, OfflineMeasurement):
            return roi_from_native_pixels(first, last)
        x1, x2 = sorted((first[0], last[0]))
        y1, y2 = sorted((first[1], last[1]))
        return Rectangle(x1, y1, x2+1, y2+1).validate(self._geometry())

    def _drag_roi(self, position) -> None:
        if self._roi_anchor is not None:
            endpoint = widget_to_native(position.x(), position.y(), self.width(),
                                         self.height(), clip=True, **self._mapping())
            if endpoint is not None:
                self.set_roi(self._rectangle(self._roi_anchor, endpoint))

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
        elif isinstance(observation, OfflineMeasurement):
            bounds = None
            if observation.has_readings:
                bounds = self._restored_bounds or (observation.auto_bounds() if self.automatic_range else
                                                  CelsiusRange(self.locked_lower, self.locked_upper))
            rgb = observation.render(self.palette, bounds)
            self.image = None if rgb is None else QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0],
                                                        QImage.Format.Format_RGB888).copy()
            self.effective_bounds = bounds
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
        self.pointer = widget_to_native(position.x(), position.y(), self.width(), self.height(), **self._mapping())
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
                self.set_roi(self._rectangle(self.pointer, self.pointer))

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
        x, y = native_to_widget(*point, self.width(), self.height(), **self._mapping())
        painter.setPen(QPen(color, 2))
        painter.drawLine(QPointF(x - radius, y), QPointF(x + radius, y))
        painter.drawLine(QPointF(x, y - radius), QPointF(x, y + radius))

    def _saved_analysis(self, painter):
        """Paint understood native annotations; future shapes remain metadata."""
        if not isinstance(self.observation, OfflineMeasurement):
            return
        analysis = self.observation.manifest.get('analysis', {})
        anchors = {}
        for point in analysis.get('points', ()):
            if point['coordinate_space'] != 'native':
                continue
            anchors[point['id']] = (point['x_px'], point['y_px'])
            self._marker(painter, anchors[point['id']], QColor('white'), 4)
        for shape in analysis.get('shapes', ()):
            if shape['type'] != 'rectangle' or shape['coordinate_space'] != 'native':
                continue
            b = shape['bounds']
            first = native_edge_to_widget(b['x1_px'], b['y1_px'], self.width(), self.height(), **self._mapping())
            last = native_edge_to_widget(b['x2_px'], b['y2_px'], self.width(), self.height(), **self._mapping())
            painter.setPen(QPen(QColor('white'), 1, Qt.PenStyle.DashLine))
            painter.drawRect(QRectF(QPointF(*first), QPointF(*last)).normalized())
            anchors[shape['id']] = (b['x1_px'], b['y1_px'])
        for annotation in analysis.get('annotations', ()):
            if annotation['coordinate_space'] != 'native':
                continue
            anchor = annotation.get('anchor')
            point = ((anchor['x_px'], anchor['y_px']) if anchor else
                     anchors.get(annotation.get('target_id')))
            if point is None:
                continue
            x, y = native_to_widget(*point, self.width(), self.height(), **self._mapping())
            painter.setPen(QColor('white'))
            # drawText is plain text: no HTML, external resource or script handling.
            painter.drawText(QPointF(x+6, y-6), annotation['text'])

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(14, 16, 20))
        if self.image is None:
            painter.setPen(QColor("white"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.empty_text)
            return
        area = image_viewport(self.width(), self.height(), **self._mapping())
        painter.drawImage(QRectF(area.left, area.top, area.width, area.height), self.image)
        self._saved_analysis(painter)
        if self.roi is not None:
            left, top = native_edge_to_widget(self.roi.x1, self.roi.y1,
                                               self.width(), self.height(), **self._mapping())
            right, bottom = native_edge_to_widget(self.roi.x2, self.roi.y2,
                                                   self.width(), self.height(), **self._mapping())
            rectangle = QRectF(min(left, right), min(top, bottom), abs(right-left), abs(bottom-top))
            painter.setPen(QPen(QColor("black"), 4))
            painter.drawRect(rectangle)
            painter.setPen(QPen(QColor(80, 255, 150), 2))
            painter.drawRect(rectangle)
        self._marker(painter, (self._geometry().width//2, self._geometry().height//2), QColor(255, 220, 0), 10)
        extrema = current_extrema(self.observation)
        if extrema is not None:
            self._marker(painter, extrema[0][0], QColor(255, 70, 70))
            self._marker(painter, extrema[1][0], QColor(70, 175, 255))
        reading = current_reading(self.observation, self.pointer)
        if reading is not None:
            self._marker(painter, (reading.x, reading.y), QColor("white"), 4)
            native = f" raw {reading.raw14};" if reading.raw14 is not None else ''
            text = f"({reading.x},{reading.y}){native} {reading.native_equivalent_c:.2f} °C"
            x, y = native_to_widget(reading.x, reading.y, self.width(), self.height(), **self._mapping())
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
        self.logger = None
        self.recorder = None
        self.offline_capture = None
        self.offline_loader = None
        self._offline_token = 0
        self.playback = None
        self.playback_worker = None
        self._playback_token = 0
        self._restoring_roi = False
        self.observation = None
        self.pointer = None
        self.init_requested = False
        self.session_initialized = False
        self._had_error = False
        self._view_count = 0
        self._fps_started = time.monotonic()
        self._setup_ui()
        self._playback_timer = QTimer(self)
        self._playback_timer.timeout.connect(self._playback_tick)
        self._playback_timer.setInterval(33)
        self._fps_timer = QTimer(self)
        self._fps_timer.timeout.connect(self._update_fps)
        self._fps_timer.timeout.connect(self._update_logging_status)
        self._fps_timer.timeout.connect(self._update_recording_status)
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
        view_column = QVBoxLayout()
        view_row = QHBoxLayout()
        view_row.addWidget(self.image_widget, 1)
        self.legend = CelsiusLegend()
        view_row.addWidget(self.legend)
        view_column.addLayout(view_row, 1)
        self.playback_group = QGroupBox("Offline recording playback")
        playback_layout = QVBoxLayout(self.playback_group)
        self.timeline_slider = QSlider(Qt.Orientation.Horizontal)
        self.timeline_slider.valueChanged.connect(self._scrub_recording)
        playback_layout.addWidget(self.timeline_slider)
        controls = QHBoxLayout()
        self.first_sample_button = QPushButton("|◀")
        self.previous_sample_button = QPushButton("Previous")
        self.play_button = QPushButton("Play")
        self.next_sample_button = QPushButton("Next")
        self.last_sample_button = QPushButton("▶|")
        self.first_sample_button.clicked.connect(lambda: self._select_recording(0))
        self.previous_sample_button.clicked.connect(lambda: self._step_recording(-1))
        self.play_button.clicked.connect(self._toggle_playback)
        self.next_sample_button.clicked.connect(lambda: self._step_recording(1))
        self.last_sample_button.clicked.connect(lambda: self._select_recording(len(self.playback.entries)-1) if self.playback else None)
        for button in (self.first_sample_button, self.previous_sample_button, self.play_button,
                       self.next_sample_button, self.last_sample_button):
            controls.addWidget(button)
        self.playback_speed = QComboBox()
        for speed in (.5, 1, 2, 4):
            self.playback_speed.addItem(f"{speed:g}×", speed)
        self.playback_speed.setCurrentIndex(1)
        self.playback_speed.currentIndexChanged.connect(self._change_playback_speed)
        controls.addWidget(self.playback_speed)
        self.close_recording_button = QPushButton("Close recording")
        self.close_recording_button.clicked.connect(self.close_recording)
        controls.addWidget(self.close_recording_button)
        playback_layout.addLayout(controls)
        self.playback_status_label = QLabel()
        self.playback_status_label.setWordWrap(True)
        playback_layout.addWidget(self.playback_status_label)
        self.playback_roi_label = QLabel("ROI: none")
        playback_layout.addWidget(self.playback_roi_label)
        self.playback_group.hide()
        view_column.addWidget(self.playback_group)
        layout.addLayout(view_column, 1)
        panel = QWidget()
        panel.setMinimumWidth(280)
        side = QVBoxLayout(panel)
        self.connect_button = QPushButton("Connect camera")
        self.connect_button.clicked.connect(self._toggle_camera)
        side.addWidget(self.connect_button)
        self.open_capture_button = QPushButton("Open still capture (.lmtx / legacy)")
        self.open_capture_button.clicked.connect(self._choose_capture)
        side.addWidget(self.open_capture_button)
        self.open_recording_button = QPushButton("Open radiometric recording")
        self.open_recording_button.clicked.connect(self._choose_recording)
        side.addWidget(self.open_recording_button)
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

        log_group = QGroupBox("Live measurement log")
        log_layout = QVBoxLayout(log_group)
        self.log_rate_combo = QComboBox()
        for rate in SUPPORTED_RATES:
            self.log_rate_combo.addItem(f"{rate:g} Hz", rate)
        self.log_rate_combo.setCurrentIndex(1)
        log_layout.addWidget(self.log_rate_combo)
        self.start_log_button = QPushButton("Start logging")
        self.start_log_button.setEnabled(False)
        self.start_log_button.clicked.connect(self._start_logging)
        self.stop_log_button = QPushButton("Stop logging")
        self.stop_log_button.setEnabled(False)
        self.stop_log_button.clicked.connect(lambda: self._stop_logging())
        log_layout.addWidget(self.start_log_button)
        log_layout.addWidget(self.stop_log_button)
        self.log_status_label = QLabel("Not recording — live camera only")
        self.log_status_label.setWordWrap(True)
        log_layout.addWidget(self.log_status_label)
        side.addWidget(log_group)

        recording_group = QGroupBox("Radiometric recording")
        recording_layout = QVBoxLayout(recording_group)
        self.record_rate_combo = QComboBox()
        for rate in RECORDING_RATES:
            self.record_rate_combo.addItem(f"{rate} Hz" + (" (experimental)" if rate == 25 else ""), rate)
        self.record_rate_combo.setCurrentIndex(2)
        recording_layout.addWidget(self.record_rate_combo)
        self.start_record_button = QPushButton("Start recording…")
        self.start_record_button.setEnabled(False)
        self.start_record_button.clicked.connect(self._start_recording)
        self.stop_record_button = QPushButton("Stop recording")
        self.stop_record_button.setEnabled(False)
        self.stop_record_button.clicked.connect(lambda: self._stop_recording())
        recording_layout.addWidget(self.start_record_button)
        recording_layout.addWidget(self.stop_record_button)
        self.record_status_label = QLabel("Not recording — live camera only; CSV log is exclusive")
        self.record_status_label.setWordWrap(True)
        recording_layout.addWidget(self.record_status_label)
        side.addWidget(recording_group)

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
        if not sys.platform.startswith('linux'):
            self.statusBar().showMessage('Live HT-301 acquisition is Linux-only; open a saved capture for offline analysis', 10000)
            return
        if (self.worker is not None or not self._stop_recording("camera_reconnect")
                or not self._stop_logging("camera_reconnect")):
            return
        if not self.close_recording():
            return
        if not self.close_capture(): return
        self.image_widget.empty_text = "Camera disconnected"
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
        self._sync_logging_controls()
        worker.start()

    def stop_camera(self) -> None:
        """Finish acquisition and close both handles before reconnecting."""
        if not self._stop_recording("camera_disconnect") or not self._stop_logging("camera_disconnect"):
            return
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
        self._sync_logging_controls()
        worker.deleteLater()

    def _on_worker_finished(self) -> None:
        worker = self.sender()
        if worker is not self.worker:
            return
        self._stop_recording("camera_finished")
        self._stop_logging("camera_finished")
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
        self._sync_logging_controls()
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
        if (self.worker is None or self.offline_capture is not None or self.playback_worker is not None or
                (self.sender() is not None and self.sender() is not self.worker)):
            return
        observation = self.worker.take_latest()
        if observation is None:
            return
        self.observation = observation
        if self.logger is not None:
            self.logger.update_latest(observation, self.image_widget.roi)
        if self.recorder is not None:
            self.recorder.update_latest(observation, self.image_widget.roi)
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
        elif isinstance(measurement, OfflineMeasurement):
            self._clear_measurements()
            if measurement.has_readings:
                self.high_label.setText(f"{measurement.high_c:.2f} °C at {measurement.high_xy}")
                self.low_label.setText(f"{measurement.low_c:.2f} °C at {measurement.low_xy}")
                center = measurement.literal_center_c
                if center is not None:
                    coordinate = (measurement.geometry.width//2, measurement.geometry.height//2)
                    raw = f"raw {measurement.literal_center_index}, " if measurement.literal_center_index is not None else ''
                    self.center_pixel_label.setText(f"{center:.2f} °C ({raw}{coordinate})")
                if measurement.reported_center is not None:
                    self.trailer_center_label.setText(f"{measurement.trailer_center_c:.2f} °C (raw {measurement.trailer_center_index})")
                self._update_cursor()
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
        if self.playback is not None:
            origin = "Recorded" if self._restoring_roi else "Inspection"
            self.playback_roi_label.setText(f"{origin} ROI" if roi is not None else f"{origin} ROI: none")
        if self.recorder is not None:
            self.recorder.update_latest(self.observation, roi)
        if self.logger is not None:
            self.logger.update_latest(self.observation, roi)
        self.clear_roi_button.setEnabled(roi is not None)
        self.roi_geometry_label.setText(
            "Drag on the image to select a rectangle" if roi is None else
            f"[{roi.x1},{roi.x2}) × [{roi.y1},{roi.y2})")
        self._update_roi()

    def _update_roi(self) -> None:
        stats = current_roi_statistics(self.observation, self.image_widget.roi)
        if stats is None:
            self.roi_values_label.setText('Unavailable')
        elif stats.min_c is None:
            self.roi_values_label.setText(f'Unavailable\nPixels: {stats.pixel_count}; valid: {stats.valid_pixel_count}')
        else:
            self.roi_values_label.setText(f"Min: {stats.min_c:.2f} °C at {stats.min_xy}\n"
                                          f"Max: {stats.max_c:.2f} °C at {stats.max_xy}\n"
                                          f"Mean: {stats.mean_c:.2f} °C\n"
                                          f"Pixels: {stats.pixel_count}" +
                                          (f"; valid: {stats.valid_pixel_count}" if hasattr(stats, 'valid_pixel_count') else ''))

    def _save_capture(self) -> None:
        """Freeze the displayed ready frame before opening the destination dialog."""
        if self.offline_capture is not None or self.playback_worker is not None:
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

    def _range_gap(self):
        # QDoubleSpinBox rounds constraints to its configured decimal precision.
        # A sub-precision nextafter constraint can collapse Min and Max together.
        return max(10.0**(-self.min_spin.decimals()), math.ulp(self.min_spin.value()),
                   math.ulp(self.max_spin.value()))

    def _range_changed(self) -> None:
        """Keep manual Celsius bounds ordered, even during interactive edits."""
        if self.sender() is self.min_spin:
            self.max_spin.setMinimum(self.min_spin.value() + self._range_gap())
        elif self.sender() is self.max_spin:
            self.min_spin.setMaximum(self.max_spin.value() - self._range_gap())
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
        native = f" raw {reading.raw14};" if reading is not None and reading.raw14 is not None else ''
        self.cursor_label.setText("Unavailable" if reading is None else
                                  f"({reading.x},{reading.y}){native} "
                                  f"{reading.native_equivalent_c:.2f} °C")

    def _update_fps(self) -> None:
        now = time.monotonic()
        elapsed = now - self._fps_started
        self.fps_label.setText(f"{self._view_count / elapsed:.1f}"
                               if self.worker is not None and elapsed > 0 else "—")
        self._view_count = 0
        self._fps_started = now

    def _on_failure(self, message: str) -> None:
        if (self.offline_capture is not None or self.playback_worker is not None or
                (self.sender() is not None and self.sender() is not self.worker)):
            return
        self._stop_recording("camera_error")
        self._stop_logging("camera_error")
        self._had_error = True
        self.init_requested = False
        self.state_label.setText(f"Error: {message}")
        self.initialize_button.setEnabled(False)
        self.capture_button.setEnabled(False)
        self.observation = None
        self.image_widget.set_observation(None)
        self._sync_legend()
        self._clear_measurements()

    def _sync_logging_controls(self) -> None:
        live = self.worker is not None and self.offline_capture is None and self.playback_worker is None
        recording = self.logger is not None
        self.start_log_button.setEnabled(live and not recording and self.recorder is None)
        self.stop_log_button.setEnabled(recording)
        self.log_rate_combo.setEnabled(not recording)
        self.start_record_button.setEnabled(live and not recording and self.recorder is None)
        self.stop_record_button.setEnabled(self.recorder is not None)
        self.record_rate_combo.setEnabled(self.recorder is None)

    def _start_logging(self) -> None:
        if (self.worker is None or self.offline_capture is not None or self.playback_worker is not None
                or self.logger is not None or self.recorder is not None):
            return
        selected, _ = QFileDialog.getSaveFileName(
            self, "Start live measurement log", f"measurements-{datetime.now():%Y%m%d-%H%M%S}.csv",
            "Measurement time series (*.csv)")
        if not selected:
            return
        # A modal dialog can process queued disconnect/failure events.
        if (self.worker is None or self.offline_capture is not None or self.playback_worker is not None
                or self.logger is not None or self.recorder is not None):
            return
        path = Path(selected)
        if not path.suffix:
            path = path.with_suffix(".csv")
        identity = self.device_label.text() if self.observation is not None else None
        try:
            self.logger = MeasurementLogger(path, rate_hz=self.log_rate_combo.currentData(),
                                            camera_identity=identity,
                                            observation=self.observation, roi=self.image_widget.roi)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Cannot start logging", str(exc))
            return
        self._sync_logging_controls()
        self._update_logging_status()

    def _stop_logging(self, reason="operator_stop") -> bool:
        if self.logger is None:
            return True
        logger = self.logger
        try:
            status = logger.stop(reason)
        except TimeoutError as exc:
            self.log_status_label.setText(str(exc))
            return False
        except OSError as exc:
            self.log_status_label.setText(f"Log incomplete: {exc}\n{logger.status.csv_path}")
            self.logger = None
            self._sync_logging_controls()
            return True
        self.logger = None
        self.log_status_label.setText(
            f"Complete: {status.sample_count} samples ({status.valid_sample_count} valid), "
            f"{status.duration_s:.1f} s\n{status.csv_path}")
        self._sync_logging_controls()
        return True

    def _update_logging_status(self) -> None:
        if self.logger is None:
            return
        status = self.logger.status
        if status.state != "recording":
            self._stop_logging()
            return
        self.log_status_label.setText(
            f"Recording: {status.duration_s:.1f} s, {status.sample_count} samples "
            f"({status.valid_sample_count} valid)\n{status.csv_path}")

    def _start_recording(self) -> None:
        if (self.worker is None or self.offline_capture is not None or self.playback_worker is not None
                or self.logger is not None or self.recorder is not None):
            return
        selected, _ = QFileDialog.getSaveFileName(
            self, "New radiometric recording directory", f"recording-{datetime.now():%Y%m%d-%H%M%S}.lmthermal",
            "Radiometric recording (*.lmthermal)", options=QFileDialog.Option.DontConfirmOverwrite)
        if not selected:
            return
        if (self.worker is None or self.offline_capture is not None or self.playback_worker is not None
                or self.logger is not None or self.recorder is not None):
            return
        path = Path(selected)
        if not path.suffix:
            path = path.with_suffix(".lmthermal")
        try:
            self.recorder = RadiometricRecorder(
                path, rate_hz=self.record_rate_combo.currentData(),
                observation=self.observation, roi=self.image_widget.roi,
                camera_identity=self.device_label.text() if self.observation is not None else None)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Cannot start recording", str(exc))
            return
        self._sync_logging_controls()
        self._update_recording_status()

    def _stop_recording(self, reason="operator_stop") -> bool:
        if self.recorder is None:
            return True
        recorder = self.recorder
        try:
            status = recorder.stop(reason)
        except TimeoutError as exc:
            self.record_status_label.setText(str(exc))
            return False
        except OSError as exc:
            self.record_status_label.setText(f"Recording incomplete: {exc}\n{recorder.directory}")
            self.recorder = None
            self._sync_logging_controls()
            return True
        self.recorder = None
        self.record_status_label.setText(
            f"Complete: {status.duration_s:.1f} s, {status.requested_samples} samples, "
            f"{status.valid_frames} stored, {status.gaps} gaps, {status.dropped} dropped\n{status.directory}")
        self._sync_logging_controls()
        return True

    def _update_recording_status(self) -> None:
        if self.recorder is None:
            return
        status = self.recorder.status
        if status.state != "recording":
            self._stop_recording()
            return
        self.record_status_label.setText(
            f"Recording: {status.duration_s:.1f} s, {status.requested_samples} samples\n"
            f"{status.valid_frames} committed, {status.gaps} gaps, {status.dropped} dropped\n"
            f"Queue {status.queue_depth}/4; chunks {status.bytes_written/1e6:.2f} MB\n{status.directory}")

    def _on_connected(self, device: str) -> None:
        if self.sender() is self.worker and self.offline_capture is None and self.playback_worker is None:
            self.device_label.setText(device)

    def _on_notice(self, notice: str) -> None:
        if self.sender() is self.worker and self.offline_capture is None and self.playback_worker is None:
            self.state_label.setText(notice)

    def _choose_recording(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Open .lmthermal recording directory")
        if selected:
            self.open_recording(Path(selected))

    def open_recording(self, path: Path) -> bool:
        """Validate asynchronously after all live resources have been finalized."""
        self._auto_connect_timer.stop()
        self.stop_camera()
        if self.worker is not None or self.logger is not None or self.recorder is not None:
            return False
        if not self.close_recording():
            return False
        if not self.close_capture(): return False
        self.playback_group.show()
        self.playback_status_label.setText("Validating manifest, timeline and committed chunks…")
        self.image_widget.empty_text = "Opening recording…"
        self.image_widget.set_observation(None)
        self.observation = None
        self._clear_measurements()
        self._sync_legend()
        self.device_label.setText("Offline recording — no camera")
        self.state_label.setText("Validating recording…")
        self.initialize_button.setEnabled(False)
        self.capture_button.setEnabled(False)
        self.render_button.setEnabled(False)
        self._set_playback_controls(False)
        worker = PlaybackWorker(self)
        worker.result_available.connect(self._on_playback_result)
        self.playback_worker = worker
        self._playback_token += 1
        worker.request(self._playback_token, path=path)
        worker.start()
        self._sync_logging_controls()
        return True

    def _set_playback_controls(self, enabled):
        for control in (self.timeline_slider, self.first_sample_button, self.previous_sample_button,
                        self.next_sample_button, self.last_sample_button, self.play_button, self.playback_speed):
            control.setEnabled(enabled)

    def close_recording(self) -> bool:
        self._playback_timer.stop()
        worker = self.playback_worker
        if worker is None:
            return True
        self._playback_token += 1
        worker.request_stop()
        if not worker.wait(10000):
            self.state_label.setText("Still closing recording loader; wait before switching modes")
            return False
        self.playback_worker = None
        self.playback = None
        self.observation = None
        self.pointer = None
        self.image_widget.pointer = None
        self.image_widget._mouse_widget = None
        self.image_widget.empty_text = "Camera disconnected"
        self.image_widget.set_observation(None)
        self.image_widget.clear_roi()
        self._sync_legend()
        self._clear_measurements()
        self.playback_group.hide()
        self.render_button.setText("Save rendered image")
        self.metadata_button.setEnabled(False)
        self.render_button.setEnabled(False)
        self.device_label.setText("Disconnected")
        self.state_label.setText("Disconnected")
        self.mode_label.setText("—")
        self._sync_logging_controls()
        worker.deleteLater()
        return True

    def _on_playback_result(self):
        if self.playback_worker is None or self.sender() is not self.playback_worker:
            return
        result = self.playback_worker.take_latest()
        if result is None:
            return
        token, model, index, frame, error = result
        if token != self._playback_token:
            return
        if error:
            if self.playback is not None:
                self.playback.pause()
            self._playback_timer.stop()
            self.observation = None
            self.image_widget.empty_text = "Recording cannot be read"
            self.image_widget.set_observation(None)
            self._sync_legend()
            self._clear_measurements()
            self.render_button.setEnabled(False)
            self.play_button.setText("Play")
            self.playback_status_label.setText(f"Recording integrity error: {error}")
            self.state_label.setText(f"Recording integrity error: {error}")
            return
        if self.playback is None:
            self.playback = model
            self.playback.set_speed(self.playback_speed.currentData())
            self.timeline_slider.setRange(0, max(0,len(model.entries)-1))
            self._set_playback_controls(bool(model.entries))
            self.metadata_button.setEnabled(True)
        self._apply_recording_entry(index, frame)

    def _apply_recording_entry(self, index, frame):
        model = self.playback
        entry = model.entries[index] if model.entries else None
        completion = "Complete" if model.completed else "INCOMPLETE — recovered committed chunks"
        self.observation = adapt_legacy(frame) if frame is not None else None
        reason = "Empty recording" if entry is None else {
            "valid": "Valid recorded matrix", "gap": "Invalid camera/session gap",
            "drop": "Recorder drop", "uncommitted": "Uncommitted matrix unavailable"}[entry.kind]
        self.image_widget.empty_text = reason + (f"\n{entry.reason}" if entry else "")
        self.image_widget.set_observation(self.observation)
        self._restoring_roi = True
        try:
            self.image_widget.set_roi(entry.roi if entry else None)
        finally:
            self._restoring_roi = False
        self._sync_legend()
        self._show_measurement(self.observation)
        self.render_button.setEnabled(frame is not None)
        self.render_button.setText("Save selected frame PNG…")
        self.state_label.setText(f"{completion}\n{reason}" + (f" — {entry.reason}" if entry else ""))
        self.mode_label.setText("Stored raw14 / native-equivalent matrix" if frame else reason)
        blocker = QSignalBlocker(self.timeline_slider)
        self.timeline_slider.setValue(index)
        del blocker
        self.playback_status_label.setText(
            f"{completion} | sample {index+1 if entry else 0}/{len(model.entries)}"
            + (f" | sequence {entry.sequence} | {entry.elapsed_s:.3f}/{model.duration_s:.3f} s\n"
               f"{entry.timestamp_utc} | {reason}: {entry.reason}" if entry else ""))
        self.play_button.setText("Pause" if model.playing else "Play")

    def _request_recording_index(self, index):
        self._playback_token += 1
        self.observation = None
        self.image_widget.empty_text = f"Loading sample {index+1}…"
        self.image_widget.set_observation(None)
        self._sync_legend()
        self._clear_measurements()
        self.render_button.setEnabled(False)
        entry = self.playback.entries[index]
        blocker = QSignalBlocker(self.timeline_slider)
        self.timeline_slider.setValue(index)
        del blocker
        completion = "Complete" if self.playback.completed else "INCOMPLETE — recovered committed chunks"
        self.playback_status_label.setText(
            f"{completion} | Loading sample {index+1}/{len(self.playback.entries)} (sequence {entry.sequence}) "
            f"| {entry.elapsed_s:.3f}/{self.playback.duration_s:.3f} s")
        self.state_label.setText("Loading recorded sample; readings unavailable")
        self.mode_label.setText("Stored matrix pending")
        self.playback_worker.request(self._playback_token, model=self.playback, index=index)

    def _select_recording(self, index):
        if self.playback is None or not self.playback.entries:
            return
        self.playback.seek(index)
        self._playback_timer.stop()
        self.play_button.setText("Play")
        self._request_recording_index(self.playback.index)

    def _scrub_recording(self, index):
        self._select_recording(index)

    def _step_recording(self, delta):
        if self.playback:
            self._select_recording(self.playback.index+delta)

    def _toggle_playback(self):
        if self.playback is None:
            return
        if self.playback.playing:
            self.playback.pause()
            self._playback_timer.stop()
            self.play_button.setText("Play")
        else:
            previous = self.playback.index
            self.playback.play()
            if previous != self.playback.index:
                self._request_recording_index(self.playback.index)
            self._playback_timer.start()
            self.play_button.setText("Pause")

    def _change_playback_speed(self):
        if self.playback:
            self.playback.set_speed(self.playback_speed.currentData())

    def _playback_tick(self):
        if self.playback is None:
            return
        previous = self.playback.index
        self.playback.advance()
        if previous != self.playback.index:
            self._request_recording_index(self.playback.index)
        if not self.playback.playing:
            self._playback_timer.stop()
            self.play_button.setText("Play")

    def _choose_capture(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self, "Open still capture", "", "Still capture (*.lmtx *.json);;All files (*)")
        if selected:
            self.open_capture(Path(selected))

    def open_capture(self, path: Path) -> bool:
        """Release acquisition first; load matrices without connecting a camera."""
        path = Path(path)
        try:
            with path.open('rb') as stream:
                zip_header = stream.read(4) == b'PK\x03\x04'
        except OSError:
            zip_header = False
        if path.suffix.lower() == '.lmtx' or zip_header:
            return self.open_lmtx(path)
        self._auto_connect_timer.stop()
        self.stop_camera()
        if self.worker is not None or self.logger is not None or self.recorder is not None:
            return False
        if not self.close_recording():
            return False
        if not self.close_capture(): return False
        try:
            capture = adapt_legacy(load_capture(path))
        except CaptureError as exc:
            QMessageBox.warning(self, "Cannot open radiometric capture", str(exc))
            return False
        return self._publish_offline(capture)

    def open_lmtx(self, path):
        self._auto_connect_timer.stop()
        self.stop_camera()
        if self.worker is not None or self.logger is not None or self.recorder is not None: return False
        if not self.close_recording() or not self.close_capture(): return False
        self.observation = None
        self.image_widget.empty_text = 'Validating LMTX…'
        self.image_widget.set_observation(None)
        self.image_widget.clear_roi()
        self._clear_measurements()
        self._sync_legend()
        self.device_label.setText('Offline — no camera')
        self.state_label.setText('Validating LMTX…')
        self.initialize_button.setEnabled(False)
        self.capture_button.setEnabled(False)
        self.render_button.setEnabled(False)
        self._offline_token += 1
        worker = OfflineLoadWorker(path, self._offline_token, self)
        worker.result_available.connect(self._on_offline_result)
        self.offline_loader = worker
        self.close_capture_button.setEnabled(True)
        worker.start()
        self._sync_logging_controls()
        return True

    def _on_offline_result(self):
        worker = self.offline_loader
        if worker is None or self.sender() is not worker or worker.result is None: return
        token, capture, timings, error = worker.result
        if token != self._offline_token: return
        if error:
            self.state_label.setText('LMTX import failed: '+error)
            self.observation = None
            self.image_widget.set_observation(None)
            self._clear_measurements()
            self._sync_legend()
            self.render_button.setEnabled(False)
        else:
            self._publish_offline(capture)
            self.statusBar().showMessage(f"LMTX validated in {timings['total_ms']:.1f} ms", 10000)

    def _publish_offline(self, capture):
        self.offline_capture = capture
        self.observation = capture
        self.pointer = None
        self.image_widget.pointer = None
        self.image_widget._mouse_widget = None
        controls = (self.palette_combo, self.auto_range_check, self.min_spin, self.max_spin)
        blockers = [QSignalBlocker(control) for control in controls]
        bounds = capture.original_bounds or capture.auto_bounds() or CelsiusRange(20, 40)
        # Expand spin limits when reopening an uncommon but finite stored range.
        span = bounds.upper-bounds.lower
        decimals = max(2, min(323, 3-math.floor(math.log10(span)))) if math.isfinite(span) else 2
        self.min_spin.setDecimals(decimals)
        self.max_spin.setDecimals(decimals)
        self.min_spin.setRange(min(-273.15, bounds.lower), max(999.99, bounds.upper))
        self.max_spin.setRange(min(-273.14, bounds.lower), max(1000.0, bounds.upper))
        self.min_spin.setValue(bounds.lower)
        self.max_spin.setValue(bounds.upper)
        self.min_spin.setMaximum(bounds.upper - self._range_gap())
        self.max_spin.setMinimum(bounds.lower + self._range_gap())
        self.palette_combo.setCurrentText(capture.original_palette)
        self.auto_range_check.setChecked(capture.automatic_range)
        self.min_spin.setEnabled(not capture.automatic_range)
        self.max_spin.setEnabled(not capture.automatic_range)
        del blockers
        self.image_widget.set_presentation(capture.original_palette, capture.automatic_range,
                                           bounds.lower, bounds.upper)
        self.image_widget.empty_text = 'No primary image / generic analysis unavailable'
        self.image_widget.set_observation(capture, restored_bounds=bounds)
        self.image_widget.set_roi(capture.roi)
        self._sync_legend()
        self._show_measurement(capture)
        self.initialize_button.setEnabled(False)
        self.capture_button.setEnabled(False)
        for button in (self.close_capture_button, self.render_button, self.metadata_button):
            button.setEnabled(True)
        self.device_label.setText("Offline — no camera")
        source = capture.manifest['source']
        self.state_label.setText(f"Saved capture: {capture.source_path.name}\n"
                                 f"{source['module_id']} / {source['model_id']} / {source['origin']}")
        warning = capture.accuracy_warning or f"Provenance: {dict(capture.provenance)}"
        self.mode_label.setText(f"{capture.geometry.width} × {capture.geometry.height} | {capture.format_id}\n{warning}" +
                               (f"\nUnknown palette {capture.original_palette_id}; using White hot" if capture.palette_fallback else ''))
        self.render_button.setEnabled(self.image_widget.image is not None)
        self.fps_label.setText("—")
        self._sync_logging_controls()
        return True

    def close_capture(self) -> bool:
        """Return to disconnected mode; never auto-connect or retain saved readings."""
        self._offline_token += 1
        loader = self.offline_loader
        if loader is not None:
            loader.request_stop()
            if not loader.wait(10000):
                self.state_label.setText('Still closing offline loader; wait before switching')
                return False
            self.offline_loader = None
            loader.deleteLater()
        if self.offline_capture is None and loader is None: return True
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
        return True

    def _show_metadata(self) -> None:
        if self.offline_capture is None and self.playback is None:
            return
        metadata = self.offline_capture.diagnostic_metadata if self.offline_capture else {
            "manifest": self.playback.recording.manifest,
            "timeline_entry": self.playback.recording.timeline[self.playback.index] if self.playback.entries else None,
            "frame": self.observation.diagnostic_metadata if isinstance(self.observation, OfflineMeasurement) else None}
        dialog = QDialog(self)
        dialog.setWindowTitle("Saved capture metadata — original" if self.offline_capture else "Recording/sample metadata — original")
        dialog.resize(680, 640)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(metadata_text(metadata))
        layout.addWidget(text)
        dialog.exec()

    def _save_rendered(self) -> None:
        capture = self.offline_capture or (self.observation if isinstance(self.observation, OfflineMeasurement) else None)
        bounds = self.image_widget.effective_bounds
        if capture is None:
            return
        palette = self.image_widget.palette
        suggested = "rendered.png"
        if self.playback is not None:
            bundle = capture.source_path.parent
            suggested = str(bundle.parent / f"{bundle.stem}-sample-{self.playback.entry.sequence:06d}.png")
        selected, _ = QFileDialog.getSaveFileName(
            self, "Save rendered image only", suggested, "PNG rendering (*.png)")
        if not selected:
            return
        target = Path(selected)
        if not target.suffix:
            target = target.with_suffix(".png")
        try:
            saved = save_offline_png(capture, target, palette, bounds)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Rendering failed", str(exc))
            return
        self.statusBar().showMessage(f"Saved rendered PNG only: {saved}", 10000)

    def closeEvent(self, event) -> None:
        if not self.close_capture() or not self.close_recording():
            event.ignore()
            return
        if not self._stop_recording("window_closed") or not self._stop_logging("window_closed"):
            event.ignore()
            return
        self.stop_camera()
        if self.worker is not None:
            event.ignore()
        else:
            event.accept()


def main() -> None:
    """Launch live display acquisition or camera-free saved capture inspection."""
    parser = argparse.ArgumentParser(description=__doc__)
    offline = parser.add_mutually_exclusive_group()
    offline.add_argument("--capture", type=Path, help="Open a saved capture without camera acquisition")
    offline.add_argument("--lmtx", type=Path, help="Open an LMTX still without camera acquisition")
    offline.add_argument("--recording", type=Path, help="Open a recording bundle without camera acquisition")
    args = parser.parse_args()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow(auto_connect=args.capture is None and args.recording is None and args.lmtx is None)
    if args.capture is not None:
        window.open_capture(args.capture)
    elif args.recording is not None:
        window.open_recording(args.recording)
    elif args.lmtx is not None:
        window.open_lmtx(args.lmtx)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
