# -*- coding: utf-8 -*-
"""레퍼런스 채널 자동 수집·형식 학습 (MF-001).

핸들만 등록하면 공식 API(business_discovery)로 게시물을 수집해
Gemini 비전으로 '형식 프리셋'(표지 공식·전개·캡션 톤·수위)을 뽑고,
기존 스타일/템플릿 프리셋으로도 등록해 제작소에서 바로 쓰게 한다.
전 과정 공식 API만 사용(비공식 스크랩 금지 — 계정 리스크 0 원칙).

+ 한국 소재 소싱(suggest_krjp): 커뮤 인기글+뉴스RSS를 역수출(일본 타겟) 4축으로 분류.
"""
import base64
import io
import json
import re
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from PIL import Image

from src import styles

GRAPH = "https://graph.facebook.com/v23.0"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_LOCK = threading.Lock()

REFS_DIRNAME = "레퍼런스"
REFS_FILE = "references.json"


# ---------------- config 저장 (BOM 금지 — 반드시 python으로만) ----------------

def _config_update(base, updates):
    """config.json에 일부 키만 병합 저장. 파일을 새로 읽어 다른 세션 변경을 보존한다."""
    p = Path(base) / "config.json"
    cfg = json.loads(p.read_text(encoding="utf-8-sig"))
    cfg.update(updates)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


# ---------------- 토큰 60일 자동 갱신 ----------------

def refresh_token_if_needed(cfg, base, log=print, max_age_days=30):
    """fb_long_token이 30일 넘게 묵으면 새 60일 토큰으로 교환(공식 fb_exchange_token).
    갱신 실패해도 기존 토큰으로 계속 진행(만료 전이면 동작)."""
    last = (cfg.get("fb_token_refreshed") or "").strip()
    try:
        age = (datetime.now() - datetime.fromisoformat(last)).days if last else 999
    except ValueError:
        age = 999
    if age < max_age_days:
        return cfg
    app_id = str(cfg.get("fb_app_id", "")).strip()
    secret = str(cfg.get("fb_app_secret", "")).strip()
    token = str(cfg.get("fb_long_token", "")).strip()
    if not (app_id and secret and token):
        return cfg
    try:
        r = requests.get(f"{GRAPH}/oauth/access_token",
                         params={"grant_type": "fb_exchange_token",
                                 "client_id": app_id, "client_secret": secret,
                                 "fb_exchange_token": token}, timeout=30)
        new = r.json().get("access_token", "")
        if new:
            cfg = _config_update(base, {"fb_long_token": new,
                                        "fb_token_refreshed": datetime.now().date().isoformat()})
            log(f"[토큰] 60일 토큰 갱신 완료 (이전 {age}일 경과)")
        else:
            log(f"[토큰] 갱신 실패(기존 토큰 유지): {str(r.json())[:120]}")
    except Exception as e:
        log(f"[토큰] 갱신 예외(기존 토큰 유지): {e}")
    return cfg


# ---------------- 수집 (business_discovery) ----------------

BD_FIELDS = ("business_discovery.username({h})"
             "{{username,name,biography,followers_count,media_count,"
             "media.limit({n}){{caption,media_type,media_url,permalink,"
             "like_count,comments_count,timestamp,children{{media_url,media_type}}}}}}")


def _refs_root(base):
    d = Path(base) / REFS_DIRNAME
    d.mkdir(exist_ok=True)
    return d


def registry_load(base):
    try:
        return json.loads((_refs_root(base) / REFS_FILE).read_text(encoding="utf-8"))
    except Exception:
        return {}


def registry_save(base, reg):
    with _LOCK:
        (_refs_root(base) / REFS_FILE).write_text(
            json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")


def collect(cfg, base, handle, limit=30, log=print):
    """채널 프로필+최근 게시물 수집 → raw.json + 상위 게시물 이미지 다운로드.
    반환: (bd프로필dict, stats dict)"""
    token = (cfg.get("fb_long_token") or "").strip()
    igid = str(cfg.get("fb_bd_ig_id", "")).strip()
    if not (token and igid):
        raise RuntimeError("레퍼런스 토큰이 없습니다 — REFERENCE-SETUP.md 참고")
    log(f"[1/4] @{handle} 게시물 수집 중...")
    r = requests.get(f"{GRAPH}/{igid}",
                     params={"fields": BD_FIELDS.format(h=handle, n=limit),
                             "access_token": token}, timeout=60)
    body = r.json()
    if "error" in body:
        msg = body["error"].get("message", "")
        if "cannot be found" in msg or "does not exist" in msg:
            raise RuntimeError(f"@{handle} 조회 불가 — 프로페셔널(비즈니스/크리에이터) 계정만 가능")
        raise RuntimeError(f"조회 실패: {msg[:160]}")
    bd = body.get("business_discovery", {})
    media = bd.get("media", {}).get("data", [])
    if not media:
        raise RuntimeError(f"@{handle} 게시물을 받지 못했습니다")

    d = _refs_root(base) / handle
    (d / "img").mkdir(parents=True, exist_ok=True)
    (d / "raw.json").write_text(json.dumps(bd, ensure_ascii=False, indent=2),
                                encoding="utf-8")
    stats = _stats(media)

    # 좋아요 상위 게시물: 표지 8장 + 상위 3개는 전체 장 (형식·후킹 시퀀스 학습 + 리메이크 재료)
    log("[2/4] 대표 이미지 내려받는 중...")
    for f in (d / "img").glob("*.jpg"):
        f.unlink()  # 이전 수집분 청소(형식이 바뀌었을 수 있음)
    top = sorted(media, key=lambda m: m.get("like_count", 0), reverse=True)
    saved = 0
    for rank, m in enumerate(top[:8], 1):
        if m.get("media_type") == "VIDEO":
            continue
        mid = str(m.get("id", ""))
        url = m.get("media_url")
        if mid and url and _download(url, d / "img" / f"{mid}.jpg"):
            saved += 1
        if rank <= 3:
            children = (m.get("children") or {}).get("data", [])
            for j, c in enumerate(children[1:10], 2):   # 표지 제외 최대 9장 = 장별 전략 분석 재료
                if c.get("media_type") == "IMAGE" and c.get("media_url"):
                    if _download(c["media_url"], d / "img" / f"{mid}_p{j}.jpg"):
                        saved += 1
    # 게시물 목록(리메이크 UI용) — 좋아요순
    posts = []
    for m in top:
        mid = str(m.get("id", ""))
        posts.append({
            "id": mid,
            "type": m.get("media_type", ""),
            "like": m.get("like_count", 0),
            "comments": m.get("comments_count", 0),
            "ts": (m.get("timestamp") or "")[:10],
            "caption": (m.get("caption") or "").strip()[:300],
            "permalink": m.get("permalink", ""),
            "img": f"{mid}.jpg" if (d / "img" / f"{mid}.jpg").exists() else "",
            "pages": len((m.get("children") or {}).get("data", [])),
        })
    (d / "posts.json").write_text(json.dumps(posts, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    log(f"[2/4] 이미지 {saved}장 저장 · 게시물 {len(posts)}개 목록화")
    return bd, stats


def _download(url, path):
    try:
        raw = requests.get(url, timeout=30).content
        Image.open(io.BytesIO(raw)).convert("RGB")  # 이미지 검증
        path.write_bytes(raw)
        return True
    except Exception:
        return False


def _stats(media):
    ts = []
    for m in media:
        t = (m.get("timestamp") or "").replace("+0000", "+00:00")
        try:
            ts.append(datetime.fromisoformat(t))
        except ValueError:
            pass
    ts.sort()
    days = (ts[-1] - ts[0]).total_seconds() / 86400 if len(ts) > 1 else 0
    likes = sorted((m.get("like_count", 0) for m in media), reverse=True)
    comms = [m.get("comments_count", 0) for m in media]
    pages = [len((m.get("children") or {}).get("data", []))
             for m in media if m.get("media_type") == "CAROUSEL_ALBUM"]
    fmt = {}
    for m in media:
        fmt.setdefault(m.get("media_type", "?"), []).append(m.get("like_count", 0))
    return {
        "count": len(media),
        "per_day": round((len(ts) - 1) / days, 1) if days else 0,
        "like_median": likes[len(likes) // 2] if likes else 0,
        "like_top": likes[0] if likes else 0,
        "comment_ratio": round(sum(comms) / max(sum(likes), 1) * 100, 1),
        "carousel_pages": round(sum(pages) / len(pages), 1) if pages else 0,
        "formats": {k: {"n": len(v), "avg_like": round(sum(v) / len(v))}
                    for k, v in fmt.items()},
    }


# ---------------- 형식 분석 (Gemini 비전) ----------------

FORMAT_PROMPT = """당신은 인스타그램 캐러셀 채널 분석가다. 첨부한 것은 채널 @{handle}의
좋아요 상위 게시물 이미지들(표지·안쪽 장)과 캡션 샘플, 운영 수치다.
이 채널의 '형식'을, 우리 제작기가 같은 형식으로 새 콘텐츠를 만들 수 있게 정밀 분석하라.

운영 수치: {stats_line}

캡션 샘플:
{captions}

주의:
- 특정 게시물 내용을 베끼는 게 아니라 '형식(포맷)'을 배우는 것이다.
- 수위 분석이 중요하다: 이 채널이 논쟁·도발을 어느 수위까지 쓰는지 구체적으로.
  (우리 원칙: 그 수위는 유지하되 국가·집단 혐오/비하 프레임은 절대 배제)
- 표지를 AI 이미지로 만들 때 쓸 지침도 뽑아라 (글자는 우리가 따로 얹으니 글자 없는 장면 묘사).

JSON만 출력:
{{
  "name": "형식 이름 (한글 12자 이내)",
  "summary": "채널 형식 한 줄 요약 (50자 이내)",
  "cover_formula": "표지 공식 — 사진 종류·텍스트 배치·줄수·장치(원형 줌/배지/서브라인 등) 구체적으로 3~5문장",
  "inner_formula": "안쪽 장 공식 — 장수·구성(스토리/인포그래픽/순위 등)·마지막 장 CTA 2~4문장",
  "caption_tone": "캡션 형식 — 시작 기호·문체·길이·해시태그·CTA 2~3문장",
  "caption_blueprint": "캡션을 그대로 따라 쓸 수 있는 단계 설계도 — ①오프닝(이모지·첫 문장 패턴) ②본문 전개 방식 ③맺음/CTA ④해시태그(개수·고정 태그 유무), 각 단계 1줄씩",
  "topic_axes": ["소재 축 3~6개 (예: 해외 화제, 참여형 순위, ...)"],
  "engagement_formula": "댓글·저장을 부르는 장치 분석 2~3문장",
  "level_guide": "수위 지침 — 이 채널의 도발/논쟁 수위를 우리가 유지하는 방법. 혐오 배제 원칙 포함 2~3문장",
  "ai_cover_style": "표지용 AI 이미지 생성 지침 (글자 없는 장면·스타일 묘사, 영문 프롬프트 힌트 포함) 2~3문장",
  "guide": "[참고 형식] 으로 시작하는, 카드뉴스 생성 프롬프트에 그대로 주입할 지침 문단. 위 요소를 5~8개 불릿(- )으로. 내용은 우리 소재를 쓰되 형식만 따른다는 점 명시."
}}"""


HOOK_PROMPT = """당신은 인스타그램 캐러셀 '후킹 전략' 분석가다. 첨부한 것은 채널 @{handle}의
좋아요 상위 게시물들 — 각 게시물의 표지부터 마지막 장까지 순서대로다 (n-m장 = 게시물n의 m번째 장).

임무: 이 채널을 통째로 복제할 수 있는 수준으로 해부하라. 사용자가 항목을 짚어주지 않아도
빠짐없이 — 아래 스키마에 없는 특이한 무기가 보이면 extra_notes에 반드시 담아라.
① 표지 텍스트 후킹 — 어떤 유형의 문구로 스크롤을 멈추는가 (유형별 비중·실제 예문·작동 원리)
② 이미지 후킹 — 사진 자체가 시선을 잡는 장치 (구도·표정·순간포착·색 대비·연출 등)
③ 장별 시퀀스 — 1장부터 마지막 장까지 각 장이 맡는 역할과, 그 장의 텍스트 전략·이미지 전략.
   특정 게시물 내용이 아니라 여러 게시물에 공통되는 '시퀀스 공식(구조)'을 뽑아라.
④ 타이포그래피 — 표지·안쪽 장의 텍스트 크기(화면 폭 대비 대략 %)·줄수·배치·굵기·강조 장치
⑤ 대본 전개 — 문체(구어/문어·존댓말)·문장 길이·서술 시점·감정 곡선·스토리 전개 형식
⑥ 지속률 장치 — 마지막 장까지 넘기게 만드는 것 (클리프행어, 결말 숨기기, 중간 반전 배치 등)
⑦ 결(톤) 분포 — 이 채널이 쓰는 정서의 배합 (예: 충격/유머/공감/비판/자부심/기묘함 비중)
⑧ 수위 코드 — 섹시 코드·야한 드립을 쓰는지, 쓴다면 어떤 소재·방식·수위·비중인지

JSON만 출력:
{{
  "hook_styles": [
    {{"type": "후킹 유형명 (예: 충격 반전형, 8자 이내)", "share": "대략 비중 (예: 40%)",
      "example": "실제 표지 문구 그대로 1개", "how": "이 유형이 클릭을 부르는 원리 1문장"}},
    "... 비중 큰 순 3~5개"
  ],
  "image_hooks": [
    {{"device": "장치명 (예: 감정 표정 클로즈업, 12자 이내)", "how": "어떻게 쓰는지 + 어떤 장에서 쓰는지 1문장"}},
    "... 3~5개"
  ],
  "sequence": [
    {{"page": "장 번호 (예: 1, 2, 3~4, 마지막)", "role": "역할명 (예: 충격 후킹, 6자 이내)",
      "text": "이 장의 텍스트 전략 1문장", "image": "이 장의 이미지 전략 1문장"}},
    "... 표지→안쪽→마지막 순 4~6단계 (비슷한 역할의 연속 장은 '3~4'처럼 묶기)"
  ],
  "sequence_summary": "시퀀스 공식 한 줄 (예: 충격 표지 → 맥락 → 고조 → 반전 → 의견 CTA)",
  "typography": {{"cover": "표지 텍스트 — 크기(화면 폭 대비 %)·줄수·위치·굵기·색 1~2문장",
    "inner": "안쪽 장 텍스트 — 크기·위치·본문 유무 1~2문장",
    "accent": "강조 장치 — 따옴표·색 강조·배지·밑줄 등 1문장"}},
  "script_style": "대본 전개 형식 — 문체·문장 길이·서술 시점·감정 곡선·전개 방식 2~3문장",
  "retention": [
    {{"device": "지속 장치명 (12자 이내)", "how": "어떻게 다음 장을 넘기게 하는지 1문장"}},
    "... 3~4개"
  ],
  "tone_mix": "결(톤) 배합 한 줄 (예: 충격 50% + 유머 30% + 공감 20%, 어떤 소재에 어떤 결)",
  "headline_stats": "표지 카피 통계 — 평균 글자수·줄수·기호 사용 경향(따옴표/느낌표/물음표/말줄임) 1~2문장",
  "signature_keywords": "표지 카피에 반복 노출되는 시그니처 키워드 실측 — 국가명·고정 단어 등이 몇 장 중 몇 장에 박히는지와 그 역할 1문장 (예: '일본' 8장 중 5장 — 채널 정체성 시그널), 없으면 '없음'",
  "spice": "섹시 코드·야한 드립 사용 분석 — 쓴다면 어떤 소재에서 어떤 방식(암시·언어유희·노출 연출 등)·어느 수위·비중까지 구체적으로 1~2문장, 안 쓰면 '사용 안 함'",
  "extra_notes": ["스키마 밖의 특이한 무기·장치 발견 시 (예: 캡처 박스 삽입, 시리즈 연속극, 업로드 시간대) 0~4개"],
  "hook_guide": "제작 프롬프트에 그대로 주입할 후킹 지침 — '- ' 불릿 5~8개. 표지 문구 작법(비중 1위 유형 중심), 장별 역할 순서, 이미지 연출, 대본 문체, 지속률 장치를 명령형으로."
}}"""


def analyze_hooks(cfg, base, handle, log=print):
    """상위 게시물의 전체 장(표지→마지막)을 순서대로 비전 분석 → 후킹 전략 + 장별 시퀀스.
    collect()가 posts.json과 img/{id}(_pN).jpg를 만들어 둔 뒤에 호출한다."""
    key = (cfg.get("gemini_api_key") or "").strip()
    if not key:
        raise RuntimeError("Gemini API 키가 없습니다")
    d = _refs_root(base) / handle
    parts = [{"text": HOOK_PROMPT.format(handle=handle)}]
    n_posts = 0
    for p in _posts_load(base, handle):
        if n_posts >= 3 or p.get("type") == "VIDEO" or not p.get("img"):
            continue
        mid = str(p.get("id"))
        pages = [d / "img" / f"{mid}.jpg"] + sorted(
            (d / "img").glob(f"{mid}_p*.jpg"),
            key=lambda f: int(re.search(r"_p(\d+)", f.name).group(1)))
        pages = [f for f in pages if f.exists()]
        if not pages:
            continue
        n_posts += 1
        parts.append({"text": f"\n[게시물{n_posts} ♥{p.get('like', 0)} · "
                              f"총 {p.get('pages') or len(pages)}장 중 {len(pages)}장 첨부 · "
                              f"캡션: {(p.get('caption') or '')[:150]}]"})
        for j, f in enumerate(pages[:10], 1):
            parts.append({"text": f"{n_posts}-{j}장:"})
            parts.append(_inline(f.read_bytes(), max_side=768))
    if not n_posts:
        raise RuntimeError("후킹 분석용 게시물 이미지가 없습니다")
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"response_mime_type": "application/json",
                                 "temperature": 0.4, "maxOutputTokens": 4096,
                                 "thinkingConfig": {"thinkingBudget": 0}}}
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    last_err = ""
    for attempt in range(3):
        resp = requests.post(GEMINI_URL.format(model=model),
                             params={"key": key}, json=body, timeout=180)
        if resp.status_code == 200:
            break
        last_err = f"{resp.status_code}: {resp.text[:160]}"
        if resp.status_code not in (429, 500, 503):
            break
        import time as _t
        _t.sleep(8 * (attempt + 1))
    else:
        resp = None
    if resp is None or resp.status_code != 200:
        raise RuntimeError(f"Gemini 오류 {last_err}")
    hooks = _parse_json(resp.json()["candidates"][0]["content"]["parts"][0]["text"])
    if not (hooks.get("sequence") or hooks.get("hook_styles")):
        raise RuntimeError("후킹 시퀀스를 뽑지 못했습니다")
    return hooks


WINNERS_PROMPT = """아래는 채널 @{handle}의 최근 게시물 목록(좋아요 수·캡션 앞부분)이다.
각 게시물을 소재 축과 후킹 유형으로 분류만 하라 (평가 금지, 전부 분류).
소재 축 후보: {axes}
후킹 유형 후보: {hooktypes}
후보에 안 맞으면 "기타". JSON만 출력:
{{"posts": [{{"n": 1, "axis": "...", "hook": "..."}}]}}

목록:
{listing}"""


def _winners(cfg, handle, media, rep, log=print):
    """최근 게시물 30개를 소재·후킹별로 분류 → 그룹별 평균 좋아요 실측 (데이터 승리 공식)."""
    posts = [m for m in media if (m.get("caption") or "").strip()][:30]
    if len(posts) < 8:
        return None
    listing = "\n".join(
        f"{i + 1}. ♥{m.get('like_count', 0)} | {(m.get('caption') or '').strip()[:90]}"
        for i, m in enumerate(posts))
    axes = ", ".join(rep.get("topic_axes") or []) or "자유 분류"
    hooktypes = ", ".join(h.get("type", "") for h in
                          (rep.get("hooks") or {}).get("hook_styles", [])) or "자유 분류"
    key = (cfg.get("gemini_api_key") or "").strip()
    body = {"contents": [{"role": "user", "parts": [{"text": WINNERS_PROMPT.format(
                handle=handle, axes=axes, hooktypes=hooktypes, listing=listing)}]}],
            "generationConfig": {"response_mime_type": "application/json",
                                 "temperature": 0.2, "maxOutputTokens": 4096,
                                 "thinkingConfig": {"thinkingBudget": 0}}}
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    resp = requests.post(GEMINI_URL.format(model=model), params={"key": key},
                         json=body, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"승리 공식 분류 실패 {resp.status_code}")
    cls = _parse_json(resp.json()["candidates"][0]["content"]["parts"][0]["text"])
    likes = [m.get("like_count", 0) for m in posts]
    med = sorted(likes)[len(likes) // 2] or 1
    ax, hk = {}, {}
    for c in cls.get("posts", []):
        try:
            i = int(c.get("n")) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(posts):
            ax.setdefault(str(c.get("axis") or "기타"), []).append(likes[i])
            hk.setdefault(str(c.get("hook") or "기타"), []).append(likes[i])

    def _top(d):
        rows = [{"name": k, "n": len(v), "avg": round(sum(v) / len(v))}
                for k, v in d.items() if len(v) >= 2 and k != "기타"]
        return sorted(rows, key=lambda r: -r["avg"])[:5]
    return {"median": med, "by_axis": _top(ax), "by_hook": _top(hk)}


BRANCHES_PROMPT = """아래는 채널 @{handle}의 최근 게시물 목록(좋아요·댓글·캡션 앞부분)이다.
이 채널이 실제로 굴리는 '콘텐츠 갈래'(내용 유형)를 스스로 발견해 4~8개로 명명하고,
모든 게시물을 배정하라. 미리 정한 틀에 끼워 맞추지 말 것 — 논쟁·폭로·비판 같은 센 갈래만
보지 말고 **꿀팁(실용 정보)/재미 스토리(사연·해프닝)/겉과 다른 속(이미지와 다른 실제
일상·반전)/미담** 같은 부드러운 갈래도 놓치지 마라 — 채널이 잘되는 건 센 것과 부드러운
것의 배합 때문일 수 있다.
각 갈래에:
- name: 갈래 이름 (8자 이내)
- desc: 뭘 하는 갈래인지 한 줄
- why: 왜 먹히는지 (독자 심리) 한 줄
- formula: 구성 공식 한 줄 (표지→전개→마무리)
JSON만 출력:
{{"branches": [{{"name":"...","desc":"...","why":"...","formula":"..."}}],
  "assign": [{{"n":1,"branch":"갈래 이름"}}]}}

목록:
{listing}"""


def _branches(cfg, handle, media, log=print):
    """콘텐츠 갈래 지도 — AI가 갈래를 발견·배정, 갈래별 ♥·💬 평균은 파이썬 실측."""
    posts = [m for m in media if (m.get("caption") or "").strip()][:30]
    if len(posts) < 8:
        return None

    def _lk(m):
        return m.get("like_count", m.get("like", 0)) or 0

    def _cm(m):
        return m.get("comments_count", m.get("comments", 0)) or 0

    listing = "\n".join(
        f"{i + 1}. ♥{_lk(m)} 💬{_cm(m)} | {(m.get('caption') or '').strip()[:120]}"
        for i, m in enumerate(posts))
    key = (cfg.get("gemini_api_key") or "").strip()
    body = {"contents": [{"role": "user", "parts": [{"text": BRANCHES_PROMPT.format(
                handle=handle, listing=listing)}]}],
            "generationConfig": {"response_mime_type": "application/json",
                                 "temperature": 0.3, "maxOutputTokens": 4096,
                                 "thinkingConfig": {"thinkingBudget": 0}}}
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    resp = requests.post(GEMINI_URL.format(model=model), params={"key": key},
                         json=body, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"갈래 지도 분석 실패 {resp.status_code}")
    data = _parse_json(resp.json()["candidates"][0]["content"]["parts"][0]["text"])
    groups = {}
    for a in data.get("assign", []):
        try:
            i = int(a.get("n")) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(posts):
            groups.setdefault(str(a.get("branch") or "기타"), []).append(posts[i])
    types = []
    for b in data.get("branches", []):
        name = str(b.get("name") or "").strip()
        ps = groups.get(name, [])
        if not name or not ps:
            continue
        top = max(ps, key=_lk)
        types.append({
            "name": name, "desc": b.get("desc", ""), "why": b.get("why", ""),
            "formula": b.get("formula", ""), "n": len(ps),
            "share": round(len(ps) * 100 / len(posts)),
            "avg_like": round(sum(_lk(p) for p in ps) / len(ps)),
            "avg_comments": round(sum(_cm(p) for p in ps) / len(ps), 1),
            "top_caption": (top.get("caption") or "").strip()[:60]})
    types.sort(key=lambda t: -t["avg_like"])
    return types or None


HIT_PROMPT = """당신은 인스타 히트작 분석가다. 아래는 채널 @{handle}에서 성과가 폭발한
게시물들이다 (채널 좋아요 중앙값 {median} 대비 몇 배인지 표기, 표지 이미지 첨부).

각 히트작이 '왜 터졌는지'를 해부하고, 우리가 새 게시물을 만들 때 그대로 써먹을 교훈을 뽑아라.
좋아요형(공감·저장)과 댓글형(논쟁·참여)은 터진 이유가 다르다 — 구분해서 분석하라.

JSON만 출력:
{{"hits": [
  {{"n": 1, "why": "터진 이유 해부 1~2문장 (소재·후킹·이미지·감정 버튼)",
    "type": "좋아요형|댓글형|둘다",
    "lesson": "제작에 바로 적용할 교훈 1줄 (명령형)"}}
]}}"""


def _hits(cfg, base, handle, media, log=print):
    """좋아요·댓글 아웃라이어 게시물 개별 해부 — 왜 터졌는지 + 제작 교훈."""
    scored = [m for m in media if m.get("media_type") != "VIDEO"]
    if len(scored) < 8:
        return None
    likes = sorted(m.get("like_count", 0) for m in scored)
    med_l = likes[len(likes) // 2] or 1
    comms = sorted(m.get("comments_count", 0) for m in scored)
    med_c = comms[len(comms) // 2] or 1
    picks, seen = [], set()
    for m in sorted(scored, key=lambda x: -x.get("like_count", 0))[:2]:
        if m.get("like_count", 0) >= med_l * 2:
            picks.append(m)
            seen.add(m.get("id"))
    for m in sorted(scored, key=lambda x: -x.get("comments_count", 0))[:2]:
        if m.get("id") not in seen and m.get("comments_count", 0) >= med_c * 2.5:
            picks.append(m)
            seen.add(m.get("id"))
    picks = picks[:3]
    if not picks:
        return None
    d = _refs_root(base) / handle
    parts = [{"text": HIT_PROMPT.format(handle=handle, median=med_l)}]
    metas = []
    for i, m in enumerate(picks, 1):
        mid = str(m.get("id"))
        lk, cm = m.get("like_count", 0), m.get("comments_count", 0)
        parts.append({"text": f"\n[히트작{i}] ♥{lk}(중앙값 {round(lk / med_l, 1)}배) · "
                              f"💬{cm}({round(cm / med_c, 1)}배) · "
                              f"캡션: {(m.get('caption') or '').strip()[:200]}"})
        f = d / "img" / f"{mid}.jpg"
        if f.exists():
            parts.append(_inline(f.read_bytes(), max_side=768))
        metas.append({"id": mid, "like": lk, "comments": cm,
                      "mult_like": round(lk / med_l, 1),
                      "mult_comm": round(cm / med_c, 1),
                      "caption": (m.get("caption") or "").strip()[:120]})
    key = (cfg.get("gemini_api_key") or "").strip()
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"response_mime_type": "application/json",
                                 "temperature": 0.3, "maxOutputTokens": 2048,
                                 "thinkingConfig": {"thinkingBudget": 0}}}
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    resp = requests.post(GEMINI_URL.format(model=model), params={"key": key},
                         json=body, timeout=150)
    if resp.status_code != 200:
        raise RuntimeError(f"히트작 해부 실패 {resp.status_code}")
    out = _parse_json(resp.json()["candidates"][0]["content"]["parts"][0]["text"])
    hits = []
    for i, h in enumerate(out.get("hits", [])[:len(metas)]):
        if isinstance(h, dict):
            hits.append({**metas[i], "why": str(h.get("why", "")),
                         "type": str(h.get("type", "")),
                         "lesson": str(h.get("lesson", ""))})
    return hits or None


PLAYBOOK_PROMPT = """당신은 인스타 채널 @{handle}의 수석 기획자다. 아래는 이 채널의 1차 분석
결과(형식·후킹·시퀀스·데이터)와 실제로 폭발한 히트작들이다 (히트작 표지 이미지 첨부).

임무: 잘된 기획을 한 번 더 파고들어 '재사용 가능한 승리 기획 플레이북'으로 강화하라.
각 플레이는 새 소재가 와도 그대로 끼워 넣을 수 있는 설계도여야 한다 — 추상적 조언 금지,
빈칸 채우기 공식 수준으로 구체적으로.

1차 분석 요약:
{summary}

히트작:
{hits}

JSON만 출력:
{{"plays": [
  {{"name": "플레이 이름 (예: 낚시-반전, 8자 이내)",
    "when": "어떤 소재/상황에 쓰는 플레이인지 1문장",
    "cover": "표지 문구 공식 — 빈칸 템플릿 (예: \\"OO에 OO 왔대!\\" 기대 조성 → 서브라인에서 반전 암시)",
    "flow": "전개 설계 — 장별로 어떻게 굴리는지 1~2문장",
    "cta": "참여 폭발 마무리 공식 — 댓글을 부르는 질문의 형태까지",
    "evidence": "근거 — 어떤 히트작이 이 플레이로 몇 배 터졌나 1줄"}},
  "... 3~5개, 성과 근거가 강한 순"
],
"playbook_guide": "기획 프롬프트 주입용 '- ' 불릿 3~5개 — 소재가 오면 어떤 플레이부터 검토하고 어떻게 조합할지 명령형으로"}}"""


def _playbook(cfg, base, handle, rep, log=print):
    """2차 강화 분석: 1차 해부 결과+히트작을 재입력해 재사용 가능한 승리 기획 플레이북 증류."""
    hooks = rep.get("hooks") or {}
    win = rep.get("winners") or {}
    hits = rep.get("hits") or []
    if not (hits or win.get("by_axis")):
        return None
    summary_bits = [
        f"시퀀스: {hooks.get('sequence_summary', '')}",
        "후킹 유형: " + ", ".join(f"{h.get('type')}({h.get('share')})"
                               for h in hooks.get("hook_styles", []) if isinstance(h, dict)),
        "터지는 소재(실측): " + ", ".join(f"{r['name']} 평균♥{r['avg']}"
                                    for r in win.get("by_axis", [])[:3]),
        f"수위 코드: {hooks.get('spice', '')}",
        f"지속률: " + ", ".join(d.get("device", "") for d in hooks.get("retention", [])
                             if isinstance(d, dict)),
    ]
    summary = "\n".join(b for b in summary_bits if b.split(": ", 1)[-1].strip())
    d = _refs_root(base) / handle
    parts_hits, imgs = [], []
    for i, h in enumerate(hits[:3], 1):
        parts_hits.append(f"[히트작{i}] ♥{h.get('like')}(중앙값 {h.get('mult_like')}배) "
                          f"💬{h.get('comments')}({h.get('mult_comm')}배) {h.get('type', '')} — "
                          f"{h.get('why', '')} / 캡션: {h.get('caption', '')}")
        f = d / "img" / f"{h.get('id')}.jpg"
        if f.exists():
            imgs.append(f)
    parts = [{"text": PLAYBOOK_PROMPT.format(handle=handle, summary=summary,
                                             hits="\n".join(parts_hits) or "(없음)")}]
    for f in imgs:
        parts.append(_inline(f.read_bytes(), max_side=768))
    key = (cfg.get("gemini_api_key") or "").strip()
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"response_mime_type": "application/json",
                                 "temperature": 0.4, "maxOutputTokens": 3072,
                                 "thinkingConfig": {"thinkingBudget": 0}}}
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    resp = requests.post(GEMINI_URL.format(model=model), params={"key": key},
                         json=body, timeout=150)
    if resp.status_code != 200:
        raise RuntimeError(f"플레이북 추출 실패 {resp.status_code}")
    pb = _parse_json(resp.json()["candidates"][0]["content"]["parts"][0]["text"])
    plays = [p for p in (pb.get("plays") or []) if isinstance(p, dict)
             and (p.get("name") or "").strip()]
    if not plays:
        raise RuntimeError("플레이를 뽑지 못했습니다")
    return {"plays": plays[:5], "playbook_guide": str(pb.get("playbook_guide", "")).strip()}


def _ops(media):
    """운영 패턴 실측(순수 계산): 업로드 시간대·요일(KST), 캐러셀 장수별 평균 좋아요."""
    hours, wdays, pl = {}, {}, {}
    for m in media:
        t = (m.get("timestamp") or "").replace("+0000", "+00:00")
        try:
            dt = datetime.fromisoformat(t) + timedelta(hours=9)
            hours[dt.hour] = hours.get(dt.hour, 0) + 1
            w = "월화수목금토일"[dt.weekday()]
            wdays[w] = wdays.get(w, 0) + 1
        except ValueError:
            pass
        if m.get("media_type") == "CAROUSEL_ALBUM":
            n = len((m.get("children") or {}).get("data", []))
            b = "4~5장" if n <= 5 else ("6~7장" if n <= 7 else "8장+")
            pl.setdefault(b, []).append(m.get("like_count", 0))
    pages = [{"len": k, "n": len(v), "avg": round(sum(v) / len(v))}
             for k, v in pl.items() if len(v) >= 2]
    return {
        "hours": [f"{h}시({c}개)" for h, c in
                  sorted(hours.items(), key=lambda x: -x[1])[:3]],
        "wdays": [f"{d}({c}개)" for d, c in
                  sorted(wdays.items(), key=lambda x: -x[1])[:3]],
        "pages_perf": sorted(pages, key=lambda r: -r["avg"]),
    }


def analyze(cfg, base, handle, bd, stats, log=print):
    """수집된 이미지+캡션 → 형식 리포트(report.json/md) + 스타일·템플릿 프리셋 등록."""
    key = (cfg.get("gemini_api_key") or "").strip()
    if not key:
        raise RuntimeError("Gemini API 키가 없습니다")
    d = _refs_root(base) / handle
    imgs = sorted((d / "img").glob("*.jpg"))
    if not imgs:
        raise RuntimeError("분석할 이미지가 없습니다 (수집 먼저)")

    media = bd.get("media", {}).get("data", [])
    top = sorted(media, key=lambda m: m.get("like_count", 0), reverse=True)
    captions = "\n---\n".join(
        (m.get("caption") or "").strip()[:400] for m in top[:5] if m.get("caption"))
    stats_line = (f"하루 {stats['per_day']}개 업로드, 좋아요 중앙값 {stats['like_median']}"
                  f"·최고 {stats['like_top']}, 댓글/좋아요 {stats['comment_ratio']}%, "
                  f"캐러셀 평균 {stats['carousel_pages']}장")

    log("[3/4] Gemini 형식 분석 중...")
    parts = [{"text": FORMAT_PROMPT.format(handle=handle, stats_line=stats_line,
                                           captions=captions or "(없음)")}]
    for p in imgs[:8]:
        parts.append(_inline(p.read_bytes()))
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"response_mime_type": "application/json",
                             "temperature": 0.4, "maxOutputTokens": 4096,
                             "thinkingConfig": {"thinkingBudget": 0}},
    }
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    last_err = ""
    for attempt in range(3):
        resp = requests.post(GEMINI_URL.format(model=model),
                             params={"key": key}, json=body, timeout=180)
        if resp.status_code == 200:
            break
        last_err = f"{resp.status_code}: {resp.text[:160]}"
        if resp.status_code not in (429, 500, 503):
            break
        import time as _t
        _t.sleep(8 * (attempt + 1))
    else:
        resp = None
    if resp is None or resp.status_code != 200:
        raise RuntimeError(f"Gemini 오류 {last_err}")
    raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    rep = _parse_json(raw)
    if not (rep.get("guide") or "").strip():
        raise RuntimeError("형식 지침을 뽑지 못했습니다 — 다시 시도해주세요")

    # 후킹 딥분석: 상위 게시물 전체 장 → 표지 후킹 유형·이미지 장치·장별 시퀀스
    try:
        log("[3/4] 후킹 전략·장별 시퀀스 분석 중...")
        rep["hooks"] = analyze_hooks(cfg, base, handle, log=log)
        log(f"      시퀀스: {rep['hooks'].get('sequence_summary', '')[:60]}")
    except Exception as e:
        log(f"      (후킹 분석 실패 — 형식 분석만 저장: {str(e)[:80]})")

    # 데이터 승리 공식 + 운영 패턴 (최근 30개 실측)
    try:
        log("[3/4] 데이터 승리 공식 분석 중 (소재·후킹별 좋아요 실측)...")
        rep["winners"] = _winners(cfg, handle, media, rep, log=log)
    except Exception as e:
        log(f"      (승리 공식 분석 실패: {str(e)[:80]})")
    try:
        rep["ops"] = _ops(media)
    except Exception:
        pass
    # 콘텐츠 갈래 지도: 센 갈래(논쟁·폭로)만이 아니라 꿀팁·스토리·반전 등 부드러운 갈래까지
    try:
        log("[3/4] 콘텐츠 갈래 지도 분석 중 (꿀팁·스토리·반전 등 전 갈래 실측)...")
        rep["branches"] = _branches(cfg, handle, media, log=log)
        if rep.get("branches"):
            log("      갈래: " + " / ".join(
                f"{b['name']}({b['share']}%)" for b in rep["branches"][:6]))
    except Exception as e:
        log(f"      (갈래 지도 분석 실패: {str(e)[:80]})")
    # 히트작 개별 해부: 좋아요·댓글 아웃라이어는 따로 배운다
    try:
        log("[3/4] 히트작 해부 중 (좋아요·댓글 폭발 게시물)...")
        rep["hits"] = _hits(cfg, base, handle, media, log=log)
    except Exception as e:
        log(f"      (히트작 해부 실패: {str(e)[:80]})")
    # 2차 강화 분석: 잘된 기획을 다시 파서 재사용 가능한 플레이북으로 증류
    try:
        log("[3/4] 2차 강화 분석 — 승리 기획 플레이북 추출 중...")
        rep["playbook"] = _playbook(cfg, base, handle, rep, log=log)
        if rep.get("playbook"):
            log("      플레이: " + " / ".join(p.get("name", "")
                for p in rep["playbook"].get("plays", [])[:5]))
    except Exception as e:
        log(f"      (플레이북 추출 실패: {str(e)[:80]})")

    rep["handle"] = handle
    rep["analyzed"] = datetime.now().isoformat(timespec="seconds")
    rep["stats"] = stats
    (d / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    (d / "report.md").write_text(_report_md(handle, bd, stats, rep), encoding="utf-8")

    # 스타일/템플릿 프리셋 등록 (같은 핸들의 이전 프리셋은 교체)
    log("[4/4] 스타일·템플릿 프리셋 등록 중...")
    reg = registry_load(base)
    old = reg.get(handle, {})
    if old.get("style_id"):
        styles.delete_style(base, old["style_id"])
    if old.get("template_id"):
        styles.delete_template(base, old["template_id"])
    cover = next((d / "img" / p["img"] for p in _posts_load(base, handle)
                  if p.get("img")), None)
    thumb = styles.make_thumb(cover.read_bytes()) if cover else None
    sp = styles.save_style(base, {
        "name": f"@{handle}"[:20],
        "summary": (rep.get("summary") or "")[:60],
        "tone": (rep.get("caption_tone") or "")[:80],
        "cover": (rep.get("cover_formula") or "")[:80],
        "flow": (rep.get("inner_formula") or "")[:80],
        "density": "",
        "cta": (rep.get("engagement_formula") or "")[:80],
        "guide": (rep.get("guide") or "")[:1500],
        "kind": "style",
    }, thumb_b64=thumb)
    tpl_saved = None
    if cover:
        try:
            tpl = styles.analyze_template([cover.read_bytes()])
            tpl["name"] = f"@{handle}"[:20]
            tpl_saved = styles.save_template(base, tpl, thumb_b64=thumb)
        except Exception:
            pass
    # 매거진 렌더 테마: 상위 표지들 밝기 다수결 — 밝은 실사 채널 → jmag, 어두운 → smag
    render_theme = "smag"
    votes = []
    for p in _posts_load(base, handle)[:6]:
        f = d / "img" / (p.get("img") or "")
        if p.get("img") and f.exists():
            try:
                t, _a = styles.extract_visual(f.read_bytes())
                if t:
                    votes.append(t)
            except Exception:
                pass
    if votes and votes.count("cream") * 2 >= len(votes):
        render_theme = "jmag"
    return rep, sp["id"], (tpl_saved or {}).get("id", ""), render_theme


def _report_md(handle, bd, stats, rep):
    fmt = " · ".join(f"{k} {v['n']}개(♥{v['avg_like']})"
                     for k, v in stats.get("formats", {}).items())
    axes = "\n".join(f"- {a}" for a in rep.get("topic_axes", []))
    hooks = rep.get("hooks") or {}
    hook_md = ""
    if hooks:
        hs = "\n".join(
            f"- **{h.get('type', '')}** ({h.get('share', '?')}) — {h.get('how', '')}\n"
            f"  예: “{h.get('example', '')}”"
            for h in hooks.get("hook_styles", []) if isinstance(h, dict))
        ih = "\n".join(f"- **{h.get('device', '')}** — {h.get('how', '')}"
                       for h in hooks.get("image_hooks", []) if isinstance(h, dict))
        sq = "\n".join(
            f"- **{s.get('page', '?')}장 · {s.get('role', '')}** — "
            f"글: {s.get('text', '')} / 사진: {s.get('image', '')}"
            for s in hooks.get("sequence", []) if isinstance(s, dict))
        ty = hooks.get("typography") or {}
        rt = "\n".join(f"- **{d.get('device', '')}** — {d.get('how', '')}"
                       for d in hooks.get("retention", []) if isinstance(d, dict))
        ex = "\n".join(f"- {n}" for n in hooks.get("extra_notes", []) if n)
        hook_md = f"""
## 후킹 전략 해부 🎣
시퀀스 공식: **{hooks.get('sequence_summary', '')}**
결(톤) 배합: {hooks.get('tone_mix', '')}
수위 코드: {hooks.get('spice', '')}

### 표지 텍스트 후킹 유형
{hs}

### 이미지 후킹 장치
{ih}

### 장별 시퀀스 (캐러셀 흐름)
{sq}

### 타이포그래피 (텍스트 크기·배치)
- 표지: {ty.get('cover', '')}
- 안쪽 장: {ty.get('inner', '')}
- 강조: {ty.get('accent', '')}

### 표지 카피 규격
{hooks.get('headline_stats', '')}
- 시그니처 키워드: {hooks.get('signature_keywords', '(미측정)')}

### 대본 전개 형식
{hooks.get('script_style', '')}

### 지속률 장치 (끝까지 넘기게)
{rt}
""" + (f"""
### 그 외 발견 (특이 무기)
{ex}
""" if ex else "")
    win = rep.get("winners") or {}
    ops = rep.get("ops") or {}
    data_md = ""
    if win or ops:
        wa = "\n".join(f"- {r['name']}: 평균 ♥{r['avg']} ({r['n']}개, 중앙값의 "
                       f"{round(r['avg'] / max(win.get('median', 1), 1), 1)}배)"
                       for r in win.get("by_axis", []))
        wh = "\n".join(f"- {r['name']}: 평균 ♥{r['avg']} ({r['n']}개)"
                       for r in win.get("by_hook", []))
        pp = " · ".join(f"{r['len']} 평균 ♥{r['avg']}({r['n']}개)"
                        for r in ops.get("pages_perf", []))
        data_md = f"""
## 데이터 승리 공식 📊 (최근 게시물 실측, 중앙값 ♥{win.get('median', '?')})

### 터지는 소재 (평균 좋아요순)
{wa or '- (표본 부족)'}

### 터지는 후킹 유형
{wh or '- (표본 부족)'}

## 운영 패턴
- 업로드 시간대(KST): {' · '.join(ops.get('hours', [])) or '?'}
- 요일: {' · '.join(ops.get('wdays', [])) or '?'}
- 캐러셀 장수별 성과: {pp or '?'}
"""
    brs = rep.get("branches") or []
    if brs:
        bm = "\n".join(
            f"- **{b.get('name', '')}** ({b.get('n')}개 {b.get('share')}% · "
            f"평균 ♥{b.get('avg_like')} 💬{b.get('avg_comments')}) — {b.get('desc', '')}\n"
            f"  · 왜 먹히나: {b.get('why', '')}\n"
            f"  · 구성 공식: {b.get('formula', '')}\n"
            f"  · 대표작: {b.get('top_caption', '')}"
            for b in brs)
        data_md += f"""
## 콘텐츠 갈래 지도 🗺 (센 것·부드러운 것 배합 실측)
{bm}
"""
    hits = rep.get("hits") or []
    if hits:
        hm = "\n".join(
            f"- **♥{h.get('like')}({h.get('mult_like')}배) · "
            f"💬{h.get('comments')}({h.get('mult_comm')}배) · {h.get('type', '')}** — "
            f"{h.get('why', '')}\n  → 교훈: {h.get('lesson', '')}"
            for h in hits)
        data_md += f"""
## 히트작 해부 🔥 (아웃라이어 개별 분석)
{hm}
"""
    pb = rep.get("playbook") or {}
    plays = [p for p in (pb.get("plays") or []) if isinstance(p, dict)]
    if plays:
        pm = "\n".join(
            f"- **{p.get('name', '')}** — {p.get('when', '')}\n"
            f"  · 표지 공식: {p.get('cover', '')}\n"
            f"  · 전개: {p.get('flow', '')}\n"
            f"  · CTA: {p.get('cta', '')}\n"
            f"  · 근거: {p.get('evidence', '')}"
            for p in plays)
        data_md += f"""
## 승리 기획 플레이북 ♟ (2차 강화 분석)
{pm}
"""
    hook_md += data_md
    return f"""# @{handle} 형식 리포트 ({rep.get('analyzed', '')[:16]})

**{bd.get('name', '')}** — 팔로워 {bd.get('followers_count', 0):,} · 게시물 {bd.get('media_count', 0):,}
bio: {(bd.get('biography') or '').strip()[:200]}

## 운영 수치 (최근 {stats['count']}개)
- 하루 {stats['per_day']}개 업로드 · 캐러셀 평균 {stats['carousel_pages']}장
- 좋아요 중앙값 {stats['like_median']} / 최고 {stats['like_top']} · 댓글율 {stats['comment_ratio']}%
- 포맷: {fmt}

## 형식 — {rep.get('name', '')}
{rep.get('summary', '')}

### 표지 공식
{rep.get('cover_formula', '')}

### 안쪽 장 공식
{rep.get('inner_formula', '')}

### 캡션
{rep.get('caption_tone', '')}

### 소재 축
{axes}

### 댓글·저장 장치
{rep.get('engagement_formula', '')}

### 수위 지침 ⚠️
{rep.get('level_guide', '')}

### AI 표지 지침
{rep.get('ai_cover_style', '')}
{hook_md}"""


def update_channel(cfg, base, handle, limit=30, log=print):
    """수집→분석→프리셋 등록→registry 갱신. UI [형식 업데이트] 버튼의 본체."""
    handle = re.sub(r"[^A-Za-z0-9._]", "", handle.strip().lstrip("@"))
    if not handle:
        raise RuntimeError("핸들이 비었습니다")
    cfg = refresh_token_if_needed(cfg, base, log=log)
    bd, stats = collect(cfg, base, handle, limit=limit, log=log)
    rep, style_id, template_id, render_theme = analyze(cfg, base, handle, bd, stats,
                                                       log=log)
    reg = registry_load(base)
    _old = reg.get(handle) or {}
    if _old.get("theme_lock") and _old.get("render_theme"):
        render_theme = _old["render_theme"]     # 수동 고정 테마는 자동 판정보다 우선
    reg[handle] = {
        "handle": handle,
        "name": bd.get("name", ""),
        "followers": bd.get("followers_count", 0),
        "media_count": bd.get("media_count", 0),
        "last_update": datetime.now().isoformat(timespec="seconds"),
        "stats": stats,
        "format_name": rep.get("name", ""),
        "summary": rep.get("summary", ""),
        "style_id": style_id,
        "template_id": template_id,
        "render_theme": render_theme,
        "theme_lock": bool(_old.get("theme_lock")),
    }
    registry_save(base, reg)
    log(f"✅ @{handle} 형식 업데이트 완료")
    return reg[handle]


def _inline(image_bytes, max_side=1024):
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=82)
    return {"inline_data": {"mime_type": "image/jpeg",
                            "data": base64.b64encode(buf.getvalue()).decode()}}


def _render_safe(s):
    """렌더에 올라가는 텍스트 소독: 개행 제거(PIL 측정 오류) + 이모지 제거(폰트 ☒ 깨짐)."""
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return re.sub(r"[‍️⁉‼←-⇿⌀-⯿〰〽"
                  r"\U0001f000-\U0001faff]", "", s).strip()


def _parse_json(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 인용부호 미이스케이프 파손 — brain의 라인 단위 보정기 재사용
        try:
            from cardnews.brain import _repair_quotes
            return json.loads(_repair_quotes(text))
        except Exception:
            text2 = re.sub(r'(?<=[가-힣A-Za-z0-9])"(?=[가-힣A-Za-z0-9])', '\\"', text)
            return json.loads(text2)


# ---------------- 한국 소재 소싱 (역수출: 일본 타겟) ----------------

KRJP_RSS_QUERIES = ["편의점 신상", "한정판 콜라보", "한국 미담 화제", "온라인 커뮤니티 논란",
                    "한국 여행 일본인", "K푸드 일본 반응",
                    "해외 반응 한국 화제", "황당 유행 밈",
                    "MZ 유행", "요즘 직장인 문화 화제", "한국 연애 결혼 문화",
                    "팝업스토어 핫플레이스",
                    "한국 여행 꿀팁", "알고보니 반전 화제"]

# 일본 수요 신호 — 일본 매체가 지금 다루는 한국 소재 = 수요가 검증된 주제 축
KRJP_JP_QUERIES = ["韓国 SNS 話題", "韓国 バズる", "韓国 ネット民 反応",
                   "韓国グルメ 人気", "K-POP 話題", "韓国 面白い"]

# 갈래 지정 스캔용 — 그 갈래 소재가 몰려 있는 전용 검색 줄기
KRJP_AXIS_QUERIES = {
    "한정템": ["편의점 신상 한정판", "콜라보 굿즈 출시", "품절 대란"],
    "인물": ["유명인 논란 화제", "연예인 미담", "스타 근황 화제"],
    "사건미담": ["훈훈한 미담 화제", "황당 사건 화제", "공분 논란"],
    "팩트체크": ["알고보니 가짜", "사실 확인 논란", "잘못 알려진 사실"],
    "꿀팁": ["한국 여행 꿀팁", "편의점 이용 꿀팁", "알아두면 유용한 정보",
             "한국 지하철 팁", "현지인 맛집 팁"],
    "스토리": ["황당 사연 화제", "감동 사연", "레전드 썰 화제", "직장인 에피소드"],
    "반전": ["알고보니 반전", "의외의 사실 화제", "오해와 진실", "겉과 달리 사실은"],
}

KRJP_AXIS_NOTE = """
⚡ 이번 스캔은 '{axis}' 갈래 전용이다 — 후보 중 이 갈래에 정확히 맞는 소재만 골라라
(다른 갈래는 아무리 좋아도 탈락). axis는 전부 "{axis}"로 표기. 최대 10개.
"""


def _trends_kr(n=8):
    """구글 트렌드 KR 급상승 검색어 RSS — 지금 한국에서 터지는 것 (관련 기사 포함)."""
    try:
        xml = requests.get("https://trends.google.co.kr/trending/rss?geo=KR",
                           timeout=15, headers={"User-Agent": "Mozilla/5.0"}).text
        root = ET.fromstring(xml)
        NS = "{https://trends.google.com/trending/rss}"
        out = []
        for item in root.iter("item"):
            kw = (item.findtext("title") or "").strip()
            traffic = (item.findtext(NS + "approx_traffic") or "").strip()
            news = item.find(NS + "news_item")
            nt = (news.findtext(NS + "news_item_title") or "").strip() if news is not None else ""
            nu = (news.findtext(NS + "news_item_url") or "").strip() if news is not None else ""
            if kw:
                title = f"{kw} (검색 {traffic})" + (f" — {nt}" if nt else "")
                out.append({"title": title, "url": nu})
            if len(out) >= n:
                break
        return out
    except Exception:
        return []


def _yt_hot(base, n=10):
    """유튜브 KR 인기 급상승 (공식 Data API) — 키 등록돼 있을 때만 작동."""
    key = ""
    try:
        yk = json.loads((Path(base) / "youtube_keys.json").read_text(encoding="utf-8"))
        for lst in yk.values():
            for k in lst:
                if k.get("key"):
                    key = k["key"]
                    break
            if key:
                break
    except Exception:
        pass
    if not key:
        return []
    try:
        r = requests.get("https://www.googleapis.com/youtube/v3/videos",
                         params={"part": "snippet,statistics",
                                 "chart": "mostPopular", "regionCode": "KR",
                                 "maxResults": n, "key": key}, timeout=15)
        if r.status_code != 200:
            return []
        out = []
        for v in r.json().get("items", []):
            views = int(v.get("statistics", {}).get("viewCount", 0))
            out.append({"title": f"{v['snippet']['title']} (조회 {views // 10000}만)",
                        "url": f"https://youtu.be/{v['id']}"})
        return out
    except Exception:
        return []

KRJP_PROMPT = """당신은 '일본인 타겟 한국 정보 인스타 채널'의 소재 편집장이다.
아래는 한국 커뮤니티 인기글과 뉴스 제목들이다. 일본 시청자가 반응할 소재를 골라라.

후보 태그 안내:
- [일본뉴스] = 일본 매체가 이미 다루는 한국 소재 — 일본 수요가 검증된 주제 축이니 같은
  주제의 한국 날것 소재에 가산점. 단 일본 매체가 이미 자세히 소개한 소식 자체는 희소성이
  없다 — 그대로 옮기지 말고 '그 주제의 커뮤 날것 버전'을 찾는 힌트로 써라.
- [급상승] = 지금 한국에서 검색량이 폭발한 것 (실시간성 가산)
- [유튜브] = 지금 한국 유튜브 인기 급상승 (한국 MZ가 지금 보는 것)

🎯 페르소나(고정): 한국에 관심 많은 일본 MZ세대 — K팝·한드로 입문해 한국 여행·유학을
꿈꾸고, 미디어에 안 나오는 '진짜 한국'을 궁금해하는 층. 이들의 니즈는 둘이다:
- 양지의 진짜 한국: 지금 한국 MZ가 뭘 먹고 입고 어디 가는지(트렌드·핫플·신상·미담),
  드라마 밖 리얼 일상(연애·학교·회사 문화)
- 음지의 진짜 한국: 한국 인터넷에서만 도는 밈·기행·서브컬처, 뉴스에 안 나오는 리얼 사회면
공식 보도자료·아이돌 공식 소식 같은 '일본에서도 볼 수 있는 뻔한 것'은 탈락 —
한국 커뮤니티에서만 건질 수 있는 날것일수록 좋다.

벤치마크 채널(@justdoeatjapan — 한국인에게 일본 정보)의 거울상이다. 그 채널의 댓글 폭발 공식:
① "거기엔 안 파는" 부러움 소재(한정판·신상) ② 논란 인물 연속극 ③ 사건+미담/공분 ④ 팩트체크
여기에 그 채널을 받쳐주는 **부드러운 갈래**가 있다 — ⑤ 꿀팁(현지 실용 정보, 저장 유발)
⑥ 스토리(재밌는 사연·해프닝) ⑦ 반전(겉 이미지와 다른 실제 속사정·일상).
이 7축으로 분류하되 센 것(논쟁·폭로)과 부드러운 것(꿀팁·스토리·반전)이 섞이게 골라라 —
논쟁 소재는 수위 유지(젠더·집단 갈등 같은 뜨거운 논쟁 소재도 그대로 다룬다). 단 **국가·민족 혐오 프레임 절대 배제**, 정치 진영 소재 배제
(정치인·북한 관련은 밈·유머여도 배제 — 일본 시장에서 계정 리스크),
그리고 우리가 집단을 비하하는 '화자'가 되지 않는다 — "한국에서 이 논쟁이 이렇게 뜨겁다"는
**논쟁 보도 프레임**으로 소화한다. 커뮤니티 유머·밈 게시물은 실제 사건·발언으로 단정하지
말고 "온라인에서 화제"로 사실관계를 지킨다.

⚡ 이 채널의 '결(톤)'이 핵심이다 — 밋밋한 단순 정보 소개는 탈락시키고, 소재마다 아래 셋 중
하나의 결이 확실히 실리는 것만 골라라:
- "비판": 한국의 현상·행태를 꼬집는 시선 — 일본 시청자도 갑론을박하게 되는 논쟁 소재
- "국뽕": 양지의 진짜 한국 — "한국엔 이런 게 있다(일본엔 없다/못 산다)" 부러움·감탄
  + 지금 한국 MZ의 트렌드·핫플·리얼 일상 (밝은 쪽 전부 이 결)
- "음지": 음지의 진짜 한국 — 이해 안 가지만 눈을 못 떼는 인터넷 밈·기행·서브컬처·
  뉴스에 안 나오는 리얼 사회면

각 후보에:
- axis: "한정템" | "인물" | "사건미담" | "팩트체크" | "꿀팁" | "스토리" | "반전"
- tone: "비판" | "국뽕" | "음지"
- score: 일본 시청자 반응 예상 1~10 (부러움·놀라움·논쟁 유발력). ⚠️인물·단체 소재는 일본
  시청자가 그들을 전혀 모른다는 전제로 — 이름 인지도 말고 현상 자체가 흥미로운지로 매겨라
- why: 선정 이유 한 줄 (일본 시청자 관점, 어떤 결로 태우는지 포함)
- jp_hook: 일본어 표지 후킹 문구 시안 1줄 (「」 스타일, 20자 이내). ⚠️일본 시청자는 한국
  유명인·커뮤 인물을 모른다 — 이름 말고 현상으로 후킹 (예: 韓国フェミ論争の裏側 ○ / 배인규 ✗)
- topic: 우리 제작기에 넣을 한국어 주제 문장 1줄
- q: 출처 교차검증용 뉴스 검색어 — 이 사건·현상을 뉴스에서 찾을 핵심 명사 2~4개 (조사 없이)
- jq: 일본 반응 교차검색용 일본어 검색어 2~4단어 (이 소재가 일본에서 언급되는지 찾을 단어)

score 6 이상만, 최대 12개, score 내림차순. 세 결이 골고루 섞이게. JSON만 출력:
{"items": [{"axis":"...","tone":"...","score":8,"why":"...","jp_hook":"...","topic":"...","q":"...","jq":"...","src_title":"원래 제목","src_url":"원래 링크"}]}

후보 목록:
"""


# ---------------- 게시물 리메이크 (주제·중심내용 유지 + 표현·썸네일 재창조) ----------------

NEUTRAL_BRAND = ("대중적인 이슈·정보를 선별해 전하는 큐레이션 매거진형 인스타그램 채널. "
                 "저장과 댓글(토론)을 부르는 콘텐츠를 만든다. 특정 강의·상품 홍보는 하지 않는다.")


def _posts_load(base, handle):
    try:
        return json.loads((_refs_root(base) / handle / "posts.json")
                          .read_text(encoding="utf-8"))
    except Exception:
        return []


def remake_source(cfg, base, handle, media_id, log=print):
    """리메이크 재료 준비: 게시물 이미지 경로들 + 캡션.
    수집 때 못 받은 이미지는 raw.json의 media_url로 즉석 다운로드."""
    d = _refs_root(base) / handle
    posts = _posts_load(base, handle)
    post = next((p for p in posts if str(p.get("id")) == str(media_id)), None)
    if not post:
        raise RuntimeError("게시물을 찾지 못했습니다 — 형식 업데이트를 먼저 돌려주세요")
    if post.get("type") == "VIDEO":
        raise RuntimeError("영상 게시물은 리메이크 대상이 아닙니다 (이미지 게시물만)")
    paths = []
    cov = d / "img" / f"{media_id}.jpg"
    if not cov.exists():
        raw = json.loads((d / "raw.json").read_text(encoding="utf-8"))
        m = next((x for x in raw.get("media", {}).get("data", [])
                  if str(x.get("id")) == str(media_id)), None)
        if m and m.get("media_url"):
            _download(m["media_url"], cov)
    if cov.exists():
        paths.append(str(cov))
    for j in (2, 3):
        pj = d / "img" / f"{media_id}_p{j}.jpg"
        if pj.exists():
            paths.append(str(pj))
    if not paths:
        raise RuntimeError("게시물 이미지를 준비하지 못했습니다 (수집 후 시간이 지나 "
                           "이미지 링크가 만료됐을 수 있어요 — 형식 업데이트를 다시 돌려주세요)")
    # 캡션은 원문 전체를 쓴다 (posts.json은 300자 요약 — 잘린 캡션은 AI 날조를 부른다)
    caption = post.get("caption") or ""
    try:
        raw = json.loads((d / "raw.json").read_text(encoding="utf-8"))
        m = next((x for x in raw.get("media", {}).get("data", [])
                  if str(x.get("id")) == str(media_id)), None)
        if m and (m.get("caption") or "").strip():
            caption = m["caption"].strip()
    except Exception:
        pass
    log(f"      리메이크 재료: 이미지 {len(paths)}장 + 캡션 {len(caption)}자")
    return paths, caption


def remake_cfg(cfg, base, handle):
    """리메이크용 cfg: 그 채널의 렌더 테마 + 형식 지침 + 중립 브랜드 (강사 브랜딩 차단)."""
    ent = registry_load(base).get(handle) or {}
    cfg = dict(cfg)
    cfg["card_theme"] = ent.get("render_theme") or "smag"
    cfg["card_brand_context"] = cfg.get("card_brand_context_mag") or NEUTRAL_BRAND
    guide = ""
    try:
        rep = json.loads((_refs_root(base) / handle / "report.json")
                         .read_text(encoding="utf-8"))
        guide = (rep.get("guide") or "").strip()
        lvl = (rep.get("level_guide") or "").strip()
        if lvl:
            guide += f"\n- 수위 지침: {lvl}"
        guide += _hook_guide_text(rep)
        cb = (rep.get("caption_blueprint") or "").strip()
        if cb:
            guide += f"\n- 캡션 설계도(이 구조 그대로): {cb}"
        win = rep.get("winners") or {}
        med = win.get("median") or 0
        ba = (win.get("by_axis") or [None])[0]
        bh = (win.get("by_hook") or [None])[0]
        if ba and med:
            guide += (f"\n- 데이터 승리 공식(실측): 소재 '{ba['name']}' 평균 ♥{ba['avg']}"
                      f"(중앙값의 {round(ba['avg'] / max(med, 1), 1)}배)"
                      + (f", 후킹 '{bh['name']}' 평균 ♥{bh['avg']}" if bh else "")
                      + " — 표지·전개 선택 시 우선 반영")
        pp = ((rep.get("ops") or {}).get("pages_perf") or [None])[0]
        if pp:
            guide += (f"\n- 최적 분량(실측): 캐러셀 {pp['len']}이 가장 터짐(평균 ♥{pp['avg']})"
                      " — beats 수를 (그 장수 - 표지·CTA 2장)에 맞춰라")
        brs = rep.get("branches") or []
        if brs:
            guide += ("\n- 콘텐츠 갈래 지도(실측) — 이 채널은 센 갈래와 부드러운 갈래를 배합한다."
                      " 소재 성격에 맞는 갈래를 고르고 그 갈래의 구성 공식을 따르라"
                      " (논쟁 소재가 아니면 억지로 논쟁 프레임을 씌우지 마라):\n"
                      + "\n".join(
                          f"  · [{b['name']}] 비중 {b['share']}% · 평균 ♥{b['avg_like']}"
                          f" 💬{b['avg_comments']} — {b['formula']}"
                          for b in brs[:6]))
        hits = rep.get("hits") or []
        if hits:
            guide += "\n- 히트작 교훈(이 채널에서 실제 폭발한 것 — 우선 적용):\n" + "\n".join(
                f"  · [{h.get('type', '')}] {h.get('lesson', '')}"
                f" (♥{h.get('like')} 중앙값 {h.get('mult_like')}배)"
                for h in hits[:3])
        pb = rep.get("playbook") or {}
        plays = [p for p in (pb.get("plays") or []) if isinstance(p, dict)]
        if plays:
            guide += ("\n- 승리 기획 플레이북 — 소재에 맞는 플레이를 골라 그 공식대로 기획하라:\n"
                      + "\n".join(
                          f"  · [{p.get('name', '')}] {p.get('when', '')} → "
                          f"표지: {p.get('cover', '')} → 전개: {p.get('flow', '')} → "
                          f"CTA: {p.get('cta', '')}"
                          for p in plays[:4]))
        if (pb.get("playbook_guide") or "").strip():
            guide += "\n" + pb["playbook_guide"].strip()
    except Exception:
        pass
    return cfg, guide


def _hook_guide_text(rep):
    """report.json의 후킹 딥분석 → 기획 프롬프트 주입용 지침 텍스트 (없으면 빈 문자열)."""
    hooks = rep.get("hooks") or {}
    out = ""
    hg = (hooks.get("hook_guide") or "").strip()
    if hg:
        out += "\n" + hg
    seq = [s for s in (hooks.get("sequence") or []) if isinstance(s, dict)]
    if seq:
        out += ("\n- 장별 시퀀스 — 표지와 beats를 반드시 이 역할 순서로 구성하라"
                f" ({hooks.get('sequence_summary', '')}):\n")
        out += "\n".join(
            f"  · {s.get('page', '?')}장 [{s.get('role', '')}] "
            f"글: {s.get('text', '')} / 사진: {s.get('image', '')}"
            for s in seq)
        out += ("\n  · 단, 원본 채널의 '광고/제품 판매' 장은 따라 하지 마라 — "
                "우리 마지막 장은 의견을 묻는 CTA다. 광고 문구·가짜 제품을 만들지 않는다.")
    ss = (hooks.get("script_style") or "").strip()
    if ss:
        out += f"\n- 대본 문체·전개(이대로 써라): {ss}"
    tm = (hooks.get("tone_mix") or "").strip()
    if tm:
        out += f"\n- 결(톤) 배합: {tm}"
    rt = [d for d in (hooks.get("retention") or []) if isinstance(d, dict)]
    if rt:
        out += ("\n- 지속률 장치(각 장 끝에서 다음 장을 넘기게 하라): "
                + " / ".join(f"{d.get('device', '')}—{d.get('how', '')}" for d in rt))
    ex = [n for n in (hooks.get("extra_notes") or []) if n]
    if ex:
        out += "\n- 이 채널의 특이 장치: " + " / ".join(ex[:3])
    hs2 = (hooks.get("headline_stats") or "").strip()
    if hs2:
        out += f"\n- 표지 카피 규격(글자수·기호 이대로): {hs2}"
    sk = (hooks.get("signature_keywords") or "").strip()
    if sk and "없음" not in sk[:4]:
        out += (f"\n- 시그니처 키워드(실측): {sk} — 우리 표지에도 같은 역할의 키워드를 "
                "박아라 (일본 타겟 콘텐츠면 '한국'/「韓国」)")
    sp = (hooks.get("spice") or "").strip()
    if sp and "안 함" not in sp:
        out += (f"\n- 수위 코드(채널 실측): {sp} — 같은 수위로 재현하라. "
                "단 노출·노골적 성 묘사는 금지(계정 정지 리스크), 암시·언어유희 드립 수준까지만")
    return out


REMAKE_PROMPT = """당신은 인스타그램 캐러셀 '리메이크' 편집자다.
첨부한 것은 레퍼런스 채널의 실제 게시물(표지·안쪽 이미지)과 캡션이다.

임무: 이 게시물의 **사건·정보·중심내용을 그대로 유지**하되, 문장·어순·후킹·구성 표현은
전부 새로 쓴다 (같은 이야기를 우리 말로 다시 쓰는 것 — 표절·벤치마킹 티 제거).
⚠️ 절대 금지: 일반화, 다른 주제로 확장, 리스트형 잡학으로 변형. 원본이 다룬 그 사건/정보만 다룬다.
⚠️ 원문에 없는 사실(이름·수치·장소·결말)을 지어내지 마라. 원문이 안 알려주는 부분은 모호하게 두거나 생략한다.
🎣 후킹: 형식 지침에 '후킹 지침'과 '장별 시퀀스'가 있으면 그대로 따르라 — 표지 문구는 이
채널에서 검증된 후킹 유형으로 쓰고, beats는 시퀀스의 역할 순서(도입→고조→반전 등)를 그대로 밟는다.
📷 모든 image_query에는 인물의 국적·외모를 원본 사건과 일치시켜라 — 한국 사건이면
"anonymous Korean students"처럼 영문으로 명시. **국가가 불분명한 보편 소재의 기본은
일본인 느낌**("contemporary Japanese people, modern Tokyo styling" — 시청자=일본인,
소재가 명시적으로 서양일 때만 서양인). ⚠️뭉뚱그린 "Asian"은 금지 — 중국풍으로
그려지면 최악이다. 안 쓰면 서양인으로 그려져 어색해진다.
📷 표지의 주인공은 원글의 '재밌는 요소' 그 자체다 — 포인트가 사물(음식·물건·상황)이면
그 사물 클로즈업이 화면 중심, 인물은 리액션·배경으로만. 인물이 핵심 요소를 가리면 실패.
🔞 형식 지침에 '수위 코드'가 있으면 그 수위의 야한 드립·암시는 그대로 쓴다 —
단 노출·노골적 성 묘사는 금지(계정 정지 리스크), 언어유희·암시 수준까지만.
😜 인물이 나오는 장면은 표정을 과장 연출하라 — 놀리는/약올리는/황당/억울/능청 등
수요층의 감정을 대변하거나 약올리는 표정이 후킹이다 (image_query에 표정을 영문으로 명시).
{audience}
{guide}

원본 캡션:
{caption}

JSON만 출력:
{{
  "intent": "⭐가장 먼저: 원본이 반응을 얻은 핵심 의도 1문장 — 무엇이 웃음/충격/공감/부러움 버튼인지. 이하 모든 기획(표지·beats·캡션)은 이 버튼을 보존해야 하며, 번역·재구성 과정에서 이 버튼이 죽으면 실패다",
  "title_top": "표지 인용/배지용 짧은 후킹 한 줄 (따옴표 없이, 18자 이내). ⚠️'저장필수'·'팔로우' 같은 상용구 금지 — 렌더러가 저장 배지를 따로 붙인다. 사건 속 대사나 궁금증 문구로",
  "title_main": "표지 헤드라인 (22자 이내, 원본과 다른 표현)",
  "subtitle": "서브라인 한 줄 (충격 디테일·반전 등, 25자 이내, 없으면 빈 문자열). ⚠️검증 불가한 절대 단정('절대 없다'류) 금지, 본문과 모순되는 주장 금지",
  "image_query": "표지 AI 이미지 장면 묘사 — 영문, 글자 없는 장면(폰·간판 화면의 글자도 금지), 원본 사건을 시각화 (1~2문장). 먼저 표지가 유발할 감정을 정하고(경악/억울/폭소/부러움 등) 그 감정이 '극대화된 순간'을 묘사하라 — 원본 표지보다 감정이 약해지면 실패다. 연출 강도는 원채널의 이미지 장치 전략을 그대로 수용(가벼운 소재도 과장·드라마틱 연출 OK). ⚠️영화·게임·연예 등 IP 소재면 캐릭터·코스튬·로고를 그리게 하지 마라(비슷하게 그려져도 저작권 위험) — 반드시 '장소·소품·군중' 장면으로만 (예: 영화 소재 → crowded movie theater lobby at night, glowing screens, popcorn / 진열대·티켓·네온 등). 인물은 익명의 일반인만",
  "callout_target": "표지에서 빨간 원+돋보기 줌으로 강조할 핵심 대상 — 영문 2~5단어 (예: the tiny peeled banana). 크기·비교·순위·숨은 디테일이 후킹인 소재면 적극 사용하라(레퍼런스 채널의 핵심 장치다). 콕 짚을 대상이 없을 때만 빈 문자열. ⚠️원·화살표·강조 표시를 image_query에 묘사하는 건 절대 금지 — 원은 렌더러가 후처리로 그린다(이미지에 그려지면 이중 원이 된다)",
  "beats": [
    {{"role": "이 장이 장별 시퀀스에서 맡는 역할 (예: 맥락/고조/반전, 6자 이내)",
      "title": "그 장면을 서술하는 완전한 문장형 후킹 헤드라인 (짧은 소제목 금지 — 원본 안쪽 장처럼 스토리가 읽히게, 20~30자)", "lines": ["본문 문장 1", "본문 문장 2"],
      "image_query": "이 전개 순간의 장면 묘사 — 영문 1문장, 표지와 같은 사건의 연속 컷(같은 장소·인물). 장별 시퀀스의 그 장 '사진' 전략을 반영"}},
    "... 3~6개 (장별 시퀀스의 역할 순서대로, 형식 지침에 '최적 분량' 실측이 있으면 그 수에 맞춰라)"
  ],
  "caption": "인스타 캡션 전문 — 원본 스토리텔링 톤을 참고해 새로 작성, 이모지로 시작, 400~700자, 마지막에 의견을 묻는 질문",
  "hashtags": "해시태그 5~8개 한 줄 (#으로 시작, 공백 구분)"
}}"""


def remake_build(cfg, base, handle, media_id, audience="", log=print):
    """게시물 리메이크: 중심내용 유지 + 표현·후킹 재창조 + AI 썸네일(채널 미감 테마)
    → 완성팩 (build_cardnews와 같은 반환 형태, 수출 탭 호환).
    audience: 최종 수요층(예: '일본 시청자') — 감정의 주어를 그쪽으로 재중심화."""
    from cardnews import render as card_render
    from cardnews import pipeline as card_pipeline
    import uuid as _uuid
    import zipfile

    cfg2, guide = remake_cfg(cfg, base, handle)
    theme = cfg2.get("card_theme", "smag")
    if not audience and theme == "jmag":
        audience = "일본 시청자(일본 네티즌)"   # 일본 타겟 채널은 언어와 무관하게 항상 일본 시점
    paths, caption = remake_source(cfg2, base, handle, media_id, log=log)
    key = (cfg2.get("gemini_api_key") or "").strip()
    if not key:
        raise RuntimeError("Gemini API 키가 없습니다")

    log("[1/4] 리메이크 기획 중 (AI 비전) — 중심내용 유지 · 표현 재창조"
        + (f" · {audience} 시점" if audience else ""))
    gtxt = f"[형식 지침 — 이 채널의 형식을 따른다]\n{guide}" if guide else ""
    atxt = ""
    if audience:
        atxt = (f"🎯 수요층: 이 게시물의 최종 시청자는 {audience}다. 감정의 주어·당사자를 "
                f"그 수요층으로 재중심화하라 — 순위·비교·평가 소재면 그 나라 항목을 후킹의 "
                f"중심에 놓고(예: 분류표라면 그 나라가 어떻게 그려졌는지부터), 반응 장은 "
                f"그 수요층 네티즌의 억울함·발끈·부러움으로 쓴다. 원본에 그 나라 정보가 "
                f"없으면 구체적 사실은 지어내지 말고 '그 나라에서도 화제' 같은 후킹 "
                f"표현으로 소화하라(반응 문구=후킹 장치). "
                f"원본이 수요층 나라의 제품·문화를 소개하는 글이면(예: 일본 한정품을 "
                f"한국인에게 소개) 방향을 뒤집어라 — '외국(한국)에서 이게 이렇게 화제다/"
                f"부러워한다'는 외부 반응 각도로 재중심화하면 수요층에겐 자국 자부심+신기함 "
                f"후킹이 된다. 단 외국 반응은 원본 캡션·이미지에 실제로 있는 것만 쓴다. "
                f"⚠️수요층이 아닌 나라의 시점 문장을 표지·본문·CTA 어디에도 남기지 마라 "
                f"(예: 일본 타겟인데 '한국에서도 팔아줬으면' 같은 한국인 목소리 금지). "
                f"CTA도 수요층에게 의미 있는 질문이어야 한다 — 일본 타겟이면 '이거 알고 "
                f"있었냐/신어봤냐/외국인이 부러워하는 거 어떻게 생각하냐'처럼. "
                f"🙈 제로 지식 전제: 수요층은 원본 나라의 유명인·인터넷 인물·단체·내수 "
                f"맥락을 전혀 모른다 — 이름으로 후킹하지 말고 현상·사건 자체로 후킹하며, "
                f"인물·단체가 나오면 첫 장에서 '어떤 존재인지' 한 줄 소개 후 진행하라. "
                f"(수요층 나라에서도 확실히 아는 최정상급 인물만 예외) "
                f"🏷 콘텐츠 무대가 되는 나라가 뚜렷한 소재면 표지 카피에 그 나라 키워드를 "
                f"자연스럽게 박아라(예: 한국 이야기면 '한국'/「韓国」) — 벤치마크 표지 실측 "
                f"62%가 국가 키워드를 노출한다. 국가 미상·보편 소재(잡학·순위·심리)면 "
                f"강제하지 마라 — 궁금증 자체로 후킹한다. "
                f"🧩 내수 난이도 처리: 원본이 그 나라 내수자만 이해할 맥락(커뮤니티 밈, "
                f"언어유희·말장난, 현지 연예인·방송 인지도)에 기대는 소재면 그대로 옮기지 "
                f"마라 — ①내수 맥락을 걷어내고 누구나 이해할 보편 구조로 재구성하거나, "
                f"②랭킹·리스트·잡학·유머 형식처럼 사실 특정성이 낮은 소재에 한해 형식과 "
                f"기획만 유지하고 수요층 나라의 사례·배경으로 치환하라(일본 타겟이면 일본 "
                f"이야기로). ⚠️실제 사건·실존 인물·구체적 수치가 핵심인 소재는 치환 금지 "
                f"— 나라를 바꾸면 날조다. 그 경우 보편 프레임으로만 소화한다.")
    parts = [{"text": REMAKE_PROMPT.format(guide=gtxt, audience=atxt,
                                           caption=(caption or "(없음)")[:1500])}]
    for p in paths[:3]:
        parts.append(_inline(Path(p).read_bytes()))
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"response_mime_type": "application/json",
                                 "temperature": 0.6, "maxOutputTokens": 4096,
                                 "thinkingConfig": {"thinkingBudget": 0}}}
    model = cfg2.get("gemini_model", "gemini-2.5-flash")
    resp = requests.post(GEMINI_URL.format(model=model), params={"key": key},
                         json=body, timeout=180)
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini 오류 {resp.status_code}: {resp.text[:160]}")
    rp = _parse_json(resp.json()["candidates"][0]["content"]["parts"][0]["text"])
    beats = [b for b in (rp.get("beats") or [])
             if isinstance(b, dict) and (b.get("title") or "").strip()][:6]
    if len(beats) < 2:
        raise RuntimeError("리메이크 전개를 뽑지 못했습니다 — 다시 시도해주세요")
    def _one(s, limit=None):
        s = _render_safe(s)
        return s[:limit] if limit else s

    items = [{"num": i + 1, "category": "",
              "role": _one(b.get("role"), 12),
              "title": _one(b.get("title"), 60),
              "lines": [{"text": _one(t)}
                        for t in (b.get("lines") or []) if _one(t)][:4]}
             for i, b in enumerate(beats)]
    n = len(items)
    plan = {
        "title_top": _one(rp.get("title_top"), 30),
        "title_main": _one(rp.get("title_main"), 40) or "리메이크",
        "subtitle": _one(rp.get("subtitle"), 40),
        "image_query": str(rp.get("image_query", "")).strip(),
        "caption": str(rp.get("caption", "")).strip(),
        "comment_keyword": "",
        "n_items": n,
        "categories": [],
        "teaser": list(range(1, n + 1)),
        "preview_titles": [it["title"] for it in items[:3]],
        "ebook_title": str(rp.get("title_main", "")).strip()[:40],
    }
    seq_log = "→".join(it["role"] for it in items if it.get("role"))
    log(f"      표지: {plan['title_top']} / {plan['title_main']} · 전개 {n}장"
        + (f" · 시퀀스 {seq_log}" if seq_log else ""))

    return _produce_pack(cfg2, base, plan, items, beats, rp,
                         {"source": "remake", "ref_handle": handle,
                          "ref_post": str(media_id)}, log=log)


JUDGE_PROMPT = """당신은 인스타 벤치마킹 심판이다. [A]는 우리가 만든 게시물 카드들,
[B]는 레퍼런스 채널의 실제 인기 표지들이다.
우리 목표: [B] 채널의 미감·후킹·형식을 완전 재현하되 내용만 다른 것. 팬심 없이 냉정하게 채점하라.

채널 형식 기준: {rubric}
소재의 핵심 의도(기획이 선언한 감정 버튼): {intent}

10점 만점(소수점 1자리):
- hook: 표지 문구가 [B]급으로 스크롤을 멈추는가
- visual: 사진 미감·연출이 [B]와 같은 채널에서 나온 것처럼 보이는가 (AI 티가 나면 감점)
- emotion: 표지의 감정 타격(경악·억울·폭소 등)이 [B] 원본급으로 즉각적인가 — 감정이 약화됐으면 확실히 감점
- sequence: 장별 흐름이 기준 시퀀스대로 굴러가는가
- copy: 문구·대본 문체가 채널 기준과 일치하는가
- intent: 위 '핵심 의도'의 감정 버튼이 카드에서 실제로 작동하는가 — 버튼이 흐려지거나
  설명조가 됐으면 확실히 감점 (의도 미명시면 카드 내용에서 추정해 채점)

JSON만 출력:
{{"scores": {{"hook": 0.0, "visual": 0.0, "emotion": 0.0, "sequence": 0.0, "copy": 0.0, "intent": 0.0}}, "total": 0.0,
  "weak": "가장 아쉬운 점 1가지 (구체적으로)",
  "fix": "hook/visual/emotion이 7 미만일 때만 — 표지 이미지 재생성에 반영할 영문 장면 보강 지시 1문장 (감정을 어떻게 키울지 포함). 아니면 빈 문자열",
  "verdict": "한 줄 총평"}}"""


def _judge_pack(cfg2, base, handle, plan, pack, cards, intent="", log=print):
    """완성 카드를 레퍼런스 원본 인기 표지와 나란히 놓고 AI 심판 채점.
    표지 점수(후킹/미감)가 낮으면 심판의 지시로 표지를 1회 보수(같은 장면 유지)."""
    from cardnews import render as card_render
    from src import genimg
    d = _refs_root(base) / handle
    refs = [d / "img" / p["img"] for p in _posts_load(base, handle)[:5] if p.get("img")]
    refs = [f for f in refs if f.exists()][:2]
    if not refs or not cards:
        return None
    rep = {}
    try:
        rep = json.loads((d / "report.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    hooks = rep.get("hooks") or {}
    rubric = " / ".join(x for x in [
        hooks.get("sequence_summary", ""),
        (hooks.get("typography") or {}).get("cover", ""),
        (hooks.get("script_style") or "")[:120]] if x)
    log("      벤치마킹 자가채점 — 원본 인기 표지와 나란히 비교 중...")
    parts = [{"text": JUDGE_PROMPT.format(rubric=rubric or "(리포트 없음)",
                                          intent=(intent or "").strip()
                                          or "(미명시 — 카드에서 추정)")},
             {"text": "[A] 우리 결과물 (표지부터 순서대로):"}]
    for c in cards[:3]:
        parts.append(_inline(Path(c).read_bytes(), max_side=768))
    parts.append({"text": "[B] 레퍼런스 채널 실제 인기 표지:"})
    for f in refs:
        parts.append(_inline(f.read_bytes(), max_side=768))
    key = (cfg2.get("gemini_api_key") or "").strip()
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"response_mime_type": "application/json",
                                 "temperature": 0.3, "maxOutputTokens": 1024,
                                 "thinkingConfig": {"thinkingBudget": 0}}}
    model = cfg2.get("gemini_model", "gemini-2.5-flash")
    resp = requests.post(GEMINI_URL.format(model=model), params={"key": key},
                         json=body, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"채점 호출 실패 {resp.status_code}")
    judge = _parse_json(resp.json()["candidates"][0]["content"]["parts"][0]["text"])
    sc = judge.get("scores") or {}
    log(f"      🧑‍⚖️ 채점 {judge.get('total', '?')}/10 — {str(judge.get('verdict', ''))[:70]}")
    fix = str(judge.get("fix") or "").strip()
    try:
        cover_low = (float(sc.get("hook", 10)) < 7 or float(sc.get("visual", 10)) < 7
                     or float(sc.get("emotion", 10)) < 7)
    except (TypeError, ValueError):
        cover_low = False
    cover_p = cfg2.get("cover_image")
    # 실사진 표지는 자동 보수(AI 재생성) 금지 — 실제 자료를 AI 컷으로 바꿔치기하면 안 됨
    if fix and cover_low and cover_p and Path(cover_p).exists() \
            and cfg2.get("_cover_ai"):
        log(f"      🔧 표지 자동 보수: {str(judge.get('weak', ''))[:60]}")
        try:
            # 깨끗한 원본(원 강조 전)을 기준으로 같은 장면 유지 보강 — 연속 컷 일관성 보존
            base_img = cfg2.get("_cover_clean") or cover_p
            genimg.generate_variation(
                cfg2, base_img,
                f"An improved, more striking cover shot of the exact same scene. {fix}",
                Path(cover_p), theme=cfg2.get("card_theme", "smag"), log=log)
            ct = (cfg2.get("_callout_target") or "").strip()
            if ct:
                try:
                    genimg.add_callout(cfg2, cover_p, ct, log=log)
                except Exception:
                    pass
            card_render.render_cover(plan, cfg2, Path(cards[0]))
            judge["fixed"] = True
        except Exception as e:
            log(f"      (표지 보수 실패 — 원본 유지: {str(e)[:60]})")
    return judge


def _produce_pack(cfg2, base, plan, items, beats, rp, meta_extra, log=print):
    """매거진 팩 공용 빌더: AI 표지+연속 컷 → 렌더 → 캡션·고지 → 팩 파일 일습.
    remake_build(게시물 리메이크)와 magazine_build(소재 기반)가 함께 쓴다."""
    from cardnews import render as card_render
    from cardnews import pipeline as card_pipeline
    import zipfile

    theme = cfg2.get("card_theme", "smag")
    n = len(items)
    pack = card_pipeline._make_pack_dir(Path(base) / cfg2.get("output_dir", "결과물"),
                                        plan)
    from src import genimg
    cover_p = pack / "_cover.jpg"
    # 실사진 우선: 소재 원문(src_link)에서 실제 자료 사진을 뽑았으면 AI 생성 대신 쓴다
    # (레퍼런스 채널 방식 — 실제 사건·자료 사진이 표지·중간 컷의 주인공)
    real = _fetch_real_images(cfg2.get("_src_link"), pack / "_real", log=log) \
        if cfg2.get("_src_link") else []
    if real:
        log(f"[2/4] 원문 실사진 {len(real)}장 사용 — 표지 + 전개 컷 (AI 생성 대체)")
        cover_p.write_bytes(Path(real[0]).read_bytes())
        cfg2["cover_image"] = str(cover_p)
        cfg2["_cover_ai"] = False
        for i, (it, _b) in enumerate(zip(items, beats), 1):
            if i < len(real):
                bp = pack / f"_b{i}.jpg"
                bp.write_bytes(Path(real[i]).read_bytes())
                it["image"] = str(bp)
            # 실사진이 모자란 비트는 표지 재사용(폴백과 동일) — AI 컷과 혼합하지 않는다
    else:
        log(f"[2/4] AI 이미지 생성 중 — 표지 + 전개 연속 컷 {len(items)}장 (채널 미감)...")
        for attempt, scene in enumerate([
                plan["image_query"] or plan["title_main"],
                # 재시도: 브랜드/캐릭터 언급이 거부됐을 가능성 — 일반화 장면으로 우회
                f"Atmospheric editorial photo evoking the topic '{plan['title_main']}' "
                f"without any brands, characters or celebrities. Generic objects and mood only."]):
            try:
                genimg.generate_cover(cfg2, scene, cover_p, theme=theme, log=log)
                cfg2["cover_image"] = str(cover_p)
                cfg2["_cover_ai"] = True
                break
            except Exception as e:
                log(f"      (AI 표지 {attempt + 1}차 실패: {str(e)[:70]})")
        if not cfg2.get("_cover_ai"):
            log("      (표지 생성 전부 실패 — 텍스트 표지로 진행)")
        # 안쪽 장: 표지와 '같은 사건의 연속 사진 세트'로 — 레퍼런스 채널의 이미지 위주 전개
        if cover_p.exists():
            for i, (it, b) in enumerate(zip(items, beats), 1):
                scene = str(b.get("image_query") or "").strip()
                if not scene:
                    continue
                bp = pack / f"_b{i}.jpg"
                try:
                    genimg.generate_variation(cfg2, cover_p, scene, bp, theme=theme, log=log)
                    it["image"] = str(bp)
                except Exception as e:
                    log(f"      (컷 {i} 실패 — 표지 재사용: {str(e)[:60]})")

    # 원 강조+돋보기 줌: 연속 컷 생성이 끝난 뒤에 그린다 (컷들이 원을 물려받지 않게)
    # 실사진 표지에도 적용 — 레퍼런스 채널의 원 강조는 원래 실사진 위 장치다
    ct = str(rp.get("callout_target") or "").strip()
    cfg2["_callout_target"] = ct
    if ct and cover_p.exists():
        try:
            (pack / "_cover_clean.jpg").write_bytes(cover_p.read_bytes())
            cfg2["_cover_clean"] = str(pack / "_cover_clean.jpg")
            if genimg.add_callout(cfg2, cover_p, ct, log=log):
                log(f"      🔴 원 강조+돋보기 줌 적용: {ct}")
        except Exception as e:
            log(f"      (원 강조 실패: {str(e)[:60]})")

    last_img = next((it["image"] for it in reversed(items) if it.get("image")), None)
    if last_img or cover_p.exists():
        cfg2["_cta_image"] = last_img or str(cover_p)   # CTA도 이미지 메인

    log("[3/4] 카드 렌더링 — 표지 + 전개 카드 + CTA")
    cards = []
    p = pack / "01.jpg"
    card_render.render_cover(plan, cfg2, p)
    cards.append(p)
    for it in items:                       # 전개 비트 = 카드 1장씩 (스토리 호흡)
        p = pack / f"{len(cards) + 1:02d}.jpg"
        card_render.render_items_card(plan, [it], cfg2, p)
        cards.append(p)
    p = pack / f"{len(cards) + 1:02d}.jpg"
    card_render.render_cta(plan, cfg2, p)
    cards.append(p)

    judge = None
    _jh = (meta_extra or {}).get("ref_handle") or ""
    if _jh and cfg2.get("card_ref_judge", True):
        try:
            judge = _judge_pack(cfg2, base, _jh, plan, pack, cards,
                                intent=str(rp.get("intent") or ""), log=log)
        except Exception as e:
            log(f"      (자가채점 실패: {str(e)[:60]})")

    log("[4/4] 캡션 + 패키징...")
    caption_out = plan["caption"] or f"{plan['title_top']} {plan['title_main']}"
    if cfg2.get("_cover_ai"):
        caption_out += "\n\n*AI를 활용해 재구성한 콘텐츠가 포함됩니다."   # 고지는 캡션에 (원본 방식)
    tags = str(rp.get("hashtags", "")).strip()
    if tags:
        caption_out += "\n\n" + tags
    (pack / "caption.txt").write_text(caption_out, encoding="utf-8")
    meta = {
        "type": "cardnews", "source": "remake", "mode": "normal",
        "theme": theme,
        "topic": plan["title_main"],
        "title": f"{plan['title_top']} {plan['title_main']}".strip(),
        "keyword": "", "ebook_title": plan["ebook_title"], "n_items": n,
        "categories": [], "teaser": plan["teaser"], "ebook": False,
        "cover_image": str(cfg2.get("cover_image") or ""),
        "cta_image": str(cfg2.get("_cta_image") or ""),
        "cover_ai": bool(cfg2.get("_cover_ai")),
        "intent": str(rp.get("intent") or ""),
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    meta.update(meta_extra or {})
    if judge:
        meta["judge"] = judge
    (pack / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    (pack / "items.json").write_text(
        json.dumps({"plan": {k: v for k, v in plan.items() if k != "caption"},
                    "items": items, "proofs": []}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    zip_path = pack / f"{pack.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for c in cards:
            zf.write(c, c.name)
        zf.write(pack / "caption.txt", "caption.txt")
    cards_html = "\n".join(f'<img src="{c.name}">' for c in cards)
    (pack / "review.html").write_text(card_pipeline.REVIEW_TEMPLATE.format(
        title=meta["title"], num_cards=len(cards), num_pages=0,
        keyword="", zip_name=zip_path.name,
        caption=caption_out.replace("&", "&amp;").replace("<", "&lt;"),
        cards_html=cards_html), encoding="utf-8")
    return {"pack": pack, "meta": meta, "caption": caption_out,
            "cards": [c.name for c in cards], "ebook_pages": 0}


MAGAZINE_PROMPT = """당신은 매거진형 인스타 캐러셀 편집자다. 아래 '소재'로 게시물을 기획하라.

소재(주제): {topic}
참고 정보: {context}

타겟: 일본인 시청자에게 한국 정보를 전하는 채널 (한국어로 쓰고 나중에 일본어로 현지화한다).
핵심 독자는 한국에 관심 많은 일본 MZ — K팝·한드로 입문해 미디어 밖 '진짜 한국'
(리얼 일상·유행·인터넷 문화)을 궁금해하는 층이다. 그들에게 "이건 한국 커뮤니티에서만
볼 수 있는 이야기"라는 희소감을 줘라.
"일본에선 못 산다/못 본다"는 부러움 포인트, 신기함, 논쟁 유발 포인트를 살려라.
🎯 감정의 주어는 언제나 일본 시청자다 — 당사자화가 핵심. 순위·비교·평가 소재면 일본 항목을
후킹의 중심에 놓고, 반응은 '일본 네티즌의 억울함·발끈·부러움'으로 쓴다 ('한국 네티즌 반응' 금지).
일본 항목이 원재료에 없으면 구체적 사실은 지어내지 말고 "일본에서도 화제"처럼 후킹
표현으로 소화하라 — 반응 문구는 사실 단정이 아니라 후킹 장치다(원채널의 관행).
🙈 제로 지식 전제: 일본 시청자는 한국의 유명인·인터넷 방송인·커뮤니티 인물·단체·내수
맥락을 **전혀 모른다**. 이름은 후킹이 될 수 없다 — 표지는 이름 없이 '현상·사건 자체'로
후킹하고(예: "반페미 단체 대표의 죽음" ○ / "배인규 사망" ✗), 첫 장에서 이 인물·단체가
한국에서 어떤 존재인지 한 줄로 소개한 뒤 이야기를 시작하라. 그 인물을 알아야만 재밌는
디테일은 빼고, 몰라도 충격적인 구조(젠더 갈등·팬덤 문화·인방 생태계 등)를 중심에 놓아라.
K팝 최정상급 등 일본에서도 확실히 아는 인물만 예외.
🏷 국가 키워드: **소재가 한국 이야기일 때만** 표지 카피(헤드라인 또는 서브라인)에
'한국'(일본어판 「韓国」)을 자연스럽게 박아라 — 벤치마크 실측: 표지의 62%가 국가 키워드
노출, "이건 한국 이야기" 시그널 자체가 클릭 이유다. 단 국가 미상·보편 소재(잡학·순위·
심리·세계 이야기)면 국가 키워드를 강제하지 마라 — 그런 소재는 궁금증 자체로 후킹한다.
문장이 어색해지면 어느 쪽이든 억지로 넣지 않는다.
😜 인물 장면은 표정을 과장 연출 — 놀리는/약올리는/황당/억울/능청 표정이 후킹이다 (영문 명시).
🎯 원글 포인트 사수: 표지·제목·전개는 소재의 **구체적 포인트**(실제로 일어난 그 사건,
실제 그 대사, 실제 그 반전)를 그대로 박아라. "한국인의 유머 감각이 대단하다",
"일본인도 충격받은 한국 이야기" 같은 **메타 서술·일반론 표지는 실패다** — 소재를
소개하지 말고 소재 그 자체가 되어라. 원글이 웃긴/충격인 이유 한 가지를 문구로 옮겨야
한다. 소재가 유머 글이면 그 글의 펀치라인이 표지 카피다.

⚡ 이 채널은 밋밋한 소개 채널이 아니다. 참고 정보에 '결'이 있으면 그 결로, 없으면 소재에
맞는 결을 골라 확실히 태워라:
- 비판: 현상을 꼬집는 시선 — 보는 사람이 갑론을박하게 (단 혐오·비하·정치는 금지)
- 국뽕: "한국엔 이런 게 있다(일본엔 없다)" 부러움·감탄을 노골적으로
- 음지: 이해 안 가지만 눈을 못 떼는 기묘함 — 밈·기행·서브컬처의 낯선 매력을 그대로
표지·전개·캡션 전부에서 그 결이 느껴져야 한다.
단, 소재가 논쟁이 아니라 부드러운 갈래면 억지로 논쟁 프레임을 씌우지 마라 — 갈래별 공식:
- 꿀팁: 저장하고 싶은 실용 정보 — 표지는 '이득' 약속(모르면 손해), 전개는 팁을 하나씩,
  CTA는 저장·써봤는지 질문
- 스토리: 사연·해프닝 — 기승전결로 끌고 가서 마지막 장에 반전·펀치라인, CTA는 "당신이라면?"
- 반전(겉과 다른 속): **표지에서부터** 통념을 박는다 — 배지·서브라인에 통념 한 줄
  ("아직 ○○라고 생각했다면"), 헤드라인은 반전 예고. 전개에서 실제 모습으로 뒤집는
  "알고 보니" 구조, CTA는 "몰랐던 사람?" (통념이 본문 1장에만 있고 표지에 없으면
  반전 갈래 실패다)
논쟁 소재(젠더·집단 갈등 포함)도 수위 그대로 태운다 — 단 채널이 비하의 화자가 되지 말고
"이 논쟁이 이렇게 뜨겁다"를 전하는 **논쟁 보도(관찰자) 프레임**으로 쓴다.
소재가 커뮤니티 글·밈이면 실제 사건·발언으로 단정하지 말고 "온라인에서 화제"로 서술한다.
참고 정보에 '출처 검증' 등급이 있으면 그대로 따르라: '뉴스 확인'이면 근거 제목에 있는
사실까지만 쓰고, '커뮤 교차'·'커뮤 단독'이면 **모든 서술을 "…라는 글이 화제/…라고 한다"의
전언 프레임**으로 쓰며 수치·날짜·실명 등 세부 사실을 절대 단정하지 않는다.
형식 지침에 '수위 코드'가 있으면 그 수위의 야한 드립·암시도 그대로 쓴다 —
단 노출·노골적 성 묘사는 금지(계정 정지 리스크), 언어유희·암시 수준까지만.

⚠️ 주의:
- 소재는 제목 수준의 정보다. 확인 안 된 세부 사실(정확한 수치·출시일·이름)을 지어내지 마라.
  모르는 디테일은 소개·큐레이션 톤으로 일반적으로 서술한다.
- **표지 카피(서브라인·배지 포함)가 본문 내용과 모순되면 실패다** (예: 표지 "일본엔
  없다" ↔ 본문 "일본과 같다고 생각했죠?") — 후킹 과장은 좋지만 앞뒤는 맞춰라.
- 후킹은 강하게, 국가·집단 혐오/비하 프레임은 배제. 정치 소재 배제.
🎣 아래에 '후킹 지침'과 '장별 시퀀스'가 있으면 그대로 따르라 — 표지 문구는 검증된 후킹
유형으로, beats는 시퀀스의 역할 순서대로.
📷 모든 image_query에 인물의 국적·외모를 명시하라 — 한국 소재면 "anonymous Korean
people", 일본 무대면 "anonymous Japanese people"처럼 영문으로. **국가 불문·보편
소재의 기본은 일본인 느낌**("contemporary Japanese people, modern Tokyo styling" —
시청자가 일본인이니 보편 이야기는 자기 모습으로 보여야 한다). 서양인은 소재가
명시적으로 서양 인물·서양 무대일 때만.
⚠️뭉뚱그린 "Asian"은 금지 — 중국풍 느낌으로 그려지면 최악이다(시청자가 바로 이질감).
안 쓰면 서양인으로 그려져 어색해진다.
📷 표지의 주인공은 소재의 '재밌는 요소' 그 자체다 — 웃긴/충격/신기 포인트가
사물(음식·물건·상황·비교 대상)이면 **그 사물의 클로즈업이 화면 중심**이고 인물은
리액션·배경으로만. 인물을 습관적으로 주인공 삼아 핵심 요소를 가리면 실패다
(실사고: 양파 소재 표지에서 양파가 안 보였음). 원채널 장치도 '사물 디테일 강조
클로즈업'이 핵심이다.

{guide}

JSON만 출력:
{{
  "intent": "⭐가장 먼저: 이 소재로 노릴 핵심 감정 버튼 1문장 (참고 정보의 선정 이유·결을 반영 — 무엇이 웃음/충격/공감/부러움인지). 이하 모든 기획은 이 버튼을 보존해야 한다",
  "title_top": "표지 배지용 짧은 후킹 (18자 이내). ⚠️'저장필수'·'팔로우' 같은 상용구 금지 — 렌더러가 저장 배지를 따로 붙인다",
  "title_main": "표지 헤드라인 (22자 이내)",
  "subtitle": "서브라인 (부러움/충격 포인트, 25자 이내, 없으면 빈 문자열)",
  "image_query": "표지 AI 이미지 장면 묘사 — 영문, 글자 없는 장면 (1~2문장). 먼저 표지가 유발할 감정을 정하고(경악/억울/폭소/부러움 등) 그 감정이 '극대화된 순간'을 묘사하라 — 감정이 약한 밋밋한 장면이면 실패다. 포인트가 사물인 소재면 그 사물의 클로즈업이 프레임 중심(인물 얼굴로 가리지 마라). 연출 강도·분위기는 원채널의 이미지 장치 전략을 그대로 수용하라(가벼운 소재도 과장·드라마틱 연출 OK — 그게 원채널의 무기다). ⚠️브랜드명·저작권 캐릭터·유명인 이름 금지 — 분위기·소품으로 우회 묘사. 폰·간판 등 화면에 글자를 그리게 하지 마라",
  "callout_target": "표지에서 빨간 원+돋보기 줌으로 강조할 핵심 대상 — 영문 2~5단어. 특정 사물·디테일을 콕 짚는 게 후킹인 소재일 때만, 아니면 빈 문자열",
  "beats": [
    {{"role": "이 장이 장별 시퀀스에서 맡는 역할 (예: 맥락/고조/반전, 6자 이내)",
      "title": "장면을 서술하는 완전한 문장형 헤드라인 (20~30자)", "lines": ["본문 문장 1", "본문 문장 2"],
      "image_query": "이 장면 묘사 — 영문 1문장, 표지와 같은 세계관의 연속 컷. 장별 시퀀스의 그 장 '사진' 전략 반영"}},
    "... 3~6개 (장별 시퀀스의 역할 순서대로, 형식 지침에 '최적 분량' 실측이 있으면 그 수에 맞춰라)"
  ],
  "caption": "인스타 캡션 전문 — 이모지 시작, 400~700자, 마지막에 의견을 묻는 질문",
  "hashtags": "해시태그 5~8개 한 줄"
}}"""


def _theme_guide(base, theme, handle=""):
    """테마(채널 미감)를 학습해 온 레퍼런스 채널의 형식·후킹 지침을 찾아온다.
    handle을 주면 그 채널, 없으면 render_theme이 일치하는 등록 채널에서 자동으로."""
    reg = registry_load(base)
    ent = reg.get(handle) if handle else None
    if not ent:
        ent = next((e for e in reg.values() if e.get("render_theme") == theme), None)
    if not ent:
        return "", ""
    try:
        _cfg, guide = remake_cfg({}, base, ent["handle"])
        return guide, ent["handle"]
    except Exception:
        return "", ent.get("handle", "")


def _fetch_real_images(src_link, dest_dir, log=print, max_images=6):
    """소재 원문(커뮤 글 등)에서 실사진 추출 — 레퍼런스 채널처럼 실제 자료 사진을
    표지·전개 컷으로 쓰기 위해. 실패하면 빈 리스트(AI 생성으로 폴백)."""
    if not src_link or not str(src_link).startswith("http"):
        return []
    try:
        from src import extractors
        data = extractors.extract(str(src_link))
        urls = data.get("image_urls") or []
        if not urls:
            return []
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        files = extractors.download_images(urls, data.get("referer") or str(src_link),
                                           dest, max_images=max_images)
        out = []
        from PIL import Image as _Img
        for f in files:                      # 아이콘·짤막한 이미지 걸러내기
            try:
                with _Img.open(f) as im:
                    if im.width >= 480 and im.height >= 360:
                        out.append(str(f))
            except Exception:
                continue
        if out:
            log(f"      📷 원문 실사진 {len(out)}장 확보 ({str(src_link)[:50]}...)")
        return out
    except Exception as e:
        log(f"      (원문 사진 추출 실패 — AI 생성으로 진행: {str(e)[:60]})")
        return []


def magazine_build(cfg, base, topic, context="", theme="jmag", handle="",
                   src_link="", log=print):
    """소재(주제 문장) → 매거진 완성팩. 역수출 소재 스캔 결과를 바로 제작하는 경로 —
    리메이크와 같은 품질(AI 표지+연속 컷+채널 미감)이되 원본 게시물 없이 주제에서 출발.
    src_link(소재 원문 URL)가 있으면 실사진을 추출해 표지·전개 컷으로 우선 사용."""
    cfg2 = dict(cfg)
    cfg2["card_theme"] = theme if theme in ("smag", "jmag") else "jmag"
    cfg2["card_brand_context"] = cfg.get("card_brand_context_mag") or NEUTRAL_BRAND
    cfg2["_src_link"] = str(src_link or "").strip()
    key = (cfg2.get("gemini_api_key") or "").strip()
    if not key:
        raise RuntimeError("Gemini API 키가 없습니다")
    topic = str(topic or "").strip()
    if len(topic) < 4:
        raise RuntimeError("소재(주제)를 4자 이상 입력해주세요")

    guide, ref_handle = _theme_guide(base, cfg2["card_theme"], handle)
    gtxt = f"[형식·후킹 지침 — 이 채널의 형식을 따른다]\n{guide}" if guide else ""
    log("[1/4] 소재 기획 중 — 매거진 형식" + (" + 채널 후킹 시퀀스" if guide else ""))
    body = {"contents": [{"role": "user", "parts": [{"text": MAGAZINE_PROMPT.format(
                topic=topic, context=(context or "(없음)")[:600], guide=gtxt)}]}],
            "generationConfig": {"response_mime_type": "application/json",
                                 "temperature": 0.6, "maxOutputTokens": 4096,
                                 "thinkingConfig": {"thinkingBudget": 0}}}
    model = cfg2.get("gemini_model", "gemini-2.5-flash")
    resp = requests.post(GEMINI_URL.format(model=model), params={"key": key},
                         json=body, timeout=180)
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini 오류 {resp.status_code}: {resp.text[:160]}")
    rp = _parse_json(resp.json()["candidates"][0]["content"]["parts"][0]["text"])
    beats = [b for b in (rp.get("beats") or [])
             if isinstance(b, dict) and (b.get("title") or "").strip()][:6]
    if len(beats) < 2:
        raise RuntimeError("전개를 뽑지 못했습니다 — 다시 시도해주세요")

    def _one(s, limit=None):
        s = _render_safe(s)
        return s[:limit] if limit else s

    items = [{"num": i + 1, "category": "",
              "role": _one(b.get("role"), 12),
              "title": _one(b.get("title"), 60),
              "lines": [{"text": _one(t)}
                        for t in (b.get("lines") or []) if _one(t)][:4]}
             for i, b in enumerate(beats)]
    n = len(items)
    plan = {
        "title_top": _one(rp.get("title_top"), 30),
        "title_main": _one(rp.get("title_main"), 40) or topic[:40],
        "subtitle": _one(rp.get("subtitle"), 40),
        "image_query": str(rp.get("image_query", "")).strip(),
        "caption": str(rp.get("caption", "")).strip(),
        "comment_keyword": "",
        "n_items": n,
        "categories": [],
        "teaser": list(range(1, n + 1)),
        "preview_titles": [it["title"] for it in items[:3]],
        "ebook_title": _one(rp.get("title_main"), 40),
    }
    seq_log = "→".join(it["role"] for it in items if it.get("role"))
    log(f"      표지: {plan['title_top']} / {plan['title_main']} · 전개 {n}장"
        + (f" · 시퀀스 {seq_log}" if seq_log else ""))
    return _produce_pack(cfg2, base, plan, items, beats, rp,
                         {"source": "magazine", "topic": topic,
                          "ref_handle": ref_handle}, log=log)


def _rss_titles(query, n=5, lang="ko"):
    """구글뉴스 RSS — 공식 공개 피드라 계정 리스크 없음. lang='ja'면 일본판."""
    tail = ("&hl=ja&gl=JP&ceid=JP:ja" if lang == "ja"
            else "&hl=ko&gl=KR&ceid=KR:ko")
    url = ("https://news.google.com/rss/search?q=" + requests.utils.quote(query)
           + tail)
    try:
        xml = requests.get(url, timeout=15,
                           headers={"User-Agent": "Mozilla/5.0"}).text
        root = ET.fromstring(xml)
        out = []
        for item in root.iter("item"):
            t = (item.findtext("title") or "").strip()
            u = (item.findtext("link") or "").strip()
            if t:
                out.append({"title": re.sub(r"\s*-\s*[^-]+$", "", t), "url": u})
            if len(out) >= n:
                break
        return out
    except Exception:
        return []


VERIFY_PROMPT = """아래는 소재 목록과, 소재마다 자동 검색으로 수집된 근거 기사 제목들이다.
각 근거가 그 소재와 '같은 사건·현상'을 다루는지 판정하라. 키워드만 겹치는 다른 사건,
무관한 기사는 제거 대상이다. JSON만 출력 (전부 관련이면 빈 배열):
{"drop": [[소재번호, 근거번호], ...]}

"""


def _krjp_verify(cfg, items, cands, log=print):
    """출처 교차검증 — 소재마다 뉴스 재검색(관련 기사 없으면 검색어 바꿔 더 넓게) +
    커뮤 후보 목록 교차 대조 + Gemini 관련성 최종 판정.
    등급: 뉴스 확인 > 커뮤 교차 > 커뮤 단독(사실 단정 금지)."""
    import concurrent.futures as cf

    def _tokens(s):
        stop = {"한국", "일본", "논란", "화제", "이유", "반응", "커뮤니티", "온라인",
                "사건", "근황", "공개", "영상", "사진", "모바일", "게임", "방송"}
        return [w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", s or "")
                if w not in stop]

    def _verify(it):
        topic = it.get("topic") or ""
        own = {(it.get("src_url") or "").strip()}
        toks = set(_tokens(topic) + _tokens(it.get("src_title") or "")
                   + _tokens(it.get("q") or ""))
        # ① 뉴스 재검색 — 관련 기사(핵심 명사 2개+ 겹침)를 못 찾으면
        #    검색어를 바꿔가며 더 넓게 재검색
        queries, seen_q = [], set()
        for q in [it.get("q"), " ".join(_tokens(topic)[:3]),
                  " ".join(_tokens(it.get("src_title") or "")[:3])]:
            q = (q or "").strip()
            if q and q not in seen_q:
                seen_q.add(q)
                queries.append(q)
        news = []
        for q in queries:
            for r in _rss_titles(q, n=4):
                if r["url"] in own or any(e["url"] == r["url"] for e in news):
                    continue
                if len(toks & set(_tokens(r["title"]))) < 2:
                    continue
                news.append({"src": "뉴스", "title": r["title"], "url": r["url"]})
            if news:
                break
        # ② 커뮤 교차 — 다른 커뮤 후보 제목과 핵심 명사 2개 이상 겹치면 근거
        cross = []
        for c in cands:
            if c.get("src") in ("뉴스", "일본뉴스", "급상승", "유튜브") \
                    or c.get("url", "") in own:
                continue
            if len(toks & set(_tokens(c.get("title") or ""))) >= 2:
                cross.append({"src": c.get("src") or "커뮤",
                              "title": c["title"], "url": c["url"]})
        # ③ 일본 반응 — 이 소재가 일본에서도 언급되는지 (수요 신호, 관련성은 AI가 판정)
        jp = []
        jq = (it.get("jq") or "").strip()
        for r in (_rss_titles(jq, n=3, lang="ja") if jq else []):
            if r["url"] not in own:
                jp.append({"src": "일본뉴스", "title": r["title"], "url": r["url"]})
        it["evidence"] = (news + jp + cross)[:5]
        return it

    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        items = list(ex.map(_verify, items))
    # ③ Gemini 관련성 최종 판정 — 키워드만 겹친 다른 사건 근거 제거 (실패 시 그대로)
    try:
        listing = ""
        for i, it in enumerate(items):
            if not it.get("evidence"):
                continue
            listing += f"소재{i}: {it.get('topic')}\n"
            for j, e in enumerate(it["evidence"]):
                listing += f"  근거{j}: [{e['src']}] {e['title']}\n"
        if listing:
            body = {"contents": [{"role": "user",
                                  "parts": [{"text": VERIFY_PROMPT + listing}]}],
                    "generationConfig": {"response_mime_type": "application/json",
                                         "temperature": 0.1,
                                         "maxOutputTokens": 1024,
                                         "thinkingConfig": {"thinkingBudget": 0}}}
            resp = requests.post(
                GEMINI_URL.format(model=cfg.get("gemini_model", "gemini-2.5-flash")),
                params={"key": (cfg.get("gemini_api_key") or "").strip()},
                json=body, timeout=60)
            drops = {(int(a), int(b)) for a, b in
                     (_parse_json(resp.json()["candidates"][0]["content"]
                                  ["parts"][0]["text"]) or {}).get("drop", [])}
            for i, it in enumerate(items):
                it["evidence"] = [e for j, e in enumerate(it.get("evidence", []))
                                  if (i, j) not in drops]
    except Exception as e:
        log(f"관련성 판정 생략(근거 그대로 유지): {str(e)[:60]}")
    for it in items:
        ev = it.get("evidence", [])
        it["jp_demand"] = any(e["src"] == "일본뉴스" for e in ev)
        it["src_grade"] = ("뉴스 확인"
                           if any(e["src"] in ("뉴스", "일본뉴스") for e in ev)
                           else "커뮤 교차" if ev else "커뮤 단독")
    n_news = sum(1 for i in items if i["src_grade"] == "뉴스 확인")
    n_solo = sum(1 for i in items if i["src_grade"] == "커뮤 단독")
    n_jp = sum(1 for i in items if i["jp_demand"])
    log(f"출처 교차검증 — 뉴스 확인 {n_news} · 커뮤 교차 "
        f"{len(items) - n_news - n_solo} · 커뮤 단독 {n_solo} · 🇯🇵일본 화제 {n_jp}")
    return items


def suggest_krjp(cfg, base, axis="", exclude=None, log=print):
    """커뮤 인기글 + 뉴스RSS → 일본 타겟 소재 후보 (7축 분류 + 수위 가드 + 출처 검증).
    axis를 주면 그 갈래 전용 스캔 (전용 검색 줄기 추가 + 그 갈래만 선별).
    exclude(이미 제시한 주제 목록)를 주면 '더보기' — 겹치지 않는 새 주제만 뽑는다."""
    key = (cfg.get("gemini_api_key") or "").strip()
    if not key:
        raise RuntimeError("Gemini API 키가 없습니다")
    axis = (axis or "").strip()
    if axis and axis not in KRJP_AXIS_QUERIES:
        raise RuntimeError(f"모르는 갈래: {axis}")
    exclude = [str(t).strip() for t in (exclude or []) if str(t).strip()]
    log(f"소재 수집 중 (커뮤 9곳 + 뉴스 + 일본 수요 신호"
        + (f" + '{axis}' 전용 줄기)..." if axis else ")..."))
    cands = []
    try:
        from src import hunter
        for it in hunter.hunt(str(base), per_site=8)[:60]:
            cands.append({"title": it.get("title", ""), "url": it.get("url", ""),
                          "src": it.get("site", "커뮤")})
    except Exception as e:
        log(f"커뮤 수집 일부 실패(뉴스로 계속): {str(e)[:80]}")
    for q in KRJP_RSS_QUERIES:
        for it in _rss_titles(q, n=4):
            cands.append({"title": it["title"], "url": it["url"], "src": "뉴스"})
    for q in KRJP_AXIS_QUERIES.get(axis, []):
        for it in _rss_titles(q, n=5):
            cands.append({"title": it["title"], "url": it["url"], "src": "뉴스"})
    # 일본 수요 신호 — 일본 매체가 다루는 한국 + 한국 실시간 급상승 + 유튜브 인기
    for q in KRJP_JP_QUERIES:
        for it in _rss_titles(q, n=3, lang="ja"):
            cands.append({"title": it["title"], "url": it["url"], "src": "일본뉴스"})
    for it in _trends_kr(n=8):
        cands.append({"title": it["title"], "url": it["url"], "src": "급상승"})
    for it in _yt_hot(base, n=10):
        cands.append({"title": it["title"], "url": it["url"], "src": "유튜브"})
    if not cands:
        raise RuntimeError("소재 후보를 하나도 모으지 못했습니다")
    listing = "\n".join(f"- [{c['src']}] {c['title']} | {c['url']}"
                        for c in cands[:140])
    log(f"후보 {min(len(cands), 140)}개 → Gemini 선별 중...")
    ptxt = KRJP_PROMPT + listing + (KRJP_AXIS_NOTE.format(axis=axis) if axis else "")
    if exclude:
        ptxt += ("\n\n[이미 제시한 주제 — 아래와 같거나 사실상 같은 사건·인물·소재는 "
                 "절대 다시 내지 마라. 완전히 다른 새 주제만 선별하라]\n"
                 + "\n".join(f"- {t}" for t in exclude[:30]))
    body = {
        "contents": [{"role": "user", "parts": [{"text": ptxt}]}],
        "generationConfig": {"response_mime_type": "application/json",
                             "temperature": 0.5, "maxOutputTokens": 4096,
                             "thinkingConfig": {"thinkingBudget": 0}},
    }
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    resp = requests.post(GEMINI_URL.format(model=model),
                         params={"key": key}, json=body, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini 오류 {resp.status_code}: {resp.text[:160]}")
    raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    items = (_parse_json(raw) or {}).get("items", [])
    items = [i for i in items if isinstance(i, dict) and i.get("topic")][:12]
    if exclude:                      # Gemini가 그래도 반복한 주제는 후필터로 제거
        exset = {t for t in exclude}
        items = [i for i in items
                 if str(i.get("topic") or "").strip() not in exset
                 and not any(t and (t in str(i.get("topic") or "")
                                    or str(i.get("topic") or "").strip() in t)
                             for t in exset)]
    log(f"소재 {len(items)}개 선별 → 출처 교차검증 중...")
    items = _krjp_verify(cfg, items, cands, log=log)
    log(f"✅ 소재 {len(items)}개 선별")
    return items
