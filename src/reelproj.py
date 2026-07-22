# -*- coding: utf-8 -*-
"""자동쇼츠 v2 프로젝트 파이프라인 (③영상수집·러프컷 ~ 최종조립).
계정별 프로젝트를 BASE/reelproj/<pid>/ 에 저장(이어하기). 자동저장.

state.json: {pid, code, script, topic, category, clips:[{id,file,thumb,tag,dur,src_url}], ...}
clips/ : 러프컷 클립(9:16) + 썸네일. 원본은 컷 후 삭제(용량), src_url로 재수집 가능.
"""
import json
import re
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

import requests

from . import autoshorts, brain, youtube


def _has_video(path):
    """영상(비디오) 트랙이 있는지. TikTok 사진 게시물은 오디오만 받아져 여기서 걸러짐."""
    try:
        r = subprocess.run([autoshorts.FFPROBE, "-v", "error", "-select_streams", "v",
                            "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, creationflags=autoshorts._NO_WINDOW)
        return "video" in (r.stdout or "")
    except Exception:
        return True   # 확인 실패 시 일단 진행


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


def _phrase_chunks(words):
    """의미단위에 가깝게(짧게) 끊기: 누적 6자↑ & 2어절↑ 또는 3어절, 문장부호에서 끊음."""
    out, cur, ln = [], [], 0
    for w in words:
        t = str(w.get("t", ""))
        cur.append(w)
        ln += len(t.replace(" ", ""))
        endp = t.strip().endswith((".", ",", "!", "?", "…", "~"))
        if endp or (ln >= 6 and len(cur) >= 2) or len(cur) >= 3:
            out.append(cur)
            cur, ln = [], 0
    if cur:
        out.append(cur)
    ev = []
    for g in out:
        ev.append({"s": g[0]["s"], "e": g[-1]["s"] + g[-1]["d"],
                   "t": " ".join(str(x["t"]) for x in g).strip().rstrip(".")})
    return ev


def list_projects(base, code):
    """계정별 프로젝트 목록(최근순)."""
    out = []
    for sj in (root(base)).glob("*/state.json"):
        try:
            st = json.loads(sj.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(st.get("code", "")) != str(code):
            continue
        out.append({"pid": st.get("pid", sj.parent.name),
                    "topic": st.get("topic", "") or (st.get("script", "")[:20]),
                    "category": st.get("category", ""),
                    "updated": st.get("updated", ""),
                    "n_clips": len(st.get("clips", [])),
                    "has_tts": bool(st.get("tts")), "has_edit": bool(st.get("edl")),
                    "has_final": bool(st.get("final"))})
    out.sort(key=lambda x: x.get("updated", ""), reverse=True)
    return out


def delete_project(base, pid):
    d = pdir(base, pid)
    if d.exists():
        shutil.rmtree(str(d), ignore_errors=True)


def new_project(base, code, script, topic="", category=""):
    pid = uuid.uuid4().hex[:10]
    st = {"pid": pid, "code": str(code), "script": script, "topic": topic, "category": category,
          "clips": [], "created": datetime.now().isoformat(timespec="seconds"), "updated": ""}
    (pdir(base, pid) / "clips").mkdir(parents=True, exist_ok=True)
    save(base, pid, st)
    return pid


# ─────────── AI 러프컷(하이라이트 여러 개 + 태그) ───────────
_HL_PROMPT = """이 영상에서 '{topic}'와(과) 관련되거나 쇼츠에 쓸 만한 하이라이트 장면 구간을 골라라.
- 관련 있거나 인상적인 장면 위주로 한 영상에서 **최대 8개**만. 억지로 채우지 마라.
- 각 구간은 보통 2~6초. 서로 다른 내용/장면이면 나눠라.
- 각 구간: start, end(초, 소수허용), tag(무슨 장면인지 한국어로 **12자 이내** 짧게).
반드시 JSON만: {{"clips":[{{"start":0,"end":3,"tag":".."}}, ...]}}"""


def _parse_clips(raw):
    """정상 JSON 우선, 잘린 경우 개별 클립 객체를 정규식으로 건져냄."""
    try:
        cs = brain._parse_json(raw).get("clips") or []
        if cs:
            return cs
    except Exception:
        pass
    import re as _re
    out = []
    for m in _re.finditer(r'"start"\s*:\s*([\d.]+)\s*,\s*"end"\s*:\s*([\d.]+)\s*,\s*"tag"\s*:\s*"([^"]*)"', raw):
        out.append({"start": m.group(1), "end": m.group(2), "tag": m.group(3)})
    return out


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
                             "maxOutputTokens": 8192, "thinkingConfig": {"thinkingBudget": 0}}}
    r = requests.post(brain.GEMINI_URL.format(model=model), params={"key": key}, json=body, timeout=180)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini 오류 {r.status_code}: {r.text[:150]}")
    cand = r.json()["candidates"][0]
    raw = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
    out = []
    for c in _parse_clips(raw)[:8]:
        try:
            s = float(c.get("start", 0))
            e = float(c.get("end", 0))
            if e > s:
                out.append({"start": s, "end": e, "tag": str(c.get("tag", "")).strip()})
        except Exception:
            pass
    return out


def collect_clips(cfg, base, pid, urls, log=print):
    """URL 목록 → 각 영상 다운로드 → AI 러프컷(하이라이트 다중) → 9:16 클립+태그 누적.
    반환: {'clips':[...], 'failed':[url,...]} (실패 영상 표시용)."""
    st = load(base, pid)
    topic = st.get("topic") or ""
    cdir = pdir(base, pid) / "clips"
    cdir.mkdir(exist_ok=True)
    total = len(urls)
    failed = []
    skipped = 0
    done = {(c.get("src_url", "") or "").split("?")[0] for c in st["clips"]}   # 이미 수집한 영상(쿼리 무시)
    for ui, url in enumerate(urls):
        base_url = url.split("?")[0]
        if base_url in done:
            log(f"[{ui+1}/{total}] 이미 수집한 영상, 건너뜀(중복)")
            skipped += 1
            continue
        done.add(base_url)
        log(f"[{ui+1}/{total}] 영상 다운로드…")
        vid = pdir(base, pid) / "_src.mp4"
        try:
            youtube.download_video(url, str(vid), cookies=youtube._reel_cookies(cfg))
        except Exception as e:
            log(f"   다운로드 실패, 건너뜀: {str(e)[:60]}")
            failed.append(url)
            continue
        if not _has_video(vid):
            log("   영상 트랙 없음(사진 게시물?), 건너뜀")
            failed.append(url)
            try:
                vid.unlink(missing_ok=True)
            except Exception:
                pass
            continue
        log(f"[{ui+1}/{total}] 하이라이트 분석·컷…")
        try:
            segs = highlight_segments(cfg, vid, topic, log=log)
        except Exception as e:
            # Gemini 영상처리 실패(특이 포맷 등) → 표준 mp4로 재인코딩 후 1회 재시도
            log(f"   분석 실패, 재인코딩 후 재시도… ({str(e)[:40]})")
            segs = []
            try:
                norm = pdir(base, pid) / "_norm.mp4"
                autoshorts._run([autoshorts.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(vid),
                                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac",
                                 "-movflags", "+faststart", str(norm)])
                segs = highlight_segments(cfg, norm, topic, log=log)
                vid = norm
            except Exception as e2:
                log(f"   재시도도 실패, 건너뜀: {str(e2)[:50]}")
        if not segs:
            if url not in failed:
                failed.append(url)
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
        for tmp in ("_src.mp4", "_norm.mp4"):
            try:
                (pdir(base, pid) / tmp).unlink(missing_ok=True)
            except Exception:
                pass
        save(base, pid, st)   # 영상 하나 끝날 때마다 자동저장
    return {"clips": clips_public(pid, st), "failed": failed, "skipped": skipped}


def clips_public(pid, st):
    return [{"id": c["id"], "tag": c.get("tag", ""), "dur": c.get("dur", 0),
             "thumb": f"/reelproj/{pid}/clips/{c['thumb']}",
             "video": f"/reelproj/{pid}/clips/{c['file']}"} for c in st.get("clips", [])]


def state_public(base, pid):
    st = load(base, pid)
    t = st.get("tts")
    tts = None
    if t and (pdir(base, pid) / "tts" / "tts.mp3").exists():
        tts = {"dur": t.get("dur"), "n_sub": t.get("n_sub"), "subs": t.get("subs", []),
               "speed": t.get("speed", 1.0), "audio": f"/reelproj/{pid}/tts/tts.mp3"}
    edit = None
    if st.get("edl") and (pdir(base, pid) / "edit" / "preview.mp4").exists():
        try:
            edit = edit_public(base, pid, st)
        except Exception:
            edit = None
    final = None
    if st.get("final") and (pdir(base, pid) / "edit" / "final.mp4").exists():
        final = {"video": f"/reelproj/{pid}/edit/final.mp4"}
    return {"pid": pid, "script": st.get("script", ""), "topic": st.get("topic", ""),
            "category": st.get("category", ""), "clips": clips_public(pid, st), "tts": tts, "edit": edit,
            "subs_style": st.get("subs_style", DEFAULT_STYLE), "wm": st.get("wm") or {},
            "final": final, "bgm": st.get("bgm")}


def build_tts(cfg, base, pid, speed=1.0, voice="", log=print):
    """확정 대본 → 문장별 ElevenLabs TTS(타임스탬프) → 앞뒤 무음 트림 → 이어붙여
    '몰아치는' 음성 + 짧은 구절 자막. speed=말속도(atempo), voice=보이스ID(빈값=기본). state['tts'] 저장."""
    speed = max(0.6, min(1.8, float(speed or 1.0)))
    voice = (voice or "").strip()
    st = load(base, pid)
    lines = [ln.strip() for ln in str(st.get("script", "")).splitlines() if ln.strip()]
    if not lines:
        raise RuntimeError("확정된 대본이 없습니다 (② 대본)")
    tdir = pdir(base, pid) / "tts"
    tdir.mkdir(exist_ok=True)
    for f in tdir.glob("*"):
        try:
            f.unlink()
        except Exception:
            pass
    FF = autoshorts.FFMPEG
    line_files, subs, cum = [], [], 0.0
    for i, line in enumerate(lines):
        log(f"[{i+1}/{len(lines)}] 음성 생성·무음제거…")
        audio, words = autoshorts.tts(cfg, line, voice_id=voice)
        raw = tdir / f"raw{i}.mp3"
        raw.write_bytes(audio)
        if not words:
            words = [{"t": line, "s": 0.0, "d": autoshorts._dur(raw)}]
        s0 = max(0.0, float(words[0]["s"]) - 0.02)          # 앞 무음 컷
        e1 = float(words[-1]["s"]) + float(words[-1]["d"]) + 0.04   # 뒤 살짝 여유
        ln = tdir / f"line{i}.mp3"
        autoshorts._run([FF, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{s0:.3f}",
                         "-to", f"{e1:.3f}", "-i", str(raw), "-af", f"atempo={speed:.3f}",
                         "-c:a", "libmp3lame", "-q:a", "2", str(ln)])
        line_files.append(ln.name)
        for ch in _phrase_chunks(words):                   # 의미단위 짧은 구절 자막(속도만큼 타이밍 축소)
            subs.append({"s": round(cum + (ch["s"] - s0) / speed, 2),
                         "e": round(cum + (ch["e"] - s0) / speed, 2), "t": ch["t"]})
        cum += autoshorts._dur(ln)
        try:
            raw.unlink(missing_ok=True)
        except Exception:
            pass
    # 문장 오디오 이어붙이기(무음 없이) → tts.mp3
    (tdir / "_list.txt").write_text("".join(f"file '{n}'\n" for n in line_files), encoding="utf-8")
    autoshorts._run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                     "-i", "_list.txt", "-c:a", "libmp3lame", "-q:a", "2", "tts.mp3"], cwd=str(tdir))
    st["tts"] = {"audio": "tts/tts.mp3", "dur": round(cum, 2), "subs": subs, "n_sub": len(subs),
                 "speed": speed, "voice": voice}
    # 음성이 바뀌면 기존 정밀컷(edl/edit)은 무효 → 제거
    st.pop("edl", None)
    st.pop("edit", None)
    save(base, pid, st)
    return {"dur": st["tts"]["dur"], "n_sub": len(subs), "subs": subs, "speed": speed,
            "audio": f"/reelproj/{pid}/tts/tts.mp3"}


def _gjson(cfg, prompt, maxtok=4096):
    key = (cfg.get("gemini_api_key") or "").strip()
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json", "temperature": 0.3,
                                 "maxOutputTokens": maxtok, "thinkingConfig": {"thinkingBudget": 0}}}
    r = requests.post(brain.GEMINI_URL.format(model=model), params={"key": key}, json=body, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini 오류 {r.status_code}: {r.text[:150]}")
    cand = r.json()["candidates"][0]
    raw = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
    return brain._parse_json(raw)


def caption_cta(cfg, script, topic=""):
    """영상 대본 기반 인스타 릴스 캡션(훅 + 핵심 + CTA + 해시태그) 자동 생성."""
    script = (script or "").strip()
    if not script:
        return (topic or "").strip()
    prompt = f"""너는 인스타 릴스 카피라이터다. 아래 영상 대본을 바탕으로 릴스 캡션을 써라.
[소재] {topic}
[대본]
{script[:1500]}

규칙:
- 첫 줄: 스크롤을 멈추게 하는 강한 훅 한 줄(이모지 1개 이내).
- 본문 2~3줄: 대본 핵심을 짧고 쉽게. 과장 금지.
- 마지막 줄: 행동 유도(CTA) 한 줄 — 저장/팔로우/댓글/프로필 링크 중 소재에 맞는 것으로 자연스럽게.
- 그 아래 해시태그 5~8개(한국어 위주, 소재 관련).
- 한국어. 존댓말 살짝. 이모지 과하지 않게.
반드시 JSON만: {{"caption":"...(줄바꿈 \\n 포함 전체 캡션)..."}}"""
    try:
        cap = str(_gjson(cfg, prompt).get("caption", "")).strip()
        return cap or script
    except Exception:
        return script


def _match_edl(cfg, subs, total, clips):
    """자막 타이밍에 클립 배치(내용 전환점 블록화, 태그매칭, 재사용금지)."""
    sub_lines = "\n".join(f"[{round(s['s'],1)}~{round(s['e'],1)}s] {s['t']}" for s in subs)
    clip_lines = "\n".join(f"{c['id']}: {c.get('tag','')} ({c.get('dur',0)}초)" for c in clips)
    prompt = f"""아래 내레이션(자막 구간, 총 {round(total,1)}초)에 소스 클립을 배치해 컷편집 계획(EDL)을 만들어라.
- 내용이 바뀌는 지점에서 블록으로 나눠라(꼭 한 문장=한 컷일 필요 없음). 각 블록은 [start,end]초, 0부터 {round(total,1)}까지 빈틈없이 이어서 덮어라.
- 각 블록에 '태그가 가장 어울리는' 클립 하나를 배정. **한 클립은 한 블록에만(재사용 금지).**
- 각 블록에 대체 후보 alt_ids(1~3개, 태그 비슷한 순)도 제시.
[내레이션 자막]
{sub_lines}
[클립들 (id: 태그 (길이초))]
{clip_lines}
반드시 JSON만: {{"edl":[{{"start":0,"end":3.2,"clip_id":"..","alt_ids":["..",".."]}}, ...]}}"""
    edl = _gjson(cfg, prompt).get("edl", [])
    ids = [c["id"] for c in clips]
    idset = set(ids)
    out = []
    for e in edl:
        cid = str(e.get("clip_id", "")).strip()
        try:
            s = float(e.get("start", 0))
            en = float(e.get("end", 0))
        except Exception:
            continue
        if cid not in idset:
            continue
        alt = [str(a) for a in (e.get("alt_ids") or []) if str(a) in idset and str(a) != cid]
        out.append({"start": s, "end": en, "clip_id": cid, "alt_ids": alt})
    out.sort(key=lambda x: x["start"])
    if not out:   # 폴백: 클립을 순서대로 균등 배치
        n = len(clips)
        step = total / max(1, n)
        out = [{"start": round(i * step, 2), "end": round((i + 1) * step, 2),
                "clip_id": clips[i]["id"], "alt_ids": []} for i in range(n)]
    # 연속 커버리지 강제(0..total)
    for i, e in enumerate(out):
        e["start"] = 0.0 if i == 0 else out[i - 1]["end"]
    out[-1]["end"] = round(total, 2)
    out = [e for e in out if e["end"] - e["start"] > 0.05]
    # 재사용 금지(중복이면 대체/미사용 클립으로 교체) + 리롤 후보 구성
    used = set()
    for e in out:
        if e["clip_id"] in used:
            repl = next((a for a in e["alt_ids"] if a not in used), None) \
                or next((cid for cid in ids if cid not in used), None)
            if repl:
                e["clip_id"] = repl
        used.add(e["clip_id"])
        cands = [e["clip_id"]] + [a for a in e["alt_ids"] if a != e["clip_id"]]
        for cid in ids:   # 나머지 클립도 리롤 후보로(부족하지 않게)
            if cid not in cands:
                cands.append(cid)
        e["cands"] = cands[:6]
        e["used"] = 0
    return out


_VF916 = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"


def _fit_clip(src, cdur, need, out):
    """클립을 정확히 need초로: 길면 트림, 짧으면 최대 2배 슬로우, 그래도 짧으면 2배+반복."""
    FF = autoshorts.FFMPEG
    tail = ["-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-an", str(out)]
    if cdur >= need + 0.05:
        autoshorts._run([FF, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                         "-t", f"{need:.3f}", "-vf", _VF916] + tail)
    else:
        factor = max(1.0, need / max(0.2, cdur))
        if factor <= 2.0:
            autoshorts._run([FF, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                             "-vf", f"setpts={factor:.4f}*PTS,{_VF916}", "-t", f"{need:.3f}"] + tail)
        else:
            autoshorts._run([FF, "-hide_banner", "-loglevel", "error", "-y", "-stream_loop", "-1", "-i", str(src),
                             "-vf", f"setpts=2.0*PTS,{_VF916}", "-t", f"{need:.3f}"] + tail)


DEFAULT_STYLE = {"family": "Malgun Gothic", "font_file": "", "size": 72, "primary": "#FFFFFF",
                 "outline": "#000000", "outline_w": 5, "align": 2, "margin_v": 320, "bold": True, "pop": False}

# 자막 '뿅' 등장(스케일 바운스): 55% → 108% 오버슈트 → 100%
_POP = r"{\fscx55\fscy55\t(0,90,\fscx108\fscy108)\t(90,170,\fscx100\fscy100)}"


def _ass_color(hexstr, default="&H00FFFFFF"):
    h = str(hexstr or "").lstrip("#")
    if len(h) != 6:
        return default
    try:
        return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}".upper()   # #RRGGBB → &H00BBGGRR
    except Exception:
        return default


def _ass_text(t):
    """자막 텍스트 방어: ASS 오버라이드로 오인될 문자 제거 + 공백/줄바꿈 정리.
    (특정 자막에서 위치가 순간 깨지는 문제 방지 — 스타일 태그 오염·불규칙 줄바꿈 차단)"""
    t = str(t or "")
    t = t.replace("\\", "＼").replace("{", "(").replace("}", ")")   # 오버라이드/이스케이프 문자 무력화
    t = re.sub(r"\s+", " ", t).strip()                              # 줄바꿈·중복공백 → 단일 공백(한 줄 고정)
    return t


# 자막 스타일 지문(렌더된 스타일과 현재 스타일이 다른지 판별 — v2.html styleSig와 동일 순서)
_SIG_KEYS = ("family", "font_file", "size", "primary", "outline", "outline_w", "align", "bold", "pop", "margin_v")


def _subs_sig(s):
    s = s or {}
    return [s.get("family"), s.get("font_file"), s.get("size"), s.get("primary"), s.get("outline"),
            s.get("outline_w"), s.get("align"), s.get("bold"), bool(s.get("pop")), s.get("margin_v")]


def _subs_ass(path, subs, style=None, wm=None, dur=None):
    s = {**DEFAULT_STYLE, **(style or {})}
    wm = wm or {}
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
            "Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
            f"Style: Default,{s['family']},{int(s['size'])},{_ass_color(s['primary'])},"
            f"{_ass_color(s['outline'],'&H00000000')},&H90000000,{-1 if s.get('bold') else 0},1,"
            f"{s['outline_w']},2,{int(s['align'])},80,80,{int(s['margin_v'])}\n"
            # 워터마크: WM=계정명(하단 중앙, 반투명), AD=[광고](우상단)
            f"Style: WM,{s['family']},68,&H55FFFFFF,&H88000000,&H00000000,0,1,2,0,2,80,80,60\n"
            f"Style: AD,{s['family']},39,&H66FFFFFF,&H88000000,&H00000000,0,1,2,0,9,28,28,28\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    pop = _POP if s.get("pop") else ""
    # 시간 겹침 제거: 겹치면 두 자막이 동시에 떠 위치가 순간 어긋나 보임 → 앞 자막 끝을 다음 시작 직전으로
    items = sorted(({"s": float(x["s"]), "e": float(x["e"]), "t": x["t"]} for x in subs),
                   key=lambda x: x["s"])
    for i in range(len(items) - 1):
        nxt = items[i + 1]["s"]
        if items[i]["e"] > nxt - 0.03:
            items[i]["e"] = max(items[i]["s"] + 0.1, nxt - 0.03)
    ev = [f"Dialogue: 0,{autoshorts._ass_ts(x['s'])},{autoshorts._ass_ts(x['e'])},Default,,0,0,0,,{pop}{_ass_text(x['t'])}"
          for x in items if _ass_text(x['t'])]
    # 워터마크: 영상 전체 길이 동안 고정 표시
    end_ts = autoshorts._ass_ts(float(dur) + 5.0) if dur else "9:59:59.00"
    acc = str(wm.get("account") or "").strip().lstrip("@")
    if acc:
        ev.append(f"Dialogue: 1,0:00:00.00,{end_ts},WM,,0,0,0,,@{_ass_text(acc)}")
    if wm.get("ad"):
        ev.append(f"Dialogue: 1,0:00:00.00,{end_ts},AD,,0,0,0,,[광고]")
    Path(path).write_text(head + "\n".join(ev) + "\n", encoding="utf-8")


def set_subs_style(base, pid, style):
    st = load(base, pid)
    st["subs_style"] = {**st.get("subs_style", DEFAULT_STYLE), **(style or {})}
    save(base, pid, st)
    return st["subs_style"]


def _apply_blur(path, bl, feather=10):
    """클립(1080x1920)의 정규화 박스(x,y,w,h 0~1) 영역에 블러 적용(가장자리 페더). 제자리 덮어씀."""
    try:
        x, y, w, h = float(bl.get("x", 0)), float(bl.get("y", 0)), float(bl.get("w", 0)), float(bl.get("h", 0))
    except Exception:
        return
    if w <= 0.02 or h <= 0.02:
        return
    x = max(0.0, min(0.98, x)); y = max(0.0, min(0.98, y))
    w = min(w, 1 - x); h = min(h, 1 - y)
    W, H = 1080, 1920

    def _ev(n):
        n = int(round(n)); return n - (n % 2)
    cw, ch = max(4, _ev(w * W)), max(4, _ev(h * H))
    cx, cy = _ev(x * W), _ev(y * H)
    f = max(0, int(feather))
    tmp = Path(str(path) + ".b.mp4")
    if f >= 2 and cw > 2 * f + 4 and ch > 2 * f + 4:
        # 크롭 스트림을 복제해 하나는 블러, 하나는 흰박스 마스크(가장자리 gblur=페더)로 → 유한 합성
        fc = (f"[0:v]crop={cw}:{ch}:{cx}:{cy},split[c1][c2];"
              f"[c1]gblur=sigma=22[fg];"
              f"[c2]drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill,"
              f"drawbox=x={f}:y={f}:w=iw-{2 * f}:h=ih-{2 * f}:color=white:t=fill,"
              f"gblur=sigma={max(1.0, f * 0.6):.1f},format=gray[m];"
              f"[fg][m]alphamerge[fga];"
              f"[0:v][fga]overlay={cx}:{cy}[v]")
    else:
        fc = (f"[0:v]crop={cw}:{ch}:{cx}:{cy},gblur=sigma=22[fg];"
              f"[0:v][fg]overlay={cx}:{cy}[v]")
    autoshorts._run([autoshorts.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
                     "-filter_complex", fc, "-map", "[v]", "-r", "30", "-c:v", "libx264", "-preset",
                     "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-an", str(tmp)])
    tmp.replace(path)


def set_block_blur(base, pid, idx, blur):
    st = load(base, pid)
    edl = st.get("edl") or []
    if not (0 <= idx < len(edl)):
        raise RuntimeError("잘못된 블록")
    edl[idx]["blur"] = blur or None
    _assemble_edit(base, pid, st, only_idx=idx)
    save(base, pid, st)
    _sync_final(base, pid)          # BGM 완성본이 있으면 자동 재합치기(⑦ 다시 안 눌러도 반영)
    return edit_public(base, pid, st)


def _assemble_edit(base, pid, st, only_idx=None):
    """EDL대로 각 블록 클립을 need초로 맞춰 컷 → 이어붙이고 음성+자막 미리보기."""
    FF = autoshorts.FFMPEG
    edir = pdir(base, pid) / "edit"
    edir.mkdir(exist_ok=True)
    byid = {c["id"]: c for c in st["clips"]}
    edl = st["edl"]
    for i, e in enumerate(edl):
        if only_idx is not None and i != only_idx:
            continue
        c = byid.get(e["cands"][e["used"]]) or byid.get(e["clip_id"])
        need = e["end"] - e["start"]
        _fit_clip(edir.parent / "clips" / c["file"], c.get("dur", need), need, edir / f"s{i}.mp4")
        if e.get("blur"):
            _apply_blur(edir / f"s{i}.mp4", e["blur"])
    (edir / "_list.txt").write_text("".join(f"file 's{i}.mp4'\n" for i in range(len(edl))), encoding="utf-8")
    autoshorts._run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                     "-i", "_list.txt", "-c", "copy", "video.mp4"], cwd=str(edir))
    _mux_subs(base, pid, st)


def _mux_subs(base, pid, st):
    """edit/video.mp4(무자막) + tts + 현재 자막스타일 → preview.mp4 (재컷 없이 자막만 재합성)."""
    FF = autoshorts.FFMPEG
    edir = pdir(base, pid) / "edit"
    style = dict(st.get("subs_style") or DEFAULT_STYLE)
    # 커스텀 폰트: 폰트만 든 깨끗한 폴더로 격리(libass가 mp4까지 폰트로 스캔하지 않게) +
    # 실제 폰트 파일에서 family를 재추출해 ASS Fontname과 확실히 일치(글꼴 반영 실패 방지)
    ff_arg = "ass=sub.ass"
    ffile = style.get("font_file")
    if ffile:
        try:
            fsrc = Path(base) / "fonts" / Path(ffile).name
            if fsrc.exists():
                fdir = edir / "_fonts"
                shutil.rmtree(fdir, ignore_errors=True)
                fdir.mkdir(parents=True, exist_ok=True)
                shutil.copy(str(fsrc), str(fdir / fsrc.name))
                try:
                    from . import fonts as _fonts
                    fam = _fonts.family_name(fsrc)
                    if fam:
                        style["family"] = fam          # 파일이 진짜 갖고 있는 family로 강제
                except Exception:
                    pass
                ff_arg = "ass=sub.ass:fontsdir=_fonts"
                # 진단용: 어떤 family/파일로 구웠는지 기록
                try:
                    (edir / "_font_used.txt").write_text(
                        f"family={style.get('family')}\nfile={fsrc.name}\n", encoding="utf-8")
                except OSError:
                    pass
        except Exception:
            pass
    # 재추출한 family를 subs_style에도 반영(옛 프로젝트의 stale 무한루프 방지: rendered==style 유지)
    st["subs_style"] = style
    st["subs_rendered"] = dict(style)          # 이 스타일로 preview.mp4를 구움(⑥ 반영 판별용)
    wm = dict(st.get("wm") or {})
    st["wm_rendered"] = dict(wm)
    _subs_ass(edir / "sub.ass", st["tts"]["subs"], style,
              wm=wm, dur=(st.get("tts") or {}).get("dur"))
    # cwd=edir 이므로 상위 tts 폴더는 ../tts 로 참조(절대/상대 base 모두 안전)
    autoshorts._run([FF, "-hide_banner", "-loglevel", "error", "-y", "-i", "video.mp4", "-i", "../tts/tts.mp3",
                     "-vf", ff_arg, "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast",
                     "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-shortest", "preview.mp4"], cwd=str(edir))


def restyle_subs(base, pid):
    """자막 스타일만 바꿔 preview.mp4 재합성(정밀컷 결과 재사용, 빠름). BGM 완성본은 무효화."""
    st = load(base, pid)
    if not (pdir(base, pid) / "edit" / "video.mp4").exists():
        raise RuntimeError("먼저 ⑥ 정밀 컷을 생성하세요")
    _mux_subs(base, pid, st)
    save(base, pid, st)
    _sync_final(base, pid)          # 자막 바뀌면 BGM 완성본도 자동 재합치기(무효화 대신 갱신)
    return edit_public(base, pid, st)


def build_edit(cfg, base, pid, log=print):
    st = load(base, pid)
    tts = st.get("tts")
    if not tts or not (pdir(base, pid) / "tts" / "tts.mp3").exists():
        raise RuntimeError("먼저 ④ 음성을 생성하세요")
    if not st.get("clips"):
        raise RuntimeError("클립이 없습니다 (③ 영상수집)")
    log("[1/3] 대본↔클립 매칭…")
    st["edl"] = _match_edl(cfg, tts["subs"], tts["dur"], st["clips"])
    log("[2/3] 컷 맞춤(트림·슬로우)…")
    log("[3/3] 미리보기 합성(음성+자막)…")
    _assemble_edit(base, pid, st)
    st["edit"] = {"preview": "edit/preview.mp4"}
    save(base, pid, st)
    return edit_public(base, pid, st)


def reroll_block(base, pid, idx):
    st = load(base, pid)
    edl = st.get("edl") or []
    if not (0 <= idx < len(edl)):
        raise RuntimeError("잘못된 블록")
    e = edl[idx]
    if len(e.get("cands", [])) <= 1:
        raise RuntimeError("대체 클립이 없어요")
    e["used"] = (e["used"] + 1) % len(e["cands"])
    e["clip_id"] = e["cands"][e["used"]]
    e.pop("blur", None)             # 새 클립엔 이전 블러 영역이 안 맞음 → 초기화
    _assemble_edit(base, pid, st, only_idx=idx)
    save(base, pid, st)
    _sync_final(base, pid)          # 완성본 있으면 자동 재합치기
    return edit_public(base, pid, st)


def _sync_final(base, pid):
    """⑥ 편집(블러·리롤·자막)이 바뀐 뒤, 이미 만든 ⑦ 완성본(final.mp4)이 있으면
    저장된 BGM 설정으로 자동 재합치기 → '최종 합치기' 다시 안 눌러도 결과물에 반영."""
    st = load(base, pid)
    if not st.get("final"):
        return
    if not (pdir(base, pid) / "edit" / "preview.mp4").exists():
        return
    b = st.get("bgm") or {}
    try:
        build_final(None, base, pid, b.get("code", ""), b.get("file", ""), b.get("db", -20.0))
    except Exception:
        pass


def edit_public(base, pid, st):
    byid = {c["id"]: c for c in st["clips"]}
    blocks = []
    for i, e in enumerate(st.get("edl", [])):
        c = byid.get(e["cands"][e["used"]]) or byid.get(e["clip_id"]) or {}
        blocks.append({"i": i, "start": round(e["start"], 1), "end": round(e["end"], 1),
                       "dur": round(e["end"] - e["start"], 1), "tag": c.get("tag", ""),
                       "thumb": f"/reelproj/{pid}/clips/{c.get('thumb','')}",
                       "cand": e["used"] + 1, "cand_total": len(e.get("cands", [])),
                       "blur": bool(e.get("blur"))})
    # 렌더된 자막 스타일과 현재 스타일이 다르면 stale=True (⑥ 진입 시 자동 재입힘 트리거)
    rendered = st.get("subs_rendered")
    stale = bool(rendered) and _subs_sig(st.get("subs_style") or {}) != _subs_sig(rendered)
    if not stale and rendered is not None:      # 워터마크(계정명·[광고]) 변경도 재입힘 대상
        stale = dict(st.get("wm") or {}) != dict(st.get("wm_rendered") or {})
    return {"preview": f"/reelproj/{pid}/edit/preview.mp4", "blocks": blocks, "subs_stale": stale}


def build_final(cfg, base, pid, bgm_code="", bgm_file="", bgm_db=-20.0, log=print):
    """⑦ 최종: ⑥ 컷 미리보기(영상+음성+자막)에 BGM 믹스 → final.mp4. BGM 없으면 그대로 복사."""
    st = load(base, pid)
    edir = pdir(base, pid) / "edit"
    prev = edir / "preview.mp4"
    if not prev.exists():
        raise RuntimeError("먼저 ⑥ 정밀 컷을 생성하세요")
    FF = autoshorts.FFMPEG
    final = edir / "final.mp4"
    bgm_path = None
    if bgm_file and bgm_code:
        p = Path(base) / "bgm" / str(bgm_code).strip() / Path(bgm_file).name
        if p.exists():
            bgm_path = p
    if bgm_path:
        log("[1/1] BGM 믹스…")
        db = max(-40.0, min(0.0, float(bgm_db)))
        fc = (f"[1:a]volume={db}dB,aformat=sample_rates=44100:channel_layouts=stereo[b];"
              "[0:a]aformat=sample_rates=44100:channel_layouts=stereo[v];"
              "[v][b]amix=inputs=2:duration=first:normalize=0[a]")
        autoshorts._run([FF, "-hide_banner", "-loglevel", "error", "-y", "-i", str(prev.resolve()),
                         "-stream_loop", "-1", "-i", str(bgm_path.resolve()), "-filter_complex", fc,
                         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                         "-shortest", str(final)])
    else:
        shutil.copy(str(prev), str(final))
    st["bgm"] = {"code": bgm_code, "file": bgm_file, "db": bgm_db}
    st["final"] = {"video": "edit/final.mp4"}
    save(base, pid, st)
    return {"video": f"/reelproj/{pid}/edit/final.mp4", "bgm": st["bgm"]}


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
