# Phase3 R5 integration review and controlled proposal

Status: ready for technical review, NOT merged or newly Windows-qualified.
Reviewed delta:8f167244de86fcb3efc79c9abd9c9e92567c8a33→9e57f227933ccffc23b400b710b6f76b4d9264cc.
This separate review pass rechecked generation publication, worker/model ownership,
cooperative closure, source identity, resource refusal and UI/presentation boundaries.
[Phase2 evidence](LMTR_R5_QUALIFICATION.md) stays historical, not rewritten.

## Findings and demonstrated corrections

1. **Malformed standardized visuals could escape the worker boundary.** The pinned
   reference checks generic byte/shape/hash integrity but does not enforce every
   known visual channel shape. A3×2 gray-shaped payload labelled RGB could be placed
   in `preview_bytes` and fail during reshape in the Qt rendering slot; malformed
   RGBA also failed during adapter conversion. New local guards verify u8/byte-order/
   exact channel shape/length before interpretation, raising explicit `invalid_payload`
   on the worker. Unknown opaque encodings remain opaque. This does not modify the
   accepted R3 algorithms or pretend their coverage was complete. RGB/RGBA/gray
   positive and negative cases, blank-on-error and valid-source reopening pass.
2. **New Open did not cancel a cooperative scan of an obsolete source.** Generation
   checks prevented stale publication, but the old scan only watched Close/Stop and
   could delay the new Open through its full remaining scan. The model's cancellation
   predicate now includes its immutable opening generation, under the same condition
   lock. A deterministic old-scan test failed before the fix and passes afterward.
   No hard latency claim is made for one synchronous read/hash/decompression/close.

Additional concurrent-source test replaces the path mid-decode with another inode
of identical size, bytes and restored mtime on Linux. Both path/descriptor guards
refuse publication, leave the decoded cache empty and close the original descriptor.
Windows's open-file replacement semantics need their own qualification; this narrow
Linux test is explicitly skipped there, not made artificially portable.

## Review verdict and retained limits

- Open-generation barrier, one bounded pending/result slot, immutable PNG capture,
  asynchronous Close and metadata copy boundaries remain valid. No new payload owner,
  cache expansion, production dependency, measurement transform or source writer.
- No canonical file, vendor algorithm or schema changed. All51 original byte hashes/
  verdicts and existing LMTX/legacy recording/playback tests remain in the full suite.
- Local128MiB working charge is a buffer estimate, not a certified total-RSS bound.
  Metadata object graphs, decoded validation scratch, displayed owner and graphics
  can coexist. A wire-valid source may honestly exceed local admission.
- Stat/fstat detects observed changes, not an atomic filesystem snapshot or protection
  from a malicious process restoring all source evidence between observations.
- Cooperative cancellation now covers replacement as well as Close, but an individual
  blocked filesystem operation remains unbounded. No abandoned/terminated QThread.
- Cleanup failures stop new owner creation and expose bounded diagnostics; tests on
  the normal filesystem do not certify every exceptional OS close behavior.

Linux:232 tests, no failures/errors/skips. New malformed-plan failures and cancellation
failure were retained before correction; no assertion was weakened. All inherited
resource and native-grid tests run again. The current native-xcb attempt fails before
QApplication/window creation (display:0 unavailable/platform loader); no Fedora/display
configuration was changed. Offscreen widget tests pass, not operator/native acceptance.
No physical camera, phone, ADB, emulator or hosted Actions run.

## Integration proposal — DO NOT execute without the next authorization

PR Desktop#4 remains draft at8f167244. The correction chain is linear:

```text
Desktop main b0afdf3 -> PR4 8f167244 -> phase2 9e57f227 -> phase3 review tip
```

Dependencies: existing PyQt6/OpenCV/NumPy/Pillow only; unchanged pinned Python R3 copy,
registry and corpus. Native measurement coordinates, original bytes and legacy readers
remain authoritative. No dependency on merging Android PR16/17 or on the R4 prototype.

After owner review, a **normal fast-forward** of PR4's branch to this reviewed tip
can preserve both corrective commits without cherry-pick duplication or history rewrite.
This is a proposal only. The existing Windows workflow matches that original PR branch;
its next push WILL require a newly authorized CI budget. Publishing this isolated
review branch must not be confused with activating that workflow.

Preconditions for that future action:

1. Recheck remote main and PR4 head.8f167244 must still be the ancestor. If changed,
   recompute the delta and resolve new conflicts explicitly; do not force-push.
2. Preserve all changelog/evidence entries. Concurrent additions to CHANGELOG, README,
   reader/viewer/tests are the likely conflict sites if other work intervened.
3. Run the full Linux suite/diff check on the exact proposed integration tree.
4. Authorize and execute native Windows verification of that SAME commit, preserving
   source/APK-independent receipts, complete tests and named expected Linux-only skips.
5. Perform the [operator recipe](LMTR_R5_OPERATOR_CHECK.md), then review integration.

## Exact next Windows / Linux operator procedure

Record Git SHA, OS/build, Python/Qt/OpenCV versions and DPI/scaling. On Windows use
Python3.13 (or explicitly report another version), a clean venv and requirements.txt.
Run `.venv\Scripts\python.exe -m unittest discover -s tests -v` with offscreen Qt for
unit tests, then launch the normal platform window in a separate shell/session.
Do not assume an inherited historical test count: report actual discovery and every
skip. Only named Linux-only support tests and the new atomic-replacement test may
skip without investigation. All51 canonical hashes/verdicts must still pass.

Follow the short recipe on Linux and Windows. Add this hostile-boundary check:
unit tests must refuse inconsistent known visual channels before Qt display; a failed
source must clear image/legend/readings and a subsequent valid Open must work. A new
Open during a long cooperative verification should cancel the obsolete scan at its
next record boundary, without an old source publishing under the new filename.
Normal Close must finish and release temporary-file ownership. Save PNG should retain
its pre-dialog frame/palette/range, including unfinished invalid manual edits.

Earlier Windows run43241547022 validates4c73e9f only. It validates neither9e57f227
nor this phase3 code. Real-window operator acceptance and broad Windows resource/
filesystem robustness remain explicit gates, separate from pure reader conformance.

Native-equivalent temperatures; absolute physical accuracy not yet independently validated.
