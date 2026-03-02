param(
  [string]$Target = "x86_64-pc-windows-msvc",
  [switch]$Clean,
  [switch]$SkipPythonBuild
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
$TauriRoot = Join-Path $ProjectRoot "desktop\tauri"
$TauriSrc = Join-Path $TauriRoot "src-tauri"
$SidecarBinDir = Join-Path $TauriSrc "bin"
$SidecarSource = Join-Path $ProjectRoot "dist\windows-portable-pyinstaller\chasingclaw-ui\chasingclaw-ui.exe"
$SidecarTarget = Join-Path $SidecarBinDir ("chasingclaw-ui-" + $Target + ".exe")

if (-not (Test-Path $TauriRoot)) {
  throw "Tauri project not found: $TauriRoot"
}

if (-not $SkipPythonBuild) {
  $portableScript = Join-Path $ScriptDir "build-portable.ps1"
  $portableArgs = @("-ExeName", "chasingclaw-ui")
  if ($Clean) {
    $portableArgs += "-Clean"
  }

  Write-Host "Building Python sidecar..."
  & $portableScript @portableArgs
  if ($LASTEXITCODE -ne 0) {
    throw "build-portable.ps1 failed"
  }
}

if (-not (Test-Path $SidecarSource)) {
  throw "Sidecar executable not found: $SidecarSource"
}

$npmCmd = Get-Command npm -ErrorAction SilentlyContinue
if (-not $npmCmd) {
  throw "npm is required. Install Node.js LTS first."
}

$cargoCmd = Get-Command cargo -ErrorAction SilentlyContinue
if (-not $cargoCmd) {
  throw "cargo is required. Install Rust toolchain first."
}

if ($Clean) {
  $tauriTargetDir = Join-Path $TauriSrc "target"
  if (Test-Path $tauriTargetDir) {
    Remove-Item -Recurse -Force $tauriTargetDir
  }
}

New-Item -ItemType Directory -Path $SidecarBinDir -Force | Out-Null
Copy-Item -Force $SidecarSource $SidecarTarget

Write-Host "Sidecar copied to: $SidecarTarget"

Push-Location $TauriRoot
try {
  if (Test-Path (Join-Path $TauriRoot "package-lock.json")) {
    npm ci
  } else {
    npm install
  }

  if ($LASTEXITCODE -ne 0) {
    throw "npm install failed"
  }

  npm run tauri:build
  if ($LASTEXITCODE -ne 0) {
    throw "tauri build failed"
  }
}
finally {
  Pop-Location
}

$bundleDir = Join-Path $TauriSrc "target\release\bundle"
Write-Host ""
Write-Host "Build complete."
Write-Host "Bundle output: $bundleDir"
