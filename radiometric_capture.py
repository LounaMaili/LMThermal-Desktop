"""Validate and reopen saved measurements without acquisition or thermometry."""

from dataclasses import dataclass, fields
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import tempfile
import zipfile

import cv2
import numpy as np

from celsius_palette import PALETTES, CelsiusRange, render_temperature
from measurement_baseline import FRAME_BYTES, IMAGE_BYTES, FrameParameters
from roi_measurement import NativeROI, roi_statistics


FORMAT_ID = "lmthermal-radiometric-capture"
FORMAT_VERSION = 1
IMAGE_SHAPE = (288, 384)
# Float32 serialization and float64 ROI means may differ by rounding only.
STAT_ATOL_C = 1e-4


class CaptureError(ValueError):
    """A capture is incomplete, unsupported, or inconsistent."""


@dataclass(frozen=True)
class OfflineCapture:
    """Owned immutable data; metadata access returns a separate JSON object."""

    source_path: Path
    raw14_bytes: bytes
    temperature_bytes: bytes
    raw_transport: bytes | None
    metadata_json: str
    original_palette: str
    original_bounds: CelsiusRange
    automatic_range: bool
    roi: NativeROI | None

    @property
    def raw14(self) -> np.ndarray:
        return np.frombuffer(self.raw14_bytes, dtype="<u2").reshape(IMAGE_SHAPE)

    @property
    def temperature_c(self) -> np.ndarray:
        return np.frombuffer(self.temperature_bytes, dtype="<f4").reshape(IMAGE_SHAPE)

    @property
    def metadata(self) -> dict:
        return json.loads(self.metadata_json)

    @property
    def captured_at_utc(self) -> str:
        return self.metadata["captured_at_utc"]

    @property
    def parameters(self) -> dict:
        return self.metadata["parameters"]

    @property
    def accuracy_warning(self) -> str:
        return self.metadata["accuracy_warning"]

    @property
    def high_xy(self):
        value = self.metadata["high"]
        return value["x_px"], value["y_px"]

    @property
    def low_xy(self):
        value = self.metadata["low"]
        return value["x_px"], value["y_px"]

    @property
    def high_c(self):
        return self.metadata["high"]["native_equivalent_c"]

    @property
    def low_c(self):
        return self.metadata["low"]["native_equivalent_c"]

    @property
    def literal_center_index(self):
        return self.metadata["literal_center_pixel"]["raw14_index"]

    @property
    def literal_center_c(self):
        return self.metadata["literal_center_pixel"]["native_equivalent_c"]

    @property
    def trailer_center_index(self):
        return self.metadata["trailer_center"]["raw14_index"]

    @property
    def trailer_center_c(self):
        return self.metadata["trailer_center"]["native_equivalent_c"]


def _require(condition, message):
    if not condition:
        raise CaptureError(message)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _finite_tree(value):
    if isinstance(value, dict):
        return all(_finite_tree(item) for item in value.values())
    if isinstance(value, list):
        return all(_finite_tree(item) for item in value)
    return not isinstance(value, float) or math.isfinite(value)


def _object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _hash(content, expected, name):
    _require(isinstance(expected, str) and len(expected) == 64 and
             all(c in "0123456789abcdef" for c in expected), f"Invalid {name} SHA-256")
    _require(hashlib.sha256(content).hexdigest() == expected, f"{name} SHA-256 mismatch")


def _read(path, limit):
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    _require(len(content) <= limit, f"Capture file too large: {path.name}")
    return content


def _arrays(content):
    """Check archive and NPY headers before allocating arrays; never allow pickle."""
    expected = {"raw14.npy": (IMAGE_SHAPE, np.dtype("<u2")),
                "temperature_c.npy": (IMAGE_SHAPE, np.dtype("<f4")),
                "raw_transport.npy": ((FRAME_BYTES,), np.dtype("uint8"))}
    with zipfile.ZipFile(BytesIO(content)) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        _require(len(names) == len(set(names)) and
                 set(names) in ({"raw14.npy", "temperature_c.npy"}, set(expected)),
                 "Unexpected or missing NPZ arrays")
        for entry in entries:
            shape, dtype = expected[entry.filename]
            _require(entry.file_size <= int(np.prod(shape)) * dtype.itemsize + 4096,
                     "NPZ array exceeds expected size")
            with archive.open(entry) as stream:
                version = np.lib.format.read_magic(stream)
                _require(version in ((1, 0), (2, 0)), "Unsupported NPY header version")
                reader = (np.lib.format.read_array_header_1_0 if version == (1, 0)
                          else np.lib.format.read_array_header_2_0)
                actual_shape, fortran, actual_dtype = reader(stream)
                _require(actual_shape == shape and actual_dtype == dtype and
                         actual_dtype.str == dtype.str and not fortran,
                         f"Wrong shape/dtype/layout for {entry.filename}")
    with np.load(BytesIO(content), allow_pickle=False) as data:
        return {name: data[name] for name in data.files}


def _close(actual, expected):
    return _number(actual) and math.isclose(actual, float(expected), rel_tol=1e-6,
                                           abs_tol=STAT_ATOL_C)


def _validate_metadata(data, raw, temperature):
    _require(isinstance(data, dict) and _finite_tree(data), "Metadata must be a finite JSON object")
    for key, expected in (("format", FORMAT_ID), ("version", FORMAT_VERSION),
                          ("native_width_px", 384), ("native_height_px", 288),
                          ("orientation", "native_camera_coordinates"),
                          ("raw14_dtype", "uint16_le"), ("temperature_dtype", "float32_le")):
        _require(type(data.get(key)) is type(expected) and data[key] == expected,
                 f"Unsupported or invalid {key}: {data.get(key)!r}")
    for key in ("captured_at_utc", "frame_measured_at_utc"):
        stamp = datetime.fromisoformat(data[key])
        _require(stamp.tzinfo is not None and stamp.utcoffset() == timezone.utc.utcoffset(stamp),
                 f"{key} must be a UTC timestamp")
    _require(isinstance(data["accuracy_warning"], str) and data["accuracy_warning"].strip(),
             "Missing physical-accuracy warning")
    params = data["parameters"]
    _require(isinstance(params, dict) and set(params) == {f.name for f in fields(FrameParameters)}
             and all(_number(v) for v in params.values()) and
             type(params["distance"]) is int and 0 <= params["distance"] <= 65535,
             "Invalid calibration/environment parameters")
    _require(isinstance(data["lookup_trace"], dict) and data["lookup_trace"] and
             all(_number(v) or (isinstance(v, list) and all(_number(n) for n in v))
                 for v in data["lookup_trace"].values()), "Invalid lookup trace")
    for key, expected in (("image_word_min", int(raw.min())), ("image_word_max", int(raw.max()))):
        _require(type(data[key]) is int and data[key] == expected, f"Inconsistent {key}")
    for key in ("high", "low", "literal_center_pixel", "trailer_center"):
        reading = data[key]
        _require(isinstance(reading, dict) and type(reading["raw14_index"]) is int and
                 0 <= reading["raw14_index"] < 0x4000 and _number(reading["native_equivalent_c"]),
                 f"Invalid {key} reading")
        if key == "trailer_center":
            continue  # Preserve the recorded reading; do not invent a center-region algorithm.
        x, y = reading["x_px"], reading["y_px"]
        _require(type(x) is int and type(y) is int and 0 <= x < 384 and 0 <= y < 288,
                 f"Invalid {key} coordinates")
        _require(reading["raw14_index"] == int(raw[y, x]) and
                 _close(reading["native_equivalent_c"], temperature[y, x]),
                 f"{key} differs from the stored matrices")
        if key == "literal_center_pixel":
            _require((x, y) == (192, 144), "Literal center must be (192,144)")
        else:
            extreme = temperature.max() if key == "high" else temperature.min()
            _require(_close(reading["native_equivalent_c"], extreme), f"Inconsistent {key} extremum")
    presentation = data["presentation"]
    _require(presentation["palette"] in PALETTES and presentation["range_mode"] in ("auto", "locked")
             and _number(presentation["effective_min_c"]) and _number(presentation["effective_max_c"]),
             "Invalid presentation metadata")
    bounds = CelsiusRange(presentation["effective_min_c"], presentation["effective_max_c"])
    roi = None
    if "roi" in data:
        saved = data["roi"]
        _require(saved["coordinate_semantics"] == "half_open", "Unsupported ROI coordinates")
        geometry = saved["geometry"]
        roi = NativeROI(*(geometry[key] for key in ("x1_px", "y1_px", "x2_px", "y2_px")))
        actual = roi_statistics(temperature, roi).metadata()
        stored = saved["statistics"]
        _require(isinstance(stored, dict) and set(stored) == set(actual), "Invalid ROI statistics")
        for key, value in actual.items():
            if key.endswith("_c"):
                _require(_close(stored[key], value), f"Inconsistent ROI {key}")
            else:
                _require(type(stored[key]) is type(value) and stored[key] == value and
                         (not isinstance(value, list) or all(type(v) is int for v in stored[key])),
                         f"Inconsistent ROI {key}")
    return bounds, roi


def load_capture(json_path: Path) -> OfflineCapture:
    """Use only selected JSON's sibling files; JSON filenames are never followed."""
    try:
        path = Path(json_path).absolute()
        _require(path.suffix.lower() == ".json", "Select the capture JSON completion marker")
        data = json.loads(_read(path, 1024 * 1024), object_pairs_hook=_object)
        _require(isinstance(data, dict) and data.get("format") == FORMAT_ID,
                 "Unsupported capture format")
        _require(type(data.get("version")) is int and data["version"] == FORMAT_VERSION,
                 "Unsupported capture version")
        _require(_finite_tree(data), "Non-finite JSON metadata")
        files = data["files"]
        npz = _read(path.with_suffix(".npz"), 8 * 1024 * 1024)
        png = _read(path.with_suffix(".png"), 8 * 1024 * 1024)
        _hash(npz, files["npz_sha256"], "NPZ")
        _hash(png, files["png_sha256"], "PNG")
        arrays = _arrays(npz)
        raw, temperature = arrays["raw14"], arrays["temperature_c"]
        _require(int(raw.max()) < 0x4000 and np.isfinite(temperature).all(),
                 "Invalid raw14 indices or non-finite temperatures")
        transport = arrays.get("raw_transport")
        transport_bytes = None if transport is None else transport.tobytes()
        if transport is None:
            _require("raw_transport_sha256" not in data and "transport_bytes" not in data,
                     "Declared transport evidence is missing")
        else:
            _require(type(data["transport_bytes"]) is int and data["transport_bytes"] == FRAME_BYTES,
                     "Invalid transport size")
            _hash(transport_bytes, data["raw_transport_sha256"], "Transport")
            _require(transport_bytes[:IMAGE_BYTES] == raw.tobytes(), "Transport image differs from raw14")
        bounds, roi = _validate_metadata(data, raw, temperature)
        return OfflineCapture(path, raw.tobytes(), temperature.tobytes(), transport_bytes,
                              json.dumps(data, allow_nan=False), data["presentation"]["palette"],
                              bounds, data["presentation"]["range_mode"] == "auto", roi)
    except CaptureError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError,
            zipfile.BadZipFile, EOFError, IndexError, RecursionError, RuntimeError,
            NotImplementedError) as exc:
        raise CaptureError(f"Incomplete or malformed capture: {exc}") from exc


def save_rendered_image(capture: OfflineCapture, path: Path, palette: str,
                        bounds: CelsiusRange) -> Path:
    """Save only a clean PNG from the stored matrix; never replace capture files."""
    target = Path(path).absolute()
    if target.suffix.lower() != ".png":
        raise ValueError("Rendered image destination must end in .png")
    originals = {capture.source_path.with_suffix(ext).resolve() for ext in (".json", ".npz", ".png")}
    if target.resolve() in originals:
        raise ValueError("Cannot replace an original capture file")
    rgb = render_temperature(capture.temperature_c, bounds.lower, bounds.upper, palette)
    ok, encoded = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    if not ok:
        raise OSError("PNG encoding failed")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".render-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded.tobytes())
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target
