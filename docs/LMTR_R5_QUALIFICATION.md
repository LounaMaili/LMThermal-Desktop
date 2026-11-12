# R5 phase2 qualification and integration-review gates

## Verdict

The corrective branch from PR4's8f167244 is ready for **technical review**, with
227 Linux tests passing. PR4's unchanged published tip still contains the reproduced
ownership/UI defects below: integrate/review these fixes before considering merge.
No merge or PR branch rewrite is performed. Current native Windows and real-window
operator qualification are pending. Earlier Windows success applies only to4c73e9f,
not this new code. No new Actions budget, physical device, camera or ADB was used.

See [qualification-results.json](qualification-results.json) for sanitized exact
source/test/resource evidence and [operator recipe](LMTR_R5_OPERATOR_CHECK.md).

## Reproduced defects and targeted corrections

| Boundary | Reproduced behavior | Correction / regression |
|---|---|---|
| File replacement | Pending OpenB overwritten by a seek while oldA still active; A publishes under newest token | Separate source generation and ready barrier; refuse navigation until new owner ready |
| Modal PNG destination | Current frame/palette changes during dialog; can crash on gap or save wrong frame | Freeze immutable frame/colors/range before dialog |
| Close | GUI waits about2s for controlled blocked read (old10s wait path) | Clear/disable immediately; cooperative asynchronous cleanup, no thread termination |
| Worker ownership | Pending/unconsumed result and self-referencing facades retain owners | Clear slots; weak facade ownership; release decoded/navigation caches and descriptor |
| Metadata ownership | Returned mutable entry/header aliases cached validation structures | Deep owned copies across public/worker boundary |
| Local memory admission | Cache ceiling checked after reference decompression | Body budget before body read; explicit decoded/working charge before inflate/numeric decode |
| Opaque primary source | Valid no-geometry source could be indexed as if it had pixels | Exact evidence API, no invented image or temperature |
| Native grid correspondence | Typed raw14 on another grid could supply wrong/out-of-bounds primary pixel | Retain bytes/plane, expose primary raw accessor only for matching grid |
| Inspection identity | Old ROI/pointer can fit new geometry yet refer to another source session | Reset on source/session/grid replacement, disable gestures during gap/load |
| Manual bounds | Invalid manual edit silently prevents Auto palette refresh | Auto ignores unfinished manual values; invalid Locked range explicitly retains last valid presentation, including the PNG inputs and legend |

Four pre-fix ownership/UI regressions were run and failed before corrections (including
modal PNG exception and pending-open wrong-source result). Other cases use deterministic
contract-valid synthetic sources, not edits to canonical fixtures or weakened validators.
Initial attempts to author the differently-sized native-plane test were correctly refused
for invalid ordered roles/block IDs/core payload ID; the corrected source passes the same
unaltered reference validator. These were test-plan errors, not reader conformance failures.

## Functional and hostile qualification

- All51 original canonical byte hashes/verdicts, all positive event adapters, known/
  unknown gaps, committed corruption, required-feature refusal and explicit recovery
  remain covered. No schema/vendor algorithm/corpus member changed.
- Exact original Float32 signed zero, validity mask, native/transport/views, optional
  semantic metadata and read-only arrays remain authoritative. Presentation never
  rebuilds thermometry. Original body/logical integrity checks still execute fully.
- New tests cover pre-read/pre-decode refusal, metadata nonaliasing, cache eviction
  before full scan, mid-operation source-change refusal, prompt owner release, opaque
  evidence and another native grid, session/geometry transition, gap clearing, rapid
  file replacement, cooperative Close and modal export freeze.
- Existing LMTX, legacy still, `.lmthermal` recording/playback, ROI/presentation/offline
  and Linux-only discovery/support tests all pass in the full227-test suite, no skips.
- Re-read existing synthetic ART run43238655791 using the new adapter:81 STORED and74
  DEFLATE observations have exact original payload hashes/lengths and full-grid ROI
  parity;245 missing observations remain gaps; both sources are byte-identical before/
  after full validation. This is a **new host read of old ART files**, not a new ART test.

## Resource and lazy-access probes (Linux only)

Fresh reader processes are separated from writer generation. No forced GC or queue
increase. `tools/qualify_lmtr_resources.py` constructs only noncanonical disposable
sources when explicitly invoked; no corpus generation/import side effect.

| Synthetic source / operation | Source bytes | Decoded cache | Peak process RSS (sample) |
|---|---:|---:|---:|
|2048×2048 Float32, DEFLATE-1, select/whole-grid ROI |89835|16777216|88308KiB|
|Sparse opaque source, select event71 beyond4GiB |4404323163|16|66080KiB|

The large grid has4194304 valid25°C **synthetic** values and exactly16777216 original
Float32 bytes. The sparse target begins at offset4404298782; six validated records,
21644 navigation-cache bytes and593920 physically allocated bytes suffice for the
selected read. No geometry/Celsius is invented for its opaque16-byte payload.
Both retain one decoded CHUNK and unchanged inode/size/mtime after access. Selected
CHUNK is fully verified; file scope remains `opened_records`, **not whole-file integrity**.
No entire4.4GiB scan or before/after whole-file hash is claimed by this navigation probe.
The same current code separately full-validates normal complete sources/corpus/ART files.

These are single finite-process samples with dependencies/allocator/version overhead.
They do not prove a total RSS bound, malicious timestamp restoration detection,
indefinite-runtime performance or acceptance of every wire-valid file under local limits.
Body/cache/working estimate limits remain local UI refusals, not frozen wire limits.

Reproduction, in the Desktop venv on Linux:

```bash
python tools/qualify_lmtr_resources.py /new/private/resource-probe
# Optional >4GiB source needs the pinned Android R3 builder (review/import construct only):
PYTHONPATH=/path/to/LMThermal/tools python tools/qualify_lmtr_resources.py \
  /another/new/private/resource-probe \
  --sparse-builder /path/to/LMThermal/tools/check_lmtr_sparse.py
```

The builder used here is pinned to Android f55bb5d's existing R3 tool. No modification
of that tool or canonical sources is necessary. Initial probe assertion incorrectly
called the navigation scope `navigation_only`; it was corrected to the actual exact
reference value `opened_records`, then the same immutable generated files were read
in fresh processes. No byte regeneration or relaxed integrity check followed.

## UI and remaining gates

Nine deterministic new Qt UI tests pass offscreen, alongside five original LMTR UI
tests and the rest of the227-test suite. A screenshot of the owned offline window was
rendered and inspected for controls,3×2 cells/mask, extrema and legend. This is automated
widget evidence, not operator usability/aiming acceptance.

A native `xcb` attempt failed **before window creation**: no connection to display:0
and Qt reports xcb/platform dependency availability. No system/display changes or
package installation were made. It is an environment limitation, not a native-window
pass. Run the short recipe on a real Linux desktop and Windows, including DPI/resize,
modal chooser, source replacement, gap/error clearing and normal process exit.

Cooperative cancellation is between reference operations. A single synchronous read,
hash/decode or filesystem close still has no certified latency bound. Close leaves a
disabled owned window until cleanup, never abandons a live QThread. Explicitly exercise
large/corrupt sources under Windows and low-resource conditions before claiming broad
platform robustness. An external writer is unsupported; stat/fstat guards are not an
atomic snapshot or a security guarantee against deliberate file replacement races.

Current blockers for a merge recommendation: bring corrective branch into PR review,
qualify the exact new runtime on Windows with a new authorization, and perform real
window/operator checks. R4 physical Full recording, safe live-heap admission, sustainable
25Hz and future real-producer LMTR interoperability remain **separate** unfinished
product gates; they are not hidden by synthetic reader conformance.

Native-equivalent temperatures; absolute physical accuracy not yet independently validated.
