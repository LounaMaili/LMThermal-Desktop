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

Example independent read:

```python
import json
import numpy as np
from pathlib import Path

base = Path("capture-name")
metadata = json.loads(base.with_suffix(".json").read_text())
with np.load(base.with_suffix(".npz"), allow_pickle=False) as arrays:
    temperature_c = arrays["temperature_c"]
    raw14 = arrays["raw14"]
    raw_transport = arrays["raw_transport"].tobytes()
```

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
