# -*- coding: utf-8 -*-
"""자동쇼츠 v2 프로젝트 파이프라인 (③영상수집·러프컷 ~ 최종조립).
계정별 프로젝트를 BASE/reelproj/<pid>/ 에 저장(이어하기). 자동저장.

state.json: {pid, code, script, topic, category, clips:[{id,file,thumb,tag,dur,src_url}], ...}
clips/ : 러프컷 클립(9:16) + 썸네일. 원본은 컷 후 삭제(용량), src_url로 재수집 가능.
"""
import json
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
            "subs_style": st.get("subs_style", DEFAULT_STYLE), "final": final, "bgm": st.get("bgm")}


def build_tts(cfg, base, pid, speed=1.0, log=print):
    """확정 대본 → 문장별 ElevenLabs TTS(타임스탬프) → 앞뒤 무음 트림 → 이어붙여
    '몰아치는' 음성 + 짧은 구절 자막. speed로 말속도 조절(atempo, 톤 유지). state['tts'] 저장."""
    speed = max(0.6, min(1.8, float(speed or 1.0)))
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
        audio, words = autoshorts.tts(cfg, line)
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
    st["tts"] = {"audio": "tts/tts.mp3", "dur": round(cum, 2), "subs": subs, "n_sub": len(subs), "speed": speed}
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


def _subs_ass(path, subs, style=None):
    s = {**DEFAULT_STYLE, **(style or {})}
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
            "Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
            f"Style: Default,{s['family']},{int(s['size'])},{_ass_color(s['primary'])},"
            f"{_ass_color(s['outline'],'&H00000000')},&H90000000,{-1 if s.get('bold') else 0},1,"
            f"{s['outline_w']},2,{int(s['align'])},80,80,{int(s['margin_v'])}\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    pop = _POP if s.get("pop") else ""
    ev = [f"Dialogue: 0,{autoshorts._ass_ts(x['s'])},{autoshorts._ass_ts(x['e'])},Default,,0,0,0,,{pop}{x['t']}"
          for x in subs]
    Path(path).write_text(head + "\n".join(ev) + "\n", encoding="utf-8")


def set_subs_style(base, pid, style):
    st = load(base, pid)
    st["subs_style"] = {**st.get("subs_style", DEFAULT_STYLE), **(style or {})}
    save(base, pid, st)
    return st["subs_style"]


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
    (edir / "_list.txt").write_text("".join(f"file 's{i}.mp4'\n" for i in range(len(edl))), encoding="utf-8")
    _run = autoshorts._run
    _run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
          "-i", "_list.txt", "-c", "copy", "video.mp4"], cwd=str(edir))
    style = st.get("subs_style") or DEFAULT_STYLE
    _subs_ass(edir / "sub.ass", st["tts"]["subs"], style)
    # 커스텀 폰트면 edit 폴더로 복사 → fontsdir=. 로 libass가 찾게(경로 콜론 회피)
    ff_arg = "ass=sub.ass"
    ffile = style.get("font_file")
    if ffile:
        try:
            fsrc = Path(base) / "fonts" / Path(ffile).name
            if fsrc.exists():
                shutil.copy(str(fsrc), str(edir / fsrc.name))
                ff_arg = "ass=sub.ass:fontsdir=."
        except Exception:
            pass
    # cwd=edir 이므로 상위 tts 폴더는 ../tts 로 참조(절대/상대 base 모두 안전)
    _run([FF, "-hide_banner", "-loglevel", "error", "-y", "-i", "video.mp4", "-i", "../tts/tts.mp3",
          "-vf", ff_arg, "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast",
          "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-shortest", "preview.mp4"], cwd=str(edir))


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
    _assemble_edit(base, pid, st, only_idx=idx)
    save(base, pid, st)
    return edit_public(base, pid, st)


def edit_public(base, pid, st):
    byid = {c["id"]: c for c in st["clips"]}
    blocks = []
    for i, e in enumerate(st.get("edl", [])):
        c = byid.get(e["cands"][e["used"]]) or byid.get(e["clip_id"]) or {}
        blocks.append({"i": i, "start": round(e["start"], 1), "end": round(e["end"], 1),
                       "dur": round(e["end"] - e["start"], 1), "tag": c.get("tag", ""),
                       "thumb": f"/reelproj/{pid}/clips/{c.get('thumb','')}",
                       "cand": e["used"] + 1, "cand_total": len(e.get("cands", []))})
    return {"preview": f"/reelproj/{pid}/edit/preview.mp4", "blocks": blocks}


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
