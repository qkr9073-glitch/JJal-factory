# -*- coding: utf-8 -*-
"""전자책(PDF) 생성기 — 카드뉴스와 같은 내용으로 전체 아이템 수록 리드마그넷 제작.
Pillow만으로 A4 페이지 이미지를 그려 멀티페이지 PDF로 저장 (외부 라이브러리 불필요)."""
from PIL import Image, ImageDraw

from .render import (_accent, _brand_mark, _font, _shadow_box, _tint,
                     _title_kind, _wrap)

EW, EH = 1240, 1754  # A4 @150dpi
EM = 90              # 좌우 여백

INK = (28, 25, 22)
SUB = (108, 98, 90)
CREAM = (252, 247, 240)


def _page():
    return Image.new("RGBA", (EW, EH), CREAM + (255,))


def _center(draw, text, font, y, fill):
    draw.text(((EW - draw.textlength(text, font=font)) / 2, y), text, font=font, fill=fill)


def _footer(canvas, cfg, page_no=None):
    draw = ImageDraw.Draw(canvas)
    wm = cfg.get("card_watermark") or ""
    f = _font("semi", 26)
    if wm:
        draw.text((EM, EH - 66), wm, font=f, fill=(178, 165, 152))
    if page_no:
        t = str(page_no)
        draw.text((EW - EM - draw.textlength(t, font=f), EH - 66), t,
                  font=f, fill=(178, 165, 152))


def _cover(plan, cfg):
    canvas = _page()
    accent = _accent(cfg)
    draw = ImageDraw.Draw(canvas)
    _brand_mark(canvas, cfg, EW / 2, 290, 350, alpha=0.95, idx=2)
    draw = ImageDraw.Draw(canvas)

    pill = "무료 배포용 PDF"
    pf = _font("semi", 32)
    pw = draw.textlength(pill, font=pf)
    draw.rounded_rectangle([(EW - pw) / 2 - 36, 500, (EW + pw) / 2 + 36, 566],
                           radius=33, fill=_tint(accent, 0.14),
                           outline=_tint(accent, 0.5), width=2)
    _center(draw, pill, pf, 514, _tint(accent, 0.9, base=INK))

    kind = _title_kind(cfg)
    tf = _font(kind, 108)
    lines = _wrap(draw, plan["ebook_title"], tf, EW - EM * 2)
    y = 660
    for ln in lines:
        _center(draw, ln, tf, y, INK)
        y += 132
    _center(draw, plan["subtitle"], _font("semi", 44), y + 40, SUB)

    n = plan.get("n_items", 0)
    _center(draw, f"전체 {n}개 수록", _font("xbold", 40), y + 150, _tint(accent, 0.95, base=INK))

    handle = cfg.get("card_handle") or cfg.get("card_watermark") or ""
    if handle:
        _center(draw, handle, _font("semi", 36), EH - 260, SUB)
    canvas.convert("RGB")
    return canvas


def _guide(plan, cfg):
    canvas = _page()
    accent = _accent(cfg)
    draw = ImageDraw.Draw(canvas)
    draw.text((EM, 130), "사용법 & 목차", font=_font("xbold", 64), fill=INK)

    tips = [
        "① 전부 다 하려고 하지 마세요. 지금 제일 필요한 것 하나만 고르세요.",
        "② 고른 항목을 그대로 따라 해보고, 내 상황에 맞게 한 줄씩 고치세요.",
        "③ 효과 본 것만 저장해두고 나만의 목록으로 만드세요.",
    ]
    y = 260
    tf = _font("reg", 34)
    for t in tips:
        for row in _wrap(draw, t, tf, EW - EM * 2):
            draw.text((EM, y), row, font=tf, fill=(52, 46, 42))
            y += 52
        y += 14

    y += 40
    draw.line([EM, y, EW - EM, y], fill=_tint(accent, 0.4), width=3)
    y += 50

    num = 1
    cf = _font("xbold", 40)
    rf = _font("reg", 34)
    for i, cat in enumerate(plan["categories"], 1):
        cnt = len(cat["items"])
        draw.text((EM, y), f"{i:02d}", font=cf, fill=accent)
        draw.text((EM + 80, y + 2), cat["name"], font=cf, fill=INK)
        rng = f"{num:02d} ~ {num + cnt - 1:02d}"
        draw.text((EW - EM - draw.textlength(rng, font=rf), y + 8), rng, font=rf, fill=SUB)
        num += cnt
        y += 78
    _footer(canvas, cfg)
    return canvas


def _divider(idx, cat_name, count, start_num, cfg):
    canvas = _page()
    accent = _accent(cfg)
    draw = ImageDraw.Draw(canvas)
    _brand_mark(canvas, cfg, EW - 165, 195, 190, alpha=0.5, idx=idx)
    draw = ImageDraw.Draw(canvas)
    kind = _title_kind(cfg)
    draw.text((EM, EH / 2 - 220), f"PART {idx}", font=_font("xbold", 48), fill=accent)
    tf = _font(kind, 120)
    y = EH / 2 - 130
    for ln in _wrap(draw, cat_name, tf, EW - EM * 2):
        draw.text((EM, y), ln, font=tf, fill=INK)
        y += 146
    draw.text((EM, y + 30), f"{start_num:02d} ~ {start_num + count - 1:02d} · 총 {count}개",
              font=_font("semi", 40), fill=SUB)
    _footer(canvas, cfg)
    return canvas


def _item_block(canvas, cfg, item, y, body_size=33):
    accent = _accent(cfg)
    draw = ImageDraw.Draw(canvas)
    inner_w = EW - EM * 2 - 88 - 60
    body_f = _font("reg", body_size)
    tag_f = _font("xbold", body_size)
    line_h = int(body_size * 1.55)

    per, rows_total = [], 0
    for ln in item["lines"]:
        tag_w = draw.textlength(f"[{ln['tag']}]", font=tag_f) + 14
        rows = _wrap(draw, ln["text"], body_f, inner_w - tag_w)
        per.append((tag_w, rows))
        rows_total += len(rows)
    inner_h = 60 + rows_total * line_h + (len(item["lines"]) - 1) * 16
    box_h = 40 + 66 + 18 + inner_h + 40

    _shadow_box(canvas, [EM, y, EW - EM, y + box_h], 28, (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    num_f = _font("xbold", 52)
    title_f = _font("xbold", 48)
    draw.text((EM + 44, y + 40), f"{item['num']:02d}", font=num_f, fill=accent)
    draw.text((EM + 44 + draw.textlength(f"{item['num']:02d}", font=num_f) + 26, y + 44),
              item["title"], font=title_f, fill=INK)
    iy = y + 40 + 66 + 18
    draw.rounded_rectangle([EM + 34, iy, EW - EM - 34, iy + inner_h],
                           radius=20, fill=_tint(accent, 0.085))
    ly = iy + 30
    for ln, (tag_w, rows) in zip(item["lines"], per):
        draw.text((EM + 34 + 26, ly), f"[{ln['tag']}]", font=tag_f,
                  fill=_tint(accent, 0.95, base=INK))
        for row in rows:
            draw.text((EM + 34 + 26 + tag_w, ly), row, font=body_f, fill=(52, 46, 42))
            ly += line_h
        ly += 16
    return y + box_h


def _outro(plan, cfg):
    from pathlib import Path

    from PIL import ImageFont

    canvas = _page()
    accent = _accent(cfg)
    draw = ImageDraw.Draw(canvas)
    _center(draw, "여기까지 읽으셨다면", _font("semi", 38), 130, SUB)
    _center(draw, "이제 하나만 실행할 차례예요", _font("xbold", 54), 196, INK)

    # 프로필 카드 + 누끼 사진 (사진은 카드 오른쪽에 겹쳐 배치)
    handle = cfg.get("card_handle") or cfg.get("card_watermark") or ""
    lines = cfg.get("card_profile_lines") or []
    card_top, card_bot = 380, 930
    _shadow_box(canvas, [EM, card_top, EW - EM, card_bot], 28, (255, 255, 255))
    photo_w = 0
    photo_path = cfg.get("card_profile_photo") or ""
    if photo_path and Path(photo_path).exists():
        try:
            img = Image.open(photo_path).convert("RGBA")
            box = img.getbbox()
            if box:
                img = img.crop(box)
            ph = 620
            img = img.resize((int(img.width * ph / img.height), ph), Image.LANCZOS)
            alpha = img.getchannel("A").point(lambda a: int(a * 0.96))
            img.putalpha(alpha)
            canvas.alpha_composite(img, (EW - EM - img.width + 24, card_bot - ph + 4))
            photo_w = img.width - 40
        except Exception:
            pass
    draw = ImageDraw.Draw(canvas)
    tx, ty = EM + 50, card_top + 56
    if handle:
        draw.text((tx, ty), handle, font=_font("xbold", 44),
                  fill=_tint(accent, 0.95, base=INK))
        ty += 84
    pf = _font("reg", 31)
    text_w = max(380, EW - EM * 2 - 100 - photo_w)
    for t in lines:
        for row in _wrap(draw, t, pf, text_w):
            draw.text((tx, ty), row, font=pf, fill=(52, 46, 42))
            ty += 48
        ty += 10

    # 유입 링크 (카톡방 / 카페 / 인스타)
    links = cfg.get("card_links") or []
    y = 1000
    if links:
        draw.text((EM, y), "더 깊게 배우고 싶다면, 여기로 오세요", font=_font("xbold", 38), fill=INK)
        y += 72
        lf, uf = _font("xbold", 33), _font("reg", 27)
        for lk in links[:4]:
            _shadow_box(canvas, [EM, y, EW - EM, y + 106], 20, (255, 255, 255))
            draw = ImageDraw.Draw(canvas)
            _link_icon(canvas, EM + 32, y + 53, 44, lk)
            draw.text((EM + 112, y + 16), lk.get("label", ""), font=lf, fill=INK)
            draw.text((EM + 112, y + 60), lk.get("url", ""), font=uf,
                      fill=_tint(accent, 0.95, base=INK))
            y += 124

    _center(draw, "이 자료가 도움됐다면 — 게시물 저장 + 팔로우가 큰 힘이 돼요",
            _font("semi", 29), y + 22, SUB)
    _center(draw, "본 자료의 무단 재배포·재판매를 금지합니다",
            _font("reg", 25), EH - 100, (178, 165, 152))
    return canvas


def _link_icon(canvas, x, cy, s, lk):
    """유입 링크 아이콘 — 유튜브=빨간 로고, 카카오/오픈채팅=노란 말풍선, 그 외=컬러 이모지.
    (x = 아이콘 왼쪽, cy = 세로 중심)"""
    draw = ImageDraw.Draw(canvas)
    key = (str(lk.get("url", "")) + " " + str(lk.get("label", ""))).lower()
    if ("youtube" in key or "youtu.be" in key or "유튜브" in key):
        w, h = s * 1.34, s * 0.94
        bx, by = x, cy - h / 2
        draw.rounded_rectangle([bx, by, bx + w, by + h], radius=h * 0.28, fill=(255, 0, 0))
        tw = h * 0.42
        draw.polygon([(bx + w / 2 - tw * 0.45, cy - tw * 0.62),
                      (bx + w / 2 - tw * 0.45, cy + tw * 0.62),
                      (bx + w / 2 + tw * 0.78, cy)], fill=(255, 255, 255))
        return
    if any(t in key for t in ("kakao", "openchat", "오픈", "채팅", "카톡", "커뮤니티", "톡방")):
        bx, by = x, cy - s / 2
        draw.rounded_rectangle([bx, by, bx + s, by + s * 0.80], radius=s * 0.30, fill=(254, 229, 0))
        draw.polygon([(bx + s * 0.24, by + s * 0.74), (bx + s * 0.46, by + s * 0.74),
                      (bx + s * 0.24, by + s)], fill=(254, 229, 0))
        r = s * 0.06
        for dxp in (0.34, 0.5, 0.66):
            ccx, ccy = bx + s * dxp, by + s * 0.38
            draw.ellipse([ccx - r, ccy - r, ccx + r, ccy + r], fill=(60, 45, 20))
        return
    try:
        ef = ImageFont.truetype("C:/Windows/Fonts/seguiemj.ttf", int(s))
        draw.text((x, cy - s / 2), lk.get("emoji", "🔗"), font=ef, embedded_color=True)
    except Exception:
        pass


def build_ebook(plan, items, cfg, out_pdf, log=print):
    """plan + 전체 아이템 → PDF 저장. 반환: 페이지 수"""
    pages = [_cover(plan, cfg), _guide(plan, cfg)]

    by_cat = {}
    for it in items:
        by_cat.setdefault(it.get("category", ""), []).append(it)

    page_no = 2
    num = 1
    for ci, cat in enumerate(plan["categories"], 1):
        cat_items = by_cat.get(cat["name"], [])
        if not cat_items:
            continue
        pages.append(_divider(ci, cat["name"], len(cat_items), num, cfg))
        num += len(cat_items)
        page_no += 1
        for i in range(0, len(cat_items), 2):
            canvas = _page()
            y = 110
            for it in cat_items[i:i + 2]:
                y = _item_block(canvas, cfg, it, y) + 44
            page_no += 1
            _footer(canvas, cfg, page_no)
            pages.append(canvas)

    pages.append(_outro(plan, cfg))
    rgb = [p.convert("RGB") for p in pages]
    rgb[0].save(out_pdf, "PDF", save_all=True, append_images=rgb[1:], resolution=150)
    log(f"      전자책 PDF {len(rgb)}페이지 저장")
    return len(rgb)
