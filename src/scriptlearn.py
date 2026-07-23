# -*- coding: utf-8 -*-
"""대본 학습 + 스타일 프로파일. 계정(회원코드)별 저장.
- 인스타 대본추출로 모인 대본을 AI가 자동분류(쇼핑/유머/이슈/썰… 사용자 추가·이름변경 가능).
- 분류별 누적학습(덮어쓰기 아니라 쌓임) → 스타일 프로파일(구조/어투/소재선정)을 재분석.
- 분류 삭제/초기화, 다른 계정 프로파일 불러오기 지원.
저장: BASE/profiles/<code>.json = {learned_ids:[...], categories:{name:{scripts,profile,updated}}}
"""
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import requests

from . import brain

_NCP_TREND = "https://naverapihub.apigw.ntruss.com/search-trend/v1/search"


def _ncp_headers(cfg):
    return {"X-NCP-APIGW-API-KEY-ID": (cfg.get("naver_client_id") or "").strip(),
            "X-NCP-APIGW-API-KEY": (cfg.get("naver_client_secret") or "").strip(),
            "Content-Type": "application/json"}


def search_trend_rise(cfg, keywords):
    """키워드별 최근 30일 상승도(마지막주-첫주 비율차). {kw: rise}. 미구독/오류면 {}."""
    cid = (cfg.get("naver_client_id") or "").strip()
    sec = (cfg.get("naver_client_secret") or "").strip()
    if not cid or not sec or not keywords:
        return {}
    end = date.today()
    start = end - timedelta(days=30)
    H = _ncp_headers(cfg)
    out = {}
    for off in range(0, len(keywords), 5):
        grp = keywords[off:off + 5]
        body = {"startDate": start.isoformat(), "endDate": end.isoformat(), "timeUnit": "week",
                "keywordGroups": [{"groupName": k, "keywords": [k]} for k in grp]}
        try:
            r = requests.post(_NCP_TREND, headers=H, json=body, timeout=15)
            if r.status_code != 200:
                return {}   # 미구독(210) 등 → 전체 트렌드 없음으로 처리
            for res in r.json().get("results", []):
                data = res.get("data", [])
                if data:
                    vals = [float(d.get("ratio", 0) or 0) for d in data]
                    out[res.get("title", "")] = round(vals[-1] - vals[0], 1)
        except Exception:
            return {}
    return out


def gen_candidates(cfg, base, code, category, n=12):
    """프로파일 스타일에 맞는(관련도) 소재 후보 키워드 n개."""
    c = (load(base, code).get("categories") or {}).get(category)
    if not c:
        raise RuntimeError("학습된 분류가 아닙니다")
    prof = c.get("profile") or {}
    prompt = f"""'{category}' 성격의 쇼츠 채널이다. 아래 스타일 프로파일을 보고, 이 채널이 다룰 법하면서
요즘 통할 만한 '소재(제품/아이템/주제)' 후보 {n}개를 뽑아라.
- 이 채널의 톤·소재 성향에 맞는 것만. 너무 뻔한 것보다 시의성 있는 것 위주.
- 각 후보는 검색 키워드 형태(1~3단어).
[스타일 프로파일]
{json.dumps(prof, ensure_ascii=False)}
반드시 JSON만: {{"items":["키워드1","키워드2", ...]}}"""
    r = _gem(cfg, prompt, maxtok=1024)
    seen, out = set(), []
    for x in (r.get("items") or []):
        k = str(x).strip()
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out[:n]


def recommend(cfg, base, code, category, log=print):
    """관련도(AI 후보) + 트렌드 상승도(네이버) 하이브리드 소재 추천."""
    cands = gen_candidates(cfg, base, code, category)
    rise = search_trend_rise(cfg, cands)
    items = []
    for kw in cands:
        items.append({"keyword": kw, "rise": rise.get(kw),
                      "shop": f"https://search.shopping.naver.com/search/all?query={quote(kw)}"})
    if rise:   # 트렌드 되면 상승도순(하이브리드: 이미 관련도로 거른 뒤 상승도 정렬)
        items.sort(key=lambda x: (x["rise"] is None, -(x["rise"] or 0)))
    return {"items": items, "trend": bool(rise)}


def _key(cfg):
    return (cfg.get("gemini_api_key") or "").strip()


def _model(cfg):
    return cfg.get("gemini_model", "gemini-2.5-flash")


def _dir(base):
    d = Path(base) / "profiles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(base, code):
    return _dir(base) / f"{str(code).strip()}.json"


def load(base, code):
    try:
        d = json.loads(_path(base, code).read_text(encoding="utf-8"))
        d.setdefault("learned_ids", [])
        d.setdefault("categories", {})
        return d
    except Exception:
        return {"learned_ids": [], "categories": {}}


def add_own_script(base, code, category, text):
    """② 최종 확정 대본을 스타일 프로파일에 누적 학습(중복 방지).
    generate_scripts의 예시 대본으로 바로 쓰여 다음 대본이 내 확정 스타일을 따라간다."""
    import hashlib
    from datetime import datetime as _dt
    text = (text or "").strip()
    if not text or len(text) < 30:
        return False
    data = load(base, code)
    sid = "own_" + hashlib.md5(text.encode("utf-8")).hexdigest()[:12]
    if sid in (data.get("learned_ids") or []):
        return False
    cat = (category or "").strip() or "내 대본"
    c = data["categories"].setdefault(cat, {"scripts": [], "profile": {}, "updated": ""})
    c.setdefault("scripts", []).append({"text": text, "src": "own_final"})
    c["scripts"] = c["scripts"][-60:]
    c["updated"] = _dt.now().isoformat(timespec="seconds")
    data["learned_ids"].append(sid)
    save(base, code, data)
    return True


def save(base, code, data):
    _path(base, code).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _gem(cfg, prompt, maxtok=3072):
    key = _key(cfg)
    if not key:
        raise RuntimeError("Gemini API 키가 없습니다")
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json", "temperature": 0.3,
                                 "maxOutputTokens": maxtok, "thinkingConfig": {"thinkingBudget": 0}}}
    r = requests.post(brain.GEMINI_URL.format(model=_model(cfg)), params={"key": key}, json=body, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini 오류 {r.status_code}: {r.text[:150]}")
    cand = r.json()["candidates"][0]
    raw = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
    return brain._parse_json(raw)


def classify(cfg, texts, existing):
    """대본 여러 개 → 각 카테고리명. {지역인덱스: 카테고리}."""
    lst = "\n".join(f"[{i}] {str(t)[:1200]}" for i, t in enumerate(texts))
    prompt = f"""아래 여러 쇼츠 대본을 각각 성격에 맞는 카테고리로 분류하라.
기존 카테고리: {', '.join(existing) if existing else '(없음)'} — 맞으면 그대로 재사용, 없으면 간결한 한국어 명사로 새로 지어라(예: 쇼핑, 유머, 이슈, 썰, 정보, 브이로그).
기준: 제품·물건 소개=쇼핑 / 웃기려는=유머 / 시사·화제=이슈 / 경험담·이야기=썰 등.
대본들:
{lst}
반드시 JSON만: {{"items":[{{"i":0,"category":".."}}, ...]}}"""
    r = _gem(cfg, prompt, maxtok=2048)
    out = {}
    for it in r.get("items", []):
        try:
            out[int(it["i"])] = (str(it.get("category", "")).strip() or "기타")
        except Exception:
            pass
    return out


def analyze(cfg, category, scripts):
    """분류 하나의 대본들 → 스타일 프로파일(새 대본 작성에 쓸 학습결과)."""
    sample = scripts[-20:]
    body = "\n\n---\n".join(str(s.get("text", ""))[:1500] for s in sample)
    prompt = f"""아래는 '{category}' 성격의 쇼츠 대본 모음이다(총 {len(scripts)}개 중 표본 {len(sample)}개).
이 스타일을 학습해 '같은 톤의 새 대본'을 쓸 때 쓸 스타일 프로파일을 뽑아라.
{body}

반드시 JSON만:
{{"structure":"대본 전개 구조(훅→전개→반전→CTA 등 단계와 각 역할)",
"hook":"첫 문장(훅) 패턴 특징",
"tone":"어투/문체(반말·존댓말, 톤, 특징적 말투)",
"rhythm":"문장 길이·리듬·호흡 특징",
"item_selection":"어떤 소재를 어떻게 고르고 어떤 각도로 다루는지",
"length":"대체적 길이(문장 수/초 느낌)",
"summary":"이 스타일을 한 문단으로 요약"}}"""
    return _gem(cfg, prompt, maxtok=3072)


def learn(cfg, base, code, corpus, log=print):
    """corpus: [{'id':.., 'text':..}]. 자동분류→분류별 누적→영향받은 분류 재분석→저장."""
    data = load(base, code)
    cats = data["categories"]
    learned = set(data["learned_ids"])
    corpus = [c for c in corpus if c.get("id") and c.get("text") and c["id"] not in learned][:80]
    if not corpus:
        return {"added": 0, "affected": [], "categories": summary(base, code)}

    log(f"[1/2] 대본 {len(corpus)}개 AI 자동분류...")
    cls = {}
    CH = 25
    for off in range(0, len(corpus), CH):
        part = corpus[off:off + CH]
        r = classify(cfg, [c["text"] for c in part], list(cats.keys()))
        for k, v in r.items():
            cls[off + k] = v

    affected = set()
    for i, c in enumerate(corpus):
        cat = cls.get(i, "기타")
        entry = cats.setdefault(cat, {"scripts": [], "profile": {}, "updated": ""})
        entry["scripts"].append({"text": c["text"], "src": c["id"],
                                 "at": datetime.now().isoformat(timespec="seconds")})
        learned.add(c["id"])
        affected.add(cat)

    log(f"[2/2] 분류 {len(affected)}개 스타일 프로파일 갱신...")
    for cat in affected:
        try:
            cats[cat]["profile"] = analyze(cfg, cat, cats[cat]["scripts"])
            cats[cat]["updated"] = datetime.now().isoformat(timespec="seconds")
        except Exception as e:
            log(f"   {cat} 분석 실패(원본 유지): {str(e)[:60]}")

    data["learned_ids"] = list(learned)
    save(base, code, data)
    return {"added": len(corpus), "affected": sorted(affected), "categories": summary(base, code)}


_TONE = {
    "mild": "톤 조절: 학습 스타일은 유지하되 과장·하이프를 확 줄여 담백하고 차분하게 써라. 과한 감탄사·낚시성·단정 표현은 자제.",
    "basic": "",
    "strong": "톤 조절: 학습된 톤보다 더 세고 몰입감 있는 훅·표현으로 써라(단, 허위·과대광고성 표현은 금지).",
}


def generate_scripts(cfg, base, code, category, topic, n=3, tone="basic"):
    """학습된 '{category}' 프로파일 스타일로 '{topic}' 소재의 쇼츠 대본 n버전 생성. tone: mild|basic|strong."""
    data = load(base, code)
    c = (data.get("categories") or {}).get(category)
    if not c:
        raise RuntimeError("학습된 분류가 아닙니다")
    prof = c.get("profile") or {}
    proftxt = "\n".join(f"- {k}: {v}" for k, v in prof.items() if v)
    examples = "\n\n---\n".join(str(s.get("text", ""))[:600] for s in c.get("scripts", [])[-3:])
    tone_line = _TONE.get(tone, "")
    # 쇼핑 소재면 3번째 버전은 무조건 '실제 써본 후기(내/지인 경험담)' 톤으로 고정
    shop_line = ""
    if "쇼핑" in (category or ""):
        shop_line = ("- 특히 마지막(3번째) 버전은 '제가(또는 가까운 지인이) 실제로 써본 후기' 1인칭 체험담 톤. "
                     "존댓말('저', '~요/~어요')로 쓰고 '나도'처럼 반말 금지. "
                     "⚠️ 첫 문장(훅)은 '저도~'로 시작하지 말 것 — 매번 소재에 맞는 다른 후킹으로 시작하고, "
                     "체험담 느낌(반신반의하다 써보고 만족한 진짜 경험)은 본문에서 자연스럽게 풀어라.")
    prompt = f"""너는 '{category}' 성격의 쇼츠 대본 작가다. 아래 학습된 스타일 프로파일과 예시 대본을 충실히 따라,
소재 '{topic}'에 대한 쇼츠 대본을 {n}가지 버전으로 써라.
- {n}개는 서로 '훅과 구성'이 뚜렷이 달라야 한다. 단, 전체 톤·문체·구조는 이 프로파일에서 벗어나지 말 것.
- 각 대본은 실제 내레이션할 문장만. 한 줄에 한 문장(의미 단위)으로 줄바꿈. 지시문/괄호/이모지 금지.
{tone_line and ('- ' + tone_line)}
{shop_line}

[학습된 스타일 프로파일]
{proftxt}

[예시 대본]
{examples}

반드시 JSON만:
{{"versions":[{{"title":"이 버전 한 줄 컨셉","approach":"훅·구성 특징 한 줄","script":"한 줄에 한 문장씩\\n줄바꿈으로 이어진 대본 전문"}}]}}"""
    r = _gem(cfg, prompt, maxtok=4096)
    out = []
    for v in (r.get("versions") or [])[:n]:
        out.append({"title": str(v.get("title", "")).strip(),
                    "approach": str(v.get("approach", "")).strip(),
                    "script": str(v.get("script", "")).strip()})
    return out


def summary(base, code):
    data = load(base, code)
    out = []
    for name, c in (data.get("categories") or {}).items():
        prof = c.get("profile") or {}
        out.append({"name": name, "count": len(c.get("scripts", [])),
                    "summary": prof.get("summary", ""), "updated": c.get("updated", "")})
    out.sort(key=lambda x: -x["count"])
    return out


def profile(base, code, name):
    c = (load(base, code).get("categories") or {}).get(name)
    if not c:
        return None
    return {"name": name, "count": len(c.get("scripts", [])), "profile": c.get("profile", {})}


def rename(base, code, old, new):
    new = str(new).strip()
    data = load(base, code)
    cats = data["categories"]
    if old not in cats:
        raise RuntimeError("없는 분류입니다")
    if not new:
        raise RuntimeError("새 이름을 입력하세요")
    if new in cats and new != old:
        raise RuntimeError("이미 있는 이름입니다")
    cats[new] = cats.pop(old)
    save(base, code, data)


def delete(base, code, name):
    """분류 삭제 + 그 분류가 학습했던 대본을 learned에서 빼서 재학습 가능하게(초기화)."""
    data = load(base, code)
    cats = data["categories"]
    srcs = {s.get("src") for s in cats.get(name, {}).get("scripts", [])}
    data["learned_ids"] = [x for x in data["learned_ids"] if x not in srcs]
    cats.pop(name, None)
    save(base, code, data)


def import_cats(base, my_code, other_code, names):
    """다른 계정의 분류 프로파일을 내 목록으로 복사(불러오기)."""
    src = load(base, other_code)
    dst = load(base, my_code)
    scats = src.get("categories", {})
    dcats = dst["categories"]
    n = 0
    for nm in names:
        if nm in scats:
            key = nm if nm not in dcats else f"{nm} (가져옴)"
            dcats[key] = json.loads(json.dumps(scats[nm]))
            n += 1
    save(base, my_code, dst)
    return n
