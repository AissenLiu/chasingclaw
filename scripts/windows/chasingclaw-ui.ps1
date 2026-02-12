param(
  [ValidateSet("start", "stop", "status", "restart")]
  [string]$Action = "start",
  [int]$Port = 18789,
  [string]$Host = "0.0.0.0"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
$RuntimeDir = Join-Path $ProjectRoot ".runtime"
$LogDir = Join-Path $ProjectRoot "logs"
$PidFile = Join-Path $RuntimeDir "chasingclaw-ui.pid"
$StdOutLog = Join-Path $LogDir "chasingclaw-ui.out.log"
$StdErrLog = Join-Path $LogDir "chasingclaw-ui.err.log"

New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

function Resolve-ChasingclawExe {
  $venvExe = Join-Path $ProjectRoot ".venv\Scripts\chasingclaw.exe"
  if (Test-Path $venvExe) {
    return (Resolve-Path $venvExe).Path
  }

  $cmd = Get-Command "chasingclaw" -ErrorAction SilentlyContinue
  if ($cmd) {
    return $cmd.Source
  }

  throw "Cannot find chasingclaw executable. Create .venv and install project first."
}

function Read-PidFile {
  if (-not (Test-Path $PidFile)) {
    return $null
  }

  $raw = (Get-Content -Path $PidFile -Raw).Trim()
  if ([string]::IsNullOrWhiteSpace($raw)) {
    return $null
  }

  try {
    return [int]$raw
  } catch {
    return $null
  }
}

function Clear-PidFile {
  if (Test-Path $PidFile) {
    Remove-Item -Path $PidFile -Force
  }
}

function Get-RunningProcessFromPidFile {
  $pid = Read-PidFile
  if (-not $pid) {
    return $null
  }

  $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
  if (-not $proc) {
    Clear-PidFile
    return $null
  }

  return $proc
}

function Save-Pid([int]$pid) {
  Set-Content -Path $PidFile -Value $pid -Encoding ASCII
}

function Start-Ui {
  $running = Get-RunningProcessFromPidFile
  if ($running) {
    Write-Host "chasingclaw UI is already running. PID=$($running.Id)"
    Write-Host "Open: http://localhost:$Port"
    return
  }

  $exe = Resolve-ChasingclawExe
  $args = @("ui", "--host", $Host, "--port", $Port)

  $proc = Start-Process `
    -FilePath $exe `
    -ArgumentList $args `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $StdOutLog `
    -RedirectStandardError $StdErrLog `
    -PassThru

  Start-Sleep -Milliseconds 400
  if ($proc.HasExited) {
    throw "Failed to start chasingclaw UI. Check logs: $StdErrLog"
  }

  Save-Pid -pid $proc.Id

  Write-Host "Started chasingclaw UI in background. PID=$($proc.Id)"
  Write-Host "Open: http://localhost:$Port"
  Write-Host "Logs: $StdOutLog"
}

function Stop-Ui {
  $running = Get-RunningProcessFromPidFile
  if (-not $running) {
    Write-Host "chasingclaw UI is not running."
    return
  }

  Stop-Process -Id $running.Id -Force
  Clear-PidFile
  Write-Host "Stopped chasingclaw UI."
}

function Show-Status {
  $running = Get-RunningProcessFromPidFile
  if ($running) {
    Write-Host "Status: RUNNING (PID=$($running.Id))"
    Write-Host "Open: http://localhost:$Port"
    Write-Host "Logs: $StdOutLog"
  } else {
    Write-Host "Status: STOPPED"
  }
}

switch ($Action) {
  "start" {
    Start-Ui
  }
  "stop" {
    Stop-Ui
  }
  "status" {
    Show-Status
  }
  "restart" {
    Stop-Ui
    Start-Sleep -Milliseconds 200
    Start-Ui
  }
}
