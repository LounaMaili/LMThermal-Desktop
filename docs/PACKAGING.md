# Desktop distribution preparation

Status: private development prototype; no public release, signing or store upload.
The workspace and R5 review are prerequisites, not assumed merged into main.

## Native one-directory prototype

Use a clean, native OS environment with Python 3.15 and pinned
`packaging/requirements-build.txt`. The headless OpenCV wheel has the same numerical
5.0.0 implementation and no competing Qt5 window plugins; the product uses PyQt6.
It deliberately does not support the old OpenCV `imshow` diagnostic in this environment.
The standard source-development requirements retain that diagnostic dependency.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r packaging/requirements-build.txt
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/build_desktop_bundle.py --output /absolute/private/new-build
/absolute/private/new-build/dist/LMThermal-Desktop/LMThermal-Desktop --self-check
/absolute/private/new-build/dist/LMThermal-Desktop/LMThermal-Desktop --verify-lmtr /path/to/synthetic.lmtr
```

The builder refuses an existing output, includes the LMTR schema, uses reviewed
[PyInstaller data-file support](https://pyinstaller.org/en/stable/spec-files.html#adding-data-files),
and creates a one-directory runtime, installed distribution notice inventory, file
SHA-256 manifest, source/dirty receipt, dependency self-check and Linux tar.gz.
No scenes, test corpus, APK research libraries or credentials are packaged. Do not run
it in an environment containing unrelated packages. An inventory is not a complete
license audit; include Python, Qt/plugin/native codec notices and relink/source obligations
before distribution. Exact byte-reproducible builds are not claimed: wheel locks lack
artifact hashes and host native libraries/compiler/glibc still affect output.

## Linux choice and limits

A tarred portable directory is the first testable artifact: extract privately and run
`LMThermal-Desktop`; no runtime Python setup or root installation. System display and
native OS libraries are still needed. Fedora-built binaries are qualified only on the
build host. Test an older glibc baseline and another clean Linux system before general
claims. See [AppImage baseline guidance](https://docs.appimage.org/introduction/concepts.html).
AppImage can wrap a qualified AppDir later; it does not fix ABI incompatibility.
Flatpak offers a pinned runtime/updates, but its
[device/filesystem sandbox](https://docs.flatpak.org/en/latest/sandbox-permissions.html)
requires a deliberate live-V4L2/USB and portal design. Do not grant all devices/host files
just to make it run. This phase changes no host display settings, drivers or permissions.

Local logs use the per-user state paths in [the workspace guide](PRODUCT_WORKSPACE.md).
Deleting the extracted application uninstalls this prototype. It never deletes captures;
logs may be removed separately after review. No file association or auto-update is added.

## Windows executable and installer preparation

Follow [the native Windows/Codex guide](WINDOWS_DEVELOPMENT.md), install the pinned
requirements into `.venv`, run the complete native/offscreen suite, then:

```powershell
.\packaging\build-windows.ps1 -OutputDirectory "$env:LOCALAPPDATA\LMThermal-builds\new-preview"
# Separately installed Inno Setup 6; optional, not run by the build script:
ISCC.exe /DBundleRoot="C:\private\new-preview\dist\LMThermal-Desktop" /DInstallerOutput="C:\private\installer" packaging\windows-user-installer.iss
```

The installer template targets the current user's LocalAppData Programs and Start Menu,
uses `PrivilegesRequired=lowest`, adds no driver, file association, service or firewall
rule and preserves external user data on uninstall. Neither PowerShell execution nor
Inno compilation, install/uninstall, Authenticode or SmartScreen behavior is qualified
by Linux. Native Windows must verify Unicode/spaced paths, Qt plugins, schema, imports,
full source integrity, PNG export and cooperative Close. Direct camera support is Linux
only. macOS would need its own native build and signing/notarization decisions.

## Linux prototype result (2026-11-12)

The isolated Python 3.15 / Qt 6.12 / NumPy 2.5.4 / OpenCV-headless 5.0.0 environment
passes all 242 hardware-independent tests, with no skips. PyInstaller 6.22.3 builds
and self-checks a roughly 374 MiB unpacked one-directory runtime on Fedora. The
frozen reader and source reader agree on all 51 unchanged canonical files in strict
and explicit recovery mode (102 comparisons), including refusals; source hashes remain
exact. `tools/check_frozen_bundle.py BINARY --report NEW_PRIVATE_JSON` reproduces this.
The first comparison harness misclassified PyInstaller's trailing exception footer;
its failed log was retained, the harness corrected, and the full comparison rerun.
No validator/reader semantics were changed to pass it.

The original/source GUI screenshots were inspected offscreen. Frozen LMTX/LMTR windows
are launch-smoked offscreen only; that does not validate interaction or normal GUI Close.
The native display was unavailable and no system graphics settings were changed.
Build receipts/manifests and private tarballs are retained outside Git. No artifact is
uploaded or published. No Windows build, installer, AppImage, Flatpak or macOS runtime
has been executed for this workspace. Older interoperability evidence remains historical.

## Publication gates and owner decisions

Neither repository currently specifies a top-level application license. Select a legal
product distribution license, branding/versioning/support policy and release ownership.
[PyQt is GPLv3 or commercially licensed](https://en.riverbankcomputing.com/software/pyqt);
Qt and native codecs have their own conditions. This prototype is private; no licensing
choice is made for the owner. Complete notices/source availability, dependency hashes,
SBOM review, native OS/installer validation and signing before public distribution.

**Native-equivalent temperatures; absolute physical accuracy not yet independently validated.**

## Phase 5 controls

`tools/build_desktop_bundle.py` now refuses output inside the source checkout, compares
source commit/status/file hashes at build boundaries and requires the exact Python runtime
license from its installation. A detected change refuses the build and preserves its
partial output. Start/end equality is not a claim of an atomic filesystem snapshot.

Independently audit every retained bundle with:

```sh
.venv/bin/python tools/audit_desktop_bundle.py /private/build/dist/LMThermal-Desktop \
  /private/build/bundle-sha256.txt --report /private/new-audit.json
```

This verifies all declared bytes and rejects escaping paths/links. On Linux, readelf
reports observed glibc symbol requirements; don't advertise compatibility with older
systems than that permits. Dynamic display/plugin behavior still needs native tests.
The opt-in window checks and their distinction between offscreen and exposed native
windows are documented in [phase 5 qualification](phase5/QUALIFICATION.md).

AppImage remains a candidate after an older build baseline/clean-host campaign; wrapping
a Fedora binary cannot lower its glibc requirements. Flatpak needs a pinned compatible
runtime, explicit file/directory portals and separately reviewed live-device access.
LMTX/LMTR are single-file sources; legacy JSON/NPZ sidecars need explicit scoped parent
access, and legacy recordings need a selected directory. Do not grant host/home/all-device
access as a shortcut. No Flatpak/AppImage installation or sandbox-policy weakening is
performed here. Existing one-directory delivery remains a private host prototype.
