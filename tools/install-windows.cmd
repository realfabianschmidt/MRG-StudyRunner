@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-windows.ps1" %*
set "study_runner_exit_code=%ERRORLEVEL%"
endlocal & exit /b %study_runner_exit_code%
