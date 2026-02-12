param(
  [ValidateSet("install", "remove", "status", "run")]
  [string]$Action = "install",

  [ValidateSet("onlogon", "onstart")]
  [string]$Trigger = "onlogon",

  [string]$TaskName = "chasingclaw-ui"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$UiBat = Join-Path $ScriptDir "chasingclaw-ui.bat"

if (-not (Test-Path $UiBat)) {
  throw "Cannot find script: $UiBat"
}

function Invoke-Schtasks([string[]]$Args) {
  $proc = Start-Process -FilePath "schtasks.exe" -ArgumentList $Args -Wait -PassThru -NoNewWindow
  if ($proc.ExitCode -ne 0) {
    throw "schtasks failed with exit code $($proc.ExitCode)"
  }
}

function Install-Task {
  $schedule = if ($Trigger -eq "onstart") { "ONSTART" } else { "ONLOGON" }
  $taskRun = ('"{0}" start' -f $UiBat)

  $args = @(
    "/Create",
    "/TN", $TaskName,
    "/SC", $schedule,
    "/TR", $taskRun,
    "/F"
  )

  if ($Trigger -eq "onstart") {
    # ONSTART usually requires elevated PowerShell
    $args += @("/RU", "SYSTEM")
  } else {
    $args += @("/RL", "HIGHEST", "/RU", $env:USERNAME)
  }

  Invoke-Schtasks $args
  Write-Host "Installed task '$TaskName' with trigger '$Trigger'."
  Write-Host "Check task: schtasks /Query /TN $TaskName /V /FO LIST"
}

function Remove-Task {
  Invoke-Schtasks @("/Delete", "/TN", $TaskName, "/F")
  Write-Host "Removed task '$TaskName'."
}

function Show-TaskStatus {
  Invoke-Schtasks @("/Query", "/TN", $TaskName, "/V", "/FO", "LIST")
}

function Run-TaskNow {
  Invoke-Schtasks @("/Run", "/TN", $TaskName)
  Write-Host "Triggered task '$TaskName'."
}

switch ($Action) {
  "install" {
    Install-Task
  }
  "remove" {
    Remove-Task
  }
  "status" {
    Show-TaskStatus
  }
  "run" {
    Run-TaskNow
  }
}
