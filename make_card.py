# -*- coding: utf-8 -*-
"""카드뉴스 공장 CLI — 주제 하나로 인스타 카드뉴스 캐러셀 + 전자책 PDF 완성팩 생성.

사용법:
  python make_card.py "쇼츠 조회수 터지는 후킹 문장" [--items 60] [--keyword 후킹]
                      [--teaser 8] [--mock] [--no-open]
"""
import argparse
import json
import sys
import webbrowser
from pathlib import Path

# CP949 콘솔에서 특수문자 출력 깨짐 방지
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from cardnews import pipeline  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="카드뉴스 + 전자책 완성팩 생성")
    ap.add_argument("topic", help="카드뉴스 주제 (예: '쇼츠 조회수 터지는 후킹 문장')")
    ap.add_argument("--items", type=int, default=None, help="전자책 총 아이템 수 (기본 60)")
    ap.add_argument("--keyword", default=None, help="댓글 트리거 키워드 (기본: AI가 선정)")
    ap.add_argument("--teaser", type=int, default=None, help="캐러셀 공개 아이템 수 (기본 8)")
    ap.add_argument("--theme", choices=["hunter", "cream"], default=None,
                    help="카드 테마 (hunter=유튜브 네온 다크, cream=크림 클래식)")
    ap.add_argument("--mock", action="store_true", help="Gemini 없이 모의 실행")
    ap.add_argument("--no-open", action="store_true", help="완성 후 브라우저 열지 않음")
    args = ap.parse_args()

    cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    if args.theme:
        cfg["card_theme"] = args.theme
    result = pipeline.build_cardnews(
        args.topic, cfg, BASE, n_items=args.items, keyword=args.keyword,
        teaser_count=args.teaser, mock=args.mock)

    pack = result["pack"]
    print(f"\n✅ 완성: {pack}")
    print(f"   카드 {len(result['cards'])}장 + 전자책 {result['ebook_pages']}p"
          f" + 캡션 (댓글 키워드: {result['meta']['keyword']})")
    if not args.no_open:
        webbrowser.open(str(pack / "review.html"))


if __name__ == "__main__":
    main()
