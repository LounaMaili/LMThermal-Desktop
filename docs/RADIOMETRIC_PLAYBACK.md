# Offline radiometric sequence playback

Native-equivalent temperatures; absolute physical accuracy not yet independently validated.

## Open and inspect

Launch without camera acquisition:

```bash
./.venv/bin/python lmthermal_viewer.py --recording /path/to/example.lmthermal
```

Or choose **Open radiometric recording** and select the bundle directory. The
viewer finalizes active CSV/matrix recording and releases the live camera
before opening. It closes any offline still. Connecting a camera or opening a
still closes playback; old worker results cannot revive another mode.

The focused playback panel beneath the image provides:

- slider covering **all timeline samples**, including gaps and drops;
- beginning/end, previous/next, Play/Pause;
- 0.5×, 1× (default), 2× and 4× speed;
- sample count, original sequence, UTC, elapsed time and entry status;
- recorded versus inspection ROI label;
- Close recording.

Scrubbing or stepping pauses playback. Playback follows recorded elapsed
monotonic timestamps, including nonuniform intervals. A timer selects the
latest entry due at playback time; rendering can skip presentation frames
rather than accumulating overdue jobs. Slider and loading status follow the
selected time even while a chunk is loading. Playing from the end restarts at
the first entry. The duration is the last recoverable timeline timestamp;
playback from the first sample spans last minus first elapsed time.

A valid matrix uses existing per-frame 2nd/98th-percentile Auto range or exact
manual Celsius bounds and the existing palettes/legend. There is no temporal
range smoothing. Hover reads original native x/y, raw14 and stored Celsius;
high/low use recorded values/coordinates. Literal `(192,144)` and trailer center
remain separate. Native orientation and coordinate mapping are unchanged.

Each selection restores that entry's recorded ROI geometry. Drawing/replacing
or clearing creates an **inspection ROI** on the selected matrix only. Its
statistics use the current stored Celsius slice. Moving to another sample
restores its recorded ROI; inspection edits are not persistent and do not
change any recording file. Palette/range controls never change matrices,
extrema, centers or ROI arithmetic.

## Gaps, drops, loading and interruption

| Entry/status | Presentation |
|---|---|
| Valid committed matrix | Image, legend, cursor/centers/extrema/ROI available |
| Invalid camera/session gap | Blank image, reason, all measurements unavailable |
| Recorder drop | Blank image, explicit drop reason such as `writer_overload` |
| Uncommitted frame in incomplete bundle | Blank image, uncommitted/unavailable label |
| Loading selected sample | Blank loading view, no previous readings |
| Corrupt or missing committed chunk | Integrity error, no current image/readings |

A drop may have a valid scalar summary in its original timeline, but playback
never exposes that as a current measurement without a stored committed
matrix. No previous valid frame is substituted. A naturally skipped gap during
real-time playback remains reachable with the slider or single-sample steps.

Opening validates the v1 manifest, finite/nondecreasing timeline times,
valid/gap/drop flags, mappings, all committed hashes, array layouts and
metadata consistency before enabling navigation. An empty recording is shown
explicitly and cannot play. Validation runs in the loader thread.

`completed=false` is labeled **INCOMPLETE — recovered committed chunks**.
Validated committed frames remain inspectable. Unlisted temporary/orphan
chunks are ignored; references to uncommitted chunks remain visible but expose
no matrix. A malformed final CSV tail may be ignored by the existing incomplete
loader. A corrupt/missing committed chunk is an integrity error, rather than a
silent recovery gap. Playback does not resume or repair the recorder, and
never marks an incomplete recording complete.

## Architecture and memory

`radiometric_playback.py` contains the camera-independent `PlaybackModel`,
immutable timeline entries and `PlaybackFrame` values. Navigation/clock methods
perform no disk I/O. Valid frame access copies exact stored bytes into owned
read-only uint16/float32 matrix views; no thermometry or LUT runs in playback.
The extended recording loader retains v1 layout and recording semantics.

`PlaybackWorker` owns validation and decompression outside Qt's main thread.
It has one replaceable pending request and one coalesced result notification;
tokens and sender checks reject obsolete loads after scrubbing or mode changes.
The window reuses existing rendering, cursor, ROI, legend and export panels.

As of the LMTX import milestone, a selected validated `PlaybackFrame` adapts
into `OfflineMeasurement` for these shared panels. Exact Celsius/raw14,
recorded metadata and separate literal/trailer centers are preserved.
Original transport remains absent in recording v1. Offline extrema derive
from the stored matrix; recorded camera/trailer observations remain metadata.
The adapter does not change timeline, cache, recovery, source validity or
integrity checks. [LMTX stills](LMTX_IMPORT.md) use the same generic analysis
model after their separate strict reader succeeds.

Opening scans committed chunks sequentially for full integrity validation,
then discards their matrices. Subsequent frame access is lazy and uses a
**two-chunk LRU cache** (one chunk is also supported by the pure model). Scrubs
within a cached chunk do not decompress it again. Eviction precedes a cold
load, and each cold read rechecks the chunk hash/structure. Timeline/manifest
indexes grow with recording duration, but image data never accumulate for the
whole sequence. Two full 16-frame caches contain about **21.23 MB** of arrays,
plus metadata, selected-frame copies and temporary load/render buffers.

## Save a selected rendering

For a valid sample choose **Save selected frame PNG…**. It freezes the selected
stored frame and current palette/effective bounds before the destination
dialog, then saves a clean native 384×288 PNG without overlays. The suggested name includes the sample sequence in the bundle’s parent
directory, outside the bundle. Existing files and destinations anywhere inside
the source bundle are refused. Gap/drop
entries disable export. No source hashes or recording files change.

The shared `save_offline_png` path uses exclusive new-file creation, sync
where supported and cleanup of its own ordinary failed write. It retains
whole-bundle protection and avoids a Unix hard-link requirement. This does
not promise atomic publication or power-loss safety on every filesystem.

Extraction to still radiometric v1 is deferred: the existing still export path
requires live transport evidence that recording v1 does not retain. Playback
must not fabricate transport or rerun thermometry to fill it in.

## Validation, 2026-09-30

The original palm/ROI/10 Hz/25 Hz live bundles were formerly stored under
`/tmp/lmthermal-sequence-live-20260930/`. That directory was gone at this task's
start, and no copies were found in the checked project/temporary/document/
video/download folders. Their earlier aggregate report remains in Git, but
it cannot reconstruct their actual frames. **Playback against those original
live bundles remains unverified.**

Instead, four explicitly named fixture-derived histories use existing
sanitized room/hand frames, synthesized elapsed timing, session gaps,
simulated overload drops, ROI creation/change/clear, and 16-frame chunks.
They are validation data, not new camera acquisitions or copies of the missing
recordings. They remain under `local-recordings/playback-validation/` (ignored
by Git) so they survive temporary-directory cleanup. The prepared payloads
were computed before playback; the viewer only loads their stored matrices.

| Fixture history | Entries | Valid / gap / drop | Open validation ms | Cold frame median ms | Cached median ms | Qt selection median/max ms |
|---|---:|---|---:|---:|---:|---|
| 5 Hz palm | 65 | 54 / 6 / 5 | 91.7 | 20.4 | 0.060 | 5.23 / 23.67 |
| 5 Hz ROI | 65 | 54 / 6 / 5 | 58.7 | 17.3 | 0.039 | 5.37 / 22.49 |
| 10 Hz | 101 | 84 / 9 / 8 | 90.2 | 16.8 | 0.039 | 5.35 / 23.61 |
| 25 Hz | 145 | 122 / 13 / 10 | 126.2 | 17.6 | 0.044 | 5.37 / 22.89 |

Timings are local observations, not performance guarantees. Opening scans all
committed chunks; cold frame timing includes verified decompression and owned
frame creation. Cached frame timing is pure model access. Qt selection includes
worker/result handling and actual rendering/labels. The separate Qt opening
measurements were 91–145 ms. Cache use never exceeded two chunks or 21.23 MB
of arrays. The offscreen Qt validation process peaked near 143 MiB RSS,
including Python, OpenCV, NumPy, Qt, validation/render buffers and caches.

1× Qt playback spans were 12.998/12.999/10.313/6.217 s against recorded spans
12.992/12.992/10.300/6.192 s. A 10 ms event-loop heartbeat remained below
15.2 ms during these playback runs. Valid/gap/drop selections were visited,
chunk boundaries crossed, PNGs saved and all source hashes retained.
The operator confirmed the 5 Hz hover/resize/ROI/palette/range/play/pause/step
and gap behavior in the visible camera-free viewer.

See [persistent playback measurements](diagnostics/2026-09-30-sequence-playback.json).
No physical surface-temperature accuracy validation is implied.

In the final 25 Hz operator check, the operator could not locate a PNG. The
same UI save action was then exercised programmatically on valid sample 101,
saving `local-recordings/playback-validation/operator-rendered.png` (144,944
bytes, SHA-256 `c14e554a2e7a7e63d31ab572a73b3a294937e23e85a234aaf33e6bb86f288de7`).
The PNG was displayed and source hashes remained unchanged. The playback
button now explicitly says **Save selected frame PNG…**, with a suggested
sample-number filename outside the source bundle. The visible viewer processes
exited normally; no camera was opened for these playback checks.

Final checks: 147 hardware-independent tests passed, all 42 project Python
files compiled successfully, and `git diff --check` passed. Research repository
was unchanged; no new camera/protocol/thermometry fact was introduced.
