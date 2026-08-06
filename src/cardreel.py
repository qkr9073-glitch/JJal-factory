# -*- coding: utf-8 -*-
"""카드팩 → 슬라이드 릴스 (jp2 셀렉션형 릴스 공식 실측 재현).

실측 공식(레퍼런스/_reels_jp2.md): 사진슬라이드 86.7%, 5~15초, BGM 필수,
컷 전환 없는 정보 밀집형. 완성팩의 카드(4:5)를 9:16 캔버스에
블러 배경 + 중앙 배치로 얹고 BGM을 깔아 video.mp4 생성.

meta.type은 cardnews 그대로 둔다(📤 캐러셀 발행과 공존) — 릴스 발행은
예약(type=reel, video_pack) 또는 발행 API에서 video.mp4를 쓴다.
"""
import os
import random
import subprocess
from pathlib import Path

from .autoshorts import FFMPEG, FFPROBE, _NO_WINDOW


def _run(cmd):
    # encoding 명시: 한글 경로가 있으면 cp949 기본 디코딩이 ffmpeg utf-8 출력에 깨짐
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       creationflags=_NO_WINDOW, timeout=600)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg 실패: {(p.stderr or '')[-200:]}")


def _pick_bgm(base, bgm_code="7777"):
    # 무드 폴더(미스터리/감동/락/무난) 우선, 비었으면 무난→7777 순 폴백
    for code in dict.fromkeys([str(bgm_code), "무난", "7777"]):
        d = Path(base) / "bgm" / code
        files = [p for p in d.glob("*")
                 if p.suffix.lower() in (".mp3", ".wav", ".m4a")]
        if files:
            return str(random.choice(files))
    return ""


def build(cfg, base, pack_dir, per_card=3.2, cover_sec=3.0, cta_sec=2.0,
          bgm_code="7777", bgm_file="", log=print):
    """팩 카드들 → pack/video.mp4 (1080x1920, BGM 포함). 반환: mp4 경로."""
    pack = Path(pack_dir)
    cards = sorted(pack.glob("[0-9][0-9].jpg"))
    if not cards:
        raise RuntimeError("팩에 카드 이미지가 없습니다")
    durs = [cover_sec] + [per_card] * max(0, len(cards) - 2) \
        + ([cta_sec] if len(cards) > 1 else [])
    total = sum(durs)
    tmp = pack / "_reel_tmp"
    tmp.mkdir(exist_ok=True)
    log(f"      🎞 슬라이드 릴스 굽는 중 — 카드 {len(cards)}장, {total:.0f}초")
    segs = []
    vf = ("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
          "crop=1080:1920,boxblur=28:2,eq=brightness=-0.18[bg];"
          "[0:v]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p")
    for i, (c, d) in enumerate(zip(cards, durs)):
        out = tmp / f"s{i:02d}.mp4"
        _run([FFMPEG, "-y", "-loop", "1", "-t", f"{d:.2f}", "-i", str(c),
              "-filter_complex", vf, "-r", "30", "-c:v", "libx264",
              "-preset", "veryfast", "-crf", "20", str(out)])
        segs.append(out)
    lst = tmp / "list.txt"
    lst.write_text("\n".join(f"file '{s.name}'" for s in segs), encoding="utf-8")
    silent = tmp / "silent.mp4"
    _run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
          "-c", "copy", str(silent)])
    out_mp4 = pack / "video.mp4"
    bgm = bgm_file or _pick_bgm(base, bgm_code)
    if bgm:
        fade = max(0.5, total - 1.2)
        _run([FFMPEG, "-y", "-i", str(silent), "-stream_loop", "-1", "-i", bgm,
              "-filter_complex",
              f"[1:a]volume=0.5,afade=t=out:st={fade:.1f}:d=1.2[a]",
              "-map", "0:v", "-map", "[a]", "-shortest",
              "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
              "-movflags", "+faststart", str(out_mp4)])
        log(f"      🎵 BGM: {Path(bgm).stem}")
    else:
        _run([FFMPEG, "-y", "-i", str(silent), "-c", "copy",
              "-movflags", "+faststart", str(out_mp4)])
        log("      (BGM 라이브러리 비어있음 — 무음)")
    for s in segs:
        try:
            s.unlink()
        except OSError:
            pass
    try:
        lst.unlink()
        silent.unlink()
        tmp.rmdir()
    except OSError:
        pass
    log(f"      ✅ 릴스 완성: {out_mp4.name} ({out_mp4.stat().st_size // 1024}KB, "
        f"{total:.0f}초)")
    return str(out_mp4)


def _reel_entries(cfg, pack, meta, plan, items, log=print):
    """릴스 프레임용 짧은 리스트 항목 — 실측 공식(순위·표 밀집)에 맞게 카드 비트를
    간결한 엔트리로 변환(Gemini 1회, meta.reel_entries 캐시). 실패 시 카드 제목 사용."""
    import json as _json
    import re as _re
    import requests
    cached = meta.get("reel_entries")
    if cached:
        out = []
        for i, t in enumerate(cached):
            if isinstance(t, dict):
                out.append({"num": i + 1, "title": t.get("t") or t.get("title", ""),
                            "value": t.get("v") or t.get("value", "")})
            else:
                out.append({"num": i + 1, "title": str(t)})
        return out
    title = (plan.get("title_main") or plan.get("title") or "").strip()
    lang = meta.get("lang") or cfg.get("card_lang", "ko")
    m = _re.search(r"TOP\s*(\d+)|(\d+)\s*選|(\d+)\s*가지", title, _re.I)
    n = int(next((g for g in (m.groups() if m else []) if g), 0) or 0) or 6
    n = max(3, min(8, n))
    try:
        from .reference import GEMINI_URL, _parse_json
        from .reelscout import _gem_text
        key = (cfg.get("gemini_api_key") or "").strip()
        mat = "\n".join(f"- {it.get('title', '')} :: {str(it.get('body', ''))[:220]}"
                        for it in items)
        lang_word = "일본어 네이티브" if lang == "ja" else "한국어"
        prompt = (f"인스타 릴스 단일 프레임(순위·리스트 '표')용 항목을 만들어라.\n"
                  f"제목: {title}\n재료:\n{mat}\n\n"
                  f"{n}개, 항목명 t=각 6~16자, {lang_word}, 명사형 리스트 톤(서사 문장 "
                  f"금지), 재료와 보편 상식 범위만(수치·사실 날조 금지), 중복 금지.\n"
                  f"v=값 열(순위표에 어울리는 실측 가능한 값 — 나이·금액·%% 등. "
                  f"근거 없으면 반드시 빈 문자열, 지어내기 절대 금지).\n"
                  f'JSON만: {{"entries": [{{"t": "항목", "v": ""}}]}}')
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"response_mime_type": "application/json",
                                     "temperature": 0.6,
                                     "maxOutputTokens": 1024,
                                     "thinkingConfig": {"thinkingBudget": 0}}}
        resp = requests.post(GEMINI_URL.format(
            model=cfg.get("gemini_model", "gemini-2.5-flash")),
            params={"key": key}, json=body, timeout=120)
        if resp.status_code != 200:
            raise RuntimeError(f"항목 생성 {resp.status_code}")
        ents = []
        for e in ((_parse_json(_gem_text(resp)) or {}).get("entries") or [])[:n]:
            if isinstance(e, dict) and str(e.get("t", "")).strip():
                ents.append({"t": str(e["t"]).strip(),
                             "v": str(e.get("v", "")).strip()})
            elif not isinstance(e, dict) and str(e).strip():
                ents.append({"t": str(e).strip(), "v": ""})
        if len(ents) >= 3:
            meta["reel_entries"] = ents
            (pack / "meta.json").write_text(
                _json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            log(f"      📋 릴스 리스트 항목 {len(ents)}개 생성 (캐시됨)")
            return [{"num": i + 1, "title": t["t"], "value": t["v"]}
                    for i, t in enumerate(ents)]
    except Exception as e:
        log(f"      (릴스 항목 생성 실패 — 카드 제목 사용: {str(e)[:60]})")
    return [{"num": it.get("num") or i + 1, "title": it.get("title", "")}
            for i, it in enumerate(items)]


def build_single(cfg, base, pack_dir, sec=9.0, bgm_code="무난", bgm_file="",
                 log=print):
    """jp2 릴스 공식 그대로의 릴스: 정보 밀집 '단일 프레임' 정지화면 + BGM.
    실측 근거 — 사진슬라이드 76%·대부분 5~15초·컷 0·한 화면에 제목+리스트 전부·저장 유도.
    (카드 슬라이드쇼 방식(build)은 공식과 달라 기본에서 제외 — 사용자 지적)"""
    import json as _json
    from cardnews import render as _render
    pack = Path(pack_dir)
    data = _json.loads((pack / "items.json").read_text(encoding="utf-8"))
    plan, items = data.get("plan") or {}, data.get("items") or []
    meta = {}
    try:
        meta = _json.loads((pack / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    cfg2 = dict(cfg)
    cfg2["card_lang"] = meta.get("lang") or cfg.get("card_lang", "ko")
    cfg2["_reel_handle"] = (meta.get("ig_account") or "").strip()
    cov = str(meta.get("cover_image") or "")
    if not cov and meta.get("source_pack"):       # 현지화판: 원본 팩의 표지를 찾아온다
        sp = pack.parent / str(meta["source_pack"])
        for fn in ("_cover_clean.jpg", "_cover.jpg"):
            if (sp / fn).exists():
                cov = str(sp / fn)
                break
    if cov.endswith("_cover.jpg") and Path(cov[:-4] + "_clean.jpg").exists():
        cov = cov[:-4] + "_clean.jpg"     # 원 강조 전 원본이 있으면 그쪽 (릴스는 깨끗하게)
    if cov and Path(cov).exists():
        cfg2["cover_image"] = cov
    rows = _reel_entries(cfg, pack, meta, plan, items, log=log)
    frame = pack / "reel.jpg"
    _render.render_reel_frame(plan, rows, cfg2, frame)
    log(f"      🖼 릴스 단일 프레임 렌더 — {len(rows)}항목, {sec:.0f}초 정지화면")
    return mp4_from_frame(base, pack, frame, sec=sec, bgm_code=bgm_code,
                          bgm_file=bgm_file, log=log)


def mp4_from_frame(base, pack, frame, sec=9.0, bgm_code="무난", bgm_file="",
                   log=print):
    """단일 프레임 이미지 → pack/video.mp4 (1080x1920 정지화면 + BGM)."""
    pack = Path(pack)
    out_mp4 = pack / "video.mp4"
    tmp_v = pack / "_reel_single.mp4"
    _run([FFMPEG, "-y", "-loop", "1", "-t", f"{sec:.2f}", "-i", str(frame),
          "-vf", "scale=1080:1920,format=yuv420p", "-r", "30",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(tmp_v)])
    bgm = bgm_file or _pick_bgm(base, bgm_code)
    if bgm:
        fade = max(0.5, sec - 1.2)
        _run([FFMPEG, "-y", "-i", str(tmp_v), "-stream_loop", "-1", "-i", bgm,
              "-filter_complex",
              f"[1:a]volume=0.5,afade=t=out:st={fade:.1f}:d=1.2[a]",
              "-map", "0:v", "-map", "[a]", "-shortest",
              "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
              "-movflags", "+faststart", str(out_mp4)])
        log(f"      🎵 BGM: {Path(bgm).stem}")
    else:
        tmp_v.replace(out_mp4)
    try:
        tmp_v.unlink()
    except OSError:
        pass
    log(f"      ✅ 릴스 완성(단일 프레임): video.mp4 "
        f"({out_mp4.stat().st_size // 1024}KB, {sec:.0f}초)")
    return str(out_mp4)
