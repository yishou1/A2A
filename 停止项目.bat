@echo off
title A2A Project Stopper
echo Stopping A2A business processes...
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\A2A-jzz-integrated-clean\.runtime\stop-windows-current.ps1"
echo.
echo Done. Note: Nacos and auth-mock keep running so the next
echo start is faster; they are reused automatically.
pause
