@echo off
setlocal
set "repo_root=%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%repo_root%\scripts\docker-start.ps1"
set "exit_code=%ERRORLEVEL%"
if not "%exit_code%"=="0" pause
exit /b %exit_code%
