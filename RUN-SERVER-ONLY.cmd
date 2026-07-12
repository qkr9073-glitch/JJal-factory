@echo off
rem Meme Factory server watchdog - restarts server automatically if it dies
cd /d D:\meme-factory
if not exist logs mkdir logs
:loop
set PYTHONIOENCODING=utf-8
python server.py >> logs\server.log 2>&1
echo [watchdog] server exited, restarting in 5s >> logs\server.log
timeout /t 5 /nobreak >nul
goto loop
