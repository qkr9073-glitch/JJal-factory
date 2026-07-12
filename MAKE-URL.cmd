@echo off
rem Meme Factory - URL mode. Copy a post URL, then double-click this file.
rem Uses its own folder as base, so it works from any location.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
set "TARGET=%~1"
if not defined TARGET (
  for /f "usebackq delims=" %%u in (`powershell -noprofile -command "$c=Get-Clipboard; if($c -is [array]){$c=$c[0]}; $c.Trim()"`) do set "TARGET=%%u"
)
echo TARGET: "%TARGET%"
python make.py "%TARGET%"
pause
