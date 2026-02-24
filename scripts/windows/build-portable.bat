@echo off
setlocal

set SCRIPT_DIR=%~dp0

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%build-portable.ps1" %*
if errorlevel 1 (
  echo.
  echo Portable build failed.
  pause
)

endlocal
