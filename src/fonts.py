# -*- coding: utf-8 -*-
"""자막용 폰트 라이브러리 (전 계정 공유). BASE/fonts/ 에 저장.
업로드/삭제/목록 + 폴더에서 가져오기. 폰트 내부 family명 추출(ASS Fontname용)."""
import shutil
from pathlib import Path

from PIL import ImageFont

_EXT = (".ttf", ".otf", ".ttc")


def fdir(base):
    d = Path(base) / "fonts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def family_name(path):
    """폰트 파일의 내부 family 이름(ASS Fontname으로 써야 libass가 찾음)."""
    try:
        fam, _style = ImageFont.truetype(str(path), 20).getname()
        return (fam or "").strip() or Path(path).stem
    except Exception:
        return Path(path).stem


def list_fonts(base):
    out = []
    for p in sorted(fdir(base).glob("*")):
        if p.suffix.lower() in _EXT:
            out.append({"file": p.name, "family": family_name(p), "name": p.stem})
    return out


def save_upload(base, filename, data):
    safe = Path(filename).name
    if Path(safe).suffix.lower() not in _EXT:
        raise RuntimeError("ttf/otf/ttc 파일만 가능합니다")
    (fdir(base) / safe).write_bytes(data)
    return {"file": safe, "family": family_name(fdir(base) / safe), "name": Path(safe).stem}


def delete_font(base, filename):
    p = fdir(base) / Path(filename).name
    if p.exists() and p.suffix.lower() in _EXT:
        p.unlink()


def import_folder(base, folder):
    """지정 폴더의 폰트를 라이브러리로 복사(중복 제외). 가져온 개수 반환."""
    src = Path(folder)
    n = 0
    if src.exists() and src.is_dir():
        for p in src.rglob("*"):
            if p.suffix.lower() in _EXT:
                dst = fdir(base) / p.name
                if not dst.exists():
                    try:
                        shutil.copy(str(p), str(dst))
                        n += 1
                    except Exception:
                        pass
    return n
