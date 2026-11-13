# Native Windows 11 handoff — offline usability candidate

**Prepared, not executed on Windows.** Fedora, Ubuntu userland, WSL, pinned wheel
downloads and old Windows results cannot qualify this new workspace/installer.
No device/ADB, Actions, administrator access, security-policy change or public release
is part of this recipe. Read AGENTS.md and preserve existing local work first.

## Exact source and environment

Working branch: `codex/desktop-offline-qualification`.
Runtime/build source: **`dfaf6ed9ac6433cdeabc4c52bf2f890c9e2f1e42`**.
Later evidence/handoff-only commits do not change the tested runtime. Fetch that exact
commit into a new native-Windows worktree; never reset a user's existing checkout:

```powershell
$ExpectedCommit = 'dfaf6ed9ac6433cdeabc4c52bf2f890c9e2f1e42'
git fetch origin codex/desktop-offline-qualification
git worktree add --detach ..\LMThermal-Desktop-offline-check $ExpectedCommit
Set-Location ..\LMThermal-Desktop-offline-check
git rev-parse HEAD
git status --porcelain
py -3.15 -c 'import sys; assert sys.version_info[:3] == (3,15,2) and sys.maxsize > 2**32'
py -3.15 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r packaging\requirements-windows-cp315.lock
```

Use native Windows 11 x64, normal Git for Windows, exact CPython 3.15.2 x64 (not
free-threaded/ARM64/32-bit) and the 13 hashed Windows dependencies. All thirteen
retained wheels were rehashed against this lock on Fedora; Windows import/PE loading
is still unverified here. Record OS/Python/pip versions and hashes of retained wheels.
Do not weaken PowerShell, SmartScreen, Defender or organizational policy. Calling
the venv Python directly avoids an activation-policy change. If a script cannot run
under existing policy, use its documented Python command or record the boundary.

## Automated native qualification

Choose a NEW private report directory outside Git. The script checks native platform,
exact clean source and exact interpreter before running tests/builds:

```powershell
$Reports = Join-Path $env:LOCALAPPDATA ('LMThermal-validation\offline-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
.\packaging\qualify-windows.ps1 -ExpectedCommit $ExpectedCommit -ReportDirectory $Reports -Build
```

Equivalent runner after the same version guard:

```powershell
.\.venv\Scripts\python.exe tools\qualify_native_desktop.py --native-windows --expected-commit $ExpectedCommit --output $Reports --build
```

Require every executed unit test to pass. Record names/counts of explicitly Linux-only
skips; do not guess acceptance from the Fedora total of 263. Require source diff check,
runtime self-check, complete bundle hashes, all **51 canonical files /102 strict and
recovery outcomes**, and all eleven frozen exposed-window cases. Preserve failures,
stdout/stderr/exit codes and partial outputs under the original run identifier.
No automatic retry or Actions is authorized. The automated window campaign substitutes
its PNG chooser and therefore does not validate the native Windows picker.

## Required real-window checks

Launch the frozen app normally, as a standard user:

```powershell
$Bundle = Join-Path $Reports 'bundle\dist\LMThermal-Desktop'
& (Join-Path $Bundle 'LMThermal-Desktop.exe')
```

Use the generated `inputs é` and unchanged repository fixtures, including paths with
spaces/accents. Check Home, normal Open dialogs and actual PNG Save/Cancel dialogs,
LMTX temperature/preview/mask, historical still/recording, LMTR available observations,
blank gaps/segment events, END completed/interrupted and explicit incomplete-prefix
recovery with unknown tail. A cancelled picker must leave the current source intact.

At 1,000 pixels wide (also record Windows DPI scaling): inspect point/native mapping,
drag/clear ROI without viewport jumps, all palettes, disabled manual bounds in Auto,
Locked 25–45 °C, and invariant same-region measurements. Confirm indexed counts are
not labelled full integrity, Diagnostics exposes original metadata/full verification,
and saved tabs omit live-only actions while Home still offers an explicit Linux-only
Live backend. No camera is needed or authorized. Inspect long metadata independently.

Play/pause/step/time-seek, switch sources rapidly, switch tabs and close during pending
read/verification. Require no stale source readings, forced thread termination or lost
ownership. Verify original hashes before/after and PNG pixels separately; record manual
acceptance distinctly from automated Qt input. The Linux-only GTK provider flag is not
a Windows test. Real physical files may be transferred privately only with owner approval;
do not put them, scene screenshots or per-frame values into Git or public CI.

## Private user installer and retraction

Only after executable checks pass, use an already authorized Inno Setup 6 compiler
and record its version. The recipe requests `PrivilegesRequired=lowest`, no driver,
service, association or firewall change:

```powershell
$InstallerOutput = Join-Path $Reports 'installer'
ISCC.exe "/DBundleRoot=$Bundle" "/DInstallerOutput=$InstallerOutput" packaging\windows-user-installer.iss
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed' }
$Setup = Join-Path $InstallerOutput 'LMThermal-Desktop-preview-setup.exe'
Get-FileHash -Algorithm SHA256 -LiteralPath $Setup
& $Setup
```

Choose a **new** private per-user install folder in the normal installer; close all
LMThermal windows and do not replace old candidates. Verify each application file
against `bundle\bundle-sha256.txt`. Record/hashes of Inno-generated uninstaller files
are separate: the strict bundle auditor intentionally rejects extra installed files,
so do not misuse it as a whole-install-directory oracle or relax its exact inventory.
Record the actual installer-owned extra inventory before adding a harmless user sentinel.
Keep captures/logs/external sentinels outside the application. Repeat normal launch,
picker, Unicode PNG, analysis and Close checks from both Start Menu and executable.

Use the normal generated uninstaller, then verify the user-added sentinel and external
recordings/logs remain with their hashes. Preserve installer/uninstaller logs and any
unsigned-app prompt; if policy blocks execution, stop that stage without bypassing it.
No install/uninstall, PE audit, signature, performance or Windows acceptance is claimed
until this native campaign actually runs.

## Acceptance receipt

Return the exact source commit, executable and installer hashes, Windows/Python/DPI,
dependency inventory, test/skips counts, all 51 outcomes, native picker/UI results,
source/PNG parity, installed-owned inventory, uninstall preservation and remaining
failures. Keep raw logs private and publish only sanitized aggregates. A build or
green test suite alone is not manual UI, installer or physical-temperature acceptance.

Native-equivalent temperatures; absolute physical accuracy not yet independently validated.
