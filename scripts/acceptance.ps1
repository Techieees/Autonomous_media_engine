# Native Windows dry-run acceptance. Docker is not used.
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
$env:AME_ACCEPTANCE_DRIVE_JOBS = "1"
$env:DRY_RUN = "true"

Write-Host "Native environment:"
& $Python -m ame.cli.native_status
Write-Host ""
Write-Host "Running python -m ame.cli.acceptance (in-process job drive, no Docker)"
& $Python -m ame.cli.acceptance
$exit = $LASTEXITCODE

try {
  $health = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/v1/health" -UseBasicParsing -TimeoutSec 3
  Write-Host "Dashboard/API check: health HTTP $($health.StatusCode)"
} catch {
  Write-Host "API not running. Start ./scripts/dev.ps1 to inspect the dashboard at http://localhost:3000"
}

exit $exit
