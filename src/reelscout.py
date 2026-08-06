# -*- coding: utf-8 -*-
"""릴스 정찰(분석 전용) — 레퍼런스 채널의 릴스를 대량 수집·해부해 '릴스 공식'을 뽑는다.

- 목록: 공식 business_discovery 페이지네이션(과거 게시물까지) → 레퍼런스/<handle>/reels.json
- 영상: 버너 부계정 세션(insta_import)으로 소량·스로틀 다운로드 → 레퍼런스/<handle>/reels/*.mp4
- 분석: Gemini 비디오(Files API) — 제작방식/후킹/자막/컷 리듬/구성/사운드 해부
- 종합: 채널 그룹별(jp1=저스트두잇 단독 / jp2=셀렉션 3계정 통합 — 전략이 다름) '릴스 공식'

제작 파이프라인(autoshorts·reelproj)과 분리된 읽기 전용 모듈이다.
"""
import json
import random
import re
import time
from pathlib import Path

import requests

from .reference import REFS_DIRNAME, GEMINI_URL, _parse_json

GRAPH = "https://graph.facebook.com/v23.0/{uid}"
FILES_UPLOAD = "https://generativelanguage.googleapis.com/upload/v1beta/files"
FILES_GET = "https://generativelanguage.googleapis.com/v1beta/{name}"

# 올리는 계정 기준 그룹 — 두 채널의 릴스 전략이 완전히 다르다 (사용자 확정):
# jp1(저스트두잇형)=일본에서 터진 원본 영상 + 간단 자막 풀버전 / jp2(셀렉션형)=AI 제작 다양
GROUPS = {
    "jp1": ["justdoeatjapan"],
    "jp2": ["selectionmgz", "1mintrend", "1mknow"],
}


def _dir(base, handle):
    return Path(base) / REFS_DIRNAME / handle


def _reels_file(base, handle):
    return _dir(base, handle) / "reels.json"


def reels_load(base, handle):
    try:
        return json.loads(_reels_file(base, handle).read_text(encoding="utf-8"))
    except Exception:
        return []


def _reels_save(base, handle, reels):
    d = _dir(base, handle)
    d.mkdir(parents=True, exist_ok=True)
    _reels_file(base, handle).write_text(
        json.dumps(reels, ensure_ascii=False, indent=1), encoding="utf-8")


def collect_reels(cfg, base, handle, max_media=250, log=print):
    """BD 페이지네이션으로 과거 게시물을 훑어 릴스(VIDEO) 목록 대량 수집.
    기존 reels.json의 분석 결과(analysis/file)는 id 기준으로 보존 병합."""
    tok = (cfg.get("fb_long_token") or "").strip()
    uid = str(cfg.get("fb_bd_ig_id") or "").strip()
    if not (tok and uid):
        raise RuntimeError("business_discovery 토큰(fb_long_token/fb_bd_ig_id)이 없습니다")
    old = {str(r.get("id")): r for r in reels_load(base, handle)}
    out, after, fetched = [], "", 0
    while fetched < max_media:
        m = "media.limit(50)" + (f".after({after})" if after else "")
        fields = (f"business_discovery.username({handle})"
                  f"{{{m}{{id,media_type,media_product_type,permalink,caption,"
                  f"like_count,comments_count,timestamp}}}}")
        r = requests.get(GRAPH.format(uid=uid),
                         params={"fields": fields, "access_token": tok}, timeout=60)
        if r.status_code != 200:
            log(f"      (페이지 수집 중단 {r.status_code}: {r.text[:100]})")
            break
        med = (((r.json().get("business_discovery") or {}).get("media")) or {})
        data = med.get("data") or []
        if not data:
            break
        fetched += len(data)
        for it in data:
            if it.get("media_type") != "VIDEO":
                continue
            rid = str(it.get("id"))
            row = old.get(rid, {})
            row.update({
                "id": rid,
                "permalink": it.get("permalink") or row.get("permalink") or "",
                "caption": (it.get("caption") or "")[:500],
                "like": int(it.get("like_count") or 0),
                "comments": int(it.get("comments_count") or 0),
                "ts": it.get("timestamp") or "",
            })
            out.append(row)
        after = ((med.get("paging") or {}).get("cursors") or {}).get("after") or ""
        if not after:
            break
        time.sleep(1.2)
    out.sort(key=lambda x: -(x.get("like") or 0))
    _reels_save(base, handle, out)
    log(f"      @{handle}: 게시물 {fetched}개 훑어 릴스 {len(out)}개 확보")
    return out


def _shortcode(permalink):
    m = re.search(r"/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)", str(permalink or ""))
    return m.group(1) if m else ""


def download_reels(cfg, base, handle, limit=10, log=print):
    """좋아요순 상위 릴스 영상 다운로드 (버너 세션, 릴스당 8~15초 간격 스로틀).
    이미 받은 파일은 건너뜀. 반환: 새로 받은 개수."""
    from . import insta_import
    import instaloader
    L, user = insta_import._loader(cfg, base, log=log)
    reels = reels_load(base, handle)
    vdir = _dir(base, handle) / "reels"
    vdir.mkdir(parents=True, exist_ok=True)
    got = 0
    for r in reels:
        if got >= limit:
            break
        dest = vdir / f"{r['id']}.mp4"
        if dest.exists() and dest.stat().st_size > 50_000:
            r["file"] = str(dest)
            continue
        sc = _shortcode(r.get("permalink"))
        if not sc:
            continue
        try:
            post = instaloader.Post.from_shortcode(L.context, sc)
            url = post.video_url
            if not url:
                continue
            resp = requests.get(url, timeout=180,
                                headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200 or len(resp.content) < 50_000:
                log(f"      (다운로드 실패 {r['id']}: HTTP {resp.status_code})")
                continue
            dest.write_bytes(resp.content)
            r["file"] = str(dest)
            got += 1
            log(f"      ⬇ @{handle} 릴스 {got}/{limit} (♥{r.get('like', 0)}, "
                f"{len(resp.content) // 1024}KB)")
            time.sleep(random.uniform(8, 15))
        except Exception as e:
            log(f"      (릴스 {r['id']} 실패: {str(e)[:80]})")
            time.sleep(random.uniform(10, 20))
    _reels_save(base, handle, reels)
    return got


def _upload_video(key, path, log=print):
    """Gemini Files API 업로드 → ACTIVE 대기 → file_uri 반환."""
    raw = Path(path).read_bytes()
    up = requests.post(FILES_UPLOAD, params={"key": key},
                       headers={"X-Goog-Upload-Protocol": "raw",
                                "Content-Type": "video/mp4"},
                       data=raw, timeout=300)
    if up.status_code != 200:
        raise RuntimeError(f"업로드 실패 {up.status_code}: {up.text[:120]}")
    f = up.json().get("file") or {}
    name, uri = f.get("name"), f.get("uri")
    t0 = time.time()
    while f.get("state") == "PROCESSING" and time.time() - t0 < 180:
        time.sleep(4)
        f = requests.get(FILES_GET.format(name=name), params={"key": key},
                         timeout=30).json()
    if f.get("state") != "ACTIVE":
        raise RuntimeError(f"파일 처리 실패: {f.get('state')}")
    return uri


REEL_PROMPT = """이 인스타 릴스를 벤치마킹 관점에서 해부하라. 감이 아니라 화면에 실제로
보이는 것 기준으로. JSON만 출력:
{"production": "원본영상+자막|AI생성영상|사진슬라이드|화면녹화|혼합|기타",
 "production_detail": "제작 방식 구체 설명 1~2문장 (원본 영상이면 어떤 종류의 원본인지, AI면 어떤 스타일인지)",
 "hook3s": {"visual": "첫 3초 화면에 보이는 것", "text": "첫 3초 자막/문구 (없으면 빈 문자열)"},
 "subtitles": {"style": "자막 스타일 — 색·크기·서체 느낌·강조 방식", "position": "위치", "density": "밀도 (거의 매초/문장 단위/드묾/없음)"},
 "cuts_per_10s": 0, "length_sec": 0,
 "sound": "원본 사운드|BGM|나레이션|무음",
 "structure": "구성 흐름 요약 — 도입→전개→(반전)→마무리를 초 단위 감각으로 1~2문장",
 "topic": "소재 한 줄", "why": "이 릴스가 반응 얻는 이유 1문장"}"""


def analyze_reels(cfg, base, handle, limit=10, log=print):
    """다운로드된 릴스를 Gemini 비디오로 해부 — reels.json에 analysis 병합 저장."""
    key = (cfg.get("gemini_api_key") or "").strip()
    if not key:
        raise RuntimeError("Gemini API 키가 없습니다")
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    reels = reels_load(base, handle)
    done = 0
    for r in reels:
        if done >= limit:
            break
        f = r.get("file")
        if not f or not Path(f).exists() or r.get("analysis"):
            continue
        try:
            uri = _upload_video(key, f, log=log)
            body = {"contents": [{"role": "user", "parts": [
                        {"file_data": {"mime_type": "video/mp4", "file_uri": uri}},
                        {"text": REEL_PROMPT}]}],
                    "generationConfig": {"response_mime_type": "application/json",
                                         "temperature": 0.2,
                                         "maxOutputTokens": 1024,
                                         "thinkingConfig": {"thinkingBudget": 0}}}
            resp = requests.post(GEMINI_URL.format(model=model),
                                 params={"key": key}, json=body, timeout=240)
            if resp.status_code != 200:
                raise RuntimeError(f"분석 호출 {resp.status_code}")
            r["analysis"] = _parse_json(
                resp.json()["candidates"][0]["content"]["parts"][0]["text"]) or {}
            done += 1
            log(f"      🔬 @{handle} 릴스 분석 {done}/{limit} — "
                f"{str((r['analysis'] or {}).get('production'))[:20]}")
            _reels_save(base, handle, reels)
        except Exception as e:
            log(f"      (분석 실패 {r['id']}: {str(e)[:80]})")
    return done


SYNTH_PROMPT = """당신은 릴스 벤치마킹 전략가다. 아래는 한 채널 그룹의 릴스 해부 데이터다
(각 줄: 좋아요/댓글 + 분석 JSON). 이 그룹의 '릴스 공식'을 실측 기반으로 종합하라.
숫자·비중은 데이터에서 세어라. JSON만 출력:
{"mix": "제작 방식 분포 (예: 원본영상+자막 80%, AI 10%...)",
 "sourcing": "소재를 어디서 가져오는가 — 어떤 종류의 원본/소재를 고르는 기준",
 "hook_formula": "첫 3초 후킹 공식 (비주얼+문구 패턴)",
 "subtitle_formula": "자막 공식 — 스타일·위치·밀도의 지배적 패턴",
 "edit_rhythm": "컷 리듬·길이 공식 (평균 길이, 컷 빈도)",
 "sound_formula": "사운드 사용 공식",
 "winners": "좋아요 상위작들의 공통점 2~3가지",
 "recipe": ["우리가 같은 형식의 릴스를 만들기 위한 단계별 레시피 — 소재 선정부터 업로드까지 5~8단계"],
 "notes": "이 그룹만의 특이점·주의점"}

데이터:
"""


def synth_group(cfg, base, group, log=print):
    """채널 그룹(jp1/jp2)의 분석 릴스를 종합 → 레퍼런스/_reels_<group>.json + .md."""
    key = (cfg.get("gemini_api_key") or "").strip()
    handles = GROUPS.get(group) or []
    rows = []
    for h in handles:
        for r in reels_load(base, h):
            a = r.get("analysis")
            if a:
                rows.append(f"- [@{h}] ♥{r.get('like', 0)} 💬{r.get('comments', 0)} "
                            + json.dumps(a, ensure_ascii=False))
    if not rows:
        raise RuntimeError(f"{group}: 분석된 릴스가 없습니다 — 다운로드·분석 먼저")
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    body = {"contents": [{"role": "user",
                          "parts": [{"text": SYNTH_PROMPT + "\n".join(rows[:60])}]}],
            "generationConfig": {"response_mime_type": "application/json",
                                 "temperature": 0.3, "maxOutputTokens": 2048,
                                 "thinkingConfig": {"thinkingBudget": 0}}}
    resp = requests.post(GEMINI_URL.format(model=model), params={"key": key},
                         json=body, timeout=180)
    if resp.status_code != 200:
        raise RuntimeError(f"종합 호출 실패 {resp.status_code}")
    formula = _parse_json(
        resp.json()["candidates"][0]["content"]["parts"][0]["text"]) or {}
    formula["group"] = group
    formula["handles"] = handles
    formula["analyzed"] = len(rows)
    out = Path(base) / REFS_DIRNAME / f"_reels_{group}.json"
    out.write_text(json.dumps(formula, ensure_ascii=False, indent=2), encoding="utf-8")
    md = [f"# 릴스 공식 — {group} ({', '.join('@' + h for h in handles)})",
          f"분석 릴스 {len(rows)}개 기반", ""]
    for k, label in [("mix", "제작 방식 분포"), ("sourcing", "소재 소싱"),
                     ("hook_formula", "후킹 공식"), ("subtitle_formula", "자막 공식"),
                     ("edit_rhythm", "컷 리듬·길이"), ("sound_formula", "사운드"),
                     ("winners", "히트작 공통점"), ("notes", "특이점")]:
        v = formula.get(k)
        if v:
            md.append(f"## {label}\n{v}\n")
    if formula.get("recipe"):
        md.append("## 재현 레시피\n" + "\n".join(
            f"{i}. {s}" for i, s in enumerate(formula["recipe"], 1)))
    (Path(base) / REFS_DIRNAME / f"_reels_{group}.md").write_text(
        "\n".join(md), encoding="utf-8")
    log(f"      📋 {group} 릴스 공식 저장 ({len(rows)}개 기반)")
    return formula
