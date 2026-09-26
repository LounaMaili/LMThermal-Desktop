# LMThermal-Desktop

Experimental desktop tooling for the Infiray HT-301 / T3-317-13. The current
priority is a reproducible measurement foundation. **Per-pixel Celsius values
from the older viewer and `thermal_capture.py` are unvalidated and can be
wrong.** The Temperature Lock widgets do not yet map colors to a Celsius range.

This desktop repository is paired with [LMThermal](https://github.com/LounaMaili/LMThermal),
which holds the hardware and reverse-engineering documentation.

## Requirements

Use the repository virtual environment when available. The diagnostic needs
Python, OpenCV, and NumPy; the legacy viewer additionally needs PyQt6.

## Measurement baseline

`measurement_baseline.py` parses exact 224,256-byte YUYV frames independently
of PyQt. Captures show 288 image rows (384 × 288), followed by four rows of
non-image data. The documented 514-byte parameter block begins at byte 223742,
inside the final row. Both trailer areas are excluded from image statistics.

`ht301_camera.py` discovers the Infiray `video-index0` device by its stable
`/dev/v4l/by-id/` link, then falls back to USB VID/PID in sysfs. It never
hard-codes `/dev/video2` and does not require sudo when normal camera access is
configured.

To inspect live frames:

```bash
./.venv/bin/python measurement_diagnostic.py --count 3 --interval 0.5
```

To save privacy-scrubbed raw fixtures while inspecting them:

```bash
./.venv/bin/python measurement_diagnostic.py --count 2 --save-dir /tmp/ht301-fixtures
```

Review newly saved fixtures before sharing them. The diagnostic scrambles the
image outside a small center patch and clears the identifier ranges observed
in these captures; other firmware revisions may store identifiers elsewhere.

To inspect a saved frame without the camera:

```bash
./.venv/bin/python measurement_diagnostic.py --frame tests/fixtures/scene-a.raw
```

The diagnostic reports the APK-identified settings, copied calibration
coefficients, image-only Y statistics, 14-bit lookup compatibility, and the
trailer center/high/low raw indices. It also retains the old rejected
`Y → GetTempEvn` calculation as historical evidence. Block field 356 is a
copy of a calibration coefficient; the Android app obtains its live center
reading from a different trailer index and a native lookup. See the
[native call chain](https://github.com/LounaMaili/LMThermal/blob/proto/native-thermometry-chain/docs/NATIVE_CALL_CHAIN.md). No per-pixel
Celsius output is validated for the current Linux fixtures.

Run the hardware-independent tests with:

```bash
./.venv/bin/python -m unittest discover -s tests -v
```

See [measurement audit](docs/MEASUREMENT_AUDIT.md) and the
[fixture notes](tests/fixtures/README.md) for evidence and remaining unknowns.
Changes are recorded in the [changelog](CHANGELOG.md).

## Legacy desktop viewer

`lmthermal_viewer.py` is an early PyQt6 prototype with palettes, camera view,
and UI controls. Its spot, min/max and range-lock behavior must not be used as
validated measurements. Work on those features resumes after the temperature
conversion chain is established.

## License

To be determined.
