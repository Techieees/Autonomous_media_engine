# Native Windows development stack. Docker is optional and never required.
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

$Root = Get-AmeRoot
$Python = Initialize-AmeNativeEnv -Root $Root
if (-not $Python -or -not (Test-Path -LiteralPath $Python)) {
  throw "Python venv executable missing. Expected .venv\\Scripts\\python.exe"
}
$LogDir = Join-Path $Root ".ame\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$existing = Read-AmePids -Root $Root
if ($existing) {
  Write-Host "Existing AME processes recorded. Stopping them first."
  & (Join-Path $PSScriptRoot "stop.ps1")
}

Write-Host "Detecting optional infrastructure (Postgres/Redis). Missing services use the local fallback."
& $Python -m ame.cli.native_status

function Start-AmeProcess {
  param(
    [string]$Name,
    [string]$FilePath,
    [string[]]$ArgumentList,
    [string]$WorkingDirectory
  )
  $outLog = Join-Path $LogDir "$Name.out.log"
  $errLog = Join-Path $LogDir "$Name.err.log"
  $proc = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList `
    -WorkingDirectory $WorkingDirectory `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -WindowStyle Hidden `
    -PassThru
  Write-Host "Started $Name (pid $($proc.Id))"
  return $proc.Id
}

$apiPid = Start-AmeProcess -Name "api" -FilePath $Python -ArgumentList @(
  "-m", "uvicorn", "ame.api.main:app", "--host", "127.0.0.1", "--port", "8000"
) -WorkingDirectory $Root

$workerPid = Start-AmeProcess -Name "worker" -FilePath $Python -ArgumentList @(
  "-m", "ame.jobs.worker"
) -WorkingDirectory $Root

$schedulerPid = Start-AmeProcess -Name "scheduler" -FilePath $Python -ArgumentList @(
  "-m", "ame.jobs.scheduler"
) -WorkingDirectory $Root

$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npm) { $npm = Get-Command npm -ErrorAction SilentlyContinue }
if (-not $npm) {
  throw "npm not found. Install Node.js, then rerun ./scripts/dev.ps1"
}
$dashboardPid = Start-AmeProcess -Name "dashboard" -FilePath $npm.Source -ArgumentList @(
  "run", "dev"
) -WorkingDirectory (Join-Path $Root "dashboard")

Write-AmePids -Root $Root -Pids ([ordered]@{
  api = $apiPid
  worker = $workerPid
  scheduler = $schedulerPid
  dashboard = $dashboardPid
  started_at = (Get-Date).ToString("o")
})

$healthy = $false
for ($i = 0; $i -lt 40; $i++) {
  try {
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/v1/health" -UseBasicParsing -TimeoutSec 2
    if ($response.StatusCode -eq 200) {
      $healthy = $true
      break
    }
  } catch {
    Start-Sleep -Milliseconds 500
  }
}

Write-Host ""
Write-Host "AME native Windows stack"
Write-Host "  API        http://127.0.0.1:8000"
Write-Host "  Dashboard  http://localhost:3000"
Write-Host "  Worker     running (pid $workerPid)"
Write-Host "  Scheduler  running (pid $schedulerPid)"
if ($healthy) {
  Write-Host "  Health     API responded"
} else {
  Write-Host "  Health     API not ready yet - check .ame/logs/api.err.log"
}
Write-Host "Stop with ./scripts/stop.ps1"
Write-Host "Accept with ./scripts/acceptance.ps1"
