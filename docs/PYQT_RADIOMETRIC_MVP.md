# PyQt radiometric MVP

`lmthermal_viewer.py` is the first application UI for the existing
`HT301RadiometricSession`. It does not interpret camera protocol or calculate
temperatures. `mvp_camera_worker.py` owns device discovery, exact acquisition,
`ZoomControl`, the session and shutdown in a `QThread`. It reuses the
diagnostic's latest-frame stream; if rendering falls behind acquisition, old
captured frames are skipped. A lock-protected single latest observation also
bounds queued Qt notifications. The UI consumes immutable session observations
and accepts temperatures only from a valid `MeasurementFrame`.

Opening the camera sends no control. The user explicitly requests normal-range
initialization from display mode; the worker calls the supported session
method. The UI follows its display, switching, shutter, unsettled, ready and
error states without a second readiness algorithm. Unknown-host-state raw14
can be previewed but not measured. A rejected or held frame removes current
cursor, extrema and center readings until a valid live frame restores them.

The image widget shows exactly the first 384 × 288 pixels. Display mode uses
image Y. A radiometric-ready frame uses `celsius_palette.py` to clamp the
native-equivalent temperature matrix to the active Celsius scale, normalize
that scale to 8-bit palette levels, and render RGB. Automatic bounds are the
current matrix's 2nd/98th percentiles with a minimum 1 °C display span; this
reduces single-pixel contrast flicker without temporal smoothing. Locked
bounds are the entered Celsius values exactly, with out-of-range pixels
clamped to the palette endpoints. White hot, Black hot, Inferno, Iron-like
(OpenCV Hot) and Turbo are presentation choices. The adjacent hot-at-top
legend uses the same palette mapping and effective bounds as the image; its
five labels are Celsius display ticks, not measured extrema. An invalid or
unsettled raw14 frame falls back to display-only normalization from
`diagnostic_preview.py` and hides the legend and current temperatures. None
of these visualizations enters thermometry. The native
raw14 and temperature matrices retain their original camera orientation.
`mvp_presentation.py` maps native pixel centers into an aspect-preserving
image rectangle and maps mouse positions back through letterboxing. Outside
positions have no native pixel or temperature. Future rotation/mirroring can
be added to these presentation helpers without rewriting measurement data.

The crosshair marks literal `(192,144)`. The panel labels its matrix value as
**Center pixel** and separately labels the trailer lookup as **Camera center
reading**. High/low markers and values come from `MeasurementFrame`, not
display brightness. The hover value is precisely
`temperature_c[y,x]`, accompanied by `raw14[y,x]`.

Left-button press/drag selects one rectangular ROI, replacing the previous
selection. A press in the letterbox margins is ignored. A drag started inside
the image clips its endpoint when it leaves the image; a click selects one
pixel. `roi_measurement.py` normalizes native endpoint pixels into half-open
`[x1,x2) × [y1,y2)` geometry, with both endpoint pixels included. The widget
stores that geometry and reprojects its boundaries through
`mvp_presentation.py` for drawing. Resizing and palette/range changes do not
modify the rectangle or camera coordinate system. **Clear ROI** removes it.

The ROI panel uses only the current `MeasurementFrame.temperature_c` slice.
It reports unsmoothed minimum, maximum, float64 mean, pixel count and native
min/max coordinates. Tied extrema use the first pixel in row-major order.
It uses the complete slice, independently of the display percentiles and
Celsius bounds. A frame without a valid ready measurement shows **Unavailable**
while preserving the geometry; the same ROI resumes on valid recovery.
Whole-image camera high/low markers and cursor values remain independent.
The sidebar scrolls when the controls exceed available window height.

Run `./.venv/bin/python lmthermal_viewer.py` with a connected HT-301. Use
**Disconnect** before physically reconfiguring the camera; reconnect starts a
new session. The OpenCV preview remains a separate diagnostic tool. A current
ready frame enables **Save radiometric capture**; `radiometric_export.py` freezes
the `MeasurementFrame` and current effective palette/range before the file
dialog, including the optional ROI geometry/statistics from that same frame,
then writes the PNG/NPZ/JSON set described in
[capture format v1](RADIOMETRIC_CAPTURE_FORMAT.md). Display, held, shutter and
unsettled observations disable the action. Recording and preferred visual
orientation are deferred.

Native-equivalent temperatures; absolute physical accuracy not yet
independently validated.

## Offline capture inspection

Launch `./.venv/bin/python lmthermal_viewer.py --capture /path/to/capture.json`
to open a saved capture without starting camera acquisition. The existing
no-argument launch still starts live display acquisition. **Open radiometric
capture** also works from a running window. It cancels a pending startup
connection and stops/releases the worker before loading. If shutdown times
out, loading is postponed; live and saved data never overlap. **Connect
camera** clears the offline image, readings and ROI before starting a new
worker. **Close saved capture** leaves a disconnected window, with no automatic
connection. Queued camera notices, failures and frame signals are accepted
only from the current worker and cannot overwrite offline data.

`radiometric_capture.py` owns a frozen `OfflineCapture`: immutable byte-backed
read-only raw14 and Celsius arrays, optional transport bytes, original JSON,
palette/range, timestamp, calibration/environment metadata and optional ROI.
Metadata access returns a separate object. Loading never calls thermometry,
initialization or acquisition. The loader rejects unsupported, incomplete or
inconsistent captures with a dialog; the PNG is hash-checked as a required v1
companion, but never used as measurement or rendering input.

The image widget and pure presentation helpers accept either a currently
ready live measurement or a validated offline capture. Hover, native extrema,
centers, letterboxing and ROI use the same coordinate mapping. Saved mode
shows **Offline — no camera**, filename/timestamp and no FPS; initialization
and **Save radiometric capture** are disabled. **Saved capture metadata**
shows the unchanged original metadata, including settings/calibration and
stored ROI statistics. The ROI panel calculates statistics from the saved
matrix after the loader verifies the stored values (1e-4 °C absolute / 1e-6
relative tolerance). Clearing/redrawing affects only inspection geometry.

Initial rendering restores the exact captured effective bounds, including
an automatic capture's stored percentiles. Subsequent palette/range controls
use the existing Celsius renderer and auto-range helper; neither array nor
numerical measurements changes. **Save rendered image** freezes the current
palette/bounds before the destination dialog and saves a clean native-size
PNG without overlays. It refuses existing destinations and original capture
paths; it does not write JSON or NPZ.

## Offline smoke test (2026-09-30)

The surviving live palm capture (`hand-roi`, captured 07:46:24 UTC) reopened
without a camera. The operator confirmed usable hand/background contrast,
aligned hover/ROI after resize, clear/new ROI interaction, palette changes and
auto/locked range changes with unchanged same-region readings, then closed
the window; the process exited successfully. High `(213,131)` lies on the palm; low `(35,237)` lies on the cool
background. Literal center remains 34.8705 °C (raw 5658); the separate trailer
center remains 34.8276 °C (raw 5656).

The older standalone auto/locked files were no longer available. Three
presentation test sets were therefore derived from the surviving capture's
unchanged NPZ: original auto-range Inferno without ROI, locked 25–45 °C White
hot without ROI, and original auto-range Inferno with ROI. This is saved-data
validation, not new live acquisition. A Qt event-loop check opened each set,
restored its original presentation, inspected cursor/extrema/centers, drew a
new cool ROI, resized, changed palette/range and saved a PNG. The new ROI
`[340,375) × [50,100)` had 1,750 pixels and min/max/mean
24.0993 / 24.4098 / 24.2463 °C; the original palm ROI restored
33.4223 / 35.5125 / 34.7923 °C. Values were unchanged by presentation controls,
and rendered PNG pixels matched direct rendering of the saved matrix.
Original JSON/NPZ/PNG hashes were unchanged. A deliberately altered future
version was rejected through the UI; automated cases also reject corrupted
NPZ, missing companions, malformed metadata and incorrect array layouts.
Temporary scenes/screenshots were kept outside Git. This test does not add
physical-temperature accuracy evidence.

## Live measurement logging

The **Live measurement log** group selects 0.5/1/2/5/10 Hz (default 1), a new
CSV destination, Start/Stop, and duration/count/status. It is enabled only
with a live camera worker and is disabled for saved captures. A rate is fixed
until Stop. Logging can start before readiness; invalid periods are explicit
empty-value rows. It does not restart acquisition or initialization.

`measurement_logger.py` owns pure sample creation/serialization, a latest-slot
monotonic sampler, sidecar metadata and incremental exclusive file publication.
`MeasurementLogger` runs sampling and writing on its own thread. The window
publishes current observations, including invalid ones, and ROI changes without
queuing frames; its existing status timer only reads logger status. The logger
copies ready-frame scalar values and uses the existing ROI helper. Rendering
and thermometry paths are unchanged. Pending mode changes wait for logger
finalization; disconnect, close, camera failure/termination and offline opening
stop and close the log. Queued old worker signals retain their existing sender
guards. Start rechecks mode after the file dialog.

The source capture matrix is never recorded in this feature. Active files use
`.incomplete` names; a clean stop publishes CSV then complete JSON. Write errors
retain incomplete evidence and display an error. See
[time-series v1](MEASUREMENT_TIME_SERIES.md) for columns, cadence, gap handling,
ROI and completion rules, including the real-camera smoke results. The initial
1 Hz runs retained median 25 view FPS; an instrumented hand/ROI retry verified
per-row geometry, warm/cool values, clear behavior and transient gaps. Its
interactive view counter varied (median 21), so no fixed FPS guarantee is
claimed. Physical accuracy remains independently unvalidated.

## Live smoke test (2026-09-29)

With the HT-301 reconnected in display mode, the operator confirmed prompt
display response, recognizable raw14 imagery, plausible hand/background
high/low behavior, distinct hover values and aligned hover after resizing.
The visible view counter was about 25 FPS. Closing the window released the
camera: a fresh read-only reopen showed raw14 with unknown host state,
initialization disabled and all current temperatures unavailable. Zoom
readback stayed `32768` before and after that reopen. The smoke test did not
measure independent target temperatures or settle preferred visual rotation.

## Celsius display smoke test (2026-09-29)

From a reconnected display-mode camera, grayscale aiming stayed available
without a Celsius legend. After explicit initialization, the operator confirmed
that Inferno colors tracked the warm hand and cooler background, and high/low
markers remained aligned. Changing to White hot and Turbo changed colors
without changing the measurement interpretation. A locked 25–45 °C scale was
shown with matching legend endpoints; returning to auto restored live contrast.
Hover and overlays remained aligned after resizing. Observed view rates were
about 24–25 FPS in ready/locked views, close to the earlier grayscale rate.
This checks presentation behavior and relative scene response, not absolute
physical temperature accuracy.

## Capture/export smoke test (2026-09-29)

From a reconnected display-mode HT-301, the viewer passed through the
supported initialization to `radiometric_ready`. With an operator-confirmed
hand in view, the Save action produced an auto-range Inferno set and a locked
25–45 °C White hot set. Both PNGs visibly retained the hand/background
structure. Independent NPZ reads found `uint16[288,384]` raw14 matrices,
`float32[288,384]` Celsius matrices and exact 224,256-byte transport frames.
Both PNGs matched a fresh render from their own matrix and JSON palette/range;
all saved file hashes matched. The first set's files were unchanged after
switching palette/range and saving the second. The view rate was about 25 FPS
before and after saving. A later transient/unsettled interval recovered to
ready; normal window closure released the camera handle. The hand demonstrates
relative scene structure only, not absolute physical accuracy.

## ROI smoke test (2026-09-30)

The HT-301 was discovered through its stable `video-index0` path with
`zoom_absolute=0`, and the existing initialization reached ready. The operator
drew an ROI within a visibly present palm and confirmed resize alignment.
The captured native ROI `[149,277) × [85,169)` contained 10,752 pixels:
min 33.4223 °C, max 35.5125 °C, mean 34.7923 °C. Its minimum was at `(276,128)`
and maximum at `(213,131)`. A dark-corner slice `[340,375) × [50,100)` in the
same saved matrix measured min 24.0993 °C, max 24.4098 °C and mean 24.2463 °C.
All three palm statistics were warmer. A later live cooler-scene sample with
the original ROI geometry measured min 23.4107 °C, max 24.2739 °C and mean
24.0092 °C; that sample did not contain a hand.

The live presentation check retained the same observation, native rectangle
and exact statistics while toggling the Celsius range controls. Offscreen
widget regressions also verify palette changes and resizing against a fixed
measurement, avoiding confusion with natural changes between live frames.
Natural mixed/calibration/summary rejections removed numerical ROI readings;
recovery restored current readings with unchanged geometry. Steady ready views
were around 25 FPS, with occasional lower rates during interactions/transients.
The Save action's v1 JSON ROI statistics exactly matched an independent NPZ
slice calculation; NPZ keys, dimensions and dtypes remained unchanged.
This is relative scene evidence, not independent absolute calibration.

The final operator check also passed: a background ROI beside the visibly
present hand showed cooler values than the hand ROI, consistent with the saved
matrix comparison. Resizing retained alignment with the same scene region.
Changing palette and switching auto/locked Celsius range changed visualization
while min/max/mean remained unchanged for the same frame and region. This
completes the live ROI interaction and presentation validation.

## Radiometric sequence recording

**Radiometric recording** is a separate live-only group below the CSV logger.
Choose 1/2/5/10/25 Hz (default 5), start a new `.lmthermal` directory, and stop
without restarting acquisition. It preserves original raw14 and float32
Celsius matrices in bounded compressed chunks, per-frame parameters and a
valid/gap/drop timeline. The UI shows committed-frame counts, gaps, drops,
queue depth and approximate chunk bytes. Scroll the side panel to see controls.
CSV logging and matrix recording are mutually exclusive in v1; neither changes
still-capture semantics. Offline opening/disconnect/close finalize the active
recorder. 25 Hz is experimental, and readiness is never weakened for recording.
See [recording v1](RADIOMETRIC_RECORDING_FORMAT.md) for format, loader, memory
bounds, interruption behavior, benchmark and live storage figures.

## Offline sequence playback

**Open radiometric recording** enters a third isolated mode alongside live
acquisition and offline still inspection. `--recording DIR` starts directly
without any camera. A focused panel beneath the image offers timeline
scrubbing across every entry, stepping, beginning/end, elapsed-time play/pause
and 0.5×/1×/2×/4× speed. Loading/validation run in `PlaybackWorker`, using the
pure `PlaybackModel` and a two-chunk LRU cache. Sender/token guards reject old
loads; opening another mode stops the worker and clears previous measurements.

Valid frames reuse stored-matrix cursor/extrema/separate-center, palette,
legend and ROI controls. Invalid/drop/uncommitted selections are blank with
reason labels and unavailable readings. Each navigation restores the recorded
ROI; replacing/clearing labels an inspection ROI. PNG export freezes the
current frame and refuses any destination inside the source bundle.
See [playback details and measured validation](RADIOMETRIC_PLAYBACK.md).
The original live bundles were unavailable; this task used explicitly
fixture-derived histories instead and does not claim playback validation of
the missing live recordings. Independent physical accuracy remains unvalidated.
