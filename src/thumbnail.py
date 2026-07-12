# -*- coding: utf-8 -*-
"""썸네일 렌더러 — (가짜 게시글 헤더 바) + 짤 + 하단 그라데이션 + 볼드 카피 2줄 + 워터마크
헤더 바: 제목 + 최근날짜 + 동물 프로필 + 조회/추천/댓글 + 빨간 강조 테두리 (참고 계정 스타일)"""
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

if getattr(sys, "frozen", False):  # PyInstaller exe
    ASSETS = Path(sys.executable).resolve().parent / "assets" / "fonts"
else:
    ASSETS = Path(__file__).resolve().parent.parent / "assets" / "fonts"

W, H = 1080, 1350
MARGIN_X = 64
HEADER_H = 170

MALGUN = Path("C:/Windows/Fonts/malgun.ttf")
MALGUN_BD = Path("C:/Windows/Fonts/malgunbd.ttf")
EMOJI_FONT = Path("C:/Windows/Fonts/seguiemj.ttf")
ANIMALS = ["🐶", "🐱", "🐹", "🐰", "🦊", "🐻", "🐼", "🐨", "🐯", "🦁", "🐷", "🐸", "🐥", "🦉"]
PASTELS = [(255, 224, 178), (200, 230, 201), (187, 222, 251), (255, 205, 210),
           (225, 190, 231), (255, 249, 196), (178, 235, 242)]


def _font(size):  # 카피용 (검은고딕)
    for candidate in [ASSETS / "BlackHanSans.ttf", MALGUN_BD]:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default(size)


def _gothic(size, bold=False):  # 헤더용 (맑은고딕)
    p = MALGUN_BD if bold else MALGUN
    if p.exists():
        return ImageFont.truetype(str(p), size)
    return _font(size)


def _text_w(draw, text, font):
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _ellipsis(draw, text, font, max_w):
    if _text_w(draw, text, font) <= max_w:
        return text
    while text and _text_w(draw, text + "…", font) > max_w:
        text = text[:-1]
    return text + "…"


def _draw_avatar(canvas, cx, cy, r):
    """동물 이모지 프로필 (컬러 이모지 실패 시 파스텔 원 + 그린 곰돌이)"""
    draw = ImageDraw.Draw(canvas)
    bg = random.choice(PASTELS)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=bg,
                 outline=(220, 220, 220), width=2)
    emoji = random.choice(ANIMALS)
    try:
        f = ImageFont.truetype(str(EMOJI_FONT), int(r * 1.35))
        box = draw.textbbox((0, 0), emoji, font=f, embedded_color=True)
        draw.text((cx - (box[2] - box[0]) / 2 - box[0],
                   cy - (box[3] - box[1]) / 2 - box[1]),
                  emoji, font=f, embedded_color=True)
    except Exception:
        # 폴백: 곰돌이 직접 그리기
        face = (160, 120, 90)
        draw.ellipse([cx - r * .8, cy - r * .8, cx + r * .8, cy + r * .8], fill=face)
        for ex in (-1, 1):
            draw.ellipse([cx + ex * r * .6 - r * .25, cy - r * .95,
                          cx + ex * r * .6 + r * .25, cy - r * .45], fill=face)
        draw.ellipse([cx - r * .08, cy - r * .1, cx + r * .08, cy + r * .06],
                     fill=(60, 40, 30))


def _draw_header(canvas, header):
    """게시글 헤더 바: 흰 배경 + 제목/날짜 + 프로필 + 조회·추천·댓글 + 빨간 강조 테두리"""
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, 0, W, HEADER_H], fill=(255, 255, 255))

    date_font = _gothic(27)
    date_w = _text_w(draw, header["date"], date_font)
    draw.text((W - 44 - date_w, 36), header["date"], font=date_font, fill=(120, 120, 120))

    title_font = _gothic(42, bold=True)
    title = _ellipsis(draw, header["title"], title_font, W - 44 * 2 - date_w - 30)
    draw.text((44, 26), title, font=title_font, fill=(20, 20, 20))

    _draw_avatar(canvas, 76, 120, 27)
    x = 126
    lab_f, num_f = _gothic(27), _gothic(29, bold=True)
    for label, value in [("조회 수", f"{header['views']:,}"),
                         ("추천 수", f"{header['recs']:,}"),
                         ("댓글", f"{header['replies']:,}")]:
        draw.text((x, 104), label, font=lab_f, fill=(100, 100, 100))
        x += _text_w(draw, label, lab_f) + 12
        draw.text((x, 102), value, font=num_f, fill=(20, 20, 20))
        x += _text_w(draw, value, num_f) + 34

    # 빨간 강조 테두리 (참고 계정의 포인트)
    draw.rounded_rectangle([10, 8, W - 10, HEADER_H - 8], radius=14,
                           outline=(225, 30, 30), width=6)


def render(src_image, line1, line2, watermark, out_path, header=None):
    canvas = Image.new("RGB", (W, H), (8, 8, 10))
    top = HEADER_H if header else 0

    img = Image.open(src_image).convert("RGB")
    # 가로 1080 커버핏, 세로가 길면 위쪽 기준 크롭 (캡처는 상단이 핵심)
    scale = W / img.width
    nh = max(1, int(img.height * scale))
    img = img.resize((W, nh), Image.LANCZOS)
    area_h = H - top
    if nh >= area_h:
        canvas.paste(img.crop((0, 0, W, area_h)), (0, top))
    else:
        canvas.paste(img, (0, top + max(0, (area_h - nh) // 3)))

    if header:
        _draw_header(canvas, header)

    # 하단 그라데이션 (텍스트 가독성)
    grad_h = int(H * 0.48)
    overlay = Image.new("L", (1, grad_h))
    for y in range(grad_h):
        overlay.putpixel((0, y), int(235 * (y / grad_h) ** 1.5))
    overlay = overlay.resize((W, grad_h))
    black = Image.new("RGB", (W, grad_h), (0, 0, 0))
    canvas.paste(black, (0, H - grad_h), overlay)

    draw = ImageDraw.Draw(canvas)

    # 카피 2줄 — 긴 줄 기준으로 폰트 크기 자동 조절
    lines = [l for l in (line1, line2) if l and l.strip()]
    size = 96
    while size > 40:
        font = _font(size)
        if max((_text_w(draw, l, font) for l in lines), default=0) <= W - MARGIN_X * 2:
            break
        size -= 4
    font = _font(size)
    line_h = int(size * 1.22)
    base_y = H - 150 - line_h * len(lines)
    for i, line in enumerate(lines):
        y = base_y + i * line_h
        draw.text((MARGIN_X + 3, y + 4), line, font=font, fill=(0, 0, 0))  # 그림자
        draw.text((MARGIN_X, y), line, font=font, fill=(255, 255, 255))

    # 워터마크 (하단 중앙)
    if watermark:
        wm_font = _font(34)
        wm_w = _text_w(draw, watermark, wm_font)
        draw.text(((W - wm_w) // 2, H - 78), watermark, font=wm_font, fill=(190, 190, 190))

    canvas.save(out_path, "JPEG", quality=93)
    return str(out_path)
