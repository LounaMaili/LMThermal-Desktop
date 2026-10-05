# LMTX v1 import and generic offline analysis

The normative authority is the Accepted v1.0
[LMThermal Exchange Format specification](https://github.com/LounaMaili/LMThermal/blob/1cbdd776a485c43e1e7cfa104e3741d1b7e99658/docs/LMTX_FORMAT_V1.md).
This document describes the Desktop consumer, not another schema.
[LMThermal #4](https://github.com/LounaMaili/LMThermal/issues/4) remains decision
history; [Desktop #1](https://github.com/LounaMaili/LMThermal-Desktop/issues/1)
tracks implementation and
[Android #5](https://github.com/LounaMaili/LMThermal/issues/5) is the producer
counterpart. The producer/corpus baseline is commit
`1cbdd776a485c43e1e7cfa104e3741d1b7e99658`.

## Open without a camera

Install [requirements.txt](../requirements.txt) in a Python 3.10+ environment,
then run:

```bash
./.venv/bin/python lmthermal_viewer.py --lmtx /path/to/capture.lmtx
```

**Open still capture (.lmtx / legacy)** also opens LMTX. ZIP content is
recognized even when renamed; the validated manifest decides format/version.
The `.lmtx` suffix does not bypass validation. There is no HT-301 fallback.
No discovery, USB open, initialization or camera controls occur on import.
Switching from live mode first releases the existing worker and finalizes
logging/recording. Camera acquisition modules are imported only on explicit
Connect; live acquisition remains Linux-only.

The offline loader runs outside Qt's UI thread. Switching source or closing
requests cooperative cancellation and waits for shutdown. Sender/generation
checks suppress obsolete results. The previous image/readings/ROI/legend
are cleared before validating a new file; a failed import cannot display
partially validated or stale measurements.

## Reader boundaries

| Module | Responsibility |
| --- | --- |
| `lmtx_container.py` | ZIP structure, paths, streaming inflation, CRC and member SHA-256; no extraction |
| `lmtx_json.py` | Bounded UTF-8 JSON, duplicate keys, exact optional decimal values |
| `lmtx_schema.py` | Manifest types, version/features, inventory, references and cross-field semantics |
| `lmtx_images.py` | Bounded PNG/JPEG validation and stored-pixel decoding |
| `lmtx_ht301.py` | Declarative known HT evidence checks; no LUT/arithmetic reconstruction |
| `lmtx_reader.py` | Entire-file validation and immutable model publication |
| `offline_measurement.py` | Owned source planes, generic statistics, presentation transforms and PNG saving |
| `offline_load_worker.py` | Cancellable Qt loading and result delivery |

The API is `load_lmtx(path, cancelled=...) -> (OfflineMeasurement, timings)`.
No source model is returned until **all** inventory members, including optional
opaque payloads, pass exact lengths, CRC and SHA-256. The reader uses one
open archive handle and detects size/timestamp changes during validation.
Whole-archive SHA-256 records source identity, not authenticity or calibration.

ZIP checks include matching local/central headers and data descriptors,
complete contiguous record coverage, safe lowercase ASCII paths, duplicates,
parent/file conflicts and Windows reserved names. ZIP64, encryption, split
archives, links, special/executable members, unsupported compression,
overlapping records, preambles and trailing content are rejected. STORED and
DEFLATE are supported with bounded output and actual length/eof checks.
No archive member path is extracted or executed.

JSON is UTF-8 without BOM, duplicate keys, NaN or infinity. Understood numeric
fields must fit their declared types/ranges; bounded unknown optional JSON
numbers use exact decimal values rather than a lossy binary64 conversion.
This implements the owner's **semantic JSON-value preservation** clarification;
original number spelling is not promised. Opaque extension JSON/binary bytes
are also retained exactly. No plugins, scripts, external resources or arbitrary
parent paths are evaluated.

Major 1 compatible minors are accepted when all required features are known.
Unknown optional fields, module extensions, shapes and palettes remain data.
Errors expose stable codes: `unsupported_format`, `unsupported_major`,
`unsupported_kind`, `unsupported_required_feature`, `invalid_container`,
`invalid_manifest`, `unsafe_path`, `missing_payload`, `integrity_mismatch`,
`invalid_payload` and `resource_limit`. Cancellation publishes no result.

## Resource policy

The accepted ceilings are enforced: 256 MiB archive, 128 MiB uncompressed
member, 512 MiB total uncompressed content, 256 members, 2 MiB central
directory, 240-byte paths, 1 MiB manifest/extension JSON, 32-container nesting,
65,536 JSON properties/elements, 16 KiB strings and 33,554,432 primary/image
pixels. Reads/inflation use at most 64 KiB output blocks; temperature checks
and sequential statistics use 8,192-pixel blocks.

For an interactive owned model, Desktop additionally applies these **local
resource limits**, permitted by the contract:

- 128 MiB combined retained uncompressed payloads and decoded image RGB;
- 8,388,608 primary-grid pixels and decoded pixels per image;
- 67,108,864 total pixel visits for stored full-grid/ROI statistics checking;
- 16 KiB per JSON numeric token.

An otherwise conformant capture above a local budget is refused as
`resource_limit`, never truncated, downsampled or described as corrupt.
Payload bytes are retained once in the final model; byte-backed NumPy views
share them. Assembly temporarily retains chunks plus the joined member, and
image decoding/rendering has additional bounded allocations. These budgets
are not a promise that total process RSS is 128 MiB. Memory exhaustion is
reported cleanly. Validation/rendering is still-only, not a sequence cache.

## Authoritative measurements and analysis

`OfflineMeasurement` owns native geometry, optional temperature/mask/native
planes, clocks/frame identity, source/module/model, provenance, exact payload
bytes, opaque evidence, analysis and presentation. Frozen metadata and
immutable byte-backed arrays survive loader/cache changes. Metadata access
returns a detached copy. Native sample encoding is explicit; unknown native
encodings are not guessed to be raw14.

Saved little-endian float32 Celsius is authoritative. Import does not run
thermometry, rebuild a LUT, apply corrections or infer values from preview
colors. The source bytes, including valid negative zero, stay exact. With no
mask every cell is valid. With `validity.u8`, only 1 means valid and 0 means
invalid; other bytes fail. Invalid cells must contain canonical positive-zero
bits but produce **no reading**, never 0 °C or a stale value. Valid cells must
be finite. Invalid render pixels are explicitly magenta and are excluded
from display scaling, extrema and ROI statistics.

Hover/drag uses actual native dimensions, top-left origin and `[y,x]` indexing.
Rectangles are `[x1,x2) × [y1,y2)`. Statistics report total/valid counts,
min/max/mean and first row-major extrema ties. Means use sequential binary64
addition, without smoothing or pairwise/reordered summation. Saved statistics
are checked with exact counts/locations and the contract tolerance
`max(1e-4 °C, 1e-6 × abs(calculated))`. Saved point readings are checked against
the valid source cell. An all-invalid ROI reports counts and no numbers.

Known native saved points, rectangles and text annotations are overlaid; the
first saved rectangle initializes inspection ROI. Clearing/redrawing changes
only in-memory inspection. Future shapes remain visible in metadata without
guessed rasterization. Additional visible-image payloads and their coordinate
spaces are validated and preserved; they are not implicitly registered onto
the thermal viewport. Creating/persisting new annotations or edited LMTX
derivatives is outside this milestone.

The literal center is `(floor(width/2), floor(height/2))`. HT trailer center
remains a separate extension observation and is never inferred to equal that
pixel. Known `org.lmthermal.camera.ht301` schema-1 evidence validates raw14
range, transport/native relationship and supplied settings/calibration/trailer
types. Full transport is retained only when supplied; recording adapters do
not fabricate it. Future optional HT fields remain opaque. Generic analysis
needs neither this extension nor any native plane.

## Presentation and source immutability

Supported palette IDs map to White hot, Black hot, Inferno, Iron-like and
Turbo. An unknown optional palette has a disclosed White hot fallback while
retaining its original ID. Initial rendering restores saved effective bounds;
later Auto uses valid-pixel 2nd/98th percentiles and Manual uses entered Celsius
bounds. Rotation by quarter turns followed by mirroring applies only to the
rendered image; hover, markers and rectangles reverse-map to native coordinates.
No matrix, mask, native samples, provenance or ROI numbers change.

Preview-only 160×120 sources display their stored image and metadata with no
Celsius legend, point values or ROI temperatures. A `no_valid_pixels` source
may retain presentation intent but has no current readings or legend. Empty
image/evidence-only sources are identified honestly; values are not invented.

**Save rendered image** writes a clean presentation-sized PNG, without
overlays. Radiometric renders use source Celsius, not stored preview pixels.
Preview-only renders use the validated preview. Export freezes settings/model
before the destination dialog, refuses existing destinations and protected
source paths (including the entire legacy recording directory), and leaves
the source unchanged. Exclusive creation and cleanup of a partial file owned
by this export work without Unix hard links; this is not a universal atomic
publication or power-loss guarantee. No LMTX serializer/derivative writer is
implemented. Lineage references/mappings are structurally checked and retained;
full parent-byte preservation cannot be established without the parent, which
is not opened automatically. File integrity does not authenticate lineage.

## Legacy coexistence

Strict `lmthermal-radiometric-capture` integer v1 and
`lmthermal-radiometric-recording` integer v1 readers remain unchanged.
Their validated stills/selected frames adapt into the same offline model,
retaining exact Celsius/raw14, metadata, distinct center observations and
supplied transport. No files are relabeled or rewritten on open. Timeline,
gaps/drops/uncommitted frames, two-chunk cache and recovery remain recording
responsibilities. Offline extrema derive from the authoritative stored matrix;
camera/trailer summaries remain original evidence. Live orientation and
camera/session/thermometry arithmetic are unchanged.

See [legacy stills](RADIOMETRIC_CAPTURE_FORMAT.md),
[playback](RADIOMETRIC_PLAYBACK.md) and
[PyQt source integration](PYQT_RADIOMETRIC_MVP.md).

## Validation on Linux

The [exact shared corpus](../tests/fixtures/lmtx/README.md) passes: eight valid
captures and three intended error codes across eleven Android archives.
It covers exact float32, masks/all-invalid, preview-only 160×120, alternate
7×19, sanitized HT evidence, compatible minor and optional extensions/shapes/
palettes. Additional negative tests cover container, JSON, SHA, shapes, images,
resource limits and deterministic statistics. UI tests cover cancellation,
source transitions, corrupt-result clearing, transforms and legacy adapters.

The actual Pixel Android SAF export also passes camera-free import, using its
independent Android source-bit proof. Results from exact stored float32:

| Property | Result |
| --- | --- |
| Geometry | 384×288 |
| Temperature source SHA-256 | `6a4508cdbe2e76dbd6ec870351cc4e2d2eb7fb364e230f93d7038c2d18fab7a6` |
| ROI | `[169,291) × [76,168)`; 11,224 valid of 11,224 pixels |
| ROI min / max / mean | 35.85474395751953 / 37.58372497558594 / 36.80378652077781 °C |
| ROI min / max coordinates | `(173,76)` / `(196,149)` |
| Presentation | Turbo; Manual 25–45 °C |
| Native / transport bytes | 221,184 / 224,256; image bytes equal native raw14 |
| Native range | 5109–5625 |

Settings/calibration/trailer evidence and provenance remain present. All five
palettes, ROI calculation and PNG saving preserve the whole source archive
hash. The operator confirmed hover, saved/new/cleared ROI, resize alignment
and palette/range independence: “All works great.” The real scene/export and
source-proof files stay private and are not committed as regression fixtures.

Repeat the private interop check with operator-supplied paths:

```bash
./.venv/bin/python tools/check_lmtx_interop.py /path/to/actual.lmtx /path/to/android-source-proof.json
QT_QPA_PLATFORM=offscreen ./.venv/bin/python -m unittest discover -s tests
git diff --check
```

The full suite passes **189 tests**, including legacy regressions. Curated
[persistent results](diagnostics/2026-10-05-lmtx-import.json) omit private
images, paths and identifying capture metadata. Five-repeat local medians
without tracemalloc:

| Input | Metadata / integrity / binary model | Total load | Render | Full-grid ROI |
| --- | --- | --- | --- | --- |
| Actual HT still | 0.606 / 1.976 / 4.137 ms | 6.673 ms | 0.502 ms | 0.845 ms |
| Synthetic 1024×1024 | 0.393 / 13.070 / 11.439 ms | 24.944 ms | 5.647 ms | 7.429 ms |

Retained payloads are about 0.98 MB / 4.19 MB respectively; the HT preview adds
0.33 MB decoded RGB. Separate single-load/render tracemalloc peaks are
4.03 MB / 29.39 MB. Tracemalloc excludes some native-library allocations and
is not total process RSS. These are local observations, not performance
guarantees. The synthetic values test allocation/throughput, not calibration.

## Windows acceptance boundary

**Real Windows offline validation is pending.** Linux tests, Windows-safe path
checks, mocked platform guards and avoiding Linux acquisition imports do not
prove Windows interoperability. No Windows host was available in this task.
Desktop #1 remains open; its Windows criterion is unsatisfied. Linux real
Android → Desktop interoperability now satisfies the producer-consumer proof
for Android #5; broader cross-platform acceptance and issue closure remain
review decisions. No issues are closed automatically.

On a Windows host, create a Python 3.10+ environment and run in PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_lmtx*.py'
.\.venv\Scripts\python.exe tools\check_lmtx_interop.py C:\captures\actual.lmtx C:\captures\android-source-proof.json
Remove-Item Env:QT_QPA_PLATFORM
.\.venv\Scripts\python.exe lmthermal_viewer.py --lmtx C:\captures\actual.lmtx
```

Supply the same private actual export/proof deliberately, verify all eleven
corpus results and exact source bits/ROI/provenance, then perform the visible
hover/resize/ROI/palette/range/PNG check. Record Windows/Python/dependency
versions and source immutability. No camera or USB driver is needed.

**Native-equivalent temperatures; absolute physical accuracy not yet
independently validated.** Archive/numeric parity establishes data and
algorithm preservation, not physical calibration.
