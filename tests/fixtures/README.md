# HT-301 raw transport fixtures

These two 224,256-byte frames were captured from the connected HT-301 on
2026-09-26 using OpenCV's V4L2 backend with RGB conversion disabled. Both
retain the observed 384 × 292 YUYV transport structure and the known numeric
parameter offsets. The camera identifier was erased from two trailer ranges.

| Fixture | SHA-256 | Scene treatment | Transport center Y | Native trailer center index | Block field 356 |
|---|---|---|---:|---:|---:|
| `scene-a.raw` | `90006c5ec49e82b126cd961d074d38840013e654939522b9c18e977ca818353b` | YUYV four-byte groups shuffled outside an 8 × 8 center patch to anonymize a person | 96 | 5165 | 35.99200058 |
| `scene-b.raw` | `37578b11b89656ff3220f711721195f250613a7ab85086fa6f3d0b3243cfbb7d` | Spatial scene retained after visual inspection found no identifying content | 107 | 5255 | 35.99200058 |

Both have reflected and ambient temperatures of 25 °C, humidity ≈ 0.45,
emissivity ≈ 0.98, distance = 1, and calibration coefficient 0 ≈ 0.2705.
All 288 image rows are retained; rows 288–291 are trailer. `scene-a.raw` has
a valid image Y maximum of 243, whereas treating the whole 292-row transport
as an image yields 255 in row 291.

The two scenes differ strongly in mean Y (83.99 versus 109.75), but field 356
is identical. It is a copy of the coefficient at byte 223498, while the native
live center indices at byte 221208 differ. Every image word has a `0x80` high
byte, putting it above the Android lookup's 14-bit range. These fixtures do
not provide expected Celsius values. The shuffled scene is unsuitable for
spatial or center-region inference beyond its preserved center patch.
