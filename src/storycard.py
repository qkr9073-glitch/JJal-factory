# -*- coding: utf-8 -*-
"""스토리카드 렌더러 (StoryRabbit 참고 스타일)
레이아웃: 상단 브랜드 헤더(로고+계정명+인증뱃지+@핸들) → 굵은 헤드라인(옵션)
          → 라운드 이미지(+우하단 워터마크 뱃지) → 본문 내러티브(강조줄 볼드)
브랜드 요소(로고/계정명/핸들/워터마크)는 config 키로 교체 가능."""
import sys
import threading
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "assets" / "fonts"

# 렌더 언어(스레드별) — render_card 진입 시 설정, 끝나면 "ko"로 복구
_LOCAL = threading.local()

_YU_B = Path("C:/Windows/Fonts/YuGothB.ttc")   # Yu Gothic Bold
_YU_M = Path("C:/Windows/Fonts/YuGothM.ttc")   # Yu Gothic Medium
_YU_R = Path("C:/Windows/Fonts/YuGothR.ttc")   # Yu Gothic Regular


def _lang():
    return getattr(_LOCAL, "lang", "ko")

W, H = 1080, 1350
PAD = 40
CONTENT_W = W - PAD * 2

INK = (17, 17, 17)
SUB = (130, 133, 140)
LINE = (232, 234, 238)
VERIFY = (29, 155, 240)
BADGE_BG = (46, 160, 87)

_MALGUN = Path("C:/Windows/Fonts/malgun.ttf")
_MALGUN_BD = Path("C:/Windows/Fonts/malgunbd.ttf")


def _font(kind, size):
    """reg / semi / bold — 한국어=Pretendard, 일본어=Yu Gothic (한글폰트는 일어 두부)"""
    if _lang() == "ja":
        cands = {
            "reg": [_YU_R, _YU_M, _MALGUN],
            "semi": [_YU_M, _YU_B, _MALGUN_BD],
            "bold": [_YU_B, _MALGUN_BD],
        }[kind]
    else:
        cands = {
            "reg": [FONTS / "Pretendard-Regular.otf", _MALGUN],
            "semi": [FONTS / "Pretendard-SemiBold.otf", _MALGUN_BD],
            "bold": [FONTS / "Pretendard-ExtraBold.otf", _MALGUN_BD],
        }[kind]
    for p in cands:
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default(size)


def _tw(d, t, f):
    # Pillow는 줄바꿈(\n) 든 텍스트 폭을 못 잼 → 가장 긴 줄로 안전 측정
    t = str(t)
    if "\n" in t:
        return max((d.textlength(ln, font=f) for ln in t.split("\n")), default=0.0)
    return d.textlength(t, font=f)


def _wrap(d, text, font, max_w):
    """공백 우선 줄바꿈, 넘치면 글자 단위 분해 (한국어/일본어 모두 대응).
    단일 줄 래핑용이므로 들어온 \\n·개행은 공백으로 눕힌다."""
    text = " ".join(str(text).split())
    lines, cur = [], ""
    for word in text.split(" "):
        trial = (cur + " " + word).strip()
        if _tw(d, trial, font) <= max_w:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        while _tw(d, word, font) > max_w and len(word) > 1:
            cut = 1
            for i in range(1, len(word) + 1):
                if _tw(d, word[:i], font) > max_w:
                    break
                cut = i
            lines.append(word[:cut])
            word = word[cut:]
        cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def _rounded_mask(size, radius):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1],
                                        radius=radius, fill=255)
    return m


def _cover(img, box_w, box_h):
    """비율 유지하며 박스를 꽉 채우도록 크롭(cover)"""
    src_r, box_r = img.width / img.height, box_w / box_h
    if src_r > box_r:
        nh = box_h
        nw = int(nh * src_r)
    else:
        nw = box_w
        nh = int(nw / src_r)
    img = img.resize((max(nw, 1), max(nh, 1)), Image.LANCZOS)
    left = (img.width - box_w) // 2
    top = (img.height - box_h) // 2
    return img.crop((left, top, left + box_w, top + box_h))


def extract_photo(path):
    """카드형 스크린샷(흰 배경 위 헤더+사진+본문텍스트, 스토리래빗류)에서 메인 '사진
    밴드'만 남기고 헤더·본문텍스트를 잘라낸다(제자리 저장). 사진을 자신 있게 못 찾거나
    이미 사진 한 장이면 원본을 그대로 둔다(안전 no-op — 깨끗한 사진엔 영향 없음)."""
    try:
        im = Image.open(path).convert("RGB")
    except Exception:
        return path
    W, H = im.size
    px = im.load()                       # ⚠️ 리사이즈하면 글씨가 뭉개져 회색(중간톤)처럼
    xs = list(range(0, W, max(1, W // 220)))   # 보임 → 원본 픽셀에서 직접 샘플(글씨 선명)
    nx = len(xs)
    ys = list(range(0, H, max(1, H // 600)))
    n = len(ys)

    def row_is_photo(py):
        white = black = sat = 0
        for x in xs:
            r, g, b = px[x, py]
            mx, mn = max(r, g, b), min(r, g, b)
            if mn > 244:                 # 순백(카드 배경)만 흰색 — 눈/하늘은 중간톤
                white += 1
            elif mx < 70:
                black += 1
            sat += mx - mn
        mid = 1 - (white + black) / nx   # 흰색도 검정도 아닌 '중간톤'(사진 특유)
        # 텍스트 행=순백배경+검은글씨(중간톤 적음)이라 제외. 사진 행=컬러 or 중간톤 풍부.
        return (sat / nx) > 12 or mid > 0.35

    rows = [row_is_photo(py) for py in ys]
    gap = max(1, int(n * 0.02))          # 사진 속 밝은 줄로 생긴 작은 구멍만 메움
    i = 1
    while i < n:
        if not rows[i]:
            j = i
            while j < n and not rows[j]:
                j += 1
            if j < n and (j - i) <= gap:
                for k in range(i, j):
                    rows[k] = True
            i = j
        else:
            i += 1
    best = 0
    bs = be = cs = None
    for i in range(n + 1):               # 최대 연속 '사진' 밴드
        on = i < n and rows[i]
        if on and cs is None:
            cs = i
        elif not on and cs is not None:
            if i - cs > best:
                best, bs, be = i - cs, cs, i
            cs = None
    if bs is None:
        return path
    frac = best / n
    if frac > 0.90 or frac < 0.12:       # 이미 사진 한 장이거나 못 찾음 → 손대지 않음
        return path
    if bs <= n * 0.03 and be >= n * 0.97:  # 위아래 흰 크롬이 없으면 카드 아님
        return path
    y0 = ys[bs]
    y1 = ys[be] if be < n else H
    im.crop((0, y0, W, y1)).save(path, "JPEG", quality=93)
    return path


def _verified(d, cx, cy, r):
    """파란 인증 뱃지"""
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=VERIFY)
    w = max(2, int(r * 0.26))
    d.line([cx - r * 0.42, cy + r * 0.02, cx - r * 0.08, cy + r * 0.4],
           fill=(255, 255, 255), width=w)
    d.line([cx - r * 0.08, cy + r * 0.4, cx + r * 0.48, cy - r * 0.36],
           fill=(255, 255, 255), width=w)


def _logo(canvas, cfg, x, y, s=76):
    """브랜드 로고 (config story_logo 경로, 없으면 녹색 라운드 사각 플레이스홀더).
    투명 PNG면 형태 그대로(contain) 헤더에 얹고, 불투명이면 라운드 사각으로 채움."""
    src = str(cfg.get("story_logo") or "")
    if src and Path(src).exists():
        try:
            lg = Image.open(src)
            if lg.mode in ("RGBA", "LA") or (lg.mode == "P" and "transparency" in lg.info):
                lg = lg.convert("RGBA")
                lg.thumbnail((s, s), Image.LANCZOS)  # 전체가 보이게 contain
                ox, oy = x + (s - lg.width) // 2, y + (s - lg.height) // 2
                canvas.paste(lg, (ox, oy), lg)       # 알파 마스크 → 투명 유지
            else:
                lg = _cover(lg.convert("RGB"), s, s)
                canvas.paste(lg, (x, y), _rounded_mask((s, s), 18))
            return
        except Exception:
            pass
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle([x, y, x + s, y + s], radius=18, fill=(232, 245, 233),
                        outline=(205, 225, 208), width=2)
    try:
        ef = ImageFont.truetype("C:/Windows/Fonts/seguiemj.ttf", int(s * 0.56))
        d.text((x + s // 2, y + s // 2), "🐰", font=ef, anchor="mm",
               embedded_color=True)
    except Exception:
        d.text((x + s // 2, y + s // 2), "S", font=_font("bold", int(s * 0.5)),
               fill=BADGE_BG, anchor="mm")


def _header(canvas, cfg):
    """상단 브랜드 헤더 → 아래 y 반환"""
    d = ImageDraw.Draw(canvas)
    _logo(canvas, cfg, PAD, 28, 76)
    name = str(cfg.get("story_brand_name") or "StoryRabbit.kr")
    handle = str(cfg.get("story_brand_handle") or "@스토리 래빗")
    nf, hf = _font("bold", 34), _font("reg", 26)
    tx = PAD + 76 + 20
    d.text((tx, 32), name, font=nf, fill=INK)
    if cfg.get("story_verified", True):
        _verified(d, tx + _tw(d, name, nf) + 20, 32 + 17, 13)
    d.text((tx, 76), handle, font=hf, fill=SUB)
    return 28 + 76 + 14


def _watermark(canvas, cfg, x1, y1):
    """이미지 우하단 워터마크 — 불투명 다크 라운드 필로 '그 자리를 덮어' 원본(스토리래빗)
    워터마크까지 가린다(0원, AI 불필요). 반투명이면 옛 워터마크가 비치므로 반드시 불투명."""
    txt = str(cfg.get("story_watermark") or "instagram.com/kangaroostory.jp")
    f = _font("semi", 21)
    tw = int(_tw(ImageDraw.Draw(canvas), txt, f))
    bw = max(tw + 34, int(CONTENT_W * 0.44))   # 옛 워터마크 폭까지 덮도록 최소폭 확보
    bh = 50
    bx, by = x1 - bw - 6, y1 - bh - 6          # 코너에 바짝 붙여 삐져나옴 방지
    pill = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    ImageDraw.Draw(pill).rounded_rectangle([0, 0, bw - 1, bh - 1], radius=16,
                                           fill=(20, 22, 28, 255))   # 불투명(핵심)
    canvas.paste(pill, (bx, by), pill)
    ImageDraw.Draw(canvas).text((bx + bw - tw - 20, by + 14), txt, font=f,
                                fill=(245, 246, 248))


def _para_lines(d, para, size):
    """문단 → 실제 그릴 줄 목록.
    본문 기본을 semi(일본어=Medium)로 — reg는 인스타에서 너무 얇아 안 보임.
    fill=True(기본): AI가 준 짧은 구절들을 폭이 찰 때까지 이어붙여 '가로 꽉 채움'
      (한국 스토리래빗 원본처럼). 줄바꿈은 구절 경계에서만 → 숫자·화폐 안 깨짐.
    fill=False: 원문의 줄바꿈(\\n)을 그대로 살림(구식 짧은 줄)."""
    f = _font("bold" if para.get("bold") else "semi", size)
    phrases = [s.strip() for s in str(para.get("text", "")).split("\n") if s.strip()]
    if not phrases:
        return [], f
    if not getattr(_LOCAL, "fill", True):
        out = []
        for ph in phrases:
            out += _wrap(d, ph, f, CONTENT_W)
        return out, f
    sep = "" if _lang() == "ja" else " "      # 일본어는 구절 사이 공백 없음
    lines, cur = [], ""
    for ph in phrases:
        cand = (cur + sep + ph) if cur else ph
        if cur and _tw(d, cand, f) > CONTENT_W:
            lines.append(cur)
            cur = ph
        else:
            cur = cand
    if cur:
        lines.append(cur)
    out = []                                  # 한 구절이 폭보다 길면 글자단위 폴백
    for ln in lines:
        out += _wrap(d, ln, f, CONTENT_W) if _tw(d, ln, f) > CONTENT_W else [ln]
    return out, f


def _body_height(d, paras, size):
    lh = int(size * 1.52)
    total = 0
    for p in paras:
        lines, _ = _para_lines(d, p, size)
        total += len(lines) * lh + 20  # 문단 간격
    return total


def render_card(image_path, headline, paragraphs, cfg, out_path,
                img_ratio=0.42, lang="ko"):
    """스토리카드 1장 렌더.
    paragraphs: [{"text": "...", "bold": False}, ...]  (bold=강조줄)
    headline: 굵은 제목 (없으면 None/"" → 생략)
    lang="ja" 면 일본어 폰트(Yu Gothic)로 렌더 (한글폰트는 일어 두부)."""
    _LOCAL.lang = lang
    _LOCAL.fill = bool(cfg.get("story_text_fill", True))      # 폭 꽉 채우기(한국 원본처럼)
    _LOCAL.fullbleed = bool(cfg.get("story_photo_fullbleed", True))  # 사진 좌우 끝까지
    try:
        return _render_card(image_path, headline, paragraphs, cfg, out_path, img_ratio)
    finally:
        _LOCAL.lang = "ko"


def _render_card(image_path, headline, paragraphs, cfg, out_path, img_ratio=0.42):
    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(canvas)
    y = _header(canvas, cfg)

    # 헤드라인
    if headline:
        hs = 50
        hf = _font("bold", hs)
        lines = _wrap(d, headline, hf, CONTENT_W)
        while len(lines) > 2 and hs > 36:
            hs -= 4
            hf = _font("bold", hs)
            lines = _wrap(d, headline, hf, CONTENT_W)
        for ln in lines:
            d.text((PAD, y), ln, font=hf, fill=INK)
            y += int(hs * 1.26)
        y += 14

    paras = [p for p in paragraphs if str(p.get("text", "")).strip()]

    # 사진: 풀블리드(좌우 끝까지·각진 모서리, 한국 스토리래빗 원본) 또는 여백+라운드.
    # 원본 비율 → 폭 맞춤 '자연 높이'. 넓은 사진은 잘림 없이, 세로긴 것만 밴드 크롭.
    fb = getattr(_LOCAL, "fullbleed", True)
    img_w = W if fb else CONTENT_W
    img_x = 0 if fb else PAD
    radius = 0 if fb else 12
    photo = None
    nat_h = int(H * 0.40)
    try:
        photo = Image.open(image_path).convert("RGB")
        nat_h = int(img_w * photo.height / max(1, photo.width))
    except Exception:
        photo = None

    MAX_IMG, MIN_IMG = int(H * 0.52), int(H * 0.34)
    crop = nat_h > MAX_IMG
    img_h = MAX_IMG if crop else nat_h

    # 콘텐츠 총 높이가 4:5(1350) 넘을 때만 본문 폰트를 줄인다. 안 넘으면 34 유지하고
    # 카드 세로를 콘텐츠에 맞춰 잘라 아래 빈 여백을 없앤다(일본 인스타/스토리래빗 스타일).
    def _bottom(sz, ih):
        return y + ih + 26 + _body_height(d, paras, sz)
    size = 34                                # 본문 크게(가독성↑, 짧은 줄 리듬 유지)
    while size > 23 and _bottom(size, img_h) > H - PAD:
        size -= 1
    # 최소 폰트로도 넘치면 이미지를 MIN_IMG까지만 줄여 맞춘다(그 이상은 본문 자연 클립)
    while _bottom(size, img_h) > H - PAD and img_h > MIN_IMG:
        img_h -= 20
        crop = True

    # 이미지 렌더: 넓은 사진은 폭맞춤(잘림 X), 세로긴/축소분만 cover 크롭
    if photo is not None:
        try:
            pimg = (_cover(photo, img_w, img_h) if (crop or nat_h != img_h)
                    else photo.resize((img_w, img_h), Image.LANCZOS))
            if radius > 0:
                canvas.paste(pimg, (img_x, y), _rounded_mask((img_w, img_h), radius))
            else:
                canvas.paste(pimg, (img_x, y))
        except Exception:
            photo = None
    if photo is None:
        if radius > 0:
            d.rounded_rectangle([img_x, y, img_x + img_w, y + img_h], radius=radius,
                                fill=(238, 240, 244))
        else:
            d.rectangle([img_x, y, img_x + img_w, y + img_h], fill=(238, 240, 244))
    _watermark(canvas, cfg, img_x + img_w, y + img_h)
    y += img_h + 26

    # 본문
    lh = int(size * 1.52)
    for p in paras:
        lines, f = _para_lines(d, p, size)
        for ln in lines:
            if y > H - PAD - lh:
                break
            d.text((PAD, y), ln, font=f, fill=INK)
            y += lh
        y += 20
    y -= 20                                  # 마지막 문단 뒤 군더더기 간격 제거

    # 동적 높이: 콘텐츠 바로 아래에서 카드를 잘라 하단 빈 여백을 없앤다.
    # 상한 4:5(1350), 하한 ~0.9(정사각보다 살짝 낮게) — 짧은 글도 타이트하게.
    bottom = min(H, max(int(W * 0.9), y + PAD))
    canvas.crop((0, 0, W, bottom)).save(out_path, "JPEG", quality=93)
    return str(out_path)
