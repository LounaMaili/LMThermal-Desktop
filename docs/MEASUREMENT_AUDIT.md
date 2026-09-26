# HT-301 measurement audit — 2026-09-26

## Native follow-up (same date)

The subsequent APK audit in the sibling `LMThermal` repository established
the Java/JNI/native caller mapping; see `docs/NATIVE_CALL_CHAIN.md` there.
The app calls `thermometryT4Line` to build a 16,384-entry lookup, then
`thermometrySearch` to map 16-bit trailer summary indices and image words.
The live center raw index is at frame byte 221208 (5165 / 5255 in these
fixtures). Block field 356 is a byte-for-byte copy of the native calibration
coefficient at byte 223498, not the live center reading. Parameter offsets
4/8/12/16/20 mean reflected temperature, ambient temperature, humidity,
emissivity, and uint16 distance. The image words in both fixtures have high
byte `0x80` and exceed the native 14-bit lookup limit; Celsius remains
unvalidated. The sections below preserve the original baseline evidence and
rejected arithmetic as historical context. The diagnostic now reports the
corrected labels and native lookup input checks.

## Evidence examined

- Desktop `thermal_capture.py` and `lmthermal_viewer.py`.
- `LMThermal` `REPO_RULES.md`, `README.md`, `CHANGELOG.md`, and the hardware,
  APK, thermometry-library and specification documents.
- OpenCV/V4L2 captures from the attached T3-317-13, including four initial
  frames, a later frame, and a 160-frame live sample.

At the time of this baseline, no native library binary or native call-site
disassembly was present in either maintained repository. The separate
`LMThermal-Research` folder supplied those binaries for the follow-up above.

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

At baseline, the decoded functions' **argument roles were not established**
by their arithmetic alone. Native tracing has since shown `a` is derived
from a 14-bit lookup index rather than an 8-bit Y value, and `env_term` is
the radiation correction output of `CalcFixRaw`.
Subtracting 25 from approximately 18.6 billion makes almost no numerical
difference, another reason to question the old parameter mapping.

The old desktop hypothesis set `a = Y_center`, `env_term = block offset 4`,
and `b = block offset 352 × block offset 12`, mistakenly calling the factors
gain and emissivity. Java identifies those fields as a calibration coefficient
and humidity. For captured scene A the old inputs give:

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
They do **not** validate field 356 as a thermometer.

The old partial `CalcFixRaw` polynomial at the mistaken Y input 96 yielded
`P = 6.265020896` and `exp(P) = 525.852574`. Native tracing now identifies
its input as ambient temperature and reconstructs the complete normal path.
`InitTempParam` receives the coefficients at frame bytes 223494 and 223498.

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

This establishes that field 356 did not track image changes. The native
follow-up identified it as copied calibration data. The app reads the live
center index from another trailer location; whether the camera computed that
index from a single pixel or an area remains unproven. Field 356 must not be
a temperature regression target.

## Missing evidence and stopping boundary

The call graph, five `CalcFixRaw` input sources, and lookup layout are now
documented in `LMThermal/docs/NATIVE_CALL_CHAIN.md`. The missing evidence is
the official app's camera mode transition and matched native temperature
outputs for controlled frames. The current saved image words exceed the
lookup's 14-bit range. No claimed Celsius matrix, cursor temperature, true
temperature min/max, or Celsius range lock should be computed from them.

Next experiments require the working camera and official app (or verified
replay of its controls), with frames and native center/high/low outputs
captured together for multiple known targets. Record shutter/zero-parameter
frames separately and identify whether center means one pixel or a region.

The new fixture tests cover exact size, field offsets, 288-row image boundary,
metadata exclusion, reproducible decoded arithmetic, and scene-to-scene
parameter stability. They do not test Celsius accuracy.
