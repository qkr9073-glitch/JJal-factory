# -*- coding: utf-8 -*-
"""BGM 라이브러리 (계정별). BASE/bgm/<code>/ 에 저장. mp3/wav만.
목록/업로드/삭제 + 다른 계정 것 불러오기(통합 안 함)."""
import shutil
from pathlib import Path

_EXT = (".mp3", ".wav")


def bdir(base, code):
    d = Path(base) / "bgm" / str(code).strip()
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_bgm(base, code):
    return [{"file": p.name, "name": p.stem, "url": f"/bgm/{str(code).strip()}/{p.name}"}
            for p in sorted(bdir(base, code).glob("*")) if p.suffix.lower() in _EXT]


def save_upload(base, code, filename, data):
    safe = Path(filename).name
    if Path(safe).suffix.lower() not in _EXT:
        raise RuntimeError("mp3/wav 파일만 가능합니다")
    (bdir(base, code) / safe).write_bytes(data)
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
