@echo off
rem Meme Factory - start windowless watchdog (keeps server alive) + named tunnel
rem Fixed public URL: https://jjal.traffic-charger.com (config: %USERPROFILE%\.cloudflared\config.yml)
cd /d "%~dp0"
if not exist logs mkdir logs
start "" pythonw watchdog.pyw
tasklist /fi "imagename eq cloudflared.exe" | find /i "cloudflared.exe" >nul
if errorlevel 1 (
  start "mf-tunnel" /min "%~dp0bin\cloudflared.exe" tunnel --logfile "%~dp0logs\tunnel-named.log" run factory
)
