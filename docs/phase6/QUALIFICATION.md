# Phase 6 Desktop qualification — 2026-11-13

## Installable private candidate

The final Linux artifact is built from clean source
`9b3374e09c825649c5222d78f707334f8bfa1840` using the
[rootless old-Linux baseline](../LINUX_BASELINE.md). Later handoff/test/documentation
commits do not change its runtime. It is an x86_64 private candidate, not a public release.

- Original tar: **146,023,879 bytes**, SHA-256
  `79b483a79486e1bc3e6e12715a56f9287331b51c194370057c1053c71c733a08`.
- **535 delivered file/link hashes**, **203 ELF files**, maximum required **GLIBC2.35**.
  Required GLIBCXX/CXXABI, needed libraries and search paths are also recorded.
- No libc, dynamic loader, libm or pthread replacement is bundled. The target supplies
  its own compatible libc. Static requirements alone do not certify an entire distro.
- The same **44 Qt plugins** as the phase5 portable-Python candidate remain, including
  GTK3 and portal themes: no plugin was removed or replaced to improve the audit.
- Actual native COLLECT ledger: **202 members, zero unknown origins** — 116 wheel,
  85 Debian and one CPython. The executable/bootloader is the additional audited ELF.
  Source and rewritten-delivery hashes are distinct; exact Debian copyright notices
  are copied. This is not complete license/source/relink acceptance.

The old Fedora chain Qt GTK theme → GTK → tinysparql/SQLite was host-specific.
Ubuntu22's retained GTK3 has a different older dependency closure without that
tinysparql/SQLite edge. Building the whole closure against the old base, rather than
dropping GTK or swapping libc, lowers the actual complete-bundle floor from2.44 to2.35.
The independently tested plugin/linkage behavior below constrains what can be claimed;
native file-provider/accessibility/operator gates remain open.

## Executed software and runtime gates

| Gate | Actual result | Scope |
| --- | --- | --- |
| Fedora host source | 258 tests, no failures/skips, CPython3.15.2 | Hardware-independent, no new performance/hardware claim |
| Ubuntu build root | 258 tests, no failures/skips, exact CPython3.13.18 | Existing rootless subuid/gid ranges, no system installation |
| Final frozen canonical corpus | All51 files unchanged, 102 strict/recovery outcomes match source | Existing R3 reader/bytes, no regenerated fixtures |
| Build-root frozen UI | 11/11 offscreen cases | Real widgets/workers, not a native desktop |
| Separate clean Ubuntu runtime | 102/102 canonical results; 11/11 GTK/xcb Xvfb cases | No build Python/venv/source/Fedora-library mount or network; shared Fedora kernel |
| Fedora XWayland windows | 11/11 exposed cases, normal Close | Automated own-process Qt events; not operator acceptance |
| Fedora native Wayland windows | 11/11 exposed cases, normal Close | Complete Qt plugin set retained; same operator limitation |
| Installed launcher | Normal default platform, fixture-derived legacy capture, source invariant and Close pass | No external Python; ready-to-use private installation retained |
| Installation/retraction | All535 installed hashes verified; idle-copy owned-file removal preserves added/external sentinels | Controlled private rehearsal, not a general production uninstaller |
| Windows | 13 x64/CPython3.15 wheel inputs pinned and verified against PyPI; guarded scripts and installer recipe | **NOT EXECUTED on Windows**, no Actions |

All24 retained Linux/Windows wheels match their official PyPI digests. Debian archives,
versions/hashes, OCI layer/config/manifest, interpreter original, source Git bundles,
logs, oracles, screenshots of this application's widgets and original tar files remain
private. Same-filesystem preservation is not an independent backup or atomic snapshot.

Source/frozen cases cover masked/full-size LMTR, strict refusal and explicit recovery,
LMTX temperature/preview/mask/alternate geometry/refusal, fixture-derived legacy still
and recording, timeline/gaps/drops, point/ROI/colors/Locked/resize, metadata, separate
PNG, source hashes and Close. Existing slow-reader/source-generation/finalization-refusal
regressions remain. No scientific reader, native-coordinate, thermometry, USB, queue,
memory-admission or acknowledgement behavior changed. R4/R6 recording is not enabled.

## Retained failures and interpretation

- The first single-ID root bootstrap failed on Debian service-account ownership. The
  existing subordinate-ID mapping solved the rootless setup; no Fedora policy changed.
- A new provenance regression used an unhashable SimpleNamespace instead of a Path.
  Its 257-test failure is retained. The corrected test and resolved-source lookup pass;
  no admission/safety threshold or application assertion was weakened.
- The first corrected-notice frozen native campaign passed10/11; one pointer check
  observed `(1,0)` after requesting `(0,0)` and pumping native events. The initiating
  event wasn't logged. Unrelated subsequent cursor input is a supported explanation,
  not a certified reconstruction of that event. The diagnostic now asserts the exact
  synchronous dispatch before pumping, records both pointers and has a deterministic
  queued-input regression. No image-to-native mapping changed. Both final backends
  pass11/11; the first failure stays separate.
- The independent runtime's first preflight lacked mount-point directories under its
  read-only root. It failed before app execution. After preparing those directories,
  new run IDs preserve the original failure and verify both candidates' runtime bytes.
- Normal Open/Save dialog probes were **not accepted**: one harness used a protected
  PyQt method; the next did not yield a validated load. No production workaround/plugin
  removal was introduced. Normal provider selection and manual export must be checked
  by the operator. The campaign's separate-PNG test substitutes the chooser, not saving.

These are software/finite-window results. No acquisition FPS, visible movement latency,
long-run memory/GC, real camera interoperability or physical calibration is measured.
No Pixel, ADB or HT-301 is accessed. No other application's window is captured/unlocked.

## Operator, Windows and release handoff

A private installed launcher and exact-path operator sheet accompany the local evidence.
Launch Home, open the named saved fixtures normally, hover/draw/clear ROI, change
palette/Auto/Locked, resize, navigate gaps/recovery, change files without stale readings,
save PNG through the normal picker and close pending work. Check readability, ordinary
keyboard/focus/dialog behavior and visible responsiveness. Keep native coordinates and
original file hashes. This final manual gate cannot be inferred from automated Qt events.

[Windows/Codex instructions](../WINDOWS_DEVELOPMENT.md) now use the reviewed full SHA,
hashed Windows profile, exact-source guards, private reports, all readers/fixtures,
frozen windows, Inno user installer and manual install/uninstall checks. Native Windows11
and unsigned/SmartScreen behavior remain pending; never disable security or use WSL as proof.

Recommended support statement today: private candidate verified on Fedora45 Wayland/
XWayland and Ubuntu22.04.5 userland/GLIBC2.35 on the same kernel with a virtual X server.
Ubuntu20.04/GLIBC2.31 is below the floor. Debian/other distros/graphics stacks, real Ubuntu
desktop, Windows11 and Android's new product UI are not newly certified by these results.
AppImage wrapping would not expand the support matrix; Flatpak remains a separate design.

Before public delivery decide application licenses/copyright, exact native obligations,
branding/icons/version, supported platform matrix, privacy identity/policy, signing and
independent encrypted backup/recovery. See the
[owner decision ledger](https://github.com/LounaMaili/LMThermal/blob/codex/phase6-android-integration/docs/phase6/DISTRIBUTION_DECISIONS.md).
Main and historical PRs remain unchanged; no release, production recording or CI is authorized.

**Native-equivalent temperatures; absolute physical accuracy not yet independently validated.**
