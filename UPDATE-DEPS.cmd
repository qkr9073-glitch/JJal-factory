@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Updating yt-dlp (Instagram/YouTube downloader)...
python -m pip install -U yt-dlp
echo.
echo Updating other requirements...
python -m pip install -r requirements.txt -q
echo.
echo Done. Re-run transcript extraction to verify.
pause
