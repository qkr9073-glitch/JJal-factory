# -*- coding: utf-8 -*-
"""BGM 라이브러리 (계정별). BASE/bgm/<code>/ 에 저장. mp3/wav만.
목록/업로드/삭제 + 다른 계정 것 불러오기(통합 안 함)."""
import shutil
from pathlib import Path

from . import autoshorts

_EXT = (".mp3", ".wav")
_VIDEO_EXT = (".mp4", ".m4a", ".mov", ".webm")   # 영상/컨테이너 → 소리만 추출해 mp3로 저장


def _trim_lead_silence(path):
    """BGM 앞부분 무음구간 자동 제거(원본보다 무음이 긴 경우 방지). 실패해도 원본 유지."""
    p = Path(path)
    tmp = p.with_name(p.stem + ".trim" + p.suffix)
    af = "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0:detection=peak"
    if p.suffix.lower() == ".wav":
        acodec = ["-c:a", "pcm_s16le"]
    else:
        acodec = ["-c:a", "libmp3lame", "-b:a", "256k"]
    try:
        autoshorts._run([autoshorts.FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                         "-i", str(p), "-af", af] + acodec + [str(tmp)])
        if tmp.exists() and tmp.stat().st_size > 1024:
            tmp.replace(p)
        elif tmp.exists():
            tmp.unlink()
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def bdir(base, code):
    d = Path(base) / "bgm" / str(code).strip()
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_bgm(base, code):
    return [{"file": p.name, "name": p.stem, "url": f"/bgm/{str(code).strip()}/{p.name}"}
            for p in sorted(bdir(base, code).glob("*")) if p.suffix.lower() in _EXT]


def save_upload(base, code, filename, data):
    safe = Path(filename).name
    suffix = Path(safe).suffix.lower()
    if suffix not in _EXT + _VIDEO_EXT:
        raise RuntimeError("mp3/wav/mp4 파일만 가능합니다")
    d = bdir(base, code)
    if suffix in _VIDEO_EXT:         # 영상이면 소리만 추출해 mp3로 저장
        tmp = d / ("_up" + suffix)
        tmp.write_bytes(data)
        dst = d / (Path(safe).stem + ".mp3")
        try:
            autoshorts._run([autoshorts.FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                             "-i", str(tmp), "-vn", "-c:a", "libmp3lame", "-b:a", "256k", str(dst)])
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        if not dst.exists() or dst.stat().st_size < 1024:
            raise RuntimeError("이 영상에서 소리를 추출하지 못했어요 (오디오 트랙 확인)")
    else:
        dst = d / safe
        dst.write_bytes(data)
    _trim_lead_silence(dst)          # 앞부분 무음 자동 제거
    return list_bgm(base, code)


def delete_bgm(base, code, file):
    p = bdir(base, code) / Path(file).name
    if p.exists() and p.suffix.lower() in _EXT:
        p.unlink()


def import_bgm(base, my_code, other_code, files):
    """다른 계정 BGM을 내 목록으로 복사."""
    n = 0
    for f in files:
        src = bdir(base, other_code) / Path(f).name
        if src.exists():
            dst = bdir(base, my_code) / src.name
            if not dst.exists():
                try:
                    shutil.copy(str(src), str(dst))
                    n += 1
                except Exception:
                    pass
    return n
