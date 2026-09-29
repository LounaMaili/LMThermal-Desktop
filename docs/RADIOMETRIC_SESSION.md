# HT-301 radiometric measurement session

`radiometric_session.py` is a PyQt-independent acquisition gate for the
observed HT-301 normal-range path. `native_equivalent_thermometry.py` contains
the float32-faithful lookup for image width 384, host range 120, lens 68 and
shutter fix 1.5. The previous `experimental_thermometry.py` command remains
as a compatibility wrapper. Neither module asserts independently calibrated
surface temperatures.

## Initialization and states

The session requires three consecutive valid display frames and zoom readback
0 before issuing any control. An invalid startup frame resets that display
streak; existing raw14 output is rejected rather than reinitialized. It uses
only standard V4L2 `zoom_absolute` values established by the official APK:

1. After at least 500 ms, send `32772`; discard 15 transport frames, then
   require two distinct complete raw14 frames with usable trailer settings.
2. After at least 600 ms, send `32800`; discard 15 frames and again verify
   distinct raw14 output. This is the supported normal-range device state.
3. After at least 500 ms, send `32768`. Treat at least the next 75 **valid**
   frames as shutter transient. Then require five consecutive, distinct
   raw14 image frames with complete calibration, consistent trailer extrema
   and defined native lookup outputs before `radiometric_ready`.

Control readback and image semantics are checked separately. A failed stage
raises `SessionError`; the next write is not sent. If raw14 is already active
at startup, the tool provides a read-only preview and does not guess its host
range or resend controls. A new session cannot mark that unknown host state
`radiometric_ready` without the documented initialization.

States are `disconnected`, `display_stream`, `switching_to_raw14`,
`raw14_unsettled`, `shutter_transient`, `radiometric_ready`, and `error`.
Readiness depends on valid live frames, not just a counter. The minimum
75-frame discard reflects observed hardware behavior; an additional 31-frame
hold occurred after that interval in a previous run. Identical **image**
hashes detect held output even when trailer bytes change. A held, malformed,
or inconsistent frame after readiness demotes the session and requires five
new distinct valid frames. A return to display output is a terminal session
error. Rejection counts and stage readbacks are available in the CLI report.

The measurement object retains the original transport bytes, read-only
384 × 288 raw14 and float32 temperature matrices, settings, calibration trace,
timestamps, trailer center/high/low indices and outputs, extrema coordinates,
and literal pixel `(192,144)` separately. A trailer center mismatch is
reported, never resolved by inventing a center-region algorithm. Full words
must be `<0x4000`; no bit masking or display-Y substitution is performed.
Undefined native LUT entries and invalid calibration are rejected.

## Diagnostic commands

With the camera connected in display mode, run the complete session and
report five live valid measurements:

```bash
./.venv/bin/python radiometric_session_diagnostic.py --samples 5 \
  --report /tmp/ht301-measurement-session.json
```

For operator aiming, launch the lightweight OpenCV view:

```bash
./.venv/bin/python radiometric_session_diagnostic.py --preview \
  --roi 176,128,32,32 --roi 16,32,32,32
```

The preview opens at a 2× native-size window for easier aiming (and remains
resizable). Press **i** while display frames are visible to run the supported
sequence; the window continues through
raw14 transition and shutter settling. Press **q** or **Escape** to close.
Clicking a valid ready frame prints the pixel coordinate, original raw14
index and native-equivalent temperature; clicks on other frames cannot report
a temperature. The center crosshair is fixed at `(192,144)`. High/low markers
appear only on valid measurement frames. The overlay labels invalid or
transient frames and keeps the physical-accuracy warning visible with any
native-equivalent temperatures. ROI rectangles are visual guides only.

In display mode, the first byte of each image word supplies grayscale Y.
In raw14 mode, the original full word is mapped to 8-bit with 1st/99th
percentile contrast solely for visualization. The measurement path remains
`raw14 -> native-equivalent LUT -> temperature`; the preview path is
`raw14 -> display-only normalization -> OpenCV window`. Preview functions
return new arrays and do not mutate raw frame or measurement arrays. This is
an operator diagnostic, not the PyQt application or a Celsius palette lock.
The diagnostic captures exact frames continuously and supplies the latest
complete receipt to the session, skipping old buffered frames if rendering or
lookup is slower than the camera. The session counts only inspected receipts;
this avoids making an old queue look like current live output.

The diagnostic has hardware-independent tests for mode/size rejection,
transition failure, held output and recovery, malformed frames, native
reference lookup, extrema/center semantics and pure preview helpers. A live
camera and graphical desktop are required to verify aiming and window
behavior. Independent multi-target surface reference measurements remain a
separate future validation task; see [PHYSICAL_VALIDATION.md](PHYSICAL_VALIDATION.md).
