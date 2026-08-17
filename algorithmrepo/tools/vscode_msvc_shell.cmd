@echo off
REM 中文注释: 该脚本用于在当前仓库中拉起带有 MSVC 环境变量的命令行。
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\Tools\VsDevCmd.bat" -arch=x64
cmd /k
