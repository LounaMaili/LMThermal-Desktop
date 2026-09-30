# Radiometric sequence recording v1

Native-equivalent temperatures; absolute physical accuracy not yet independently validated.

This format preserves time-dependent matrices. The separate CSV measurement
logger stores scalar values only; still-capture v1 remains unchanged. The two
live recorders are mutually exclusive to keep resource usage and lifecycle
simple. Offline still captures cannot be recorded as live sequences.

## Operator controls

Launch `./.venv/bin/python lmthermal_viewer.py`, connect and initialize using
the existing session controls. Scroll to **Radiometric recording**, select
1/2/5/10/25 Hz (default **5**), choose **Start recording…** and a new directory
name, then **Stop recording**. A recording may start before readiness; those
instants become gaps. Starting and stopping do not restart the camera or send
controls. Disconnect, camera termination/error, window close and opening an
offline still all finalize the recording. A timeout keeps the recorder attached
and prevents switching modes until finalization finishes.

Status shows duration, requested instants, committed matrices, gaps, drops,
queue depth and approximate chunk bytes. Pending matrices are not included in
the committed counter until their chunk is published. The 25 Hz option remains
experimental: nominal camera FPS is not a promise of 25 valid matrices per second.

## Bundle layout

```text
example.lmthermal/
  manifest.json
  timeline.csv
  chunks/
    chunk-000001.npz
    chunk-000002.npz
```

The recorder creates the destination exclusively and never overwrites an
existing bundle. Manifest `format` is `lmthermal-radiometric-recording`,
`version` is integer `1`. It records UTC start/stop, native dimensions 384×288,
native camera orientation, rate, units, compression, bounds, completion,
stop reason/error, counts, duration, accuracy warning and committed chunk
entries (filename, frame count, sequences, bytes, SHA-256).

There is **no original 384×292 transport** per frame in v1. Raw14 image data
and per-frame environmental/calibration inputs are retained. This differs
from a still capture's optional transport evidence. No physical calibration,
image rotation, palette settings or raw14-to-temperature arithmetic is added.

## Cadence, gaps and backpressure

A sampler thread reads one paired latest-observation/ROI slot at each monotonic
sample deadline. UI publication replaces the slot; intermediate camera frames
are not queued. It uses the existing ready/current/rejection rules. A repeated
observation, display frame, shutter transient or invalid observation creates
an empty-measurement timeline row, never a copy of the previous valid matrix.

The single sampler copies valid `MeasurementFrame.raw14` and `temperature_c`
bytes exactly; no lookup, thermometry or rendering runs in the recorder.
ROI statistics use the unchanged existing matrix helper. Palette/range changes
never enter the recording path. Each payload owns immutable bytes and settings.

The writer handoff holds at most **4** payloads. A full handoff drops the
current requested matrix immediately and journals `writer_overload`; it does
not block camera acquisition or grow a queue. Its current valid scalar summary
may still be present, with `measurement_valid=true`, `dropped=true`,
`stored=false`. Validity and successful storage are separate concepts.

If the sampler misses deadlines, it journals empty `sampler_missed_deadline`
rows for those scheduled instants (`state=not_observed`, `dropped=true`), then
samples only the newest observation. There is no matrix catch-up. Missed rows
use the scheduled elapsed time and an estimated corresponding UTC timestamp;
normal rows use the actual monotonic/UTC sampling time. UTC can follow system
clock corrections; monotonic elapsed time is the cadence reference.

`timeline.csv` retains the existing scalar logger columns: UTC, elapsed,
one-based sequence, state/status, measurement validity, frame UTC, high/low
Celsius/native coordinates, distinct literal/trailer center values/raw indices,
ROI half-open geometry/min/max/mean/count. It adds:

| Column | Meaning |
|---|---|
| `scheduled_elapsed_s` | Sequence / configured Hz |
| `dropped` | Writer overload or missed sampler deadline |
| `stored` | Matrix accepted for the named chunk/frame; committed status is determined by manifest |
| `chunk` | Safe relative chunk basename, or empty |
| `frame_index` | Zero-based valid-frame index within chunk, or empty |

Invalid rows retain active ROI geometry if known but no ROI statistics or
thermometry. Cleared ROI fields are empty. Gap count excludes dropped rows;
requested = committed valid + gaps + drops on normal completion. `valid_frames`
means committed matrices, not all observations with valid scalar summaries.
`uncommitted_frames` reports accepted but not yet committed payloads at the
last manifest update. In incomplete recordings, counters are a commit-time
snapshot; the timeline may contain newer rows.

## Chunk payload and bounds

Each NPZ contains:

| Array | Shape | Dtype |
|---|---|---|
| `temperature_c` | `[N,288,384]` | little-endian float32 |
| `raw14` | `[N,288,384]` | little-endian uint16 |
| `sequence` | `[N]` | little-endian uint64 |
| `metadata_utf8` | `[UTF-8 byte count]` | uint8 |

`metadata_utf8` is a JSON list with one entry per valid frame: full typed scalar
sample (including timestamps/ROI), parameter dataclass, lookup trace, dimensions,
orientation, transport flag and accuracy warning. Parameters are preserved
**per frame**, even when they change. Literal center is `(192,144)`; trailer
center remains a separate observation, not an invented center-region algorithm.

The default bound is **16 valid frames per chunk**, independent of cadence,
plus a final shorter chunk. This is about 10.62 MB of arrays; count bounds keep
memory fixed at high rates rather than holding 5–10 seconds of 25 Hz matrices.
Writer stack/compression/hash buffers add a bounded number of chunk-size copies;
the complete recording's matrices are never accumulated in RAM. Manifest and
timeline indexes grow with duration; this is not unlimited-duration storage.

## Publication and interruption

The sampler writes and flushes each timeline row before handing its payload
to the writer. The writer creates a temporary chunk in `chunks/`, flushes and
fsyncs it, then publishes using an exclusive same-directory hard link and
removes the temporary name. This has atomic final-name visibility and refuses
replacement. It fsyncs the timeline before atomically replacing the manifest
with the new committed chunk list. JSON uses finite numbers only.

Normal stop joins the sampler, fsyncs/closes the timeline, drains the bounded
handoff, commits a final partial chunk and publishes `completed=true`. Disk or
serialization errors leave `completed=false` with error information when the
manifest is writable. Finalization is off the GUI thread; Stop joins it with
a bounded wait. A slow filesystem can make Stop wait; live compression never
runs in the Qt/camera thread.

After process interruption, temporary chunks and unlisted published chunks
are ignored. Timeline references to uncommitted chunks expose no matrix.
Previously manifest-committed chunks remain readable. A final truncated CSV
row may be ignored only in an incomplete bundle. A missing/corrupt **committed**
chunk is an integrity error, never silently treated as a normal gap. Chunks are
fsynced, but directory entries are not explicitly fsynced; this provides process
interruption recovery, not a guarantee against sudden power loss/filesystem loss.
There is no resume/append operation; start a new bundle after interruption.

## Hardware-independent inspection

```python
from pathlib import Path
from radiometric_recording import open_recording

recording = open_recording(Path("example.lmthermal"))
print(recording.manifest["completed"])
for row in recording.timeline:
    print(row["sequence"], row["status"], row["stored"])
frame = recording.frame(1)  # None for a gap/drop/uncommitted reference
if frame is not None:
    print(frame.raw14.shape, frame.temperature_c.dtype, frame.metadata["parameters"])
```

The loader verifies format/version, hashes, finite values, safe filenames,
NPY headers before allocation, shapes/dtypes, sequence mappings, native
coordinates, matrix extrema/literal center/ROI summaries and completed counts.
It loads at most one chunk's matrices at a time; timeline and manifest indexes
are held in memory. Limits are 16 MiB per chunk file, 12 MiB per member,
1 MiB per metadata array and 8 MiB per manifest. Unsupported versions,
malformed/missing chunks and inconsistent metadata raise `RecordingError`.
No thermometry is recomputed. A playback GUI is deferred.

## Compression benchmark (saved data, 2026-09-30)

Alternating sanitized settled-room/hand matrices, local filesystem, NumPy NPZ;
write includes flush/fsync, load includes array equality checking. Decimal MB.
These are individual local timings, not a universal throughput guarantee.

| Valid frames | Array MB | Stored NPZ MB | Stored write/load s | Deflate NPZ MB | Deflate write/load s |
|---|---:|---:|---|---:|---|
| 16 | 10.617 | 10.618 | 0.0135 / 0.0332 | 2.848 | 0.2405 / 0.0263 |
| 25 | 16.589 | 16.590 | 0.0126 / 0.0283 | 4.464 | 0.3677 / 0.0386 |
| 50 | 33.178 | 33.179 | 0.0234 / 0.0431 | 8.900 | 0.7499 / 0.0730 |

The benchmark measures image/sequence arrays; production chunks also contain
per-frame UTF-8 metadata. Default **NPZ deflate** trades CPU for roughly 73%
smaller files on these scenes. The constructor supports uncompressed NPZ for
controlled diagnostics; v1 UI uses compression. Uncompressed matrix storage
alone is approximately 199 MB/min at 5 Hz, 398 MB/min at 10 Hz and 995 MB/min
at 25 Hz. Scene variation changes compressibility; live results follow below.

## Live HT-301 validation (2026-09-30)

Read-only discovery found the stable `video-index0` device with zoom readback
0. The existing official session initialization reached ready; recording did
not send additional controls. The ready viewer's median idle view counter was
25 FPS. Four compressed recordings finalized normally and passed the loader's
chunk hashes, metadata/matrix consistency and complete-count checks. Duration
below includes finalization; disk size includes manifest, timeline and chunks.

| Run | Seconds | Requested | Stored | Gaps | Drops | Disk MB | MB/s | MB/min | Median view FPS |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 5 Hz palm | 60.24 | 300 | 244 | 56 | 0 | 44.60 | 0.740 | 44.43 | 25 |
| 5 Hz ROI follow-up | 60.24 | 299 | 219 | 80 | 0 | 37.55 | 0.623 | 37.40 | 25 |
| 10 Hz | 30.43 | 300 | 219 | 81 | 0 | 31.82 | 1.046 | 62.73 | 25 |
| 25 Hz stress | 15.36 | 377 | 265 | 66 | 46 | 44.17 | 2.876 | 172.54 | 25 |

The first palm run had native ROI `[266,282) × [94,135)` with valid means
36.519–36.924 °C. Its operator background/clear actions occurred after the
60-second stop, so they are not claimed as part of that timeline. A follow-up
recorded ROI creation/drag updates, palm `[267,299) × [80,126)` (41 valid
means 35.613–35.664 °C), then 237 cleared-ROI samples. The subsequent 10 Hz
run retained the cooler ROI `[93,174) × [277,279)` with means
25.576–25.798 °C. These are native-equivalent observations, not independent
surface-temperature accuracy measurements. The operator confirmed the
interaction run; palette/auto/locked changes were exercised. Separate automated
same-frame rendering tests verify matrix/ROI independence from presentation.

Selected loaded matrices re-rendered recognizable warm-hand and cooler-room
scenes. Invalid rows retained no stale thermometry/matrix. Gap reasons included
`mixed_or_out_of_range_words`, `awaiting_live_evidence`, a single
`summary_mismatch` in the palm run and, at higher cadence,
`no_new_observation`. Existing measurement validity was retained unchanged.

Maximum queue depth sampled at approximately 1 Hz was 0/1/2/4 respectively;
this sampled maximum may miss short peaks. All 46 stress drops were explicit
`writer_overload` rows. The 25 Hz requested cadence was reached but **25 valid
stored matrices/s was not sustained**: about 17.25 stored/s including finalization.
The writer remained bounded and view FPS had median 25, range 21.1–26.1 during
stress (the one-second view counter is approximate). 5/10 Hz had zero storage
drops on this machine/scenes; gaps still prevent an unconditional valid-rate
promise. No disk saturation or unsafe USB faults were forced.

[Persistent aggregate report](diagnostics/2026-09-30-sequence-recording.json)
records counts, sampled FPS/backlog, ROI histories, status reasons, storage
rates, manifest and selected-array hashes. Live bundles and scene screenshots
remain outside Git in `/tmp/lmthermal-sequence-live-20260930/`; they contain
operator scene data and are not sanitized regression fixtures. Pure journal
ordering/fsync and loader consistency were hardened after the initial viewer
launch and covered by hardware-independent tests; these changes do not alter
acquisition, initialization, sampling rates or matrix arithmetic.

The operator closed the viewer normally after the runs; its process exited
successfully. A fresh read-only camera reopen returned a complete 224,256-byte
frame, with zoom still 32768, and released the handle again. Final checks:
128 hardware-independent tests passed, Python compilation passed and
`git diff --check` passed.
