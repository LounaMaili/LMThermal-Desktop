#!/bin/sh
# Execute a prepared, private old-Linux build root. Never invoke this script as sudo/root.
# Bootstrap/pinned inputs and the separate runtime gate are documented in LINUX_BASELINE.md.
set -eu
if [ "$(id -u)" = 0 ]; then echo 'Run as a normal host user' >&2; exit 2; fi
if [ "$#" -ne 3 ]; then echo 'Usage: build-linux-rootless.sh ROOTFS SOURCE_BUNDLE NEW_REPORT_DIRECTORY' >&2; exit 2; fi
rootfs=$(realpath -e -- "$1")
bundle=$(realpath -e -- "$2")
reports=$(realpath -m -- "$3")
test -d "$rootfs"
test -f "$bundle"
test ! -e "$reports"
mkdir -m 700 -- "$reports"
# The parent namespace maps already-authorized subordinate IDs for dpkg ownership.
# No default Podman store, system configuration, host home, device or display is mounted.
exec unshare --user --map-auto --map-root-user bwrap \
 --unshare-pid --unshare-ipc --unshare-uts --unshare-cgroup --unshare-net \
 --die-with-parent --new-session --clearenv \
 --setenv PATH /opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
 --setenv LANG C.UTF-8 --setenv QT_QPA_PLATFORM offscreen \
 --bind "$rootfs" / --dev /dev --proc /proc --tmpfs /tmp \
 --ro-bind "$bundle" /source.bundle --bind "$reports" /reports \
 -- /bin/sh -eu -c '
   source_sha=$(git bundle list-heads /source.bundle HEAD | cut -d " " -f 1)
   test -n "$source_sha"
   source_dir=/source-$source_sha
   test ! -e "$source_dir"
   git clone /source.bundle "$source_dir"
   cd "$source_dir"
   git status --porcelain > /reports/source-status.txt
   git rev-parse HEAD > /reports/source-sha.txt
   python -m unittest discover -s tests -v > /reports/tests.log 2>&1
   python tools/build_desktop_bundle.py --output /reports/bundle > /reports/build.log 2>&1
   python tools/audit_desktop_bundle.py /reports/bundle/dist/LMThermal-Desktop /reports/bundle/bundle-sha256.txt --report /reports/audit.json
   python tools/check_frozen_bundle.py /reports/bundle/dist/LMThermal-Desktop/LMThermal-Desktop --report /reports/canonical-parity.json > /reports/canonical.log 2>&1
   python tools/prepare_offline_qualification.py --output "/reports/inputs é" > /reports/inputs.log
   python tools/run_window_campaign.py --fixtures /source/tests/fixtures --legacy "/reports/inputs é" --output /reports/frozen-offscreen --binary /reports/bundle/dist/LMThermal-Desktop/LMThermal-Desktop > /reports/frozen-offscreen.log 2>&1
 '
