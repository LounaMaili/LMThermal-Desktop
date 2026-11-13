# Rootless Linux baseline — private qualification candidate

Phase 6 uses an official Ubuntu 22.04.5 amd64 userland (GLIBC 2.35), not copied Fedora
libraries or an arbitrarily replaced libc. The kernel remains Fedora's: a rootless
userland test is not a complete Ubuntu desktop/driver certification. All roots, caches,
archives and reports are private/project-local. Nothing installs into Fedora or modifies
its configuration, security, existing container store or other development projects.

## Pinned inputs and isolation

- Official `docker.io/library/ubuntu:22.04` resolved on 2026-11-13 to
  `sha256:74b12b11ffa550fba1929ad4d8d16533c34f6612728469671afba1b03b66881c`.
- Selected amd64 root layer: `sha256:02163de04dd1fb14a97b06804be3fbc51b564fa8744b8f97153744e0cba5de4c`.
  Retain the original OCI manifest/config/layer and check each digest before extraction.
- CPython 3.13.18 / Astral build 20261110, original archive SHA-256
  `f2d2199eb25880e07863b391cf7124e7ffa1338b0d65600dfcec117c4223a7fb`.
- `packaging/requirements-linux-cp313.lock` records exact downloaded wheel hashes.
  The matching private wheelhouse and Debian package archives/checksums must be retained.
  `packaging/linux-baseline-packages.tsv` records actual versions; live apt repositories
  can change, so version names alone are not a promise of future byte-reproducibility.

Skopeo downloads public OCI bytes without a daemon. Extract only into a new private root
inside a restricted bubblewrap mount namespace. Host `/usr`/loader are read-only for that
extraction; no home or hardware is exposed. Check resource availability first.
Build preparation uses rootless `unshare --user --map-auto --map-root-user` with the
host's EXISTING authorized subuid/subgid ranges. Do not create/change those ranges.
Bubblewrap isolates mounts, process/IPC/UTS/cgroup namespaces; only bootstrap downloads
share networking. No host ADB, display socket, credentials or device node is mounted.

The first single-UID bootstrap failed at Debian service-account ownership. Its raw failure
is retained. Use the existing subordinate mappings rather than weakening permissions,
running host root, bypassing dpkg or silently calling a partially configured root valid.
No host service is started. Require `dpkg --audit` to be empty after preparation.

Bootstrap only the selected root using the recorded package set, install the exact Python
archive into `/opt/python`, create `/opt/venv`, and install the wheelhouse with
`pip install --no-index --require-hashes -r /input/requirements-linux-cp313.lock`.
Retain dpkg versions, .deb hashes, wheel hashes, interpreter hash and namespace mapping.
No source recording or phone permission is needed. Do not substitute host native libraries.

## Exact-source build

Create a NEW Git bundle from the reviewed clean branch; preserve all prerequisite history.
The runner clones it into a new `/source`, with no source-directory bind and networking
disabled. Set a private report path outside the checkout:

```sh
git bundle create /private/source.bundle HEAD
sh packaging/build-linux-rootless.sh /private/prepared-root /private/source.bundle /private/new-build
```

The runner requires already available `unshare`/bubblewrap and a prepared root; it does
not install system tools. It runs the complete suite, build/self-check, every delivered
file/link/ELF audit, strict+recovery parity on all 51 unchanged canonical files and eleven
frozen offline cases. Failure stops the stage and preserves partial files/logs. It does
not retry, enlarge limits, change formats or repair sources. Each root/report path is
single-use; never reset a historical root to rerun conveniently.

The audit distinguishes required symbol versions from versions a library merely provides,
includes GLIBCXX/CXXABI, needed libraries/search paths and refuses an absent readelf or
unlisted file. Native COLLECT provenance retains source/delivered hashes separately
(PyInstaller may rewrite RPATH), wheel/Debian ownership, actual Debian copyright notices
and explicit unmapped members. This is not yet complete legal/source/relink acceptance.

## Independent runtime and native desktop gates

Use another new Ubuntu root from the pinned OCI bytes, install only the retained runtime
Debian dependencies, and expose the candidate read-only with a private writable report
directory. Do NOT expose the build venv, Python, source checkout or Fedora library paths.
Run the frozen self-check and CLI corpus oracle, then Xvfb/xcb offline windows if available.
Xvfb tests QPA/plugin linkage; it is not an actual desktop, native picker, accessibility
or visible latency test. Keep its result separate from native Fedora windows and operator
checks. A broad support claim additionally needs real clean distro/graphics environments.

On unlocked Fedora, run the same frozen campaign in normal xcb windows, inspect only its
own widgets and separately check normal opening/PNG dialogs. Never unlock a locked session
or change display/security settings. See the phase-6 qualification report for actual runs.

Install the private tar by extraction into a NEW user-owned directory, verify every
`bundle-sha256.txt` member and run the executable directly. Keep `_internal` alongside it.
Preserve external captures/logs. Removal deletes only the known installed application
files; retain anything user-added/changed. No desktop association, service or root access
is required. Public license/signing/ABI/installer gates remain open.

**Native-equivalent temperatures; absolute physical accuracy not yet independently validated.**
