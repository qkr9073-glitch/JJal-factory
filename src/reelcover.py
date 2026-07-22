# -*- coding: utf-8 -*-
"""릴스 커스텀 커버(썸네일) 렌더러 — 9:16(1080x1920).
영상 프레임 위에 후킹 문구 2줄을 얹는다: 투톤 색(1줄/2줄 다른 색) + 굵은 폰트 +
진한 외곽선 + 그림자. 인스타 프로필 그리드에서 1:1로 잘리는 걸 감안해 기본 위치는
가운데 안전영역.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1920
BASEDIR = Path(__file__).resolve().parent.parent
FONTS = BASEDIR / "fonts"            # 공유 폰트 라이브러리(자막과 동일)
ASSETS = BASEDIR / "assets" / "fonts"
# 레퍼런스 느낌(둥근 굵은체) 우선순위
_PREF = ["Cafe24Ssurround-v2.0.otf", "Paperlogy-7Bold.ttf", "BlackHanSans.ttf"]


def _hex(c, default=(255, 255, 255)):
    s = str(c or "").lstrip("#")
    if len(s) != 6:
        return default
    try:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return default


def _font_path(font_file=None):
    if font_file:
        p = FONTS / Path(font_file).name
        if p.exists():
            return p
    for n in _PREF:
        for d in (FONTS, ASSETS):
            if (d / n).exists():
                return d / n
    return None


def _font(size, font_file=None):
    p = _font_path(font_file)
    if p:
        try:
            return ImageFont.truetype(str(p), size)
        except Exception:
            pass
    return ImageFont.load_default(size)


def _fit_cover(img):
    """원본을 1080x1920에 꽉 차게(잘라내기) 맞춘다."""
    img = img.convert("RGB")
    sw, sh = img.size
    scale = max(W / sw, H / sh)
    nw, nh = int(sw * scale + 0.5), int(sh * scale + 0.5)
    img = img.resize((nw, nh), Image.LANCZOS)
    return img.crop(((nw - W) // 2, (nh - H) // 2, (nw - W) // 2 + W, (nh - H) // 2 + H))


def _autosize(draw, line, font_file, start, max_w):
    """문구가 max_w 안에 들어오도록 폰트 크기 자동 축소."""
    size = start
    while size > 34:
        f = _font(size, font_file)
        if draw.textlength(line, font=f) <= max_w:
            return f
        size -= 4
    return _font(34, font_file)


def render(src_image, line1, line2, out_path, font_file=None,
           color1="#FFFFFF", color2="#FFE24A", pos="center", size=120):
    """프레임 이미지 + 2줄 문구 → 릴스 커버 JPEG 저장. 반환: out_path"""
    base = _fit_cover(Image.open(src_image))
    lines = [(str(line1 or "").strip(), _hex(color1)),
             (str(line2 or "").strip(), _hex(color2, (255, 226, 74)))]
    lines = [(t, c) for t, c in lines if t]
    if not lines:
        base.save(out_path, "JPEG", quality=92)
        return out_path

    margin = 70
    max_w = W - margin * 2
    d0 = ImageDraw.Draw(base)
    fonts = [_autosize(d0, t, font_file, size, max_w) for t, _ in lines]
    heights = [int((f.getbbox(t)[3] - f.getbbox(t)[1]) * 1.0) for (t, _), f in zip(lines, fonts)]
    gap = int(size * 0.22)
    total = sum(heights) + gap * (len(lines) - 1)

    if pos == "top":
        y = int(H * 0.13)
    elif pos == "bottom":
        y = int(H * 0.70) - total
    else:                                  # center — 1:1 그리드 크롭에도 살아남는 안전영역
        y = (H - total) // 2 - int(H * 0.06)

    # 그림자 레이어(부드럽게) → 본문(외곽선 포함) 순서로 합성
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ds = ImageDraw.Draw(shadow)
    stroke = max(6, int(size * 0.10))
    yy = y
    for (t, _), f, h in zip(lines, fonts, heights):
        x = (W - d0.textlength(t, font=f)) / 2
        ds.text((x, yy + int(size * 0.10)), t, font=f, fill=(0, 0, 0, 170),
                stroke_width=stroke, stroke_fill=(0, 0, 0, 170))
        yy += h + gap
    shadow = shadow.filter(ImageFilter.GaussianBlur(int(size * 0.10)))
    base = Image.alpha_composite(base.convert("RGBA"), shadow)

    d = ImageDraw.Draw(base)
    yy = y
    for (t, col), f, h in zip(lines, fonts, heights):
        x = (W - d.textlength(t, font=f)) / 2
        d.text((x, yy), t, font=f, fill=col + (255,),
               stroke_width=stroke, stroke_fill=(20, 18, 16, 255))
        yy += h + gap

    base.convert("RGB").save(out_path, "JPEG", quality=92)
    return out_path
