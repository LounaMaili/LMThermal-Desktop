# Preparation only. Run as a normal user on native Windows 11, using the reviewed exact source.
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{40}$')][string]$ExpectedCommit,
    [Parameter(Mandatory=$true)][string]$ReportDirectory,
    [switch]$Build
)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Native Windows required; do not use WSL as evidence.' }
$ProjectRoot = Split-Path $PSScriptRoot -Parent
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw 'Prepare the documented native Python venv first.' }
Push-Location $ProjectRoot
try {
    # Avoid embedded quotes in native -c arguments, including Windows PowerShell 5.1.
    & $Python -c 'import sys; assert sys.version_info[:3] == (3,15,2) and sys.maxsize > 2**32'
    if ($LASTEXITCODE -ne 0) { throw 'Wrong Python version/architecture' }
    $Arguments = @('tools\qualify_native_desktop.py','--native-windows','--expected-commit',$ExpectedCommit,'--output',$ReportDirectory)
    if ($Build) { $Arguments += '--build' }
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'Qualification failed; preserve the report directory, do not retry automatically.' }
} finally { Pop-Location }
