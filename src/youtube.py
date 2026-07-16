# -*- coding: utf-8 -*-
"""유튜브 → 짤 소재 수집기.

- 메타/썸네일 즉시 조회 (다운로드 없음)
- Gemini가 유튜브 URL을 직접 보고 대본 + 뒷장 구성안(카드) 생성
- yt-dlp로 비디오-온리(<=1080p) 다운로드 (오디오·병합 불필요 → ffmpeg 병합 회피)
- imageio-ffmpeg 번들 ffmpeg로 프레임 풀 추출 + 선명도 필터
- 카드별로 대본 시점에 맞는 프레임 자동 배정 (리롤은 풀에서 즉시)

핵심: 대본은 다운로드 없이 URL로(빠름), 프레임만 다운로드해서 뽑는다.
"""
import glob
import os
import statistics
import subprocess
import time
from pathlib import Path

import requests

from . import brain, packer, thumbnail  # GEMINI_URL/_parse_json + 렌더/패킹 재사용

_ASSETS = Path(__file__).resolve().parent.parent / "assets" / "fonts"
CARD_W, CARD_H = 1080, 1350
CARD_MARGIN = 72

try:
    import yt_dlp
except Exception:  # pragma: no cover
    yt_dlp = None
try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:  # pragma: no cover
    FFMPEG = "ffmpeg"


def _key(cfg):
    return (cfg.get("gemini_api_key") or "").strip() or os.environ.get("GEMINI_API_KEY", "")


# ─────────────────────────── 1) 메타 / 썸네일 ───────────────────────────
def fetch_meta(url):
    """다운로드 없이 제목·길이·채널·썸네일 URL. 빠름(1~2초)."""
    if yt_dlp is None:
        raise RuntimeError("yt-dlp 가 설치되지 않았습니다 (pip install yt-dlp)")
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as y:
        info = y.extract_info(url, download=False)
    # 가장 큰 썸네일 고르기
    thumbs = sorted((info.get("thumbnails") or []),
                    key=lambda t: (t.get("width") or 0) * (t.get("height") or 0))
    thumb = thumbs[-1]["url"] if thumbs else info.get("thumbnail")
    return {
        "id": info.get("id"),
        "title": info.get("title") or "",
        "duration": info.get("duration") or 0,
        "uploader": info.get("uploader") or "",
        "thumbnail": thumb,
        "webpage_url": info.get("webpage_url") or url,
    }


def save_thumbnail(thumb_url, dest):
    """썸네일 URL → JPG 파일로 저장."""
    r = requests.get(thumb_url, timeout=30)
    r.raise_for_status()
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(r.content)).convert("RGB")
    img.save(dest, "JPEG", quality=92)
    return dest


# ─────────────────────────── 2) 대본 + 뒷장 구성안 (Gemini, URL 직접) ───────────────────────────
_YT_PROMPT = """너는 유튜브 쇼츠를 한국 인스타그램 '짤 뉴스' 게시물로 재구성하는 작가다.
아래 유튜브 영상을 직접 보고(자막 없으면 음성·화면을 근거로) 한국어 인스타 게시물을 구성하라.

## 출력 (반드시 이 JSON 형식만)
{{
  "skip": false,
  "skip_reason": "",
  "lang_src": "영상의 원래 언어 (예: ko/en/ja)",
  "transcript": "영상 대본을 한국어로 정리 (타임스탬프 없이 자연스러운 글)",
  "hooks": [
    {{"line1": "대표 썸네일 첫 줄", "line2": "썸네일 둘째 줄"}},
    {{"line1": "다른 공식 후보", "line2": "다른 공식 후보"}},
    {{"line1": "또 다른 공식 후보", "line2": "또 다른 공식 후보"}}
  ],
  "cards": [
    {{"text": "이 구간 대본을 최대한 살린 장문 (작은 글씨로 렌더됨, 2~5문장, \\n로 줄바꿈)", "t": 3.5}}
  ],
  "caption": "인스타 본문 전체 (한국어)"
}}

## 규칙 — ★ 이 영상은 '대본(설명) 중심' 쇼츠다. 뒷장의 목적은 대본 내용을 최대한 다 담는 것.
- cards 개수는 **적게**: 대본 전체를 의미 단위로 **3~6개 구간**으로 나눠라 (영상 길면 최대 6장).
- 각 card "text" = 그 구간의 대본을 **최대한 보존한 장문**(작은 글씨로 렌더된다). 2~5문장.
  설명의 정보(수치·방법·순서·이유)를 절대 빠뜨리거나 요약하지 마라.
- 구간 경계에서 문장이 잘리면 **시작·끝만 자연스럽게 다듬어라**(예: 이어지는 접속사 정리). 내용은 생략 금지.
  **뒷장 전체를 이어 붙이면 원본 대본 대부분이 복원돼야 한다.**
- 각 card "t" = 그 구간의 대표 시점(초, 0~{dur}). 순서대로 커지게. 프레임 배정에 쓰인다.
- 줄바꿈('\\n')은 문장/구 단위로 자연스럽게.
- hooks 3개는 서로 다른 공식(근황/현장형 · 질문/가정형 · 반전/의외형). line1 6~13자, line2 6~16자.
- 영상이 외국어면 전부 한국어로 '현지화'(직역 금지). 한국 커뮤 감성 후킹.
- caption: 1행 헤드라인 → 서브 한 줄 → 빈 줄 → 짧은 문단 3~5개 → 마지막 드립 한 줄. 400~800자. 이모지·해시태그 금지.
- skip 판정(정치/성적수위/참사조롱/신상)은 표시만 하고 hooks·cards·caption은 전부 작성.
"""

_YT_MOCK = {
    "skip": False, "skip_reason": "", "lang_src": "en",
    "transcript": "모의 실행: Gemini 없이 생성된 가짜 대본입니다.",
    "hooks": [{"line1": "유튜브 짤 테스트", "line2": "\"모의 실행 모드\""},
              {"line1": "이게 실전이라면?", "line2": "대본이 여기 들어간다"},
              {"line1": "평범해 보이지만", "line2": "사실 목업이었다"}],
    "cards": [{"text": "모의 카드 1", "t": 1.0}, {"text": "모의 카드 2", "t": 4.0},
              {"text": "모의 카드 3", "t": 8.0}],
    "caption": "모의 캡션 — --mock 으로 생성되었습니다.",
}


def transcribe_and_outline(cfg, url, duration=0, mock=False, log=print):
    """유튜브 URL을 Gemini에 직접 넣어 대본 + 뒷장 카드 구성안 + 후킹 + 캡션 생성."""
    if mock:
        import json
        return json.loads(json.dumps(_YT_MOCK))
    key = _key(cfg)
    if not key:
        raise RuntimeError("Gemini API 키가 없습니다 (config.json gemini_api_key)")
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    prompt = _YT_PROMPT.format(dur=duration or "끝")
    body = {
        "contents": [{"parts": [
            {"file_data": {"file_uri": url}},
            {"text": prompt},
        ]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.9,
            "maxOutputTokens": 8192,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    last_err = None
    for _ in range(2):
        resp = requests.post(brain.GEMINI_URL.format(model=model),
                             params={"key": key}, json=body, timeout=180)
        if resp.status_code != 200:
            raise RuntimeError(f"Gemini API 오류 {resp.status_code}: {resp.text[:300]}")
        try:
            cand = resp.json()["candidates"][0]
            raw = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
            if not raw and cand.get("finishReason") not in (None, "STOP"):
                raise RuntimeError(f"응답 없음(finishReason={cand.get('finishReason')})")
            r = brain._parse_json(raw)
            r.setdefault("skip", False)
            r.setdefault("hooks", [])
            r.setdefault("cards", [])
            r.setdefault("caption", "")
            # 카드 정규화
            cards = []
            for c in r.get("cards") or []:
                t = c.get("t", 0)
                try:
                    t = float(t)
                except Exception:
                    t = 0.0
                cards.append({"text": str(c.get("text", "")).strip(), "t": t})
            r["cards"] = [c for c in cards if c["text"]] or cards
            return r
        except Exception as e:
            last_err = e
    raise RuntimeError(f"Gemini 유튜브 응답 파싱 실패: {last_err}")


# ─────────────────────────── 3) 비디오-온리 다운로드 ───────────────────────────
def download_video(url, dest, max_height=1080, log=print):
    """오디오/병합 없이 비디오 스트림만 다운로드. 프레임 추출 전용."""
    if yt_dlp is None:
        raise RuntimeError("yt-dlp 가 설치되지 않았습니다")
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    opts = {
        "quiet": True, "no_warnings": True,
        "format": f"bv*[height<={max_height}]/b[height<={max_height}]/best",
        "outtmpl": dest, "overwrites": True,
        "retries": 3, "fragment_retries": 3,
    }
    t0 = time.time()
    with yt_dlp.YoutubeDL(opts) as y:
        y.download([url])
    if not os.path.exists(dest):
        raise RuntimeError("다운로드 실패 — 파일이 생성되지 않음")
    log(f"      다운로드 {time.time() - t0:.1f}s ({os.path.getsize(dest) / 1e6:.1f}MB)")
    return dest


# ─────────────────────────── 4) 프레임 풀 + 선명도 필터 ───────────────────────────
def _sharpness(path):
    """간이 선명도(가로 인접 픽셀 차분 분산) — 값 클수록 또렷."""
    from PIL import Image
    im = Image.open(path).convert("L").resize((160, 284))
    px = list(im.getdata())
    w = 160
    diffs = [abs(px[i] - px[i - 1]) for i in range(1, len(px)) if i % w]
    return statistics.pvariance(diffs) if diffs else 0.0


def _brightness(path):
    from PIL import Image
    im = Image.open(path).convert("L").resize((64, 64))
    d = list(im.getdata())
    return sum(d) / len(d)


def extract_frame_pool(video, out_dir, interval=0.7, target_h=1350,
                       min_sharp=60, min_bright=28, max_bright=250, log=print):
    """interval초마다 프레임 추출 → 선명도/밝기 필터 → [{path, t, sharp}] 정렬(선명 우선)."""
    os.makedirs(out_dir, exist_ok=True)
    for f in glob.glob(os.path.join(out_dir, "pool_*.jpg")):
        os.remove(f)
    pat = os.path.join(out_dir, "pool_%03d.jpg")
    t0 = time.time()
    cmd = [FFMPEG, "-y", "-i", video, "-vf", f"fps=1/{interval},scale=-2:{target_h}",
           "-q:v", "3", pat]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    raw = sorted(glob.glob(os.path.join(out_dir, "pool_*.jpg")))
    if not raw:
        raise RuntimeError(f"프레임 추출 실패: {r.stderr[-200:]}")
    pool = []
    for i, f in enumerate(raw):
        t = (i + 0.5) * interval            # 대략적 시점(초)
        s = _sharpness(f)
        b = _brightness(f)
        keep = (s >= min_sharp) and (min_bright <= b <= max_bright)
        pool.append({"path": f, "t": t, "sharp": s, "bright": b, "keep": keep})
    kept = [p for p in pool if p["keep"]] or pool   # 다 걸러지면 전체 사용
    log(f"      프레임 {len(raw)}장 → 선명 {len(kept)}장 사용 ({time.time() - t0:.1f}s)")
    return kept


def assign_frames(cards, pool):
    """카드별로 'card.t 에 가장 가까운' 프레임을 배정(중복 최소화). 각 카드에 후보 리스트도 부여."""
    remaining = sorted(pool, key=lambda p: p["t"])
    used = set()
    for c in cards:
        ct = c.get("t", 0)
        # t 근접 + 미사용 우선, 없으면 t 근접
        cand = sorted(remaining, key=lambda p: (p["path"] in used, abs(p["t"] - ct)))
        pick = cand[0] if cand else None
        c["frame"] = pick["path"] if pick else None
        if pick:
            used.add(pick["path"])
    return cards


def reroll_frame(card, pool, exclude=None):
    """리롤: card.t 근처에서 다른 선명 프레임을 하나 골라 교체(재다운로드 없음)."""
    exclude = set(exclude or [])
    if card.get("frame"):
        exclude.add(card["frame"])
    ct = card.get("t", 0)
    cand = [p for p in sorted(pool, key=lambda p: abs(p["t"] - ct)) if p["path"] not in exclude]
    if cand:
        card["frame"] = cand[0]["path"]
    return card


# ─────────── 5) 프레임 자막(고정 위치 글자) 감지 + 페더 가우시안 블러 ───────────
# 쇼츠 자막은 보통 화면의 '일정한 세로 위치'에 '너비만 다르게' 들어간다.
# → 프레임별로 자막 한 덩어리만 감지하고, 세로 위치·높이는 카드 전체에서 통일(공유 밴드),
#   너비만 프레임별로, 가장자리는 페더(부드럽게) 처리한다.
_CAPTION_PROMPT = """이 이미지에서 '영상 제작자가 얹은 자막(캡션) 글자'가 있으면,
그 주 자막 한 덩어리를 감싸는 바운딩 박스 하나만 반환하라.
- 쇼츠 자막은 보통 화면의 일정한 세로 위치에 가로로 들어간다. 여러 줄이면 전체를 하나로 감싼다.
- 워터마크·로고·UI 아이콘·배경 간판/제품에 원래 있던 글자는 제외. 오직 '얹은 자막 텍스트'만.
- 자막이 없으면 null.
JSON만: {"box": [ymin, xmin, ymax, xmax]} 또는 {"box": null}  (좌상단 0,0 기준, 0~1000 정규화)."""


def detect_caption_box(cfg, image_path, log=print):
    """프레임 속 '주 자막' 박스 하나 감지 → [ymin,xmin,ymax,xmax] (0~1000) 또는 None."""
    key = _key(cfg)
    if not key:
        return None
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    body = {
        "contents": [{"parts": [brain._inline_image(image_path), {"text": _CAPTION_PROMPT}]}],
        "generationConfig": {"response_mime_type": "application/json", "temperature": 0,
                             "maxOutputTokens": 512, "thinkingConfig": {"thinkingBudget": 0}},
    }
    try:
        resp = requests.post(brain.GEMINI_URL.format(model=model),
                             params={"key": key}, json=body, timeout=60)
        if resp.status_code != 200:
            return None
        cand = resp.json()["candidates"][0]
        raw = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
        box = brain._parse_json(raw).get("box")
        if isinstance(box, (list, tuple)) and len(box) == 4:
            return [float(x) for x in box]
        return None
    except Exception as e:
        log(f"      (자막 감지 건너뜀: {str(e)[:60]})")
        return None


def blur_region(src, box, dest=None, pad=0.012, blur_ratio=0.35, log=print):
    """box(0~1000) 영역을 일반 가우시안 블러(사각형, 페더 없음)로 처리해 저장.
    blur_ratio 는 영역 높이 대비 블러 세기 비율(자막 높이에 따라 자동 스케일)."""
    from PIL import Image, ImageFilter
    dest = dest or src
    im = Image.open(src).convert("RGB")
    W, H = im.size
    ymin, xmin, ymax, xmax = box
    x0 = int(max(0.0, xmin / 1000 - pad) * W)
    y0 = int(max(0.0, ymin / 1000 - pad) * H)
    x1 = int(min(1.0, xmax / 1000 + pad) * W)
    y1 = int(min(1.0, ymax / 1000 + pad) * H)
    if x1 - x0 < 3 or y1 - y0 < 3:
        im.save(dest, "JPEG", quality=90)
        return dest
    blur_r = max(10, int((y1 - y0) * blur_ratio))       # 글자 안 보이게 충분히
    region = im.crop((x0, y0, x1, y1)).filter(ImageFilter.GaussianBlur(blur_r))
    im.paste(region, (x0, y0))
    im.save(dest, "JPEG", quality=90)
    log(f"      자막 블러 → {os.path.basename(dest)}")
    return dest


_FALLBACK_W = (100.0, 900.0)   # 감지 실패 프레임: 전체 너비의 80%(중앙 10~90%) 고정 블러


def clean_card_frames(cfg, cards, out_dir, log=print):
    """카드 프레임 자막을 감지 → '공유 세로 밴드(위치·높이 통일) + 프레임별 너비'로 블러.
    감지 실패한 프레임은 세로 위치·높이는 공유 밴드로 동일, 너비만 전체폭 80% 고정으로 안전 블러."""
    import statistics
    from PIL import Image
    os.makedirs(out_dir, exist_ok=True)

    # 1) 프레임별 자막 박스 감지
    for c in cards:
        c["_cap"] = detect_caption_box(cfg, c["frame"]) if c.get("frame") else None

    # 2) 감지된 자막들로 공유 세로 밴드(위치·높이) 계산
    caps = [c["_cap"] for c in cards if c.get("_cap")]
    band = None
    if caps:
        band = (statistics.median(b[0] for b in caps),   # y0 중앙값
                statistics.median(b[2] for b in caps))    # y1 중앙값

    # 3) 자막이 하나라도 있으면(=자막형 영상) 모든 카드 프레임을 블러.
    #    감지된 프레임은 자기 너비, 감지 실패한 프레임은 전체폭 80% 고정 (세로는 공유 밴드로 통일).
    for i, c in enumerate(cards):
        fr = c.get("frame")
        if not fr:
            continue
        dest = os.path.join(out_dir, f"clean_{i:02d}.jpg")
        cap = c.get("_cap")
        if band:
            x0, x1 = (cap[1], cap[3]) if cap else _FALLBACK_W
            if not cap:
                log(f"      (카드{i + 1} 자막 감지 실패 → 공유 밴드+전체폭 80% 고정 블러)")
            region = [band[0], x0, band[1], x1]           # y=공유밴드, x=자기 or 폴백 너비
            blur_region(fr, region, dest, log=log)
            c["cap_region"] = region
            c["cap_detected"] = bool(cap)
        else:                                             # 감지된 자막이 전혀 없으면 원본
            Image.open(fr).convert("RGB").save(dest, "JPEG", quality=90)
            c["cap_region"] = None
        c["text_boxes"] = [c["cap_region"]] if c.get("cap_region") else []
        c["frame_clean"] = dest
        c.pop("_cap", None)
    return cards


# ─────────────────────────── 6) 렌더 (대표 썸네일 + 장문 뒷장) ───────────────────────────
_JA_FONTS = ["C:/Windows/Fonts/YuGothB.ttc", "C:/Windows/Fonts/meiryob.ttc",
             "C:/Windows/Fonts/msgothic.ttc"]


def _ja_font_path():
    for p in _JA_FONTS:
        if os.path.exists(p):
            return p
    return None


def _body_font(size, bold=False, lang="ko"):
    from PIL import ImageFont
    if lang == "ja":
        jp = _ja_font_path()
        if jp:
            try:
                return ImageFont.truetype(jp, size)
            except Exception:
                pass
    p = _ASSETS / ("Pretendard-ExtraBold.otf" if bold else "Pretendard-SemiBold.otf")
    if p.exists():
        return ImageFont.truetype(str(p), size)
    fb = "C:/Windows/Fonts/malgunbd.ttf"
    return ImageFont.truetype(fb, size) if os.path.exists(fb) else ImageFont.load_default(size)


def _cover_fill(img, tw, th):
    """이미지를 tw×th 를 꽉 채우도록 스케일 후 가운데 크롭."""
    from PIL import Image
    scale = max(tw / img.width, th / img.height)
    nw, nh = max(1, int(img.width * scale)), max(1, int(img.height * scale))
    img = img.resize((nw, nh), Image.LANCZOS)
    x, y = (nw - tw) // 2, (nh - th) // 2
    return img.crop((x, y, x + tw, y + th))


def _wrap_text(draw, text, font, max_w):
    """텍스트를 max_w 폭에 맞춰 줄바꿈(어절 우선, 긴 어절은 글자 단위). '\\n' 은 존중."""
    out = []
    for para in (text or "").split("\n"):
        if not para.strip():
            out.append("")
            continue
        line = ""
        for word in para.split(" "):
            chunk = (line + " " + word) if line else word
            if draw.textlength(chunk, font=font) <= max_w:
                line = chunk
                continue
            if line:
                out.append(line)
                line = ""
            if draw.textlength(word, font=font) <= max_w:
                line = word
            else:                                    # 한 어절이 너무 길면 글자 단위로
                cur = ""
                for ch in word:
                    if not cur or draw.textlength(cur + ch, font=font) <= max_w:
                        cur += ch
                    else:
                        out.append(cur)
                        cur = ch
                line = cur
        if line:
            out.append(line)
    return out


def render_back_card(frame, text, watermark, out_path, cfg=None, lang="ko"):
    """뒷장 카드: 프레임(4:5 꽉채움) 위에 대본 장문을 작은 글씨로 얹는다.
    frame 이 없으면 에메랄드 그라데이션 템플릿 배경(폴백)."""
    from PIL import Image, ImageDraw
    canvas = Image.new("RGB", (CARD_W, CARD_H), (12, 14, 22))
    if frame and Path(frame).exists():
        canvas.paste(_cover_fill(Image.open(frame).convert("RGB"), CARD_W, CARD_H), (0, 0))
    else:
        d0 = ImageDraw.Draw(canvas)
        for y in range(CARD_H):                      # 폴백: 에메랄드 세로 그라데이션
            t = y / CARD_H
            d0.line([(0, y), (CARD_W, y)],
                    fill=(int(14 + 12 * t), int(70 - 24 * t), int(56 - 8 * t)))
    draw = ImageDraw.Draw(canvas)

    # 폰트 자동 크기: 작게 시작(최대 44), 텍스트가 하단 45%(아래 절반)를 넘지 않게
    max_h = int(CARD_H * 0.45)
    size = 44
    while size >= 26:
        font = _body_font(size, lang=lang)
        lines = _wrap_text(draw, text, font, CARD_W - CARD_MARGIN * 2)
        lh = int(size * 1.4)
        if lh * len(lines) <= max_h:
            break
        size -= 2
    font = _body_font(size, lang=lang)
    lh = int(size * 1.4)
    lines = _wrap_text(draw, text, font, CARD_W - CARD_MARGIN * 2)
    block_h = lh * len(lines)

    # 하단 그라데이션(가독성)
    grad_h = min(CARD_H, block_h + 200)
    overlay = Image.new("L", (1, grad_h))
    for y in range(grad_h):
        overlay.putpixel((0, y), int(230 * (y / grad_h) ** 1.4))
    overlay = overlay.resize((CARD_W, grad_h))
    canvas.paste(Image.new("RGB", (CARD_W, grad_h), (0, 0, 0)), (0, CARD_H - grad_h), overlay)
    draw = ImageDraw.Draw(canvas)

    base_y = CARD_H - 130 - block_h
    for i, ln in enumerate(lines):
        y = base_y + i * lh
        draw.text((CARD_MARGIN + 2, y + 2), ln, font=font, fill=(0, 0, 0))     # 그림자
        draw.text((CARD_MARGIN, y), ln, font=font, fill=(255, 255, 255))
    if watermark:
        wf = _body_font(30, lang=lang)
        ww = draw.textlength(watermark, font=wf)
        draw.text(((CARD_W - ww) // 2, CARD_H - 74), watermark, font=wf, fill=(205, 205, 205))
    canvas.save(out_path, "JPEG", quality=93)
    return str(out_path)


def render_youtube_thumb(bg_path, line1, line2, watermark, out_path, lang="ko"):
    """대표 썸네일: 유튜브 썸네일(또는 프레임)을 4:5 꽉채움 배경으로 후킹 2줄 렌더.
    기존 thumbnail.render 재사용(가짜 커뮤 헤더 없이 자막형). lang='ja'면 일본어 폰트."""
    from PIL import Image
    tmp = str(Path(out_path).with_suffix(".bg.jpg"))
    if bg_path and Path(bg_path).exists():
        _cover_fill(Image.open(bg_path).convert("RGB"), CARD_W, CARD_H).save(tmp, "JPEG", quality=92)
    else:
        Image.new("RGB", (CARD_W, CARD_H), (18, 44, 36)).save(tmp, "JPEG", quality=92)
    fp = _ja_font_path() if lang == "ja" else None
    thumbnail.render(tmp, line1, line2, watermark, out_path, header=None, font_path=fp)
    try:
        Path(tmp).unlink()
    except Exception:
        pass
    return out_path


# ─────────────────────────── 7) 완성팩 생성 (CLI/서버 공용) ───────────────────────────
def build_from_youtube(url, cfg, base_dir, mock=False, log=print):
    """유튜브 URL → 짤 완성팩. 반환 형태는 pipeline.build_from_url 과 동일."""
    import json as _json
    import shutil
    import tempfile
    from datetime import datetime

    root = Path(base_dir) / cfg.get("output_dir", "결과물")
    root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="yt_", dir=root))
    try:
        log(f"[1/5] 유튜브 메타 조회... {url}")
        my = fetch_meta(url)
        log(f"      제목: {my['title']} ({my['duration']}s · {my['uploader']})")
        yt_thumb = work / "ytthumb.jpg"
        try:
            save_thumbnail(my["thumbnail"], str(yt_thumb))
        except Exception:
            yt_thumb = None

        log("[2/5] 대본·뒷장 구성안 (Gemini)...")
        outline = transcribe_and_outline(cfg, url, duration=my["duration"], mock=mock, log=log)
        hooks = [h for h in (outline.get("hooks") or [])
                 if h.get("line1") or h.get("line2")][:3] or [{"line1": my["title"][:13], "line2": ""}]
        cards = outline.get("cards") or []
        if outline.get("skip"):
            log(f"[참고] AI 의견: 민감 소재일 수 있음 — {outline.get('skip_reason')} (판단은 직접)")

        if not mock:
            log("[3/5] 영상 다운로드 + 프레임 추출 + 자막 블러...")
            vid = work / "video.mp4"
            try:
                download_video(url, str(vid), log=log)
                pool = extract_frame_pool(str(vid), str(work / "pool"), log=log)
                assign_frames(cards, pool)
                clean_card_frames(cfg, cards, str(work / "clean"), log=log)
            except Exception as e:
                log(f"      다운로드/프레임 실패 → 템플릿 배경으로 폴백: {e}")
            finally:
                try:
                    vid.unlink()                    # 원본 영상은 삭제(용량)
                except Exception:
                    pass

        log("[4/5] 렌더링 (대표 썸네일 3종 + 뒷장 장문)...")
        bg = None
        if yt_thumb and Path(yt_thumb).exists():
            bg = str(yt_thumb)
        elif cards:
            bg = cards[0].get("frame_clean") or cards[0].get("frame")
        thumb_paths = []
        for i, h in enumerate(hooks):
            tp = work / ("thumb.jpg" if i == 0 else f"thumb{i + 1}.jpg")
            render_youtube_thumb(bg, h.get("line1", ""), h.get("line2", ""),
                                 cfg.get("watermark", ""), str(tp))
            thumb_paths.append(tp)
        card_paths = []
        for i, c in enumerate(cards, 1):
            cp = work / f"card{i:02d}.jpg"
            render_back_card(c.get("frame_clean") or c.get("frame"),
                             c.get("text", ""), cfg.get("watermark", ""), str(cp), cfg)
            card_paths.append(str(cp))

        log("[5/5] 완성팩 패키징...")
        caption_full = (outline.get("caption") or "").strip()
        if cfg.get("hashtags"):
            caption_full += "\n\n" + cfg["hashtags"]
        if cfg.get("signature"):
            caption_full += "\n\n" + cfg["signature"]
        meta = {
            "title": my["title"] or hooks[0]["line1"],
            "site": "유튜브", "url": my["webpage_url"],
            "template": "youtube", "source": "youtube",
            "hooks": hooks,
            "skip": bool(outline.get("skip")), "skip_reason": outline.get("skip_reason", ""),
            "created": datetime.now().isoformat(timespec="seconds"),
        }
        pack = packer.build_pack(root, meta, card_paths, thumb_paths, caption_full)

        # 수입·수출 대비: 원천 데이터 + 깨끗한 프레임 보관 (frameNN.jpg, 업로드엔 안 나옴)
        source = {
            "source": "youtube", "url": my["webpage_url"], "video_id": my["id"],
            "lang_src": outline.get("lang_src", ""), "transcript": outline.get("transcript", ""),
            "hooks": hooks, "caption": outline.get("caption", ""),
            "cards": [{"text": c.get("text", ""), "t": c.get("t", 0)} for c in cards],
        }
        (Path(pack) / "source.json").write_text(
            _json.dumps(source, ensure_ascii=False, indent=2), encoding="utf-8")
        if yt_thumb and Path(yt_thumb).exists():      # 재수출 대표 썸네일 배경용
            try:
                shutil.copy(str(yt_thumb), str(Path(pack) / "ytthumb.jpg"))
            except Exception:
                pass
        for i, c in enumerate(cards, 1):
            fr = c.get("frame_clean") or c.get("frame")
            if fr and Path(fr).exists():
                try:
                    shutil.copy(str(fr), str(Path(pack) / f"frame{i:02d}.jpg"))
                except Exception:
                    pass
        return {"pack": pack, "meta": meta, "caption": caption_full,
                "num_images": len(card_paths), "num_thumbs": len(thumb_paths)}
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ─────────────────────────── 8) 인기 쇼츠 브라우징 (YouTube Data API) ───────────────────────────
_YT_DATA = "https://www.googleapis.com/youtube/v3"


def _yt_key(cfg):
    keys = cfg.get("youtube_api_keys") or []
    return (cfg.get("youtube_api_key") or (keys[0] if keys else "") or "").strip()


def _iso_seconds(iso):
    import re
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    h, mn, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mn * 60 + s


def _yt_item(it, region=""):
    sn = it.get("snippet", {}) or {}
    st = it.get("statistics", {}) or {}
    vid = it.get("id") if isinstance(it.get("id"), str) else (it.get("id", {}) or {}).get("videoId", "")
    th = (sn.get("thumbnails", {}) or {})
    thumb = (th.get("medium") or th.get("high") or th.get("default") or {}).get("url", "")
    return {"id": vid, "url": f"https://www.youtube.com/shorts/{vid}",
            "title": sn.get("title", ""), "channel": sn.get("channelTitle", ""),
            "views": int(st.get("viewCount", 0) or 0), "thumb": thumb, "region": region}


# 제외 카테고리: 1=영화·애니, 10=음악, 30=영화, 44=트레일러 (요청: 영화·드라마 트레일러·음악 제외)
_BLOCK_CATS = {"1", "10", "30", "44"}
# 제목에 이런 말이 들어가면 영화·드라마·음악 홍보물로 보고 제외
# (유튜브 트렌딩엔 예고편/뮤비가 Entertainment로 분류돼 섞이므로 제목으로 한 번 더 거른다)
_BLOCK_TITLE = [
    "trailer", "teaser", "official video", "official audio", "lyric",
    "music video", "m/v", " mv", "mv)", "concept film", "highlight film",
    "예고편", "예고", "티저", "뮤직비디오", "뮤비", "공식 영상", "공식영상",
    "official trailer", "ost", "라이브 클립", "live clip", "performance video",
]


def _is_short_clip(it, max_sec):
    """쇼츠로 볼 만한지: 60초 미만 + 음악/영화/트레일러 카테고리·제목 아님."""
    dur = _iso_seconds(it.get("contentDetails", {}).get("duration"))
    if dur == 0 or dur > max_sec:
        return False
    sn = it.get("snippet", {}) or {}
    if str(sn.get("categoryId", "")) in _BLOCK_CATS:
        return False
    title = (sn.get("title", "") or "").lower()
    if any(w in title for w in _BLOCK_TITLE):
        return False
    return True


_REGION_Q = {"KR": "쇼츠", "US": "shorts", "JP": "ショート"}
_REGION_LANG = {"KR": "ko", "US": "en", "JP": "ja"}


def trending_shorts(cfg, regions=("KR", "US", "JP"), per=10, max_sec=59):
    """지역별 '최근 인기 쇼츠'. (유튜브 mostPopular 차트는 쇼츠 피드가 아니라 음악/예고편/롱폼
    위주라, 검색 API의 videoDuration=short + 조회순 + 최근 14일로 실제 쇼츠를 모은다.)
    → 60초 미만 + 음악/영화/트레일러 카테고리·제목 제외."""
    import datetime as _dt
    key = _yt_key(cfg)
    if not key:
        raise RuntimeError("유튜브 API 키가 없습니다 (config youtube_api_key)")
    after = (_dt.datetime.utcnow() - _dt.timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = []
    for rg in regions:
        try:
            sr = requests.get(f"{_YT_DATA}/search",
                              params={"part": "snippet", "type": "video",
                                      "videoDuration": "short", "order": "viewCount",
                                      "q": _REGION_Q.get(rg, "shorts"),
                                      "regionCode": rg,
                                      "relevanceLanguage": _REGION_LANG.get(rg, "en"),
                                      "publishedAfter": after,
                                      "maxResults": 40, "key": key}, timeout=30)
            if sr.status_code != 200:
                continue
            ids = [it.get("id", {}).get("videoId") for it in sr.json().get("items", [])
                   if it.get("id", {}).get("videoId")]
            if not ids:
                continue
            vr = requests.get(f"{_YT_DATA}/videos",
                              params={"part": "snippet,contentDetails,statistics",
                                      "id": ",".join(ids[:50]), "key": key}, timeout=30)
            cand = [it for it in vr.json().get("items", []) if _is_short_clip(it, max_sec)]
            cand.sort(key=lambda it: int((it.get("statistics", {}) or {}).get("viewCount", 0) or 0),
                      reverse=True)
            out.extend(_yt_item(it, rg) for it in cand[:per])
        except Exception:
            continue
    return out


def _translate_query(cfg, query):
    """검색어 → 영어·일본어 (Gemini). 실패 시 빈 리스트."""
    key = _key(cfg)
    if not key:
        return []
    try:
        model = cfg.get("gemini_model", "gemini-2.5-flash")
        prompt = ('유튜브 검색어를 영어와 일본어로 번역하라. JSON만: {"en":"...","ja":"..."}\n검색어: '
                  + query)
        resp = requests.post(brain.GEMINI_URL.format(model=model), params={"key": key},
                             json={"contents": [{"parts": [{"text": prompt}]}],
                                   "generationConfig": {"response_mime_type": "application/json",
                                                        "temperature": 0, "maxOutputTokens": 256,
                                                        "thinkingConfig": {"thinkingBudget": 0}}},
                             timeout=30)
        cand = resp.json()["candidates"][0]
        j = brain._parse_json("".join(p.get("text", "") for p in cand["content"]["parts"]))
        return [x for x in [j.get("en"), j.get("ja")] if x]
    except Exception:
        return []


def search_shorts(cfg, query, count=30, translate=True, max_sec=59):
    """검색어로 인기 쇼츠 검색(조회순, 60초 미만 · 음악/영화/트레일러 제외). 한국어면 영/일로도 번역해 합침."""
    key = _yt_key(cfg)
    if not key:
        raise RuntimeError("유튜브 API 키가 없습니다 (config youtube_api_key)")
    queries = [query] + (_translate_query(cfg, query) if translate else [])
    per = max(5, min(50, count // len(queries) + 5))
    seen, ids = set(), []
    for q in queries:
        try:
            r = requests.get(f"{_YT_DATA}/search",
                             params={"part": "snippet", "type": "video",
                                     "videoDuration": "short", "order": "viewCount",
                                     "q": q, "maxResults": per, "key": key}, timeout=30)
            if r.status_code != 200:
                continue
            for it in r.json().get("items", []):
                vid = (it.get("id", {}) or {}).get("videoId")
                if vid and vid not in seen:
                    seen.add(vid)
                    ids.append(vid)
        except Exception:
            continue
    ids = ids[:count]
    if not ids:
        return []
    out = []
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        try:
            r = requests.get(f"{_YT_DATA}/videos",
                             params={"part": "snippet,contentDetails,statistics",
                                     "id": ",".join(chunk), "key": key}, timeout=30)
            for it in r.json().get("items", []):
                if _is_short_clip(it, max_sec):
                    out.append(_yt_item(it))
        except Exception:
            continue
    out.sort(key=lambda v: v["views"], reverse=True)
    return out


# ─────────────────────────── 9) 유튜브 짤 수출 (번역 + 재렌더) ───────────────────────────
def _translate_source(cfg, src, lang_label):
    """source.json(후킹·카드·캡션)을 도착 언어로 번역. 반환 {title, hooks, cards, caption}."""
    import json as _json
    key = _key(cfg)
    if not key:
        raise RuntimeError("Gemini API 키가 없습니다")
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    payload = {"hooks": src.get("hooks", []),
               "cards": [c.get("text", "") for c in src.get("cards", [])],
               "caption": src.get("caption", "")}
    prompt = (f"아래 한국어 인스타 '짤 뉴스' 콘텐츠를 {lang_label}로 자연스럽게 번역·현지화하라. "
              f"{lang_label} 원어민이 처음부터 쓴 것처럼(번역투 금지). 구조·개수·의미 유지.\n"
              '출력 JSON만: {"title":"짧은 제목(파일명용)","hooks":[{"line1":"..","line2":".."}],'
              '"cards":["장별 문구",".."],"caption":"인스타 본문"}\n\n입력:\n'
              + _json.dumps(payload, ensure_ascii=False))
    resp = requests.post(brain.GEMINI_URL.format(model=model), params={"key": key},
                         json={"contents": [{"parts": [{"text": prompt}]}],
                               "generationConfig": {"response_mime_type": "application/json",
                                                    "temperature": 0.7, "maxOutputTokens": 4096,
                                                    "thinkingConfig": {"thinkingBudget": 0}}},
                         timeout=150)
    if resp.status_code != 200:
        raise RuntimeError(f"번역 API 오류 {resp.status_code}")
    cand = resp.json()["candidates"][0]
    j = brain._parse_json("".join(p.get("text", "") for p in cand["content"]["parts"]))
    return {"title": j.get("title", ""), "hooks": j.get("hooks", []),
            "cards": j.get("cards", []), "caption": j.get("caption", "")}


def build_youtube_translated(pack_dir, cfg, base_dir, target="ja", log=print):
    """유튜브 짤 팩(source.json+frameNN.jpg) → 도착 언어로 번역·재렌더한 새 팩."""
    import json as _json
    import shutil
    import tempfile
    from datetime import datetime
    pd = Path(pack_dir)
    try:
        src = _json.loads((pd / "source.json").read_text(encoding="utf-8"))
    except Exception:
        raise RuntimeError("유튜브 짤 팩이 아니에요 (source.json 없음)")
    if src.get("source") != "youtube":
        raise RuntimeError("유튜브 짤만 수출 가능해요")
    lang_label = {"ja": "일본어", "en": "영어"}.get(target, "일본어")

    log(f"[1/3] {lang_label} 번역...")
    tr = _translate_source(cfg, src, lang_label)
    hooks = (tr.get("hooks") or src.get("hooks", []))[:3]
    cards_txt = tr.get("cards") or [c.get("text", "") for c in src.get("cards", [])]

    root = Path(base_dir) / cfg.get("output_dir", "결과물")
    root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="ytx_", dir=root))
    try:
        log("[2/3] 렌더링 (대표 썸네일 + 뒷장)...")
        bg = pd / "ytthumb.jpg"
        if not bg.exists():
            bg = pd / "frame01.jpg"
        bg = str(bg) if bg.exists() else None
        thumb_paths = []
        for i, h in enumerate(hooks or [{"line1": tr.get("title", ""), "line2": ""}]):
            tp = work / ("thumb.jpg" if i == 0 else f"thumb{i + 1}.jpg")
            render_youtube_thumb(bg, h.get("line1", ""), h.get("line2", ""),
                                 cfg.get("watermark", ""), str(tp), lang=target)
            thumb_paths.append(tp)
        card_paths = []
        for i, text in enumerate(cards_txt, 1):
            fr = pd / f"frame{i:02d}.jpg"
            cp = work / f"card{i:02d}.jpg"
            render_back_card(str(fr) if fr.exists() else None, text,
                             cfg.get("watermark", ""), str(cp), cfg, lang=target)
            card_paths.append(str(cp))

        log("[3/3] 완성팩 패키징...")
        caption = tr.get("caption", "") or src.get("caption", "")
        meta = {"title": tr.get("title") or (src.get("url", "")[:20]),
                "site": "유튜브", "url": src.get("url", ""),
                "template": "youtube", "source": "youtube", "lang": target,
                "hooks": hooks, "skip": False, "skip_reason": "",
                "created": datetime.now().isoformat(timespec="seconds")}
        pack = packer.build_pack(root, meta, card_paths, thumb_paths, caption)
        newsrc = dict(src)
        newsrc.update({"lang_src": target, "caption": caption, "hooks": hooks,
                       "cards": [{"text": t} for t in cards_txt]})
        (Path(pack) / "source.json").write_text(
            _json.dumps(newsrc, ensure_ascii=False, indent=2), encoding="utf-8")
        for i in range(1, len(cards_txt) + 1):     # 재수출 대비 프레임 복사
            fr = pd / f"frame{i:02d}.jpg"
            if fr.exists():
                shutil.copy(str(fr), str(Path(pack) / f"frame{i:02d}.jpg"))
        if (pd / "ytthumb.jpg").exists():
            shutil.copy(str(pd / "ytthumb.jpg"), str(Path(pack) / "ytthumb.jpg"))
        return {"pack": pack, "meta": meta, "caption": caption,
                "cards": [Path(c).name for c in card_paths],
                "num_images": len(card_paths), "num_thumbs": len(thumb_paths)}
    finally:
        shutil.rmtree(work, ignore_errors=True)
