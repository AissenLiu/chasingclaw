@echo off
setlocal

set SCRIPT_DIR=%~dp0
set ACTION=%1
if "%ACTION%"=="" set ACTION=start
if not "%1"=="" shift

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%chasingclaw-portable-ui.ps1" -Action %ACTION% %*
if errorlevel 1 (
  echo.
  echo Start failed. Check logs under logs\\chasingclaw-ui.err.log
  pause
)

endlocal
