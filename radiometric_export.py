"""Lossless, versioned capture sets from one ready HT-301 measurement."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import tempfile

import cv2
import numpy as np

from celsius_palette import PALETTES, CelsiusRange, render_temperature
from measurement_baseline import FRAME_BYTES, FRAME_WIDTH, IMAGE_BYTES, IMAGE_HEIGHT
from radiometric_session import FrameObservation, SessionState


FORMAT_ID = "lmthermal-radiometric-capture"
FORMAT_VERSION = 1
ACCURACY_WARNING = (
    "Native-equivalent temperatures; absolute physical accuracy not yet "
    "independently validated."
)
IMAGE_SHAPE = (IMAGE_HEIGHT, FRAME_WIDTH)


@dataclass(frozen=True)
class CaptureSnapshot:
    """Immutable copy of one measurement and its displayed Celsius presentation."""

    raw_transport: bytes
    raw14_bytes: bytes
    temperature_bytes: bytes
    metadata_json: str
    palette: str
    bounds: CelsiusRange

    @property
    def raw14(self) -> np.ndarray:
        return np.frombuffer(self.raw14_bytes, dtype="<u2").reshape(IMAGE_SHAPE)

    @property
    def temperature_c(self) -> np.ndarray:
        return np.frombuffer(self.temperature_bytes, dtype="<f4").reshape(IMAGE_SHAPE)

    @property
    def metadata(self) -> dict:
        return json.loads(self.metadata_json)


def snapshot_capture(observation: FrameObservation, palette: str,
                     bounds: CelsiusRange, automatic_range: bool) -> CaptureSnapshot:
    """Reject stale observations and freeze arrays before a dialog can change UI state."""
    if observation.state != SessionState.RADIOMETRIC_READY or observation.measurement is None:
        raise ValueError("Only a radiometric-ready measurement can be exported")
    measurement = observation.measurement
    if measurement.state != SessionState.RADIOMETRIC_READY or observation.raw != measurement.raw:
        raise ValueError("Observation and measurement do not describe the same ready frame")
    if palette not in PALETTES or not isinstance(bounds, CelsiusRange):
        raise ValueError("A supported palette and effective Celsius range are required")
    if type(automatic_range) is not bool:
        raise ValueError("Range mode must be a Boolean")
    if len(measurement.raw) != FRAME_BYTES:
        raise ValueError("Transport frame has the wrong size")
    raw14 = np.asarray(measurement.raw14)
    temperature = np.asarray(measurement.temperature_c)
    if raw14.shape != IMAGE_SHAPE or raw14.dtype != np.dtype("uint16"):
        raise ValueError("Expected native 288 by 384 uint16 raw14 matrix")
    if temperature.shape != IMAGE_SHAPE or temperature.dtype != np.dtype("float32"):
        raise ValueError("Expected native 288 by 384 float32 Celsius matrix")
    if int(raw14.max()) >= 0x4000 or not np.isfinite(temperature).all():
        raise ValueError("Capture contains invalid raw14 indices or temperatures")
    transport_words = np.frombuffer(measurement.raw, dtype="<u2",
                                    count=IMAGE_BYTES // 2).reshape(IMAGE_SHAPE)
    if not np.array_equal(raw14, transport_words):
        raise ValueError("Raw14 matrix differs from the exact transport image")
    for index, point in ((measurement.high_index, measurement.high_xy),
                         (measurement.low_index, measurement.low_xy)):
        x, y = point
        if not (0 <= x < FRAME_WIDTH and 0 <= y < IMAGE_HEIGHT and
                int(raw14[y, x]) == index):
            raise ValueError("Camera extremum coordinates do not match raw14")
    if measurement.literal_center_index != int(raw14[144, 192]):
        raise ValueError("Literal center index does not match raw14")

    raw14_bytes = raw14.astype("<u2", copy=False).tobytes()
    temperature_bytes = temperature.astype("<f4", copy=False).tobytes()
    metadata = {
        "format": FORMAT_ID,
        "version": FORMAT_VERSION,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "frame_measured_at_utc": measurement.measured_at_utc,
        "native_width_px": FRAME_WIDTH,
        "native_height_px": IMAGE_HEIGHT,
        "orientation": "native_camera_coordinates",
        "raw14_dtype": "uint16_le",
        "temperature_dtype": "float32_le",
        "transport_bytes": FRAME_BYTES,
        "raw_transport_sha256": hashlib.sha256(measurement.raw).hexdigest(),
        "image_word_min": measurement.image_word_min,
        "image_word_max": measurement.image_word_max,
        "high": {"raw14_index": measurement.high_index,
                 "native_equivalent_c": measurement.high_c,
                 "x_px": measurement.high_xy[0], "y_px": measurement.high_xy[1]},
        "low": {"raw14_index": measurement.low_index,
                "native_equivalent_c": measurement.low_c,
                "x_px": measurement.low_xy[0], "y_px": measurement.low_xy[1]},
        "literal_center_pixel": {"raw14_index": measurement.literal_center_index,
                                 "native_equivalent_c": measurement.literal_center_c,
                                 "x_px": 192, "y_px": 144},
        "trailer_center": {"raw14_index": measurement.trailer_center_index,
                           "native_equivalent_c": measurement.trailer_center_c},
        "parameters": asdict(measurement.parameters),
        "lookup_trace": measurement.lookup_trace,
        "presentation": {"palette": palette, "range_mode": "auto" if automatic_range else "locked",
                         "effective_min_c": bounds.lower, "effective_max_c": bounds.upper},
        "accuracy_warning": ACCURACY_WARNING,
    }
    # Reject NaN/Infinity in the trace or scalar metadata before any file is written.
    metadata_json = json.dumps(metadata, allow_nan=False, sort_keys=True, indent=2) + "\n"
    return CaptureSnapshot(bytes(measurement.raw), raw14_bytes, temperature_bytes,
                           metadata_json, palette, bounds)


def _payloads(snapshot: CaptureSnapshot) -> dict[str, bytes]:
    rgb = render_temperature(snapshot.temperature_c, snapshot.bounds.lower,
                             snapshot.bounds.upper, snapshot.palette)
    ok, encoded = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    if not ok:
        raise OSError("PNG encoding failed")
    npz = BytesIO()
    np.savez_compressed(npz, temperature_c=snapshot.temperature_c,
                        raw14=snapshot.raw14,
                        raw_transport=np.frombuffer(snapshot.raw_transport, dtype=np.uint8))
    png_bytes, npz_bytes = encoded.tobytes(), npz.getvalue()
    metadata = snapshot.metadata
    metadata["files"] = {
        "png_sha256": hashlib.sha256(png_bytes).hexdigest(),
        "npz_sha256": hashlib.sha256(npz_bytes).hexdigest(),
    }
    json_bytes = (json.dumps(metadata, allow_nan=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    return {".png": png_bytes, ".npz": npz_bytes, ".json": json_bytes}


def export_capture(snapshot: CaptureSnapshot, basename: Path) -> dict[str, Path]:
    """Publish a no-overwrite three-file set; JSON is the final completion marker.

    Same-directory hard links publish each fully written temporary file atomically.
    Ordinary failures roll back files from this call. A process crash between
    links can leave a set without JSON; consumers must require all three files.
    """
    base = Path(basename)
    if not base.name or base.name in (".", ".."):
        raise ValueError("A capture basename is required")
    finals = {suffix: base.with_name(base.name + suffix)
              for suffix in (".png", ".npz", ".json")}
    for final in finals.values():
        if final.exists():
            raise FileExistsError(f"Capture file already exists: {final}")
    payloads = _payloads(snapshot)
    temporary = []
    published = []
    try:
        for suffix, content in payloads.items():
            with tempfile.NamedTemporaryFile(mode="wb", prefix=f".{base.name}.",
                                             suffix=suffix + ".tmp", dir=base.parent,
                                             delete=False) as stream:
                temporary.append(Path(stream.name))
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        for suffix, temp in zip(payloads, temporary):
            os.link(temp, finals[suffix])
            published.append(finals[suffix])
        return finals
    except Exception:
        for final in published:
            final.unlink(missing_ok=True)
        raise
    finally:
        for temp in temporary:
            temp.unlink(missing_ok=True)
