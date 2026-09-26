# HT-301 measurement audit — 2026-09-26

## Evidence examined

- Desktop `thermal_capture.py` and `lmthermal_viewer.py`.
- `LMThermal` `REPO_RULES.md`, `README.md`, `CHANGELOG.md`, and the hardware,
  APK, thermometry-library and specification documents.
- OpenCV/V4L2 captures from the attached T3-317-13, including four initial
  frames, a later frame, and a 160-frame live sample.

No native library binary or native call-site disassembly is present in either
repository. The `CalcFixRaw` notes are explicitly partial.

## Confirmed by captures or decoded arithmetic

| Observation | Evidence |
|---|---|
| Transport is `uint8[292,384,2]`, 224,256 bytes | Exact size and shape on captured frames |
| The useful picture occupies rows 0–287, 221,184 bytes | Row 288 contains sparse binary data, rows 289–290 are zero-filled, row 291 contains parameter and identifier data; rows 0–287 contain picture-like Y and chroma bytes |
| Documented parameter block begins at 223742 and is 514 bytes | Captured little-endian values at documented offsets match plausible settings and repeats |
| `GetTempEvn` arithmetic is `((a+273.15)^4 - env_term) * b`, fourth root, then minus 273.15 | Disassembly transcribed in `LMThermal/docs/THERMOMETRY_LIB.md` |
| `InitTempParam(x,y)` returns `y/(2x)` and its square | Same disassembly |
| Full-frame extrema can read metadata | Original scene A Y maximum is 255 at row 291; the 288-row image maximum is 243 |

The parameter offset is 2,558 bytes after the end of the useful image. Thus
excluding only the last 514 bytes still leaves trailer data in rows 288–291.
The earlier documentation's “288 usable rows” was correct, while its claim
that all bytes before offset 223742 are image bytes was not.

## Inferences and rejected approximation

The decoded functions' **argument roles are not established** by their
arithmetic alone. In particular, the native `a` argument has not been shown to
be an 8-bit Y value, and `env_term` has not been shown to be a Celsius value.
Subtracting 25 from approximately 18.6 billion makes almost no numerical
difference, another reason to question the old parameter mapping.

The desktop code sets `a = Y_center`, `env_term = env_temp1`, and
`b = gain × emissivity`. For captured scene A these inputs give:

| Intermediate | Value |
|---|---:|
| Transport-center Y at (192,146) | 96 |
| Candidate `b` | 0.121725 |
| `a + 273.15` | 369.15 |
| Fourth power | 18,569,982,353.117 |
| After subtracting 25 | 18,569,982,328.117 |
| After multiplying by `b` | 2,260,431,074.871 |
| Fourth root | 218.04585 |
| Candidate result | **−55.104 °C** |
| Frame field 356 | **35.992** |

For scene B, transport-center Y is 107 and the legacy result is −48.607 °C,
while field 356 remains 35.992. These calculations reject the current mapping.
They do **not** independently validate field 356 as a thermometer.

The partial documented `CalcFixRaw` polynomial at candidate input 96 yields
`P = 6.265020896` and `exp(P) = 525.852574`. Its input, factors, later
corrections, and relation to frame fields are still unproven. `InitTempParam`
can be reproduced mathematically but no frame-field mapping to its `x` and
`y` inputs is justified.

## Field 356 and center semantics

Four initial frames had different image bytes but an identical parameter
block. In a 160-frame sample, 158 frames had field 356 = 35.99200058 and
field 352 = 0.27050000; two frames had both fields zero. The transport-center
Y ranged from 115 to 125 among the nonzero-field frames. A later saved scene
has transport-center Y 107 and still has field 356 = 35.99200058. Its whole
image mean Y is 109.75, versus 83.99 in scene A.

An end-to-end run of the new diagnostic later found transport-center Y = 201
and image mean Y = 142.72 with the same field 356 = 35.99200058. Under the
rejected legacy mapping this produced 6.916 °C, still 29.076 °C below field
356. The large image change with a constant field strengthens the conclusion
that this field cannot currently be treated as a verified live center reading.

This establishes that field 356 did not track these image changes. It may be
calibration data, a stale reading, or some other value. The experiment cannot
decide whether an actual firmware center measurement is a pixel or an area
average, nor whether its coordinate is row 144 (center of the 288-row image)
or row 146 (center of the 292-row transport). It must not be a formal
regression target yet.

## Missing evidence and stopping boundary

The exact call graph and argument mapping of `thermometryT`, `CalcFixRaw`,
`GetTempEvn`, and `InitTempParam` are missing. The role/order of auto gain,
emissivity, distance, environment, calibration, offset, and possible shutter
state cannot be derived from the present notes or two scenes. The app must
not compute a claimed Celsius matrix, cursor temperature, true temperature
min/max, or Celsius range lock from Y until that chain is established.

Next experiments need a native library and call-site trace (or equivalent
firmware protocol evidence), plus controlled targets of known temperature
and emissivity moved through the center while capturing frames and all fields.
Record shutter/zero-parameter frames separately. Compare row 144, row 146,
and neighborhoods only after a live firmware measurement field is identified.

The new fixture tests cover exact size, field offsets, 288-row image boundary,
metadata exclusion, reproducible decoded arithmetic, and scene-to-scene
parameter stability. They do not test Celsius accuracy.
