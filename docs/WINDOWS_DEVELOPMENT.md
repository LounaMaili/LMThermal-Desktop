# Native Windows development and Codex

This is a preparation recipe, not evidence that the new workspace/installer ran on Windows.
Use a native Windows 11 x64 environment for qualification. Keep its clone independent of
Linux worktrees. Linux/WSL results do not establish native NTFS/Qt behavior.

## Tools and source

Install current Git for Windows and Python 3.15 x64 from their official installers as a
normal user where supported. Python versions must match available dependency wheels;
the Linux prototype pins Python 3.15-era packages. Record exact versions before a native
campaign. Optional GitHub CLI is useful for read-only review; use normal user sign-in,
never copy keys/tokens into the repository. No Android SDK, ADB, device or administrator
access is needed for Desktop offline development.

Install the official Windows desktop app containing Codex and sign in through its normal
UI. The [official Windows guide](https://learn.chatgpt.com/docs/app/windows) supports native
PowerShell; keep the project on a Windows filesystem. Open the clone as a project, read
AGENTS/REPO_RULES and select the approved working branch. Retain the
[Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox) and narrow approvals;
do not run the app elevated or disable isolation to bypass a build problem. WSL2 is
optional for Linux tasks, not a replacement for Windows qualification. .NET is unnecessary
for this Python/Qt application. No OpenAI API integration/key is required by LMThermal.

```powershell
git clone https://github.com/LounaMaili/LMThermal-Desktop.git
Set-Location LMThermal-Desktop
git fetch origin
# Copy the complete reviewed phase6 source SHA from the handoff, not a moving branch tip.
$ExpectedCommit = '<40-character-reviewed-SHA>'
git switch --detach $ExpectedCommit
py -3.15 -c 'import sys; assert sys.version_info[:3] == (3,15,2)'
py -3.15 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r packaging\requirements-windows-cp315.lock
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
git diff --check
Remove-Item Env:QT_QPA_PLATFORM
.\.venv\Scripts\python.exe lmthermal_desktop.py
```

Use the official [Python 3.15.2 release/install manager](https://www.python.org/downloads/release/python-3152/)
and record its normal signed x64 installation. Do not accidentally select ARM64, 32-bit
or free-threaded Python. The version guard refuses another patch rather than silently
changing the qualification environment. Thirteen Windows wheels, including explicit
pefile/pywin32-ctypes builder dependencies, were downloaded/hashed on Linux; this verifies
availability/provenance only, not import, PE linkage or Windows execution.

## Exact-source executable campaign

After tools and normal Codex sign-in are available, read this repository's AGENTS.md.
Use the exact reviewed commit and a NEW private report directory outside Git:

```powershell
$Reports = Join-Path $env:LOCALAPPDATA ('LMThermal-validation\phase6-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
.\packaging\qualify-windows.ps1 -ExpectedCommit $ExpectedCommit -ReportDirectory $Reports -Build
```

The script refuses Linux/WSL, wrong SHA, dirty source and reused output. It runs the
complete native suite with offscreen Qt only for that child, prepares explicitly
fixture-derived legacy inputs, builds/self-checks the executable, audits every byte,
compares all 51 canonical files in strict/recovery modes and executes eleven real-window
cases with the frozen application. Native cases require exposure; locked sessions fail
honestly. Every stage has a private log and raw exit status, with no automatic rerun.
No camera, Android tooling, administrative privileges or hosted Actions is required.

Open the frozen application normally after the campaign:

```powershell
& (Join-Path $Reports 'bundle\dist\LMThermal-Desktop\LMThermal-Desktop.exe')
```

Manually check Home/tabs, normal open/save dialogs, LMTX/LMTR/legacy still and directory
recording, timeline/gaps/recovery, point/ROI, palette/Locked, 1,000-pixel window, errors,
changing files without stale readings, Close during pending work and ordinary final Close.
Keep original hashes and separate PNGs. Automated Qt events are not operator acceptance.

## User installer and retraction

After the executable campaign succeeds, install Inno Setup 6 normally for the current
user and record its exact compiler version. Compile the existing template with:

```powershell
$BundleRoot = Join-Path $Reports 'bundle\dist\LMThermal-Desktop'
$InstallerOutput = Join-Path $Reports 'installer'
ISCC.exe "/DBundleRoot=$BundleRoot" "/DInstallerOutput=$InstallerOutput" packaging\windows-user-installer.iss
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed' }
Get-FileHash -Algorithm SHA256 (Join-Path $InstallerOutput 'LMThermal-Desktop-preview-setup.exe')
```

Close all LMThermal windows first. Use a new private user destination, without replacing
an existing installation. Verify the installed file inventory against the build manifest,
launch from Start Menu and executable, repeat normal dialogs/analysis/Close, add a harmless
user sentinel and keep captures/logs outside the application. Use the normal generated
uninstaller, then verify the sentinel/external files and their hashes remain. Record any
unsigned/SmartScreen prompt; do not disable Defender or bypass organizational policy.
The template requests no elevation, association, driver, service or firewall rule.
No Inno compilation, install/uninstall or signature is claimed from Linux preparation.

Calling the venv executable directly avoids requiring activation-policy changes. If a
reviewed build script is blocked by local execution policy, follow organizational policy
or run its documented Python command directly; no permanent policy weakening is required.
This guide does not install tools, alter credentials or modify system configuration.

## Qualification and handoff

Record source commit, OS/Python/runtime versions, complete test output and expected
Linux-only skips. Open synthetic/copy-owned LMTX, LMTR and historical examples, including
paths with spaces and accented characters. Check stored Float32/mask/native/transport
parity, source hashes before/after, point/ROI, resize, palette/range, timeline/gaps,
metadata and separate PNG save. Exercise Close while a read is pending. Run frozen
`--self-check` and `--verify-lmtr`, then the same GUI checks and install/uninstall as a
standard user. Never reinterpret old Windows LMTX/R5 evidence as qualification of this
new shell or installer. No hardware camera acceptance is implied.

Hosted CI needs a separate budget authorization. Do not push a branch with matching
workflow triggers, start Actions, merge or publish a release merely to follow this recipe.
See [packaging gates](PACKAGING.md). Keep private examples/logs outside Git; review paths
and exception details before sharing. Stop at the tested artifact until release approval.

## Phase 5 qualification candidate

The follow-up source is `codex/phase5-desktop-qualification`, descending from the pinned
phase 4 packaging branch; it fixes refused-Close recovery and narrow LMTR control layout.
The official Windows/Codex documentation was rechecked on 2026-11-13; native PowerShell
and a retained sandbox remain the recommended setup for this project. No Windows run
has occurred. Don't infer runtime/installer acceptance from these script reviews.

After a separately authorized native build, reuse `tools/audit_desktop_bundle.py` for
byte/link checks and `tools/run_window_campaign.py --binary <exe>` for normal-window checks.
Include spaced/Unicode paths, finalization refusal/retry and a 1,000-pixel-wide LMTR window.
The campaign requires explicit offline fixtures/legacy inputs and new private outputs.
Keep the exposure/operator distinction; offscreen mode does not qualify native display.
Review licensing and sign/install/uninstall as a standard user only after owner decisions.

The Linux-only CPython 3.13.18 experiment does not validate Windows or improve the final
Linux glibc floor. Keep native Windows Python/wheel provenance explicit rather than
assuming identical version labels imply identical binary inputs. Either native Python
candidate must run the full Windows suite, frozen UI and installer checks separately.
