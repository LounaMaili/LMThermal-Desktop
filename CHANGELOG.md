# Changelog

All notable changes to LMThermal-Desktop will be documented in this file.

## [Unreleased]

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
