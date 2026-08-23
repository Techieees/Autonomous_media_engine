# Native Windows test runner. Docker is not used.
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

$Root = Get-AmeRoot
$Python = Initialize-AmeNativeEnv -Root $Root
Set-Location (Join-Path $Root "backend")
$env:PYTHONPATH = Join-Path $Root "backend"
& $Python -m pytest -q @args
exit $LASTEXITCODE
