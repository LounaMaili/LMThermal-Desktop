# Native Windows only. No admin, system install, signing or publication.
param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Build on native Windows; Linux is not a Windows qualification.' }
$ProjectRoot = Split-Path $PSScriptRoot -Parent
Push-Location $ProjectRoot
try {
    & .\.venv\Scripts\python.exe tools\build_desktop_bundle.py --output $OutputDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Bundle build/self-check failed' }
} finally { Pop-Location }
