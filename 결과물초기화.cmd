@echo off
chcp 65001 >nul
cd /d "%~dp0"
python reset_results.py
echo.
pause
