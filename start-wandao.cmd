@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-wandao.ps1" %*
set "WANDAO_EXIT_CODE=%ERRORLEVEL%"
if not "%WANDAO_EXIT_CODE%"=="0" (
  echo.
  echo Wandao start failed. Press any key to close.
  pause >nul
)
exit /b %WANDAO_EXIT_CODE%
