"""Small live-measurement CSV logs; no acquisition, palettes, or thermometry."""

import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
import os
from numbers import Real
from pathlib import Path
import tempfile
import threading
import time

from radiometric_export import ACCURACY_WARNING
from radiometric_session import FrameObservation, SessionState
from roi_measurement import NativeROI, roi_statistics


FORMAT_ID = "lmthermal-measurement-time-series"
FORMAT_VERSION = 1
SUPPORTED_RATES = (0.5, 1.0, 2.0, 5.0, 10.0)
COLUMN_DEFINITIONS = {
    "timestamp_utc": "UTC sampling time (ISO 8601)",
    "elapsed_s": "Monotonic seconds since logging started",
    "sequence": "One-based emitted sample sequence, including gaps",
    "state": "Observed session state, or disconnected when no observation",
    "status": "valid, rejection/state reason, or no_new_observation",
    "measurement_valid": "true or false; false rows have empty measured values",
    "frame_measured_at_utc": "UTC measurement-frame time; empty for gaps",
    "high_c": "Whole-image native-equivalent high temperature (Celsius)",
    "high_x_px": "High native x pixel", "high_y_px": "High native y pixel",
    "low_c": "Whole-image native-equivalent low temperature (Celsius)",
    "low_x_px": "Low native x pixel", "low_y_px": "Low native y pixel",
    "literal_center_c": "Literal (192,144) native-equivalent Celsius",
    "literal_center_raw14": "Literal (192,144) original raw14 index",
    "trailer_center_c": "Separate trailer-center native-equivalent Celsius",
    "trailer_center_raw14": "Separate trailer-center raw14 index",
    "roi_x1_px": "Native half-open ROI left", "roi_y1_px": "Native half-open ROI top",
    "roi_x2_px": "Native half-open ROI right", "roi_y2_px": "Native half-open ROI bottom",
    "roi_min_c": "Exact saved-frame ROI minimum (Celsius)",
    "roi_max_c": "Exact saved-frame ROI maximum (Celsius)",
    "roi_mean_c": "Exact frame ROI mean, float64 accumulation (Celsius)",
    "roi_pixel_count": "Number of pixels in the measured ROI",
}
COLUMNS = tuple(COLUMN_DEFINITIONS)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_sample(observation: FrameObservation | None, roi: NativeROI | None, *,
                  timestamp_utc: str, elapsed_s: float, sequence: int,
                  fresh: bool = True) -> dict:
    """Copy validated frame readings and reuse ROI arithmetic; gaps contain no values."""
    if observation is not None and not isinstance(observation, FrameObservation):
        raise ValueError("Time-series measurements require live observations, not offline captures")
    if not math.isfinite(elapsed_s) or elapsed_s < 0 or type(sequence) is not int or sequence < 1:
        raise ValueError("Invalid sample time or sequence")
    row = dict.fromkeys(COLUMNS)
    state = observation.state.value if observation is not None else "disconnected"
    row.update(timestamp_utc=timestamp_utc, elapsed_s=elapsed_s, sequence=sequence,
               state=state, status=state, measurement_valid=False)
    if roi is not None:
        row.update(roi_x1_px=roi.x1, roi_y1_px=roi.y1, roi_x2_px=roi.x2, roi_y2_px=roi.y2)
    measurement = None if observation is None else observation.measurement
    valid = (observation is not None and observation.state == SessionState.RADIOMETRIC_READY
             and measurement is not None and measurement.state == SessionState.RADIOMETRIC_READY
             and observation.raw == measurement.raw and not observation.repeated_image
             and observation.rejection is None)
    if not fresh:
        row["status"] = "no_new_observation"
    elif not valid:
        if observation is not None and observation.rejection:
            row["status"] = observation.rejection
    else:
        row.update(status="valid", measurement_valid=True,
                   frame_measured_at_utc=measurement.measured_at_utc,
                   high_c=measurement.high_c, high_x_px=measurement.high_xy[0], high_y_px=measurement.high_xy[1],
                   low_c=measurement.low_c, low_x_px=measurement.low_xy[0], low_y_px=measurement.low_xy[1],
                   literal_center_c=measurement.literal_center_c,
                   literal_center_raw14=measurement.literal_center_index,
                   trailer_center_c=measurement.trailer_center_c,
                   trailer_center_raw14=measurement.trailer_center_index)
        if roi is not None:
            stats = roi_statistics(measurement.temperature_c, roi)
            row.update(roi_min_c=stats.min_c, roi_max_c=stats.max_c, roi_mean_c=stats.mean_c,
                       roi_pixel_count=stats.pixel_count)
    # Reject inconsistent/non-finite inputs rather than emitting plausible CSV values.
    serialize_row(row)
    return row


def serialize_row(row: dict) -> dict:
    """Locale-independent decimals, UTF-8 CSV values and explicit empty cells."""
    if tuple(row) != COLUMNS:
        raise ValueError("Unexpected measurement CSV schema")
    for value in row.values():
        if isinstance(value, Real) and not math.isfinite(value):
            raise ValueError("Non-finite measurement cannot be logged")
    return {key: "" if value is None else ("true" if value else "false")
            if type(value) is bool else value for key, value in row.items()}


class LatestSampler:
    """One latest slot, fixed monotonic cadence, no backlog or catch-up rows."""

    def __init__(self, rate_hz: float, started_monotonic: float):
        if rate_hz not in SUPPORTED_RATES:
            raise ValueError("Unsupported measurement sample rate")
        self.interval = 1.0 / rate_hz
        self.started = started_monotonic
        self._next_tick = 1
        self.next_due = started_monotonic + self.interval
        self.sequence = 0
        self._latest = None
        self._roi = None
        self._sampled = None
        self._lock = threading.Lock()

    def update(self, observation, roi):
        if observation is not None and not isinstance(observation, FrameObservation):
            raise ValueError("Logging is available only for live camera observations")
        with self._lock:
            self._latest, self._roi = observation, roi

    def sample_due(self, now: float, timestamp_utc: str) -> dict | None:
        if now < self.next_due:
            return None
        with self._lock:
            observation, roi = self._latest, self._roi
        fresh = observation is None or observation is not self._sampled
        self._sampled = observation
        self.sequence += 1
        elapsed = now - self.started
        # A delayed scheduler writes one current sample and skips missed instants.
        self._next_tick = max(self._next_tick + 1, math.floor(elapsed / self.interval) + 1)
        self.next_due = self.started + self._next_tick * self.interval
        return create_sample(observation, roi, timestamp_utc=timestamp_utc,
                             elapsed_s=elapsed, sequence=self.sequence, fresh=fresh)


def session_metadata(*, created_at_utc: str, rate_hz: float, camera_identity: str | None,
                     observation: FrameObservation | None) -> dict:
    if rate_hz not in SUPPORTED_RATES:
        raise ValueError("Unsupported measurement sample rate")
    if observation is not None and not isinstance(observation, FrameObservation):
        raise ValueError("Offline captures cannot start time-series recording")
    measurement = (observation.measurement if observation is not None and
                   observation.state == SessionState.RADIOMETRIC_READY else None)
    result = {
        "format": FORMAT_ID, "version": FORMAT_VERSION, "completion": "incomplete",
        "created_at_utc": created_at_utc, "camera_identity": camera_identity,
        "native_width_px": 384, "native_height_px": 288,
        "orientation": "native_camera_coordinates", "rate_hz": rate_hz,
        "sampling_interval_s": 1.0 / rate_hz,
        "units": {"temperature": "degree_Celsius", "elapsed": "second", "coordinates": "native_pixel"},
        "accuracy_warning": ACCURACY_WARNING, "columns": COLUMN_DEFINITIONS,
        "parameters_at_start": None if measurement is None else asdict(measurement.parameters),
        "lookup_trace_at_start": None if measurement is None else measurement.lookup_trace,
        "gap_policy": "Invalid or not-new observations have empty measurement cells; ROI geometry may remain.",
        "sample_count": 0, "valid_sample_count": 0,
    }
    # Also own the settings snapshot, independent of later host references.
    return json.loads(json.dumps(result, allow_nan=False))


class IncrementalLogWriter:
    """Exclusive incomplete files; complete JSON published last on a clean finish."""

    def __init__(self, csv_path: Path, metadata: dict):
        self.csv_path = Path(csv_path).absolute()
        if self.csv_path.suffix.lower() != ".csv":
            raise ValueError("Measurement log destination must end in .csv")
        self.json_path = self.csv_path.with_suffix(".json")
        self.csv_partial = self.csv_path.with_name(self.csv_path.name + ".incomplete")
        self.json_partial = self.json_path.with_name(self.json_path.name + ".incomplete")
        for path in (self.csv_path, self.json_path, self.csv_partial, self.json_partial):
            if path.exists():
                raise FileExistsError(f"Log destination already exists: {path}")
        self.metadata = json.loads(json.dumps(metadata, allow_nan=False))
        self.count = self.valid_count = 0
        self.closed = False
        owned = []
        try:
            self.stream = self.csv_partial.open("x", encoding="utf-8", newline="")
            owned.append(self.csv_partial)
            with self.json_partial.open("x", encoding="utf-8") as stream:
                owned.append(self.json_partial)
                json.dump(self.metadata, stream, allow_nan=False, indent=2)
                stream.write("\n")
            self.writer = csv.DictWriter(self.stream, fieldnames=COLUMNS)
            self.writer.writeheader()
            self.stream.flush()
        except Exception:
            if hasattr(self, "stream"):
                self.stream.close()
            for path in owned:
                path.unlink(missing_ok=True)
            raise

    def append(self, row: dict):
        if self.closed:
            raise ValueError("Measurement writer is closed")
        self.writer.writerow(serialize_row(row))
        self.stream.flush()
        self.count += 1
        self.valid_count += int(row["measurement_valid"])

    def _metadata_file(self):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.json_path.parent,
                                             prefix=".measurement-metadata-", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(self.metadata, stream, allow_nan=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.json_partial)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def finish(self, *, stopped_at_utc: str, duration_s: float, reason: str, error: str | None = None):
        if self.closed:
            return
        published = []
        try:
            self.stream.flush()
            os.fsync(self.stream.fileno())
            self.stream.close()
            self.metadata.update(completion="incomplete" if error else "complete",
                                 stopped_at_utc=stopped_at_utc, duration_s=duration_s,
                                 stop_reason=reason, error=error, sample_count=self.count,
                                 valid_sample_count=self.valid_count, gap_sample_count=self.count-self.valid_count)
            self._metadata_file()
            if error is None:
                for partial, final in ((self.csv_partial, self.csv_path), (self.json_partial, self.json_path)):
                    os.link(partial, final)
                    published.append(final)
                for partial in (self.csv_partial, self.json_partial):
                    try:
                        partial.unlink()
                    except OSError:
                        pass  # Complete files are already published; leftover links are harmless.
        except Exception as exc:
            for path in published:
                path.unlink(missing_ok=True)
            self.metadata.update(completion="incomplete", error=str(exc))
            try:
                self._metadata_file()
            except OSError:
                pass
            raise
        finally:
            self.stream.close()
            self.closed = True


@dataclass(frozen=True)
class LoggerStatus:
    state: str
    sample_count: int
    valid_sample_count: int
    duration_s: float
    error: str | None
    csv_path: Path


class MeasurementLogger:
    """Background sampler/writer with bounded input; the Qt layer controls lifecycle."""

    def __init__(self, path: Path, *, rate_hz=1.0, camera_identity=None,
                 observation=None, roi=None):
        self.started = time.monotonic()
        self.sampler = LatestSampler(rate_hz, self.started)
        self.sampler.update(observation, roi)
        metadata = session_metadata(created_at_utc=utc_now(), rate_hz=rate_hz,
                                    camera_identity=camera_identity, observation=observation)
        self.writer = IncrementalLogWriter(path, metadata)
        self._stop = threading.Event()
        self._reason = "operator_stop"
        self._state = "recording"
        self._error = None
        self._duration = 0.0
        self._thread = threading.Thread(target=self._run, name="measurement-csv-writer", daemon=True)
        self._thread.start()

    def update_latest(self, observation, roi):
        self.sampler.update(observation, roi)

    @property
    def status(self) -> LoggerStatus:
        duration = time.monotonic() - self.started if self._thread.is_alive() else self._duration
        return LoggerStatus(self._state, self.writer.count, self.writer.valid_count,
                            duration, self._error, self.writer.csv_path)

    def stop(self, reason="operator_stop", timeout=10.0):
        self._reason = reason
        self._stop.set()
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise TimeoutError("Measurement log is still finalizing; files remain incomplete")
        if self._error:
            raise OSError(self._error)
        return self.status

    def _run(self):
        try:
            while not self._stop.wait(max(0.0, self.sampler.next_due - time.monotonic())):
                row = self.sampler.sample_due(time.monotonic(), utc_now())
                if row is not None:
                    self.writer.append(row)
        except Exception as exc:
            self._error = str(exc)
            self._reason = "writer_error"
        finally:
            self._duration = time.monotonic() - self.started
            try:
                self.writer.finish(stopped_at_utc=utc_now(), duration_s=self._duration,
                                   reason=self._reason, error=self._error)
            except Exception as exc:
                self._error = str(exc)
            self._state = "error" if self._error else "complete"
