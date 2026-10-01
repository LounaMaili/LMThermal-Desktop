# AGENTS.md — LMThermal-Desktop

## Repository role

This is the primary development repository for the LMThermal desktop application for the Infiray HT-301 / T3-317-13 thermal camera.

When the sibling repository `LMThermal` is available in the same workspace, treat it as the primary Android product repository and the source of truth for hardware reverse engineering, thermometry research, protocol documentation, and functional specifications. This Desktop repository is the executable reference/diagnostic implementation, not the final product target.

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
- thermal image: first 288 rows, `221184` bytes;
- final four transport rows: non-image trailer;
- temperature parameter block starts at byte `223742`;
- parameter block size: `514` bytes;
- parameter offset `356` copies a calibration coefficient from byte `223498`; the app's live center index is elsewhere in the trailer.

The parameter block occupies only part of the non-image trailer. Exclude all
four trailer rows from image min/max, statistics, palette scaling, and
temperature extrema.

## Current thermometry status

Parameter extraction from real hardware is working and produces plausible values.

Example observed values include approximately:

- reflected and ambient temperatures: 25.0 C
- humidity: 0.450
- emissivity: 0.980
- distance: 1 (uint16)
- calibration coefficients: 0.2705 and 35.992, duplicated at block offsets 352 and 356

The existing per-pixel temperature implementation is **not validated**. The
original baseline image words exceed the Android app's 14-bit thermometry
lookup range. The official `zoom_absolute=32772` control now yields raw14
indices; a standalone experimental lookup matches executed official x86_64
arithmetic for the tested range-120/lens-68 branch. Independent physical
accuracy is still unresolved. Read the sibling repository's
`docs/RADIOMETRIC_INITIALIZATION.md` before further camera/thermometry changes.
Field 356 is a duplicated calibration coefficient, not a live center
temperature.

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

Do not use field 356 as a center-temperature regression target. The native
app derives its live center from a trailer raw index at frame byte 221208.
See `docs/MEASUREMENT_AUDIT.md` and the sibling repository's
`docs/NATIVE_CALL_CHAIN.md` for the evidence.

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

## Code readability and human maintainability

The codebase must remain understandable to a human developer who did not participate in the reverse-engineering work. Prefer clarity over cleverness.

### Comments and docstrings

Add meaningful comments or docstrings when code encodes:

- non-obvious HT-301 protocol behavior or camera-specific invariants;
- binary offsets, trailer fields, UVC controls, timing requirements, or state transitions;
- measurement-validity and frame-rejection rules;
- threading, synchronization, buffering, or lifecycle decisions;
- coordinate transforms or distinctions between native measurement data and presentation-only transforms;
- thermometry formulas and floating-point behavior that must match validated reference behavior;
- platform/device workarounds whose rationale would otherwise be lost.

Comments should explain **why** a behavior exists, what evidence or invariant it protects, and what must not be simplified casually. Do not comment obvious syntax or restate trivial code.

### Named constants instead of unexplained magic values

Do not scatter unexplained camera-specific numbers through implementation code. Frame dimensions, byte offsets, UVC control values, trailer locations, thresholds, and timing values must use descriptive constants where practical.

When a value is reverse-engineered or non-obvious, add a short rationale/source comment or reference the relevant LMThermal documentation. Values such as the radiometric initialization controls `32772`, `32800`, and `32768` must not appear as unexplained literals throughout the codebase.

### Structure and naming

Prefer descriptive identifiers and focused functions. If one function mixes USB/camera I/O, frame parsing, session-state decisions, thermometry, and rendering, split responsibilities instead of compensating with a large comment block.

Public or architecturally important classes/functions should document their responsibility, important invariants, coordinate/data semantics, ownership/lifetime where relevant, and failure states. Do not add verbose documentation to trivial private helpers merely to satisfy a quota.

### Reverse-engineered behavior

When implementation behavior comes from validated LMThermal research or the Desktop reference path, leave enough context for a future maintainer to recognize that unusual behavior is intentional. Reference the corresponding research/documentation file when useful rather than duplicating large notes in source code.

A future maintainer should be able to understand what a component does, why unusual camera-specific behavior exists, which assumptions are confirmed facts versus unresolved approximations, and which parts must not be changed casually without reproducing the underlying evidence.

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


## End-of-task repository sync

At the end of every completed task, after relevant tests or diagnostics pass:

1. review the final diff and remove accidental or unrelated changes;
2. update all documentation affected by the change;
3. update the repository changelog for every meaningful code, behavior, architecture, dependency, hardware, protocol, or thermometry change;
4. run the relevant tests/diagnostics again when practical;
5. commit the complete task on its focused working branch with a descriptive English commit message;
6. push that working branch to GitHub so remote reviewers and other agents can inspect the exact result.

Do not leave completed work only in the local working tree unless the user explicitly asks for that.

Do not merge into `main` automatically. Push the feature/fix/prototype branch and leave merge or pull-request approval to the user/reviewer.

If a task also establishes new hardware, protocol, calibration, or thermometry knowledge, update the sibling `LMThermal` documentation and changelog in a separate commit and push its corresponding branch as well.

Before reporting a task as complete, include:

- branch name;
- commit SHA(s);
- whether the branch was pushed successfully;
- tests/diagnostics run and their result;
- documentation/changelog files updated;
- any remaining uncertainty or follow-up work.
