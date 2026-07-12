# -*- coding: utf-8 -*-
"""카드뉴스 렌더러 — 인스타 4:5 (2160x2700) 카드 3종: 표지 / 아이템 / CTA.
라이트 크림 배경 + 테라코타 강조 + 픽셀 타이틀 (레퍼런스 카드뉴스 스타일)."""
import re
import sys
import threading
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

if getattr(sys, "frozen", False):
    ASSETS = Path(sys.executable).resolve().parent / "assets" / "fonts"
else:
    ASSETS = Path(__file__).resolve().parent.parent / "assets" / "fonts"

W, H = 2160, 2700  # 인스타 4:5 (1080x1350의 2배 해상도)
M = 120            # 좌우 여백

INK = (28, 25, 22)
SUB = (108, 98, 90)
CREAM_TOP = (253, 248, 241)
CREAM_BOT = (247, 236, 225)
CARD_BG = (255, 255, 255)
CARD_BORDER = (240, 223, 208)

FONTS = {
    "pixel": ASSETS / "neodgm.ttf",
    "black": ASSETS / "BlackHanSans.ttf",
    "xbold": ASSETS / "Pretendard-ExtraBold.otf",
    "semi": ASSETS / "Pretendard-SemiBold.otf",
    "reg": ASSETS / "Pretendard-Regular.otf",
}
_FALLBACK = Path("C:/Windows/Fonts/malgunbd.ttf")

# ---- 다국어(일본어) 렌더 지원 -------------------------------------------------
# 워커 스레드별 언어 (동시 렌더 안전). "ja"면 일본어 폰트 + KO 크롬 문구 자동 번역.
_LOCAL = threading.local()

# 일본어 폰트 (Windows 내장 Yu Gothic). 한글 글리프는 없지만 본문이 일본어라 무방.
_JP_FONTS = {
    "black": Path("C:/Windows/Fonts/YuGothB.ttc"),  # 굵게 (제목/강조)
    "xbold": Path("C:/Windows/Fonts/YuGothB.ttc"),
    "pixel": Path("C:/Windows/Fonts/YuGothB.ttc"),
    "semi":  Path("C:/Windows/Fonts/YuGothM.ttc"),  # 중간
    "reg":   Path("C:/Windows/Fonts/YuGothR.ttc"),  # 본문
}
_JP_FALLBACK = Path("C:/Windows/Fonts/msgothic.ttc")


def _lang():
    return getattr(_LOCAL, "lang", "ko")


def _font(kind, size):
    if _lang() == "ja":
        jp = _JP_FONTS.get(kind, _JP_FONTS["black"])
        if not jp.exists():
            jp = _JP_FALLBACK
        if jp.exists():
            try:
                return ImageFont.truetype(str(jp), size, index=0)
            except Exception:
                pass
    p = FONTS.get(kind, _FALLBACK)
    if not p.exists():
        p = FONTS["black"] if FONTS["black"].exists() else _FALLBACK
    return ImageFont.truetype(str(p), size)


# KO 크롬(라벨·버튼·CTA 등) → JA 변환 규칙. 이미 일본어인 본문/제목은 매치 안 돼 그대로 통과.
_JA_RULES = [
    (re.compile(r"^지금 저장하고 하나씩 따라하기$"), "今すぐ保存して一つずつ実践"),
    (re.compile(r"^저장하고 하나씩 따라하기$"), "保存して一つずつ実践"),
    (re.compile(r"^지금 저장 \+ 댓글$"), "保存＋コメント"),
    (re.compile(r"^댓글 입력$"), "コメントを入力"),
    (re.compile(r"^댓글 추가\.\.\.$"), "コメントを追加..."),
    (re.compile(r"^전송$"), "送信"),
    (re.compile(r"^댓글$"), "コメント"),
    (re.compile(r"^DM으로 자동 전송$"), "DMで自動送信"),
    (re.compile(r"^팔로우하면 다음 자료도 놓치지 않아요$"), "フォローで次の資料も見逃さない"),
    (re.compile(r"^저장 \+ 팔로우하면 다음 자료도 놓치지 않아요$"), "保存＋フォローで次も見逃さない"),
    (re.compile(r"^복붙용 PDF로 깔끔하게 정리했어요\.$"), "コピペ用PDFにまとめました。"),
    (re.compile(r"^캐러셀엔 대표만 공개했어요\. 전체 (\d+)개를$"), r"カルーセルは一部だけ公開。全\1個を"),
    (re.compile(r"^캐러셀엔 대표 몇 개만 공개했어요\.$"), "カルーセルは一部だけ公開。"),
    (re.compile(r"^전체 정리본은 DM으로 바로 보내드(?:려요|립니다)\.$"), "まとめはDMですぐお送りします。"),
    (re.compile(r"^카드뉴스$"), "カードニュース"),
    (re.compile(r"^체크리스트$"), "チェックリスト"),
    (re.compile(r"^핵심 정리$"), "重要ポイント"),
    (re.compile(r"^전체 (\d+)개는 마지막 장에서 받아가세요$"), r"全\1個は最後のページで"),
    (re.compile(r"^전체 (\d+)개 PDF는 마지막 장에서 →$"), r"全\1個のPDFは最後のページで →"),
    (re.compile(r"^전체 (\d+)개 PDF 드려요$"), r"全\1個のPDFをお渡し"),
    (re.compile(r"^전체 (\d+)개 드려요$"), r"全\1個お渡し"),
    (re.compile(r"^전체 (\d+)개$"), r"全\1個"),
    (re.compile(r"^(\d+)개 전부$"), r"\1個すべて"),
    (re.compile(r"^ 드려요$"), "お渡しします"),
    (re.compile(r"^저장 ↗  (.*)$"), r"保存 ↗  \1"),
    (re.compile(r"^댓글에 '(.+)'$"), r"コメントに「\1」"),
    (re.compile(r"^쇼츠 수익화 · 매일 실전 꿀팁$"), "ショート収益化・毎日実践Tips"),
    (re.compile(r"^실화 스토리$"), "実話ストーリー"),
    (re.compile(r"^밀어서 스토리 보기 →$"), "スワイプでストーリーを →"),
    (re.compile(r"^이 스토리가 도움됐다면$"), "このストーリーが役立ったら"),
    (re.compile(r"^이 스토리에서 배운 실전 교훈을 PDF로 정리했어요\.$"),
     "このストーリーの学びをPDFにまとめました。"),
    (re.compile(r"^댓글 남기면 DM으로 바로 보내드립니다\.$"), "コメントすればDMですぐお送りします。"),
    (re.compile(r"^팔로우하면 다음 스토리도 놓치지 않아요$"), "フォローで次のストーリーも見逃さない"),
    (re.compile(r"^AI 트렌드 리포트$"), "AIトレンドレポート"),
    (re.compile(r"^AI 인사이트$"), "AIインサイト"),
    (re.compile(r"^AI 가이드$"), "AIガイド"),
    (re.compile(r"^실제 자료 캡처$"), "実際の資料キャプチャ"),
    (re.compile(r"^실제 수익 화면$"), "実際の収益画面"),
    (re.compile(r"^실제 수강생 후기$"), "実際の受講生レビュー"),
    (re.compile(r"^실제 기록$"), "実際の記録"),
    (re.compile(r"^수익지표$"), "収益指標"),
    (re.compile(r"^수강생후기$"), "受講生レビュー"),
]


def _t(s):
    """lang=ja일 때만 KO 크롬 → JA. 규칙에 없는(=이미 일본어인 본문) 문자열은 그대로."""
    if not isinstance(s, str) or not s or _lang() != "ja":
        return s
    for rx, rep in _JA_RULES:
        if rx.search(s):
            return rx.sub(rep, s)
    return s


# PIL draw.text / textlength 를 감싸 그릴 때 자동 번역 — 호출부 수정 없이 전 테마 커버.
# lang!="ja"면 _t가 즉시 원문 반환 → 한국어 렌더는 영향 없음.
_ORIG_TEXT = ImageDraw.ImageDraw.text
_ORIG_TLEN = ImageDraw.ImageDraw.textlength


def _draw_text(self, xy, text="", *a, **k):
    return _ORIG_TEXT(self, xy, _t(text), *a, **k)


def _draw_tlen(self, text, *a, **k):
    return _ORIG_TLEN(self, _t(text), *a, **k)


ImageDraw.ImageDraw.text = _draw_text
ImageDraw.ImageDraw.textlength = _draw_tlen


def _accent(cfg):
    hexs = (cfg.get("card_accent") or "#E2683C").lstrip("#")
    return tuple(int(hexs[i:i + 2], 16) for i in (0, 2, 4))


def _tint(accent, alpha, base=(255, 255, 255)):
    """accent를 base 위에 alpha(0~1)로 얹은 색"""
    return tuple(int(b + (a - b) * alpha) for a, b in zip(accent, base))


def _title_kind(cfg):
    k = cfg.get("card_title_font", "black")
    if k == "pixel":
        return "pixel" if FONTS["pixel"].exists() else "black"
    return k if k in FONTS else "black"


def _tw(draw, text, font):
    return draw.textlength(text, font=font)


def _wrap(draw, text, font, max_w):
    """한국어 단어 우선 줄바꿈 (넘치면 글자 단위 분해)"""
    lines, cur = [], ""
    for word in text.split(" "):
        trial = (cur + " " + word).strip()
        if _tw(draw, trial, font) <= max_w:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        while _tw(draw, word, font) > max_w and len(word) > 1:
            cut = 1
            for i in range(1, len(word) + 1):
                if _tw(draw, word[:i], font) > max_w:
                    break
                cut = i
            lines.append(word[:cut])
            word = word[cut:]
        cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def _fit_font(draw, text, kind, size, max_w, min_size=60):
    while size > min_size and _tw(draw, text, _font(kind, size)) > max_w:
        size -= 6
    return _font(kind, size), size


def _bg():
    """세로 크림 그라데이션 캔버스"""
    grad = Image.new("RGB", (1, H))
    for y in range(H):
        t = y / H
        grad.putpixel((0, y), tuple(int(a + (b - a) * t)
                                    for a, b in zip(CREAM_TOP, CREAM_BOT)))
    return grad.resize((W, H))


def _shadow_box(canvas, xy, radius, fill, border=None, width=3):
    """부드러운 그림자 + 흰 라운드 박스"""
    x0, y0, x1, y1 = xy
    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle(
        [x0 + 4, y0 + 14, x1 + 4, y1 + 18], radius=radius, fill=(120, 90, 60, 46))
    sh = sh.filter(ImageFilter.GaussianBlur(18))
    canvas.alpha_composite(sh)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(xy, radius=radius, fill=fill,
                           outline=border or CARD_BORDER, width=width)


def _asterisk(canvas, cx, cy, r, color, spokes=11, lw=None):
    """레퍼런스풍 방사형 애스터리스크 장식"""
    import math
    draw = ImageDraw.Draw(canvas)
    lw = lw or max(8, int(r * 0.22))
    for i in range(spokes):
        a = math.pi * 2 * i / spokes - math.pi / 2
        x0 = cx + math.cos(a) * r * 0.30
        y0 = cy + math.sin(a) * r * 0.30
        x1 = cx + math.cos(a) * r
        y1 = cy + math.sin(a) * r
        draw.line([x0, y0, x1, y1], fill=color, width=lw)
        for (px, py) in ((x0, y0), (x1, y1)):
            draw.ellipse([px - lw / 2, py - lw / 2, px + lw / 2, py + lw / 2], fill=color)


def _watermark(canvas, cfg):
    wm = cfg.get("card_watermark") or ""
    if not wm:
        return
    draw = ImageDraw.Draw(canvas)
    f = _font("semi", 46)
    draw.text(((W - _tw(draw, wm, f)) / 2, H - 96), wm, font=f, fill=(168, 152, 138))


def _brand_mark(canvas, cfg, cx, cy, h, alpha=1.0, idx=0):
    """라이트 테마 브랜드 마크 — 라인 캥거루 로고 (옅은 워터마크 스타일).
    config card_logo_marks 리스트에서 idx 순환 선택, alpha로 농도 조절."""
    marks = cfg.get("card_logo_marks") or []
    if marks:
        path = marks[idx % len(marks)]
    else:
        path = cfg.get("card_logo_light") or str(ASSETS.parent / "logo_card.png")
    try:
        img = _asset_img(path, int(h))
    except Exception:
        return
    if alpha < 1.0:
        img = img.copy()
        img.putalpha(img.getchannel("A").point(lambda a: int(a * alpha)))
    canvas.alpha_composite(img, (int(cx - img.width / 2), int(cy - h / 2)))


def _title_block(canvas, cfg, plan, y, big):
    """표지/아이템 카드 공용 타이틀 블록. 반환: 블록 아래 y"""
    draw = ImageDraw.Draw(canvas)
    accent = _accent(cfg)
    kind = _title_kind(cfg)
    size = 236 if big else 158
    max_w = W - M * 2
    f1, s1 = _fit_font(draw, plan["title_top"], kind, size, max_w)
    f2, s2 = _fit_font(draw, plan["title_main"], kind, size, max_w)
    s = min(s1, s2)
    f = _font(kind, s)
    line_h = int(s * 1.14)
    draw.text((M, y), plan["title_top"], font=f, fill=INK)
    draw.text((M, y + line_h), plan["title_main"], font=f, fill=accent)
    y2 = y + line_h * 2 + (56 if big else 34)
    fs = _font("semi", 76 if big else 58)
    draw.text((M, y2), plan["subtitle"], font=fs, fill=SUB)
    return y2 + int((76 if big else 58) * 1.4)


# ---------------------------------------------------------------- 표지 (크림)

def _cream_cover(plan, cfg, out_path):
    canvas = _bg().convert("RGBA")
    accent = _accent(cfg)
    _brand_mark(canvas, cfg, W - 230, 265, 260, alpha=0.42, idx=2)
    draw = ImageDraw.Draw(canvas)

    # 상단 라벨 필
    label = cfg.get("card_label", "") or "지금 저장하고 하나씩 따라하기"
    lf = _font("semi", 54)
    lw_ = _tw(draw, label, lf)
    lx, ly = M, 430
    draw.rounded_rectangle([lx, ly, lx + lw_ + 72, ly + 108], radius=54,
                           fill=_tint(accent, 0.14), outline=_tint(accent, 0.5), width=3)
    draw.text((lx + 36, ly + 24), label, font=lf, fill=_tint(accent, 0.9, base=INK))

    y_end = _title_block(canvas, cfg, plan, 640, big=True)

    _cov = cfg.get("cover_image")
    if _cov and Path(str(_cov)).exists() and _news_image_band(
            canvas, cfg, M, y_end + 80, W - M, H - 300):
        draw = ImageDraw.Draw(canvas)
        n = plan.get("n_items", 0)
        foot = f"전체 {n}개는 마지막 장에서 받아가세요"
        ff = _font("semi", 56)
        draw.text(((W - _tw(draw, foot, ff)) / 2, H - 210), foot, font=ff, fill=SUB)
        _watermark(canvas, cfg)
        canvas.convert("RGB").save(out_path, "JPEG", quality=92)
        return str(out_path)

    # 대표 아이템 미리보기 3줄 (티저의 앞 3개 제목)
    preview = plan.get("preview_titles") or []
    if preview:
        py = y_end + 120
        box_top = py - 64
        rows_f = _font("semi", 62)
        num_f = _font("xbold", 62)
        box_h = 64 * 2 + len(preview[:3]) * 118 - 26
        _shadow_box(canvas, [M, box_top, W - M, box_top + box_h], 44, CARD_BG)
        draw = ImageDraw.Draw(canvas)
        for i, t in enumerate(preview[:3]):
            ny = py + i * 118 - 20
            draw.text((M + 64, ny), f"{i + 1:02d}", font=num_f, fill=accent)
            tt = t
            if _tw(draw, tt, rows_f) > W - M * 2 - 128 - 110:
                while _tw(draw, tt + "…", rows_f) > W - M * 2 - 128 - 110 and len(tt) > 2:
                    tt = tt[:-1]
                tt += "…"
            draw.text((M + 64 + 110, ny), tt, font=rows_f, fill=INK)
        box_bottom = box_top + box_h

        # 카테고리 칩 (구성 미리보기)
        chips = [f"{c['name']} {len(c['items'])}" for c in plan.get("categories", [])][:6]
        if chips:
            cf = _font("semi", 52)
            cy = box_bottom + 100
            cx = M
            for chip in chips:
                cw = _tw(draw, chip, cf) + 72
                if cx + cw > W - M:
                    cx = M
                    cy += 128
                draw.rounded_rectangle([cx, cy, cx + cw, cy + 104], radius=52,
                                       fill=(255, 255, 255), outline=CARD_BORDER, width=3)
                draw.text((cx + 36, cy + 22), chip, font=cf, fill=SUB)
                cx += cw + 28

    # 하단 안내
    n = plan.get("n_items", 0)
    foot = f"전체 {n}개는 마지막 장에서 받아가세요"
    ff = _font("semi", 56)
    draw.text(((W - _tw(draw, foot, ff)) / 2, H - 210), foot, font=ff, fill=SUB)
    _watermark(canvas, cfg)
    canvas.convert("RGB").save(out_path, "JPEG", quality=92)
    return str(out_path)


# ---------------------------------------------------------------- 아이템 카드

def _measure_item(draw, item, body_size, inner_w):
    body_f = _font("reg", body_size)
    tag_f = _font("xbold", body_size)
    line_h = int(body_size * 1.5)
    rows_total, per_line_rows = 0, []
    for ln in item["lines"]:
        tag_w = _tw(draw, f"[{ln['tag']}]", tag_f) + 20
        rows = _wrap(draw, ln["text"], body_f, inner_w - tag_w)
        per_line_rows.append((tag_w, rows))
        rows_total += len(rows)
    inner_h = 88 + rows_total * line_h + (len(item["lines"]) - 1) * 22
    box_h = 56 + 104 + 24 + inner_h + 56
    return box_h, per_line_rows, line_h


def _draw_item_box(canvas, cfg, item, y, body_size, per_line_rows, line_h, box_h):
    accent = _accent(cfg)
    _shadow_box(canvas, [M, y, W - M, y + box_h], 44, CARD_BG)
    draw = ImageDraw.Draw(canvas)
    # 번호 + 제목
    num_f = _font("xbold", 92)
    title_f = _font("xbold", 88)
    tx = M + 72
    draw.text((tx, y + 56), f"{item['num']:02d}", font=num_f, fill=accent)
    draw.text((tx + _tw(draw, f"{item['num']:02d}", num_f) + 40, y + 58),
              item["title"], font=title_f, fill=INK)
    # 태그 라인 박스
    iy = y + 56 + 104 + 24
    inner = [M + 60, iy, W - M - 60, iy + box_h - (56 + 104 + 24) - 56]
    draw.rounded_rectangle(inner, radius=32, fill=_tint(accent, 0.085))
    body_f = _font("reg", body_size)
    tag_f = _font("xbold", body_size)
    ly = iy + 44
    for ln, (tag_w, rows) in zip(item["lines"], per_line_rows):
        draw.text((inner[0] + 44, ly), f"[{ln['tag']}]", font=tag_f,
                  fill=_tint(accent, 0.95, base=INK))
        for row in rows:
            draw.text((inner[0] + 44 + tag_w, ly), row, font=body_f, fill=(52, 46, 42))
            ly += line_h
        ly += 22


def _cream_items_card(plan, items, cfg, out_path):
    """카드 1장에 아이템 2~3개 (내용량에 따라 폰트 자동 축소)"""
    canvas = _bg().convert("RGBA")
    _brand_mark(canvas, cfg, W - 205, 205, 200,
                alpha=0.38, idx=(items[0].get("num", 0) if items else 0))
    draw = ImageDraw.Draw(canvas)
    y0 = _title_block(canvas, cfg, plan, 250, big=False) + 60

    for body_size in (58, 54, 50, 46, 42):
        measured = [_measure_item(draw, it, body_size, W - M * 2 - 120 - 88) for it in items]
        total = sum(m[0] for m in measured) + (len(items) - 1) * 52
        if y0 + total <= H - 150:
            break
    y = y0
    for it, (box_h, rows, line_h) in zip(items, measured):
        _draw_item_box(canvas, cfg, it, y, body_size, rows, line_h, box_h)
        y += box_h + 52
    _watermark(canvas, cfg)
    canvas.convert("RGB").save(out_path, "JPEG", quality=92)
    return str(out_path)


# ---------------------------------------------------------------- CTA (댓글 유도)

def _cream_cta(plan, cfg, out_path):
    canvas = _bg().convert("RGBA")
    accent = _accent(cfg)
    draw = ImageDraw.Draw(canvas)
    kind = _title_kind(cfg)
    n = plan.get("n_items", 0)
    keyword = plan["comment_keyword"]

    # 상단 라벨 + 빅타이틀
    label = "지금 저장 + 댓글"
    lf = _font(kind, 74)
    draw.text(((W - _tw(draw, label, lf)) / 2, 330), label, font=lf, fill=accent)

    line1 = re.sub(r"\s*\d+\s*(?:개|가지|선|종)?$", "", plan["title_main"]).strip() or plan["title_main"]
    line2a, line2b = f"{n}개 전부", " 드려요"
    f_big, s = _fit_font(draw, max(line1, line2a + line2b, key=len), kind, 210, W - M * 2)
    line_h = int(s * 1.2)
    draw.text(((W - _tw(draw, line1, f_big)) / 2, 480), line1, font=f_big, fill=INK)
    w2 = _tw(draw, line2a + line2b, f_big)
    x2 = (W - w2) / 2
    draw.text((x2, 480 + line_h), line2a, font=f_big, fill=accent)
    draw.text((x2 + _tw(draw, line2a, f_big), 480 + line_h), line2b, font=f_big, fill=INK)

    # 설명 2줄
    desc1 = f"캐러셀엔 대표만 공개했어요. 전체 {n}개를"
    desc2 = "복붙용 PDF로 깔끔하게 정리했어요."
    df = _font("reg", 62)
    dy = 480 + line_h * 2 + 90
    for t in (desc1, desc2):
        draw.text(((W - _tw(draw, t, df)) / 2, dy), t, font=df, fill=SUB)
        dy += 92

    # 댓글 입력 목업
    cy = dy + 90
    _shadow_box(canvas, [M, cy, W - M, cy + 260], 40, CARD_BG)
    draw = ImageDraw.Draw(canvas)
    ph_f = _font("reg", 44)
    draw.text((M + 70, cy + 44), "댓글 입력", font=ph_f, fill=(178, 165, 152))
    kw_f = _font("xbold", 96)
    draw.text((M + 70, cy + 110), keyword, font=kw_f, fill=INK)
    # 전송 버튼
    btn_f = _font("xbold", 60)
    bw = _tw(draw, "전송", btn_f) + 96
    draw.rounded_rectangle([W - M - 70 - bw, cy + 74, W - M - 70, cy + 186],
                           radius=28, fill=accent)
    draw.text((W - M - 70 - bw + 48, cy + 96), "전송", font=btn_f, fill=(255, 255, 255))

    # 화살표
    ay = cy + 260 + 70
    draw.polygon([(W / 2 - 44, ay), (W / 2 + 44, ay), (W / 2, ay + 60)], fill=accent)

    # DM 박스
    dmy = ay + 130
    draw.rounded_rectangle([M, dmy, W - M, dmy + 300], radius=40,
                           fill=_tint(accent, 0.05), outline=_tint(accent, 0.65), width=5)
    t1 = "DM으로 자동 전송"
    t1f = _font("xbold", 84)
    draw.text(((W - _tw(draw, t1, t1f)) / 2, dmy + 62), t1, font=t1f, fill=INK)
    t2 = f"「{plan['ebook_title']}」 PDF"
    t2f = _font("semi", 62)
    draw.text(((W - _tw(draw, t2, t2f)) / 2, dmy + 178), t2, font=t2f,
              fill=_tint(accent, 0.95, base=INK))

    # 하단 장식 + 팔로우 유도
    _brand_mark(canvas, cfg, W / 2, dmy + 300 + 170, 210, alpha=0.5, idx=1)
    foot = "팔로우하면 다음 자료도 놓치지 않아요"
    ff = _font("semi", 54)
    draw = ImageDraw.Draw(canvas)
    draw.text(((W - _tw(draw, foot, ff)) / 2, H - 230), foot, font=ff, fill=SUB)
    _watermark(canvas, cfg)
    canvas.convert("RGB").save(out_path, "JPEG", quality=92)
    return str(out_path)


# ================================================================ 헌터(유튜브 네온 다크) 테마
import random as _random

HBG_TOP, HBG_BOT = (13, 13, 20), (23, 17, 33)
HCARD, HBORDER = (26, 26, 38), (46, 46, 66)
HTEXT, HSUB = (242, 242, 247), (152, 152, 174)
HRED = (255, 46, 66)
NEONS = [((232, 75, 255), (122, 82, 255)),   # magenta -> purple
         ((57, 208, 255), (64, 112, 255)),   # cyan -> blue
         ((255, 99, 62), (255, 176, 62)),    # red -> orange
         ((82, 232, 152), (44, 202, 222))]   # green -> teal
GRAD_A, GRAD_B = (255, 70, 130), (100, 180, 255)  # 타이틀 강조 그라데이션
EMOJI_POOL = ["🔥", "🎯", "⚡", "💰", "📈", "🎬", "✂️", "🧠", "📌", "🚀", "👀", "💡"]
EMOJI_FONT = Path("C:/Windows/Fonts/seguiemj.ttf")

_IMG_CACHE = {}


def _asset_img(path, max_h):
    key = (str(path), max_h)
    if key not in _IMG_CACHE:
        img = Image.open(path).convert("RGBA")
        box = img.getbbox()
        if box:
            img = img.crop(box)
        scale = max_h / img.height
        img = img.resize((max(1, int(img.width * scale)), max_h), Image.LANCZOS)
        _IMG_CACHE[key] = img
    return _IMG_CACHE[key]


def _hunter_logo(cfg, h):
    path = cfg.get("card_logo") or str(ASSETS.parent / "logo_hunter.png")
    try:
        return _asset_img(path, h)
    except Exception:
        return None


def _emoji(canvas, xy, char, size):
    if not char:
        return
    try:
        f = ImageFont.truetype(str(EMOJI_FONT), size)
        ImageDraw.Draw(canvas).text(xy, char, font=f, embedded_color=True)
    except Exception:
        pass


def _item_emoji(item):
    e = (item.get("emoji") or "").strip()
    return e if e else EMOJI_POOL[item.get("num", 0) % len(EMOJI_POOL)]


def _hbg():
    """다크 그라데이션 + 네온 글로우 + 그리드"""
    grad = Image.new("RGB", (1, H))
    for y in range(H):
        t = y / H
        grad.putpixel((0, y), tuple(int(a + (b - a) * t)
                                    for a, b in zip(HBG_TOP, HBG_BOT)))
    canvas = grad.resize((W, H)).convert("RGBA")
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([-450, -400, 700, 500], fill=(150, 60, 255, 52))
    gd.ellipse([W - 650, H - 620, W + 420, H + 380], fill=(40, 170, 255, 44))
    gd.ellipse([W - 500, -350, W + 350, 350], fill=(255, 60, 90, 34))
    canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(160)))
    grid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(grid)
    for x in range(0, W, 120):
        d.line([x, 0, x, H], fill=(255, 255, 255, 5), width=1)
    for y in range(0, H, 120):
        d.line([0, y, W, y], fill=(255, 255, 255, 5), width=1)
    canvas.alpha_composite(grid)
    return canvas


def _grad_text(canvas, pos, text, font, c1=GRAD_A, c2=GRAD_B):
    mask = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(mask).text(pos, text, font=font, fill=255)
    box = mask.getbbox()
    if not box:
        return
    grad = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    x0, x1 = box[0], box[2]
    for x in range(x0, x1 + 1):
        t = (x - x0) / max(1, x1 - x0)
        col = tuple(int(a + (b - a) * t) for a, b in zip(c1, c2)) + (255,)
        gd.line([x, box[1], x, box[3]], fill=col)
    canvas.paste(grad, (0, 0), mask)


def _round_grad(canvas, xy, radius, c1, c2):
    """세로 그라데이션 라운드 박스 (썸네일 배경)"""
    x0, y0, x1, y1 = [int(v) for v in xy]
    w, h = x1 - x0, y1 - y0
    grad = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(1, h)
        grad.putpixel((0, y), tuple(int(a + (b - a) * t) for a, b in zip(c1, c2)))
    grad = grad.resize((w, h)).convert("RGBA")
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    canvas.paste(grad, (x0, y0), mask)


def _hunter_channel_bar(canvas, cfg, y, logo_h=120, show_sub=True):
    draw = ImageDraw.Draw(canvas)
    x = M
    logo = _hunter_logo(cfg, logo_h)
    if logo:
        canvas.alpha_composite(logo, (x, y))
        x += logo.width + 34
    handle = cfg.get("card_handle") or cfg.get("card_watermark") or "@channel"
    hf = _font("xbold", int(logo_h * 0.42))
    draw.text((x, y + int(logo_h * 0.08)), handle, font=hf, fill=HTEXT)
    sub_label = cfg.get("card_channel_sub", "쇼츠 수익화 · 매일 실전 꿀팁")
    sf = _font("reg", int(logo_h * 0.30))
    draw.text((x, y + int(logo_h * 0.56)), sub_label, font=sf, fill=HSUB)
    # 구독 버튼은 촌스러워서 제거 (2026-07-07 사용자 피드백) — show_sub 인자는 호환용
    return y + logo_h


def _hunter_watermark(canvas, cfg):
    wm = cfg.get("card_watermark") or ""
    if not wm:
        return
    draw = ImageDraw.Draw(canvas)
    f = _font("semi", 44)
    draw.text(((W - _tw(draw, wm, f)) / 2, H - 92), wm, font=f, fill=(110, 110, 132))


def _fit_two_lines(draw, l1, l2, kind, size, max_w, min_size=90):
    f1, s1 = _fit_font(draw, l1, kind, size, max_w, min_size)
    f2, s2 = _fit_font(draw, l2, kind, size, max_w, min_size)
    s = min(s1, s2)
    return _font(kind, s), s


def _hunter_cover(plan, cfg, out_path):
    """표지 v2 — 짤공장식: 큰 프로필 이미지 + 그 위에 문구 (목업·통계 제거)"""
    canvas = _hbg()

    photo = cfg.get("cover_image") or cfg.get("card_cover_photo") or cfg.get("card_profile_photo") or ""
    img = None
    if photo and Path(photo).exists():
        try:
            img = Image.open(photo).convert("RGBA")
            box = img.getbbox()
            if box:
                img = img.crop(box)
            ph = 1560
            img = img.resize((int(img.width * ph / img.height), ph), Image.LANCZOS)
        except Exception:
            img = None
    if img is not None:
        glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse(
            [W - img.width - 260, H - 1560 - 160, W + 260, H + 260],
            fill=(140, 60, 255, 66))
        canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(170)))
        canvas.alpha_composite(img, (W - img.width + 70, H - 1560 + 20))
        fade = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        fd = ImageDraw.Draw(fade)
        for i in range(260):
            fd.line([0, H - 260 + i, W, H - 260 + i],
                    fill=(13, 13, 20, int(i / 260 * 200)))
        canvas.alpha_composite(fade)

    draw = ImageDraw.Draw(canvas)
    _hunter_channel_bar(canvas, cfg, 130, logo_h=140)
    draw = ImageDraw.Draw(canvas)

    label = cfg.get("card_label", "저장하고 하나씩 따라하기")
    chip_f = _font("xbold", 48)
    cw = _tw(draw, label, chip_f) + 80
    y = 430
    draw.rounded_rectangle([M, y, M + cw, y + 92], radius=46,
                           fill=(58, 20, 28), outline=HRED, width=3)
    draw.text((M + 40, y + 18), label, font=chip_f, fill=(255, 170, 180))
    y += 190

    f, s = _fit_two_lines(draw, plan["title_top"], plan["title_main"],
                          "black", 210, W - M * 2, min_size=110)
    line_h = int(s * 1.14)
    draw.text((M, y), plan["title_top"], font=f, fill=HTEXT)
    _grad_text(canvas, (M, y + line_h), plan["title_main"], f)
    draw = ImageDraw.Draw(canvas)
    y += line_h * 2 + 66

    sf = _font("semi", 62)
    for row in _wrap(draw, plan.get("subtitle", ""), sf, 1060)[:2]:
        draw.text((M, y), row, font=sf, fill=HSUB)
        y += 90

    foot = f"전체 {plan.get('n_items', 0)}개 PDF는 마지막 장에서 →"
    ff = _font("xbold", 56)
    draw.text((M, H - 220), foot, font=ff, fill=(255, 210, 90))
    _hunter_watermark(canvas, cfg)
    canvas.convert("RGB").save(out_path, "JPEG", quality=92)
    return str(out_path)


def _measure_hunter_item(draw, item, body_size, text_w):
    """클린 v2 측정 — 번호+제목 한 줄, 본문 태그 라인"""
    title_f = _font("xbold", 82)
    rows_t = _wrap(draw, item["title"], title_f, text_w - 180)[:2]
    body_f = _font("reg", body_size)
    tag_f = _font("xbold", body_size)
    line_h = int(body_size * 1.55)
    per, rows_total = [], 0
    for ln in item["lines"]:
        tag_w = _tw(draw, f"[{ln['tag']}]", tag_f) + 20
        rows = _wrap(draw, ln["text"], body_f, text_w - tag_w)
        per.append((tag_w, rows))
        rows_total += len(rows)
    text_h = (len(rows_t) * 102 + 40 + rows_total * line_h
              + (len(item["lines"]) - 1) * 20)
    return text_h + 120, rows_t, per, line_h


def _hunter_items_card(plan, items, cfg, out_path):
    """아이템 카드 v2 — 가짜 썸네일·통계·이모지 제거, 번호+제목+본문만 크게"""
    canvas = _hbg()
    draw = ImageDraw.Draw(canvas)
    y0 = _hunter_channel_bar(canvas, cfg, 110, logo_h=104) + 70

    title_one = f"{plan['title_top']} {plan['title_main']}"
    f, s = _fit_font(draw, title_one, "black", 96, W - M * 2, min_size=58)
    draw.text((M, y0), title_one, font=f, fill=HTEXT)
    y0 += int(s * 1.2) + 70

    text_w = W - M * 2 - 128
    for body_size in (58, 54, 50, 46, 42):
        measured = [_measure_hunter_item(draw, it, body_size, text_w)
                    for it in items]
        total = sum(m[0] for m in measured) + (len(items) - 1) * 64
        if y0 + total <= H - 170:
            break

    y = y0
    for it, (box_h, rows_t, per, line_h) in zip(items, measured):
        pair = NEONS[it["num"] % len(NEONS)]
        _shadow_box(canvas, [M, y, W - M, y + box_h], 40, HCARD,
                    border=HBORDER, width=3)
        num = f"{it['num']:02d}"
        num_f = _font("black", 100)
        _grad_text(canvas, (M + 64, y + 52), num, num_f, pair[0], pair[1])
        draw = ImageDraw.Draw(canvas)
        title_f = _font("xbold", 82)
        ly = y + 58
        for row in rows_t:
            draw.text((M + 64 + 180, ly), row, font=title_f, fill=HTEXT)
            ly += 102
        ly += 40
        body_f = _font("reg", body_size)
        tag_f = _font("xbold", body_size)
        for ln, (tag_w, rows) in zip(it["lines"], per):
            draw.text((M + 64, ly), f"[{ln['tag']}]", font=tag_f, fill=pair[0])
            for row in rows:
                draw.text((M + 64 + tag_w, ly), row, font=body_f,
                          fill=(216, 216, 230))
                ly += line_h
            ly += 20
        y += box_h + 64
    _hunter_watermark(canvas, cfg)
    canvas.convert("RGB").save(out_path, "JPEG", quality=92)
    return str(out_path)


def _hunter_cta(plan, cfg, out_path):
    """CTA v2 — 스토리 CTA와 같은 톤 (고정댓글 목업·DM 박스 제거)"""
    canvas = _hbg()
    draw = ImageDraw.Draw(canvas)
    _hunter_channel_bar(canvas, cfg, 130, logo_h=140)
    kw = plan["comment_keyword"]
    n = plan.get("n_items", 0)

    y = 470
    l1, l2 = f"댓글에 '{kw}'", f"전체 {n}개 PDF 드려요"
    f, s = _fit_two_lines(draw, l1, l2, "black", 190, W - M * 2, min_size=100)
    line_h = int(s * 1.18)
    draw.text(((W - _tw(draw, l1, f)) / 2, y), l1, font=f, fill=HTEXT)
    _grad_text(canvas, (int((W - _tw(draw, l2, f)) / 2), y + line_h), l2, f)
    draw = ImageDraw.Draw(canvas)
    y += line_h * 2 + 70
    for t in ("캐러셀엔 대표 몇 개만 공개했어요.",
              "전체 정리본은 DM으로 바로 보내드려요."):
        df = _font("reg", 58)
        draw.text(((W - _tw(draw, t, df)) / 2, y), t, font=df, fill=HSUB)
        y += 88
    y += 90

    ih = 230
    _shadow_box(canvas, [M, y, W - M, y + ih], 36, (20, 20, 30),
                border=HBORDER, width=3)
    draw = ImageDraw.Draw(canvas)
    draw.text((M + 60, y + 40), "댓글 추가...", font=_font("reg", 42),
              fill=(120, 120, 142))
    draw.text((M + 60, y + 104), kw, font=_font("xbold", 88), fill=HTEXT)
    btn_f = _font("xbold", 56)
    bw = _tw(draw, "댓글", btn_f) + 92
    draw.rounded_rectangle([W - M - 60 - bw, y + 66, W - M - 60, y + 170],
                           radius=28, fill=HRED)
    draw.text((W - M - 60 - bw + 46, y + 88), "댓글", font=btn_f,
              fill=(255, 255, 255))
    y += ih + 90

    t2 = f"「{plan['ebook_title']}」"
    t2f = _font("semi", 60)
    draw.text(((W - _tw(draw, t2, t2f)) / 2, y), t2, font=t2f,
              fill=(255, 210, 90))
    y += 110
    foot = "저장 + 팔로우하면 다음 자료도 놓치지 않아요"
    ff = _font("semi", 54)
    draw.text(((W - _tw(draw, foot, ff)) / 2, y), foot, font=ff, fill=HSUB)
    _hunter_watermark(canvas, cfg)
    canvas.convert("RGB").save(out_path, "JPEG", quality=92)
    return str(out_path)


# ================================================================ 증빙(스크린샷) 카드

def _fit_image(path, max_w, max_h):
    img = Image.open(path).convert("RGB")
    # 작은 캡처(카톡 후기 등)는 확대해서 채우되 3배 한도 (화질 보호)
    scale = min(max_w / img.width, max_h / img.height, 3.0)
    return img.resize((max(1, int(img.width * scale)),
                       max(1, int(img.height * scale))), Image.LANCZOS)


def _paste_shot(canvas, path, box, radius=36, border=(255, 255, 255), dark=False):
    """스크린샷을 영역 안에 비율 유지로 배치 (그림자+라운드+테두리). 반환: 하단 y"""
    x0, y0, x1, y1 = [int(v) for v in box]
    img = _fit_image(path, x1 - x0, y1 - y0)
    px = x0 + (x1 - x0 - img.width) // 2
    py = y0 + (y1 - y0 - img.height) // 2
    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle(
        [px + 6, py + 18, px + img.width + 6, py + img.height + 22],
        radius=radius, fill=(0, 0, 0, 130) if dark else (120, 90, 60, 60))
    canvas.alpha_composite(sh.filter(ImageFilter.GaussianBlur(22)))
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.width - 1, img.height - 1],
                                           radius=radius, fill=255)
    canvas.paste(img, (px, py), mask)
    ImageDraw.Draw(canvas).rounded_rectangle(
        [px, py, px + img.width, py + img.height], radius=radius,
        outline=border, width=5)
    return py + img.height


_PROOF_LABEL = {"수익지표": "실제 수익 화면", "수강생후기": "실제 수강생 후기",
                "스토리": "실제 기록", "기타": "실제 자료 캡처"}


def _hunter_proof_card(plan, proof, cfg, out_path):
    canvas = _hbg()
    draw = ImageDraw.Draw(canvas)
    y = _hunter_channel_bar(canvas, cfg, 130, logo_h=120, show_sub=False) + 64
    draw = ImageDraw.Draw(canvas)

    label = _PROOF_LABEL.get(proof.get("kind", ""), "실제 자료 캡처")
    chip_f = _font("xbold", 46)
    cw = _tw(draw, label, chip_f) + 76
    draw.rounded_rectangle([M, y, M + cw, y + 88], radius=44,
                           fill=(58, 20, 28), outline=HRED, width=3)
    draw.text((M + 38, y + 16), label, font=chip_f, fill=(255, 170, 180))
    y += 150

    hook = proof.get("hook", "") or proof.get("headline", "")
    hf, hs = _fit_font(draw, hook, "black", 130, W - M * 2, min_size=80)
    rows = _wrap(draw, hook, hf, W - M * 2)[:2]
    for row in rows:
        draw.text((M, y), row, font=hf, fill=HTEXT)
        y += int(hs * 1.16)
    y += 40

    bottom = H - 400
    _paste_shot(canvas, proof["file"], [M, y, W - M, bottom],
                border=(88, 88, 118), dark=True)

    sub = proof.get("sub", "")
    if sub:
        sf = _font("semi", 56)
        draw = ImageDraw.Draw(canvas)
        for i, row in enumerate(_wrap(draw, sub, sf, W - M * 2)[:2]):
            draw.text(((W - _tw(draw, row, sf)) / 2, H - 330 + i * 78),
                      row, font=sf, fill=(214, 214, 228))
    _hunter_watermark(canvas, cfg)
    canvas.convert("RGB").save(out_path, "JPEG", quality=92)
    return str(out_path)


def _cream_proof_card(plan, proof, cfg, out_path):
    canvas = _bg().convert("RGBA")
    accent = _accent(cfg)
    draw = ImageDraw.Draw(canvas)
    _brand_mark(canvas, cfg, W - 205, 205, 200, alpha=0.38, idx=3)
    draw = ImageDraw.Draw(canvas)

    label = _PROOF_LABEL.get(proof.get("kind", ""), "실제 자료 캡처")
    chip_f = _font("xbold", 46)
    cw = _tw(draw, label, chip_f) + 76
    draw.rounded_rectangle([M, 190, M + cw, 278], radius=44,
                           fill=_tint(accent, 0.14), outline=_tint(accent, 0.55),
                           width=3)
    draw.text((M + 38, 206), label, font=chip_f,
              fill=_tint(accent, 0.95, base=INK))
    y = 350

    hook = proof.get("hook", "") or proof.get("headline", "")
    kind = _title_kind(cfg)
    hf, hs = _fit_font(draw, hook, kind, 128, W - M * 2, min_size=80)
    for row in _wrap(draw, hook, hf, W - M * 2)[:2]:
        draw.text((M, y), row, font=hf, fill=INK)
        y += int(hs * 1.18)
    y += 40

    bottom = H - 400
    _paste_shot(canvas, proof["file"], [M, y, W - M, bottom],
                border=CARD_BORDER)

    sub = proof.get("sub", "")
    if sub:
        sf = _font("semi", 56)
        draw = ImageDraw.Draw(canvas)
        for i, row in enumerate(_wrap(draw, sub, sf, W - M * 2)[:2]):
            draw.text(((W - _tw(draw, row, sf)) / 2, H - 330 + i * 78),
                      row, font=sf, fill=SUB)
    _watermark(canvas, cfg)
    canvas.convert("RGB").save(out_path, "JPEG", quality=92)
    return str(out_path)


# ================================================================ 스토리 카드

def _story_dots(canvas, cur, total, dark=True):
    """하단 진행 점 (표지=0, 장면=1..N, CTA=N+1)"""
    draw = ImageDraw.Draw(canvas)
    n = total + 2
    gap, r = 52, 13
    x0 = W / 2 - (n - 1) * gap / 2
    for i in range(n):
        on = i == cur
        color = (HRED if dark else (226, 104, 60)) if on else \
                ((70, 70, 92) if dark else (216, 200, 186))
        rr = r + 3 if on else r
        draw.ellipse([x0 + i * gap - rr, H - 170 - rr,
                      x0 + i * gap + rr, H - 170 + rr], fill=color)


def _hunter_story_cover(plan, cfg, out_path):
    canvas = _hbg()
    draw = ImageDraw.Draw(canvas)
    _hunter_channel_bar(canvas, cfg, 130, logo_h=150)
    draw = ImageDraw.Draw(canvas)

    label = "실화 스토리"
    chip_f = _font("xbold", 48)
    cw = _tw(draw, label, chip_f) + 80
    y = 460
    draw.rounded_rectangle([M, y, M + cw, y + 92], radius=46,
                           fill=(58, 20, 28), outline=HRED, width=3)
    draw.text((M + 40, y + 18), label, font=chip_f, fill=(255, 170, 180))
    y += 190

    l1, l2 = plan["title_top"], plan["title_main"]
    f, s = _fit_two_lines(draw, l1, l2, "black", 200, W - M * 2, min_size=110)
    line_h = int(s * 1.14)
    draw.text((M, y), l1, font=f, fill=HTEXT)
    _grad_text(canvas, (M, y + line_h), l2, f)
    draw = ImageDraw.Draw(canvas)
    y += line_h * 2 + 70

    sf = _font("semi", 64)
    for row in _wrap(draw, plan.get("subtitle", ""), sf, W - M * 2)[:2]:
        draw.text((M, y), row, font=sf, fill=HSUB)
        y += 92

    hint = "밀어서 스토리 보기 →"
    hf = _font("semi", 52)
    draw.text(((W - _tw(draw, hint, hf)) / 2, H - 300), hint, font=hf, fill=HSUB)
    _story_dots(canvas, 0, len(plan.get("scenes", [])), dark=True)
    _hunter_watermark(canvas, cfg)
    canvas.convert("RGB").save(out_path, "JPEG", quality=92)
    return str(out_path)


def _hunter_story_scene(plan, scene, total, cfg, out_path):
    canvas = _hbg()
    draw = ImageDraw.Draw(canvas)
    _hunter_channel_bar(canvas, cfg, 130, logo_h=110, show_sub=False)
    draw = ImageDraw.Draw(canvas)

    label = f"{scene['no']:02d} · {scene.get('label', '장면')}"
    chip_f = _font("xbold", 48)
    cw = _tw(draw, label, chip_f) + 80
    y = 400
    draw.rounded_rectangle([M, y, M + cw, y + 92], radius=46,
                           fill=(58, 20, 28), outline=HRED, width=3)
    draw.text((M + 40, y + 18), label, font=chip_f, fill=(255, 170, 180))
    y += 200

    hf, hs = _fit_font(draw, scene["heading"], "black", 150, W - M * 2, min_size=96)
    for row in _wrap(draw, scene["heading"], hf, W - M * 2)[:2]:
        draw.text((M, y), row, font=hf, fill=HTEXT)
        y += int(hs * 1.16)
    y += 70

    bf = _font("reg", 66)
    for line in scene.get("lines", [])[:3]:
        for row in _wrap(draw, line, bf, W - M * 2):
            draw.text((M, y), row, font=bf, fill=(216, 216, 230))
            y += 98
        y += 26

    quote = scene.get("quote", "")
    if quote:
        y = max(y + 40, H - 780)
        qf = _font("semi", 62)
        rows = _wrap(draw, f"“{quote}”", qf, W - M * 2 - 90)[:2]
        qh = len(rows) * 92 + 76
        draw.rounded_rectangle([M, y, M + 14, y + qh], radius=7, fill=HRED)
        for i, row in enumerate(rows):
            draw.text((M + 64, y + 40 + i * 92), row, font=qf, fill=(255, 214, 220))
    _story_dots(canvas, scene["no"], total, dark=True)
    _hunter_watermark(canvas, cfg)
    canvas.convert("RGB").save(out_path, "JPEG", quality=92)
    return str(out_path)


def _hunter_story_cta(plan, cfg, out_path):
    canvas = _hbg()
    draw = ImageDraw.Draw(canvas)
    _hunter_channel_bar(canvas, cfg, 130, logo_h=140, show_sub=False)
    kw = plan["comment_keyword"]

    y = 430
    l1 = plan.get("cta_line") or "이 스토리가 도움됐다면"
    f1, s1 = _fit_font(draw, l1, "black", 130, W - M * 2, min_size=84)
    draw.text(((W - _tw(draw, l1, f1)) / 2, y), l1, font=f1, fill=HTEXT)
    y += int(s1 * 1.3)
    l2 = f"댓글에 '{kw}'"
    f2, s2 = _fit_font(draw, l2, "black", 190, W - M * 2, min_size=110)
    _grad_text(canvas, (int((W - _tw(draw, l2, f2)) / 2), y), l2, f2)
    draw = ImageDraw.Draw(canvas)
    y += int(s2 * 1.25) + 40

    for t in ("이 스토리에서 배운 실전 교훈을 PDF로 정리했어요.",
              "댓글 남기면 DM으로 바로 보내드립니다."):
        df = _font("reg", 58)
        draw.text(((W - _tw(draw, t, df)) / 2, y), t, font=df, fill=HSUB)
        y += 88
    y += 70

    ih = 220
    _shadow_box(canvas, [M, y, W - M, y + ih], 36, (20, 20, 30),
                border=HBORDER, width=3)
    draw = ImageDraw.Draw(canvas)
    draw.text((M + 60, y + 36), "댓글 추가...", font=_font("reg", 40),
              fill=(120, 120, 142))
    draw.text((M + 60, y + 96), kw, font=_font("xbold", 84), fill=HTEXT)
    btn_f = _font("xbold", 54)
    bw = _tw(draw, "댓글", btn_f) + 88
    draw.rounded_rectangle([W - M - 60 - bw, y + 64, W - M - 60, y + 164],
                           radius=26, fill=HRED)
    draw.text((W - M - 60 - bw + 44, y + 84), "댓글", font=btn_f,
              fill=(255, 255, 255))
    y += ih + 80

    eb = plan.get("ebook_title", "")
    if eb:
        t2 = f"「{eb}」 PDF"
        t2f = _font("semi", 58)
        draw.text(((W - _tw(draw, t2, t2f)) / 2, y), t2, font=t2f,
                  fill=(255, 210, 90))
        y += 100
    foot = "팔로우하면 다음 스토리도 놓치지 않아요"
    ff = _font("semi", 54)
    draw.text(((W - _tw(draw, foot, ff)) / 2, y), foot, font=ff, fill=HSUB)
    _story_dots(canvas, len(plan.get("scenes", [])) + 1,
                len(plan.get("scenes", [])), dark=True)
    _hunter_watermark(canvas, cfg)
    canvas.convert("RGB").save(out_path, "JPEG", quality=92)
    return str(out_path)


def _cream_story_cover(plan, cfg, out_path):
    canvas = _bg().convert("RGBA")
    accent = _accent(cfg)
    draw = ImageDraw.Draw(canvas)
    _brand_mark(canvas, cfg, W - 230, 265, 260, alpha=0.42, idx=2)
    draw = ImageDraw.Draw(canvas)

    label = "실화 스토리"
    chip_f = _font("xbold", 48)
    cw = _tw(draw, label, chip_f) + 80
    y = 430
    draw.rounded_rectangle([M, y, M + cw, y + 92], radius=46,
                           fill=_tint(accent, 0.14), outline=_tint(accent, 0.55),
                           width=3)
    draw.text((M + 40, y + 18), label, font=chip_f,
              fill=_tint(accent, 0.95, base=INK))
    y += 190

    kind = _title_kind(cfg)
    l1, l2 = plan["title_top"], plan["title_main"]
    f, s = _fit_two_lines(draw, l1, l2, kind, 190, W - M * 2, min_size=104)
    line_h = int(s * 1.16)
    draw.text((M, y), l1, font=f, fill=INK)
    draw.text((M, y + line_h), l2, font=f, fill=accent)
    y += line_h * 2 + 70

    sf = _font("semi", 62)
    for row in _wrap(draw, plan.get("subtitle", ""), sf, W - M * 2)[:2]:
        draw.text((M, y), row, font=sf, fill=SUB)
        y += 90

    hint = "밀어서 스토리 보기 →"
    hf = _font("semi", 52)
    draw.text(((W - _tw(draw, hint, hf)) / 2, H - 300), hint, font=hf, fill=SUB)
    _story_dots(canvas, 0, len(plan.get("scenes", [])), dark=False)
    _watermark(canvas, cfg)
    canvas.convert("RGB").save(out_path, "JPEG", quality=92)
    return str(out_path)


def _cream_story_scene(plan, scene, total, cfg, out_path):
    canvas = _bg().convert("RGBA")
    accent = _accent(cfg)
    draw = ImageDraw.Draw(canvas)
    _brand_mark(canvas, cfg, W - 205, 205, 200, alpha=0.38, idx=scene["no"])
    draw = ImageDraw.Draw(canvas)

    label = f"{scene['no']:02d} · {scene.get('label', '장면')}"
    chip_f = _font("xbold", 48)
    cw = _tw(draw, label, chip_f) + 80
    y = 380
    draw.rounded_rectangle([M, y, M + cw, y + 92], radius=46,
                           fill=_tint(accent, 0.14), outline=_tint(accent, 0.55),
                           width=3)
    draw.text((M + 40, y + 18), label, font=chip_f,
              fill=_tint(accent, 0.95, base=INK))
    y += 200

    kind = _title_kind(cfg)
    hf, hs = _fit_font(draw, scene["heading"], kind, 145, W - M * 2, min_size=92)
    for row in _wrap(draw, scene["heading"], hf, W - M * 2)[:2]:
        draw.text((M, y), row, font=hf, fill=INK)
        y += int(hs * 1.18)
    y += 70

    bf = _font("reg", 64)
    for line in scene.get("lines", [])[:3]:
        for row in _wrap(draw, line, bf, W - M * 2):
            draw.text((M, y), row, font=bf, fill=(58, 52, 46))
            y += 96
        y += 26

    quote = scene.get("quote", "")
    if quote:
        y = max(y + 40, H - 780)
        qf = _font("semi", 60)
        rows = _wrap(draw, f"“{quote}”", qf, W - M * 2 - 90)[:2]
        qh = len(rows) * 90 + 76
        draw.rounded_rectangle([M, y, M + 14, y + qh], radius=7, fill=accent)
        for i, row in enumerate(rows):
            draw.text((M + 64, y + 40 + i * 90), row, font=qf,
                      fill=_tint(accent, 0.9, base=INK))
    _story_dots(canvas, scene["no"], total, dark=False)
    _watermark(canvas, cfg)
    canvas.convert("RGB").save(out_path, "JPEG", quality=92)
    return str(out_path)


def _cream_story_cta(plan, cfg, out_path):
    canvas = _bg().convert("RGBA")
    accent = _accent(cfg)
    draw = ImageDraw.Draw(canvas)
    _brand_mark(canvas, cfg, W / 2, 300, 210, alpha=0.5, idx=1)
    draw = ImageDraw.Draw(canvas)
    kw = plan["comment_keyword"]
    kind = _title_kind(cfg)

    y = 520
    l1 = plan.get("cta_line") or "이 스토리가 도움됐다면"
    f1, s1 = _fit_font(draw, l1, kind, 120, W - M * 2, min_size=80)
    draw.text(((W - _tw(draw, l1, f1)) / 2, y), l1, font=f1, fill=INK)
    y += int(s1 * 1.3)
    l2 = f"댓글에 '{kw}'"
    f2, s2 = _fit_font(draw, l2, kind, 180, W - M * 2, min_size=104)
    draw.text(((W - _tw(draw, l2, f2)) / 2, y), l2, font=f2, fill=accent)
    y += int(s2 * 1.25) + 40

    for t in ("이 스토리에서 배운 실전 교훈을 PDF로 정리했어요.",
              "댓글 남기면 DM으로 바로 보내드립니다."):
        df = _font("reg", 56)
        draw.text(((W - _tw(draw, t, df)) / 2, y), t, font=df, fill=SUB)
        y += 84
    y += 70

    ih = 210
    _shadow_box(canvas, [M, y, W - M, y + ih], 32, CARD_BG)
    draw = ImageDraw.Draw(canvas)
    draw.text((M + 60, y + 34), "댓글 추가...", font=_font("reg", 40),
              fill=(178, 165, 152))
    draw.text((M + 60, y + 92), kw, font=_font("xbold", 82), fill=INK)
    btn_f = _font("xbold", 52)
    bw = _tw(draw, "댓글", btn_f) + 84
    draw.rounded_rectangle([W - M - 56 - bw, y + 58, W - M - 56, y + 156],
                           radius=26, fill=accent)
    draw.text((W - M - 56 - bw + 42, y + 78), "댓글", font=btn_f,
              fill=(255, 255, 255))
    y += ih + 76

    eb = plan.get("ebook_title", "")
    if eb:
        t2 = f"「{eb}」 PDF"
        t2f = _font("semi", 56)
        draw.text(((W - _tw(draw, t2, t2f)) / 2, y), t2, font=t2f,
                  fill=_tint(accent, 0.95, base=INK))
        y += 96
    foot = "팔로우하면 다음 스토리도 놓치지 않아요"
    ff = _font("semi", 52)
    draw.text(((W - _tw(draw, foot, ff)) / 2, y), foot, font=ff, fill=SUB)
    _story_dots(canvas, len(plan.get("scenes", [])) + 1,
                len(plan.get("scenes", [])), dark=False)
    _watermark(canvas, cfg)
    canvas.convert("RGB").save(out_path, "JPEG", quality=92)
    return str(out_path)


# ---------------------------------------------------------------- 테마 디스패처

# ==================== 뉴스 에디토리얼 테마 (highestlevel33 구조 참고) ====================
NEWS_BG = (250, 249, 247)
NEWS_INK = (24, 22, 26)
NEWS_BODY = (58, 52, 56)
NEWS_SUB = (120, 116, 124)
NEWS_LINE = (234, 228, 232)


def _news_handle(cfg):
    return (cfg.get("card_handle") or cfg.get("card_watermark") or "@").strip()


def _spaced(draw, text, font, x, y, fill, gap):
    text = _t(text)  # 글자별로 쪼개기 전에 전체 번역 (letter-spacing 크롬도 JA 반영)
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += _tw(draw, ch, font) + gap
    return x


def _spaced_w(draw, text, font, gap):
    text = _t(text)
    return sum(_tw(draw, ch, font) + gap for ch in text)


def _news_image_band(canvas, cfg, x0, y0, x1, y1, radius=44):
    src = cfg.get("cover_image")
    if not src or not Path(str(src)).exists():
        return False
    try:
        img = Image.open(src).convert("RGB")
    except Exception:
        return False
    bw, bh = x1 - x0, y1 - y0
    scale = max(bw / img.width, bh / img.height)
    img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                     Image.LANCZOS)
    ix, iy = (img.width - bw) // 2, (img.height - bh) // 3
    img = img.crop((ix, iy, ix + bw, iy + bh))
    mask = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, bw, bh], radius=radius, fill=255)
    canvas.paste(img, (x0, y0), mask)
    return True


def _news_cover(plan, cfg, out_path):
    accent = _accent(cfg)
    c = Image.new("RGB", (W, H), NEWS_BG)
    d = ImageDraw.Draw(c)
    eyebrow = (plan.get("title_top") or "AI 트렌드 리포트").strip()
    d.rectangle([M, 300, M + 96, 312], fill=accent)
    _spaced(d, eyebrow, _font("xbold", 46), M, 352, accent, 6)
    title = (plan.get("title_main") or plan.get("title") or "").strip()
    tf, ts = _fit_font(d, title, "xbold", 175, W - M * 2, min_size=96)
    lines = _wrap(d, title, tf, W - M * 2)[:3]
    lh = int(ts * 1.16)
    y = 470
    for i, ln in enumerate(lines):
        col = accent if (len(lines) > 1 and i == len(lines) - 1) else NEWS_INK
        d.text((M, y), ln, font=tf, fill=col)
        y += lh
    y += 24
    src = cfg.get("cover_image")
    has_img = bool(src and Path(str(src)).exists())
    if has_img and _news_image_band(c, cfg, M, y, W - M, H - 330):
        # 큰 이미지: 제목 아래부터 하단까지 꽉 채우고, 소제목은 이미지 밑 한 줄만
        y = H - 300
        sub = (plan.get("subtitle") or "").strip()
        if sub:
            sf = _font("semi", 56)
            rows = _wrap(d, sub, sf, W - M * 2)
            if rows:
                d.text((M, y), rows[0], font=sf, fill=NEWS_SUB)
    else:
        d.line([M, y, W - M, y], fill=NEWS_LINE, width=3)
        y += 52
        sub = (plan.get("subtitle") or "").strip()
        if sub:
            sf = _font("semi", 62)
            for ln in _wrap(d, sub, sf, W - M * 2)[:2]:
                d.text((M, y), ln, font=sf, fill=NEWS_SUB)
                y += 90
        prev = plan.get("preview_titles") or []
        if prev:
            y = max(y + 30, H - 560)
            nf, itf = _font("xbold", 64), _font("semi", 58)
            for i, t in enumerate(prev[:3]):
                if y > H - 250:
                    break
                d.text((M, y), f"{i + 1:02d}", font=nf, fill=accent)
                tt = t
                while _tw(d, tt, itf) > W - 2 * M - 125 and len(tt) > 2:
                    tt = tt[:-1]
                d.text((M + 125, y + 4), tt, font=itf, fill=NEWS_INK)
                y += 120
    d.text((M, H - 150), _news_handle(cfg), font=_font("semi", 52), fill=NEWS_SUB)
    ef, sw = _font("xbold", 48), "SWIPE →"
    _spaced(d, sw, ef, W - M - _spaced_w(d, sw, ef, 4), H - 152, accent, 4)
    c.save(out_path, "JPEG", quality=92)
    return str(out_path)


def _news_measure(draw, items, hsize, bsize):
    hlf, bf, lh = _font("xbold", hsize), _font("reg", bsize), int(bsize * 1.5)
    y = 430
    for it in items:
        if (it.get("category") or "").strip():
            y += 104
        y += len(_wrap(draw, it.get("title", ""), hlf, W - M * 2)) * int(hsize * 1.2) + 24
        for ln in it.get("lines", []):
            txt = (ln.get("text") or "").strip()
            if txt:
                y += len(_wrap(draw, txt, bf, W - M * 2)) * lh + 18
        y += 58
    return y


def _news_items_card(plan, items, cfg, out_path):
    accent = _accent(cfg)
    c = Image.new("RGB", (W, H), NEWS_BG)
    d = ImageDraw.Draw(c)
    num = items[0].get("num", 1) if items else 1
    tag, tf = f"#{num:02d}", _font("xbold", 46)
    d.rounded_rectangle([M, 250, M + _tw(d, tag, tf) + 64, 342], radius=20, fill=accent)
    d.text((M + 32, 270), tag, font=tf, fill=(255, 255, 255))
    hnd, hf = _news_handle(cfg), _font("semi", 44)
    d.text((W - M - _tw(d, hnd, hf), 274), hnd, font=hf, fill=(190, 186, 192))
    hsize, bsize = 128, 78
    for hs, bs in ((128, 78), (116, 72), (104, 66), (92, 60), (82, 54), (74, 50)):
        if _news_measure(d, items, hs, bs) <= H - 210:
            hsize, bsize = hs, bs
            break
        hsize, bsize = hs, bs
    y = 430
    hlf, bf, lh = _font("xbold", hsize), _font("reg", bsize), int(bsize * 1.5)
    for it in items:
        cat = (it.get("category") or "").strip()
        if cat:
            d.text((M, y), cat, font=_font("xbold", 42), fill=accent)
            d.rectangle([M, y + 64, M + 84, y + 72], fill=accent)
            y += 104
        for ln in _wrap(d, it.get("title", ""), hlf, W - M * 2):
            d.text((M, y), ln, font=hlf, fill=NEWS_INK)
            y += int(hsize * 1.2)
        y += 24
        for line in it.get("lines", []):
            txt = (line.get("text") or "").strip()
            if not txt:
                continue
            for row in _wrap(d, txt, bf, W - M * 2):
                d.text((M, y), row, font=bf, fill=NEWS_BODY)
                y += lh
            y += 18
        y += 58
    foot = f"저장 ↗  {hnd}"
    ff = _font("semi", 50)
    d.text((W - M - _tw(d, foot, ff), H - 150), foot, font=ff, fill=accent)
    c.save(out_path, "JPEG", quality=92)
    return str(out_path)


def _news_cta(plan, cfg, out_path):
    accent = _accent(cfg)
    c = Image.new("RGB", (W, H), NEWS_BG)
    d = ImageDraw.Draw(c)
    n, kw = plan.get("n_items", 0), plan.get("comment_keyword", "")
    d.rectangle([M, 440, M + 96, 452], fill=accent)
    _spaced(d, "지금 저장 + 댓글", _font("xbold", 46), M, 492, accent, 6)
    line1 = re.sub(r"\s*\d+\s*(?:개|가지|선|종)?$", "", plan.get("title_main", "")).strip() \
        or plan.get("title_main", "카드뉴스")
    tf, ts = _fit_font(d, line1, "xbold", 140, W - M * 2, min_size=88)
    y = 620
    for ln in _wrap(d, line1, tf, W - M * 2):
        d.text((M, y), ln, font=tf, fill=NEWS_INK)
        y += int(ts * 1.16)
    d.text((M, y + 16), f"전체 {n}개 드려요", font=tf, fill=accent)
    y += int(ts * 1.16) + 130
    bf = _font("semi", 60)
    d.rounded_rectangle([M, y, W - M, y + 210], radius=32, fill=(255, 255, 255),
                        outline=NEWS_LINE, width=3)
    d.text((M + 56, y + 40), "댓글 입력", font=_font("reg", 44), fill=NEWS_SUB)
    d.text((M + 56, y + 100), kw, font=_font("xbold", 76), fill=NEWS_INK)
    btw = _tw(d, "전송", bf) + 96
    d.rounded_rectangle([W - M - btw - 44, y + 52, W - M - 44, y + 158], radius=30, fill=accent)
    d.text((W - M - btw - 44 + 48, y + 68), "전송", font=bf, fill=(255, 255, 255))
    y += 210 + 90
    d.text((M, y), "▼", font=_font("xbold", 76), fill=accent)
    y += 130
    d.rounded_rectangle([M, y, W - M, y + 200], radius=28, fill=_tint(accent, 0.08),
                        outline=_tint(accent, 0.5), width=3)
    d.text((M + 56, y + 42), "DM으로 자동 전송", font=_font("xbold", 66), fill=NEWS_INK)
    d.text((M + 56, y + 126), f"「{plan.get('ebook_title') or kw}」 PDF",
           font=_font("semi", 52), fill=accent)
    d.text((M, H - 150), _news_handle(cfg), font=_font("semi", 52), fill=NEWS_SUB)
    c.save(out_path, "JPEG", quality=92)
    return str(out_path)


# ==================== 다크 강펀치 테마 (howtosuca 참고, 라임→골드) ====================
PUNCH_WHITE = (245, 245, 245)
PUNCH_DIM = (150, 150, 155)
PUNCH_CARD = (32, 32, 38)
PUNCH_BODY = (205, 205, 210)
PUNCH_DARK = (20, 20, 24)
PUNCH_GOLD = (255, 200, 61)


def _punch_bg():
    grad = Image.new("RGB", (1, H))
    for y in range(H):
        v = 20 + int(9 * (y / H))
        grad.putpixel((0, y), (v, v, v + 4))
    return grad.resize((W, H))


def _punch_pop(cfg):
    r, g, b = _accent(cfg)
    if (0.299 * r + 0.587 * g + 0.114 * b) >= 150:  # 밝은 프리셋/설정색이면 사용
        return (r, g, b)
    return PUNCH_GOLD


def _accent_line_idx(lines):
    for i, ln in enumerate(lines):
        if re.search(r"\d", ln):
            return i
    return (len(lines) - 1) if lines else 0


def _punch_cover(plan, cfg, out_path):
    pop = _punch_pop(cfg)
    c = _punch_bg()
    d = ImageDraw.Draw(c)
    tag = (plan.get("title_top") or "AI 인사이트").strip()
    tf = _font("xbold", 46)
    d.rounded_rectangle([M, 360, M + _tw(d, tag, tf) + 80, 456], radius=48, outline=pop, width=4)
    d.text((M + 40, 382), tag, font=tf, fill=pop)
    title = (plan.get("title_main") or plan.get("title") or "").strip()
    f_big, ts = _fit_font(d, title, "black", 190, W - M * 2, min_size=104)
    lines = _wrap(d, title, f_big, W - M * 2)[:4]
    ai = _accent_line_idx(lines)
    lh = int(ts * 1.16)
    y = 600
    for i, ln in enumerate(lines):
        d.text((M, y), ln, font=f_big, fill=(pop if i == ai else PUNCH_WHITE))
        y += lh
    y += 30
    src = cfg.get("cover_image")
    if src and Path(str(src)).exists() and _news_image_band(c, cfg, M, y, W - M, H - 300):
        y = H - 268
        sub = (plan.get("subtitle") or "").strip()
        if sub:
            sf = _font("semi", 54)
            rows = _wrap(d, sub, sf, W - M * 2)
            if rows:
                d.text((M, y), rows[0], font=sf, fill=PUNCH_DIM)
    else:
        y += 10
        sub = (plan.get("subtitle") or "").strip()
        if sub:
            sf = _font("semi", 60)
            for ln in _wrap(d, sub, sf, W - M * 2)[:2]:
                d.text((M, y), ln, font=sf, fill=PUNCH_DIM)
                y += 88
        prev = plan.get("preview_titles") or []
        if prev:
            y = max(y + 20, H - 560)
            for i, t in enumerate(prev[:3]):
                if y > H - 250:
                    break
                d.text((M, y), f"{i + 1:02d}", font=_font("black", 62), fill=pop)
                itf = _font("semi", 58)
                tt = t
                while _tw(d, tt, itf) > W - 2 * M - 130 and len(tt) > 2:
                    tt = tt[:-1]
                d.text((M + 130, y + 2), tt, font=itf, fill=PUNCH_WHITE)
                y += 118
    hf, h = _font("xbold", 56), _news_handle(cfg)
    d.text(((W - _tw(d, h, hf)) / 2, H - 170), h, font=hf, fill=PUNCH_WHITE)
    c.save(out_path, "JPEG", quality=92)
    return str(out_path)


def _punch_measure(draw, items, hsize, bsize):
    hlf, bf, lh = _font("black", hsize), _font("reg", bsize), int(bsize * 1.5)
    y = 430
    for it in items:
        if (it.get("category") or "").strip():
            y += 78
        y += len(_wrap(draw, it.get("title", ""), hlf, W - M * 2)) * int(hsize * 1.18) + 20
        for ln in it.get("lines", []):
            txt = (ln.get("text") or "").strip()
            if txt:
                y += len(_wrap(draw, txt, bf, W - M * 2)) * lh + 16
        y += 54
    return y


def _punch_items_card(plan, items, cfg, out_path):
    pop = _punch_pop(cfg)
    c = _punch_bg()
    d = ImageDraw.Draw(c)
    num = items[0].get("num", 1) if items else 1
    d.text((M, 210), f"{num:02d}", font=_font("black", 150), fill=pop)
    hnd, hf = _news_handle(cfg), _font("semi", 44)
    d.text((W - M - _tw(d, hnd, hf), 270), hnd, font=hf, fill=PUNCH_DIM)
    hsize, bsize = 122, 74
    for hs, bs in ((122, 74), (108, 68), (96, 62), (86, 56), (76, 50), (68, 46)):
        if _punch_measure(d, items, hs, bs) <= H - 200:
            hsize, bsize = hs, bs
            break
        hsize, bsize = hs, bs
    y = 430
    hlf, bf, lh = _font("black", hsize), _font("reg", bsize), int(bsize * 1.5)
    for it in items:
        cat = (it.get("category") or "").strip()
        if cat:
            d.text((M, y), cat, font=_font("xbold", 42), fill=pop)
            y += 78
        for ln in _wrap(d, it.get("title", ""), hlf, W - M * 2):
            d.text((M, y), ln, font=hlf, fill=PUNCH_WHITE)
            y += int(hsize * 1.18)
        y += 20
        for line in it.get("lines", []):
            txt = (line.get("text") or "").strip()
            if not txt:
                continue
            for row in _wrap(d, txt, bf, W - M * 2):
                d.text((M, y), row, font=bf, fill=PUNCH_BODY)
                y += lh
            y += 16
        y += 54
    foot, ff = f"저장 ↗  {hnd}", _font("semi", 50)
    d.text((W - M - _tw(d, foot, ff), H - 150), foot, font=ff, fill=pop)
    c.save(out_path, "JPEG", quality=92)
    return str(out_path)


def _punch_cta(plan, cfg, out_path):
    pop = _punch_pop(cfg)
    c = _punch_bg()
    d = ImageDraw.Draw(c)
    n, kw = plan.get("n_items", 0), plan.get("comment_keyword", "")
    lab, lf = "지금 저장 + 댓글", _font("xbold", 46)
    d.rounded_rectangle([M, 430, M + _tw(d, lab, lf) + 80, 526], radius=48, outline=pop, width=4)
    d.text((M + 40, 452), lab, font=lf, fill=pop)
    line1 = re.sub(r"\s*\d+\s*(?:개|가지|선|종)?$", "", plan.get("title_main", "")).strip() \
        or plan.get("title_main", "카드뉴스")
    f_big, ts = _fit_font(d, line1, "black", 150, W - M * 2, min_size=92)
    y = 640
    for ln in _wrap(d, line1, f_big, W - M * 2):
        d.text((M, y), ln, font=f_big, fill=PUNCH_WHITE)
        y += int(ts * 1.16)
    d.text((M, y + 12), f"전체 {n}개 드려요", font=f_big, fill=pop)
    y += int(ts * 1.16) + 120
    d.rounded_rectangle([M, y, W - M, y + 210], radius=32, fill=PUNCH_CARD)
    d.text((M + 56, y + 40), "댓글 입력", font=_font("reg", 44), fill=PUNCH_DIM)
    d.text((M + 56, y + 100), kw, font=_font("black", 76), fill=PUNCH_WHITE)
    bf = _font("xbold", 60)
    btw = _tw(d, "전송", bf) + 96
    d.rounded_rectangle([W - M - btw - 44, y + 52, W - M - 44, y + 158], radius=30, fill=pop)
    d.text((W - M - btw - 44 + 48, y + 66), "전송", font=bf, fill=PUNCH_DARK)
    y += 210 + 80
    d.text((M, y), "▼", font=_font("black", 72), fill=pop)
    y += 120
    d.rounded_rectangle([M, y, W - M, y + 200], radius=28, fill=PUNCH_CARD, outline=pop, width=3)
    d.text((M + 56, y + 42), "DM으로 자동 전송", font=_font("black", 64), fill=PUNCH_WHITE)
    d.text((M + 56, y + 126), f"「{plan.get('ebook_title') or kw}」 PDF",
           font=_font("semi", 52), fill=pop)
    hf, h = _font("xbold", 52), _news_handle(cfg)
    d.text(((W - _tw(d, h, hf)) / 2, H - 150), h, font=hf, fill=PUNCH_DIM)
    c.save(out_path, "JPEG", quality=92)
    return str(out_path)


# ==================== 인포그래픽 체크리스트 테마 (칠판 구조 → 디지털) ====================
INFO_BG = (244, 246, 249)
INFO_INK = (26, 30, 38)
INFO_SUB = (110, 120, 135)
INFO_CARD = (255, 255, 255)
INFO_LINE = (230, 234, 240)


def _check(d, cx, cy, r, color):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
    w = max(6, int(r * 0.24))
    d.line([cx - r * 0.42, cy + r * 0.02, cx - r * 0.08, cy + r * 0.38], fill=(255, 255, 255), width=w)
    d.line([cx - r * 0.08, cy + r * 0.38, cx + r * 0.5, cy - r * 0.36], fill=(255, 255, 255), width=w)


def _info_measure(draw, items):
    tf, df, lh = _font("xbold", 62), _font("reg", 50), 66
    y = 0
    for it in items:
        y += 72 * max(1, len(_wrap(draw, it.get("title", ""), tf, W - 2 * M - 250))) + 12
        for line in it.get("lines", []):
            txt = (line.get("text") or "").strip()
            if txt:
                y += len(_wrap(draw, txt, df, W - 2 * M - 260)) * lh + 8
        y += 44
    return y


def _info_cover(plan, cfg, out_path):
    accent = _accent(cfg)
    c = Image.new("RGB", (W, H), INFO_BG)
    d = ImageDraw.Draw(c)
    eyebrow = (plan.get("title_top") or "체크리스트").strip()
    ef = _font("xbold", 46)
    d.rounded_rectangle([M, 300, M + _tw(d, eyebrow, ef) + 72, 392], radius=22, fill=accent)
    d.text((M + 36, 320), eyebrow, font=ef, fill=(255, 255, 255))
    title = (plan.get("title_main") or plan.get("title") or "").strip()
    tf, ts = _fit_font(d, title, "black", 150, W - M * 2, min_size=96)
    y = 470
    for ln in _wrap(d, title, tf, W - M * 2)[:3]:
        d.text((M, y), ln, font=tf, fill=INFO_INK)
        y += int(ts * 1.16)
    y += 16
    sub = (plan.get("subtitle") or "").strip()
    if sub:
        sf = _font("semi", 58)
        for ln in _wrap(d, sub, sf, W - M * 2)[:2]:
            d.text((M, y), ln, font=sf, fill=INFO_SUB)
            y += 84
    y += 30
    _cov = cfg.get("cover_image")
    if _cov and Path(str(_cov)).exists() and _news_image_band(c, cfg, M, y, W - M, H - 320):
        pass
    else:
        prev = plan.get("preview_titles") or []
        if prev:
            box_h = 60 + len(prev[:5]) * 150
            d.rounded_rectangle([M, y, W - M, y + box_h], radius=36, fill=INFO_CARD,
                                outline=INFO_LINE, width=3)
            ry, rf = y + 60, _font("xbold", 60)
            for t in prev[:5]:
                _check(d, M + 80, ry + 34, 34, accent)
                tt = t
                while _tw(d, tt, rf) > W - 2 * M - 260 and len(tt) > 2:
                    tt = tt[:-1]
                d.text((M + 150, ry), tt, font=rf, fill=INFO_INK)
                ry += 150
    d.text((M, H - 160), _news_handle(cfg), font=_font("xbold", 52), fill=accent)
    c.save(out_path, "JPEG", quality=92)
    return str(out_path)


def _info_items_card(plan, items, cfg, out_path):
    accent = _accent(cfg)
    c = Image.new("RGB", (W, H), INFO_BG)
    d = ImageDraw.Draw(c)
    num = items[0].get("num", 1) if items else 1
    d.rounded_rectangle([M, 250, M + 150, 366], radius=24, fill=accent)
    d.text((M + 30, 262), f"{num:02d}", font=_font("black", 70), fill=(255, 255, 255))
    sec = (items[0].get("category") or plan.get("title_main") or "핵심 정리").strip()
    stf, _ = _fit_font(d, sec, "black", 78, W - 2 * M - 190, min_size=52)
    d.text((M + 190, 262), sec, font=stf, fill=INFO_INK)
    y = 430
    box_h = min(H - 240 - y, _info_measure(d, items) + 96)
    d.rounded_rectangle([M, y, W - M, y + box_h], radius=40, fill=INFO_CARD,
                        outline=INFO_LINE, width=3)
    iy = y + 56
    tf, df, lh = _font("xbold", 62), _font("reg", 50), 66
    for it in items:
        _check(d, M + 80, iy + 34, 34, accent)
        rows = _wrap(d, it.get("title", ""), tf, W - 2 * M - 250)
        for k, row in enumerate(rows):
            d.text((M + 150, iy + k * 72), row, font=tf, fill=INFO_INK)
        iy += 72 * max(1, len(rows)) + 12
        for line in it.get("lines", []):
            txt = (line.get("text") or "").strip()
            if not txt:
                continue
            d.ellipse([M + 158, iy + 22, M + 174, iy + 38], fill=INFO_SUB)
            for row in _wrap(d, txt, df, W - 2 * M - 260):
                d.text((M + 210, iy), row, font=df, fill=INFO_SUB)
                iy += lh
            iy += 8
        iy += 44
    d.text((M, H - 160), f"저장 ↗  {_news_handle(cfg)}", font=_font("semi", 50), fill=accent)
    c.save(out_path, "JPEG", quality=92)
    return str(out_path)


def _info_cta(plan, cfg, out_path):
    accent = _accent(cfg)
    c = Image.new("RGB", (W, H), INFO_BG)
    d = ImageDraw.Draw(c)
    n, kw = plan.get("n_items", 0), plan.get("comment_keyword", "")
    ef = _font("xbold", 46)
    d.rounded_rectangle([M, 440, M + _tw(d, "지금 저장 + 댓글", ef) + 72, 532], radius=22, fill=accent)
    d.text((M + 36, 460), "지금 저장 + 댓글", font=ef, fill=(255, 255, 255))
    line1 = re.sub(r"\s*\d+\s*(?:개|가지|선|종)?$", "", plan.get("title_main", "")).strip() \
        or plan.get("title_main", "카드뉴스")
    tf, ts = _fit_font(d, line1, "black", 140, W - M * 2, min_size=90)
    y = 640
    for ln in _wrap(d, line1, tf, W - M * 2):
        d.text((M, y), ln, font=tf, fill=INFO_INK)
        y += int(ts * 1.16)
    d.text((M, y + 12), f"전체 {n}개 드려요", font=tf, fill=accent)
    y += int(ts * 1.16) + 120
    d.rounded_rectangle([M, y, W - M, y + 210], radius=32, fill=INFO_CARD, outline=INFO_LINE, width=3)
    d.text((M + 56, y + 40), "댓글 입력", font=_font("reg", 44), fill=INFO_SUB)
    d.text((M + 56, y + 100), kw, font=_font("black", 76), fill=INFO_INK)
    bf = _font("xbold", 60)
    btw = _tw(d, "전송", bf) + 96
    d.rounded_rectangle([W - M - btw - 44, y + 52, W - M - 44, y + 158], radius=30, fill=accent)
    d.text((W - M - btw - 44 + 48, y + 66), "전송", font=bf, fill=(255, 255, 255))
    y += 210 + 80
    d.text((M, y), "▼", font=_font("black", 72), fill=accent)
    y += 120
    d.rounded_rectangle([M, y, W - M, y + 200], radius=28, fill=_tint(accent, 0.08),
                        outline=_tint(accent, 0.5), width=3)
    d.text((M + 56, y + 42), "DM으로 자동 전송", font=_font("black", 64), fill=INFO_INK)
    d.text((M + 56, y + 126), f"「{plan.get('ebook_title') or kw}」 PDF",
           font=_font("semi", 52), fill=accent)
    d.text((M, H - 150), _news_handle(cfg), font=_font("semi", 52), fill=INFO_SUB)
    c.save(out_path, "JPEG", quality=92)
    return str(out_path)


# ==================== 파스텔 소프트 테마 (prompt_what 참고) ====================
PASTEL_INK = (42, 46, 69)
PASTEL_SUB = (122, 128, 152)
PASTEL_CARD = (255, 255, 255)


def _pastel_bg():
    top, bot = (222, 232, 255), (255, 227, 240)
    grad = Image.new("RGB", (1, H))
    for y in range(H):
        t = y / H
        grad.putpixel((0, y), tuple(int(a + (b - a) * t) for a, b in zip(top, bot)))
    return grad.resize((W, H))


def _pastel_measure(draw, items, hsize, bsize):
    hlf, bf, lh = _font("black", hsize), _font("reg", bsize), int(bsize * 1.5)
    y = 0
    for it in items:
        y += 60  # 카드 상단 패딩
        y += len(_wrap(draw, it.get("title", ""), hlf, W - 2 * M - 120)) * int(hsize * 1.16) + 16
        for line in it.get("lines", []):
            txt = (line.get("text") or "").strip()
            if txt:
                y += len(_wrap(draw, txt, bf, W - 2 * M - 120)) * lh + 12
        y += 60 + 44  # 하단 패딩 + 카드 간격
    return y


def _pastel_cover(plan, cfg, out_path):
    accent = _accent(cfg)
    c = _pastel_bg()
    d = ImageDraw.Draw(c)
    eyebrow = (plan.get("title_top") or "AI 가이드").strip()
    ef = _font("xbold", 46)
    d.rounded_rectangle([M, 340, M + _tw(d, eyebrow, ef) + 76, 436], radius=48, fill=(255, 255, 255))
    d.text((M + 38, 362), eyebrow, font=ef, fill=accent)
    title = (plan.get("title_main") or plan.get("title") or "").strip()
    tf, ts = _fit_font(d, title, "black", 158, W - M * 2, min_size=98)
    lines = _wrap(d, title, tf, W - M * 2)[:3]
    ai = _accent_line_idx(lines)
    y = 500
    for i, ln in enumerate(lines):
        d.text((M, y), ln, font=tf, fill=(accent if (len(lines) > 1 and i == ai) else PASTEL_INK))
        y += int(ts * 1.16)
    y += 18
    sub = (plan.get("subtitle") or "").strip()
    if sub:
        sf = _font("semi", 58)
        for ln in _wrap(d, sub, sf, W - M * 2)[:2]:
            d.text((M, y), ln, font=sf, fill=PASTEL_SUB)
            y += 84
    y += 30
    _cov = cfg.get("cover_image")
    if _cov and Path(str(_cov)).exists() and _news_image_band(c, cfg, M, y, W - M, H - 320):
        pass
    else:
        prev = plan.get("preview_titles") or []
        if prev:
            box_h = 56 + len(prev[:4]) * 142
            d.rounded_rectangle([M, y, W - M, y + box_h], radius=44, fill=PASTEL_CARD)
            ry, rf = y + 54, _font("xbold", 58)
            for i, t in enumerate(prev[:4]):
                d.ellipse([M + 64, ry + 8, M + 128, ry + 72], fill=accent)
                nn = str(i + 1)
                d.text((M + 96 - _tw(d, nn, _font("xbold", 46)) / 2, ry + 18), nn,
                       font=_font("xbold", 46), fill=(255, 255, 255))
                tt = t
                while _tw(d, tt, rf) > W - 2 * M - 280 and len(tt) > 2:
                    tt = tt[:-1]
                d.text((M + 170, ry + 12), tt, font=rf, fill=PASTEL_INK)
                ry += 142
    h = _news_handle(cfg)
    d.text(((W - _tw(d, h, _font("xbold", 52))) / 2, H - 165), h, font=_font("xbold", 52), fill=accent)
    c.save(out_path, "JPEG", quality=92)
    return str(out_path)


def _pastel_items_card(plan, items, cfg, out_path):
    accent = _accent(cfg)
    c = _pastel_bg()
    d = ImageDraw.Draw(c)
    num = items[0].get("num", 1) if items else 1
    d.text((M, 220), f"{num:02d}", font=_font("black", 128), fill=accent)
    hnd = _news_handle(cfg)
    d.text((W - M - _tw(d, hnd, _font("semi", 44)), 275), hnd, font=_font("semi", 44), fill=PASTEL_SUB)
    hsize, bsize = 92, 58
    for hs, bs in ((92, 58), (84, 54), (76, 50), (68, 46)):
        if _pastel_measure(d, items, hs, bs) <= H - 470:
            hsize, bsize = hs, bs
            break
        hsize, bsize = hs, bs
    y = 420
    hlf, bf, lh = _font("black", hsize), _font("reg", bsize), int(bsize * 1.5)
    for it in items:
        # 카드 높이 계산
        ch = 60
        ch += len(_wrap(d, it.get("title", ""), hlf, W - 2 * M - 120)) * int(hsize * 1.16) + 16
        for line in it.get("lines", []):
            txt = (line.get("text") or "").strip()
            if txt:
                ch += len(_wrap(d, txt, bf, W - 2 * M - 120)) * lh + 12
        ch += 50
        d.rounded_rectangle([M, y, W - M, y + ch], radius=40, fill=PASTEL_CARD)
        iy = y + 46
        for row in _wrap(d, it.get("title", ""), hlf, W - 2 * M - 120):
            d.text((M + 60, iy), row, font=hlf, fill=PASTEL_INK)
            iy += int(hsize * 1.16)
        iy += 12
        for line in it.get("lines", []):
            txt = (line.get("text") or "").strip()
            if not txt:
                continue
            for row in _wrap(d, txt, bf, W - 2 * M - 120):
                d.text((M + 60, iy), row, font=bf, fill=PASTEL_SUB)
                iy += lh
            iy += 12
        y += ch + 44
    d.text((M, H - 150), f"저장 ↗  {hnd}", font=_font("semi", 50), fill=accent)
    c.save(out_path, "JPEG", quality=92)
    return str(out_path)


def _pastel_cta(plan, cfg, out_path):
    accent = _accent(cfg)
    c = _pastel_bg()
    d = ImageDraw.Draw(c)
    n, kw = plan.get("n_items", 0), plan.get("comment_keyword", "")
    ef = _font("xbold", 46)
    d.rounded_rectangle([M, 440, M + _tw(d, "지금 저장 + 댓글", ef) + 76, 536], radius=48, fill=(255, 255, 255))
    d.text((M + 38, 462), "지금 저장 + 댓글", font=ef, fill=accent)
    line1 = re.sub(r"\s*\d+\s*(?:개|가지|선|종)?$", "", plan.get("title_main", "")).strip() \
        or plan.get("title_main", "카드뉴스")
    tf, ts = _fit_font(d, line1, "black", 140, W - M * 2, min_size=90)
    y = 640
    for ln in _wrap(d, line1, tf, W - M * 2):
        d.text((M, y), ln, font=tf, fill=PASTEL_INK)
        y += int(ts * 1.16)
    d.text((M, y + 12), f"전체 {n}개 드려요", font=tf, fill=accent)
    y += int(ts * 1.16) + 120
    d.rounded_rectangle([M, y, W - M, y + 210], radius=36, fill=PASTEL_CARD)
    d.text((M + 56, y + 40), "댓글 입력", font=_font("reg", 44), fill=PASTEL_SUB)
    d.text((M + 56, y + 100), kw, font=_font("black", 76), fill=PASTEL_INK)
    bf = _font("xbold", 60)
    btw = _tw(d, "전송", bf) + 96
    d.rounded_rectangle([W - M - btw - 44, y + 52, W - M - 44, y + 158], radius=30, fill=accent)
    d.text((W - M - btw - 44 + 48, y + 66), "전송", font=bf, fill=(255, 255, 255))
    y += 210 + 80
    d.text((M, y), "▼", font=_font("black", 72), fill=accent)
    y += 120
    d.rounded_rectangle([M, y, W - M, y + 200], radius=32, fill=PASTEL_CARD)
    d.text((M + 56, y + 42), "DM으로 자동 전송", font=_font("black", 64), fill=PASTEL_INK)
    d.text((M + 56, y + 126), f"「{plan.get('ebook_title') or kw}」 PDF",
           font=_font("semi", 52), fill=accent)
    d.text((M, H - 150), _news_handle(cfg), font=_font("semi", 52), fill=PASTEL_SUB)
    c.save(out_path, "JPEG", quality=92)
    return str(out_path)


def _theme(cfg):
    return cfg.get("card_theme", "hunter")


def render_cover(plan, cfg, out_path):
    _LOCAL.lang = cfg.get("card_lang", "ko")
    try:
        t = _theme(cfg)
        if t == "hunter":
            return _hunter_cover(plan, cfg, out_path)
        if t == "news":
            return _news_cover(plan, cfg, out_path)
        if t == "punch":
            return _punch_cover(plan, cfg, out_path)
        if t == "info":
            return _info_cover(plan, cfg, out_path)
        if t == "pastel":
            return _pastel_cover(plan, cfg, out_path)
        return _cream_cover(plan, cfg, out_path)
    finally:
        _LOCAL.lang = "ko"


def render_items_card(plan, items, cfg, out_path):
    _LOCAL.lang = cfg.get("card_lang", "ko")
    try:
        t = _theme(cfg)
        if t == "hunter":
            return _hunter_items_card(plan, items, cfg, out_path)
        if t == "news":
            return _news_items_card(plan, items, cfg, out_path)
        if t == "punch":
            return _punch_items_card(plan, items, cfg, out_path)
        if t == "info":
            return _info_items_card(plan, items, cfg, out_path)
        if t == "pastel":
            return _pastel_items_card(plan, items, cfg, out_path)
        return _cream_items_card(plan, items, cfg, out_path)
    finally:
        _LOCAL.lang = "ko"


def render_cta(plan, cfg, out_path):
    _LOCAL.lang = cfg.get("card_lang", "ko")
    try:
        t = _theme(cfg)
        if t == "hunter":
            return _hunter_cta(plan, cfg, out_path)
        if t == "news":
            return _news_cta(plan, cfg, out_path)
        if t == "punch":
            return _punch_cta(plan, cfg, out_path)
        if t == "info":
            return _info_cta(plan, cfg, out_path)
        if t == "pastel":
            return _pastel_cta(plan, cfg, out_path)
        return _cream_cta(plan, cfg, out_path)
    finally:
        _LOCAL.lang = "ko"


def render_proof_card(plan, proof, cfg, out_path):
    if _theme(cfg) == "hunter":
        return _hunter_proof_card(plan, proof, cfg, out_path)
    return _cream_proof_card(plan, proof, cfg, out_path)


def render_photo_card(plan, photo_path, caption, cfg, out_path):
    """본문 사이에 끼우는 풀블리드 사진 카드 (테마 공통).
    사진을 카드 전체에 cover-fit + 하단 그라데이션 + 한국어 캡션 + 핸들."""
    canvas = Image.new("RGB", (W, H), (18, 18, 22))
    try:
        img = Image.open(photo_path).convert("RGB")
        scale = max(W / img.width, H / img.height)
        nw, nh = int(img.width * scale) + 1, int(img.height * scale) + 1
        img = img.resize((nw, nh), Image.LANCZOS)
        left, top = (nw - W) // 2, (nh - H) // 2
        canvas.paste(img.crop((left, top, left + W, top + H)), (0, 0))
    except Exception:
        pass
    canvas = canvas.convert("RGBA")
    grad = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for i in range(820):   # 하단 어둡게 (캡션 가독)
        gd.line([0, H - 820 + i, W, H - 820 + i], fill=(10, 10, 16, int(i / 820 * 205)))
    canvas.alpha_composite(grad)
    draw = ImageDraw.Draw(canvas)
    accent = _accent(cfg)

    # 상단 강조 바
    draw.rectangle([M, 210, M + 96, 224], fill=accent)

    caption = (caption or "").strip()
    if caption:
        kind = _title_kind(cfg)
        cf, cs = _fit_font(draw, caption, kind, 128, W - M * 2, min_size=78)
        rows = _wrap(draw, caption, cf, W - M * 2)[:3]
        yy = H - 300 - (len(rows) - 1) * int(cs * 1.16)
        for row in rows:
            draw.text((M, yy), row, font=cf, fill=(255, 255, 255))
            yy += int(cs * 1.16)

    handle = (cfg.get("card_handle") or cfg.get("card_watermark") or "").strip()
    if handle:
        hf = _font("xbold", 48)
        draw.text((M, H - 168), handle, font=hf, fill=(240, 240, 245))
    canvas.convert("RGB").save(out_path, "JPEG", quality=92)
    return str(out_path)


def render_story_cover(plan, cfg, out_path):
    if _theme(cfg) == "hunter":
        return _hunter_story_cover(plan, cfg, out_path)
    return _cream_story_cover(plan, cfg, out_path)


def render_story_scene(plan, scene, total, cfg, out_path):
    if _theme(cfg) == "hunter":
        return _hunter_story_scene(plan, scene, total, cfg, out_path)
    return _cream_story_scene(plan, scene, total, cfg, out_path)


def render_story_cta(plan, cfg, out_path):
    if _theme(cfg) == "hunter":
        return _hunter_story_cta(plan, cfg, out_path)
    return _cream_story_cta(plan, cfg, out_path)
