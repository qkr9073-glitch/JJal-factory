@echo off
chcp 65001 >nul
rem ============================================================
rem  짤공장 전용 VSCode + 클로드 세션 열기 (더블클릭)
rem  - 이 폴더(d:\meme-factory)를 VSCode로 엽니다.
rem  - remotion-video 프로젝트와 완전히 분리된 세션입니다.
rem  - 짤공장 CLAUDE.md + 짤공장 메모리가 이 폴더 기준으로 로드됩니다.
rem  - VSCode가 뜨면 클로드 패널을 여세요:  Ctrl + Esc  (또는 좌측 클로드 아이콘)
rem    지난 대화는 이 폴더 워크스페이스에 저장돼 그대로 이어집니다.
rem ============================================================
cd /d "%~dp0"

where code >nul 2>&1
if errorlevel 1 (
  echo [오류] VSCode의 'code' 명령을 찾을 수 없습니다.
  echo        VSCode에서  Ctrl+Shift+P  →  "Shell Command: Install 'code' command in PATH"  실행 후 다시 시도하세요.
  pause
  exit /b 1
)

echo 짤공장 폴더를 VSCode로 엽니다...
start "" code "%~dp0"

echo.
echo  ✅ VSCode가 열리면  Ctrl + Esc  로 클로드 세션을 시작/재개하세요.
echo     (서버까지 켜려면  짤공장-서버-재시작.cmd  를 더블클릭)
timeout /t 4 /nobreak >nul
