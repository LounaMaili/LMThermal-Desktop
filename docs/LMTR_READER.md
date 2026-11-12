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
latency bound and Close refuses rather than abandoning a running worker.

One decoded-CHUNK cache is at most64MiB; evict before loading. This is **retained decoded
bytes**, not total process memory. Reference input body up to65MiB, decompression/
logical-slice/JSON/statistics scratch, a selected owned frame, result/UI copies, image
and Qt graphics can coexist. The reference currently decodes once for validation and
again to materialize analysis blocks; no claim of zero-copy or memory-admission proof.
Navigation caches retain at most16 records/8MiB raw bodies, at most two pages/depth.
Reference reporting state has a65536-record/hash local budget. Desktop's current slider
admits at most1000000 events; recovered fixed ranges cap65536 CHUNKs. These are explicit
local refusals, not v1 wire changes. Large-file/indefinite-runtime performance is unproven.

Intake seek compares exact integer nanoseconds. Display/playback seconds are derived
presentation values, never sensor-time substitutes. All events (including gaps and
segment boundaries) remain navigable. Tied intake times may share a display position.
Point/ROI use stored native geometry and validity; geometry changes clear incompatible
inspection rectangles. Source metadata stays separate from inspection edits. Palette,
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
