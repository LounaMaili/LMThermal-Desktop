# Live measurement time series v1

`measurement_logger.py` records small tabular measurements, not radiometric
video. It consumes existing live `FrameObservation` / `MeasurementFrame`
objects, copies their validated readings, and reuses `roi_statistics` for the
selected matrix slice. It does not acquire frames, send camera controls,
build a lookup table, process raw14 into Celsius, or use rendered RGB values.
The capture format v1 and native thermometry/session validity rules are unchanged.

## Operator workflow

Connect the camera and use the existing explicit radiometric initialization.
Select **Live measurement log** rate (default **1 Hz**), click **Start logging**
and choose an existing writable directory and a new `.csv` filename. Starting
recording does not restart the camera or session. The controls show duration,
all emitted sample count, valid sample count and target path. The selected
rate is fixed for that recording. **Stop logging** flushes/closes and finalizes
the pair. Disconnect, normal window close, camera termination/failure, or
opening a saved capture also stop the logger. Offline captures cannot start
logs; reconnecting never starts recording automatically.

Starting during a non-ready live state is permitted: the log contains gaps
until valid measurements appear. No current thermometry is fabricated.
Move/redraw/clear the ROI while recording; each subsequent sample uses the
current native geometry. Palette, Celsius auto/locked bounds and resize affect
only presentation. They do not change logged values from the same frame.

## Sampling and responsiveness

Supported rates are 0.5, 1, 2, 5 and 10 Hz. A dedicated sampler/writer thread
uses `time.monotonic()` deadlines; the first sample is due one interval after
start. The Qt view publishes only its newest observation and current immutable
ROI into one lock-protected slot. This also publishes invalid observations,
so a gap cannot silently reuse the last valid frame. There is no frame queue.
At a deadline the thread snapshots that slot, creates one row, and writes it
incrementally. ROI arithmetic uses the existing unsmoothed matrix-slice helper.
Disk I/O and sampling are outside the Qt rendering loop.

A delayed scheduler emits one row for the actual sampling instant, skips
missed instants and resumes the original cadence without a catch-up burst.
`elapsed_s` records actual monotonic elapsed time, not a fabricated deadline.
`timestamp_utc` records sampling wall-clock UTC; `frame_measured_at_utc`
separately identifies the source measurement. Sample sequence increments for
every emitted row, including gaps. It does not count camera frames.

A numerical row requires the current observation and its measurement to be
`radiometric_ready`, describe the same raw frame, and have no rejection or
repeated-image flag. Those are existing validity checks, not a new readiness
algorithm. A repeated reference with no new observation since the previous
sample is a logger gap (`no_new_observation`), even if its old state was ready.
Invalid/display/shutter/unsettled observations produce `measurement_valid=false`
and empty temperature, index and measurement-coordinate cells. Available ROI
geometry can remain in a gap, but its statistics/count are empty. Recovery to a
new ready observation automatically resumes numerical rows. Non-finite values
are rejected; no `NaN` or `Infinity` values are serialized.

## CSV schema

The header is stable for identifier `lmthermal-measurement-time-series`,
version **1**. Files use UTF-8, comma-separated fields, standard CSV quoting,
locale-independent decimal points and empty cells for unavailable values.
Booleans are the literal strings `true` / `false`.

| Columns | Meaning |
| --- | --- |
| `timestamp_utc`, `elapsed_s`, `sequence` | ISO 8601 UTC sampling timestamp, monotonic elapsed seconds, one-based row sequence |
| `state`, `status`, `measurement_valid` | Observed session state; `valid`, rejection/state reason, or `no_new_observation`; Boolean validity |
| `frame_measured_at_utc` | Source measurement UTC timestamp; empty in gaps |
| `high_c`, `high_x_px`, `high_y_px` | Whole-image high native-equivalent Celsius and native coordinates |
| `low_c`, `low_x_px`, `low_y_px` | Whole-image low native-equivalent Celsius and native coordinates |
| `literal_center_c`, `literal_center_raw14` | Literal pixel `(192,144)` temperature and original raw14 index |
| `trailer_center_c`, `trailer_center_raw14` | Separate trailer-center temperature and raw14 index; no center-region algorithm inferred |
| `roi_x1_px`, `roi_y1_px`, `roi_x2_px`, `roi_y2_px` | Current native half-open ROI `[x1,x2) × [y1,y2)`; empty when no ROI |
| `roi_min_c`, `roi_max_c`, `roi_mean_c`, `roi_pixel_count` | Current valid-frame ROI min/max, float64 mean and pixel count; empty without a ROI or in a gap |

Every coordinate uses the unmodified 384 × 288 image. Temperature values are
native-equivalent Celsius. ROI fields describe the region used for that row;
there is no interpolation or smoothing. The literal and trailer centers are
intentionally distinct. Extrema are validated measurement values, not extrema
of palette brightness.

## Sidecar and file lifecycle

A normal stop produces `<name>.csv` and `<name>.json`. The JSON contains:

- `format`, integer `version`, `completion: complete`;
- UTC creation/stop times, actual monotonic `duration_s`, and `stop_reason`;
- camera identity when available, native dimensions/orientation;
- `rate_hz`, `sampling_interval_s`, units and ordered column definitions;
- accuracy warning and documented gap policy;
- settings/calibration `parameters_at_start` and `lookup_trace_at_start` when
  logging started on a ready frame, otherwise null;
- total, valid and gap sample counts, and null `error` on a clean completion.

The settings snapshot is session-start evidence, not a claim that calibration
is physically validated or that parameters can never change later.

Active files are `<name>.csv.incomplete` and `<name>.json.incomplete`. They are
created exclusively; existing final or incomplete destinations are protected.
CSV is written/flushed incrementally without buffering a recording in memory.
The initial incomplete sidecar describes the recording but its counts remain
initial until finalization. On normal stop the writer flushes/fsyncs/closes
CSV, updates/fsyncs metadata, publishes CSV without overwriting and publishes
complete JSON last. Temporary/incomplete links are then removed. Publication
failure rolls back new final names and retains identifiable incomplete files
with an error when writable. Already published complete files are not removed
merely because cleanup of an extra incomplete link fails.

A process or machine crash can leave incomplete files or CSV without final
JSON. Treat a session as complete only when the final CSV and JSON both exist
and metadata says `completion: complete`. Interrupted rows may be inspectable,
but final counts/duration are not guaranteed. Finalization errors are shown in
the UI. If a writer is still stopping, closing/reconnecting/loading another
mode waits for its completion rather than abandoning it. No recovery/import
UI or full-frame recorder is introduced here.

## Live HT-301 smoke test (2026-09-30)

Stable `video-index0` discovery and read-only `zoom_absolute=0` preceded the
existing explicit initialization. The first 1 Hz run produced 30 samples in
30.20 s: 24 valid and 6 natural unsettled gaps with automatic recovery. Its
view counter ranged 23–26.1 FPS, median 25. A second operator-started/stopped
run produced 32 samples, 31 valid and one gap, at median 25 FPS. Although the
operator initially reported hand/ROI completion, all rows in these initial
files had empty ROI geometry; they establish room sampling/gap/lifecycle
behavior only. They are not used as hand or ROI evidence.

A focused retry waited for a visibly present hand and a rectangle fully inside
it before logging. A screenshot and saved CSV independently confirmed geometry
`[164,231) × [111,184)` (4,891 pixels). The operator then moved the rectangle
wholly onto visible cooler background, `[166,273) × [0,34)` (3,638 pixels),
keeping the hand visible nearby. Only after background rows were verified did
the operator clear ROI and remove the hand. The same log contains those later
empty ROI fields. Source values and ROI statistics were unchanged during a
same-frame palette/locked-range/resize check.

The focused run lasted 222.80 s rather than roughly 30 s because each physical
placement was independently checked before the next instruction. It produced
222 rows: 171 valid, 51 explicit unsettled gaps; the entire CSV is 54,076 bytes.
The geometry phases were 74 hand rows (56 valid), 100 background rows (77
valid), and 48 cleared rows (38 valid). Median native-equivalent ROI statistics
across valid rows were:

| Region | ROI min °C | ROI mean °C | ROI max °C |
| --- | ---: | ---: | ---: |
| Visible hand | 35.6738 | 36.5884 | 37.4204 |
| Cooler background | 27.7032 | 27.9505 | 28.2137 |

The median mean difference was 8.6379 °C. Every valid hand-ROI mean exceeded
every valid background-ROI mean for these sequential samples; this is not a
simultaneous controlled-reference comparison or independent physical accuracy
validation. The two centers remained separate in CSV.

Independent CSV/JSON inspection confirmed contiguous sequences, UTC and
monotonic timestamps, matching total/valid/gap counts, no non-finite values,
empty measured cells during invalid states, changed per-row ROI geometry,
and clean `completion: complete` publication. Adjacent sampling intervals in
the focused run ranged 0.9619–1.0356 s, median 1.0000 s. Initial runs were
0.9950–1.0051 s. There were no catch-up rows. The focused interactive run,
which additionally grabbed a window PNG every second for scene verification,
had median displayed view FPS 21 (minimum 17, with counter spikes during
interaction/resize). This is not a controlled logger overhead benchmark;
the uninstrumented initial runs remained approximately 25 FPS. No protocol,
initialization, palette arithmetic or thermometry changes were needed.
Temporary logs/screenshots are deliberately kept outside Git.

## Independent inspection

```python
import csv
import json
from pathlib import Path

path = Path("measurements.csv")
metadata = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
assert metadata["format"] == "lmthermal-measurement-time-series"
assert metadata["version"] == 1 and metadata["completion"] == "complete"
with path.open(newline="", encoding="utf-8") as stream:
    rows = list(csv.DictReader(stream))
assert len(rows) == metadata["sample_count"]
valid = [row for row in rows if row["measurement_valid"] == "true"]
```

Native-equivalent temperatures; absolute physical accuracy not yet independently validated.

## Coexistence with radiometric recording

The new [sequence recorder](RADIOMETRIC_RECORDING_FORMAT.md) stores raw14 and
Celsius matrices in chunks and has its own timeline/manifest. In v1, CSV logging
and sequence recording are mutually exclusive in the viewer; stop one before
starting the other. CSV schema and completion semantics remain unchanged.
