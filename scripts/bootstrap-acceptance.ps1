# Maximum real bootstrap acceptance. Simulated external boundaries only.
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

$Root = Get-AmeRoot
$Python = Initialize-AmeNativeEnv -Root $Root
Set-Location $Root

if (Test-Path (Join-Path $Root "scripts\stop.ps1")) {
  & (Join-Path $Root "scripts\stop.ps1")
}

@(
  "data\ame.dev.db",
  "data\ame.dev.db-wal",
  "data\ame.dev.db-shm"
) | ForEach-Object {
  $path = Join-Path $Root $_
  if (Test-Path $path) { Remove-Item -Force $path }
}

$env:PYTHONPATH = Join-Path $Root "backend"
$env:AUTONOMOUS_MODE = "true"
$env:DRY_RUN = "true"
$env:AME_BOOTSTRAP_SIMULATION = "true"
$env:AME_BOOTSTRAP_OPEN_BROWSER = "false"
$env:AME_ACCEPTANCE_DRIVE_JOBS = "1"
$env:OWNER_TIMEZONE = "Europe/Dublin"

Write-Host "Native bootstrap acceptance (simulated external boundaries, no real accounts)"
& $Python -m ame.cli.native_status
Write-Host ""
& $Python -m ame.cli.bootstrap_acceptance
exit $LASTEXITCODE
