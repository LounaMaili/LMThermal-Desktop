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

Run `./.venv/bin/python lmthermal_viewer.py` with a connected HT-301. Use
**Disconnect** before physically reconfiguring the camera; reconnect starts a
new session. The OpenCV preview remains a separate diagnostic tool. A current
ready frame enables **Save radiometric capture**; `radiometric_export.py` freezes
the `MeasurementFrame` and current effective palette/range before the file
dialog, then writes the PNG/NPZ/JSON set described in
[capture format v1](RADIOMETRIC_CAPTURE_FORMAT.md). Display, held, shutter and
unsettled observations disable the action. ROI measurements, recording and
preferred visual orientation are deferred.

Native-equivalent temperatures; absolute physical accuracy not yet
independently validated.

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
