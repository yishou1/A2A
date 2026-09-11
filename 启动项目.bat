@echo off
title A2A Project Launcher
cd /d D:\A2A-jzz-integrated-clean
echo ============================================
echo  A2A full stack launcher
echo  Infra (Nacos + auth-mock): Docker first,
echo  auto-fallback to native if Docker fails.
echo  Expected startup time: about 3-5 minutes
echo  (first run may download Docker images).
echo  Please keep this window open.
echo ============================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File ".runtime\start-windows-current.ps1"
echo.
echo Startup finished. Opening AMOS UI in browser...
start "" http://127.0.0.1:5000/
pause
