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
image Y; raw14 mode uses display-only contrast normalization from
`diagnostic_preview.py`. Neither visualization enters thermometry. The native
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
new session. The OpenCV preview remains a separate diagnostic tool. Celsius
palette lock, ROI measurements, capture/export, recording and preferred visual
orientation are deferred.

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
