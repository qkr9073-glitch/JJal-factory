# -*- coding: utf-8 -*-
"""결과물 초기화 (롤백 가능).
현재 결과물/ 폴더 전체 + 소유자/게시/사용기록을 _backup_결과물_<시각>/ 로 옮겨 백업하고,
사이트 결과물을 빈 상태로 초기화한다. (구 UI로 만든 '소유자 미상' 팩 정리용)
롤백: 백업 폴더의 내용을 원래 위치(짤공장 폴더)로 되돌려 복사.
사용: python reset_results.py  (또는 결과물초기화.cmd 더블클릭)
⚠️ 제작이 돌고 있지 않을 때 실행하세요(파일 이동 중 충돌 방지).
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


def _cfg():
    try:
        return json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def main():
    out_name = _cfg().get("output_dir") or "결과물"
    out = BASE / out_name
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BASE / ("_backup_결과물_" + stamp)

    n = sum(1 for p in out.iterdir() if p.is_dir()) if out.exists() else 0
    print("결과물 폴더 : " + str(out) + "  (하위 " + str(n) + "개)")
    print("백업 위치   : " + str(dest))
    print("")
    ans = input("모든 결과물을 백업하고 사이트를 초기화할까요? (yes 입력): ").strip().lower()
    if ans not in ("yes", "y"):
        print("취소됨.")
        return

    dest.mkdir(parents=True, exist_ok=True)

    # 1) 결과물 폴더 통째로 백업(이동) 후 빈 폴더 재생성
    if out.exists():
        shutil.move(str(out), str(dest / out_name))
    out.mkdir(parents=True, exist_ok=True)

    # 2) 소유자/게시/사용 기록 백업 후 초기화(모두 dict → 빈 {})
    for fn in ("pack_owners.json", "published.json", "usage.json"):
        src = BASE / fn
        if src.exists():
            shutil.copy2(str(src), str(dest / fn))
            src.write_text("{}", encoding="utf-8")

    print("")
    print("완료: 결과물 " + str(n) + "개 + 소유자/게시/사용 기록 → " + dest.name)
    print("사이트 결과물은 빈 상태로 초기화됨.")
    print("롤백: 백업 폴더의 '" + out_name + "' 및 *.json 을 원위치로 되돌리기.")


if __name__ == "__main__":
    main()
