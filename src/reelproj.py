# -*- coding: utf-8 -*-
"""자동쇼츠 v2 프로젝트 파이프라인 (③영상수집·러프컷 ~ 최종조립).
계정별 프로젝트를 BASE/reelproj/<pid>/ 에 저장(이어하기). 자동저장.

state.json: {pid, code, script, topic, category, clips:[{id,file,thumb,tag,dur,src_url}], ...}
clips/ : 러프컷 클립(9:16) + 썸네일. 원본은 컷 후 삭제(용량), src_url로 재수집 가능.
"""
import json
import uuid
from datetime import datetime
from pathlib import Path

import requests

from . import autoshorts, brain, youtube


def root(base):
    d = Path(base) / "reelproj"
    d.mkdir(parents=True, exist_ok=True)
    return d


def pdir(base, pid):
    d = root(base) / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    return d


def load(base, pid):
    return json.loads((pdir(base, pid) / "state.json").read_text(encoding="utf-8"))


def exists(base, pid):
    return (pdir(base, pid) / "state.json").exists()


def save(base, pid, st):
    st["updated"] = datetime.now().isoformat(timespec="seconds")
    (pdir(base, pid) / "state.json").write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def new_project(base, code, script, topic="", category=""):
    pid = uuid.uuid4().hex[:10]
    st = {"pid": pid, "code": str(code), "script": script, "topic": topic, "category": category,
          "clips": [], "created": datetime.now().isoformat(timespec="seconds"), "updated": ""}
    (pdir(base, pid) / "clips").mkdir(parents=True, exist_ok=True)
    save(base, pid, st)
    return pid


# ─────────── AI 러프컷(하이라이트 여러 개 + 태그) ───────────
_HL_PROMPT = """이 영상에서 '{topic}'와(과) 관련되거나 쇼츠에 쓸 만한 하이라이트 장면 구간을 여러 개 찾아라.
- 느슨하게: 관련 있거나 인상적이면 다 포함해라. 한 영상에서 여러 개(보통 3~8개) 뽑아도 좋다.
- 각 구간은 보통 2~6초. 서로 다른 내용/장면이면 나눠라.
- 각 구간: start, end(초, 소수허용), tag(이 장면이 무슨 내용인지 한국어 한 줄).
반드시 JSON만: {{"clips":[{{"start":..,"end":..,"tag":".."}}, ...]}}"""


def highlight_segments(cfg, video, topic, log=print):
    key = (cfg.get("gemini_api_key") or "").strip()
    if not key:
        raise RuntimeError("Gemini API 키가 없습니다")
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    uri = youtube._gemini_upload_video(key, str(video), log=log)
    body = {"contents": [{"parts": [
        {"file_data": {"mime_type": "video/mp4", "file_uri": uri}},
        {"text": _HL_PROMPT.format(topic=topic or "이 채널 주제")}]}],
        "generationConfig": {"response_mime_type": "application/json", "temperature": 0.3,
                             "maxOutputTokens": 4096, "thinkingConfig": {"thinkingBudget": 0}}}
    r = requests.post(brain.GEMINI_URL.format(model=model), params={"key": key}, json=body, timeout=180)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini 오류 {r.status_code}: {r.text[:150]}")
    cand = r.json()["candidates"][0]
    raw = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
    out = []
    for c in (brain._parse_json(raw).get("clips") or []):
        try:
            s = float(c.get("start", 0))
            e = float(c.get("end", 0))
            if e > s:
                out.append({"start": s, "end": e, "tag": str(c.get("tag", "")).strip()})
        except Exception:
            pass
    return out


def collect_clips(cfg, base, pid, urls, log=print):
    """URL 목록 → 각 영상 다운로드 → AI 러프컷(하이라이트 다중) → 9:16 클립+태그 누적."""
    st = load(base, pid)
    topic = st.get("topic") or ""
    cdir = pdir(base, pid) / "clips"
    cdir.mkdir(exist_ok=True)
    total = len(urls)
    for ui, url in enumerate(urls):
        log(f"[{ui+1}/{total}] 영상 다운로드…")
        vid = pdir(base, pid) / "_src.mp4"
        try:
            youtube.download_video(url, str(vid), cookies=youtube._reel_cookies(cfg))
        except Exception as e:
            log(f"   다운로드 실패, 건너뜀: {str(e)[:60]}")
            continue
        log(f"[{ui+1}/{total}] 하이라이트 분석·컷…")
        try:
            segs = highlight_segments(cfg, vid, topic, log=log)
        except Exception as e:
            log(f"   분석 실패: {str(e)[:60]}")
            segs = []
        slen = autoshorts._dur(vid)
        for ci, seg in enumerate(segs):
            cid = f"{ui}_{ci}_{uuid.uuid4().hex[:4]}"
            out = cdir / f"c{cid}.mp4"
            th = cdir / f"c{cid}.jpg"
            dur = min(max(0.8, seg["end"] - seg["start"]), 10.0)   # 러프컷 클립 최대 10초로 캡
            try:
                autoshorts._cut(vid, seg["start"], dur, out, src_len=slen)
                autoshorts._thumb(out, th)
                st["clips"].append({"id": cid, "file": out.name, "thumb": th.name,
                                    "tag": seg["tag"], "dur": round(dur, 1),
                                    "src_url": url})
            except Exception as e:
                log(f"   컷 실패: {str(e)[:50]}")
        try:
            vid.unlink(missing_ok=True)
        except Exception:
            pass
        save(base, pid, st)   # 영상 하나 끝날 때마다 자동저장
    return clips_public(pid, st)


def clips_public(pid, st):
    return [{"id": c["id"], "tag": c.get("tag", ""), "dur": c.get("dur", 0),
             "thumb": f"/reelproj/{pid}/clips/{c['thumb']}",
             "video": f"/reelproj/{pid}/clips/{c['file']}"} for c in st.get("clips", [])]


def delete_clip(base, pid, cid):
    st = load(base, pid)
    keep = []
    for c in st["clips"]:
        if c["id"] == cid:
            for fn in (c.get("file"), c.get("thumb")):
                try:
                    (pdir(base, pid) / "clips" / fn).unlink(missing_ok=True)
                except Exception:
                    pass
        else:
            keep.append(c)
    st["clips"] = keep
    save(base, pid, st)
    return clips_public(pid, st)
