@echo off
title A2A Project Launcher
cd /d D:\A2A-jzz-integrated-clean
echo ============================================
echo  A2A full stack launcher (native Windows,
echo  no Docker / no WSL required)
echo  Expected startup time: about 3-5 minutes.
echo  Please keep this window open.
echo ============================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File ".runtime\start-windows-current.ps1"
echo.
echo Startup finished. Opening AMOS UI in browser...
start "" http://127.0.0.1:5000/
pause
