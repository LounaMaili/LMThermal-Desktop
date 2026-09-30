"""Pure presentation helpers for the native 384 by 288 camera coordinate system."""

from dataclasses import dataclass
import math

from diagnostic_preview import preview_gray
from measurement_baseline import FRAME_WIDTH, IMAGE_HEIGHT
from radiometric_session import FrameObservation, SessionState


@dataclass(frozen=True)
class ImageViewport:
    """The centered image rectangle after aspect-preserving widget scaling."""

    left: float
    top: float
    width: float
    height: float


@dataclass(frozen=True)
class PixelReading:
    """A native pixel reading copied from a verified measurement frame."""

    x: int
    y: int
    raw14: int
    native_equivalent_c: float


def image_viewport(widget_width: int, widget_height: int) -> ImageViewport:
    """Fit the image without changing its native orientation or aspect ratio."""
    if widget_width <= 0 or widget_height <= 0:
        raise ValueError("Widget dimensions must be positive")
    scale = min(widget_width / FRAME_WIDTH, widget_height / IMAGE_HEIGHT)
    width, height = FRAME_WIDTH * scale, IMAGE_HEIGHT * scale
    return ImageViewport((widget_width - width) / 2, (widget_height - height) / 2,
                         width, height)


def widget_to_native(widget_x: float, widget_y: float,
                     widget_width: int, widget_height: int, *,
                     clip: bool = False) -> tuple[int, int] | None:
    """Return a native pixel; optional clipping is for an already-started drag."""
    area = image_viewport(widget_width, widget_height)
    if not (math.isfinite(widget_x) and math.isfinite(widget_y)):
        return None
    if not clip and not (area.left <= widget_x < area.left + area.width and
                         area.top <= widget_y < area.top + area.height):
        return None
    x = math.floor((widget_x - area.left) * FRAME_WIDTH / area.width)
    y = math.floor((widget_y - area.top) * IMAGE_HEIGHT / area.height)
    return max(0, min(x, FRAME_WIDTH - 1)), max(0, min(y, IMAGE_HEIGHT - 1))


def native_edge_to_widget(x: float, y: float, widget_width: int,
                          widget_height: int) -> tuple[float, float]:
    """Map native pixel boundaries, including right/bottom image edges."""
    if not (0 <= x <= FRAME_WIDTH and 0 <= y <= IMAGE_HEIGHT):
        raise ValueError("Native edge is outside the thermal image")
    area = image_viewport(widget_width, widget_height)
    return (area.left + x * area.width / FRAME_WIDTH,
            area.top + y * area.height / IMAGE_HEIGHT)


def native_to_widget(x: int, y: int, widget_width: int,
                     widget_height: int) -> tuple[float, float]:
    """Locate a native pixel center inside the current letterboxed view."""
    if not (0 <= x < FRAME_WIDTH and 0 <= y < IMAGE_HEIGHT):
        raise ValueError("Native coordinate is outside the thermal image")
    return native_edge_to_widget(x + .5, y + .5, widget_width, widget_height)


def current_reading(observation: FrameObservation | None,
                    point: tuple[int, int] | None) -> PixelReading | None:
    """Read only the immutable native matrix of a currently ready frame."""
    if observation is None or observation.state != SessionState.RADIOMETRIC_READY:
        return None
    measurement = observation.measurement
    if measurement is None or point is None:
        return None
    x, y = point
    if not (0 <= x < FRAME_WIDTH and 0 <= y < IMAGE_HEIGHT):
        return None
    return PixelReading(x, y, int(measurement.raw14[y, x]),
                        float(measurement.temperature_c[y, x]))


def current_extrema(observation: FrameObservation | None):
    """Expose validated trailer/matrix extrema, never display brightness extrema."""
    if observation is None or observation.state != SessionState.RADIOMETRIC_READY:
        return None
    measurement = observation.measurement
    if measurement is None:
        return None
    return ((measurement.high_xy, measurement.high_c),
            (measurement.low_xy, measurement.low_c))


def display_image(observation: FrameObservation):
    """Produce a visualization independent of the raw14-to-LUT measurement path."""
    return preview_gray(observation.raw, observation.inspection.mode)
