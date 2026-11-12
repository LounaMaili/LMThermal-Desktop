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
git switch codex/desktop-packaging-preview
py -3.15 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r packaging\requirements-build.txt
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
git diff --check
Remove-Item Env:QT_QPA_PLATFORM
.\.venv\Scripts\python.exe lmthermal_desktop.py
```

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
