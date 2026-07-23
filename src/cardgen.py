# -*- coding: utf-8 -*-
"""릴스 → 카드뉴스(캐러셀) 짤 변환.
- 스타일 학습: 수집확장으로 모은 참고 캐러셀 계정의 카드 이미지를 Gemini 비전으로 읽어
  전개방식(훅/흐름/카드당 글자량/CTA/말투) + 비주얼(색/정렬/폰트 느낌) 프로파일 저장.
- 변환: 릴스 확정 대본을 내용 유지한 채 카드별 텍스트로 재구성 → 4:5(1080x1350) 렌더.
저장: BASE/carousel_styles.json = {"<회원코드>:<채널>": {profile..., "learned": iso}}
"""
import base64
import io
import json
import re
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont, ImageStat

from cardnews import brain as cbrain
from src import fonts as fontlib

W, H = 1080, 1350
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"}


# ── 스타일 저장소 ─────────────────────────────────────────────
def _styles_path(base):
    return Path(base) / "carousel_styles.json"


def load_styles(base):
    p = _styles_path(base)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return {}


def save_styles(base, data):
    _styles_path(base).write_text(json.dumps(data, ensure_ascii=False, indent=1),
                                  encoding="utf-8")


# ── 학습 ─────────────────────────────────────────────────────
def _img_part_bytes(data, max_side=768):
    img = Image.open(io.BytesIO(data)).convert("RGB")
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=72)
    return {"inline_data": {"mime_type": "image/jpeg",
                            "data": base64.b64encode(buf.getvalue()).decode()}}


def fetch_card_images(posts, max_total=80, log=print):
    """수집 항목들에서 카드 이미지 bytes 다운로드(만료/실패는 건너뜀).
    게시물당 제한 없이 담긴 카드 전부(안전 상한 max_total)."""
    out = []
    for it in posts:
        urls = list(it.get("imageUrls") or []) or \
            ([it["thumbUrl"]] if it.get("thumbUrl") else [])
        for u in urls:
            if len(out) >= max_total:
                return out
            try:
                r = requests.get(u, headers=UA, timeout=15)
                if r.status_code == 200 and len(r.content) > 5000:
                    out.append(r.content)
            except Exception as e:
                log(f"      이미지 스킵: {str(e)[:60]}")
    return out


LEARN_PROMPT = """이 이미지들은 한 인스타그램 카드뉴스(캐러셀) 계정의 게시물 카드들이다.
이 계정의 '카드뉴스 만드는 법'을 분석해 프로파일로 정리하라.
- flow: 카드 전개 순서(예: "훅 → 공감 → 정보 1~4 → 요약 → CTA")
- hook_style: 1번 카드(표지)가 시선을 잡는 방식
- card_count: 게시물당 카드 수 [최소, 최대]
- head_chars: 카드 헤드(큰 글씨) 대략 글자수(숫자), body_chars: 본문 대략 글자수(없으면 0)
- cta_style: 마지막 카드의 행동유도 방식
- tone: 말투(반말/존댓말, 특징적 어미)
- visual: {"bg":"배경 대표색 hex", "bg2":"보조/그라데이션색 hex(단색이면 bg와 동일)",
  "text":"글자색 hex", "accent":"표지 큰 글씨의 포인트색 hex(핑크 등 — 없으면 #F9A8C9)",
  "label_bg":"표지의 작은 라벨(태그) 배경 파스텔색 hex(없으면 #FFD9E3)", "align":"center|left",
  "font_feel":"손글씨|둥근|고딕굵게|픽셀 중 가장 가까운 것 (아기자기/귀여운 손글씨체면 반드시 손글씨)",
  "text_pos":"top|center|bottom — 사진 위 글자 블록의 위치",
  "panel":"white(반투명 흰 박스 위 글자)|dark(어두운 그라데이션 위 글자)|none(사진 위 바로)",
  "handle_color":"하단 계정 핸들(@아이디) 글자색 hex (대개 #FFFFFF)",
  "deco":"페이지번호/밑줄/형광펜 등 눈에 띄는 장식 짧게"}
반드시 JSON만."""


def learn_style(cfg, images, log=print):
    """카드 이미지 bytes 목록 → 전개+비주얼 프로파일 dict.
    장수가 많아 빈 응답이 오면 절반으로 줄여 재시도(40→20→10)."""
    last = None
    for cap in (40, 20, 10):
        batch = images[:cap]
        parts = [{"text": LEARN_PROMPT}] + [_img_part_bytes(b) for b in batch]
        try:
            prof = cbrain._call_parts(cfg, parts, max_tokens=4096,
                                      temperature=0.3, thinking=0)
        except Exception as e:
            last = e
            log(f"      학습 호출 실패({len(batch)}장): {str(e)[:80]} — 줄여서 재시도")
            continue
        if isinstance(prof, dict) and str(prof.get("flow") or "").strip():
            prof["cards_used"] = len(batch)
            return prof
        log(f"      결과 비어있음({len(batch)}장) — 줄여서 재시도")
    raise RuntimeError(f"스타일 분석 실패 — 카드 이미지를 줄여도 안 됐어요"
                       + (f" ({str(last)[:60]})" if last else ""))


# ── 변환 ─────────────────────────────────────────────────────
def convert_script(cfg, script, topic, profile=None):
    """쇼츠 대본 → 카드 텍스트 + 캡션. 내용은 유지, 형식만 카드뉴스체."""
    if profile:
        guide = ("[이 계정의 카드뉴스 전개방식 프로파일 — 반드시 이 방식을 따라라]\n"
                 + json.dumps({k: profile.get(k) for k in
                               ("flow", "hook_style", "card_count", "head_chars",
                                "body_chars", "cta_style", "tone")},
                              ensure_ascii=False) + "\n")
    else:
        guide = "[기본 전개] 1번 훅(표지) → 내용 카드 3~6장 → 마지막 CTA 카드.\n"
    prompt = (f"""너는 인스타 카드뉴스 작가다. 아래 '쇼츠 대본'의 내용을 그대로 살려서
카드뉴스(캐러셀 이미지 여러 장)용 텍스트로 형식만 바꿔라.
[소재] {topic}
[쇼츠 대본]
{str(script)[:1800]}

""" + guide + """규칙:
- 내용 창작 금지: 대본에 있는 정보만 재구성(새 사실/과장 추가 금지).
- 1번 카드(표지): label=상품/소재 이름 짧게(10자 내, 예: 모션 센서 간접등),
  head=후킹 큰 글씨 딱 두 줄(줄바꿈 \n로 구분, 각 줄 6~12자 — 짧을수록 좋다), body는 "".
- 2번부터(내지): head=첫 줄, body=둘째 줄 — 손글씨 감성 문장(각 8~18자), 공감형 존댓말 체험담.
- 마지막 카드는 CTA(저장/팔로우/댓글 유도) — 역시 head/body 두 줄.
- 이모지는 ❤️ 하나를 줄 끝에 아주 가끔만(표지 label이나 내지 강조 줄).
- caption: 인스타 캡션 — 훅 1줄 + 핵심 2줄 + CTA 1줄 + 해시태그 5개.
반드시 JSON만: {"cards":[{"label":"표지만","head":"...","body":"..."}],"caption":"..."}""")
    r = cbrain._call_parts(cfg, [{"text": prompt}], max_tokens=4096,
                           temperature=0.7, thinking=0)
    cards = [c for c in (r.get("cards") or [])
             if isinstance(c, dict) and str(c.get("head") or c.get("body") or "").strip()]
    if len(cards) < 3:
        raise RuntimeError("카드 텍스트 생성 실패(3장 미만) — 다시 시도해 주세요")
    return cards[:10], str(r.get("caption") or "").strip()


# ── 렌더 ─────────────────────────────────────────────────────
def _hex(c, fb):
    m = re.match(r"^#?([0-9a-fA-F]{6})$", str(c or "").strip())
    return "#" + m.group(1) if m else fb


def _rgb(hx):
    return tuple(int(hx[i:i + 2], 16) for i in (1, 3, 5))


_FEEL_FONTS = {
    "손글씨": ("oneprettynight", "ownglyph", "meetme", "gaegu", "hi melody",
             "himelody", "온글잎", "나눔손", "handwriting"),
    "둥근": ("jua", "dongle", "do hyeon", "dohyeon", "ssurround", "round"),
}


def _lib_font(base, feel):
    """자막 폰트 라이브러리에서 느낌에 맞는 폰트 파일 경로 탐색(없으면 None)."""
    kws = None
    for k, words in _FEEL_FONTS.items():
        if k in feel:
            kws = words
            break
    if not kws:
        return None
    try:
        for f in fontlib.list_fonts(base):
            nm = (str(f.get("family", "")) + " " + str(f.get("name", ""))).lower()
            if any(w in nm for w in kws):
                return str(fontlib.fdir(base) / f["file"])
    except Exception:
        pass
    return None


def _font(base, feel, size, bold=True):
    feel = str(feel or "")
    lib = _lib_font(base, feel)
    if lib:                       # 손글씨/둥근 → 라이브러리 폰트(굵기 구분 없이 같은 파일)
        return ImageFont.truetype(lib, size)
    d = Path(base) / "assets" / "fonts"
    if "픽셀" in feel:
        f = d / "neodgm.ttf"
    elif bold:
        f = d / ("Pretendard-ExtraBold.otf" if ("둥근" in feel or "손글씨" in feel)
                 else "BlackHanSans.ttf")
    else:
        f = d / "Pretendard-SemiBold.otf"
    return ImageFont.truetype(str(f), size)


_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\u2B00-\u2BFF\u2190-\u21FF\uFE0E\uFE0F\u200D\u200B\u2728\u2764]+")


_EMOJI_FONT = r"C:\Windows\Fonts\seguiemj.ttf"


def _cleanws(s):
    """공백만 정규화(이모지 유지 — 사진 카드는 컬러 이모지로 렌더)."""
    return re.sub(r"[^\S\n]+", " ", str(s or "")).strip()


def _emoji_runs(text):
    out = []
    for ch in str(text):
        if ch == "\ufe0f":
            continue
        e = bool(_EMOJI.match(ch))
        if out and out[-1][1] == e:
            out[-1][0] += ch
        else:
            out.append([ch, e])
    return [(s, e) for s, e in out if s]


def _emoji_font(size):
    try:
        return ImageFont.truetype(_EMOJI_FONT, int(size * 0.92))
    except Exception:
        return None


def _mixed_w(d, text, font, size, ef):
    w = 0
    for s, is_e in _emoji_runs(text):
        if is_e:
            if ef:
                for ch in s:
                    w += d.textlength(ch, font=ef) + int(size * 0.06)
        else:
            w += d.textlength(s, font=font)
    return w


def _mixed_draw(d, x, y, text, font, size, fill, ef, stroke=0, stroke_fill=(255, 255, 255)):
    """이모지 섞인 한 줄 그리기 — 이모지는 컬러 폰트(embedded_color)."""
    for s, is_e in _emoji_runs(text):
        if is_e:
            if ef:
                for ch in s:
                    try:
                        d.text((x, y + int(size * 0.05)), ch, font=ef, embedded_color=True)
                    except Exception:
                        pass
                    x += d.textlength(ch, font=ef) + int(size * 0.06)
        else:
            d.text((x, y), s, font=font, fill=fill,
                   stroke_width=stroke, stroke_fill=stroke_fill)
            x += d.textlength(s, font=font)
    return x


def _clean(s):
    """문구 정리 — 이모지(폰트에 글리프가 없어 투명한 틈으로 렌더됨) 제거 + 공백 정규화."""
    s = _EMOJI.sub(" ", str(s or ""))
    return re.sub(r"[^\S\n]+", " ", s).strip()


def _wrap(draw, text, font, maxw):
    lines = []
    for para in str(text).split("\n"):
        cur = ""
        for ch in para:
            t = cur + ch
            if cur and draw.textlength(t, font=font) > maxw:
                lines.append(cur)
                cur = ch.lstrip()
            else:
                cur = t
        lines.append(cur)
    return [ln for ln in lines if ln.strip()]


def _crop_45(img):
    """세로 영상 프레임 → 4:5(1080x1350) 크롭(살짝 위쪽 중심 — 얼굴/제품이 보통 상단)."""
    img = img.convert("RGB")
    w, h = img.size
    if w != W:
        nh = max(1, int(h * W / w))
        img = img.resize((W, nh))
        w, h = img.size
    if h > H:
        top = max(0, int((h - H) * 0.42))
        img = img.crop((0, top, W, top + H))
    elif h < H:
        canvas = Image.new("RGB", (W, H), (14, 16, 20))
        canvas.paste(img, (0, (H - h) // 2))
        img = canvas
    return img


def render_cards_photo(base, cards, visual, out_dir, frame_paths, handle=""):
    """참고 캐러셀 문법 재현:
    표지 = 사진 하단 왼쪽, 파스텔 라벨(손글씨) + 큰 라운드볼드 2줄(포인트색+흰색, 흰 테두리)
    내지 = 상단 중앙 반투명 흰 박스 + 손글씨 2줄. 이모지는 컬러 폰트로 그대로 렌더."""
    v = visual or {}
    accent = _hex(v.get("accent"), "#F9A8C9")
    label_bg = _hex(v.get("label_bg"), "#FFD9E3")
    hand_path = _lib_font(base, "손글씨")
    round_path = _lib_font(base, "둥근")
    bold_path = round_path or str(Path(base) / "assets" / "fonts" / "BlackHanSans.ttf")
    hand_path = hand_path or str(Path(base) / "assets" / "fonts" / "Pretendard-SemiBold.otf")

    frame_paths = list(frame_paths or [])
    if len(frame_paths) > 1:          # 표지가 어두운 인트로면 가장 밝은 프레임과 교체
        def _lum(path):
            try:
                im = Image.open(path).convert("L")
                im.thumbnail((64, 64))
                return ImageStat.Stat(im).mean[0]
            except Exception:
                return 0.0
        lums = [_lum(f) for f in frame_paths]
        if lums[0] < 50:
            k = max(range(len(lums)), key=lambda j: lums[j])
            frame_paths[0], frame_paths[k] = frame_paths[k], frame_paths[0]

    out = []
    for i, c in enumerate(cards, 1):
        fp = frame_paths[min(i - 1, len(frame_paths) - 1)] if frame_paths else None
        try:
            img = _crop_45(Image.open(fp)) if fp else Image.new("RGB", (W, H), (240, 238, 235))
        except Exception:
            img = Image.new("RGB", (W, H), (240, 238, 235))
        img = img.convert("RGBA")
        d = ImageDraw.Draw(img)

        if i == 1:
            # ── 표지: 라벨 + 큰 두 줄 (하단 왼쪽)
            label = _cleanws(c.get("label") or "")
            head_lines = [_cleanws(x) for x in str(c.get("head") or "").split("\n") if _cleanws(x)]
            if not head_lines:
                head_lines = [_cleanws(c.get("body") or "")]
            head_lines = head_lines[:2]
            margin = 70
            hsize = 92
            bf = ImageFont.truetype(bold_path, hsize)
            ef = _emoji_font(hsize)
            while hsize > 54 and any(
                    _mixed_w(d, ln, bf, hsize, ef) > W - margin * 2 for ln in head_lines):
                hsize -= 4
                bf = ImageFont.truetype(bold_path, hsize)
                ef = _emoji_font(hsize)
            hand_size = 48
            hf = ImageFont.truetype(hand_path, hand_size)
            hef = _emoji_font(hand_size)
            hlh = int(hsize * 1.18)
            y_last = H - 120 - hlh
            ys = [y_last - hlh * (len(head_lines) - 1 - k) for k in range(len(head_lines))]
            y_label = ys[0] - int(hand_size * 1.6)
            if label:
                lw = _mixed_w(d, label, hf, hand_size, hef)
                lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                ld = ImageDraw.Draw(lay)
                lb = _rgb(label_bg)
                ld.rounded_rectangle([margin - 14, y_label - 8,
                                      margin + lw + 18, y_label + int(hand_size * 1.25)],
                                     radius=10, fill=(lb[0], lb[1], lb[2], 235))
                img = Image.alpha_composite(img, lay)
                d = ImageDraw.Draw(img)
                _mixed_draw(d, margin, y_label, label, hf, hand_size, (58, 58, 58), hef)
            sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            sd = ImageDraw.Draw(sh)
            for k, ln in enumerate(head_lines):
                sd.text((margin + 4, ys[k] + 5), ln, font=bf,
                        fill=(0, 0, 0, 70 if k == 0 else 90))
            img = Image.alpha_composite(img, sh)
            d = ImageDraw.Draw(img)
            for k, ln in enumerate(head_lines):
                if k == 0 and len(head_lines) > 1:
                    _mixed_draw(d, margin, ys[k], ln, bf, hsize, _rgb(accent), ef,
                                stroke=max(5, hsize // 16), stroke_fill=(255, 255, 255))
                else:
                    _mixed_draw(d, margin, ys[k], ln, bf, hsize, (255, 255, 255), ef,
                                stroke=2, stroke_fill=(90, 90, 90))
        else:
            # ── 내지: 상단 중앙 반투명 흰 박스 + 손글씨 1~2줄
            lines = [_cleanws(c.get("head") or ""), _cleanws(c.get("body") or "")]
            lines = [x for x in lines if x][:2] or [""]
            size = 56
            hf = ImageFont.truetype(hand_path, size)
            ef = _emoji_font(size)
            while size > 40 and any(_mixed_w(d, ln, hf, size, ef) > W - 200 for ln in lines):
                size -= 3
                hf = ImageFont.truetype(hand_path, size)
                ef = _emoji_font(size)
            lh = int(size * 1.55)
            total = lh * len(lines)
            wmax = max(_mixed_w(d, ln, hf, size, ef) for ln in lines)
            y0 = int(H * 0.15)
            bw = min(W - 120, int(wmax) + 110)
            x0 = (W - bw) // 2
            lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ld = ImageDraw.Draw(lay)
            ld.rounded_rectangle([x0, y0 - 30, x0 + bw, y0 + total + 18],
                                 radius=16, fill=(255, 255, 255, 195))
            img = Image.alpha_composite(img, lay)
            d = ImageDraw.Draw(img)
            y = y0
            for ln in lines:
                lw = _mixed_w(d, ln, hf, size, ef)
                _mixed_draw(d, int((W - lw) // 2), y, ln, hf, size, (52, 52, 52), ef)
                y += lh
        hf2 = ImageFont.truetype(hand_path, 32)
        s = "@" + str(handle or "").lstrip("@") if handle else ""
        if s:
            d.text(((W - d.textlength(s, font=hf2)) // 2, H - 56), s, font=hf2,
                   fill=(255, 255, 255), stroke_width=1, stroke_fill=(120, 120, 120))
        name = f"{i:02d}.jpg"
        img.convert("RGB").save(str(Path(out_dir) / name), quality=92)
        out.append(name)
    return out


def render_cards(base, cards, visual, out_dir, handle=""):
    """카드 텍스트 목록 → out_dir/01..NN.jpg. 반환: 파일명 리스트."""
    v = visual or {}
    bg1 = _hex(v.get("bg"), "#12161C")
    bg2 = _hex(v.get("bg2"), bg1)
    txt = _hex(v.get("text"), "#F2F2F2")
    acc = _hex(v.get("accent"), "#FFD34D")
    align = "left" if str(v.get("align") or "").strip() == "left" else "center"
    feel = str(v.get("font_feel") or "고딕굵게")
    deco = str(v.get("deco") or "")
    out = []
    n = len(cards)
    for i, c in enumerate(cards, 1):
        img = Image.new("RGB", (W, H), _rgb(bg1))
        d = ImageDraw.Draw(img)
        if bg2 != bg1:                       # 세로 그라데이션
            c1, c2 = _rgb(bg1), _rgb(bg2)
            for y in range(H):
                t = y / (H - 1)
                d.line([(0, y), (W, y)],
                       fill=tuple(int(c1[k] + (c2[k] - c1[k]) * t) for k in range(3)))
        head = _clean(c.get("head"))
        body = _clean(c.get("body"))
        cover = (i == 1)
        hsize = 96 if cover else 74
        hf = _font(base, feel, hsize, bold=True)
        bf = _font(base, feel, 44, bold=False)
        maxw = W - 170
        hl = _wrap(d, head, hf, maxw) if head else []
        bl = _wrap(d, body, bf, maxw) if body else []
        hlh, blh = int(hsize * 1.3), int(44 * 1.6)
        total = len(hl) * hlh + (34 if hl and bl else 0) + len(bl) * blh
        y = max(120, (H - total) // 2)
        for ln in hl:
            x = 85 if align == "left" else (W - d.textlength(ln, font=hf)) // 2
            d.text((x, y), ln, font=hf, fill=_rgb(acc if (cover or not body) else txt))
            y += hlh
        if hl and bl:
            y += 34
        for ln in bl:
            x = 85 if align == "left" else (W - d.textlength(ln, font=bf)) // 2
            d.text((x, y), ln, font=bf, fill=_rgb(txt))
            y += blh
        if not cover and ("번호" in deco or "페이지" in deco):
            sf = _font(base, "", 30, bold=False)
            s = f"{i} / {n}"
            d.text(((W - d.textlength(s, font=sf)) // 2, H - 96), s,
                   font=sf, fill=_rgb(txt))
        if handle:
            sf = _font(base, "", 28, bold=False)
            s = "@" + str(handle).lstrip("@")
            d.text(((W - d.textlength(s, font=sf)) // 2, H - 52), s,
                   font=sf, fill=_rgb(acc))
        name = f"{i:02d}.jpg"
        img.save(str(Path(out_dir) / name), quality=92)
        out.append(name)
    return out
