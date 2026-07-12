@echo off
REM ============================================================
REM  DEPLOY (semi-auto): pull the employee's pushed code,
REM  then restart the live server on this PC.
REM  Run this AFTER you have reviewed what was pushed.
REM ============================================================
cd /d "E:\짤공장 (게시물자동화)"

echo [1/3] Pulling latest code from GitHub...
git pull
if errorlevel 1 (
  echo.
  echo *** git pull FAILED. Fix the problem first. Do NOT restart. ***
  pause
  exit /b 1
)

echo.
echo [2/3] Restarting server (watchdog revives it in ~35s)...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8777 ^| findstr LISTENING') do taskkill /f /pid %%p

echo.
echo [3/3] Done. New code goes live in about 35 seconds.
echo Open http://localhost:8777/ to verify.
pause
