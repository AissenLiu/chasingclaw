param(
  [string]$ExeName = "chasingclaw-ui",
  [switch]$Clean,
  [switch]$Zip
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
$EntryScript = Join-Path $ScriptDir "portable_ui_entry.py"
$PortableAssetsDir = Join-Path $ScriptDir "portable"

if (-not (Test-Path $EntryScript)) {
  throw "Entrypoint not found: $EntryScript"
}

$pythonCandidates = @(
  (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
  "python"
)

$PythonExe = $null
foreach ($candidate in $pythonCandidates) {
  if ($candidate -eq "python") {
    $cmd = Get-Command "python" -ErrorAction SilentlyContinue
    if ($cmd) {
      $PythonExe = $cmd.Source
      break
    }
    continue
  }

  if (Test-Path $candidate) {
    $PythonExe = (Resolve-Path $candidate).Path
    break
  }
}

if (-not $PythonExe) {
  throw "Cannot find Python. Use a build machine with Python installed (or create .venv first)."
}

$PyBuildDir = Join-Path $ProjectRoot "build\windows-portable"
$PyDistDir = Join-Path $ProjectRoot "dist\windows-portable-pyinstaller"
$PortableDir = Join-Path $ProjectRoot "dist\chasingclaw-portable"

if ($Clean) {
  if (Test-Path $PyBuildDir) { Remove-Item -Recurse -Force $PyBuildDir }
  if (Test-Path $PyDistDir) { Remove-Item -Recurse -Force $PyDistDir }
  if (Test-Path $PortableDir) { Remove-Item -Recurse -Force $PortableDir }
}

$hasPyInstaller = $false
try {
  & $PythonExe -m PyInstaller --version *> $null
  if ($LASTEXITCODE -eq 0) {
    $hasPyInstaller = $true
  }
} catch {
  $hasPyInstaller = $false
}

if (-not $hasPyInstaller) {
  Write-Host "PyInstaller not found, installing..."
  & $PythonExe -m pip install --upgrade pyinstaller
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to install PyInstaller"
  }
}

New-Item -ItemType Directory -Path $PyBuildDir -Force | Out-Null
New-Item -ItemType Directory -Path $PyDistDir -Force | Out-Null

Write-Host "Building portable executable with: $PythonExe"
$pyInstallerArgs = @(
  "-m", "PyInstaller",
  "--noconfirm",
  "--clean",
  "--onedir",
  "--name", $ExeName,
  "--workpath", $PyBuildDir,
  "--specpath", $PyBuildDir,
  "--distpath", $PyDistDir,
  "--paths", $ProjectRoot,
  "--collect-data", "chasingclaw",
  "--collect-data", "litellm",
  $EntryScript
)

& $PythonExe @pyInstallerArgs
if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller build failed"
}

$BuiltAppDir = Join-Path $PyDistDir $ExeName
if (-not (Test-Path $BuiltAppDir)) {
  throw "PyInstaller output not found: $BuiltAppDir"
}

if (Test-Path $PortableDir) {
  Remove-Item -Recurse -Force $PortableDir
}

Copy-Item -Recurse -Force $BuiltAppDir $PortableDir
Copy-Item -Force (Join-Path $PortableAssetsDir "chasingclaw-portable-ui.ps1") (Join-Path $PortableDir "chasingclaw-portable-ui.ps1")
Copy-Item -Force (Join-Path $PortableAssetsDir "chasingclaw-portable-ui.bat") (Join-Path $PortableDir "chasingclaw-portable-ui.bat")
Copy-Item -Force (Join-Path $PortableAssetsDir "README-PORTABLE.txt") (Join-Path $PortableDir "README-PORTABLE.txt")

New-Item -ItemType Directory -Path (Join-Path $PortableDir ".runtime") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $PortableDir "logs") -Force | Out-Null

$ZipPath = Join-Path $ProjectRoot "dist\chasingclaw-portable.zip"
if ($Zip) {
  if (Test-Path $ZipPath) {
    Remove-Item -Force $ZipPath
  }
  Compress-Archive -Path $PortableDir -DestinationPath $ZipPath
}

Write-Host ""
Write-Host "Build complete."
Write-Host "Portable folder: $PortableDir"
if ($Zip) {
  Write-Host "Zip package: $ZipPath"
}
Write-Host "Run on target machine: chasingclaw-portable-ui.bat"
