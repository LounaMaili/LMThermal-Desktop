# Controlled physical validation preparation

The 16384-entry reconstruction matches the inspected official x86_64 APK
arithmetic. This does not establish absolute camera accuracy. Hand/background
captures test direction and spatial discrimination only; a hand's surface
must not be assigned an assumed temperature.

## Acquisition

Check the current zoom readback and image-word range first. If output is
already raw14, retain that state and use read-only `--sequence baseline`.
If output is display mode and zoom readback is 0, the official replay uses
only the three documented commands. It does not reset the camera. Use a new
output directory for each run; existing reports and captures are retained.

```bash
./.venv/bin/python radiometric_sequence_diagnostic.py \
  --sequence official --report /path/to/new-session/report.json \
  --capture-dir /path/to/new-session/captures --preserve-spatial \
  --count 3 --settle 15 --stability-count 75
```

This preserves baseline, settling and selected frames after `32772`, `32800`
and `32768`, plus 75 additional frames after shutter. The shutter stage itself
discards 75 frames before selecting a steady-state candidate; the earlier
15-frame window was insufficient after an observed roughly 1.3-second hold.
Receipt times and control
readbacks are recorded. Settling frames are evidence, not automatically valid
measurements. Identify the first consecutive raw14 samples with valid nonzero
calibration and inspect their spatial content. RGB conversion stays disabled;
the advertised transport remains YUYV despite its changed word semantics.

`--preserve-spatial` clears known device identifier spans but retains the scene.
Review for personal content before sharing. Without that option, spatial
shuffling prevents a later coordinate/ROI check. Neither path overwrites an
existing capture. Keep the complete run in persistent research storage and
select a small, documented set of fixtures for Git.

A subsequent **read-only** capture on an already initialized camera can use
`--sequence baseline --count 75 --settle 15` with a new report/capture directory.
It does not change range, shutter or parameter controls. Record the existing
state; the offline lookup currently assumes range 120, native lens 68 and
shutter fix 1.5. Other modes are unsupported. Select a spatially preserved
raw14 frame for ROI analysis; the public `temperature_matrix` function returns
the complete 288 × 384 float32 native-equivalent output for that frame.

## Independent reference experiment

1. Use a stable target surface measured by an independent instrument. Record
   instrument identity, method, uncertainty and measurement time. A setpoint,
   room thermostat or assumed body temperature is not a surface reference.
2. Record target material/emissivity basis, actual distance, ambient and
   reflected conditions, camera warmup time and before/after reference drift.
   Record the camera's trailer settings as well. Do not tune constants or
   settings solely to force agreement. The native lens-68 distance multiplier
   is reconstructed behavior; its physical interpretation still needs checking.
3. Choose interior target/background ROIs before numerical comparison. Keep
   edges, reflections and moving objects out of the target ROI. Ensure the
   target fills enough pixels and keep camera/targets still.
4. Use at least two, preferably three, matte high-emissivity targets at
   independently measured surface temperatures. Repeat at multiple stable
   surface temperatures within normal range, with
   repeated captures before and after a documented shutter event. Preserve
   failed/unstable frames separately. Predefine acceptable drift and error
   using the reference uncertainty and intended measurement use.
5. Compare region statistics against the independent readings. Report signed
   residuals, repeatability and drift; do not equate native arithmetic parity
   with calibrated accuracy or fit a per-scene correction.

## Offline comparison

Copy [the plan template](validation/target-plan.template.json), adjust the
ROIs (`x,y,width,height`, unrotated 384 × 288 coordinates), and record actual
conditions. Leave `reference` null for discrimination-only work. For an
independently measured target, use this structure with actual values:

```json
"reference": {
  "surface_temperature_c": 30.2,
  "uncertainty_c": 0.3,
  "instrument": "Replace with the instrument used",
  "measured_at_utc": "Replace with the measurement timestamp",
  "method": "Replace with the surface measurement method"
}
```

These numbers demonstrate the schema and are not measured fixture values.
Use one plan per stable reference plateau. The tool verifies finite values,
reference metadata and image-only ROI boundaries; it cannot verify that an
operator's reference measurement is correct.

```bash
./.venv/bin/python physical_validation.py \
  --plan /path/to/filled-plan.json --report /path/to/new-comparison.json \
  /path/to/session/captures/E_post_shutter_stability/frame-*.raw
```

The report preserves all lookup inputs, raw/lookup region statistics, signed
reference residuals when supplied, and temporal standard deviation/drift. It
also checks trailer high/low against image extrema and their reported `(x,y)`
locations. Center is compared explicitly with pixel `(192,144)`; a mismatch
is recorded and does not establish which area the firmware uses for center.
No reference value changes the computed lookup. No GUI or camera I/O is used
by `physical_validation.py`.

## Operator-confirmed hand discrimination (2026-09-29)

The operator placed a hand centrally with cooler background visible; the
read-only baseline preview and saved spatial frames show that hand. Initial
zoom readback was 0 and image words were 32777–33008. The documented
`32772 -> 32800 -> 32768` sequence yielded raw14 image words. The shutter
stage discarded 75 frames. The later stability stage contained an additional
31-frame repeated image interval at frames 4–34, so the 75-frame discard is
a **minimum**, not a guarantee of live output. Frame 58 was malformed and
stopped the diagnostic. Both anomalies remain in the persistent report; neither
was used for discrimination or repeatability.

The subsequent frames 35–57 supplied 23 distinct, fully raw14 images. A
fixed 32 × 32 central palm ROI averaged **36.587 °C** across these frames,
with frame-to-frame standard deviation **0.0083 °C**. A fixed 32 × 32 lower
left background ROI averaged **27.991 °C**, with standard deviation
**0.0065 °C**. The hand-minus-background difference averaged **8.596 °C**
and was positive in every selected frame (range 8.582–8.607 °C). The
observed full image indices spanned 5338–5786 and matrix values
27.702–37.442 °C. Visual inspection places reported high points on the hand
near the thumb base and low points in dark background. Trailer high/low
indices and coordinates matched image extrema in all 23 frames.

The selected sanitized, spatially preserved
`tests/fixtures/warm-hand-settled.raw` comes from frame 45 of that live
window. Its full provenance, calibration inputs, raw and matrix ranges,
ROI mean/min/max/spatial standard deviation, and literal/trailer center
comparison are in
[the compact discrimination report](diagnostics/2026-09-29-hand-discrimination.json).
The source run and per-frame analysis remain in persistent
`LMThermal-Research/analysis/warm-target-20260929/hand-run-01/` storage.
Only 5 of the 23 live frames had trailer center index equal to literal pixel
`(192,144)`; no center-region algorithm is inferred. This is a relative
discrimination result, not an independently calibrated temperature test.
