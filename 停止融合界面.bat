@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\stop_integrated_ui.ps1"
if errorlevel 1 pause
