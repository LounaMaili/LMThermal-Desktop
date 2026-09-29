"""Display-only Celsius palette rendering; never an input to thermometry."""

from dataclasses import dataclass
import math

import cv2
import numpy as np

from measurement_baseline import FRAME_WIDTH, IMAGE_HEIGHT


PALETTES = {
    "White hot": None,
    "Black hot": None,
    "Inferno": cv2.COLORMAP_INFERNO,
    "Iron-like": cv2.COLORMAP_HOT,
    "Turbo": cv2.COLORMAP_TURBO,
}
DEFAULT_PALETTE = "Inferno"


@dataclass(frozen=True)
class CelsiusRange:
    """The effective display scale, separate from measured extrema."""

    lower: float
    upper: float

    def __post_init__(self) -> None:
        if not (math.isfinite(self.lower) and math.isfinite(self.upper)) or self.lower >= self.upper:
            raise ValueError("Celsius display range requires finite minimum < maximum")


def _matrix(temperature_c: np.ndarray) -> np.ndarray:
    """Accept only the native image-area matrix with finite temperature values."""
    matrix = np.asarray(temperature_c)
    if matrix.shape != (IMAGE_HEIGHT, FRAME_WIDTH) or not np.issubdtype(matrix.dtype, np.floating):
        raise ValueError("Expected a 288 by 384 floating Celsius matrix")
    if not np.isfinite(matrix).all():
        raise ValueError("Temperature matrix contains non-finite values")
    return matrix


def auto_range(temperature_c: np.ndarray) -> CelsiusRange:
    """Clip 2nd/98th percentile for display; retain at least a 1 °C span."""
    matrix = _matrix(temperature_c)
    lower, upper = map(float, np.percentile(matrix, (2, 98)))
    if upper - lower < 1.0:
        middle = (lower + upper) / 2
        lower, upper = middle - .5, middle + .5
    return CelsiusRange(lower, upper)


def effective_range(temperature_c: np.ndarray, automatic: bool,
                    locked_lower: float, locked_upper: float) -> CelsiusRange:
    """Use only Celsius matrix values in auto mode and exact bounds in lock mode."""
    _matrix(temperature_c)
    return auto_range(temperature_c) if automatic else CelsiusRange(locked_lower, locked_upper)


def normalized_levels(temperature_c: np.ndarray, lower: float, upper: float) -> np.ndarray:
    """Clamp Celsius values to palette indices without mutating source data."""
    matrix = _matrix(temperature_c)
    bounds = CelsiusRange(lower, upper)
    scale = 255.0 / (bounds.upper - bounds.lower)
    return np.rint(np.clip((matrix.astype(np.float64) - bounds.lower) * scale,
                           0, 255)).astype(np.uint8)


def palette_rgb(levels: np.ndarray, palette: str) -> np.ndarray:
    """Color any 8-bit level grid using the same mapping as image and legend."""
    if palette not in PALETTES:
        raise ValueError(f"Unsupported Celsius palette: {palette}")
    if levels.dtype != np.uint8 or levels.ndim != 2:
        raise ValueError("Palette levels must be a two-dimensional uint8 grid")
    if palette == "White hot":
        return np.repeat(levels[:, :, None], 3, axis=2)
    if palette == "Black hot":
        return np.repeat((255 - levels)[:, :, None], 3, axis=2)
    bgr = cv2.applyColorMap(levels, PALETTES[palette])
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def render_temperature(temperature_c: np.ndarray, lower: float,
                       upper: float, palette: str) -> np.ndarray:
    """Render a native Celsius matrix to RGB for display only."""
    return palette_rgb(normalized_levels(temperature_c, lower, upper), palette)


def legend_image(lower: float, upper: float, palette: str,
                 height: int = 256, width: int = 18) -> np.ndarray:
    """Render hot-at-top scale with exactly the image's Celsius color mapping."""
    CelsiusRange(lower, upper)
    if height < 2 or width < 1:
        raise ValueError("Legend needs at least two rows and one column")
    values = np.linspace(upper, lower, height, dtype=np.float64)[:, None]
    levels = np.rint(np.clip((values - lower) * (255.0 / (upper - lower)),
                             0, 255)).astype(np.uint8)
    return palette_rgb(np.repeat(levels, width, axis=1), palette)
