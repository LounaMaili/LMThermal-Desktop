# AGENTS.md — LMThermal-Desktop

## Repository role

This is the primary development repository for the LMThermal desktop application for the Infiray HT-301 / T3-317-13 thermal camera.

When the sibling repository `LMThermal` is available in the same workspace, treat it as the source of truth for hardware reverse engineering, thermometry research, protocol documentation, and functional specifications.

Before changing camera parsing or thermometry, read the relevant documentation from `LMThermal`, especially:

- `REPO_RULES.md`
- `README.md`
- `docs/HARDWARE.md`
- `docs/APK_ANALYSIS.md`
- `docs/THERMOMETRY_LIB.md`
- `docs/SPECIFICATION.md`
- `CHANGELOG.md`

## Development principles

- Code comments and docstrings must be in English.
- Keep camera acquisition, frame parsing, thermometry, palette generation, measurements, exports, and PyQt UI separable.
- Do not bury new thermometry logic inside UI widgets.
- Do not present approximations as validated measurements.
- Prefer small, testable changes over large rewrites.
- Avoid unrelated cosmetic work while measurement fundamentals are unresolved.
- Do not commit credentials, tokens, private machine data, virtual environments, or temporary capture files unless deliberately added as sanitized test fixtures.

## Python environment

Use the repository virtual environment when present:

```bash
./.venv/bin/python
```

Do not assume the system Python contains project dependencies.

Current known runtime dependencies include:

- PyQt6
- OpenCV
- NumPy

If dependencies become formalized in `pyproject.toml` or another dependency file, that file becomes authoritative.

## Camera

Target hardware:

- Infiray HT-301 / T3-317-13
- USB VID: `1514`
- USB PID: `0001`
- UVC / V4L2
- YUYV 4:2:2
- 384 x 292
- 25 fps
- frame size: 224256 bytes

Do not hard-code `/dev/video2`.

Prefer discovering the `video-index0` device through `/dev/v4l/by-id/` for the Infiray T3-317-13, with a VID/PID/V4L2 fallback when necessary.

The development user should be able to access the camera without sudo. Do not make sudo a runtime requirement.

## Frame parsing

Known structure:

- complete frame: `224256` bytes;
- temperature parameter block starts at byte `223742`;
- parameter block size: `514` bytes;
- firmware center temperature: parameter offset `356`.

The parameter block occupies bytes at the end of the nominal YUYV frame. Exclude metadata bytes from image min/max, statistics, palette scaling, and temperature extrema.

## Current thermometry status

Parameter extraction from real hardware is working and produces plausible values.

Example observed values include approximately:

- environment temperature: 25.0 C
- emissivity: 0.450
- distance factor: 0.980
- gain: 0.2705
- firmware center temperature: 35.99 C

The existing per-pixel temperature implementation is **not validated**. It can produce clearly incorrect center values while the firmware reports a plausible center temperature.

In particular, do not assume the current approximation based on `gain * emissivity` is correct.

Use the reverse-engineering work in `LMThermal` to investigate the real processing chain, including:

- raw/Y values and auto gain;
- environment temperature;
- emissivity;
- distance correction;
- calibration and offset factors;
- `InitTempParam`;
- `CalcFixRaw`;
- `GetTempEvn`.

Do not add empirical constants solely to force agreement with one frame.

Before using firmware `center_temp` as an exact regression target, determine whether it represents a single center pixel, a region average, or another camera-side calculation.

## Measurement-first development order

Do not begin with a GUI redesign.

First establish a trustworthy and reproducible measurement layer:

1. stable HT-301 discovery;
2. raw frame acquisition;
3. correct frame/metadata separation;
4. parameter parsing;
5. diagnostic output for all thermometry intermediate values;
6. real raw-frame test fixtures;
7. automated parser and thermometry tests;
8. validated temperature matrix, as far as evidence permits.

Only then continue the desktop MVP:

1. live thermal view;
2. cursor temperature;
3. true temperature min/max;
4. fixed temperature range / palette lock based on Celsius values, not Y brightness;
5. palette selection;
6. image capture with useful measurement metadata.

Advanced measurement regions, CSV/radiometric exports, recording, gallery/comparison, and Android work come later.

## Testing

Measurement code should be testable without the physical camera for every run.

Store a small, deliberate set of sanitized real raw-frame fixtures when needed. Each fixture should record enough metadata to explain what is expected from it.

Tests should cover at least:

- exact frame size;
- parameter offsets and decoding;
- image/metadata boundary;
- exclusion of metadata from image statistics;
- thermometry intermediate calculations;
- reproducibility across saved frames.

When tests are introduced, use the repository virtual environment to run them.

## Experimental workflow

For each thermometry change:

1. state the hypothesis;
2. identify the relevant reverse-engineering evidence;
3. run the diagnostic against real or saved frames;
4. show the important intermediate values;
5. compare with camera-provided information;
6. keep or reject the hypothesis based on evidence;
7. document confirmed discoveries in `LMThermal`.

If the available evidence is insufficient to reconstruct a calculation exactly, stop at that boundary and document what is missing rather than inventing a plausible-looking result.

## Git workflow

Use focused branches for substantial changes, for example:

- `feat/measurement-baseline`
- `feat/temperature-map`
- `feat/range-lock`
- `fix/frame-metadata-region`

Keep changes reviewable and avoid mixing unrelated tasks in the same commit.

The sibling `LMThermal` repository has a separate Git history. If both repositories need changes, commit them independently.
