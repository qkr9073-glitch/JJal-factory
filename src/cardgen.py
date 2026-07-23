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
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

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
- examples: 카드에 실제로 적힌 문구를 그대로 6개 옮겨 적기(표지 큰 글씨 2개 + 내지 문구 3개 + 마지막 CTA 1개 — 말투/호흡 학습용)
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


def _dhash(im):
    im2 = im.resize((9, 8))
    px = list(im2.getdata())
    bits = 0
    for r in range(8):
        for c in range(8):
            bits = (bits << 1) | (1 if px[r * 9 + c] > px[r * 9 + c + 1] else 0)
    return bits


def _ham(a, b):
    return bin(a ^ b).count("1")


def filter_frames(frame_files, min_keep=12):
    """비전에 보내기 전 사전 필터: 어두운 프레임·흐릿한 프레임·비슷한 연속 장면 제거."""
    scored = []
    for fp in frame_files:
        try:
            im = Image.open(fp).convert("L")
            im.thumbnail((160, 160))
            bright = ImageStat.Stat(im).mean[0]
            sharp = ImageStat.Stat(im.filter(ImageFilter.FIND_EDGES)).mean[0]
            scored.append((fp, bright, sharp, _dhash(im)))
        except Exception:
            continue
    kept = []
    for fp, b, s, h in scored:               # 중복 장면(해시 유사) 제거 — 후보 다양성 확보
        if any(_ham(h, h2) <= 6 for _, _, _, h2 in kept):
            continue
        kept.append((fp, b, s, h))
    good = [k for k in kept if k[1] >= 40]   # 어두운 프레임 컷
    if len(good) < min_keep:
        good = sorted(kept, key=lambda x: -x[1])[:min_keep]
    if len(good) > min_keep:                 # 여유 있으면 선명도 하위 20%도 컷
        thr = sorted(x[2] for x in good)[max(0, len(good) // 5)]
        g2 = [k for k in good if k[2] >= thr]
        if len(g2) >= min_keep:
            good = g2
    return [k[0] for k in good]


MATCH_PROMPT = """이미지들은 쇼츠 영상에서 뽑은 프레임 후보다(각 이미지 앞에 [프레임 N] 번호).
그 아래 카드뉴스 각 장의 문구가 있다. 각 카드에 가장 어울리는 프레임을 골라라.
기준:
- 밝고 선명하며 제품/장면이 또렷하게 보이는 프레임 우선. 어둡거나 흐릿한(모션블러) 프레임은 제외.
- 카드 문구의 내용과 장면이 맞아야 한다(설치 얘기면 설치 장면, 사용 후기는 사용 장면).
- 1번(표지)은 제품 전체가 잘 보이는 가장 예쁜 장면.
- 여러 카드가 같은 프레임을 1순위로 쓰지 않도록 분산하라.
각 카드마다 어울리는 순서대로 프레임 번호 4개.
반드시 JSON만: {"picks":[[3,7,1,9],[...]]} (카드 수만큼, 각각 4개)"""


def match_frames(cfg, cards, frame_files, log=print):
    """카드 문구 ↔ 프레임 비전 매칭. 반환: 카드별 프레임 인덱스 후보 목록(순위순)."""
    n = len(cards)
    texts = []
    for i, c in enumerate(cards):
        s = " ".join(str(c.get(k) or "").replace("\n", " ").replace("|", " ")
                     for k in ("label", "head", "body")).strip()
        texts.append(f"{i + 1}번 카드: {s}")
    parts = [{"text": MATCH_PROMPT + "\n\n[카드 문구]\n" + "\n".join(texts)}]
    for i, fp in enumerate(frame_files):
        parts.append({"text": f"[프레임 {i}]"})
        parts.append(_img_part_bytes(Path(fp).read_bytes(), max_side=360))
    fallback = _bright_order(frame_files)
    try:
        r = cbrain._call_parts(cfg, parts, max_tokens=2048, temperature=0.2, thinking=0)
        picks = r.get("picks") or []
    except Exception as e:
        log(f"      프레임 매칭 실패(밝기순 폴백): {str(e)[:80]}")
        picks = []
    out = []
    for i in range(n):
        cand = []
        if i < len(picks) and isinstance(picks[i], list):
            cand = [int(x) for x in picks[i]
                    if isinstance(x, (int, float)) and 0 <= int(x) < len(frame_files)]
        seen = set(cand)
        for j in fallback:                    # 비거나 부족하면 밝기순으로 채움
            if len(cand) >= 4:
                break
            if j not in seen:
                cand.append(j)
                seen.add(j)
        out.append(cand[:6])
    used = set()                             # 1순위 배경이 카드끼리 겹치지 않게 강제 배정
    for cand in out:
        pick = next((f for f in cand if f not in used), cand[0] if cand else None)
        if pick is not None:
            if pick in cand:
                cand.remove(pick)
            cand.insert(0, pick)
            used.add(pick)
    return out


def _bright_order(frame_files):
    """밝기 내림차순 프레임 인덱스(비전 실패 폴백)."""
    lums = []
    for i, fp in enumerate(frame_files):
        try:
            im = Image.open(fp).convert("L")
            im.thumbnail((64, 64))
            lums.append((ImageStat.Stat(im).mean[0], i))
        except Exception:
            lums.append((0.0, i))
    return [i for _, i in sorted(lums, reverse=True)]


# ── 변환 ─────────────────────────────────────────────────────
def convert_script(cfg, script, topic, profile=None):
    """쇼츠 대본 → 카드 텍스트 + 캡션. 내용은 유지, 형식만 카드뉴스체."""
    if profile:
        guide = ("[이 계정의 카드뉴스 전개방식 프로파일 — 반드시 이 방식을 따라라]\n"
                 + json.dumps({k: profile.get(k) for k in
                               ("flow", "hook_style", "card_count", "head_chars",
                                "body_chars", "cta_style", "tone")},
                              ensure_ascii=False) + "\n")
        ex = [str(x).strip() for x in (profile.get("examples") or []) if str(x).strip()]
        if ex:
            guide += ("[이 계정 카드의 실제 문구 예시 — 이 말투·호흡·어미를 그대로 흉내내라]\n"
                      + "\n".join(f"- {x}" for x in ex[:8]) + "\n")
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
  head=후킹 큰 글씨 딱 두 줄을 '|' 문자로 구분(예: "밤길 불안 끝ㄷㄷ|이게 뭐라고 삶의 질 상승",
  각 줄 6~12자 — 짧을수록 좋다. 줄바꿈 문자 금지, 반드시 '|'), body는 "".
- 2번부터(내지): head=첫 줄, body=둘째 줄 — 손글씨 감성 문장(각 8~18자), 공감형 존댓말 체험담.
- 마지막 카드는 CTA: 소재에서 짧은 키워드 하나를 정해 head=댓글 키워드 유도 큰 글씨(예: 댓글에 '조명' 남겨줘!), body=작은 안내 한 줄(예: 제품 정보 바로 보내드려요). caption의 댓글 키워드도 반드시 같은 단어로.
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


def regen_card(cfg, script, topic, cards, idx, profile=None):
    """카드 1장 문구만 재생성(다른 카드 문구와 겹치지 않게)."""
    n = len(cards)
    role = "표지(훅)" if idx == 1 else ("CTA(저장/팔로우/댓글 유도)" if idx == n else "내지")
    others = "\n".join(
        f"{i + 1}번: " + " ".join(str(cards[i].get(k) or "").replace("\n", " ").replace("|", " ")
                                  for k in ("label", "head", "body")).strip()
        for i in range(n) if i != idx - 1)
    guide = ""
    if profile:
        tone = str(profile.get("tone") or "").strip()
        if tone:
            guide += f"[말투] {tone}\n"
        ex = [str(x).strip() for x in (profile.get("examples") or []) if str(x).strip()]
        if ex:
            guide += "[실제 문구 예시 — 이 말투·호흡 그대로]\n" + "\n".join(f"- {x}" for x in ex[:6]) + "\n"
    if idx == 1:
        spec = "label=상품/소재 이름 짧게(10자 내), head=후킹 큰 글씨 두 줄을 '|'로 구분(각 6~12자, 줄바꿈 금지), body=\"\""
    elif idx == n:
        spec = ("head=댓글 키워드 유도 큰 글씨(예: 댓글에 '조명' 남겨줘! — 기존 키워드가 있으면 그대로), "
                "body=작은 안내 한 줄(8~16자), label=\"\"")
    else:
        spec = "head=첫 줄(8~18자), body=둘째 줄(8~18자) — 공감형 존댓말 체험담, label=\"\""
    cur = " · ".join(f"{k}={str(cards[idx - 1].get(k) or '').strip()}"
                     for k in ("label", "head", "body")
                     if str(cards[idx - 1].get(k) or "").strip())
    prompt = f"""너는 인스타 카드뉴스 작가다. 아래 대본을 근거로 {idx}번 카드({role})의 문구를 다듬어 다시 써라.
[소재] {topic}
[대본]
{str(script)[:1500]}
[현재 문구 — 이 내용과 의미를 유지한 채 표현만 새로(A→A′). 쓰인 이모지도 가급적 그대로 유지]
{cur}
[다른 카드 문구 — 내용이 겹치지 않게]
{others}
{guide}규칙: 내용 창작 금지(대본에 있는 정보만), 현재 문구와 같은 메시지를 더 좋은 표현으로. {spec}
반드시 JSON만: {{"label":"...","head":"...","body":"..."}}"""
    r = cbrain._call_parts(cfg, [{"text": prompt}], max_tokens=1024, temperature=0.9, thinking=0)
    out = {k: str(r.get(k) or "").strip() for k in ("label", "head", "body")}
    if not (out["head"] or out["body"]):
        raise RuntimeError("생성 결과가 비었어요 — 다시 시도하세요")
    return out


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


def _mixed_w(d, text, font, size, ef, track=0.0):
    w = 0
    for s, is_e in _emoji_runs(text):
        if is_e:
            if ef:
                for ch in s:
                    w += d.textlength(ch, font=ef) + int(size * 0.06)
        elif track:
            for ch in s:
                w += d.textlength(ch, font=font) - size * track
        else:
            w += d.textlength(s, font=font)
    return w


def _mixed_draw(d, x, y, text, font, size, fill, ef, stroke=0, stroke_fill=(255, 255, 255), track=0.0):
    """이모지 섞인 한 줄 그리기 — 이모지는 컬러 폰트(embedded_color). track=자간 축소 비율."""
    for s, is_e in _emoji_runs(text):
        if is_e:
            if ef:
                for ch in s:
                    try:
                        d.text((x, y + int(size * 0.05)), ch, font=ef, embedded_color=True)
                    except Exception:
                        pass
                    x += d.textlength(ch, font=ef) + int(size * 0.06)
        elif track:
            for ch in s:
                d.text((x, y), ch, font=font, fill=fill,
                       stroke_width=stroke, stroke_fill=stroke_fill)
                x += d.textlength(ch, font=font) - size * track
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


def render_card_photo_one(base, card, visual, out_path, frame_path, handle="", cover=False, cta=False):
    """카드 1장 렌더(표지/내지) — 리롤·일괄 공용."""
    v = visual or {}
    accent = _hex(v.get("accent"), "#F9A8C9")
    label_bg = _hex(v.get("label_bg"), "#FFD9E3")
    hand_path = _lib_font(base, "손글씨") or str(Path(base) / "assets" / "fonts" / "Pretendard-SemiBold.otf")
    round_path = _lib_font(base, "둥근")
    bold_path = round_path or str(Path(base) / "assets" / "fonts" / "BlackHanSans.ttf")
    try:
        img = _crop_45(Image.open(frame_path)) if frame_path else Image.new("RGB", (W, H), (240, 238, 235))
    except Exception:
        img = Image.new("RGB", (W, H), (240, 238, 235))
    img = img.convert("RGBA")
    d = ImageDraw.Draw(img)
    if cover:
        label = _cleanws(card.get("label") or "")
        head_lines = [_cleanws(x) for x in re.split(r"[|\n]", str(card.get("head") or "")) if _cleanws(x)]
        if not head_lines:
            head_lines = [_cleanws(card.get("body") or "")]
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
    elif cta:
        # ── CTA 전용: 중앙 큰 흰 패널 + 파스텔 띠 + 포인트색 라운드볼드 + 핸들
        small = _cleanws(card.get("body") or "")
        big_lines = [_cleanws(x) for x in re.split(r"[|\n]", str(card.get("head") or "")) if _cleanws(x)][:2]
        if not big_lines:
            big_lines = ["저장하고 팔로우!"]
        bsize = 74
        bf = ImageFont.truetype(bold_path, bsize)
        ef = _emoji_font(bsize)
        while bsize > 46 and any(_mixed_w(d, ln, bf, bsize, ef) > W - 280 for ln in big_lines):
            bsize -= 4
            bf = ImageFont.truetype(bold_path, bsize)
            ef = _emoji_font(bsize)
        sf = ImageFont.truetype(hand_path, 40)
        sef = _emoji_font(40)
        hf3 = ImageFont.truetype(hand_path, 38)
        blh = int(bsize * 1.24)
        inner = (int(40 * 1.55) if small else 0) + len(big_lines) * blh + 20 + int(38 * 1.35)
        wmax = max([_mixed_w(d, ln, bf, bsize, ef) for ln in big_lines]
                   + ([_mixed_w(d, small, sf, 40, sef)] if small else [0]))
        bw = min(W - 110, int(wmax) + 160)
        bh = inner + 108
        x0, y0 = (W - bw) // 2, (H - bh) // 2
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(lay)
        ld.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=34, fill=(255, 255, 255, 210))
        lb = _rgb(label_bg)
        ld.rounded_rectangle([x0 + 26, y0, x0 + bw - 26, y0 + 12], radius=6,
                             fill=(lb[0], lb[1], lb[2], 255))     # 상단 파스텔 띠
        img = Image.alpha_composite(img, lay)
        d = ImageDraw.Draw(img)
        y = y0 + 50
        if small:
            lw = _mixed_w(d, small, sf, 40, sef)
            _mixed_draw(d, int((W - lw) // 2), y, small, sf, 40, (95, 95, 95), sef)
            y += int(40 * 1.55)
        for ln in big_lines:
            lw = _mixed_w(d, ln, bf, bsize, ef)
            _mixed_draw(d, int((W - lw) // 2), y, ln, bf, bsize, _rgb(accent), ef,
                        stroke=max(4, bsize // 18), stroke_fill=(255, 255, 255))
            y += blh
        y += 18
        s2 = ("@" + str(handle).lstrip("@")) if handle else ""
        if s2:
            lw = d.textlength(s2, font=hf3)
            d.text((int((W - lw) // 2), y), s2, font=hf3, fill=_rgb(accent))
    else:
        lines = [_cleanws(card.get("head") or ""), _cleanws(card.get("body") or "")]
        lines = [x for x in lines if x][:2] or [""]
        size = 58
        TR = 0.05
        hf = ImageFont.truetype(hand_path, size)
        ef = _emoji_font(size)
        while size > 40 and any(_mixed_w(d, ln, hf, size, ef, TR) > W - 170 for ln in lines):
            size -= 3
            hf = ImageFont.truetype(hand_path, size)
            ef = _emoji_font(size)
        lh = int(size * 1.3)
        total = lh * len(lines)
        wmax = max(_mixed_w(d, ln, hf, size, ef, TR) for ln in lines)
        y0 = int(H * 0.15)
        bw = min(W - 120, int(wmax) + 56)
        x0 = (W - bw) // 2
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(lay)
        ld.rounded_rectangle([x0, y0 - 18, x0 + bw, y0 + total + 10],
                             radius=14, fill=(255, 255, 255, 195))
        img = Image.alpha_composite(img, lay)
        d = ImageDraw.Draw(img)
        y = y0
        for ln in lines:
            lw = _mixed_w(d, ln, hf, size, ef, TR)
            _mixed_draw(d, int((W - lw) // 2), y, ln, hf, size, (52, 52, 52), ef, track=TR)
            y += lh
    if handle and not cta:
        hf2 = ImageFont.truetype(hand_path, 32)
        s = "@" + str(handle).lstrip("@")
        d.text(((W - d.textlength(s, font=hf2)) // 2, H - 56), s, font=hf2,
               fill=(255, 255, 255), stroke_width=1, stroke_fill=(120, 120, 120))
    img.convert("RGB").save(str(out_path), quality=92)


def render_cards_photo(base, cards, visual, out_dir, frame_paths, handle=""):
    """카드 전체 렌더 — frame_paths는 카드별 배경(카드 수와 같은 길이 권장)."""
    frame_paths = list(frame_paths or [])
    out = []
    for i, c in enumerate(cards, 1):
        fp = frame_paths[min(i - 1, len(frame_paths) - 1)] if frame_paths else None
        name = f"{i:02d}.jpg"
        render_card_photo_one(base, c, visual, Path(out_dir) / name, fp,
                              handle=handle, cover=(i == 1),
                              cta=(i == len(cards) and len(cards) > 1))
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
