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
