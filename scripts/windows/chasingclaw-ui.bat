@echo off
setlocal

set SCRIPT_DIR=%~dp0
set ACTION=%1
if "%ACTION%"=="" set ACTION=start
if not "%1"=="" shift

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%chasingclaw-ui.ps1" -Action %ACTION% %*

endlocal
