@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/3] Fetching latest from GitHub...
git fetch origin
echo [2/3] Force sync to origin/main (discards local CODE edits; keeps config/results/bgm/fonts)...
git reset --hard origin/main
echo [3/3] Restarting server (watchdog revives it in ~35s)...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8777 ^| findstr LISTENING') do taskkill /f /pid %%p
echo.
echo Done. New code goes live in about 35 seconds. Open http://localhost:8777/
pause
