@echo off
chcp 65001 >nul
py -3.12 "%~dp0paperbox_grasp_simple.py" prepare --run
pause
