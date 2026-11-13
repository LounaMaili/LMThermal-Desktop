# R5 read-only LMTR recording analysis

Work in progress for [Desktop #2](https://github.com/LounaMaili/LMThermal-Desktop/issues/2).
Authority: [frozen LMTR v1](https://github.com/LounaMaili/LMThermal/blob/8eb4aceaf8bb36a8061c932b3121f898b81b4af3/docs/LMTR_WIRE_V1.md).
No new schema, producer, recording UI, historical converter or R6 export is introduced.

## Implementation and ownership

`lmtr_reference` vendors the independent Python R3 implementation from the accepted
integration, preserving reader algorithms. Only the default schema resource location
changes. `PROVENANCE.json` pins source hashes; the frozen schema and 51 canonical files
are copied unchanged, not regenerated. Tool-only writer helpers are inherited source,
not a Desktop recording/conversion API. Tests compare every original hash and verdict.

`lmtr_reader.LmtrPlaybackModel` wraps the validator and returns existing owned
`OfflineMeasurement` values. Original Float32/mask/native/acquisition bytes and unknown
optional semantic JSON are retained. Restricted same-observation views use the exact
parent acquisition slice. Native-only/visual-only observations never acquire fabricated
Celsius. Standardized raw gray/RGB/RGBA preview is display-only; opaque visual evidence
remains opaque. A source-change stat guard refuses observed replacement/modification;
it is not an atomic snapshot, protection against malicious timestamp restoration or
concurrent writer certification. Use finalized/static sources.

Normal open verifies HEADER/version/features/END/checkpoint/navigation; selected access
fully validates the CHUNK before publishing any plane, including generator checks after
yield. **Verify entire file / prefix** checks all committed history and chronology.
No claim that an unvisited corrupt payload is valid follows from an opened file.
Explicit recovery uses the reference full-prefix procedure and retains unknown tail,
original completion status and failure evidence. Unsupported required features and
committed source corruption still reject. There is no salvage-to-success fallback.

## Bounds and UI

One worker owns one descriptor and coalesced request/result slots. All index/file/hash/
decompression work runs there. A slider does not eagerly load duration-sized matrices
or a timeline list. Timer requests wait for an in-flight request instead of endlessly
superseding slow work. User scrubs can supersede pending work; obsolete results do not
publish. Shutdown cancels between records; one synchronous operation has no proven
latency bound. Close disables/clears the window immediately and completes asynchronously
when its worker releases the descriptor; it never blocks the GUI or terminates a thread.

One decoded-CHUNK cache is at most64MiB; evict before loading. This is **retained decoded
bytes**, not total process memory. Reference input body up to65MiB, decompression/
logical-slice/JSON/statistics scratch, a selected owned frame, result/UI copies, image
and Qt graphics can coexist. The reference currently decodes once for validation and
again to materialize analysis blocks; no claim of zero-copy or memory-admission proof.
Navigation caches retain at most16 records/8MiB raw bodies, at most two pages/depth.
Reference reporting state has a65536-record/hash local budget. Desktop's current slider
admits at most1000000 events; recovered fixed ranges cap65536 CHUNKs. These are explicit
local refusals, not v1 wire changes. Large-file/indefinite-runtime performance is unproven beyond the finite probes below.

Intake seek compares exact integer nanoseconds. Display/playback seconds are derived
presentation values, never sensor-time substitutes. All events (including gaps and
segment boundaries) remain navigable. Tied intake times may share a display position.
The UI initially lands on the first observation in the already validated initial
CHUNK. This avoids a blank segment-declaration screen for normal recordings and the
canonical examples. It does not scan later CHUNKs or choose only favorable/available
measurements. Control-only/empty initial CHUNKs keep event zero. Previous/seek still
select every original segment/gap and clear the image; the model's default index is
unchanged. No source bytes, order, validation scope or cache/resource limit changes.
Point/ROI use stored native geometry and validity; source/session/native-grid changes clear inspection rectangles and pointers,
even when the old bounds would still fit. Gaps disable new image gestures. Source metadata stays separate from inspection edits. Palette,
range and PNG change presentation only. PNG uses exclusive creation and protects the
original file; it is not an archive rewrite or physical power-loss guarantee.

## Validation and Windows preparation

Run the complete established hardware-independent suite in the repository venv:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
```

The new tests include all51 pinned corpus cases,33 positive adapters, masks/signed zero,
full/recovery corruption, native-only refusal of Celsius, cache ownership, source-change
rejection, exact time seek, ROI/palette/PNG and asynchronous UI states/shutdown.
Linux full suite:207 pass, no skips. Native Windows2022/Python3.13.17 run
[43241547022](https://github.com/LounaMaili/LMThermal-Desktop/actions/runs/43241547022)
on runtime/test source4c73e9f:200 tests,196 pass and four Linux-only skips. The earlier
run43240927585 remains a recorded failure: one test closed its descriptor only after
Windows tried to remove its temporary directory. Moving that close into `finally`
corrected test ownership without changing application behavior or assertions.

The actual new R5 adapter also consumes81 STORED and74 DEFLATE retained observations
from synthetic hosted ART R4 run43238655791, comparing every Float32/native/acquisition
hash and length with the original module oracle, whole-grid ROI with matrix statistics,
and unchanged source hashes. All245 represented missing observations remain gaps;
no temperatures are invented. This is synthetic interoperability, not physical Android.
The displayed Linux offscreen layout was inspected; real operator interaction remains
unperformed. The final evidence-only commit uses `[skip ci]` to avoid an unnecessary
extra run; the above SHAs identify the exact tested runtime sources.
Native Windows is a separate hosted validation, not inferred from Linux or portable
syntax. Offscreen Qt tests are not an operator aiming/visual/usability acceptance.

No physical camera or Pixel access occurred. The historical real LMTX/legacy evidence
is preserved; it does not establish real R4 LMTR interoperability. Final acceptance
requires a future authorized real Android LMTR source with independent Float32/native/
transport/chronology parity, gaps, ROI, UI and source-immutability checks on both hosts.

Native-equivalent temperatures; absolute physical accuracy not yet independently validated.


## Phase 2 local qualification (2026-11-12)

See [qualification and review gates](LMTR_R5_QUALIFICATION.md) and the short
[Linux/Windows operator recipe](LMTR_R5_OPERATOR_CHECK.md). These supersede the
historical Linux count above for this corrective branch; Windows run43241547022
only validates its pinned older runtime, never the new changes.

`LmtrReadLimits` adds local refusal before CHUNK body allocation and before numerical
or DEFLATE decode. Default decoded cache is64MiB and working charge128MiB. The latter
charges serialized body + all decoded blocks + logical payload lengths (including
views) + largest stored block +8×serialized metadata. This is an explicit conservative
buffer estimate, **not a proven Python-object/RSS ceiling**. Graphics, owned frames,
metadata object graphs and runtime overhead remain separate. A valid wire file can
exceed local admission; there is no silent partial decode or weakened validation.
The original R3 decode still runs in full after these checks.

HEADER/entry values crossing the worker boundary are owned copies. Public logical
payload bytes are immutable, including opaque observations without primary geometry.
No invented grid or Celsius view is made for those observations. A native plane with
another grid remains exact evidence but cannot supply primary-grid hover raw indices.
Full verification evicts the old decoded cache first; close releases cache/navigation
ownership. Path and descriptor identity are checked before and after source operations.
They are change detectors, not a filesystem snapshot/security boundary.

A source-opening generation barrier keeps old scrubs from cancelling a new Open.
PNG freezes its immutable frame/palette/range before the destination dialog, which can
run a nested Qt event loop. Invalid locked range edits are explicit; Auto is independent
of them. No archive rewrite, converter, measurement transform or R6 export is added.

### Maintaining the vendored validator

`lmtr_reference/PROVENANCE.json` pins the accepted upstream Git revision, three source
hashes and exact schema hash. `test_vendored_algorithms_and_registry_match_pinned_source_hashes`
checks the normalized schema-path relocation only. Do not edit vendor algorithms or
repin hashes to hide differences. An update requires explicit upstream revision review,
algorithm/schema diff, all51 original hash/verdict checks, adapter/ownership/resource/UI
regressions and new Windows verification. Keep original corpus/manifest receipts;
new engineering sources are noncanonical and never replace those fixtures.


## Phase3 integration review

[Independent review and integration proposal](LMTR_R5_INTEGRATION_REVIEW.md) records
additional malformed-visual and obsolete-scan corrections,232 passing Linux tests,
and exact pending Windows/operator gates. Historical phase2 evidence remains pinned.
