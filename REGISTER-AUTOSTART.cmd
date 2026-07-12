@echo off
rem Register auto-start: run Meme Factory server on every PC boot/logon
rem Points to START-SERVER.cmd in this file's own folder.
schtasks /Create /TN "MemeFactoryServer" /TR "\"%~dp0START-SERVER.cmd\"" /SC ONLOGON /F
echo.
if %errorlevel%==0 (
  echo [OK] Registered! Server will auto-start when PC boots.
) else (
  echo [FAIL] Something went wrong. Send this screen to Claude.
)
pause
