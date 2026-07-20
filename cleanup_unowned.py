# -*- coding: utf-8 -*-
"""소유자(계정태그) 없는 옛 팩만 골라 백업폴더로 이동 (되돌리기 가능).
- pack_owners.json 에 소유자가 있는 팩은 그대로 둠(예약 팩 포함 안전).
- 소유자 없는 팩만 _backup_결과물_미소유_<시각>/ 로 이동(하드삭제 아님).
- 이동 대상 중 예약(schedule.json)에 걸린 게 있으면 따로 표시.
사용: python cleanup_unowned.py  (또는 미소유팩정리.cmd 더블클릭)
⚠️ 전체 초기화가 아님 — 계정태그 있는(=최근·예약) 팩은 안전하게 보존됨.
"""
import io
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass


def _load(fn, default):
    try:
        return json.loads((BASE / fn).read_text(encoding="utf-8"))
    except Exception:
        return default


def main():
    cfg = _load("config.json", {})
    out = BASE / (cfg.get("output_dir") or "결과물")
    owners = _load("pack_owners.json", {})
    sched_items = _load("schedule.json", [])
    scheduled = set()
    for e in (sched_items if isinstance(sched_items, list) else []):
        if isinstance(e, dict) and e.get("status") in ("pending", "await"):
            pk = e.get("pack") or e.get("video_pack") or ""
            if pk:
                scheduled.add(pk)

    if not out.is_dir():
        print("결과물 폴더가 없습니다: " + str(out))
        return

    unowned, sched_hit = [], []
    for d in sorted(out.iterdir()):
        if not d.is_dir() or d.name in ("_사용완료", "_휴지통"):
            continue
        if not (d / "review.html").exists():
            continue
        if owners.get(d.name):          # 소유자 있으면 보존
            continue
        unowned.append(d.name)
        if d.name in scheduled:
            sched_hit.append(d.name)

    print("결과물 폴더 : " + str(out))
    print("소유자 없는(정리 대상) 팩 : " + str(len(unowned)) + "개")
    if sched_hit:
        print("  ⚠️ 이 중 예약 걸린 팩 " + str(len(sched_hit)) + "개(백업으로 옮기면 그 예약은 게시 안 됨):")
        for n in sched_hit:
            print("     - " + n)
    if not unowned:
        print("정리할 게 없습니다.")
        return

    ans = input("\n소유자 없는 팩 " + str(len(unowned)) + "개를 백업폴더로 옮길까요? (yes 입력): ").strip().lower()
    if ans not in ("yes", "y"):
        print("취소됨.")
        return

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BASE / ("_backup_결과물_미소유_" + stamp)
    dest.mkdir(parents=True, exist_ok=True)
    moved = 0
    for name in unowned:
        try:
            shutil.move(str(out / name), str(dest / name))
            moved += 1
        except Exception as e:
            print("  이동 실패: " + name + " (" + str(e)[:60] + ")")
    print("\n완료: " + str(moved) + "개 팩을 " + dest.name + " 으로 이동(되돌리기 가능).")
    print("롤백: 그 폴더의 팩들을 결과물/ 로 되돌려 복사.")


if __name__ == "__main__":
    main()
