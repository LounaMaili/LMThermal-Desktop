# Changelog

All notable changes to LMThermal-Desktop will be documented in this file.

## [Unreleased]

### Added (PyQt radiometric MVP — 2026-09-29)
- Replaced the obsolete Y-based PyQt thermometry path with a camera worker that owns stable HT-301 discovery, latest-frame acquisition, the validated radiometric session, controls, and clean shutdown outside the GUI thread.
- Added a native-orientation 384 × 288 display with explicit initialization, session validity, view FPS, true matrix cursor values, validated high/low markers, and separately labeled literal/trailer center readings. Pure aspect-preserving coordinate helpers reject letterbox positions and retain raw14 indices independently of display normalization.
- Removed misleading legacy range-lock, palette, point persistence, and screenshot controls from the first measurement MVP. Absolute physical accuracy remains unvalidated.

### Fixed (Live diagnostic preview validation — 2026-09-29)
- Enlarged the OpenCV diagnostic window and rendered the center crosshair last at two-pixel thickness after a live display-mode window capture showed that the original one-pixel marker disappeared when HighGUI scaled the image down.
- Allowed the noninteractive session to skip an invalid startup frame while requiring three consecutive valid display frames before any initialization write; the live camera produced a mixed first frame after reconnect.

### Added (Radiometric measurement session and diagnostic preview — 2026-09-29)
- Added a PyQt-independent normal-range HT-301 session with explicit display, transition, shutter, unsettled, ready and error states. It uses only the confirmed `32772 -> 32800 -> 32768` controls, readbacks and observed-frame gates.
- Added a measurement object containing original raw14 pixels, a native-equivalent 288 × 384 temperature matrix, settings/calibration trace, distinct trailer and literal-center readings, extrema and timestamps. Held, malformed, inconsistent and undefined frames cannot be reported as ready measurements.
- Promoted the float32-faithful lookup to `native_equivalent_thermometry.py` while retaining the experimental CLI compatibility path; physical accuracy remains unvalidated.
- Added an OpenCV diagnostic preview for display Y and display-only normalized raw14, with state/transient overlays, center and extrema markers, optional ROI guides and valid-frame click inspection. Added saved-fixture state/recovery and pure preview tests; no PyQt measurement integration.

### Added (Operator-confirmed warm-target discrimination — 2026-09-29)
- Captured a visible hand against cooler background after the documented official raw14 transition; selected 23 distinct post-shutter frames for relative ROI and center/high/low analysis.
- Saved one identifier-redacted, spatially preserved steady hand fixture with provenance, a fixed ROI plan, a compact results report, and a hardware-independent regression test.
- Detected and excluded an additional 31-frame held image interval after the 75-frame shutter discard and one malformed frame; neither contributes to reported repeatability. This does not establish absolute temperature accuracy.

### Changed (Settled radiometric validation — 2026-09-29)
- Exposed the complete 288 × 384 experimental native-equivalent temperature matrix for valid raw14 frames, with strict display-word and undefined-entry rejection.
- Recorded the observed roughly 1.3-second shutter hold and 75-frame experimental settling window; separate transient and settled fixtures, liveness, trailer-coordinate, and region-stability evidence are retained.
- Updated the capture procedure to inspect the current camera mode before replay and to use independent surface references for a later physical-accuracy test.

### Added (Full radiometric sequence — 2026-09-27)
- Added staged official and ThermViewer replays with exact decoded parameter commands, readbacks, command timing, settling observations and high-bit statistics. No raw vendor requests or GUI changes are involved.
- Observed genuine raw14 output after official `zoom_absolute=32772`; the separately reset ThermViewer type-0 startup changed emissivity but retained display words.
- Added an experimental 16384-entry official range-120/lens-68 lookup and an optional hash-pinned native reference harness. Every entry of the initial fixture agrees with executed x86_64 APK arithmetic, including undefined values; physical accuracy is not yet validated.
- Preserved the interrupted work in timestamped research archives, and retained the surviving sanitized raw fixture/native table. Labeled the earlier temporary-report results as transcript-derived evidence.
- Added persistent stage capture, no-overwrite checks, spatial/trailer comparisons and an offline controlled-target validation tool with independent-reference metadata and temporal drift reporting.

### Corrected (Lookup inputs — 2026-09-27)
- Separated the FPA word at frame byte 221186 from the calibration-temperature word at 223490 (`word/10-273.15`).
- Reconstructed the native lens-68 distance multiplier and final correction using explicit float32/double arithmetic. Masked image-word statistics remain diagnostics only; invalid full pixel indices are rejected.
- Updated the measurement audit, fixture provenance, agent measurement status, README and controlled-validation procedure while preserving repository completion workflow requirements.

### Added (Radiometric-mode diagnostic — 2026-09-26)
- Added a PyQt-independent, baseline-first HT-301 mode diagnostic reporting 14-bit word compatibility, trailer center/high/low indices, calibration inputs, and exact before/after frame metrics.
- Added an explicit, single-control ThermViewer HT-301 output-type-zero test (`zoom_absolute=32773`) through standard V4L2, with settling frames and hardware-independent tests.
- Observed that the documented control alone retained `0x80YY` display words in three live post-control frames; no Celsius lookup or GUI measurement was enabled.

### Corrected (Native thermometry audit — 2026-09-26)
- Renamed diagnostic parameter fields to match the APK's correction, reflected temperature, ambient temperature, humidity, emissivity, distance, and copied calibration coefficients.
- Added 14-bit lookup compatibility and trailer center/high/low index reporting for saved or live frames; the current saved image words exceed that range.
- Moved the historical rejected Y-based calculation behind explicit legacy labeling and evaluated the documented `CalcFixRaw` first stage with ambient temperature.
- Updated measurement and fixture documentation to identify field 356 as a duplicated calibration coefficient while preserving the end-of-task workflow requirements.

### Added
- A PyQt-independent HT-301 measurement diagnostic with stable camera discovery, exact raw-frame capture, known parameter decoding, and intermediate arithmetic traces.
- Two sanitized captures and hardware-independent tests for frame size, parameter offsets, image/trailer separation, metadata exclusion, and reproducibility.
- A measurement audit recording the evidence, rejected legacy calculation, and remaining thermometry questions.

### Changed
- Added persistent agent workflow instructions requiring documentation and changelog maintenance for meaningful changes.
- Completed task branches must be committed and pushed to GitHub for remote review; agents must not merge them into `main` automatically.

### Corrected
- Measurement diagnostic image statistics use only the 384 × 288 thermal image area; the four trailing transport rows are non-image data.
- Documentation no longer presents the existing per-pixel Celsius calculation or parameter field 356 as a validated live center temperature.

## [0.1.0] - 2026-04-19

### Added
- Initial PyQt6 desktop thermal viewer for the Infiray HT-301 / T3-317-13.
- Real-time YUYV capture through V4L2.
- Per-pixel click and hover measurement prototype.
- Min/max markers.
- Multiple color palettes.
- Zoom and frame-information controls.
- Image capture with overlays.
