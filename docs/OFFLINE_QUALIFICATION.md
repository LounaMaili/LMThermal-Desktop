# Offline Desktop qualification and usability follow-up

Baseline `582df7f407f1336d243390aab7fca880e095f653` already reads the unchanged real
45-observation/60-gap file. This follow-up is camera-free. No ADB, phone/camera,
Actions, historical PR, main or public release is touched. Real thermal files and
screenshots remain private; the frozen LMTR validator and 51 corpus hashes stay intact.

## Targeted presentation changes

- LMTR keeps Open, explicit incomplete-file recovery and PNG export together.
  Diagnostics is an explicit toggle for original metadata and full verification;
  the underlying integrity scope is preserved, not silently promoted to full.
- Interrupted/completed/recovered status, indexed observation/gap/known-loss counts
  and event/time are prominent. Gap events remain blank; no repeated frame or
  made-up temperature hides an interruption. Indexed totals are labelled as indexed.
- Manual Celsius bounds are visibly disabled while Auto is active, matching the
  existing still/legacy behavior. Palette/bounds never change stored numbers.
- A compact half-open native ROI and a reserved two-line readout prevent measurement
  updates from setting window width or changing the image viewport during a drag.
- Saved LMTX/legacy workspace tabs omit live-only camera, initialization, capture/log/
  recording commands. Home's explicit Live tab retains them; source loaders, worker
  ownership, finalization refusals and acquisition logic are unchanged.
- Saved-source captions show filename, native size and measurement availability;
  verbose module/provenance dictionaries remain in original Metadata. Accuracy and
  unknown-palette warnings stay visible. The window campaign now actually requests
  and asserts 1,000 pixels; its former 1,020-pixel screenshots do not establish that gate.

The 1,000-pixel exposed baseline screenshot showed a technical integrity string in
the primary status line and live controls even in a saved-document tab. These changes
are a focused usability pass, not a redesign or a coordinate/scientific transformation.

## What was actually tested at the source checkpoint

The existing Fedora qualification venv runs CPython 3.15.2, Qt 6.12.0, NumPy 2.5.4 and
OpenCV 5.0.0. The old repository venv points at the upgraded system interpreter and
cannot import PyQt6; it is left untouched. The first failed import/cleanup diagnostic
and first incorrect GTK-response driver attempt remain in private logs. They are not
product reader failures and are not counted as passes.

263 source tests pass, including stable ROI layout, visible Auto-bound semantics,
explicit diagnostics, saved/live action separation, source generation/Close/recovery
and all prior capture/legacy/playback/readers. Later build/frozen/native receipts must
identify their exact source SHA separately.

Actual exposed Fedora/XWayland Open and PNG Save pass with **no replacement of
QFileDialog**. The platform-default configuration used Qt's actual fallback selector;
it is not a GTK/portal qualification. A separate `QT_QPA_PLATFORMTHEME=gtk3` test
activated the actual GTK3 Open/Save buttons and verified resulting PNG pixels, an
accented destination, source hashes and cooperative Close. This is automated own-
process native-provider interaction, not manual operator acceptance or a portal test.

## Repeatable source/frozen provider probe

Use a NEW private output directory and an existing offline LMTX or LMTR file. A valid
observation must be available; recovery/legacy-directory scenarios use the separate
window campaign. The provider probe deliberately starts Home and calls its normal
Open action, rather than treating command-line opening as chooser validation.

```sh
QT_QPA_PLATFORM=xcb QT_QPA_PLATFORMTHEME=gtk3 python lmthermal_desktop.py \
  --open tests/fixtures/lmtr/files/illustration-01-stored.lmtr \
  --native-dialog-check-output /private/NEW-provider-report
```

The frozen executable accepts the same options with an external fixture path. The
tool never replaces the selector/return value. It inspects only its own in-process
GTK/Qt controls, activates native buttons or the real Qt filename field, records the
actual provider, saves a distinct PNG, compares pixels and hashes the immutable source.
No global input injection, clipboard, other application or desktop screenshot is used.
Unsupported portal/exposure paths fail honestly and remain a manual gate. Do not force
non-native mode or infer a GTK pass merely from `DontUseNativeDialog` being false.

The older eleven-case window campaign still substitutes its PNG chooser: it validates
widgets/worker/rendering, not native dialogs. Both evidence classes remain distinct.
One later frozen GTK run remained in the real Open dialog and needed termination of
that exact offline test process. Its empty output, log and owned-window title are
preserved as **nonconclusive**, not a reader pass/failure. The diagnostic previously
stopped its timer immediately after activation and checked timeout only when no chooser
was visible. It now requires matching selection/button sensitivity, activates once,
keeps pumping until return and enforces the deadline even on a visible GTK chooser.
Progress and final source-identity receipts survive failure. This corrects the test
driver; it does not alter normal Open/Save or infer that the old GTK wait's sole cause
was timing. The prior passing default Wayland selector remains separate evidence.

## Packaging and Windows boundaries

Build only a clean reviewed commit with the existing pinned Ubuntu 22.04 / CPython
3.13.18 dependency root, full source suite, frozen corpus/window gates and complete ABI/
plugin/member audits. Install into a NEW private folder; do not replace old candidates.
The required GLIBC ceiling stays 2.35 and the complete GTK/portal/Qt plugin set remains.
The provider tool is opt-in; normal application startup is unchanged.
The rootless runner now uses the exact `$source_dir/tests/fixtures`: inspection found
the former `/source` pathname referred to an older checkout retained in the dependency
root. Canonical bytes match, but that stale reference was not a sound source-binding
rule. The historical source/checks remain untouched and the new build uses a separate
private copy of the dependency root.

No authorized native Windows 11 environment is available to this Linux session.
Thirteen retained Windows wheels still match `requirements-windows-cp315.lock` exactly;
this is input integrity only. The existing `qualify-windows.ps1` uses CPython 3.15.2 x64,
exact clean-SHA guards, native tests/build/corpus/window stages and private outputs.
The [Windows guide](WINDOWS_DEVELOPMENT.md) remains the installation/manual/retraction
procedure. A final handoff must pin the new runtime SHA and include normal Windows
Open/Save, Unicode paths, 1,000-pixel layout, interrupted/recovery views, install/uninstall
and source/sentinel preservation; Fedora/WSL cannot validate these gates.

The [exact Windows handoff](WINDOWS_OFFLINE_HANDOFF.md) now pins runtime source
`cb5f310f2d367b459066de9af7d72451de4fac09`, PowerShell commands, native-window
acceptance and separate installer-generated inventory. It is ready for a future
authorized Windows session, not a Windows result.

Native-equivalent temperatures; absolute physical accuracy not yet independently validated.
