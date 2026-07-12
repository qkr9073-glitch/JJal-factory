@echo off
rem Meme Factory - image mode. Drag and drop screenshot files (or a folder) onto this file.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
if "%~1"=="" (
  echo Drop image files or a folder onto this cmd file.
  pause
  exit /b 1
)
python make.py %*
pause
