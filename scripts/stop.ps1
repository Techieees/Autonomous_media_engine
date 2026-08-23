# Stop the native Windows development stack started by scripts/dev.ps1
$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "_common.ps1")

$Root = Get-AmeRoot
$pids = Read-AmePids -Root $Root
if (-not $pids) {
  Write-Host "No .ame/pids.json found. Nothing to stop."
  exit 0
}

Stop-AmePid -ProcessId ([int]$pids.api) -Name "api"
Stop-AmePid -ProcessId ([int]$pids.worker) -Name "worker"
Stop-AmePid -ProcessId ([int]$pids.scheduler) -Name "scheduler"
Stop-AmePid -ProcessId ([int]$pids.dashboard) -Name "dashboard"

$pidFile = Get-AmePidFile -Root $Root
if (Test-Path $pidFile) {
  Remove-Item $pidFile -Force
}
Write-Host "AME native processes stopped."
