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
    """Decoded fields; names ending in 'candidate' retain unverified semantics."""

    env_temp1: float
    env_temp2: float
    emissivity: float
    distance_factor: float
    active: int
    gain_candidate: float
    center_temp_candidate: float
    field_360: float
    offset_factor_candidate: float
    calib_factor_candidate: float
    field_372: float
    env_temp1_repeat: float
    env_temp2_repeat: float
    emissivity_repeat: float
    distance_factor_repeat: float


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
        env_temp1=f32(4),
        env_temp2=f32(8),
        emissivity=f32(12),
        distance_factor=f32(16),
        active=struct.unpack_from("<I", p, 20)[0],
        gain_candidate=f32(352),
        center_temp_candidate=f32(356),
        field_360=f32(360),
        offset_factor_candidate=f32(364),
        calib_factor_candidate=f32(368),
        field_372=f32(372),
        env_temp1_repeat=f32(376),
        env_temp2_repeat=f32(380),
        emissivity_repeat=f32(384),
        distance_factor_repeat=f32(388),
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

    The native caller and the units of ``env_term`` remain unknown. This function
    does not validate a temperature measurement or map frame fields to arguments.
    Python arithmetic traces the algebra, not native float32 rounding exactly.
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
    """Reproduce decoded InitTempParam arithmetic; frame inputs are unknown."""
    if x == 0:
        raise ValueError("x must be nonzero")
    a = y / (2.0 * x)
    return a, a * a


def documented_calcfixraw_polynomial(t: float) -> dict:
    """Show the documented partial polynomial without claiming a full conversion."""
    c0 = 1.5587
    c1 = 0.06939 * t
    c2 = -0.000278 * t * t
    c3 = 6.86e-7 * t * t * t
    polynomial = c0 + c1 + c2 + c3
    return {
        "t_candidate": t,
        "constant": c0,
        "linear": c1,
        "quadratic": c2,
        "cubic": c3,
        "polynomial": polynomial,
        "exp_polynomial": math.exp(polynomial),
    }


def diagnostic_report(parsed: ParsedFrame) -> dict:
    """Report observations and the rejected legacy hypothesis separately."""
    stats = image_statistics(parsed.image_y)
    params = parsed.parameters
    metadata_zeroed = all(
        value == 0
        for value in (
            params.env_temp1,
            params.env_temp2,
            params.emissivity,
            params.distance_factor,
            params.gain_candidate,
            params.center_temp_candidate,
        )
    )
    legacy = None
    if not metadata_zeroed:
        legacy_b = params.gain_candidate * params.emissivity
        trace = get_temp_evn_trace(stats["transport_center_y"], params.env_temp1, legacy_b)
        legacy = {
            "assumed_a": "transport center Y (row 146) as native a",
            "assumed_env_term": "env_temp1 as native env_term",
            "assumed_b": "gain_candidate * emissivity",
            "trace": trace,
            "difference_from_field_356_c": (
                trace["result_c_candidate"] - params.center_temp_candidate
                if trace["result_c_candidate"] is not None else None
            ),
        }
    return {
        "frame_bytes": len(parsed.raw),
        "image_bytes": IMAGE_BYTES,
        "pre_parameter_trailer_bytes": PARAMS_OFFSET - IMAGE_BYTES,
        "parameter_bytes": len(parsed.parameter_bytes),
        "metadata_zeroed": metadata_zeroed,
        "parameters": asdict(params),
        "image_y": stats,
        "legacy_rejected_hypothesis": legacy,
        "calcfixraw_partial_candidate": documented_calcfixraw_polynomial(stats["transport_center_y"]),
        "limitations": [
            "No validated per-pixel temperature conversion is available.",
            "Field 356 has not been shown to be a live center measurement.",
            "The native inputs to GetTempEvn, InitTempParam and CalcFixRaw are unknown.",
        ] + (["Parameter fields are zeroed; no legacy arithmetic was attempted."] if metadata_zeroed else []),
    }


def load_frame(path: Path) -> ParsedFrame:
    """Load a saved raw transport frame for repeatable diagnostics."""
    return parse_frame(path.read_bytes())
