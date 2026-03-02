@echo off
setlocal

set SCRIPT_DIR=%~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%build-tauri-desktop.ps1" %*
if errorlevel 1 (
  echo.
  echo Build failed.
  exit /b 1
)

echo.
echo Build succeeded.
exit /b 0
