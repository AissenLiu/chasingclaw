@echo off
setlocal

set SCRIPT_DIR=%~dp0

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%chasingclaw-portable-ui.ps1" %*
if errorlevel 1 (
  echo.
  echo Start failed. Check logs under logs\\chasingclaw-ui.err.log
  pause
)

endlocal
