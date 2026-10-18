@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\project.ps1" -Action Stop
set "launch_result=%ERRORLEVEL%"
if not "%launch_result%"=="0" pause
exit /b %launch_result%
