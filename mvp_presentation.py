"""Native-grid presentation helpers; live HT-301 geometry is the legacy default."""

from dataclasses import dataclass
from offline_measurement import OfflineMeasurement, Geometry, Transform
import math

from radiometric_capture import OfflineCapture
from radiometric_playback import PlaybackFrame
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
    raw14: int | None
    native_equivalent_c: float


def image_viewport(widget_width: int, widget_height: int, *, geometry=None, transform=None) -> ImageViewport:
    """Fit the image without changing its native orientation or aspect ratio."""
    if widget_width <= 0 or widget_height <= 0:
        raise ValueError("Widget dimensions must be positive")
    geometry = geometry or Geometry(FRAME_WIDTH, IMAGE_HEIGHT)
    dw, dh = (transform or Transform()).dimensions(geometry)
    scale = min(widget_width / dw, widget_height / dh)
    width, height = dw * scale, dh * scale
    return ImageViewport((widget_width - width) / 2, (widget_height - height) / 2,
                         width, height)


def widget_to_native(widget_x: float, widget_y: float,
                     widget_width: int, widget_height: int, *,
                     clip: bool = False, geometry=None, transform=None) -> tuple[int, int] | None:
    """Return a native pixel; optional clipping is for an already-started drag."""
    geometry = geometry or Geometry(FRAME_WIDTH, IMAGE_HEIGHT)
    transform = transform or Transform()
    area = image_viewport(widget_width, widget_height, geometry=geometry, transform=transform)
    if not (math.isfinite(widget_x) and math.isfinite(widget_y)):
        return None
    if not clip and not (area.left <= widget_x < area.left + area.width and
                         area.top <= widget_y < area.top + area.height):
        return None
    dw, dh = transform.dimensions(geometry)
    x, y = transform.inverse((widget_x-area.left)*dw/area.width,
                             (widget_y-area.top)*dh/area.height, geometry)
    return max(0, min(math.floor(x), geometry.width-1)), max(0, min(math.floor(y), geometry.height-1))


def native_edge_to_widget(x: float, y: float, widget_width: int,
                          widget_height: int, *, geometry=None, transform=None) -> tuple[float, float]:
    """Map native pixel boundaries, including right/bottom image edges."""
    geometry = geometry or Geometry(FRAME_WIDTH, IMAGE_HEIGHT)
    if not (0 <= x <= geometry.width and 0 <= y <= geometry.height):
        raise ValueError("Native edge is outside the thermal image")
    transform = transform or Transform()
    area = image_viewport(widget_width, widget_height, geometry=geometry, transform=transform)
    x, y = transform.forward(x, y, geometry)
    dw, dh = transform.dimensions(geometry)
    return area.left+x*area.width/dw, area.top+y*area.height/dh


def native_to_widget(x: int, y: int, widget_width: int,
                     widget_height: int, *, geometry=None, transform=None) -> tuple[float, float]:
    """Locate a native pixel center inside the current letterboxed view."""
    geometry = geometry or Geometry(FRAME_WIDTH, IMAGE_HEIGHT)
    if not (0 <= x < geometry.width and 0 <= y < geometry.height):
        raise ValueError("Native coordinate is outside the thermal image")
    return native_edge_to_widget(x + .5, y + .5, widget_width, widget_height, geometry=geometry, transform=transform)


def current_measurement(observation):
    """Distinguish verified offline matrices from a currently ready live frame."""
    if isinstance(observation, OfflineMeasurement):
        return observation if observation.has_readings else None
    if isinstance(observation, (OfflineCapture, PlaybackFrame)):
        return observation
    if (observation is None or observation.state != SessionState.RADIOMETRIC_READY or
            observation.measurement is None or
            observation.measurement.state != SessionState.RADIOMETRIC_READY):
        return None
    return observation.measurement


def current_reading(observation, point: tuple[int, int] | None) -> PixelReading | None:
    """Read the stored native matrix, independently of display normalization."""
    measurement = current_measurement(observation)
    if measurement is None or point is None:
        return None
    x, y = point
    if isinstance(measurement, OfflineMeasurement):
        value = measurement.point(x, y)
        if value is None: return None
        raw = measurement.raw14
        return PixelReading(x, y, int(raw[y, x]) if raw is not None else None, value)
    if not (0 <= x < FRAME_WIDTH and 0 <= y < IMAGE_HEIGHT):
        return None
    return PixelReading(x, y, int(measurement.raw14[y, x]),
                        float(measurement.temperature_c[y, x]))


def current_extrema(observation):
    """Expose saved or validated live extrema, never display brightness extrema."""
    measurement = current_measurement(observation)
    if measurement is None:
        return None
    return ((measurement.high_xy, measurement.high_c),
            (measurement.low_xy, measurement.low_c))


def display_image(observation: FrameObservation):
    """Produce a visualization independent of the raw14-to-LUT measurement path."""
    return preview_gray(observation.raw, observation.inspection.mode)
