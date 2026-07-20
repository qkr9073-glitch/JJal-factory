# -*- coding: utf-8 -*-
"""운영 데이터 백업 (롤백용).
상태 JSON들과 릴스 프로젝트 state를 _backup_data_<시각>/ 로 복사한다.
v2 전환/코드 변경 전에 한 번 돌려두면, 문제 시 이 폴더 파일을 원위치로 되돌려 복구 가능.
사용: python backup_data.py  (또는 데이터백업.cmd 더블클릭)
※ config.json·scheduled_reels.json 등은 .gitignore라 git이 안 건드림 —
  이 백업은 '실행 중 파일이 수정/오염되는 경우'까지 대비한 스냅샷이다.
"""
import io
import shutil
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
try:                                     # CP949 콘솔에서 한글/이모지 print 깨짐 방지
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

# 롤백에 필요한 런타임 상태 파일들(작은 JSON) — 결과물/영상 등 대용량은 제외
STATE_FILES = [
    "config.json",              # 키·계정·설정(토큰 자동연장으로 실행 중 갱신됨)
    "schedule.json",            # v2 통합 예약·승인
    "scheduled_reels.json",     # 구 릴스 예약(이관 전 데이터 보존)
    "members.json",             # 회원코드
    "published.json",           # 업로드 중복방지 기록
    "pack_owners.json",         # 결과물 소유자
    "ig_targets.json", "ig_collected.json", "ig_accounts.json",
    "youtube_keys.json", "usage.json", "seen.json",
    "jobs_pending.json", "styles.json", "templates.json",
]


def main():
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BASE / ("_backup_data_" + stamp)
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    print("데이터 백업 -> " + dest.name)
    for name in STATE_FILES:
        src = BASE / name
        if src.exists():
            shutil.copy2(str(src), str(dest / name))
            n += 1
            print("  o " + name)
    # 릴스 프로젝트는 state.json만(클립 영상은 용량 커서 제외)
    rp = BASE / "reelproj"
    if rp.is_dir():
        for st in rp.glob("*/state.json"):
            d = dest / "reelproj" / st.parent.name
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(st), str(d / "state.json"))
            n += 1
    print("")
    print("완료: " + str(n) + "개 파일 백업됨  (" + str(dest) + ")")
    print("롤백: 이 폴더의 파일을 원래 위치(짤공장 폴더)로 덮어쓰기 복사")


if __name__ == "__main__":
    main()
