# Changelog

All notable changes to LMThermal-Desktop will be documented in this file.

## [Unreleased]

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
