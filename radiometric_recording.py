"""Versioned bounded NPZ chunks and hardware-independent recording inspection."""

import csv
from dataclasses import asdict, dataclass, fields
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import zipfile

import numpy as np

from measurement_logger import COLUMNS, create_sample, serialize_row
from measurement_baseline import FrameParameters
from radiometric_export import ACCURACY_WARNING
from radiometric_session import FrameObservation

FORMAT_ID = "lmthermal-radiometric-recording"
FORMAT_VERSION = 1
CHUNK_FRAMES = 16
HANDOFF_FRAMES = 4
RATES = (1, 2, 5, 10, 25)
SHAPE = (288, 384)
TIMELINE_COLUMNS = COLUMNS + ("scheduled_elapsed_s", "dropped", "stored", "chunk", "frame_index")


@dataclass(frozen=True)
class FramePayload:
    sequence: int
    raw14_bytes: bytes
    temperature_bytes: bytes
    metadata_json: str

    @property
    def raw14(self):
        return np.frombuffer(self.raw14_bytes, dtype="<u2").reshape(SHAPE)

    @property
    def temperature_c(self):
        return np.frombuffer(self.temperature_bytes, dtype="<f4").reshape(SHAPE)

    @property
    def metadata(self):
        return json.loads(self.metadata_json)


def snapshot_frame(observation: FrameObservation, row: dict) -> FramePayload:
    """Own exact matrices and per-frame settings, without retaining transport."""
    if not isinstance(observation, FrameObservation) or not row["measurement_valid"]:
        raise ValueError("Only current valid live measurements have recording payloads")
    validated = create_sample(observation, None, timestamp_utc=row["timestamp_utc"],
                              elapsed_s=row["elapsed_s"], sequence=row["sequence"])
    if not validated["measurement_valid"]:
        raise ValueError("Observation is not radiometrically ready")
    measurement = observation.measurement
    raw, temperature = measurement.raw14, measurement.temperature_c
    if (raw.shape != SHAPE or raw.dtype != np.dtype("uint16") or
            temperature.shape != SHAPE or temperature.dtype != np.dtype("float32") or
            int(raw.max()) >= 0x4000 or not np.isfinite(temperature).all()):
        raise ValueError("Invalid recording matrix shape/dtype/values")
    metadata = {"summary": row, "parameters": asdict(measurement.parameters),
                "lookup_trace": measurement.lookup_trace, "native_width_px": 384,
                "native_height_px": 288, "orientation": "native_camera_coordinates",
                "accuracy_warning": ACCURACY_WARNING, "transport_preserved": False}
    return FramePayload(row["sequence"], raw.astype("<u2", copy=False).tobytes(),
                        temperature.astype("<f4", copy=False).tobytes(),
                        json.dumps(metadata, allow_nan=False))


def atomic_json(path: Path, data: dict):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".manifest-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, allow_nan=False, indent=2)
            stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class ChunkWriter:
    """One bounded batch; chunk publication precedes the manifest commit."""

    def __init__(self, directory: Path, *, compression=True, chunk_frames=CHUNK_FRAMES):
        if type(chunk_frames) is not int or not 1 <= chunk_frames <= CHUNK_FRAMES:
            raise ValueError("Chunk frame bound must be 1..16")
        self.directory = Path(directory)
        self.compression = compression
        self.chunk_frames = chunk_frames
        self.pending = []
        self.chunks = []
        self.bytes_written = 0

    def append(self, frame: FramePayload):
        self.pending.append(frame)
        return self.commit() if len(self.pending) == self.chunk_frames else None

    def commit(self):
        if not self.pending:
            return None
        name = f"chunk-{len(self.chunks)+1:06d}.npz"
        target = self.directory / name
        if target.exists():
            raise FileExistsError(target)
        arrays = {"temperature_c": np.stack([f.temperature_c for f in self.pending]),
                  "raw14": np.stack([f.raw14 for f in self.pending]),
                  "sequence": np.array([f.sequence for f in self.pending], dtype="<u8"),
                  "metadata_utf8": np.frombuffer(json.dumps([f.metadata for f in self.pending],
                                                              allow_nan=False).encode(), dtype=np.uint8)}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", dir=self.directory,
                                             prefix=".chunk-", suffix=".incomplete", delete=False) as stream:
                temporary = Path(stream.name)
                (np.savez_compressed if self.compression else np.savez)(stream, **arrays)
                stream.flush(); os.fsync(stream.fileno())
            # Exclusive hard-link publication, same atomic visibility as rename.
            os.link(temporary, target)
            content = target.read_bytes()
            entry = {"file": name, "frames": len(self.pending), "sha256": hashlib.sha256(content).hexdigest(),
                     "bytes": len(content), "sequences": [f.sequence for f in self.pending]}
            self.chunks.append(entry)
            self.bytes_written += len(content)
            self.pending.clear()
            return entry
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


class RecordingError(ValueError):
    """Recording structure, integrity or version is invalid."""


def _require(condition, message):
    if not condition:
        raise RecordingError(message)


def _finite_json(content):
    def reject(value):
        raise RecordingError(f"Non-finite JSON value: {value}")
    value = json.loads(content, parse_constant=reject)
    # Also reject numeric overflow such as 1e999, which parse_constant does not see.
    json.dumps(value, allow_nan=False)
    return value


def _load_chunk(path, entry):
    _require(path.stat().st_size == entry["bytes"] and entry["bytes"] <= 16 * 1024 * 1024,
             "Wrong or oversized chunk file")
    content = path.read_bytes()
    _require(hashlib.sha256(content).hexdigest() == entry["sha256"], "Chunk SHA-256 mismatch")
    count = entry["frames"]
    _require(type(count) is int and 1 <= count <= CHUNK_FRAMES, "Invalid chunk frame count")
    expected = {"temperature_c.npy": ((count, *SHAPE), np.dtype("<f4")),
                "raw14.npy": ((count, *SHAPE), np.dtype("<u2")),
                "sequence.npy": ((count,), np.dtype("<u8"))}
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        _require(len(entries) == 4 and {e.filename for e in entries} == set(expected) | {"metadata_utf8.npy"},
                 "Unexpected chunk members")
        for member in entries:
            _require(member.file_size <= 12 * 1024 * 1024, "Oversized NPY payload")
            with archive.open(member) as stream:
                version = np.lib.format.read_magic(stream)
                _require(version in ((1, 0), (2, 0)), "Unsupported NPY version")
                reader = np.lib.format.read_array_header_1_0 if version == (1, 0) else np.lib.format.read_array_header_2_0
                shape, fortran, dtype = reader(stream)
                if member.filename == "metadata_utf8.npy":
                    _require(len(shape) == 1 and shape[0] <= 1024*1024 and dtype == np.dtype("uint8"),
                             "Invalid metadata payload")
                else:
                    _require((shape, dtype) == expected[member.filename] and
                             dtype.str == expected[member.filename][1].str and not fortran,
                             "Wrong chunk shape/dtype/layout")
    with np.load(path, allow_pickle=False) as data:
        arrays = {key: data[key] for key in data.files}
    _require(np.isfinite(arrays["temperature_c"]).all() and int(arrays["raw14"].max()) < 0x4000,
             "Invalid chunk matrix values")
    _require(arrays["sequence"].tolist() == entry["sequences"], "Chunk sequences differ from manifest")
    metadata = _finite_json(arrays.pop("metadata_utf8").tobytes())
    _require(isinstance(metadata, list) and len(metadata) == count, "Wrong per-frame metadata count")
    for i, value in enumerate(metadata):
        summary = value["summary"]
        _require(value["native_width_px"] == 384 and value["native_height_px"] == 288 and
                 value["orientation"] == "native_camera_coordinates" and value["transport_preserved"] is False
                 and value["accuracy_warning"] == ACCURACY_WARNING and
                 summary["sequence"] == entry["sequences"][i] and summary["measurement_valid"] is True,
                 "Invalid per-frame metadata")
        _require(isinstance(value["parameters"], dict) and
                 set(value["parameters"]) == {f.name for f in fields(FrameParameters)} and
                 isinstance(value["lookup_trace"], dict) and bool(value["lookup_trace"]),
                 "Missing per-frame settings")
        for key in ("high", "low"):
            x, y = summary[key+"_x_px"], summary[key+"_y_px"]
            _require(type(x) is int and type(y) is int and 0 <= x < 384 and 0 <= y < 288,
                     "Invalid extrema coordinates")
            _require(np.isclose(summary[key+"_c"], arrays["temperature_c"][i,y,x], atol=1e-4),
                     "Stored extremum differs from matrix")
            extrema = arrays["temperature_c"][i].max() if key == "high" else arrays["temperature_c"][i].min()
            _require(np.isclose(summary[key+"_c"], extrema, atol=1e-4), "Wrong matrix extremum")
        _require(summary["literal_center_raw14"] == int(arrays["raw14"][i,144,192]) and
                 np.isclose(summary["literal_center_c"], arrays["temperature_c"][i,144,192], atol=1e-4),
                 "Literal center differs from matrix")
        roi_keys = ("roi_x1_px", "roi_y1_px", "roi_x2_px", "roi_y2_px")
        geometry = [summary[k] for k in roi_keys]
        if any(v is not None for v in geometry):
            x1, y1, x2, y2 = geometry
            _require(all(type(v) is int for v in geometry) and 0 <= x1 < x2 <= 384 and 0 <= y1 < y2 <= 288,
                     "Invalid ROI geometry")
            region = arrays["temperature_c"][i,y1:y2,x1:x2]
            _require(summary["roi_pixel_count"] == region.size and
                     np.isclose(summary["roi_min_c"], region.min(), atol=1e-4) and
                     np.isclose(summary["roi_max_c"], region.max(), atol=1e-4) and
                     np.isclose(summary["roi_mean_c"], region.mean(dtype=np.float64), atol=1e-4),
                     "ROI statistics differ from matrix")
        else:
            _require(all(summary[k] is None for k in ("roi_min_c", "roi_max_c", "roi_mean_c", "roi_pixel_count")),
                     "Statistics without ROI")
    return arrays, metadata


@dataclass(frozen=True)
class Recording:
    directory: Path
    manifest: dict
    timeline: tuple

    def load_chunk(self, name):
        """Validate one manifest-committed chunk for a bounded external cache."""
        entry = next((c for c in self.manifest["chunks"] if c["file"] == name), None)
        if entry is None:
            raise RecordingError("Chunk is not manifest-committed")
        try:
            return _load_chunk(self.directory/"chunks"/name, entry)
        except RecordingError:
            raise
        except (OSError, ValueError, TypeError, KeyError, IndexError, zipfile.BadZipFile, EOFError) as exc:
            raise RecordingError(f"Cannot load committed chunk: {exc}") from exc

    def frame(self, sequence: int) -> FramePayload | None:
        row = next((r for r in self.timeline if int(r["sequence"]) == sequence), None)
        if row is None:
            raise KeyError(sequence)
        if row["stored"] != "true":
            return None
        entry = next((c for c in self.manifest["chunks"] if c["file"] == row["chunk"]), None)
        if entry is None:
            return None  # Visible uncommitted reference in an interrupted recording.
        arrays, metadata = self.load_chunk(entry["file"])
        index = int(row["frame_index"])
        return FramePayload(sequence, arrays["raw14"][index].tobytes(),
                            arrays["temperature_c"][index].tobytes(), json.dumps(metadata[index], allow_nan=False))


def open_recording(directory: Path) -> Recording:
    """Ignore unlisted temporary chunks; validate every manifest-committed chunk."""
    try:
        root = Path(directory)
        manifest_path = root/"manifest.json"
        _require(manifest_path.stat().st_size <= 8*1024*1024, "Oversized manifest")
        manifest = _finite_json(manifest_path.read_bytes())
        _require(manifest["format"] == FORMAT_ID and type(manifest["version"]) is int and
                 manifest["version"] == FORMAT_VERSION, "Unsupported recording format/version")
        _require(type(manifest["completed"]) is bool and manifest["native_width_px"] == 384 and
                 manifest["native_height_px"] == 288 and manifest["rate_hz"] in RATES,
                 "Invalid manifest dimensions/rate/completion")
        _require(manifest["orientation"] == "native_camera_coordinates" and
                 manifest["accuracy_warning"] == ACCURACY_WARNING and manifest["transport_preserved"] is False,
                 "Invalid manifest measurement semantics")
        mapping = {}
        summaries = {}
        names = set()
        for entry in manifest["chunks"]:
            _require(re.fullmatch(r"chunk-[0-9]{6}\.npz", entry["file"]) and entry["file"] not in names,
                     "Unsafe or duplicated chunk path")
            names.add(entry["file"])
            arrays, metadata = _load_chunk(root/"chunks"/entry["file"], entry)
            del arrays  # Keep only scalar summaries before loading the next chunk.
            for index, sequence in enumerate(entry["sequences"]):
                _require(type(sequence) is int and sequence > 0 and sequence not in mapping,
                         "Duplicated/invalid sequence")
                mapping[sequence] = (entry["file"], index)
                summaries[sequence] = serialize_row(metadata[index]["summary"])
        rows = []
        with (root/"timeline.csv").open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            _require(tuple(reader.fieldnames or ()) == TIMELINE_COLUMNS, "Wrong timeline schema")
            for row in reader:
                if None in row or None in row.values():
                    _require(not manifest["completed"], "Truncated complete timeline")
                    break  # A final partial row after interruption cannot erase prior rows.
                sequence = int(row["sequence"])
                _require(sequence == len(rows)+1 and row["measurement_valid"] in ("true","false")
                         and row["stored"] in ("true","false") and row["dropped"] in ("true","false"),
                         "Invalid timeline sequence/flags")
                for key in ("elapsed_s", "scheduled_elapsed_s"):
                    _require(np.isfinite(float(row[key])) and float(row[key]) >= 0, "Invalid timeline time")
                if row["stored"] == "true":
                    location = (row["chunk"], int(row["frame_index"]))
                    _require(row["measurement_valid"] == "true" and row["dropped"] == "false",
                             "Invalid stored sample flags")
                    _require(mapping.get(sequence) == location or
                             (not manifest["completed"] and row["chunk"] not in names),
                             "Timeline mapping differs from committed chunk")
                else:
                    _require(not row["chunk"] and not row["frame_index"], "Gap references a matrix")
                if sequence in summaries:
                    _require(all(row[k] == str(summaries[sequence][k]) for k in COLUMNS),
                             "Timeline summary differs from chunk metadata")
                elif row["measurement_valid"] == "false":
                    _require(all(row[k] == "" for k in COLUMNS if k.startswith(("high_", "low_", "literal_center_", "trailer_center_"))
                                 or k in ("roi_min_c", "roi_max_c", "roi_mean_c", "roi_pixel_count", "frame_measured_at_utc")),
                             "Invalid sample contains thermometry")
                _require(not rows or float(row["elapsed_s"]) >= float(rows[-1]["elapsed_s"]),
                         "Timeline elapsed times must be nondecreasing")
                if row["stored"] == "false" and row["measurement_valid"] == "true":
                    _require(row["dropped"] == "true" and row["status"] == "writer_overload",
                             "Unstored valid sample must be an explicit overload drop")
                if row["dropped"] == "true":
                    _require(row["status"] in ("writer_overload", "sampler_missed_deadline"),
                             "Unknown dropped sample reason")
                rows.append(row)
        if manifest["completed"]:
            _require(len(rows) == manifest["requested_samples"] and len(mapping) == manifest["valid_frames"] and
                     sum(r["stored"]=="true" for r in rows) == manifest["valid_frames"] and
                     sum(r["dropped"]=="true" for r in rows) == manifest["dropped_samples"] and
                     sum(r["measurement_valid"]=="false" and r["dropped"]=="false" for r in rows) == manifest["gap_samples"],
                     "Manifest counts differ from timeline")
        return Recording(root, manifest, tuple(rows))
    except RecordingError:
        raise
    except (OSError, ValueError, TypeError, KeyError, IndexError, zipfile.BadZipFile, EOFError) as exc:
        raise RecordingError(f"Incomplete or malformed recording: {exc}") from exc
