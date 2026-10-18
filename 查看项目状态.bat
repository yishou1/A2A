@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\project.ps1" -Action Status
set "launch_result=%ERRORLEVEL%"
pause
exit /b %launch_result%
