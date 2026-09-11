@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\start_integrated_ui.ps1" -OpenBrowser
if errorlevel 1 pause
