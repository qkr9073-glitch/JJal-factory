@echo off
REM Restart factory server: kill the process on port 8777.
REM Watchdog (port 8778 lock) will revive it with the new code in about 35 seconds.
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8777 ^| findstr LISTENING') do taskkill /f /pid %%p
echo.
echo Server stopped. Watchdog will restart it automatically in ~35 seconds.
echo Then open http://localhost:8777/card to check the new Card News page.
pause
