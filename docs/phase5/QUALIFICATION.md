# Phase 5 Desktop qualification

## Scope and current evidence boundary

This branch starts at packaging `8976eb7425763d12419d347c2f3d117e39b802f7`, containing
workspace `35ee105c342903c0505eef648ede7f7745bc85ed` and reviewed R5 ancestry. No main,
historical branch, PR, accepted format or camera protocol is changed. Common LMTR
production recording remains unavailable. The HT-301 was absent; only a different
webcam was enumerated and never opened. Pixel/ADB access is explicitly deferred.

The phase 4 preservation manifest and all 1,615 recorded files matched their SHA-256
before work. New private reports use a separate dated directory. Earlier failed harness
runs, metadata and images are retained, never relabeled as successful acceptance.

## Demonstrated corrections

- A legacy recorder/logger finalization refusal could leave a disabled document and
  the workspace stuck in application-Close state. The adapter now signals refusal,
  keeps ownership, re-enables retry/inspection and aborts application Close. It does
  not force finalization, discard data, acknowledge pending measurements or alter END.
  The deterministic regression refuses Close and then proves an explicit retry works.
- LMTR controls required 1,179 pixels on the tested native theme when resized to a
  1,020-pixel request. Split the same controls across rows; native grid/mapping,
  reader workers, bounds and validation remain unchanged. A laptop-width regression
  checks all navigation/palette controls remain inside a 1,000-pixel window.

## Reproducible offline window checks

The explicit developer option below uses normal viewer widgets/reader workers, and
never opens hardware. A report directory must be new and outside a legacy source
recording; invalid requests fail before Qt initialization. Normal launches don't enter
this diagnostic path. Timers use a normal QApplication event loop; sources retain their
exact hashes and every Close waits for owned workers.

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python lmthermal_desktop.py \
  --open tests/fixtures/lmtr/files/illustration-01-stored.lmtr \
  --window-check-output /absolute/private/new-report
# Native exposure is mandatory in xcb/wayland mode; never unlock/bypass a session:
QT_QPA_PLATFORM=xcb .venv/bin/python lmthermal_desktop.py \
  --open tests/fixtures/lmtr/files/missing-end.lmtr --recover-lmtr \
  --window-check-output /absolute/private/new-native-report
```

`tools/run_window_campaign.py` runs eleven source or frozen cases: complete/masked/full-size
LMTR, explicit recovery, expected required-feature refusal, LMTX normal/preview/masked/
alternate geometry/corruption, and fixture-derived legacy still/recording. Legacy inputs
must be prepared in a private directory using the existing test support, labeled as
fixture-derived/noncanonical. No physical acquisition or canonical R3 regeneration.

The checks cover native point mapping, ROI, palette/Locked/resize invariance, separate
PNG, original metadata dialog, blank timeline events/gaps/drops, recovery/full integrity,
source hashes and normal Close. Qt events are programmatic, not operator acceptance.
Reported elapsed times include deliberate test waits; they are not visible motion latency,
acquisition FPS or isolated rendering benchmarks. The helper's `ready_wait_after_test_settle_ms`
is explicitly only remaining wait after startup, not total file-open time.

## Native display observations — 2026-11-13

An initial normal Fedora Home window was exposed, and its own widget screenshot was
inspected. Qt/X11 unit tests also run, but this alone is not visual/operator validation.
Later native case checks could not confirm exposure; they correctly fail that gate.
Read-only inspection of this application's X11 properties showed `_NET_WM_STATE_HIDDEN`;
read-only `loginctl` for the current session reported `LockedHint=yes`. No unlock,
lock-policy change, display reconfiguration or capture of other applications occurred.
Native visual qualification therefore remains pending in an authorized unlocked session.
A failed diagnostic probe that assumed xwininfo was installed is retained as a tool failure;
subsequent bounded probes used the available xprop and closed normally.

Keep failed exposure-gated runs separate from successful offscreen functionality. A
window widget grab can render while hidden, so its PNG is not evidence of actual exposure.
The normal operator recipe must still confirm aiming/hover/ROI/resize/readability, picker
behavior and visible responsiveness. No actual Linux camera or Windows validation occurred.

## Packaging consolidation

The builder now requires/copies the actual embedded Python license, refuses output inside
the checkout, fingerprints source inputs before/after and refuses detected source changes.
This is a boundary consistency check, not an atomic snapshot. Failed builds are retained.
`tools/audit_desktop_bundle.py` independently checks file hashes/contained links and reads
ELF version requirements without executing shared libraries. Its glibc result constrains
what can be claimed for this host-built runtime; it is not broad distro certification.
Installed wheel metadata is a build-environment inventory, not a complete runtime SBOM.
See [packaging](../PACKAGING.md), [Windows setup](../WINDOWS_DEVELOPMENT.md) and the
[canonical license worklist](https://github.com/LounaMaili/LMThermal/blob/codex/phase5-android-qualification/docs/DISTRIBUTION_LICENSE_REVIEW.md).

Before integration/public release: approve the ancestry/order, finish an unlocked native
Linux/operator check, qualify a clean Linux ABI baseline, run native Windows tests and
install/uninstall, decide licensing/branding/signing/privacy and obtain explicit Pixel
availability for Android. No CI budget or public release is consumed by this phase.

**Native-equivalent temperatures; absolute physical accuracy not yet independently validated.**

## Final software checkpoint

The reviewed source passes **249 hardware-independent tests** under both CPython 3.15.2
and a project-local Astral CPython 3.13.18 (build 20261110). Both frozen candidates pass
all eleven offline window cases and 102 strict/recovery comparisons over the unchanged
51 canonical files. The complete canonical comparison reports are identical across
Python versions. These are software/offscreen results; native visual/operator cases
remain deferred because the session is locked. There is no physical acquisition FPS,
Pixel heap/GC or visible-motion-latency result from this phase.

Both artifacts were built from clean commit
`c9a0854743df488b38042e4de77ac0920aa962c5`, with stable before/after source inventory.
Later documentation commits do not change that tested artifact provenance.

| Candidate | File hashes verified | Private tar bytes | Original tar SHA-256 |
| --- | ---: | ---: | --- |
| Fedora CPython 3.15.2 | 510 | 153225660 | `6f6954fd4319134e40a5d6a544788ace7f40dacc140d3ec5ef96968df2f4d03c` |
| Astral CPython 3.13.18 | 452 | 153593451 | `f291887faa39059fd2a1d4d7ef49bdedb007a6b6121d5846c69cf91bf6771676` |

Private installation/retraction rehearsals extract each tar under spaced/Unicode paths,
verify every declared file, self-check with an empty executable PATH and no external
Python settings, remove only owned files and retain an added user-file sentinel plus
external local logs. The original archive hashes are measured before/after and match.
This is a controlled prototype rehearsal, not a general production uninstaller or a
native Windows installation result. Artifacts, raw logs, screenshots and manifests stay
private and have not been published as releases.

### ABI finding and next packaging decision

**Both candidates still contain a GLIBC 2.44 requirement.** The portable interpreter
removes the system-math requirement but not the copied host SQLite dependency. The
observed chain is Qt's GTK platform-theme plugin → GTK3 → tinysparql → libsqlite3.
Changing Python alone is therefore not a portability solution. No plugin is blindly
removed and no favorable partial ABI scan is substituted for the complete inventory.

Recommend a separately reviewed older build baseline matching all dependency floors,
plus clean-distro tests, or an intentional platform-theme/plugin policy with native
picker/input/accessibility qualification. Do not advertise older-distro compatibility
for these artifacts. AppImage wrapping cannot remove the requirement; Flatpak needs
its own pinned runtime/portal/device design. No system package or security policy was
changed, and neither candidate is selected as a public release.
