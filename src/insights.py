# -*- coding: utf-8 -*-
"""AI 인사이트 주제 추천 — 해외 유튜버/전문가의 '이 프롬프트/툴로 이런 걸 자동화·제작'
류 인사이트를 ①Gemini 웹검색 그라운딩(A) ②유튜브 Data API(B, 키 여러 개 로테이션)로 수집.
국내 소재는 후킹·구성을 크게 바꿔 벤치마킹 티를 없애고, 해외 소재는 한국 정서로 현지화한다.
각 인사이트는 카드 파이프라인의 기존 context(기획 근거) 훅으로 흘러들어간다."""
import json
import os
import re
from datetime import datetime, timedelta
from urllib.parse import quote

import requests

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TOPIC_HINT = (
    "쇼츠·유튜브 콘텐츠 제작, AI 자동화, 프롬프트, 수익화 관련을 우선한다. "
    "단, 항목끼리 겹치면 안 된다 — 기획/후킹/대본/편집/썸네일/알고리즘/수익화/자동화/"
    "실패담·역발상 등 서로 다른 각도로 골고루 뽑아라. "
    "title은 '이득이 딱 보이거나 궁금하게'(맹탕 '활용법/가이드/최적화' 금지), "
    "detail에는 실제 툴명·구체 수치·복붙 프롬프트·단계 중 최소 하나를 반드시 넣어 "
    "'바로 카드로 만들 수 있게' 구체적으로 써라.")
# 벤치마킹 티 제거 규칙 (구조화 프롬프트에 주입)
REMIX_RULE = (
    "규칙: 원문 제목/문장을 그대로 베끼지 마라. title은 새로 창작한 후킹 문구로 쓰고, "
    "detail도 우리 표현으로 재구성하되 뜬구름(파악/활용/벤치마킹 같은 추상어로 때우기) 금지 — "
    "구체 툴·수치·예시로 채워라. 각 항목에 origin('국내' 또는 '해외')을 판단해 넣어라. "
    "해외 소재는 트렌드가 빠르니 적극 활용하되 한국 정서로 현지화 가능해야 한다.")


def _key(cfg):
    return (cfg.get("gemini_api_key") or "").strip() or os.environ.get("GEMINI_API_KEY", "")


def _model(cfg):
    return cfg.get("gemini_model", "gemini-2.5-flash")


def _yt_keys(cfg):
    keys = list(cfg.get("youtube_api_keys") or [])
    single = (cfg.get("youtube_api_key") or "").strip()
    if single:
        keys.append(single)
    seen, out = set(), []
    for k in keys:
        k = (k or "").strip()
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _parse_json(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    if not text.lstrip().startswith("{"):
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            text = m.group(0)
    return json.loads(text)


def _remix_ctx(title, detail, source, channel, origin):
    """카드 생성기에 주입할 근거 + 벤치마킹 티 제거 지시."""
    d = ("이 인사이트는 '소재/힌트'로만 참고하라. 제목·후킹 문구·카드 구성은 새로 창작해 "
         "원본을 벤치마킹한 티가 나지 않게 할 것. ")
    if origin == "해외":
        d += "출처가 해외이므로 단순 번역이 아니라 한국 크리에이터 톤·한국 예시로 자연스럽게 현지화하라. "
    else:
        d += "출처가 국내이므로 표현과 구성을 특히 많이 바꿔 원본과 겹치지 않게 하라. "
    return (f"[AI 인사이트 참고 — {channel}]\n주제: {title}\n핵심: {detail}\n출처: {source}\n\n[지시] {d}")[:1100]


def _shape(items, channel):
    out = []
    today = datetime.now().strftime("%Y.%m.%d")
    for it in items:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        source = (it.get("source") or "").strip()
        detail = (it.get("detail") or "").strip()
        url = (it.get("url") or "").strip()
        origin = "해외" if str(it.get("origin", "")).strip() in ("해외", "global", "overseas") else "국내"
        # 링크 신뢰성: 유튜브는 직링크 유지, 웹은 그라운딩 URL이 만료·부정확할 수 있어
        # 구글 검색으로 대체(항상 원문에 닿음).
        if channel == "유튜브" and url.startswith("http") and "youtube.com" in url:
            final_url, kind = url, "video"
        else:
            q = (f"{source} {title}").strip() or title
            final_url, kind = "https://www.google.com/search?q=" + quote(q), "search"
        out.append({
            "title": title[:90], "channel": channel, "origin": origin,
            "cat": f"{origin}·{channel}", "source": source[:60], "date": today,
            "detail": detail[:400], "url": final_url[:500], "url_kind": kind,
            "ctx": _remix_ctx(title, detail, source, channel, origin),
        })
    return out


def _structure(cfg, raw_text, channel, n, focus=None):
    """원자료(텍스트) → 카드 주제 items JSON (강제 JSON, 리믹스 규칙 적용)."""
    key = _key(cfg)
    if not key or not raw_text.strip():
        return []
    focus_line = f"특히 '{focus}' 관련을 우선해서 골라라. " if focus else ""
    prompt = (f"다음 자료에서 인스타 카드뉴스 주제 {n}개를 뽑아라. {TOPIC_HINT} {focus_line}{REMIX_RULE}\n"
              'JSON만 출력: {"items":[{"title":"새로 창작한 후킹 한 줄 주제",'
              '"source":"출처/유튜버/사이트명","origin":"국내 또는 해외",'
              '"url":"관련 원문/영상 링크(자료에 있으면)","detail":"핵심 2~3문장, 구체적 툴·방법 포함"}]}'
              f"\n\n자료:\n{raw_text[:5000]}")
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json",
                                 "temperature": 0.6, "maxOutputTokens": 4096,
                                 "thinkingConfig": {"thinkingBudget": 0}}}
    try:
        r = requests.post(GEMINI_URL.format(model=_model(cfg)),
                          params={"key": key}, json=body, timeout=90)
        return _shape(_parse_json(
            r.json()["candidates"][0]["content"]["parts"][0]["text"]).get("items", []), channel)
    except Exception:
        return []


def fetch_gemini(cfg, n=8, focus=None):
    """A) Gemini 웹검색 그라운딩 → 원자료+출처URL 수집 → 구조화."""
    key = _key(cfg)
    if not key:
        return []
    focus_line = f"특히 '{focus}' 주제를 중심으로. " if focus else ""
    prompt = (f"요즘 해외·국내 AI 유튜버·전문가들이 공유하는 실전 인사이트를 최신 웹 기준으로 {n + 4}개 찾아줘. "
              f"{focus_line}{TOPIC_HINT} 각각 핵심 방법·툴명·출처를 구체적으로 적어줘. "
              "해외에서 먼저 뜬 기술·주제도 적극 포함해줘(한국에 아직 덜 알려진 것 위주).")
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 2048,
                                 "thinkingConfig": {"thinkingBudget": 0}}}
    try:
        r = requests.post(GEMINI_URL.format(model=_model(cfg)),
                          params={"key": key}, json=body, timeout=90)
        cand = r.json()["candidates"][0]
        text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
        gm = cand.get("groundingMetadata") or cand.get("grounding_metadata") or {}
        chunks = gm.get("groundingChunks") or gm.get("grounding_chunks") or []
        srcs = []
        for c in chunks[:12]:
            w = c.get("web", {})
            if w.get("uri"):
                srcs.append(f"{w.get('title', '')} :: {w.get('uri')}")
        if srcs:
            text += "\n\n[출처 링크들]\n" + "\n".join(srcs)
    except Exception:
        return []
    return _structure(cfg, text, "웹", n, focus)


def _recent_iso(days=75):
    return (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_youtube(cfg, n=8, focus=None):
    """B) 유튜브 Data API(키 여러 개 로테이션) → 인기 영상 → Gemini가 카드 주제로 변환."""
    keys = _yt_keys(cfg)
    if not keys:
        return []
    queries = list(cfg.get("youtube_queries") or [
        "AI automation workflow tutorial", "ChatGPT prompt engineering tips",
        "faceless AI youtube shorts", "AI 자동화 프롬프트 꿀팁"])
    if focus:
        queries = [focus] + queries
    vids, ki = [], 0
    for q in queries[:5]:
        got = False
        while ki < len(keys) and not got:
            try:
                r = requests.get(
                    "https://www.googleapis.com/youtube/v3/search",
                    params={"key": keys[ki], "part": "snippet", "q": q, "type": "video",
                            "order": "viewCount", "maxResults": 6,
                            "publishedAfter": _recent_iso()},
                    timeout=30)
                if r.status_code == 403:  # 할당량 소진 → 다음 키로
                    ki += 1
                    continue
                for it in r.json().get("items", []):
                    sn = it.get("snippet", {})
                    vid = it.get("id", {}).get("videoId", "")
                    vids.append({"title": sn.get("title", ""),
                                 "desc": sn.get("description", ""),
                                 "channel": sn.get("channelTitle", ""),
                                 "url": f"https://www.youtube.com/watch?v={vid}" if vid else ""})
                got = True
            except Exception:
                ki += 1
        if ki >= len(keys):
            break
    if not vids:
        return []
    key = _key(cfg)
    if not key:
        return _shape([{"title": v["title"][:60], "source": v["channel"],
                        "url": v["url"], "origin": "해외", "detail": v["desc"][:200]}
                       for v in vids[:n]], "유튜브")
    listing = "\n".join(f"- [{v['channel']}] {v['title']} :: {v['desc'][:120]} :: {v['url']}"
                        for v in vids[:22])
    focus_line = f"특히 '{focus}' 관련을 우선. " if focus else ""
    prompt = (f"아래는 유튜브 영상 목록이야. 인스타 카드뉴스로 만들 만한 "
              f"'이 프롬프트/툴로 이런 걸 자동화·제작' 류 주제 {n}개를 골라 한국어로 정리해. "
              f"{focus_line}{TOPIC_HINT} {REMIX_RULE} 대부분 해외 영상이면 origin은 '해외'로.\n"
              'JSON만: {"items":[{"title":"새로 창작한 후킹 한 줄 주제","source":"유튜브 채널명",'
              '"origin":"국내 또는 해외","url":"해당 영상 링크","detail":"핵심 2~3문장"}]}\n\n' + listing)
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json",
                                 "temperature": 0.6, "maxOutputTokens": 4096,
                                 "thinkingConfig": {"thinkingBudget": 0}}}
    try:
        r = requests.post(GEMINI_URL.format(model=_model(cfg)),
                          params={"key": key}, json=body, timeout=90)
        return _shape(_parse_json(
            r.json()["candidates"][0]["content"]["parts"][0]["text"]).get("items", []), "유튜브")
    except Exception:
        return _shape([{"title": v["title"][:60], "source": v["channel"], "url": v["url"],
                        "origin": "해외", "detail": v["desc"][:200]} for v in vids[:n]], "유튜브")


def fetch(cfg, n=8, focus=None):
    """A(웹) + B(유튜브) 합치기. 유튜브 키 없으면 웹만."""
    items = fetch_gemini(cfg, n, focus) + fetch_youtube(cfg, n, focus)
    return {"items": items, "youtube_ready": bool(_yt_keys(cfg))}
