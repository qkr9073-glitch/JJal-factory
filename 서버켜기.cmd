@echo off
chcp 65001 >nul
rem ============================================================
rem  짤공장 서버 켜기 (이 PC 전용)
rem  - venv 파이썬으로 감시견(watchdog)을 실행합니다.
rem  - 감시견이 서버(8777) + 고정주소 터널 + 자동배포를 알아서 켜고 유지합니다.
rem  - 이미 켜져 있으면 아무 일도 안 합니다(중복 실행 안전).
rem  ※ START-SERVER.cmd 는 시스템 파이썬을 써서 이 PC에선 flask 없음 -> 실패.
rem    그래서 반드시 이 파일을 쓰세요.
rem ============================================================
cd /d "%~dp0"
if not exist logs mkdir logs

if not exist "%~dp0.venv\Scripts\pythonw.exe" (
  echo [오류] .venv 파이썬이 없습니다. 먼저 가상환경을 만들고 의존성을 설치하세요.
  pause
  exit /b 1
)

echo 짤공장 감시견을 시작합니다...
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0watchdog.pyw"

echo 서버가 뜨는지 확인 중입니다 (최대 40초)...
setlocal enabledelayedexpansion
set UP=0
for /l %%i in (1,1,20) do (
  timeout /t 2 /nobreak >nul
  powershell -NoProfile -Command "try{(New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',8777);exit 0}catch{exit 1}" >nul 2>&1
  if !errorlevel! equ 0 (
    set UP=1
    goto :done
  )
)
:done
if "!UP!"=="1" (
  echo.
  echo  ✅ 서버가 켜졌습니다.  브라우저에서 http://localhost:8777/v2 를 여세요.
  start "" "http://localhost:8777/v2"
) else (
  echo.
  echo  ⚠ 아직 안 떴습니다. logs\server.log 를 확인하세요.
)
echo.
pause
