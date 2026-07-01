@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-wandao.ps1" %*
if errorlevel 1 (
  echo.
  echo Wandao start failed. Press any key to close.
  pause >nul
)
