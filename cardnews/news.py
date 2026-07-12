# -*- coding: utf-8 -*-
"""뉴스 헌터 — 구글 뉴스 RSS로 쇼츠/AI/크리에이터 최신 소식을 수집해
'오늘의 주제 추천'과 최신정보 기반 카드뉴스의 근거로 제공한다. API 키 불필요.
날것 헤드라인은 '기사 제목'일 뿐이라 카드 소재로는 밍밍해서, Gemini로 한 번 더
'바로 카드로 만들 앵글'까지 가공한다(fetch_topics). 키 없으면 날것 그대로 폴백."""
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

RSS_URL = ("https://news.google.com/rss/search?q={q}+when:7d"
           "&hl=ko&gl=KR&ceid=KR:ko")

QUERIES = [
    ("쇼츠", "유튜브 쇼츠"),
    ("알고리즘", "유튜브 알고리즘"),
    ("AI 영상", "AI 영상 생성"),
    ("AI 도구", "챗GPT 활용"),
    ("릴스", "인스타그램 릴스"),
    ("수익화", "유튜브 수익화 크리에이터"),
]

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _clean_title(t):
    """구글뉴스 제목 끝의 ' - 언론사' 꼬리 제거"""
    return re.sub(r"\s*-\s*[^-]{2,30}$", "", (t or "").strip())


def fetch(per_query=6, log=print):
    """최신 소식 수집. 반환: [{cat, title, source, date, link}] (최신순)"""
    items, seen = [], set()
    for cat, q in QUERIES:
        try:
            r = requests.get(RSS_URL.format(q=quote(q)), headers=HEADERS, timeout=15)
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
        except Exception as e:
            log(f"      뉴스 수집 실패({cat}): {str(e)[:60]}")
            continue
        count = 0
        for it in root.iter("item"):
            title = _clean_title(it.findtext("title"))
            if not title or title[:18] in seen:
                continue
            seen.add(title[:18])
            src = it.findtext("source") or ""
            try:
                dt = parsedate_to_datetime(it.findtext("pubDate"))
                date = dt.strftime("%Y-%m-%d")
            except Exception:
                date = datetime.now().strftime("%Y-%m-%d")
            items.append({"cat": cat, "title": title, "source": src,
                          "date": date, "link": it.findtext("link") or ""})
            count += 1
            if count >= per_query:
                break
    items.sort(key=lambda x: x["date"], reverse=True)
    return items


def context_text(picked):
    """선택된 소식들 → 기획 프롬프트에 넣을 근거 텍스트"""
    return "\n".join(f"- [{p.get('date','')}] {p.get('title','')}"
                     f" ({p.get('source','')})" for p in picked if p.get("title"))


# ---------------------------------------------------------------- 앵글 가공(Gemini)

ANGLE_PROMPT = """너는 유튜브 쇼츠 수익화를 가르치는 강사 '캥거루쇼츠'의 정보형 인스타
카드뉴스 편집장이다. 아래는 오늘의 관련 뉴스 헤드라인 목록이다.
기사 제목은 그대로는 카드 소재로 밍밍하다. 각 소식에서 **'우리 독자(쇼츠로 부수입
만들고 싶은 초보)가 당장 써먹을 카드 주제 앵글'**을 뽑아라.

## 규칙
- 헤드라인을 베끼지 마라. 소식은 '근거'로만 쓰고, title은 이득이 딱 보이거나
  궁금하게 만드는 새 카드 앵글로 창작하라(12~40자).
    ✗ "유튜브, 쇼츠 수익배분 개편"  (그냥 기사 제목)
    ✓ "이번 수익배분 개편으로 '조회수당 단가' 뛰는 채널의 3가지 공통점"
- 카드로 만들 각도가 약한 소식(단순 사건·정치·중복)은 버려라. 억지로 다 쓰지 마라.
- 서로 다른 각도로 최대 {n}개. 겹치면 하나만.

## 오늘의 소식 (번호. [분류] 제목 — 출처)
{listing}

## 출력 (반드시 이 JSON만)
{{"topics": [
  {{"ref": 근거로 쓴 소식 번호(정수),
    "title": "새로 창작한 카드 앵글",
    "why": "이 각도가 왜 반응 나올지 한 줄"}}
]}}
"""


def _gemini_topics(cfg, items, n=10):
    key = (cfg.get("gemini_api_key") or "").strip() or os.environ.get("GEMINI_API_KEY", "")
    if not key or not items:
        return None
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    listing = "\n".join(
        f"{i + 1}. [{it.get('cat','')}] {it.get('title','')} — {it.get('source','')}"
        for i, it in enumerate(items[:26]))
    body = {"contents": [{"role": "user",
                          "parts": [{"text": ANGLE_PROMPT.format(n=n, listing=listing)}]}],
            "generationConfig": {"response_mime_type": "application/json",
                                 "temperature": 0.85, "maxOutputTokens": 4096,
                                 "thinkingConfig": {"thinkingBudget": 512}}}
    try:
        r = requests.post(GEMINI_URL.format(model=model),
                          params={"key": key}, json=body, timeout=90)
        cand = r.json()["candidates"][0]
        raw = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
        return json.loads(raw).get("topics", [])
    except Exception:
        return None


def fetch_topics(cfg, per_query=6, log=print):
    """최신 소식 → Gemini로 '카드 앵글'까지 가공한 추천 목록.
    반환 아이템: {cat, title(앵글), source, date, link, base(원 헤드라인), ctx}.
    Gemini 실패/키 없으면 날것 헤드라인 그대로 폴백."""
    raw = fetch(per_query=per_query, log=log)
    if not raw:
        return []
    topics = _gemini_topics(cfg, raw, n=12)
    if not topics:  # 폴백: 날것이라도 보여준다
        for it in raw:
            it["base"] = it["title"]
        return raw
    out, seen = [], set()
    for t in topics:
        title = str(t.get("title", "")).strip()
        if not title or title in seen:
            continue
        seen.add(title)
        try:
            src = raw[int(t.get("ref", 0)) - 1]
        except (TypeError, ValueError, IndexError):
            src = raw[0]
        base = src.get("title", "")
        ctx = (f"[최신 소식 근거]\n원 소식: {base} ({src.get('source','')}, {src.get('date','')})\n"
               f"카드 앵글: {title}\n\n[지시] 이 소식을 배경 근거로만 쓰고, 아이템은 독자가 "
               f"바로 써먹는 실용 팁으로 변환하라. 소식에 없는 사실은 지어내지 마라.")
        out.append({"cat": src.get("cat", ""), "title": title,
                    "source": src.get("source", ""), "date": src.get("date", ""),
                    "link": src.get("link", ""), "base": base, "ctx": ctx})
    return out or raw
