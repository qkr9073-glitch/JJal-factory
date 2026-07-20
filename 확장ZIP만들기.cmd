@echo off
chcp 65001 >nul
rem Build the collector extension into a zip for another PC.
cd /d "%~dp0"
python 확장ZIP만들기.py
echo.
pause
