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
    """좋아요순 상위 릴스 영상 다운로드 — yt-dlp(익명 웹 경로, 실측 검증) + 스로틀.
    instaloader Post API는 2026-08 기준 막혀 있음(BadResponse) — yt-dlp가 정답.
    이미 받은 파일은 건너뜀. 반환: 새로 받은 개수."""
    import subprocess
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
        perm = str(r.get("permalink") or "")
        if not perm.startswith("http"):
            continue
        try:
            p = subprocess.run(
                ["yt-dlp", "--no-playlist", "-f", "bv*+ba/b",
                 "--merge-output-format", "mp4", "-o", str(dest),
                 "--quiet", "--no-warnings", perm],
                capture_output=True, text=True, timeout=300)
            if dest.exists() and dest.stat().st_size > 50_000:
                r["file"] = str(dest)
                got += 1
                log(f"      ⬇ @{handle} 릴스 {got}/{limit} (♥{r.get('like', 0)}, "
                    f"{dest.stat().st_size // 1024}KB)")
            else:
                log(f"      (실패 {r['id']}: {(p.stderr or '')[-90:].strip()})")
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


def _gem_text(resp):
    """Gemini 200 응답에서 텍스트 추출 — 안전성 차단 등 candidates 부재 시 사유를 에러로."""
    j = resp.json()
    cands = j.get("candidates") or []
    if not cands:
        reason = (j.get("promptFeedback") or {}).get("blockReason") or "candidates 없음"
        raise RuntimeError(f"응답 차단({reason})")
    c = cands[0]
    parts = (c.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts)
    if not text:
        raise RuntimeError(f"본문 없음(finishReason={c.get('finishReason')})")
    return text


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
            r["analysis"] = _parse_json(_gem_text(resp)) or {}
            done += 1
            log(f"      🔬 @{handle} 릴스 분석 {done}/{limit} — "
                f"{str((r['analysis'] or {}).get('production'))[:20]}")
            _reels_save(base, handle, reels)
        except Exception as e:
            log(f"      (분석 실패 {r['id']}: {str(e)[:80]})")
    return done


def collect_thumbs(cfg, base, handle, max_media=250, log=print):
    """전체 릴스 썸네일 수집(공식 BD, 부계정 불필요) → 레퍼런스/<h>/reels_thumb/<id>.jpg.
    reels.json은 건드리지 않는다(다운로드 배치와 동시 실행 안전)."""
    tok = (cfg.get("fb_long_token") or "").strip()
    uid = str(cfg.get("fb_bd_ig_id") or "").strip()
    tdir = _dir(base, handle) / "reels_thumb"
    tdir.mkdir(parents=True, exist_ok=True)
    got, after, fetched = 0, "", 0
    while fetched < max_media:
        m = "media.limit(50)" + (f".after({after})" if after else "")
        fields = (f"business_discovery.username({handle})"
                  f"{{{m}{{id,media_type,thumbnail_url}}}}")
        r = requests.get(GRAPH.format(uid=uid),
                         params={"fields": fields, "access_token": tok}, timeout=60)
        if r.status_code != 200:
            break
        med = (((r.json().get("business_discovery") or {}).get("media")) or {})
        data = med.get("data") or []
        if not data:
            break
        fetched += len(data)
        for it in data:
            if it.get("media_type") != "VIDEO" or not it.get("thumbnail_url"):
                continue
            dest = tdir / f"{it['id']}.jpg"
            if dest.exists():
                continue
            try:
                resp = requests.get(it["thumbnail_url"], timeout=40)
                if resp.status_code == 200 and len(resp.content) > 3000:
                    dest.write_bytes(resp.content)
                    got += 1
            except Exception:
                continue
        after = ((med.get("paging") or {}).get("cursors") or {}).get("after") or ""
        if not after:
            break
        time.sleep(1.0)
    log(f"      @{handle}: 릴스 썸네일 {got}개 신규 (총 {len(list(tdir.glob('*.jpg')))}개)")
    return got


def thumb_sheet(base, group, out_dir, per_sheet=40, cols=5, log=print):
    """그룹의 릴스 썸네일을 좋아요순 그리드 시트로 — 한눈에 '어떤 영상인지' 파악용.
    각 칸에 ♥수·채널 표시. 반환: 생성된 시트 파일 경로 목록."""
    from PIL import Image, ImageDraw, ImageFont
    rows = []
    for h in GROUPS.get(group, []):
        tdir = _dir(base, h) / "reels_thumb"
        for r in reels_load(base, h):
            f = tdir / f"{r['id']}.jpg"
            if f.exists():
                rows.append((r.get("like") or 0, r.get("comments") or 0, h, f))
    rows.sort(key=lambda x: -x[0])
    if not rows:
        return []
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    CW, CH, BAR = 270, 420, 44          # 셀 크기 + 하단 정보 바
    try:
        font = ImageFont.truetype(
            str(Path(base) / "assets" / "fonts" / "Pretendard-SemiBold.otf"), 26)
    except Exception:
        font = ImageFont.load_default()
    sheets = []
    for si in range(0, len(rows), per_sheet):
        chunk = rows[si:si + per_sheet]
        nrows = -(-len(chunk) // cols)
        canvas = Image.new("RGB", (cols * CW, nrows * (CH + BAR)), (18, 18, 22))
        d = ImageDraw.Draw(canvas)
        for i, (like, cm, h, f) in enumerate(chunk):
            x, y = (i % cols) * CW, (i // cols) * (CH + BAR)
            try:
                im = Image.open(f).convert("RGB")
                sc = max(CW / im.width, CH / im.height)
                im = im.resize((int(im.width * sc) + 1, int(im.height * sc) + 1))
                im = im.crop(((im.width - CW) // 2, (im.height - CH) // 3,
                              (im.width - CW) // 2 + CW, (im.height - CH) // 3 + CH))
                canvas.paste(im, (x, y))
            except Exception:
                continue
            d.rectangle([x, y + CH, x + CW, y + CH + BAR], fill=(18, 18, 22))
            d.text((x + 8, y + CH + 8), f"♥{like:,} 💬{cm}", font=font,
                   fill=(255, 205, 90))
            tag = {"justdoeatjapan": "저스트", "selectionmgz": "셀렉",
                   "1mintrend": "1분트", "1mknow": "1분지"}.get(h, h[:4])
            w = d.textlength(tag, font=font)
            d.text((x + CW - w - 8, y + CH + 8), tag, font=font, fill=(150, 150, 160))
        p = out_dir / f"{group}_{si // per_sheet + 1}.jpg"
        canvas.save(p, "JPEG", quality=88)
        sheets.append(str(p))
    log(f"      🗺 {group} 썸네일 지도 {len(sheets)}장 ({len(rows)}개 릴스)")
    return sheets


# ── jp1 소재 영상 소싱: 커뮤 인기글 임베드 영상 → 다운로드 → 감성 적합도 채점 ──
# 원리: 우리가 틱톡을 뒤지는 게 아니라, 한국 커뮤 인기글이 이미 '터진 영상'을
# 큐레이션한다 — 인기글에 박힌 영상 링크를 뽑아 yt-dlp로 받고 Gemini가 jp1 감성 판정.
_VIDEO_PAT = re.compile(
    r"https?://(?:www\.)?(?:"
    r"youtube\.com/(?:watch\?v=|shorts/|embed/)[\w-]+"
    r"|youtu\.be/[\w-]+"
    r"|(?:vm|vt)\.tiktok\.com/[\w]+"
    r"|tiktok\.com/@[^\s\"'<>]+/video/\d+"
    r"|instagram\.com/(?:reel|reels|p|tv)/[A-Za-z0-9_-]+"
    r"|(?:twitter|x)\.com/[^\s\"'<>]+/status/\d+"
    r")")

# 커뮤별 Referer (extractors의 사이트별 관행과 동일 — 한글 사이트명을 넘기면 헤더 인코딩 깨짐)
_REFERERS = {"dcinside": "https://gall.dcinside.com/",
             "ruliweb": "https://bbs.ruliweb.com/",
             "fmkorea": "https://www.fmkorea.com/"}


def _referer_for(url):
    for k, v in _REFERERS.items():
        if k in url:
            return v
    return None

FIELD_FILE = "_field_videos.json"


def _field_load(base):
    try:
        return json.loads((Path(base) / REFS_DIRNAME / FIELD_FILE)
                          .read_text(encoding="utf-8"))
    except Exception:
        return []


def _field_save(base, rows):
    (Path(base) / REFS_DIRNAME / FIELD_FILE).write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


def find_field_videos(cfg, base, per_source=8, log=print):
    """커뮤 인기글에서 임베드 영상 링크 발굴. 반환: 새로 발견한 후보 수."""
    from . import hunter, extractors
    posts = []
    for fn in hunter.SOURCES:
        try:
            posts += fn(per_source) or []
        except Exception:
            continue
    rows = _field_load(base)
    seen = {r.get("video_url") for r in rows} | {r.get("post_url") for r in rows}
    new = 0
    for p in posts:
        url = p.get("url") or ""
        if not url or url in seen:
            continue
        try:
            from urllib.parse import urljoin
            soup = extractors.fetch_html(url, _referer_for(url))
            html = str(soup)
            links = list(dict.fromkeys(_VIDEO_PAT.findall(html)))
            og = soup.find("meta", property="og:video")
            if og and og.get("content", "").startswith("http"):
                links.append(og["content"])
            # 커뮤 자체 호스팅 영상 (<video>/<source> 직접 mp4 — 루리웹 등.
            # 디시는 동적 로드라 정적 HTML에서 안 잡힘 = 커버리지 한계)
            for v in soup.find_all("video"):
                for s in [v.get("src") or ""] + [x.get("src") or ""
                                                 for x in v.find_all("source")]:
                    if ".mp4" in s:
                        links.append(urljoin(url, s))
            links = list(dict.fromkeys(links))
            for v in links[:2]:
                if v in seen:
                    continue
                rows.append({"title": p.get("title", ""), "post_url": url,
                             "video_url": v, "site": p.get("site", ""),
                             "direct": ".mp4" in v,
                             "referer": _referer_for(url) or url,
                             "recs": p.get("recs", 0), "replies": p.get("replies", 0)})
                seen.add(v)
                new += 1
            seen.add(url)
            time.sleep(0.8)
        except Exception:
            continue
    _field_save(base, rows)
    log(f"      🔎 커뮤 임베드 영상 후보 신규 {new}개 (누적 {len(rows)}개)")
    return new


def find_sns_field(cfg, base, top_n=5, log=print):
    """SNS 이슈 계정에서 터진 영상 발굴 — 터진 영상의 본진은 틱톡·인스타(사용자 확정).
    config field_sources={"instagram": [핸들], "tiktok": [핸들]} 를 원천으로:
    인스타=공식 business_discovery(계정 리스크 0, ♥ 중앙값 대비 배수로 '터짐' 판정),
    틱톡=yt-dlp 공개 페이지 목록(익명, 조회수 상위). 반환: 신규 후보 수."""
    import statistics
    import subprocess
    src = cfg.get("field_sources") or {}
    rows = _field_load(base)
    seen = {r.get("video_url") for r in rows} | {r.get("post_url") for r in rows}
    new = 0
    for handle in src.get("instagram") or []:
        try:
            reels = collect_reels(cfg, base, handle, max_media=100, log=log)
            likes = [r["like"] for r in reels if r.get("like")]
            med = statistics.median(likes) if likes else 0
            for r in reels[:top_n]:
                url = r.get("permalink") or ""
                if not url or url in seen:
                    continue
                rows.append({"title": re.sub(r"\s+", " ", r.get("caption") or "")[:80],
                             "post_url": url, "video_url": url,
                             "site": f"IG@{handle}", "direct": False,
                             "referer": "",
                             "recs": r.get("like", 0), "replies": r.get("comments", 0),
                             "mult": round(r["like"] / med, 1) if med else 0})
                seen.add(url)
                new += 1
        except Exception as e:
            log(f"      (IG @{handle} 소싱 실패: {str(e)[:80]})")
    for handle in src.get("tiktok") or []:
        try:
            p = subprocess.run(
                ["yt-dlp", "--flat-playlist", "-J", "--playlist-end", "30",
                 f"https://www.tiktok.com/@{handle}"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=180)
            info = json.loads(p.stdout or "{}")
            ents = [e for e in (info.get("entries") or []) if e]
            ents.sort(key=lambda e: -(e.get("view_count") or 0))
            for e in ents[:top_n]:
                url = e.get("url") or e.get("webpage_url") or ""
                if not url or url in seen:
                    continue
                rows.append({"title": re.sub(r"\s+", " ", e.get("title") or "")[:80],
                             "post_url": url, "video_url": url,
                             "site": f"TT@{handle}", "direct": False,
                             "referer": "",
                             "recs": int(e.get("view_count") or 0),
                             "replies": int(e.get("comment_count") or 0)})
                seen.add(url)
                new += 1
        except Exception as e:
            log(f"      (틱톡 @{handle} 소싱 실패: {str(e)[:80]})")
    _field_save(base, rows)
    log(f"      📱 SNS 이슈 계정 후보 신규 {new}개 (누적 {len(rows)}개)")
    return new


FIELD_PROMPT = """이 영상이 아래 채널의 '릴스 소재'로 적합한지 냉정하게 채점하라.

채널: 일본 시청자에게 '진짜 한국'을 보여주는 릴스 채널.
형식 = 터진 현장 원본 영상 + 간단한 일본어 자막 (벤치마크 실측 93%).
잘 먹히는 소재 = 현장 논란·갑질·반전·유머 해프닝·기묘한 일상·사회면 —
'와 이게 한국이야?' 싶은 현장감. 뉴스 앵커 화면·긴 설명형·광고·연출 티 나는 건 부적합.

JSON만 출력:
{"fit": 0.0, "topic": "영상 내용 한 줄", "why": "적합/부적합 이유 1문장",
 "hook_ja": "일본어 후킹 자막 시안 1줄 (fit 7+ 일 때만, 아니면 빈 문자열)",
 "risk": "초상권·폭력·선정성·미성년 등 주의점 (없으면 빈 문자열)"}"""


def scout_field_videos(cfg, base, max_judge=6, log=print):
    """후보 영상 다운로드(yt-dlp) + Gemini 감성 적합도 채점 → fit순 정렬 저장."""
    import subprocess
    key = (cfg.get("gemini_api_key") or "").strip()
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    rows = _field_load(base)
    vdir = Path(base) / REFS_DIRNAME / "_field_videos"
    vdir.mkdir(parents=True, exist_ok=True)
    done = 0
    for r in rows:
        if done >= max_judge:
            break
        if r.get("judge") or r.get("dead"):
            continue
        vid = re.sub(r"[^\w]", "", r["video_url"])[-24:]
        dest = vdir / f"{vid}.mp4"
        try:
            if not (dest.exists() and dest.stat().st_size > 50_000):
                if r.get("direct"):          # 커뮤 자체 호스팅 mp4 — 직접 다운로드
                    resp = requests.get(
                        r["video_url"], timeout=180,
                        headers={"User-Agent": "Mozilla/5.0",
                                 "Referer": r.get("referer") or r.get("post_url", "")})
                    if resp.status_code == 200 and len(resp.content) > 50_000:
                        dest.write_bytes(resp.content)
                else:
                    p = subprocess.run(
                        ["yt-dlp", "--no-playlist", "-f", "bv*+ba/b",
                         "--merge-output-format", "mp4", "--max-filesize", "80M",
                         "-o", str(dest), "--quiet", "--no-warnings",
                         r["video_url"]],
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=300)
                if not (dest.exists() and dest.stat().st_size > 50_000):
                    r["dead"] = "다운로드 실패"
                    continue
            r["file"] = str(dest)
            uri = _upload_video(key, dest, log=log)
            body = {"contents": [{"role": "user", "parts": [
                        {"file_data": {"mime_type": "video/mp4", "file_uri": uri}},
                        {"text": FIELD_PROMPT}]}],
                    "generationConfig": {"response_mime_type": "application/json",
                                         "temperature": 0.2,
                                         "maxOutputTokens": 512,
                                         "thinkingConfig": {"thinkingBudget": 0}}}
            resp = requests.post(GEMINI_URL.format(model=model),
                                 params={"key": key}, json=body, timeout=240)
            if resp.status_code != 200:
                raise RuntimeError(f"채점 {resp.status_code}")
            r["judge"] = _parse_json(_gem_text(resp)) or {}
            done += 1
            log(f"      🎯 적합도 {r['judge'].get('fit', '?')}/10 — "
                f"{str(r['judge'].get('topic'))[:30]}")
            _field_save(base, rows)
            time.sleep(random.uniform(5, 9))
        except Exception as e:
            log(f"      (소재 영상 실패: {str(e)[:70]})")
            r["dead"] = str(e)[:80]
    rows.sort(key=lambda x: -(float((x.get("judge") or {}).get("fit") or 0)))
    _field_save(base, rows)
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
    formula = _parse_json(_gem_text(resp)) or {}
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


# ── jp2 주제 엔진: 벤치마크 실측 주제(♥ 근거) → 이식(일본화) + 창조(같은 류 신규) ──
TOPIC_PROMPT = """너는 일본 타겟 인스타 채널의 릴스 기획자다.
채널 = 궁금한 이야기 큐레이션 매거진(気になるマガジン류) — 저장 필수 실용정보·순위·
공감·심리·기묘한 상식을 사진슬라이드 릴스로 낸다. 시청자는 일본인이고 한국을 모른다.

[벤치마크 한국 채널 릴스 실측 — 주제 (좋아요)]
{samples}

[이 그룹의 릴스 공식 요약]
{formula}

임무: 이런 류의 주제를 두 갈래로 각 {n}개.
A. import(이식) — 위 실측에서 실제 터진 주제를 골라 일본 시청자용으로 현지화.
   한국 내수 전제(한국 유명인·한국식 순위)는 보편/일본형으로 변환하되 터진 이유는 보존.
   evidence에 원본 주제와 좋아요 수를 남겨라. 실측에 없는 것을 이식이라 하지 마라.
B. create(창조) — 실측에 없는 새 각도. 같은 류(저장형 실용·순위·공감·심리·기묘)로
   일본 시청자가 자기 이야기로 느낄 주제. 일본 생활 디테일(콘비니·전철·직장·라인 등) 환영.

규칙: 순위·통계 주제는 실제 조사 가능한 것만(수치 날조 금지), 실존 인물 저격 금지,
정치·혐오·성인 노골 배제(가벼운 은유 수위는 허용), 제목은 일본어 네이티브(직역투 금지),
「保存必須」「9割が知らない」류 후킹 관용구 활용 가능.

JSON만 출력:
{{"import": [{{"title_ja": "", "title_ko": "한국어 해석", "angle": "왜 먹히나 1문장",
              "evidence": "원본 주제 (♥수, @채널)", "branch": "실용|순위|공감|심리|기묘"}}],
  "create": [{{"title_ja": "", "title_ko": "", "angle": "", "branch": ""}}]}}"""


def suggest_reel_topics(cfg, base, group="jp2", n=10, log=print):
    """벤치마크 릴스 실측(주제·캡션·♥)로 릴스 주제 두 갈래(이식/창조) 생성 →
    레퍼런스/_reel_topics_<group>.json/.md 저장."""
    key = (cfg.get("gemini_api_key") or "").strip()
    if not key:
        raise RuntimeError("Gemini API 키가 없습니다")
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    handles = GROUPS.get(group) or []
    samples = []
    for h in handles:
        for r in reels_load(base, h):
            t = ((r.get("analysis") or {}).get("topic")
                 or (r.get("caption") or "").split("\n")[0])
            t = re.sub(r"\s+", " ", str(t)).strip()
            if len(t) > 4:
                samples.append(f"- {t[:90]} (♥{r.get('like', 0)}, @{h})")
    if not samples:
        raise RuntimeError("실측 표본이 없습니다 — collect_reels 먼저")
    formula = {}
    try:
        formula = json.loads((Path(base) / REFS_DIRNAME / f"_reels_{group}.json")
                             .read_text(encoding="utf-8"))
    except Exception:
        pass
    fsum = " / ".join(str(formula.get(k) or "")[:150]
                      for k in ("mix", "hook_formula", "winners") if formula.get(k))
    prompt = TOPIC_PROMPT.format(samples="\n".join(samples[:130]),
                                 formula=fsum or "(공식 미산출)", n=n)
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json",
                                 "temperature": 0.8,
                                 "maxOutputTokens": 8192}}
    resp = requests.post(GEMINI_URL.format(model=model), params={"key": key},
                         json=body, timeout=180)
    if resp.status_code != 200:
        raise RuntimeError(f"주제 생성 호출 실패 {resp.status_code}")
    data = _parse_json(_gem_text(resp)) or {}
    data["group"] = group
    data["sample_count"] = len(samples)
    (Path(base) / REFS_DIRNAME / f"_reel_topics_{group}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    md = [f"# 릴스 주제 후보 — {group} (실측 {len(samples)}개 기반)", ""]
    for sec, label in [("import", "A. 이식 — 벤치마크 실측에서 일본화"),
                       ("create", "B. 창조 — 같은 류의 새 주제")]:
        md.append(f"## {label}")
        for i, t in enumerate(data.get(sec) or [], 1):
            md.append(f"{i}. **{t.get('title_ja', '')}**  \n"
                      f"   🇰🇷 {t.get('title_ko', '')} · [{t.get('branch', '')}] "
                      f"{t.get('angle', '')}"
                      + (f"  \n   근거: {t.get('evidence')}"
                         if t.get("evidence") else ""))
        md.append("")
    (Path(base) / REFS_DIRNAME / f"_reel_topics_{group}.md").write_text(
        "\n".join(md), encoding="utf-8")
    log(f"      💡 {group} 주제 후보 이식 {len(data.get('import') or [])}"
        f"+창조 {len(data.get('create') or [])}개 저장")
    return data
