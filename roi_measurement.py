"""Native rectangular geometry and unsmoothed current-frame Celsius statistics."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from measurement_baseline import FRAME_WIDTH, IMAGE_HEIGHT
if TYPE_CHECKING:
    from radiometric_capture import OfflineCapture
    from radiometric_session import FrameObservation


@dataclass(frozen=True)
class NativeROI:
    """Nonempty half-open rectangle: temperature_c[y1:y2, x1:x2]."""

    x1: int
    y1: int
    x2: int
    y2: int

    def __post_init__(self) -> None:
        if (not all(type(value) is int for value in (self.x1, self.y1, self.x2, self.y2)) or
                not (0 <= self.x1 < self.x2 <= FRAME_WIDTH and
                     0 <= self.y1 < self.y2 <= IMAGE_HEIGHT)):
            raise ValueError("ROI must be a nonempty native half-open rectangle")

    @property
    def pixel_count(self) -> int:
        return (self.x2 - self.x1) * (self.y2 - self.y1)


@dataclass(frozen=True)
class ROIStatistics:
    """Direct matrix extrema and float64 mean; ties use first row-major pixel."""

    min_c: float
    max_c: float
    mean_c: float
    pixel_count: int
    min_xy: tuple[int, int]
    max_xy: tuple[int, int]

    def metadata(self) -> dict:
        return {"min_c": self.min_c, "max_c": self.max_c, "mean_c": self.mean_c,
                "pixel_count": self.pixel_count,
                "min_xy_px": list(self.min_xy), "max_xy_px": list(self.max_xy)}


def roi_from_native_pixels(start: tuple[int, int], end: tuple[int, int]) -> NativeROI:
    """Normalize either drag direction, clip endpoints, and include both pixels."""
    if not all(type(value) is int for point in (start, end) for value in point):
        raise ValueError("Drag endpoints must be integer native pixels")
    x1, x2 = sorted(max(0, min(x, FRAME_WIDTH - 1)) for x, _ in (start, end))
    y1, y2 = sorted(max(0, min(y, IMAGE_HEIGHT - 1)) for _, y in (start, end))
    return NativeROI(x1, y1, x2 + 1, y2 + 1)


def roi_from_widget_drag(start: tuple[float, float], end: tuple[float, float],
                         widget_width: int, widget_height: int) -> NativeROI | None:
    """A drag must start inside the image; an outside endpoint clips to its edge."""
    from mvp_presentation import widget_to_native

    first = widget_to_native(*start, widget_width, widget_height)
    last = widget_to_native(*end, widget_width, widget_height, clip=True)
    if first is None or last is None:
        return None
    return roi_from_native_pixels(first, last)


def roi_statistics(temperature_c: np.ndarray, roi: NativeROI) -> ROIStatistics:
    """Read the exact native matrix slice without smoothing or display scaling."""
    matrix = np.asarray(temperature_c)
    if matrix.shape != (IMAGE_HEIGHT, FRAME_WIDTH) or not np.issubdtype(matrix.dtype, np.floating):
        raise ValueError("Expected a native 288 by 384 floating Celsius matrix")
    region = matrix[roi.y1:roi.y2, roi.x1:roi.x2]
    if not np.isfinite(region).all():
        raise ValueError("ROI contains undefined temperature values")
    min_y, min_x = np.unravel_index(int(region.argmin()), region.shape)
    max_y, max_x = np.unravel_index(int(region.argmax()), region.shape)
    return ROIStatistics(float(region.min()), float(region.max()),
                         float(region.mean(dtype=np.float64)), roi.pixel_count,
                         (roi.x1 + int(min_x), roi.y1 + int(min_y)),
                         (roi.x1 + int(max_x), roi.y1 + int(max_y)))


def current_roi_statistics(observation: "FrameObservation | OfflineCapture | None",
                           roi: NativeROI | None) -> ROIStatistics | None:
    """Use a saved matrix or a currently ready live measurement, never display pixels."""
    from mvp_presentation import current_measurement
    from offline_measurement import OfflineMeasurement, Rectangle
    if isinstance(observation, OfflineMeasurement):
        if roi is None: return None
        return observation.roi_statistics(Rectangle(roi.x1, roi.y1, roi.x2, roi.y2))

    measurement = current_measurement(observation)
    if roi is None or measurement is None:
        return None
    return roi_statistics(measurement.temperature_c, roi)
