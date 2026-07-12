# -*- coding: utf-8 -*-
"""짤공장 감시견 (무창 실행) — ①서버(8777) ②고정주소 터널(cloudflared) 둘 다 지킨다.
죽어 있으면 되살린다. 포트 8778 잠금으로 감시견 중복 실행 방지."""
import socket
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
PYTHONW = Path(sys.executable).parent / "pythonw.exe"
if not PYTHONW.exists():
    PYTHONW = Path(sys.executable)
TUNNEL_ID = "45f69140-7bb3-4646-b6a3-cf891a1af97b"  # jjal.traffic-charger.com (named)

# 감시견 중복 방지 잠금
try:
    _lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _lock.bind(("127.0.0.1", 8778))
    _lock.listen(1)
except OSError:
    sys.exit(0)  # 이미 다른 감시견이 돌고 있음

DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


def server_up():
    try:
        s = socket.create_connection(("127.0.0.1", 8777), timeout=3)
        s.close()
        return True
    except OSError:
        return False


def tunnel_up():
    try:
        out = subprocess.run(
            ["tasklist", "/fi", "imagename eq cloudflared.exe"],
            capture_output=True, text=True, timeout=15,
            creationflags=NO_WINDOW).stdout
        return "cloudflared.exe" in out
    except Exception:
        return True  # 확인 실패 시 재시작 남발 방지


def start_tunnel():
    subprocess.Popen(
        [str(BASE / "bin" / "cloudflared.exe"), "tunnel", "run", TUNNEL_ID],
        cwd=str(BASE), creationflags=DETACHED | NO_WINDOW,
        stdin=subprocess.DEVNULL,
        stdout=open(BASE / "logs" / "tunnel.log", "a"),
        stderr=subprocess.STDOUT)


while True:
    if not server_up():
        try:
            subprocess.Popen([str(PYTHONW), str(BASE / "server.py")],
                             cwd=str(BASE), creationflags=DETACHED,
                             stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        time.sleep(15)
    if not tunnel_up():
        try:
            start_tunnel()
        except Exception:
            pass
        time.sleep(20)
    time.sleep(20)
