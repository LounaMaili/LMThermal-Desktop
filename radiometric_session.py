"""Evidence-gated, PyQt-independent HT-301 normal-range radiometric session.

Only the observed 384-wide, range-120, lens-68, shutter-fix-1.5 branch is
supported. Native arithmetic equivalence is not physical calibration.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import math
import struct
import time
from typing import Callable

import numpy as np

from measurement_baseline import FRAME_BYTES, FRAME_WIDTH, IMAGE_BYTES, IMAGE_HEIGHT, FrameParameters, parse_frame
from native_equivalent_thermometry import build_lookup, temperature_matrix


class SessionState(str, Enum):
    """A state is ready only after observed live, valid post-shutter frames."""

    DISCONNECTED = "disconnected"
    DISPLAY_STREAM = "display_stream"
    SWITCHING_TO_RAW14 = "switching_to_raw14"
    RAW14_UNSETTLED = "raw14_unsettled"
    SHUTTER_TRANSIENT = "shutter_transient"
    RADIOMETRIC_READY = "radiometric_ready"
    ERROR = "error"


class SessionError(RuntimeError):
    """A required stage or measurement condition was not observed."""


@dataclass(frozen=True)
class FrameInspection:
    """Cheap transport checks; no display-normalized value enters thermometry."""

    mode: str
    reason: str | None
    word_min: int | None
    word_max: int | None
    image_digest: str | None
    summary_valid: bool


@dataclass(frozen=True)
class MeasurementFrame:
    """One verified raw14 frame and native-equivalent range-120 outputs.

    Trailer center and literal pixel (192, 144) are separate observations.
    Arrays are read-only views/copies so preview code cannot alter evidence.
    """

    raw: bytes
    received_monotonic: float
    measured_at_utc: str
    raw14: np.ndarray
    temperature_c: np.ndarray
    lookup_trace: dict
    parameters: FrameParameters
    trailer_center_index: int
    trailer_center_c: float
    literal_center_index: int
    literal_center_c: float
    high_index: int
    high_c: float
    high_xy: tuple[int, int]
    low_index: int
    low_c: float
    low_xy: tuple[int, int]
    image_word_min: int
    image_word_max: int
    state: SessionState = SessionState.RADIOMETRIC_READY


@dataclass(frozen=True)
class FrameObservation:
    """Result of a camera read, including rejected and shutter-held frames."""

    raw: bytes
    received_monotonic: float
    state: SessionState
    inspection: FrameInspection
    repeated_image: bool
    rejection: str | None
    measurement: MeasurementFrame | None


def inspect_frame(raw: bytes) -> FrameInspection:
    """Classify complete pixels and check inputs before any lookup indexing."""
    if len(raw) != FRAME_BYTES:
        return FrameInspection("invalid", "transport_size", None, None, None, False)
    words = np.frombuffer(raw, dtype="<u2", count=IMAGE_BYTES // 2).reshape(IMAGE_HEIGHT, FRAME_WIDTH)
    low, high = int(words.min()), int(words.max())
    digest = hashlib.sha256(raw[:IMAGE_BYTES]).hexdigest()
    if np.all((words & 0xFF00) == 0x8000):
        return FrameInspection("display", None, low, high, digest, False)
    if high >= 0x4000:
        return FrameInspection("invalid", "mixed_or_out_of_range_words", low, high, digest, False)
    params = parse_frame(raw).parameters
    values = (params.correction, params.reflected_temp, params.ambient_temp,
              params.humidity, params.emissivity, params.calibration_0,
              params.calibration_1, params.calibration_2, params.calibration_3,
              params.calibration_4)
    if (not all(math.isfinite(v) for v in values) or params.calibration_0 <= 0 or
            params.emissivity <= 0 or params.humidity < 0 or params.distance <= 0):
        return FrameInspection("raw14", "invalid_settings_or_calibration", low, high, digest, False)
    if raw[223494:223514] != raw[224094:224114]:
        return FrameInspection("raw14", "calibration_copy_mismatch", low, high, digest, False)
    center, high_index, low_index = (struct.unpack_from("<H", raw, offset)[0]
                                     for offset in (221208, 221192, 221198))
    if max(center, high_index, low_index) >= 0x4000:
        return FrameInspection("raw14", "summary_index_out_of_range", low, high, digest, False)
    high_xy = struct.unpack_from("<2H", raw, 221188)
    low_xy = struct.unpack_from("<2H", raw, 221194)
    coords_ok = all(x < FRAME_WIDTH and y < IMAGE_HEIGHT for x, y in (high_xy, low_xy))
    summary_valid = (coords_ok and high_index == high and low_index == low and
                     int(words[high_xy[1], high_xy[0]]) == high_index and
                     int(words[low_xy[1], low_xy[0]]) == low_index)
    return FrameInspection("raw14", None, low, high, digest, bool(summary_valid))


def make_measurement(raw: bytes, received_monotonic: float) -> MeasurementFrame:
    """Create an immutable measurement only from fully consistent raw14 data."""
    inspection = inspect_frame(raw)
    if inspection.mode != "raw14" or inspection.reason or not inspection.summary_valid:
        raise ValueError(f"Frame is not measurement-ready: {inspection.reason or 'summary_mismatch'}")
    try:
        lookup, trace = build_lookup(raw)
        matrix = temperature_matrix(raw, lookup)
    except ArithmeticError as exc:
        raise ValueError(f"Native-equivalent arithmetic failed: {exc}") from exc
    words = np.frombuffer(raw, dtype="<u2", count=IMAGE_BYTES // 2).reshape(IMAGE_HEIGHT, FRAME_WIDTH)
    center = struct.unpack_from("<H", raw, 221208)[0]
    high = struct.unpack_from("<H", raw, 221192)[0]
    low = struct.unpack_from("<H", raw, 221198)[0]
    for index in (center, high, low):
        if not math.isfinite(float(lookup[index])):
            raise ValueError("Trailer selects undefined native lookup entry")
    correction = np.float32(parse_frame(raw).parameters.correction)
    # Keep the native summary correction separate from the literal center.
    summary = lookup[[center, high, low]] + correction
    high_xy = tuple(map(int, struct.unpack_from("<2H", raw, 221188)))
    low_xy = tuple(map(int, struct.unpack_from("<2H", raw, 221194)))
    words.setflags(write=False)
    matrix.setflags(write=False)
    return MeasurementFrame(
        raw=bytes(raw), received_monotonic=received_monotonic,
        measured_at_utc=datetime.now(timezone.utc).isoformat(),
        raw14=words, temperature_c=matrix, lookup_trace=trace,
        parameters=parse_frame(raw).parameters,
        trailer_center_index=center, trailer_center_c=float(summary[0]),
        literal_center_index=int(words[144, 192]), literal_center_c=float(matrix[144, 192]),
        high_index=high, high_c=float(summary[1]), high_xy=high_xy,
        low_index=low, low_c=float(summary[2]), low_xy=low_xy,
        image_word_min=inspection.word_min, image_word_max=inspection.word_max,
    )


class HT301RadiometricSession:
    """Run the observed 32772, 32800, 32768 sequence with evidence gates.

    `stream.next()` returns `(receipt_monotonic_time, complete_frame_bytes)`;
    `control` provides `get()` and `set(value)`. Callbacks can display each
    observation without modifying bytes or controlling the acquisition order.
    """

    def __init__(self, stream, control, *, min_shutter_discard: int = 75,
                 live_frames: int = 5, stage_limit: int = 150, stage_discard: int = 15,
                 on_observation: Callable[[FrameObservation], None] | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        if min_shutter_discard < 75 or live_frames < 2 or stage_limit < live_frames or stage_discard < 0:
            raise ValueError("Require at least 75 shutter frames and two live frames")
        self.stream, self.control = stream, control
        self.min_shutter_discard, self.live_frames, self.stage_limit = min_shutter_discard, live_frames, stage_limit
        self.stage_discard = stage_discard
        self.on_observation, self.sleep, self.clock = on_observation, sleep, clock
        self.state = SessionState.DISCONNECTED
        self.rejections = Counter()
        self.events: list[dict] = []
        self._previous_digest: str | None = None
        self._live_streak = 0
        self._shutter_reads = 0
        self._normal_range_confirmed = False
        self._ready_since: float | None = None
        self._initialization_started: float | None = None
        self._started = self.clock()

    @property
    def time_to_ready_seconds(self) -> float | None:
        """Return elapsed monotonic time from session construction to readiness."""
        return (None if self._ready_since is None or self._initialization_started is None
                else self._ready_since - self._initialization_started)

    def _write_control(self, value: int) -> None:
        """Record the exact supported zoom command and require its readback."""
        if value not in (32772, 32800, 32768):
            raise ValueError("Unsupported camera command")
        self.control.set(value)
        readback = self.control.get()
        self.events.append({"control": value, "readback": readback, "at_seconds": self.clock() - self._started})
        if readback != value:
            raise SessionError(f"zoom_absolute readback {readback} differs from {value}")

    def poll(self) -> FrameObservation:
        """Read one frame and demote readiness on malformed or held output."""
        if self.state == SessionState.ERROR:
            raise SessionError("Session is in an error state")
        try:
            timestamp, raw = self.stream.next()
        except Exception as exc:
            self.state = SessionState.ERROR
            raise SessionError(f"Camera acquisition failed: {exc}") from exc
        inspection = inspect_frame(raw)
        repeated = bool(inspection.image_digest and inspection.image_digest == self._previous_digest)
        self._previous_digest = inspection.image_digest
        rejection = inspection.reason
        measurement = None
        if inspection.mode == "display":
            self._live_streak = 0
            if self.state == SessionState.RADIOMETRIC_READY:
                self.state = SessionState.ERROR
                rejection = "lost_raw14_mode"
            elif self.state == SessionState.SHUTTER_TRANSIENT:
                rejection = "shutter_display_frame"
            elif self.state not in (SessionState.SWITCHING_TO_RAW14, SessionState.SHUTTER_TRANSIENT):
                self.state = SessionState.DISPLAY_STREAM
        elif inspection.mode == "raw14" and rejection is None:
            if self.state == SessionState.SHUTTER_TRANSIENT:
                self._shutter_reads += 1
                if self._shutter_reads >= self.min_shutter_discard:
                    self.state = SessionState.RAW14_UNSETTLED
                    self._live_streak = 0
                    self._previous_digest = None
                rejection = "shutter_settling"
            elif repeated:
                self._live_streak = 0
                if self.state == SessionState.RADIOMETRIC_READY:
                    self.state = SessionState.RAW14_UNSETTLED
                rejection = "held_image"
            elif self.state != SessionState.SWITCHING_TO_RAW14:
                if self.state == SessionState.DISCONNECTED:
                    self.state = SessionState.RAW14_UNSETTLED
                if self._normal_range_confirmed and not inspection.summary_valid:
                    rejection = "summary_mismatch"
                    self._live_streak = 0
                    self.state = SessionState.RAW14_UNSETTLED
                elif self._normal_range_confirmed:
                    # All readiness frames must select defined LUT entries,
                    # not merely have high bits clear on the final frame.
                    try:
                        candidate = make_measurement(raw, timestamp)
                    except ValueError as exc:
                        rejection = f"thermometry_invalid: {exc}"
                        self._live_streak = 0
                        self.state = SessionState.RAW14_UNSETTLED
                    else:
                        self._live_streak += 1
                        if self._live_streak >= self.live_frames:
                            measurement = candidate
                            self.state = SessionState.RADIOMETRIC_READY
                            if self._ready_since is None:
                                self._ready_since = self.clock()
                        else:
                            rejection = "awaiting_live_evidence"
                else:
                    self._live_streak += 1
                    rejection = "host_range_unverified"
            elif self.state == SessionState.SWITCHING_TO_RAW14:
                self._live_streak += 1
        else:
            self._live_streak = 0
            if self.state == SessionState.RADIOMETRIC_READY:
                self.state = SessionState.RAW14_UNSETTLED
            rejection = rejection or "invalid_frame"
        if rejection:
            self.rejections[rejection] += 1
        observation = FrameObservation(raw, timestamp, self.state, inspection, repeated, rejection, measurement)
        if self.on_observation:
            self.on_observation(observation)
        return observation

    def _await_raw_stage(self, label: str) -> None:
        """Require two distinct, valid raw14 frames before the next command."""
        # V4L2 can retain frames exposed before a control write.
        for _ in range(self.stage_discard):
            self.poll()
        seen = 0
        previous = None
        last_inspection = None
        for _ in range(self.stage_limit):
            observation = self.poll()
            inspection = observation.inspection
            if (inspection.mode == "raw14" and inspection.reason is None and
                    not observation.repeated_image and inspection.image_digest != previous):
                seen += 1
                previous = inspection.image_digest
                last_inspection = inspection
                if seen >= 2:
                    self.state = SessionState.RAW14_UNSETTLED
                    self.events.append({"stage": label, "verified": True,
                                        "discarded_after_control": self.stage_discard,
                                        "image_word_min_max": [last_inspection.word_min, last_inspection.word_max]})
                    return
            else:
                seen = 0
                previous = inspection.image_digest
        raise SessionError(f"{label}: no distinct valid raw14 transition within {self.stage_limit} frames")

    def initialize_normal_range(self) -> MeasurementFrame:
        """Initialize from display mode and wait for valid live post-shutter data."""
        try:
            self._initialization_started = self.clock()
            baseline = [self.poll() for _ in range(3)]
            if any(item.inspection.mode != "display" for item in baseline):
                raise SessionError("Initialization requires three display baseline frames; existing raw14 needs a known session")
            initial_zoom = self.control.get()
            self.events.append({"stage": "display_baseline", "verified": True,
                                "zoom_readback": initial_zoom,
                                "image_word_min_max": [min(item.inspection.word_min for item in baseline),
                                                       max(item.inspection.word_max for item in baseline)]})
            if initial_zoom != 0:
                raise SessionError("Supported initialization requires zoom_absolute readback 0")
            self.sleep(.5)
            self.state = SessionState.SWITCHING_TO_RAW14
            self._write_control(32772)
            self._await_raw_stage("raw14_transition")
            self.sleep(.6)
            self._write_control(32800)
            self._await_raw_stage("normal_range")
            self._normal_range_confirmed = True
            self.sleep(.5)
            self._write_control(32768)
            self.state = SessionState.SHUTTER_TRANSIENT
            self._shutter_reads = 0
            self._live_streak = 0
            self._previous_digest = None
            for _ in range(self.stage_limit + self.min_shutter_discard):
                observation = self.poll()
                if observation.measurement is not None:
                    self.events.append({"stage": "radiometric_ready", "verified": True,
                                        "shutter_frames": self._shutter_reads,
                                        "time_to_ready_seconds": self.time_to_ready_seconds})
                    return observation.measurement
            raise SessionError("No valid, live post-shutter measurement within frame limit")
        except Exception:
            self.state = SessionState.ERROR
            raise
