@echo off
chcp 65001 >nul
rem ============================================================
rem  Backup runtime data (JSON state) for rollback.
rem  Double-click before/after switching to v2 to keep a restore point.
rem ============================================================
cd /d "%~dp0"
python backup_data.py
echo.
echo (backup folder: _backup_data_<time>)
pause
