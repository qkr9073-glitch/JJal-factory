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
from datetime import datetime, timezone
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

    # 좋아요 상위 게시물: 표지 8장 + 상위 3개는 안쪽 2장씩 (형식 학습 + 리메이크 재료)
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
            for j, c in enumerate(children[1:3], 2):
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
  "topic_axes": ["소재 축 3~6개 (예: 해외 화제, 참여형 순위, ...)"],
  "engagement_formula": "댓글·저장을 부르는 장치 분석 2~3문장",
  "level_guide": "수위 지침 — 이 채널의 도발/논쟁 수위를 우리가 유지하는 방법. 혐오 배제 원칙 포함 2~3문장",
  "ai_cover_style": "표지용 AI 이미지 생성 지침 (글자 없는 장면·스타일 묘사, 영문 프롬프트 힌트 포함) 2~3문장",
  "guide": "[참고 형식] 으로 시작하는, 카드뉴스 생성 프롬프트에 그대로 주입할 지침 문단. 위 요소를 5~8개 불릿(- )으로. 내용은 우리 소재를 쓰되 형식만 따른다는 점 명시."
}}"""


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
"""


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
                    "한국 여행 일본인", "K푸드 일본 반응"]

KRJP_PROMPT = """당신은 '일본인 타겟 한국 정보 인스타 채널'의 소재 편집장이다.
아래는 한국 커뮤니티 인기글과 뉴스 제목들이다. 일본 시청자가 반응할 소재를 골라라.

벤치마크 채널(@justdoeatjapan — 한국인에게 일본 정보)의 거울상이다. 그 채널의 댓글 폭발 공식:
① "거기엔 안 파는" 부러움 소재(한정판·신상) ② 논란 인물 연속극 ③ 사건+미담/공분 ④ 팩트체크
이 4축으로 분류하고, 논쟁을 부르는 소재를 선호하되(수위 유지) **국가·민족 혐오/비하 프레임은 절대 배제**
(인물·사실 중심으로만). 정치 진영 소재도 배제.

각 후보에:
- axis: "한정템" | "인물" | "사건미담" | "팩트체크"
- score: 일본 시청자 반응 예상 1~10 (부러움·놀라움·논쟁 유발력)
- why: 선정 이유 한 줄 (일본 시청자 관점)
- jp_hook: 일본어 표지 후킹 문구 시안 1줄 (「」 스타일, 20자 이내)
- topic: 우리 제작기에 넣을 한국어 주제 문장 1줄

score 6 이상만, 최대 12개, score 내림차순. JSON만 출력:
{"items": [{"axis":"...","score":8,"why":"...","jp_hook":"...","topic":"...","src_title":"원래 제목","src_url":"원래 링크"}]}

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
    except Exception:
        pass
    return cfg, guide


REMAKE_PROMPT = """당신은 인스타그램 캐러셀 '리메이크' 편집자다.
첨부한 것은 레퍼런스 채널의 실제 게시물(표지·안쪽 이미지)과 캡션이다.

임무: 이 게시물의 **사건·정보·중심내용을 그대로 유지**하되, 문장·어순·후킹·구성 표현은
전부 새로 쓴다 (같은 이야기를 우리 말로 다시 쓰는 것 — 표절·벤치마킹 티 제거).
⚠️ 절대 금지: 일반화, 다른 주제로 확장, 리스트형 잡학으로 변형. 원본이 다룬 그 사건/정보만 다룬다.
⚠️ 원문에 없는 사실(이름·수치·장소·결말)을 지어내지 마라. 원문이 안 알려주는 부분은 모호하게 두거나 생략한다.

{guide}

원본 캡션:
{caption}

JSON만 출력:
{{
  "title_top": "표지 인용/배지용 짧은 후킹 한 줄 (따옴표 없이, 18자 이내)",
  "title_main": "표지 헤드라인 (22자 이내, 원본과 다른 표현)",
  "subtitle": "서브라인 한 줄 (충격 디테일·반전 등, 25자 이내, 없으면 빈 문자열)",
  "image_query": "표지 AI 이미지 장면 묘사 — 영문, 글자 없는 장면, 원본 사건을 시각화 (1~2문장)",
  "beats": [
    {{"title": "그 장면을 서술하는 완전한 문장형 후킹 헤드라인 (짧은 소제목 금지 — 원본 안쪽 장처럼 스토리가 읽히게, 20~30자)", "lines": ["본문 문장 1", "본문 문장 2"],
      "image_query": "이 전개 순간의 장면 묘사 — 영문 1문장, 표지와 같은 사건의 연속 컷(같은 장소·인물)"}},
    "... 3~4개 (도입→전개→결말/반전 순)"
  ],
  "caption": "인스타 캡션 전문 — 원본 스토리텔링 톤을 참고해 새로 작성, 이모지로 시작, 400~700자, 마지막에 의견을 묻는 질문",
  "hashtags": "해시태그 5~8개 한 줄 (#으로 시작, 공백 구분)"
}}"""


def remake_build(cfg, base, handle, media_id, log=print):
    """게시물 리메이크: 중심내용 유지 + 표현·후킹 재창조 + AI 썸네일(채널 미감 테마)
    → 완성팩 (build_cardnews와 같은 반환 형태, 수출 탭 호환)."""
    from cardnews import render as card_render
    from cardnews import pipeline as card_pipeline
    import uuid as _uuid
    import zipfile

    cfg2, guide = remake_cfg(cfg, base, handle)
    theme = cfg2.get("card_theme", "smag")
    paths, caption = remake_source(cfg2, base, handle, media_id, log=log)
    key = (cfg2.get("gemini_api_key") or "").strip()
    if not key:
        raise RuntimeError("Gemini API 키가 없습니다")

    log("[1/4] 리메이크 기획 중 (AI 비전) — 중심내용 유지 · 표현 재창조")
    gtxt = f"[형식 지침 — 이 채널의 형식을 따른다]\n{guide}" if guide else ""
    parts = [{"text": REMAKE_PROMPT.format(guide=gtxt, caption=(caption or "(없음)")[:1500])}]
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
             if isinstance(b, dict) and (b.get("title") or "").strip()][:5]
    if len(beats) < 2:
        raise RuntimeError("리메이크 전개를 뽑지 못했습니다 — 다시 시도해주세요")
    def _one(s, limit=None):
        s = _render_safe(s)
        return s[:limit] if limit else s

    items = [{"num": i + 1, "category": "",
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
    log(f"      표지: {plan['title_top']} / {plan['title_main']} · 전개 {n}장")

    return _produce_pack(cfg2, base, plan, items, beats, rp,
                         {"source": "remake", "ref_handle": handle,
                          "ref_post": str(media_id)}, log=log)


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
    log(f"[2/4] AI 이미지 생성 중 — 표지 + 전개 연속 컷 {len(items)}장 (채널 미감)...")
    from src import genimg
    cover_p = pack / "_cover.jpg"
    try:
        genimg.generate_cover(cfg2, plan["image_query"] or plan["title_main"],
                              cover_p, theme=theme, log=log)
        cfg2["cover_image"] = str(cover_p)
        cfg2["_cover_ai"] = True
    except Exception as e:
        log(f"      (AI 표지 실패 — 텍스트 표지로 진행: {str(e)[:70]})")
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
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    meta.update(meta_extra or {})
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
"일본에선 못 산다/못 본다"는 부러움 포인트, 신기함, 논쟁 유발 포인트를 살려라.

⚠️ 주의:
- 소재는 제목 수준의 정보다. 확인 안 된 세부 사실(정확한 수치·출시일·이름)을 지어내지 마라.
  모르는 디테일은 소개·큐레이션 톤으로 일반적으로 서술한다.
- 후킹은 강하게, 국가·집단 혐오/비하 프레임은 배제. 정치 소재 배제.

JSON만 출력:
{{
  "title_top": "표지 배지용 짧은 후킹 (18자 이내)",
  "title_main": "표지 헤드라인 (22자 이내)",
  "subtitle": "서브라인 (부러움/충격 포인트, 25자 이내, 없으면 빈 문자열)",
  "image_query": "표지 AI 이미지 장면 묘사 — 영문, 글자 없는 장면 (1~2문장)",
  "beats": [
    {{"title": "장면을 서술하는 완전한 문장형 헤드라인 (20~30자)", "lines": ["본문 문장 1", "본문 문장 2"],
      "image_query": "이 장면 묘사 — 영문 1문장, 표지와 같은 세계관의 연속 컷"}},
    "... 3~4개"
  ],
  "caption": "인스타 캡션 전문 — 이모지 시작, 400~700자, 마지막에 의견을 묻는 질문",
  "hashtags": "해시태그 5~8개 한 줄"
}}"""


def magazine_build(cfg, base, topic, context="", theme="jmag", log=print):
    """소재(주제 문장) → 매거진 완성팩. 역수출 소재 스캔 결과를 바로 제작하는 경로 —
    리메이크와 같은 품질(AI 표지+연속 컷+채널 미감)이되 원본 게시물 없이 주제에서 출발."""
    cfg2 = dict(cfg)
    cfg2["card_theme"] = theme if theme in ("smag", "jmag") else "jmag"
    cfg2["card_brand_context"] = cfg.get("card_brand_context_mag") or NEUTRAL_BRAND
    key = (cfg2.get("gemini_api_key") or "").strip()
    if not key:
        raise RuntimeError("Gemini API 키가 없습니다")
    topic = str(topic or "").strip()
    if len(topic) < 4:
        raise RuntimeError("소재(주제)를 4자 이상 입력해주세요")

    log("[1/4] 소재 기획 중 — 매거진 형식")
    body = {"contents": [{"role": "user", "parts": [{"text": MAGAZINE_PROMPT.format(
                topic=topic, context=(context or "(없음)")[:600])}]}],
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
             if isinstance(b, dict) and (b.get("title") or "").strip()][:5]
    if len(beats) < 2:
        raise RuntimeError("전개를 뽑지 못했습니다 — 다시 시도해주세요")

    def _one(s, limit=None):
        s = _render_safe(s)
        return s[:limit] if limit else s

    items = [{"num": i + 1, "category": "",
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
    log(f"      표지: {plan['title_top']} / {plan['title_main']} · 전개 {n}장")
    return _produce_pack(cfg2, base, plan, items, beats, rp,
                         {"source": "magazine", "topic": topic}, log=log)


def _rss_titles(query, n=5):
    """구글뉴스 RSS — 공식 공개 피드라 계정 리스크 없음."""
    url = ("https://news.google.com/rss/search?q=" + requests.utils.quote(query)
           + "&hl=ko&gl=KR&ceid=KR:ko")
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


def suggest_krjp(cfg, base, log=print):
    """커뮤 인기글 + 뉴스RSS → 일본 타겟 소재 후보 (4축 분류 + 수위 가드)."""
    key = (cfg.get("gemini_api_key") or "").strip()
    if not key:
        raise RuntimeError("Gemini API 키가 없습니다")
    log("소재 수집 중 (커뮤 8곳 + 뉴스 6줄기)...")
    cands = []
    try:
        from src import hunter
        for it in hunter.hunt(str(base), per_site=8)[:60]:
            cands.append({"title": it.get("title", ""), "url": it.get("url", ""),
                          "src": it.get("site", "커뮤")})
    except Exception as e:
        log(f"커뮤 수집 일부 실패(뉴스로 계속): {str(e)[:80]}")
    for q in KRJP_RSS_QUERIES:
        for it in _rss_titles(q, n=5):
            cands.append({"title": it["title"], "url": it["url"], "src": "뉴스"})
    if not cands:
        raise RuntimeError("소재 후보를 하나도 모으지 못했습니다")
    listing = "\n".join(f"- [{c['src']}] {c['title']} | {c['url']}"
                        for c in cands[:90])
    log(f"후보 {min(len(cands), 90)}개 → Gemini 선별 중...")
    body = {
        "contents": [{"role": "user", "parts": [{"text": KRJP_PROMPT + listing}]}],
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
    log(f"✅ 소재 {len(items)}개 선별")
    return items
