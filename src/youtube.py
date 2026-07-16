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

import requests

from . import brain  # GEMINI_URL, _parse_json 재사용

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
    {{"text": "이 장 프레임 위에 얹을 짧은 문구 (한 줄 12~24자, 필요시 \\n로 두 줄)", "t": 3.5}}
  ],
  "caption": "인스타 본문 전체 (한국어)"
}}

## 규칙
- cards 개수는 **영상 길이·내용에 맞춰 자동으로** 정하라: 대략 5~8초당 1장, 최소 3장, 최대 8장.
- 각 card의 "t" = 그 문구가 대응하는 영상 시점(초, 0~{dur}). 순서대로 커지게. 프레임 배정에 쓰인다.
- card "text"는 프레임 위에 얹는 자막이므로 짧고 임팩트 있게. 대사 인용은 큰따옴표.
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
