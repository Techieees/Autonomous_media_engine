# Native Windows setup. Docker is optional deploy tooling and is never invoked here.
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

$Root = Get-AmeRoot
$Python = Initialize-AmeNativeEnv -Root $Root
Write-Host "Native setup complete."
& $Python -m ame.cli.native_status
Write-Host ""
Write-Host "Next: ./scripts/dev.ps1"
Write-Host "Then: ./scripts/acceptance.ps1"
