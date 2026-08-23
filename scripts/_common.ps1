$ErrorActionPreference = "Stop"

function Get-AmeRoot {
  return (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

function Get-AmePython {
  param([string]$Root)
  $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
  if (Test-Path $venvPython) {
    return $venvPython
  }
  return $null
}

function Find-AmeSystemPython {
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  $cmd = Get-Command py -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  throw "Python 3.12+ not found. Install Python and retry. Docker is not required."
}

function Initialize-AmeNativeEnv {
  param([string]$Root)

  Set-Location $Root
  if (-not (Test-Path (Join-Path $Root ".env"))) {
    Copy-Item (Join-Path $Root ".env.example") (Join-Path $Root ".env")
    Write-Host "Created .env from .env.example"
  }

  $venvPython = Get-AmePython -Root $Root
  if (-not $venvPython) {
    Write-Host "Creating Python virtual environment at .venv"
    $systemPython = Find-AmeSystemPython
    & $systemPython -m venv (Join-Path $Root ".venv") | Out-Host
    $venvPython = Get-AmePython -Root $Root
    if (-not $venvPython) {
      throw "Failed to create .venv"
    }
  }

  $marker = Join-Path $Root ".ame\pip.stamp"
  $pyproject = Join-Path $Root "backend\pyproject.toml"
  $needsInstall = -not (Test-Path $marker)
  if (-not $needsInstall -and (Test-Path $pyproject)) {
    if ((Get-Item $pyproject).LastWriteTimeUtc -gt (Get-Item $marker).LastWriteTimeUtc) {
      $needsInstall = $true
    }
  }
  if ($needsInstall) {
    Write-Host "Installing backend package into .venv (native Windows, no Docker)"
    & $venvPython -m pip install --upgrade pip | Out-Host
    & $venvPython -m pip install -e "$Root\backend[dev]" | Out-Host
    New-Item -ItemType Directory -Force -Path (Join-Path $Root ".ame") | Out-Null
    Get-Date -Format o | Set-Content -Path $marker
  }

  $dashboardPkg = Join-Path $Root "dashboard\package.json"
  $dashboardMods = Join-Path $Root "dashboard\node_modules"
  if ((Test-Path $dashboardPkg) -and -not (Test-Path $dashboardMods)) {
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npm) { $npm = Get-Command npm -ErrorAction SilentlyContinue }
    if (-not $npm) {
      throw "npm not found. Install Node.js to run the dashboard. Docker is not required."
    }
    Write-Host "Installing dashboard npm dependencies"
    Push-Location (Join-Path $Root "dashboard")
    try {
      & $npm.Source install | Out-Host
    } finally {
      Pop-Location
    }
  }

  $env:PYTHONPATH = Join-Path $Root "backend"
  $env:PYTHONUNBUFFERED = "1"
  $env:DRY_RUN = "true"
  return [string](Get-AmePython -Root $Root)
}

function Get-AmePidFile {
  param([string]$Root)
  return (Join-Path $Root ".ame\pids.json")
}

function Read-AmePids {
  param([string]$Root)
  $path = Get-AmePidFile -Root $Root
  if (-not (Test-Path $path)) { return $null }
  return Get-Content -Raw -Path $path | ConvertFrom-Json
}

function Write-AmePids {
  param([string]$Root, $Pids)
  New-Item -ItemType Directory -Force -Path (Join-Path $Root ".ame") | Out-Null
  $Pids | ConvertTo-Json | Set-Content -Path (Get-AmePidFile -Root $Root) -Encoding utf8
}

function Stop-AmePid {
  param([int]$ProcessId, [string]$Name)
  if (-not $ProcessId) { return }
  $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
  if ($proc) {
    Write-Host "Stopping $Name (pid $ProcessId)"
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
  }
}
