# -*- coding: utf-8 -*-
"""
짤공장 (meme-factory) — 커뮤니티 글 → 인스타 완성팩 자동 생성

사용법:
  python make.py <게시물URL>              # URL 모드 (디시/루리웹/에펨/기타)
  python make.py <이미지1> <이미지2> ...   # 이미지 모드 (캡처 짤 직접 투입)
  python make.py <이미지폴더>              # 폴더 안 이미지 전부 사용

옵션:
  --mock      Gemini 없이 테스트 (가짜 캡션)
  --no-open   완료 후 review.html 자동 열기 끔
"""
import json
import sys
from pathlib import Path

if getattr(sys, "frozen", False):  # PyInstaller exe
    BASE = Path(sys.executable).resolve().parent
else:
    BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from src import packer, pipeline

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def load_config():
    return json.loads((BASE / "config.json").read_text(encoding="utf-8"))


def collect_image_args(args):
    files = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            files += sorted(x for x in p.iterdir() if x.suffix.lower() in IMG_EXT)
        elif p.is_file() and p.suffix.lower() in IMG_EXT:
            files.append(p)
    return files


def run(argv):
    flags = {a for a in argv if a.startswith("--")}
    args = [a for a in argv if not a.startswith("--")]
    mock = "--mock" in flags
    auto_open = "--no-open" not in flags
    cfg = load_config()

    if not args:
        print(__doc__)
        return 1

    if args[0].startswith("http"):
        result = pipeline.build_from_url(args[0], cfg, BASE, mock=mock)
    else:
        files = collect_image_args(args)
        if not files:
            print("[!] 이미지 파일을 찾지 못했습니다.")
            return 1
        result = pipeline.build_from_images(files, cfg, BASE, mock=mock)

    pack = result["pack"]
    if packer.copy_to_clipboard(result["caption"]):
        print("      캡션이 클립보드에 복사됐습니다")
    print(f"\n✅ 완성팩: {pack}")
    print("   썸네일 후보 중 하나 첫 장 → 01.jpg~ 순서로 올리고 캡션 붙여넣기")
    if auto_open:
        import os
        os.startfile(pack / "review.html")
    return 0


def main():
    argv = sys.argv[1:]
    interactive = not argv
    if interactive:
        print("=" * 46)
        print("  짤공장 — 커뮤니티 글 → 인스타 완성팩")
        print("=" * 46)
        print("게시물 주소(디시/루리웹/에펨 등)를 붙여넣고 Enter.")
        target = input("\nURL> ").strip().strip('"')
        if not target:
            return 1
        argv = [target]
    try:
        code = run(argv)
    except Exception as e:
        if "--debug" in argv:
            raise
        print(f"\n[오류] {e}")
        print("(자세한 내용이 필요하면 --debug 옵션으로 다시 실행)")
        code = 1
    if (interactive or getattr(sys, "frozen", False)) and sys.stdin and sys.stdin.isatty():
        try:
            input("\nEnter 키를 누르면 창이 닫힙니다...")
        except EOFError:
            pass
    return code


if __name__ == "__main__":
    sys.exit(main())
