# -*- coding: utf-8 -*-
"""짤공장 자동배포기 (무창 실행).

깃허브 origin/main 에 새 코드가 올라오면 자동으로 받아(git pull --ff-only) 반영한다.
.py 가 바뀌면 서버(8777)를 재시작한다(감시견이 새 코드로 부활).
- 포트 8779 잠금으로 중복 실행 방지.
- 사장님이 로컬에서 수정 중(작업트리 dirty)이면 pull 을 건너뛴다(로컬 작업 보호).
- 로컬/원격이 분기되면(ff 불가) 자동 병합하지 않고 로그만 남긴다(사람 개입 필요).
주기(POLL)만큼 확인 → 보통 push 후 ~1분 안에 라이브 반영.
"""
import socket
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
LOG = BASE / "logs" / "autopull.log"
POLL = 30            # 확인 주기(초) — 더 빠르게/느리게 바꾸려면 이 숫자만 수정
NO_WINDOW = 0x08000000
DETACHED = 0x00000008 | 0x00000200

# 중복 실행 방지 잠금
try:
    _lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _lock.bind(("127.0.0.1", 8779))
    _lock.listen(1)
except OSError:
    sys.exit(0)  # 이미 다른 자동배포기가 돌고 있음


def log(msg):
    try:
        LOG.parent.mkdir(exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S  ") + msg + "\n")
    except Exception:
        pass


def git(*args, timeout=90):
    return subprocess.run(
        ["git", *args], cwd=str(BASE),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=NO_WINDOW)


def dirty():
    """로컬에 커밋 안 된 수정이 있으면 True (사장님이 편집 중일 수 있음)."""
    r = git("status", "--porcelain")
    return bool((r.stdout or "").strip())


def head():
    return (git("rev-parse", "HEAD").stdout or "").strip()


def restart_server():
    """8777 서버 프로세스를 죽인다. 감시견이 ~20초 내 새 코드로 부활시킨다."""
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             timeout=15, creationflags=NO_WINDOW).stdout or ""
        for line in out.splitlines():
            if ":8777" in line and "LISTENING" in line.upper():
                pid = line.split()[-1]
                subprocess.run(["taskkill", "/f", "/pid", pid],
                               capture_output=True, timeout=15, creationflags=NO_WINDOW)
                return True
    except Exception as e:
        log("서버 재시작 실패: %r" % e)
    return False


log("=== 자동배포기 시작 (%d초 주기) ===" % POLL)

while True:
    try:
        f = git("fetch", "origin", "main")
        if f.returncode != 0:
            log("fetch 실패: " + (f.stderr or "").strip()[:200])
            time.sleep(POLL)
            continue

        before = head()
        remote = (git("rev-parse", "origin/main").stdout or "").strip()

        if before and remote and before != remote:
            if dirty():
                log("로컬 수정 중(dirty) — 자동 pull 건너뜀. 커밋/푸시하면 그때 반영됨.")
                time.sleep(POLL)
                continue

            m = git("merge", "--ff-only", "origin/main")
            after = head()

            if after == before:
                # fast-forward 불가 = 로컬과 원격이 분기됨 → 사람이 정리해야 함
                log("⚠ ff-only 병합 불가(로컬/원격 분기): " + (m.stderr or "").strip()[:200])
            else:
                diff = git("diff", "--name-only", before, after).stdout or ""
                changed = [x for x in diff.splitlines() if x.strip()]
                log("배포됨 %s → %s (%d개 파일)" % (before[:7], after[:7], len(changed)))
                if any(c.endswith(".py") for c in changed):
                    if restart_server():
                        log(".py 변경 감지 → 서버 재시작 (감시견이 새 코드로 부활)")
                else:
                    log("코드(.py) 변경 없음 → 서버 재시작 생략")
    except Exception as e:
        log("루프 예외: %r" % e)

    time.sleep(POLL)
