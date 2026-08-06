# -*- coding: utf-8 -*-
"""jp1 릴스 변환기 — 사용자가 구해온 한국(현장) 영상 → jp1 채널 공식의 해외판.

실측 공식(_reels_jp1.md, 표본 44): 원본영상+자막 86% — 흰 고딕+노랑 강조 일본어
자막 거의 매초, 컷 없이 풀버전, 원본 사운드 유지. 영상을 Gemini가 보고 일본어
자막 세그먼트(타임코드)를 쓰고, ASS로 구워 ffmpeg 번인한다. 한국어 해석 병기(검수용).
"""
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import requests

from .reference import GEMINI_URL, _parse_json
from .reelscout import _gem_text, _upload_video
from .autoshorts import FFMPEG, FFPROBE, _NO_WINDOW

JP1_SUB_PROMPT = """이 영상은 한국에서 화제가 된 현장 영상이다. 일본 시청자에게 '진짜 한국'을
보여주는 릴스 채널(리얼 칸고쿠)용으로, 영상을 보면서 일본어 자막을 써라.

[채널 실측 공식 — 반드시 재현]
- 자막은 거의 매초(2~4초 간격), 짧고 직설적으로. 상황 설명+시청자 공감 코멘트 혼합.
- 첫 3초 자막 = 가장 자극적인 후킹(「まさかの…」「衝撃…」류) — 상황을 압축.
- 핵심 대목(반전·충격)은 hl=true (노란 박스 강조).
- 화면 속 한국어(간판·말소리)는 일본어로 자연 의역. 번역투 금지, 일본 커뮤 감성.
- 원본 사운드를 살리므로 자막이 내레이션 역할. 과장 OK, 사실 날조 금지.

JSON만 출력:
{"segments": [{"s": 0.0, "e": 2.5, "ja": "자막(8~18자)", "ko": "한국어 해석", "hl": false}],
 "title_ja": "제목(후킹 한 줄)", "title_ko": "제목 한국어",
 "caption": "인스타 캡션 — 상황 설명+마지막 시청자 질문, 150~300자 일본어",
 "caption_ko": "캡션 한국어 해석",
 "origin": "한국 확실|불명 — 화면 증거로 판정",
 "risk": "초상권·폭력 등 주의점(없으면 빈 문자열)"}"""

_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: WB,Yu Gothic,64,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,4,1,2,60,60,240,1
Style: YB,Yu Gothic,66,&H00101010,&H00101010,&H0020D8FF,&H0020D8FF,-1,0,0,0,100,100,0,0,3,5,0,2,60,60,240,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ts(sec):
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _probe_dur(path):
    p = subprocess.run([FFPROBE, "-v", "quiet", "-show_entries",
                        "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", creationflags=_NO_WINDOW, timeout=60)
    try:
        return float((p.stdout or "0").strip())
    except ValueError:
        return 0.0


def convert(cfg, base, video_path, account="real_kankoku", log=print):
    """한국 현장 영상 → jp1 공식 일본어 자막 번인 릴스 완성팩."""
    src = Path(video_path)
    if not src.exists():
        raise RuntimeError(f"영상이 없습니다: {video_path}")
    key = (cfg.get("gemini_api_key") or "").strip()
    if not key:
        raise RuntimeError("Gemini API 키가 없습니다")
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    dur = _probe_dur(src)
    log(f"      🎥 영상 분석 중 ({dur:.0f}초) — 일본어 자막 대본 작성")
    uri = _upload_video(key, src, log=log)
    body = {"contents": [{"role": "user", "parts": [
                {"file_data": {"mime_type": "video/mp4", "file_uri": uri}},
                {"text": JP1_SUB_PROMPT}]}],
            "generationConfig": {"response_mime_type": "application/json",
                                 "temperature": 0.5,
                                 "maxOutputTokens": 8192,
                                 "thinkingConfig": {"thinkingBudget": 0}}}
    resp = requests.post(GEMINI_URL.format(model=model), params={"key": key},
                         json=body, timeout=300)
    if resp.status_code != 200:
        raise RuntimeError(f"자막 생성 실패 {resp.status_code}")
    data = _parse_json(_gem_text(resp)) or {}
    segs = []
    for sg in data.get("segments") or []:
        ja = re.sub(r"\s+", " ", str(sg.get("ja", ""))).strip()
        try:
            s, e = float(sg.get("s", 0)), float(sg.get("e", 0))
        except (TypeError, ValueError):
            continue
        if ja and e > s:
            if dur:
                s, e = min(s, dur - 0.3), min(e, dur)
            segs.append({"s": round(s, 2), "e": round(e, 2), "ja": ja,
                         "ko": str(sg.get("ko", "")).strip(),
                         "hl": bool(sg.get("hl"))})
    if len(segs) < 3:
        raise RuntimeError(f"자막 세그먼트 부족({len(segs)}) — 영상을 확인하세요")
    log(f"      ✍ 자막 {len(segs)}개 — {data.get('title_ja', '')}"
        + (f" ⚠️{data.get('risk')}" if data.get("risk") else "")
        + f" · 원산지 판정: {data.get('origin', '?')}")

    root = Path(base) / cfg.get("output_dir", "결과물")
    slug = re.sub(r"[^\w가-힣]+", "_", str(data.get("title_ko")
                                          or src.stem))[:24].strip("_")
    pack = root / f"{datetime.now():%Y%m%d_%H%M}_reel1_{slug}_JP"
    n = 1
    while pack.exists():
        n += 1
        pack = root / f"{datetime.now():%Y%m%d_%H%M}_reel1_{slug}_JP_{n}"
    pack.mkdir(parents=True)

    ass = _ASS_HEADER + "\n".join(
        f"Dialogue: 0,{_ts(sg['s'])},{_ts(sg['e'])},{'YB' if sg['hl'] else 'WB'}"
        f",,0,0,0,,{sg['ja'].replace(',', '，')}" for sg in segs)
    ass_p = pack / "subs.ass"
    ass_p.write_text(ass, encoding="utf-8")
    out = pack / "video.mp4"
    # ffmpeg ass 필터 경로: 콜론·역슬래시 이스케이프 (Windows 함정)
    ass_f = str(ass_p).replace("\\", "/").replace(":", "\\:")
    p = subprocess.run(
        [FFMPEG, "-y", "-i", str(src),
         "-vf", f"scale='min(1080,iw)':-2,ass='{ass_f}'",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=_NO_WINDOW, timeout=900)
    if p.returncode != 0 or not out.exists():
        raise RuntimeError(f"자막 번인 실패: {(p.stderr or '')[-160:]}")
    subprocess.run([FFMPEG, "-y", "-ss", "1", "-i", str(out), "-frames:v", "1",
                    "-q:v", "3", str(pack / "01.jpg")],
                   capture_output=True, creationflags=_NO_WINDOW, timeout=60)
    shutil.copy2(src, pack / "_source.mp4")

    cap = str(data.get("caption", "")).strip()
    (pack / "caption.txt").write_text(cap, encoding="utf-8")
    title = str(data.get("title_ja") or slug)
    meta = {"type": "cardnews", "template": "reel", "reel_template": "jp1",
            "source": "jp1reel", "title": title, "lang": "ja",
            "ig_account": account, "video": "video.mp4",
            "reel_entries": [{"t": sg["ja"], "v": ""} for sg in segs],
            "entries_ko": [sg["ko"] for sg in segs],
            "caption_ko": str(data.get("caption_ko", "")),
            "origin": str(data.get("origin", "")),
            "risk": str(data.get("risk", "")),
            "created": datetime.now().isoformat(timespec="seconds")}
    (pack / "meta.json").write_text(json.dumps(meta, ensure_ascii=False,
                                               indent=2), encoding="utf-8")
    rows = "".join(f"<tr><td>{_ts(sg['s'])[2:]}</td><td>{sg['ja']}</td>"
                   f"<td class='ko'>{sg['ko']}</td></tr>" for sg in segs)
    (pack / "review.html").write_text(f"""<!doctype html><meta charset="utf-8">
<title>{title}</title>
<style>body{{font-family:sans-serif;max-width:760px;margin:24px auto;padding:0 12px}}
video{{width:100%;border-radius:12px}}table{{border-collapse:collapse;width:100%}}
td{{border:1px solid #ccc;padding:5px 9px}}td.ko{{color:#777}}
pre{{white-space:pre-wrap;background:#f6f6f8;padding:14px;border-radius:10px}}</style>
<h2>🗾 {title}</h2>
<p>업로드 계정: @{account} · 원산지 판정: {meta['origin']}{' · ⚠️' + meta['risk'] if meta['risk'] else ''}</p>
<video src="video.mp4" controls></video>
<h3>자막 (🇯🇵/🇰🇷)</h3><table>{rows}</table>
<h3>캡션 🇯🇵</h3><pre>{cap}</pre><h3>캡션 해석 🇰🇷</h3><pre>{meta['caption_ko']}</pre>""",
                                     encoding="utf-8")
    log(f"      📦 jp1 릴스 완성: {pack.name} ({out.stat().st_size // 1024}KB)")
    return {"pack": str(pack), "video": str(out), "title": title,
            "origin": meta["origin"], "risk": meta["risk"]}
