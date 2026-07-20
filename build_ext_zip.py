# -*- coding: utf-8 -*-
"""browser-extension/ 폴더를 '짤공장-수집확장.zip' 으로 묶는다(다른 컴퓨터 설치용).
다른 PC: 이 zip 풀기 → Chrome 주소창 chrome://extensions → 개발자 모드 ON →
        '압축해제된 확장 프로그램 로드' → 푼 폴더 선택. 팝업에서 '짤공장 주소'를
        https://jjal.traffic-charger.com 로 두면 됨.
사용: python build_ext_zip.py  (또는 확장ZIP만들기.cmd 더블클릭)
"""
import io
import sys
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
SRC = BASE / "browser-extension"
OUT = BASE / "짤공장-수집확장.zip"
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

SKIP_SUFFIX = (".zip", ".log", ".tmp")


def main():
    if not SRC.is_dir():
        print("browser-extension 폴더가 없습니다: " + str(SRC))
        return
    n = 0
    with zipfile.ZipFile(str(OUT), "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(SRC.rglob("*")):
            if p.is_dir():
                continue
            if p.suffix.lower() in SKIP_SUFFIX:
                continue
            # zip 내부 경로: browser-extension/ 아래 상대경로(풀면 폴더 하나로)
            arc = "browser-extension/" + str(p.relative_to(SRC)).replace("\\", "/")
            z.write(str(p), arc)
            n += 1
            print("  + " + arc)
    print("")
    print("완료: " + str(OUT) + "  (" + str(n) + "개 파일)")
    print("다른 컴퓨터에서 이 zip 풀고 chrome://extensions → 개발자모드 → 압축해제된 확장 로드")


if __name__ == "__main__":
    main()
