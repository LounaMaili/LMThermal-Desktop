"""Lightweight OpenCV operator view; no normalized pixel is used for thermometry."""

import os

import cv2
import numpy as np

from measurement_baseline import FRAME_BYTES, FRAME_WIDTH, IMAGE_BYTES, IMAGE_HEIGHT, parse_frame
from radiometric_session import FrameObservation, SessionState


class PreviewClosed(Exception):
    """The operator closed the diagnostic preview with q or Escape."""


def normalize_raw14(words: np.ndarray) -> np.ndarray:
    """Map raw14 contrast to display-only 8-bit values without changing indices."""
    if words.shape != (IMAGE_HEIGHT, FRAME_WIDTH) or words.dtype != np.uint16:
        raise ValueError("Expected a 288 by 384 uint16 raw14 image")
    if np.any(words >= 0x4000):
        raise ValueError("Preview cannot normalize out-of-range raw14 words")
    low, high = np.percentile(words, (1, 99))
    if high <= low:
        return np.full(words.shape, 127, dtype=np.uint8)
    return np.rint(np.clip((words.astype(np.float32) - low) * (255.0 / (high - low)), 0, 255)).astype(np.uint8)


def preview_gray(raw: bytes, mode: str) -> np.ndarray:
    """Use transport Y in display mode and independent contrast in raw14 mode."""
    if len(raw) != FRAME_BYTES:
        return np.zeros((IMAGE_HEIGHT, FRAME_WIDTH), dtype=np.uint8)
    parsed = parse_frame(raw)
    if mode == "display":
        return parsed.image_y.copy()
    if mode == "raw14":
        words = np.frombuffer(raw, dtype="<u2", count=IMAGE_BYTES // 2).reshape(IMAGE_HEIGHT, FRAME_WIDTH)
        return normalize_raw14(words)
    return parsed.image_y.copy()


def validate_rois(rois: list[tuple[int, int, int, int]]) -> None:
    """Keep optional operator boxes within the known image, never the trailer."""
    for x, y, width, height in rois:
        if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > FRAME_WIDTH or y + height > IMAGE_HEIGHT:
            raise ValueError("ROI must fit inside the 384 by 288 image")


def render_overlay(observation: FrameObservation, rois=(), pointer=None) -> np.ndarray:
    """Draw operator annotations on a new BGR image, never on raw bytes."""
    validate_rois(list(rois))
    gray = preview_gray(observation.raw, observation.inspection.mode)
    canvas = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    cv2.drawMarker(canvas, (192, 144), (0, 255, 255), cv2.MARKER_CROSS, 13, 1)
    for x, y, width, height in rois:
        cv2.rectangle(canvas, (x, y), (x + width - 1, y + height - 1), (255, 200, 0), 1)
    status = f"{observation.state.value} | {observation.inspection.mode} | ready={observation.measurement is not None}"
    cv2.rectangle(canvas, (0, 0), (FRAME_WIDTH - 1, 43), (0, 0, 0), -1)
    cv2.putText(canvas, status, (4, 13), cv2.FONT_HERSHEY_SIMPLEX, .37, (255, 255, 255), 1)
    word_range = f"words {observation.inspection.word_min}..{observation.inspection.word_max}"
    cv2.putText(canvas, word_range, (4, 29), cv2.FONT_HERSHEY_SIMPLEX, .37, (255, 255, 255), 1)
    if observation.rejection or observation.repeated_image:
        reason = observation.rejection or "held image"
        prefix = ("UNREADY/TRANSIENT" if reason in
                  ("host_range_unverified", "awaiting_live_evidence", "shutter_settling")
                  else "INVALID/TRANSIENT")
        cv2.putText(canvas, f"{prefix}: {reason}", (4, 42),
                    cv2.FONT_HERSHEY_SIMPLEX, .36, (0, 0, 255), 1)
    if observation.measurement:
        measured = observation.measurement
        cv2.rectangle(canvas, (0, IMAGE_HEIGHT - 43), (FRAME_WIDTH - 1, IMAGE_HEIGHT - 1), (0, 0, 0), -1)
        cv2.putText(canvas, f"native-equivalent C/H/L {measured.trailer_center_c:.2f}/{measured.high_c:.2f}/{measured.low_c:.2f} C",
                    (4, IMAGE_HEIGHT - 27), cv2.FONT_HERSHEY_SIMPLEX, .36, (255, 255, 255), 1)
        cv2.putText(canvas, f"pixel center {measured.literal_center_c:.2f} C | physical accuracy unvalidated",
                    (4, IMAGE_HEIGHT - 11), cv2.FONT_HERSHEY_SIMPLEX, .35, (255, 255, 255), 1)
        cv2.drawMarker(canvas, measured.high_xy, (0, 0, 255), cv2.MARKER_TILTED_CROSS, 11, 1)
        cv2.drawMarker(canvas, measured.low_xy, (255, 0, 0), cv2.MARKER_TILTED_CROSS, 11, 1)
    if pointer and 0 <= pointer[0] < FRAME_WIDTH and 0 <= pointer[1] < IMAGE_HEIGHT:
        cv2.circle(canvas, pointer, 2, (0, 255, 0), 1)
    return canvas


class DiagnosticPreview:
    """Keep the live window responsive while the session reads exact frames."""

    def __init__(self, rois=(), title="HT-301 diagnostic preview"):
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            raise RuntimeError("OpenCV preview needs a graphical desktop session")
        self.rois = tuple(rois)
        validate_rois(list(self.rois))
        self.title = title
        self.latest: FrameObservation | None = None
        self.pointer = None
        self.last_key = -1
        cv2.namedWindow(self.title, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(self.title, self._mouse)

    def _mouse(self, event, x, y, flags, userdata) -> None:
        """Report actual raw indices and lookup values only on valid raw14 clicks."""
        self.pointer = (x, y)
        if event != cv2.EVENT_LBUTTONDOWN or self.latest is None:
            return
        if not (0 <= x < FRAME_WIDTH and 0 <= y < IMAGE_HEIGHT):
            return
        observation = self.latest
        if observation.measurement:
            measurement = observation.measurement
            print(f"pixel ({x},{y}) raw14={int(measurement.raw14[y, x])} "
                  f"native-equivalent={float(measurement.temperature_c[y, x]):.4f} C "
                  "(absolute accuracy unvalidated)", flush=True)
        elif observation.inspection.mode == "display":
            print(f"pixel ({x},{y}) display Y={int(parse_frame(observation.raw).image_y[y, x])}; "
                  "no validated temperature", flush=True)
        else:
            print(f"pixel ({x},{y}) frame is not measurement-ready", flush=True)

    def show(self, observation: FrameObservation) -> None:
        """Render one observation and allow q/Escape to abort at any stage."""
        self.latest = observation
        cv2.imshow(self.title, render_overlay(observation, self.rois, self.pointer))
        self.last_key = cv2.waitKey(1) & 0xFF
        if self.last_key in (ord("q"), 27):
            raise PreviewClosed()

    def close(self) -> None:
        """Release only this diagnostic window."""
        cv2.destroyWindow(self.title)
