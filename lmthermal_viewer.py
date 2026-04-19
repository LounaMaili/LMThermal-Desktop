#!/usr/bin/env python3
"""
LMThermal — Phase 2: Desktop thermal camera application
PyQt6-based real-time thermal viewer with temperature measurement.

Camera: Infiray HT-301 (T3-317-13), 384x292 YUYV @ 25fps
"""

import sys
import time
import math
import struct
import numpy as np
import cv2

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QGroupBox, QFormLayout,
    QSpinBox, QDoubleSpinBox, QCheckBox, QFileDialog, QMessageBox,
    QStatusBar, QSplitter, QToolBar,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, pyqtBoundSignal, QTimer
from PyQt6.QtGui import QImage, QPixmap, QPainter, QPen, QFont, QColor, QCursor


# --- Constants ---
FRAME_WIDTH = 384
FRAME_HEIGHT = 292
PARAMS_OFFSET = (FRAME_HEIGHT * 3 - 3) * 256 + 254  # 223,742
PARAMS_SIZE = 514

COLORMAPS = {
    "JET": cv2.COLORMAP_JET,
    "INFERNO": cv2.COLORMAP_INFERNO,
    "PLASMA": cv2.COLORMAP_PLASMA,
    "VIRIDIS": cv2.COLORMAP_VIRIDIS,
    "WHITE HOT": -1,  # Grayscale
    "BLACK HOT": -2,   # Inverted grayscale
    "TURBO": cv2.COLORMAP_TURBO,
}


# --- Temperature functions (from Phase 1 reverse engineering) ---

def extract_params(raw_frame: bytes) -> dict:
    """Extract temperature parameters from the last 514 bytes of a YUYV frame."""
    params = raw_frame[PARAMS_OFFSET:PARAMS_OFFSET + PARAMS_SIZE]
    if len(params) < PARAMS_SIZE:
        return None

    def f32(offset):
        return struct.unpack_from('<f', params, offset)[0]

    return {
        'env_temp': f32(4),
        'env_temp2': f32(8),
        'emissivity': f32(12),
        'distance_factor': f32(16),
        'active': struct.unpack_from('<I', params, 20)[0],
        'gain': f32(352),
        'center_temp': f32(356),
        'offset_factor': f32(364),
        'calib_factor': f32(368),
    }


def get_temp_evn(raw_val: float, env_temp: float, b: float) -> float:
    """
    Stefan-Boltzmann temperature calculation.
    Decoded from libthermometry.so::GetTempEvn.
    """
    val = math.pow(raw_val + 273.15, 4.0) - env_temp
    val = b * val
    if val <= 0:
        return float('nan')
    return math.pow(val, 0.25) - 273.15


def compute_temp_for_y(y_val: float, params: dict) -> float:
    """Compute temperature for a single Y pixel value using frame params."""
    if params is None or y_val <= 0:
        return float('nan')
    env_temp = params['env_temp']
    gain = params['gain'] if params['gain'] > 0 else 0.27
    b = gain * params['emissivity']
    return get_temp_evn(y_val, env_temp, b)


# --- Capture Thread ---

class CaptureThread(QThread):
    """Background thread for continuous frame capture from the camera."""
    frame_ready = pyqtSignal(np.ndarray, dict)  # YUYV frame, params
    error = pyqtSignal(str)

    def __init__(self, device="/dev/video2"):
        super().__init__()
        self.device = device
        self.running = False
        self.cap = None

    def run(self):
        self.cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            self.error.emit(f"Cannot open {self.device}")
            return

        self.cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
        self.running = True

        # Warmup
        for _ in range(10):
            if not self.running:
                break
            self.cap.read()

        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                self.error.emit("Frame capture failed")
                break

            raw = frame.flatten().tobytes()
            params = extract_params(raw)
            self.frame_ready.emit(frame, params or {})

        if self.cap:
            self.cap.release()

    def stop(self):
        self.running = False
        self.wait(3000)


# --- Thermal Display Widget ---

class ThermalDisplay(QLabel):
    """Widget that displays thermal image with temperature overlay."""

    clicked = pyqtSignal(int, int)  # x, y in image coordinates

    def __init__(self):
        super().__init__()
        self.setMinimumSize(384, 292)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMouseTracking(True)

        self.y_channel = None
        self.params = {}
        self.mouse_pos = None
        self.measurements = []  # List of (x, y) click points
        self.lock_min = None
        self.lock_max = None
        self.colormap_name = "JET"
        self.scale_factor = 2.0

    def update_frame(self, frame: np.ndarray, params: dict):
        """Update display with new frame data."""
        self.y_channel = frame[:, :, 0].copy()
        self.params = params

        # Build display image
        y = self.y_channel.astype(np.uint8)

        # Apply colormap
        cmap = COLORMAPS.get(self.colormap_name, cv2.COLORMAP_JET)
        if cmap == -1:
            colored = cv2.cvtColor(y, cv2.COLOR_GRAY2BGR)
        elif cmap == -2:
            colored = cv2.cvtColor(255 - y, cv2.COLOR_GRAY2BGR)
        else:
            colored = cv2.applyColorMap(y, cmap)

        # Scale up
        h, w = colored.shape[:2]
        new_w, new_h = int(w * self.scale_factor), int(h * self.scale_factor)
        colored = cv2.resize(colored, (new_w, new_h), interpolation=cv2.INTER_NEAREST)

        # Draw overlays
        self._draw_overlays(colored, params)

        # Convert to QPixmap
        rgb = cv2.cvtColor(colored, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
        self.setPixmap(QPixmap.fromImage(qimg))

    def _draw_overlays(self, img: np.ndarray, params: dict):
        """Draw temperature readings and measurement points on image."""
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = 0.5 * self.scale_factor
        thickness = max(1, int(self.scale_factor))

        # Center crosshair + temperature
        center_temp = params.get('center_temp', float('nan'))
        if not math.isnan(center_temp):
            cx = int(192 * self.scale_factor)
            cy = int(146 * self.scale_factor)
            cv2.drawMarker(img, (cx, cy), (255, 255, 255),
                          cv2.MARKER_CROSS, int(20 * self.scale_factor), thickness)
            cv2.putText(img, f"{center_temp:.1f} C", (cx + 15, cy - 10),
                       font, scale, (255, 255, 255), thickness)

        # Mouse hover temperature
        if self.mouse_pos is not None and self.y_channel is not None:
            mx, my = self.mouse_pos
            ix = int(mx / self.scale_factor)
            iy = int(my / self.scale_factor)
            if 0 <= ix < FRAME_WIDTH and 0 <= iy < FRAME_HEIGHT:
                y_val = self.y_channel[iy, ix]
                temp = compute_temp_for_y(float(y_val), params)
                if not math.isnan(temp):
                    smx, smy = int(mx), int(my)
                    cv2.circle(img, (smx, smy), int(4 * self.scale_factor), (255, 255, 255), thickness)
                    cv2.putText(img, f"{temp:.1f} C", (smx + 10, smy - 8),
                               font, scale, (255, 255, 0), thickness)

        # Measurement points (clicked)
        for i, (px, py) in enumerate(self.measurements):
            if self.y_channel is not None and 0 <= px < FRAME_WIDTH and 0 <= py < FRAME_HEIGHT:
                y_val = self.y_channel[py, px]
                temp = compute_temp_for_y(float(y_val), params)
                spx = int(px * self.scale_factor)
                spy = int(py * self.scale_factor)
                cv2.drawMarker(img, (spx, spy), (0, 255, 0),
                              cv2.MARKER_DIAMOND, int(10 * self.scale_factor), thickness)
                if not math.isnan(temp):
                    cv2.putText(img, f"P{i+1}: {temp:.1f} C", (spx + 10, spy + 15),
                               font, scale, (0, 255, 0), thickness)

        # Min/Max indicators
        if self.y_channel is not None and self.y_channel.size > 0:
            inner = self.y_channel[5:-5, 5:-5]
            if inner.size > 0:
                min_pos = np.unravel_index(inner.argmin(), inner.shape)
                max_pos = np.unravel_index(inner.argmax(), inner.shape)
                min_pos = (min_pos[0] + 5, min_pos[1] + 5)
                max_pos = (max_pos[0] + 5, max_pos[1] + 5)

                # Min marker (blue)
                smin = (int(min_pos[1] * self.scale_factor), int(min_pos[0] * self.scale_factor))
                cv2.drawMarker(img, smin, (255, 100, 100),
                              cv2.MARKER_TILTED_CROSS, int(8 * self.scale_factor), thickness)
                min_temp = compute_temp_for_y(float(self.y_channel[min_pos[0], min_pos[1]]), params)
                if not math.isnan(min_temp):
                    cv2.putText(img, f"Min: {min_temp:.1f} C", (smin[0] + 10, smin[1] + 5),
                               font, scale * 0.8, (255, 150, 150), thickness)

                # Max marker (red)
                smax = (int(max_pos[1] * self.scale_factor), int(max_pos[0] * self.scale_factor))
                cv2.drawMarker(img, smax, (100, 100, 255),
                              cv2.MARKER_TILTED_CROSS, int(8 * self.scale_factor), thickness)
                max_temp = compute_temp_for_y(float(self.y_channel[max_pos[0], max_pos[1]]), params)
                if not math.isnan(max_temp):
                    cv2.putText(img, f"Max: {max_temp:.1f} C", (smax[0] + 10, smax[1] + 5),
                               font, scale * 0.8, (150, 150, 255), thickness)

    def mouseMoveEvent(self, event):
        if self.pixmap():
            self.mouse_pos = (event.position().x(), event.position().y())

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.pixmap():
            pos = event.position()
            ix = int(pos.x() / self.scale_factor)
            iy = int(pos.y() / self.scale_factor)
            if 0 <= ix < FRAME_WIDTH and 0 <= iy < FRAME_HEIGHT:
                self.measurements.append((ix, iy))
                self.clicked.emit(ix, iy)

    def clear_measurements(self):
        self.measurements.clear()


# --- Main Window ---

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LMThermal — HT-301 Thermal Viewer")
        self.setMinimumSize(900, 650)

        self.capture_thread = None
        self.last_frame = None
        self.last_params = {}
        self.fps_counter = 0
        self.fps_time = time.time()
        self.current_fps = 0

        self._setup_ui()
        self._setup_statusbar()

        # FPS timer
        self.fps_timer = QTimer()
        self.fps_timer.timeout.connect(self._update_fps)
        self.fps_timer.start(1000)

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        # Left: thermal display
        self.display = ThermalDisplay()
        self.display.clicked.connect(self._on_point_clicked)

        # Right: controls panel
        controls = QWidget()
        controls.setFixedWidth(250)
        controls_layout = QVBoxLayout(controls)

        # Camera controls
        cam_group = QGroupBox("Camera")
        cam_layout = QVBoxLayout(cam_group)

        self.btn_start = QPushButton("▶ Start")
        self.btn_start.clicked.connect(self._toggle_capture)
        cam_layout.addWidget(self.btn_start)

        self.device_label = QLabel("Device: /dev/video2")
        cam_layout.addWidget(self.device_label)

        controls_layout.addWidget(cam_group)

        # Display controls
        disp_group = QGroupBox("Display")
        disp_layout = QFormLayout(disp_group)

        self.combo_colormap = QComboBox()
        self.combo_colormap.addItems(COLORMAPS.keys())
        self.combo_colormap.setCurrentText("JET")
        self.combo_colormap.currentTextChanged.connect(self._on_colormap_changed)
        disp_layout.addRow("Palette:", self.combo_colormap)

        self.spin_scale = QDoubleSpinBox()
        self.spin_scale.setRange(1.0, 4.0)
        self.spin_scale.setValue(2.0)
        self.spin_scale.setSingleStep(0.5)
        self.spin_scale.valueChanged.connect(self._on_scale_changed)
        disp_layout.addRow("Zoom:", self.spin_scale)

        self.check_minmax = QCheckBox("Show Min/Max")
        self.check_minmax.setChecked(True)
        disp_layout.addRow(self.check_minmax)

        controls_layout.addWidget(disp_group)

        # Temperature lock
        lock_group = QGroupBox("Temperature Lock")
        lock_layout = QFormLayout(lock_group)

        self.check_lock = QCheckBox("Lock range")
        lock_layout.addRow(self.check_lock)

        self.spin_min = QDoubleSpinBox()
        self.spin_min.setRange(-40, 500)
        self.spin_min.setValue(0)
        self.spin_min.setSuffix(" °C")
        lock_layout.addRow("Min:", self.spin_min)

        self.spin_max = QDoubleSpinBox()
        self.spin_max.setRange(-40, 500)
        self.spin_max.setValue(50)
        self.spin_max.setSuffix(" °C")
        lock_layout.addRow("Max:", self.spin_max)

        controls_layout.addWidget(lock_group)

        # Measurements
        meas_group = QGroupBox("Measurements")
        meas_layout = QVBoxLayout(meas_group)

        self.btn_clear_meas = QPushButton("Clear points")
        self.btn_clear_meas.clicked.connect(self.display.clear_measurements)
        meas_layout.addWidget(self.btn_clear_meas)

        self.lbl_meas = QLabel("Click on image to measure")
        self.lbl_meas.setWordWrap(True)
        meas_layout.addWidget(self.lbl_meas)

        controls_layout.addWidget(meas_group)

        # Capture
        capt_group = QGroupBox("Capture")
        capt_layout = QVBoxLayout(capt_group)

        self.btn_capture = QPushButton("📷 Save Image")
        self.btn_capture.clicked.connect(self._save_image)
        capt_layout.addWidget(self.btn_capture)

        controls_layout.addWidget(capt_group)

        controls_layout.addStretch()

        # Info panel
        info_group = QGroupBox("Frame Info")
        info_layout = QFormLayout(info_group)
        self.lbl_env_temp = QLabel("--")
        self.lbl_emissivity = QLabel("--")
        self.lbl_gain = QLabel("--")
        self.lbl_center = QLabel("--")
        info_layout.addRow("Env temp:", self.lbl_env_temp)
        info_layout.addRow("Emissivity:", self.lbl_emissivity)
        info_layout.addRow("Gain:", self.lbl_gain)
        info_layout.addRow("Center:", self.lbl_center)
        controls_layout.addWidget(info_group)

        # Assemble
        main_layout.addWidget(self.display, stretch=1)
        main_layout.addWidget(controls)

    def _setup_statusbar(self):
        self.status_fps = QLabel("FPS: --")
        self.statusBar().addPermanentWidget(self.status_fps)

    def _toggle_capture(self):
        if self.capture_thread and self.capture_thread.isRunning():
            self._stop_capture()
        else:
            self._start_capture()

    def _start_capture(self):
        self.capture_thread = CaptureThread("/dev/video2")
        self.capture_thread.frame_ready.connect(self._on_frame)
        self.capture_thread.error.connect(self._on_error)
        self.capture_thread.start()
        self.btn_start.setText("⏹ Stop")
        self.statusBar().showMessage("Capturing...")

    def _stop_capture(self):
        if self.capture_thread:
            self.capture_thread.stop()
            self.capture_thread = None
        self.btn_start.setText("▶ Start")
        self.statusBar().showMessage("Stopped")

    def _on_frame(self, frame: np.ndarray, params: dict):
        self.last_frame = frame.copy()
        self.last_params = params

        # Update display
        self.display.update_frame(frame, params)

        # Update info panel
        self.lbl_env_temp.setText(f"{params.get('env_temp', 0):.1f} °C")
        self.lbl_emissivity.setText(f"{params.get('emissivity', 0):.3f}")
        self.lbl_gain.setText(f"{params.get('gain', 0):.4f}")
        ct = params.get('center_temp', float('nan'))
        self.lbl_center.setText(f"{ct:.1f} °C" if not math.isnan(ct) else "--")

        # FPS counting
        self.fps_counter += 1

    def _on_error(self, msg: str):
        self.statusBar().showMessage(f"Error: {msg}")
        self._stop_capture()
        QMessageBox.warning(self, "Capture Error", msg)

    def _on_colormap_changed(self, name: str):
        self.display.colormap_name = name

    def _on_scale_changed(self, val: float):
        self.display.scale_factor = val

    def _on_point_clicked(self, x: int, y: int):
        if self.last_params:
            y_val = self.last_frame[y, x, 0] if self.last_frame is not None else 0
            temp = compute_temp_for_y(float(y_val), self.last_params)
            n = len(self.display.measurements)
            self.lbl_meas.setText(f"P{n}: ({x},{y}) = {temp:.1f} °C")

    def _update_fps(self):
        elapsed = time.time() - self.fps_time
        if elapsed > 0:
            self.current_fps = self.fps_counter / elapsed
            self.status_fps.setText(f"FPS: {self.current_fps:.1f}")
        self.fps_counter = 0
        self.fps_time = time.time()

    def _save_image(self):
        if self.last_frame is None:
            QMessageBox.information(self, "No Frame", "Start capture first.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Save Thermal Image", "thermal_capture.png",
            "PNG (*.png);;JPEG (*.jpg);;All Files (*)"
        )
        if path:
            # Save the current display pixmap
            pixmap = self.display.pixmap()
            if pixmap:
                pixmap.save(path)
                self.statusBar().showMessage(f"Saved: {path}", 3000)

    def closeEvent(self, event):
        self._stop_capture()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
