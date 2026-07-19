# -*- coding: utf-8 -*-
"""자동 쇼츠 제작 파이프라인.
소스영상 + 대본 → Gemini 시각태깅 → 대본↔화면 매칭 → scene-snap 정밀컷
→ ElevenLabs 음성 → 짧은 구절 자막 → BGM(-20dB) → 완성 mp4. 구간 리롤 지원.

프로젝트는 BASE/autoshorts/<pid>/ 에 저장:
  source.mp4, state.json, a{i}.mp3, v{i}.mp4, thumb{i}.jpg, sub.ass, final.mp4
"""
import base64
import json
import os
import subprocess
import uuid
from pathlib import Path

import requests

from . import brain, youtube

_BIN = Path(__file__).resolve().parent.parent / "bin"
FFMPEG = str(_BIN / "ffmpeg.exe") if (_BIN / "ffmpeg.exe").exists() else "ffmpeg"
FFPROBE = str(_BIN / "ffprobe.exe") if (_BIN / "ffprobe.exe").exists() else "ffprobe"
# Windows에서 subprocess가 검은 콘솔창을 띄우지 않도록(서버는 창 없이 도는데 ffmpeg마다 창 깜빡임 방지)
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _key(cfg):
    return (cfg.get("gemini_api_key") or "").strip()


def _run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 실패: {(r.stderr or '')[-500:]}")
    return r


def _dur(path):
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True,
                       creationflags=_NO_WINDOW)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def proj_root(base):
    r = Path(base) / "autoshorts"
    r.mkdir(parents=True, exist_ok=True)
    return r


# ─────────────── 1) 소스 확보 (링크=yt-dlp, 파일=이미 저장됨) ───────────────
def ingest_url(url, dest, cfg, log=print):
    youtube.download_video(url, str(dest), log=log, cookies=youtube._reel_cookies(cfg))
    return dest


# ─────────────── 2) Gemini 시각 구간 태깅 ───────────────
_SEG_PROMPT = """이 영상을 처음부터 끝까지 '화면에 무엇이 보이는가' 기준으로 **잘게** 구간을 나눠라.
- 오디오/말이 아니라 화면 장면 기준. 제품·동작·샷이 바뀔 때마다 **반드시 새 구간**.
- 한 구간은 보통 2~5초로 짧게. 영상 전체를 빈틈없이 촘촘히 덮어라(보통 15~40개 구간).
- 한 구간에 서로 다른 제품/장면을 절대 섞지 마라.
- 각 구간: start, end(초, 소수허용), scene(화면 한 줄 묘사), tags(핵심 사물·행동 키워드 배열).
반드시 JSON만: {"duration": 초, "segments": [{"start":..,"end":..,"scene":"..","tags":[..]}, ...]}"""


def visual_segments(cfg, video, log=print):
    key = _key(cfg)
    if not key:
        raise RuntimeError("Gemini API 키가 없습니다")
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    uri = youtube._gemini_upload_video(key, str(video), log=log)
    body = {"contents": [{"parts": [
        {"file_data": {"mime_type": "video/mp4", "file_uri": uri}},
        {"text": _SEG_PROMPT}]}],
        "generationConfig": {"response_mime_type": "application/json",
                             "temperature": 0.2, "maxOutputTokens": 8192,
                             "thinkingConfig": {"thinkingBudget": 0}}}
    r = requests.post(brain.GEMINI_URL.format(model=model), params={"key": key}, json=body, timeout=180)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini 태깅 오류 {r.status_code}: {r.text[:200]}")
    cand = r.json()["candidates"][0]
    raw = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
    segs = brain._parse_json(raw).get("segments", [])
    out = []
    for s in segs:
        try:
            out.append({"start": float(s.get("start", 0)), "end": float(s.get("end", 0)),
                        "scene": str(s.get("scene", "")), "tags": s.get("tags", [])})
        except Exception:
            pass
    return out


# ─────────────── 3) 장면전환(컷) 감지 + 앞쪽우선 스냅 ───────────────
def scene_cuts(video):
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(video), "-filter:v",
                        "select='gt(scene,0.3)',showinfo", "-an", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=_NO_WINDOW)
    cuts = [0.0]
    for ln in (r.stderr or "").splitlines():
        if "pts_time:" in ln:
            try:
                cuts.append(float(ln.split("pts_time:")[1].split()[0]))
            except Exception:
                pass
    return sorted(set(cuts))


def snap(t, cuts, tol=0.5):
    """Gemini는 시작을 살짝 이르게 잡는 경향 → 'Gemini 시작 이후(>= t-tol) 컷 우선'으로 스냅해
    직전 제품으로 뒤로 밀리는 것 방지."""
    if not cuts:
        return max(0.0, t)
    fwd = [c for c in cuts if c >= t - tol]
    return min(fwd, key=lambda c: abs(c - t)) if fwd else min(cuts, key=lambda c: abs(c - t))


# ─────────────── 4) 대본 ↔ 화면 매칭 (EDL) ───────────────
def match_script(cfg, beats, segments):
    key = _key(cfg)
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    seg_lines = "\n".join(
        f'{i}: [{s["start"]}~{s["end"]}s] {s["scene"]} (태그:{",".join(map(str, s.get("tags", [])))})'
        for i, s in enumerate(segments))
    prompt = f"""너는 쇼츠 편집 감독이다. 아래 '내 대본'의 각 문장(비트)에 가장 잘 맞는 '소스 영상 구간'을 골라 배정하라.
- 화면 내용이 대본과 맞아야 한다(태그/장면 참고). 맞는 구간이 없으면 seg_ids를 빈 배열로.
- 각 비트에 대체 후보(alt_ids) 1~3개도 제시(리롤용, 겹치지 않게).

[내 대본]
{chr(10).join(f'{i}. {b}' for i, b in enumerate(beats))}

[소스 영상 구간들]
{seg_lines}

반드시 JSON만: {{"edl":[{{"beat":0,"seg_ids":[..],"alt_ids":[..]}}, ...]}}"""
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json", "temperature": 0.2,
                                 "maxOutputTokens": 4096, "thinkingConfig": {"thinkingBudget": 0}}}
    r = requests.post(brain.GEMINI_URL.format(model=model), params={"key": key}, json=body, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"매칭 오류 {r.status_code}")
    cand = r.json()["candidates"][0]
    raw = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
    return brain._parse_json(raw).get("edl", [])


# ─────────────── 5) ElevenLabs TTS (문자 타임스탬프) ───────────────
def tts(cfg, text):
    key = (cfg.get("elevenlabs_api_key") or "").strip()
    voice = (cfg.get("elevenlabs_voice_id") or "").strip()
    if not key or not voice:
        raise RuntimeError("ElevenLabs 키/보이스가 config에 없습니다")
    model = cfg.get("elevenlabs_model", "eleven_multilingual_v2")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps?output_format=mp3_44100_128"
    r = requests.post(url, headers={"xi-api-key": key, "Content-Type": "application/json"},
                      json={"text": text, "model_id": model,
                            "voice_settings": {"stability": 0.5, "similarity_boost": 0.8,
                                               "style": 0, "use_speaker_boost": True}}, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs 오류 {r.status_code}: {r.text[:150]}")
    j = r.json()
    audio = base64.b64decode(j["audio_base64"])
    al = j.get("alignment") or j.get("normalized_alignment") or {}
    chars = al.get("characters", [])
    st = al.get("character_start_times_seconds", [])
    en = al.get("character_end_times_seconds", [])
    words = []
    cur = ""
    cs = None
    ce = 0.0
    for c, s, e in zip(chars, st, en):
        if c == " ":
            if cur:
                words.append({"t": cur, "s": cs, "d": ce - cs})
                cur = ""
                cs = None
            continue
        if cs is None:
            cs = s
        ce = e
        cur += c
    if cur:
        words.append({"t": cur, "s": cs, "d": ce - cs})
    return audio, words


def _chunks(words):
    """짧은 구절(최대 2어절, 문장부호에서 끊기)."""
    out, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= 2 or str(w["t"]).strip().endswith((".", ",", "!", "?", "…")):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    ev = []
    for g in out:
        ev.append({"s": g[0]["s"], "e": g[-1]["s"] + g[-1]["d"],
                   "t": " ".join(x["t"] for x in g).strip().rstrip(".")})
    return ev


# ─────────────── 컷/조립 ───────────────
_VF_916 = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"


def _cut(src, start, dur, out, src_len=None):
    """소스에서 start부터 dur초를 잘라 9:16로. 항상 정확히 dur초를 만들어(음성과 길이 일치)
    최종 -shortest가 음성을 자르지 않게 한다.
    - start+dur가 소스 끝을 넘으면 start를 당겨서 소스 안에 맞춤.
    - 비트가 소스 전체보다 길면 소스를 반복 재생해서 dur를 채움."""
    if src_len is None:
        src_len = _dur(src)
    if src_len and dur >= src_len:
        _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-stream_loop", "-1",
              "-i", str(src), "-t", f"{dur:.3f}", "-an", "-vf", _VF_916, "-r", "30",
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(out)])
    else:
        if src_len:
            start = max(0.0, min(start, src_len - dur))
        _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
              "-i", str(src), "-an", "-vf", _VF_916, "-r", "30",
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(out)])


def _thumb(video, out):
    try:
        _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", "0.3", "-i", str(video),
              "-frames:v", "1", "-vf", "scale=320:-2", str(out)])
    except Exception:
        pass


def _ass_ts(t):
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _write_ass(path, beats):
    t = 0.0
    ev = []
    for b in beats:
        for c in b["sub"]:
            ev.append(f"Dialogue: 0,{_ass_ts(t + c['s'])},{_ass_ts(t + c['e'])},Default,,0,0,0,,{c['t']}")
        t += b["dur"]
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
            "Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
            "Style: Default,Malgun Gothic,72,&H00FFFFFF,&H00000000,&H90000000,-1,1,5,2,2,80,80,320\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    Path(path).write_text(head + "\n".join(ev) + "\n", encoding="utf-8")


def assemble(proj, state, cfg):
    """비트별 v{i}.mp4 / a{i}.mp3 를 이어붙이고 자막·BGM 얹어 final.mp4."""
    proj = Path(proj)
    pc = str(proj)   # 모든 ffmpeg를 proj 폴더에서 실행 + 파일명만(경로 중복/콜론 이스케이프 회피)
    beats = state["beats"]
    (proj / "_v.txt").write_text("".join(f"file 'v{i}.mp4'\n" for i in range(len(beats))), encoding="utf-8")
    (proj / "_a.txt").write_text("".join(f"file 'a{i}.mp3'\n" for i in range(len(beats))), encoding="utf-8")
    _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
          "-i", "_v.txt", "-c", "copy", "video.mp4"], cwd=pc)
    _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
          "-i", "_a.txt", "-c:a", "aac", "-b:a", "192k", "audio.m4a"], cwd=pc)
    _write_ass(proj / "sub.ass", beats)
    bgm = (cfg.get("autoshorts_bgm") or "").strip()
    if bgm and Path(bgm).exists():
        fc = ("[0:v]ass=sub.ass[v];"
              "[1:a]aformat=sample_rates=44100:channel_layouts=stereo[voice];"
              "[2:a]volume=-20dB,aformat=sample_rates=44100:channel_layouts=stereo[bg];"
              "[voice][bg]amix=inputs=2:duration=first:normalize=0[aout]")
        _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", "video.mp4", "-i", "audio.m4a",
              "-stream_loop", "-1", "-i", bgm, "-filter_complex", fc, "-map", "[v]", "-map", "[aout]",
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "192k",
              "-shortest", "final.mp4"], cwd=pc)
    else:
        _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", "video.mp4", "-i", "audio.m4a",
              "-vf", "ass=sub.ass", "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast",
              "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-shortest", "final.mp4"], cwd=pc)
    return proj / "final.mp4"


def _beat_candidates(edl_entry, segments, cuts, min_cands=4):
    """primary seg_ids + alt_ids → 스냅된 시작초 후보 리스트(중복 제거).
    후보가 min_cands 미만이면 '주 구간 주변의 다른 장면'으로 보충해 리롤 옵션을 항상 확보한다.
    cands[0]은 항상 매칭된 주 구간(첫 생성에 쓰임)."""
    seen, cands = set(), []

    def add(idx):
        if not (0 <= idx < len(segments)):
            return
        st = round(snap(float(segments[idx]["start"]), cuts), 2)
        if st not in seen:
            seen.add(st)
            cands.append(st)

    prim = [int(i) for i in (edl_entry.get("seg_ids") or []) if str(i).lstrip("-").isdigit()]
    alts = [int(i) for i in (edl_entry.get("alt_ids") or []) if str(i).lstrip("-").isdigit()]
    for i in prim:
        add(i)
    for i in alts:
        add(i)
    if len(cands) < min_cands and segments:
        base = prim[0] if prim else 0
        for j in sorted(range(len(segments)), key=lambda k: abs(k - base)):
            if len(cands) >= min_cands:
                break
            add(j)
    return cands or [0.0]


# ─────────────── 메인: 생성 / 리롤 ───────────────
def create(base, cfg, script_text, source_path=None, source_url=None, log=print):
    pid = uuid.uuid4().hex[:10]
    proj = proj_root(base) / pid
    proj.mkdir(parents=True, exist_ok=True)
    beats_txt = [ln.strip() for ln in (script_text or "").splitlines() if ln.strip()]
    if not beats_txt:
        raise RuntimeError("대본이 비어있습니다 (한 줄에 한 비트)")

    src = proj / "source.mp4"
    log("[1/6] 소스 영상 준비...")
    if source_url:
        ingest_url(source_url, src, cfg, log=log)
    elif source_path:
        # 업로드 파일 → 표준 mp4로 정규화
        _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source_path),
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-an", str(src)])
    else:
        raise RuntimeError("소스(링크 또는 파일)가 없습니다")

    log("[2/6] Gemini 시각 구간 태깅...")
    segments = visual_segments(cfg, src, log=log)
    log(f"      구간 {len(segments)}개")
    log("[3/6] 장면전환 감지...")
    cuts = scene_cuts(src)
    log("[4/6] 대본↔화면 매칭...")
    edl = match_script(cfg, beats_txt, segments)
    edl_by = {int(e.get("beat", i)): e for i, e in enumerate(edl)}

    log("[5/6] 음성(ElevenLabs) + 컷...")
    beats = []
    for i, text in enumerate(beats_txt):
        audio, words = tts(cfg, text)
        (proj / f"a{i}.mp3").write_bytes(audio)
        dur = _dur(proj / f"a{i}.mp3") or 3.0
        cands = _beat_candidates(edl_by.get(i, {}), segments, cuts)
        _cut(src, cands[0], dur, proj / f"v{i}.mp4")
        _thumb(proj / f"v{i}.mp4", proj / f"thumb{i}.jpg")
        beats.append({"text": text, "dur": dur, "candidates": cands, "used": 0,
                      "sub": _chunks(words)})
        log(f"      비트{i+1}/{len(beats_txt)} 음성 {dur:.1f}s")

    state = {"pid": pid, "script": beats_txt, "segments": segments, "cuts": cuts,
             "edl": edl, "beats": beats}
    log("[6/6] 조립 + 자막 + BGM...")
    assemble(proj, state, cfg)
    (proj / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"pid": pid, "beats": _beats_public(pid, beats)}


def reroll(base, cfg, pid, beat_idx):
    proj = proj_root(base) / pid
    st = json.loads((proj / "state.json").read_text(encoding="utf-8"))
    beats = st["beats"]
    if not (0 <= beat_idx < len(beats)):
        raise RuntimeError("잘못된 비트 번호")
    b = beats[beat_idx]
    cands = b["candidates"]
    if len(cands) <= 1:
        raise RuntimeError("이 비트는 대체 후보가 없어요")
    b["used"] = (b["used"] + 1) % len(cands)
    _cut(proj / "source.mp4", cands[b["used"]], b["dur"], proj / f"v{beat_idx}.mp4")
    _thumb(proj / f"v{beat_idx}.mp4", proj / f"thumb{beat_idx}.jpg")
    assemble(proj, st, cfg)   # 오디오/자막 동일, 비디오만 갱신
    (proj / "state.json").write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"pid": pid, "beats": _beats_public(pid, beats)}


def _beats_public(pid, beats):
    return [{"i": i, "text": b["text"], "dur": round(b["dur"], 1),
             "cand": b["used"] + 1, "cand_total": len(b["candidates"]),
             "thumb": f"/autoshorts/{pid}/thumb{i}.jpg"} for i, b in enumerate(beats)]
