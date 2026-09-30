# Radiometric capture format v1

The PyQt viewer's **Save radiometric capture** button is enabled only for a
current `radiometric_ready` observation with a valid `MeasurementFrame`.
Clicking it first freezes the displayed measurement, palette, effective Celsius
bounds, and auto/locked range mode. A destination dialog then selects a basename.
Saving does not recompute thermometry or change the camera session.

An active rectangular ROI adds an optional `roi` object to JSON, preserving
format version 1. Captures without a ROI omit that key. Its `geometry` fields
`x1_px`, `y1_px`, `x2_px`, `y2_px` use the explicit `coordinate_semantics:
half_open`: `temperature_c[y1_px:y2_px, x1_px:x2_px]`. Bounds are in the native
384 × 288 image and the rectangle is nonempty. `statistics` stores `min_c`,
`max_c`, `mean_c`, `pixel_count`, `min_xy_px` and `max_xy_px` for this exact
captured matrix slice. The mean uses float64 accumulation without smoothing;
ties use the first row-major pixel. Arrays and the clean PNG remain unchanged.

Each basename produces three sibling files:

| File | Contents | Role |
| --- | --- | --- |
| `<name>.png` | Native-orientation 384 × 288 RGB thermal image using the captured temperature matrix, palette and Celsius bounds | Human-viewable rendering only |
| `<name>.npz` | `temperature_c` (`float32[288,384]`), `raw14` (`uint16[288,384]`), `raw_transport` (`uint8[224256]`) | Lossless measurement and original-frame evidence; source for future re-rendering/import |
| `<name>.json` | `format: lmthermal-radiometric-capture`, `version: 1`, UTC capture and frame times, dimensions/dtypes, native orientation, image-word range, high/low values and coordinates, literal `(192,144)` center and separate trailer center, parsed settings/calibration parameters, native-equivalent lookup trace, palette and effective Celsius bounds, range mode, raw-frame hash, PNG/NPZ hashes, accuracy warning | Interpretation and completion marker |

All pixel coordinates use the original 384 × 288 camera image. The last four
transport rows are included only in `raw_transport`, not in either image matrix.
`temperature_c`, `native_equivalent_c`, `effective_min_c` and `effective_max_c`
are Celsius. The copied `parameters` keep their parser names: reflected and
ambient temperatures and the correction are Celsius quantities; humidity and
emissivity are fractions; `distance` is the camera's stored uint16 setting,
with no independently validated physical unit. Calibration coefficients and
`lookup_trace` retain their native-equivalent diagnostic meanings and should
not be treated as independent surface-temperature measurements.
The literal center pixel and trailer center reading remain separate fields;
their values can differ. Temperatures are values from the existing
native-equivalent range-120 measurement pipeline. The PNG is generated from
`temperature_c` using `celsius_palette.py` and can be recreated with another
palette or range from the NPZ matrix. Neither the PNG nor its normalized
8-bit palette levels are thermometry input.

Validated independent read:

```python
from pathlib import Path
from radiometric_capture import load_capture

capture = load_capture(Path("capture-name.json"))
temperature_c = capture.temperature_c  # owned, read-only float32[288,384]
raw14 = capture.raw14                  # owned, read-only uint16[288,384]
metadata = capture.metadata            # separate JSON object
raw_transport = capture.raw_transport  # bytes, or None when omitted
```

## Loader contract

`load_capture` supports exactly identifier `lmthermal-radiometric-capture`,
integer version 1, native orientation and the declared native dimensions and
dtypes. It derives `.npz` and `.png` siblings from the selected JSON filename;
filename/path fields added to JSON are never followed. All three files and
`files.png_sha256` / `files.npz_sha256` are required. There is no separate
Boolean completion flag in v1: JSON publication is the completion marker.
Hashes detect companion-file corruption; they do not authenticate the JSON
or the physical measurements.

The loader checks bounded file sizes, NPZ member names and NPY headers before
allocation, and uses `allow_pickle=False`. It requires C-order little-endian
float32/uint16 image matrices, finite temperatures and full raw indices below
`0x4000`. It validates finite structured metadata, UTC times, parameters,
presentation bounds, native high/low coordinates/values and literal center
against the stored matrices. Trailer center remains a separate recorded
reading; no center-region or thermometry algorithm is inferred on import.
Optional ROI bounds/count/coordinates are checked exactly; min/max/mean are
checked against the exact matrix slice within 1e-4 °C absolute and 1e-6 relative
floating-point tolerance. Read-only arrays are backed by owned immutable
bytes, and metadata access cannot mutate the model.

The current exporter always includes `raw_transport`. The loader also accepts
a v1 set without preserved transport when the NPZ member **and** JSON
`transport_bytes` / `raw_transport_sha256` declarations are omitted together.
When present, transport must be uint8[224256], match its SHA-256, and its image
bytes must equal raw14. Declared-but-missing transport is rejected. Captures
without ROI omit `roi`; absence is valid. Unsupported versions, malformed
metadata, invalid arrays, missing completion/companions and hash mismatches
raise `CaptureError`; the UI displays the reason without using partial data.

The stored `temperature_c` matrix is authoritative on reopen. The loader does
not rebuild a LUT or derive temperatures from raw14 or the PNG. Offline
re-rendering uses `celsius_palette.py` on that matrix and writes a new clean
PNG only, with no overwrite of any original JSON/NPZ/PNG or other existing
file. Original metadata keeps the original presentation and ROI even after
interactive changes.

All three files are required for a complete v1 capture. The exporter writes
temporary files in the destination directory, publishes complete files without
overwriting, and removes files it published if an ordinary write/publish step
fails. JSON is published last as the completion marker. A process or machine
crash between file publications can leave an incomplete set; consumers should
require all three files and can verify the hashes in JSON. The user must choose
an existing writable destination directory.

The exact original transport bytes may contain device metadata or a private
scene. Review captures before sharing. Native-equivalent temperatures;
absolute physical accuracy not yet independently validated.
