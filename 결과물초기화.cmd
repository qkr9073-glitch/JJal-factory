@echo off
chcp 65001 >nul
rem ============================================================
rem  Reset site results: move current 결과물/ + owner/publish/usage
rem  records into a timestamped backup folder, then start empty.
rem  Rollback = copy the backup folder contents back.
rem  Run when no creation job is running.
rem ============================================================
cd /d "%~dp0"
python reset_results.py
echo.
pause
