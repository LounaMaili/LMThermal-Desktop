# LMThermal-Desktop

Experimental desktop tooling for the Infiray HT-301 / T3-317-13. The first
radiometric PyQt MVP now uses the evidence-gated native-equivalent measurement
session. Absolute physical accuracy has not been independently validated.
Historical `thermal_capture.py` calculations are not part of this viewer.

This desktop repository is paired with [LMThermal](https://github.com/LounaMaili/LMThermal),
which holds the hardware and reverse-engineering documentation.

## Requirements

Use the repository virtual environment when available. Python 3.10 or newer
and the dependencies in [requirements.txt](requirements.txt) are required
(NumPy, OpenCV, PyQt6 and Pillow for bounded PNG/JPEG validation).

```bash
./.venv/bin/python -m pip install -r requirements.txt
```

## Camera-free LMTX still analysis

Open Android-exported LMThermal Exchange Format v1 captures without a camera:

```bash
./.venv/bin/python lmthermal_viewer.py --lmtx /path/to/capture.lmtx
```

Use **Open still capture (.lmtx / legacy)** in an existing window. The strict
reader validates the complete archive before displaying data. Saved float32
Celsius values are authoritative; importing never rebuilds thermometry.
Hover and half-open rectangular ROI use the imported native geometry and
validity mask. Palette/range and saved rotation/mirroring affect presentation
only. Preview-only captures have no Celsius readings or legend. PNG rerender
saves separately; analysis edits stay in memory and never modify the source.

The exact 11 Android conformance files and the same real Pixel-exported capture
pass Linux and real Windows 11 offline validation, including exact Float32/ROI/
native/transport parity and operator hover/ROI/resize/presentation/PNG checks.
Windows drive-letter paths with spaces and non-ASCII characters also pass;
live camera acquisition remains Linux-only.
See [LMTX import architecture and validation](docs/LMTX_IMPORT.md)
for the canonical specification, resource limits, interoperability evidence,
Windows commands and legacy-format coexistence.

## Radiometric desktop MVP

Launch the viewer with the HT-301 connected:

```bash
./.venv/bin/python lmthermal_viewer.py
```

The viewer discovers the stable Infiray `video-index0` path and opens a
read-only display-Y preview. Use **Initialize radiometric** only when that
display preview is active. The worker then runs the existing confirmed
`32772 -> 32800 -> 32768` sequence, checks each readback and raw14 transition,
discards shutter frames, and waits for live valid measurements. An existing
raw14 stream with unknown host state remains viewable but has no temperatures;
reconnect the camera to start a known display-mode session.

Ready frames now use a Celsius-driven palette derived solely from the
native-equivalent temperature matrix. **Auto range** clips the display scale
to the current frame's 2nd/98th temperature percentiles; **Locked range** uses
the entered Celsius minimum and maximum exactly. The color legend shows the
effective scale. Choose White hot, Black hot, Inferno, Iron-like (OpenCV Hot),
or Turbo. Display mode remains grayscale and has no Celsius legend. On an
invalid/unsettled frame, the viewer falls back to the current raw14 aiming
view and hides current Celsius readings and legend until readiness returns.
Palette and range affect visualization only.

Hover shows the original raw14 index and the corresponding value from the
native-equivalent temperature matrix. Red and blue markers use the validated high/low camera
coordinates. **Center pixel** means native `(192,144)`; **Camera center
reading** means the separate trailer index. Invalid, held or unsettled frames
hide current temperature readings until the session recovers. Resizing keeps
the image's native aspect ratio and rejects cursor locations in the margins.
Camera orientation remains native; preferred presentation rotation/mirroring
is deferred. See [PyQt MVP architecture](docs/PYQT_RADIOMETRIC_MVP.md).

Native-equivalent temperatures; absolute physical accuracy not yet
independently validated. Recording remains outside this MVP.

Left-click and drag on the image to create or replace one rectangular ROI.
**Clear ROI** removes it. Both endpoint pixels are included, including a
single-pixel click; the stored geometry is half-open
`temperature_c[y1:y2, x1:x2]` in native camera coordinates. A drag must start
inside the image and its endpoint clips at the image edge. The ROI panel shows
the current matrix slice's min, max, mean, pixel count and native extrema
locations. Geometry stays fixed through resizing and palette/range changes.
During display, held or unsettled frames the rectangle remains selected and
its numerical readings become unavailable; ready-frame recovery restores them.

When **Radiometric ready**, **Save radiometric capture** writes a versioned
`<name>.png`, `<name>.npz`, `<name>.json` set from one frozen measurement and
the displayed palette/range. The button is disabled during display, shutter,
held, and unsettled states. The PNG is a human-viewable rendering; the NPZ
contains lossless native `raw14`, float32 `temperature_c`, and exact transport
bytes for later analysis or re-rendering. The JSON records measurement,
calibration, and presentation metadata. Existing filenames are never replaced.
An active ROI adds its native geometry and same-frame statistics to JSON;
the PNG stays a clean thermal rendering and the NPZ matrices retain all pixels.

Saved captures can be inspected without the camera:

```bash
./.venv/bin/python lmthermal_viewer.py --capture /path/to/capture.json
```

Alternatively use **Open still capture (.lmtx / legacy)**. Opening releases any live
camera first. **Close saved capture** returns to disconnected mode; **Connect
camera** closes the saved view. The sidebar identifies **Saved capture** and
**Offline — no camera**. The loader verifies the complete v1 set and hashes,
then uses the saved float32 Celsius and uint16 raw14 matrices directly, with
no thermometry recomputation. Cursor, high/low, distinct centers and ROI stay
in native camera coordinates. Stored ROI statistics are checked against the
matrix; clear or drag to inspect another region. **Saved capture metadata**
shows the original timestamp, calibration/settings and presentation metadata.
Palette and auto/locked Celsius controls change colors only. **Save rendered
image** writes a separate clean PNG using the current display settings; it
never replaces original capture files or creates a new JSON/NPZ set.

For live measurements over time, select **Live measurement log** rate and
click **Start logging**. The default is 1 Hz; 0.5/2/5/10 Hz are also available.
It records the latest ready frame's high/low, distinct centers and current ROI
in a small CSV with a versioned JSON sidecar. Invalid or not-new observations
produce explicit gap rows with empty measurements. Moving/clearing ROI affects
later rows; palette/range/resize never changes same-frame numbers. **Stop
logging**, disconnect or normal close finalizes the files. Offline static
captures cannot be logged. Existing logs are protected; interrupted recordings
remain identifiable by `.incomplete` files. See the
[time-series schema and workflow](docs/MEASUREMENT_TIME_SERIES.md).

See [capture format v1](docs/RADIOMETRIC_CAPTURE_FORMAT.md). The exact transport
frame can contain camera or scene information; review captures before sharing.

## Measurement baseline

`measurement_baseline.py` parses exact 224,256-byte YUYV frames independently
of PyQt. Captures show 288 image rows (384 × 288), followed by four rows of
non-image data. The documented 514-byte parameter block begins at byte 223742,
inside the final row. Both trailer areas are excluded from image statistics.

`ht301_camera.py` discovers the Infiray `video-index0` device by its stable
`/dev/v4l/by-id/` link, then falls back to USB VID/PID in sysfs. It never
hard-codes `/dev/video2` and does not require sudo when normal camera access is
configured.

To inspect live frames:

```bash
./.venv/bin/python measurement_diagnostic.py --count 3 --interval 0.5
```

To save privacy-scrubbed raw fixtures while inspecting them:

```bash
./.venv/bin/python measurement_diagnostic.py --count 2 --save-dir /tmp/ht301-fixtures
```

Review newly saved fixtures before sharing them. The diagnostic scrambles the
image outside a small center patch and clears the identifier ranges observed
in these captures; other firmware revisions may store identifiers elsewhere.

To inspect a saved frame without the camera:

```bash
./.venv/bin/python measurement_diagnostic.py --frame tests/fixtures/scene-a.raw
```

## Earlier single-command diagnostic

`radiometric_mode_diagnostic.py` reports image-word min/max, the percentage
within the native 14-bit lookup range, center/high/low trailer indices,
calibration inputs, and image Y variation before and after a control. It is
independent of PyQt and does not calculate Celsius. By default it captures
baseline frames without changing the camera:

```bash
./.venv/bin/python radiometric_mode_diagnostic.py --count 3
```

To test the single HT-301 output-type-zero command traced in ThermViewer
2.0.23(ot), explicitly request the control:

```bash
./.venv/bin/python radiometric_mode_diagnostic.py --count 3 --apply-thermviewer-output0
```

This sends V4L2 `zoom_absolute=32773` only after baseline frames are captured,
discards 15 settling frames, and prints exact before/after metrics as JSON.
The 2026-09-26 live test used 20 settling frames and still found 0% of image
words in `0..16383`; see the sibling repository's
[application comparison](https://github.com/LounaMaili/LMThermal/blob/proto/full-radiometric-init/docs/APPLICATION_COMPARISON.md).
The diagnostic does not reset the camera because a verified reset sequence is
not yet known. It only writes sanitized fixtures with `--fixture-dir` when
**all** post-control image words fit the lookup; no such fixture was obtained
in this experiment.

`measurement_diagnostic.py` reports the APK-identified settings, copied
calibration coefficients, image-only Y statistics, 14-bit lookup
compatibility, and the trailer center/high/low raw indices. It also retains
the old rejected `Y → GetTempEvn` calculation as historical evidence. Block
field 356 is a copy of a calibration coefficient; the Android app obtains its
live center reading from a different trailer index and a native lookup. See the
[native call chain](https://github.com/LounaMaili/LMThermal/blob/proto/full-radiometric-init/docs/NATIVE_CALL_CHAIN.md).
These original display fixtures remain unsuitable as pixel LUT indices.
New raw14 fixtures and experimental arithmetic are described below.

## Staged radiometric initialization and experimental lookup

`radiometric_sequence_diagnostic.py` replays the three official device writes:
`32772 -> 32800 -> 32768`, recording baseline, every stage, high-bit
statistics, trailer/calibration inputs, readbacks and actual timing. A separate
ThermViewer mode reproduces its nested byte-write schedule from the APK.
The official first command produced genuine raw14 pixels; the ThermViewer
type-0 sequence retained `0x80YY` display words. No bit mask, Y16 negotiation
or raw vendor request was used.

```bash
./.venv/bin/python radiometric_sequence_diagnostic.py \
  --sequence official --report /path/to/new-session/report.json \
  --capture-dir /path/to/new-session/captures --preserve-spatial \
  --stability-count 75
```

Check zoom readback and image words first. If raw14 output is already active,
use a read-only baseline capture rather than reconnecting or resending controls.
The official replay requires readback 0. Its shutter stage discards at least
75 frames: the first approximately 1.3 seconds can be held/transient output.
Reports and captures refuse existing paths. Captures redact known identifier
spans; review any preserved spatial scene before sharing. Full run output belongs in persistent
research storage. See [physical validation preparation](docs/PHYSICAL_VALIDATION.md)
for the capture procedure and read-only follow-up capture.

`native_equivalent_thermometry.py` reconstructs the official 16384-entry lookup
for width 384, range 120, native lens 68 and shutter fix 1.5. It rejects
out-of-range full words and does not mask them. It corrects the earlier
conflation of FPA word 221186 and calibration-temperature word 223490.
Its `temperature_matrix(raw)` function exposes all 288 × 384 float32
native-equivalent values for valid raw14 frames without PyQt. The historical
`experimental_thermometry.py` CLI/import path remains available.

```bash
./.venv/bin/python experimental_thermometry.py tests/fixtures/radiometric-initial.raw
```

Every LUT entry agrees with the executed official x86_64 library for the
initial fixture, including undefined entries; center/high/low are about
16.283/16.800/12.868 °C. This is algorithmic parity, not independently
validated physical temperature accuracy. The optional hash-pinned
`tools/native_lookup_reference.py` needs pyelftools in an analysis environment;
normal tests use saved reference tables and need no APK or RE dependencies.

## Measurement session and operator preview

`radiometric_session.py` manages the supported normal-range initialization
and admits measurements only after live, complete raw14 frames survive the
post-shutter interval. It rejects held images, malformed frames, inconsistent
trailer extrema and undefined lookup values. Readiness is not based on a
frame count alone. The separate OpenCV preview uses display Y or display-only
contrast normalization of raw14 words; normalized bytes never enter the LUT.

Run a noninteractive diagnostic from display mode:

```bash
./.venv/bin/python radiometric_session_diagnostic.py --samples 5 \
  --report /tmp/ht301-measurement-session.json
```

For live aiming, run the preview and press **i** to initialize after aiming;
press **q** or **Escape** to close. Optional ROI boxes are visual guides.

```bash
./.venv/bin/python radiometric_session_diagnostic.py --preview \
  --roi 176,128,32,32
```

The preview shows center, high/low markers when measurements are valid, state,
mode and transient status. A click prints the original pixel index and
native-equivalent temperature only for ready raw14 frames. If the camera is
already raw14 when opened, the preview stays read-only rather than guessing
the current range or repeating initialization. See the
[session architecture](docs/RADIOMETRIC_SESSION.md) for state and validity
rules. Physical temperature accuracy still needs independent targets.

`physical_validation.py` compares unshuffled image regions, trailer extrema,
temporal stability and optional independently measured target temperatures.
It records signed errors without fitting or modifying the lookup. Use the
[reference plan template](docs/validation/target-plan.template.json) and
[validation procedure](docs/PHYSICAL_VALIDATION.md). The GUI now colors
native-equivalent Celsius values, while independent physical accuracy remains
unvalidated.
An operator-confirmed hand capture now shows a central palm ROI consistently
warmer than the cooler background across 23 distinct post-shutter raw14
frames; see the validation procedure for the liveness and accuracy limits.

Run the hardware-independent tests with:

```bash
./.venv/bin/python -m unittest discover -s tests -v
```

See [measurement audit](docs/MEASUREMENT_AUDIT.md) and the
[fixture notes](tests/fixtures/README.md) for evidence and remaining unknowns.
Changes are recorded in the [changelog](CHANGELOG.md).

## Historical prototype

`thermal_capture.py` remains for research history. Its older Y-based
temperature approximation is not used by `lmthermal_viewer.py`.

## License

To be determined.

## Radiometric sequence recording

In the live viewer, scroll to **Radiometric recording**. Select 1/2/5/10/25 Hz
(default 5; 25 experimental), choose **Start recording…** and a new `.lmthermal`
directory, then **Stop recording**. This stores exact native raw14/float32
measurement matrices, per-frame settings, ROI summaries and valid/gap/drop
history in compressed 16-frame chunks. A four-frame handoff bounds backlog;
overload produces explicit drops. CSV scalar logging and matrix recording are
mutually exclusive. Closing/disconnecting/opening an offline still finalizes
recording. Still-capture v1 is unchanged; offline playback is documented below.

See [recording v1 documentation](docs/RADIOMETRIC_RECORDING_FORMAT.md) for the
hardware-independent loader, recovery rules and measured storage tradeoffs.
Native-equivalent temperatures; absolute physical accuracy not yet independently validated.

## Offline radiometric playback

Open a `.lmthermal` directory with **Open radiometric recording**, or launch
without a camera:

```bash
./.venv/bin/python lmthermal_viewer.py --recording /path/to/example.lmthermal
```

The timeline includes valid samples, gaps and drops. Scrub/step, play/pause
at 0.5×/1×/2×/4×, inspect stored raw14/Celsius values and ROI, change palette/
range, and save a selected rendering as PNG. Gaps/drops have no current
measurements. Incomplete recordings clearly identify recoverable committed
frames. Loading uses a background worker and a bounded two-chunk cache.
See [playback documentation](docs/RADIOMETRIC_PLAYBACK.md) for timing, ROI
policy, recovery, source protection and validation limitations.
