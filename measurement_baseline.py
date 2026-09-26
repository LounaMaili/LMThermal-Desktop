"""Frame parsing and explicitly unvalidated thermometry diagnostics for HT-301."""

from dataclasses import asdict, dataclass
import math
from pathlib import Path
import struct

import numpy as np


FRAME_WIDTH = 384
FRAME_HEIGHT = 292
IMAGE_HEIGHT = 288
FRAME_BYTES = FRAME_WIDTH * FRAME_HEIGHT * 2
IMAGE_BYTES = FRAME_WIDTH * IMAGE_HEIGHT * 2
PARAMS_OFFSET = 223742
PARAMS_SIZE = 514


@dataclass(frozen=True)
class FrameParameters:
    """Trailer settings identified by the APK and five copied calibration floats."""

    correction: float
    reflected_temp: float
    ambient_temp: float
    humidity: float
    emissivity: float
    distance: int
    calibration_0: float
    calibration_1: float
    calibration_2: float
    calibration_3: float
    calibration_4: float
    field_372: float
    reflected_temp_repeat: float
    ambient_temp_repeat: float
    humidity_repeat: float
    emissivity_repeat: float


@dataclass(frozen=True)
class ParsedFrame:
    """A complete transport frame with a conservative 384 by 288 image area."""

    raw: bytes
    image_y: np.ndarray
    parameters: FrameParameters

    @property
    def nonimage_bytes(self) -> bytes:
        """Return all trailer bytes, including data before the parameter block."""
        return self.raw[IMAGE_BYTES:]

    @property
    def parameter_bytes(self) -> bytes:
        """Return the documented 514-byte parameter block."""
        return self.raw[PARAMS_OFFSET:]


def parse_frame(raw: bytes) -> ParsedFrame:
    """Decode an exact-size YUYV transport frame without exposing trailer as pixels."""
    if len(raw) != FRAME_BYTES:
        raise ValueError(f"Expected {FRAME_BYTES} bytes, got {len(raw)}")

    pixels = np.frombuffer(raw, dtype=np.uint8).reshape(FRAME_HEIGHT, FRAME_WIDTH, 2)
    y = pixels[:IMAGE_HEIGHT, :, 0]
    p = memoryview(raw)[PARAMS_OFFSET:PARAMS_OFFSET + PARAMS_SIZE]

    def f32(offset: int) -> float:
        return struct.unpack_from("<f", p, offset)[0]

    parameters = FrameParameters(
        correction=f32(0),
        reflected_temp=f32(4),
        ambient_temp=f32(8),
        humidity=f32(12),
        emissivity=f32(16),
        distance=struct.unpack_from("<H", p, 20)[0],
        calibration_0=f32(352),
        calibration_1=f32(356),
        calibration_2=f32(360),
        calibration_3=f32(364),
        calibration_4=f32(368),
        field_372=f32(372),
        reflected_temp_repeat=f32(376),
        ambient_temp_repeat=f32(380),
        humidity_repeat=f32(384),
        emissivity_repeat=f32(388),
    )
    return ParsedFrame(raw, y, parameters)


def image_statistics(image_y: np.ndarray) -> dict:
    """Summarize only the trusted thermal image rows."""
    if image_y.shape != (IMAGE_HEIGHT, FRAME_WIDTH):
        raise ValueError("Expected the 384 by 288 thermal image area")
    low = np.unravel_index(int(np.argmin(image_y)), image_y.shape)
    high = np.unravel_index(int(np.argmax(image_y)), image_y.shape)
    return {
        "min_y": int(image_y[low]),
        "min_position_yx": tuple(int(v) for v in low),
        "max_y": int(image_y[high]),
        "max_position_yx": tuple(int(v) for v in high),
        "mean_y": float(np.mean(image_y)),
        "active_image_center_y": int(image_y[IMAGE_HEIGHT // 2, FRAME_WIDTH // 2]),
        "transport_center_y": int(image_y[FRAME_HEIGHT // 2, FRAME_WIDTH // 2]),
    }


def get_temp_evn_trace(a: float, env_term: float, b: float) -> dict:
    """Trace documented native arithmetic with explicit, caller-supplied inputs.

    The native caller supplies a calibrated lookup value, CalcFixRaw's fourth
    output, and CalcFixRaw's third output. Python traces the algebra without
    native float32 rounding; arbitrary arguments are not a measurement.
    """
    kelvin_candidate = a + 273.15
    fourth_power = kelvin_candidate ** 4
    after_env = fourth_power - env_term
    scaled = b * after_env
    root = scaled ** 0.25 if scaled >= 0 else None
    return {
        "a": a,
        "env_term": env_term,
        "b": b,
        "a_plus_273_15": kelvin_candidate,
        "fourth_power": fourth_power,
        "after_env_subtraction": after_env,
        "after_b_multiplication": scaled,
        "fourth_root": root,
        "result_c_candidate": root - 273.15 if root is not None else None,
    }


def init_temp_param(x: float, y: float) -> tuple[float, float]:
    """Reproduce InitTempParam arithmetic for coefficients at 223494/223498."""
    if x == 0:
        raise ValueError("x must be nonzero")
    a = y / (2.0 * x)
    return a, a * a


def documented_calcfixraw_polynomial(t: float) -> dict:
    """Trace CalcFixRaw's first stage for caller-supplied ambient temperature."""
    c0 = 1.5587
    c1 = 0.06939 * t
    c2 = -0.00027816 * t * t
    c3 = 6.8455e-7 * t * t * t
    polynomial = c0 + c1 + c2 + c3
    return {
        "ambient_temp": t,
        "constant": c0,
        "linear": c1,
        "quadratic": c2,
        "cubic": c3,
        "polynomial": polynomial,
        "exp_polynomial": math.exp(polynomial),
    }


def diagnostic_report(parsed: ParsedFrame) -> dict:
    """Report confirmed trailer inputs and keep rejected historical math separate."""
    stats = image_statistics(parsed.image_y)
    params = parsed.parameters
    metadata_zeroed = all(
        value == 0
        for value in (
            params.reflected_temp,
            params.ambient_temp,
            params.humidity,
            params.emissivity,
            params.distance,
            params.calibration_0,
            params.calibration_1,
        )
    )
    legacy = None
    if not metadata_zeroed:
        legacy_b = params.calibration_0 * params.humidity
        trace = get_temp_evn_trace(stats["transport_center_y"], params.reflected_temp, legacy_b)
        legacy = {
            "assumed_a": "transport center Y (row 146) as native a",
            "assumed_env_term": "block offset 4 (actually reflected temperature) as native radiation term",
            "assumed_b": "block offset 352 times offset 12 (actually calibration_0 times humidity)",
            "trace": trace,
        }
    image_words = np.frombuffer(parsed.raw, dtype="<u2", count=FRAME_WIDTH * IMAGE_HEIGHT)
    trailer = parsed.nonimage_bytes
    center_index = struct.unpack_from("<H", trailer, 24)[0]
    high_index = struct.unpack_from("<H", trailer, 8)[0]
    low_index = struct.unpack_from("<H", trailer, 14)[0]
    calibration_copy_matches = (
        parsed.raw[223494:223514] == parsed.raw[PARAMS_OFFSET + 352:PARAMS_OFFSET + 372]
    )
    return {
        "frame_bytes": len(parsed.raw),
        "image_bytes": IMAGE_BYTES,
        "pre_parameter_trailer_bytes": PARAMS_OFFSET - IMAGE_BYTES,
        "parameter_bytes": len(parsed.parameter_bytes),
        "metadata_zeroed": metadata_zeroed,
        "parameters": asdict(params),
        "image_y": stats,
        "native_lookup_inputs": {
            "image_word_min": int(image_words.min()),
            "image_word_max": int(image_words.max()),
            "image_words_fit_14_bit_lookup": bool(np.all(image_words <= 0x3fff)),
            "trailer_center_index": center_index,
            "trailer_high_index": high_index,
            "trailer_low_index": low_index,
            "calibration_copy_matches": calibration_copy_matches,
        },
        "legacy_rejected_hypothesis": legacy,
        "calcfixraw_first_stage": (
            documented_calcfixraw_polynomial(params.ambient_temp) if not metadata_zeroed else None
        ),
        "limitations": [
            "No validated per-pixel temperature conversion is available.",
            "Block field 356 is a copied calibration coefficient, not the app's live center reading.",
            "The camera mode and native readings needed to validate the lookup remain unavailable.",
        ] + (["Parameter fields are zeroed; no legacy arithmetic was attempted."] if metadata_zeroed else []),
    }


def load_frame(path: Path) -> ParsedFrame:
    """Load a saved raw transport frame for repeatable diagnostics."""
    return parse_frame(path.read_bytes())
