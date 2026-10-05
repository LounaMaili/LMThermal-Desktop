"""Owned camera-independent source data and deterministic native-grid analysis.

This module never imports acquisition, camera discovery, or thermometry code.
Byte-backed arrays cannot be made writable even by setting NumPy flags.
"""

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
import copy
import os
import math
import numpy as np

from celsius_palette import CelsiusRange, palette_rgb


PALETTE_IDS = {'white_hot': 'White hot', 'black_hot': 'Black hot',
               'inferno': 'Inferno', 'iron_like': 'Iron-like', 'turbo': 'Turbo'}


def freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    return value


def thaw(value):
    if isinstance(value, MappingProxyType):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [thaw(v) for v in value]
    return copy.deepcopy(value)


@dataclass(frozen=True)
class Geometry:
    width: int
    height: int

    def __post_init__(self):
        if type(self.width) is not int or type(self.height) is not int or min(self.width, self.height) < 1:
            raise ValueError('Geometry requires positive integer dimensions')

    @property
    def shape(self): return self.height, self.width
    @property
    def pixel_count(self): return self.width * self.height


@dataclass(frozen=True)
class Rectangle:
    x1: int
    y1: int
    x2: int
    y2: int

    def validate(self, geometry):
        if (not all(type(v) is int for v in (self.x1, self.y1, self.x2, self.y2)) or
                not 0 <= self.x1 < self.x2 <= geometry.width or
                not 0 <= self.y1 < self.y2 <= geometry.height):
            raise ValueError('Rectangle must be nonempty, half-open, and in the native grid')
        return self

    @property
    def pixel_count(self): return (self.x2-self.x1)*(self.y2-self.y1)


@dataclass(frozen=True)
class Statistics:
    pixel_count: int
    valid_pixel_count: int
    min_c: float | None = None
    max_c: float | None = None
    mean_c: float | None = None
    min_xy: tuple | None = None
    max_xy: tuple | None = None


def statistics(matrix, mask=None, roi=None, *, cancelled=lambda: False):
    """First row-major ties and sequential binary64 addition, never pairwise sum.

    NumPy flat slices copy only one bounded block from a strided ROI. Each
    accumulate starts with the previous accumulator, preserving operation order
    across blocks and rows, including very tall/narrow non-camera geometries.
    """
    geometry = Geometry(matrix.shape[1], matrix.shape[0])
    roi = roi or Rectangle(0, 0, geometry.width, geometry.height)
    roi.validate(geometry)
    region = matrix[roi.y1:roi.y2, roi.x1:roi.x2]
    validity = None if mask is None else mask[roi.y1:roi.y2, roi.x1:roi.x2]
    width = roi.x2-roi.x1
    count = 0
    total = 0.0
    minimum = maximum = min_xy = max_xy = None
    for start in range(0, roi.pixel_count, 8192):
        if cancelled():
            raise InterruptedError('Load cancelled')
        values = region.flat[start:start+8192]
        locations = None
        if validity is not None:
            locations = np.flatnonzero(validity.flat[start:start+8192] == 1)
            values = values[locations]
        if not values.size:
            continue
        imin, imax = int(values.argmin()), int(values.argmax())
        lo, hi = float(values[imin]), float(values[imax])
        if minimum is None or lo < minimum:
            index = start+(imin if locations is None else int(locations[imin]))
            minimum, min_xy = lo, (roi.x1+index % width, roi.y1+index//width)
        if maximum is None or hi > maximum:
            index = start+(imax if locations is None else int(locations[imax]))
            maximum, max_xy = hi, (roi.x1+index % width, roi.y1+index//width)
        buffer = np.empty(values.size+1, dtype=np.float64)
        buffer[0], buffer[1:] = total, values
        np.add.accumulate(buffer, out=buffer)
        total = float(buffer[-1])
        count += int(values.size)
    return Statistics(roi.pixel_count, count, minimum, maximum,
                      total/count if count else None, min_xy, max_xy)


@dataclass(frozen=True)
class NativePlane:
    id: str
    encoding: str
    dtype: str
    shape: tuple
    data: bytes

    def __post_init__(self):
        object.__setattr__(self, 'data', bytes(self.data))
        object.__setattr__(self, 'shape', tuple(self.shape))

    @property
    def array(self): return np.frombuffer(self.data, dtype=self.dtype).reshape(self.shape)


@dataclass(frozen=True)
class Transform:
    rotation: int = 0
    mirror_x: bool = False
    mirror_y: bool = False

    def dimensions(self, geometry):
        return (geometry.height, geometry.width) if self.rotation in (90, 270) else (geometry.width, geometry.height)

    def forward(self, x, y, geometry):
        """Map pixel edges (centers use +0.5) clockwise, then mirror."""
        w, h = geometry.width, geometry.height
        if self.rotation == 90: x, y = h-y, x
        elif self.rotation == 180: x, y = w-x, h-y
        elif self.rotation == 270: x, y = y, w-x
        dw, dh = self.dimensions(geometry)
        return (dw-x if self.mirror_x else x, dh-y if self.mirror_y else y)

    def inverse(self, x, y, geometry):
        dw, dh = self.dimensions(geometry)
        if self.mirror_x: x = dw-x
        if self.mirror_y: y = dh-y
        w, h = geometry.width, geometry.height
        if self.rotation == 90: return y, h-x
        if self.rotation == 180: return w-x, h-y
        if self.rotation == 270: return w-y, x
        return x, y

    def image(self, rgb):
        result = np.rot90(rgb, -(self.rotation//90))
        if self.mirror_x: result = result[:, ::-1]
        if self.mirror_y: result = result[::-1]
        return np.ascontiguousarray(result)


@dataclass(frozen=True)
class OfflineMeasurement:
    source_path: Path
    format_id: str
    geometry: Geometry
    temperature_bytes: bytes | None
    mask_bytes: bytes | None
    native_planes: tuple[NativePlane, ...]
    manifest: object
    evidence: object = field(default_factory=lambda: freeze({}))
    payload_bytes: object = field(default_factory=lambda: freeze({}))
    preview_bytes: bytes | None = None  # Decoded RGB, primary native geometry only.
    archive_sha256: str | None = None
    stats: Statistics | None = None
    roi: Rectangle | None = None
    original_palette: str = 'White hot'
    original_palette_id: str = 'white_hot'
    palette_fallback: bool = False
    original_bounds: CelsiusRange | None = None
    automatic_range: bool = True
    transform: Transform = Transform()
    protected_paths: tuple[Path, ...] = ()
    reported_center: tuple[int, float] | None = None

    def __post_init__(self):
        object.__setattr__(self, 'manifest', freeze(thaw(self.manifest)))
        object.__setattr__(self, 'evidence', freeze(thaw(self.evidence)))
        object.__setattr__(self, 'payload_bytes', freeze(thaw(self.payload_bytes)))
        if self.temperature_bytes is not None:
            object.__setattr__(self, 'temperature_bytes', bytes(self.temperature_bytes))
        if self.mask_bytes is not None:
            object.__setattr__(self, 'mask_bytes', bytes(self.mask_bytes))
        if self.preview_bytes is not None:
            object.__setattr__(self, 'preview_bytes', bytes(self.preview_bytes))

    @property
    def temperature_c(self):
        return None if self.temperature_bytes is None else np.frombuffer(self.temperature_bytes, dtype='<f4').reshape(self.geometry.shape)

    @property
    def validity_mask(self):
        return None if self.mask_bytes is None else np.frombuffer(self.mask_bytes, dtype='u1').reshape(self.geometry.shape)

    @property
    def raw14(self):
        plane = next((p for p in self.native_planes if p.encoding == 'ht301.raw14'), None)
        return None if plane is None else plane.array

    @property
    def metadata(self):
        return thaw(self.manifest['legacy_metadata']) if 'legacy_metadata' in self.manifest else thaw(self.manifest)

    @property
    def diagnostic_metadata(self):
        """Expose original metadata and parsed evidence without mutable aliases."""
        return {'manifest': self.metadata, 'extension_evidence': thaw(self.evidence)}

    @property
    def literal_center_c(self): return self.point(self.geometry.width//2, self.geometry.height//2)
    @property
    def literal_center_index(self):
        raw = self.raw14
        return None if raw is None else int(raw[self.geometry.height//2, self.geometry.width//2])
    @property
    def trailer_center_index(self): return None if self.reported_center is None else self.reported_center[0]
    @property
    def trailer_center_c(self): return None if self.reported_center is None else self.reported_center[1]
    @property
    def high_xy(self): return self.stats.max_xy if self.stats else None
    @property
    def low_xy(self): return self.stats.min_xy if self.stats else None
    @property
    def high_c(self): return self.stats.max_c if self.stats else None
    @property
    def low_c(self): return self.stats.min_c if self.stats else None
    @property
    def provenance(self): return self.manifest.get('measurement', {}).get('provenance', {})
    @property
    def accuracy_warning(self): return self.provenance.get('warning', '')
    @property
    def has_readings(self): return self.stats is not None and self.stats.valid_pixel_count > 0

    def point(self, x, y):
        if not 0 <= x < self.geometry.width or not 0 <= y < self.geometry.height:
            return None
        if self.temperature_bytes is None or (self.mask_bytes is not None and self.validity_mask[y, x] != 1):
            return None
        return float(self.temperature_c[y, x])

    def roi_statistics(self, roi):
        roi.validate(self.geometry)
        return None if self.temperature_bytes is None else statistics(self.temperature_c, self.validity_mask, roi)

    def render(self, palette=None, bounds=None):
        """Numeric rendering is derived from Celsius and mask, never preview RGB."""
        if not self.has_readings:
            if self.temperature_bytes is not None:
                rgb = np.empty((*self.geometry.shape, 3), dtype=np.uint8)
                rgb[:] = (255, 0, 255)
            elif self.preview_bytes is not None:
                rgb = np.frombuffer(self.preview_bytes, dtype=np.uint8).reshape((*self.geometry.shape, 3))
            else:
                return None
        else:
            bounds = bounds or self.original_bounds or self.auto_bounds()
            values = self.temperature_c.astype(np.float64)
            span = bounds.upper-bounds.lower
            if math.isfinite(span):
                levels = np.rint(np.clip((values-bounds.lower)/span, 0, 1)*255).astype(np.uint8)
            else:
                # Finite opposite-sign endpoints can have an overflowing span.
                levels = np.rint(np.clip((values*.5-bounds.lower*.5)/(bounds.upper*.5-bounds.lower*.5), 0, 1)*255).astype(np.uint8)
            rgb = palette_rgb(levels, palette or self.original_palette)
            if self.mask_bytes is not None:
                rgb[self.validity_mask == 0] = (255, 0, 255)
        return self.transform.image(rgb)

    def auto_bounds(self):
        if not self.has_readings:
            return None
        values = self.temperature_c
        if self.mask_bytes is not None: values = values[self.validity_mask == 1]
        lo, hi = map(float, np.percentile(values.astype(np.float64), (2, 98)))
        if hi-lo < 1: lo, hi = (lo+hi)/2-.5, (lo+hi)/2+.5
        return CelsiusRange(lo, hi)


def adapt_legacy(capture):
    """Validate with the existing loader first; adapt without reinterpreting it."""
    geometry = Geometry(*reversed(capture.temperature_c.shape))
    metadata = capture.metadata
    legacy_roi = capture.roi
    roi = None if legacy_roi is None else Rectangle(legacy_roi.x1, legacy_roi.y1, legacy_roi.x2, legacy_roi.y2)
    presentation = metadata.get('presentation', {})
    from celsius_palette import DEFAULT_PALETTE
    palette = getattr(capture, 'original_palette', presentation.get('palette', DEFAULT_PALETTE))
    bounds = getattr(capture, 'original_bounds', None)
    if bounds is None and 'effective_min_c' in presentation:
        bounds = CelsiusRange(presentation['effective_min_c'], presentation['effective_max_c'])
    automatic = getattr(capture, 'automatic_range', presentation.get('range_mode', 'auto') == 'auto')
    temperature = capture.temperature_c.astype('<f4', copy=False).tobytes()
    native = NativePlane('raw14', 'ht301.raw14', '<u2', geometry.shape, capture.raw14.tobytes())
    provenance = {'kind': 'native_equivalent', 'physical_accuracy': 'not_independently_validated',
                  'warning': metadata.get('accuracy_warning', 'Native-equivalent temperatures; absolute physical accuracy not yet independently validated.')}
    manifest = {'source': {'module_id': 'ht301', 'model_id': 'HT-301/T3-317-13', 'origin': 'imported_legacy'},
                'measurement': {'provenance': provenance}, 'legacy_metadata': metadata}
    is_recording = hasattr(capture, 'payload')
    protected = (capture.source_path.parent,) if is_recording else tuple(capture.source_path.with_suffix(s) for s in ('.json', '.npz', '.png'))
    return OfflineMeasurement(capture.source_path, 'lmthermal-radiometric-recording' if is_recording else 'lmthermal-radiometric-capture',
                              geometry, temperature, None, (native,), freeze(manifest),
                              evidence=freeze({'legacy_metadata': metadata}),
                              payload_bytes=freeze({'temperature': temperature, 'native': native.data,
                                                    **({'acquisition': capture.raw_transport} if getattr(capture, 'raw_transport', None) is not None else {})}),
                              stats=statistics(capture.temperature_c),
                              roi=roi, original_palette=palette, original_palette_id=next(k for k, v in PALETTE_IDS.items() if v == palette), original_bounds=bounds,
                              automatic_range=automatic, protected_paths=protected,
                              reported_center=(capture.trailer_center_index, capture.trailer_center_c))


def save_offline_png(source, path, palette, bounds=None):
    """Portable exclusive PNG publication; protect originals and recording roots.

    No overwrite and no Unix hard links. A failed write removes its own partial
    output. This is not an atomic power-loss guarantee or an LMTX writer.
    """
    import cv2
    target = Path(path).absolute()
    if target.suffix.lower() != '.png': raise ValueError('Destination must end in .png')
    resolved = target.resolve()
    if resolved == source.source_path.resolve() or any(
            resolved == p.resolve() or (p.is_dir() and resolved.is_relative_to(p.resolve()))
            for p in source.protected_paths):
        raise ValueError('Cannot overwrite source capture or write inside source recording')
    rgb = source.render(palette, bounds)
    if rgb is None: raise ValueError('No primary image or measurable temperatures to render')
    ok, encoded = cv2.imencode('.png', cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    if not ok: raise OSError('PNG encoding failed')
    created = False
    try:
        with target.open('xb') as stream:
            created = True
            stream.write(encoded.tobytes())
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        if created: target.unlink(missing_ok=True)
        raise
    return target
