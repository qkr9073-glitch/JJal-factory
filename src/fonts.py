# -*- coding: utf-8 -*-
"""자막용 폰트 라이브러리 (전 계정 공유). BASE/fonts/ 에 저장.
업로드/삭제/목록 + 폴더에서 가져오기. 폰트 내부 family명 추출(ASS Fontname용)."""
import shutil
import struct
from pathlib import Path

from PIL import ImageFont

_EXT = (".ttf", ".otf", ".ttc")


def _sfnt_name(path, want_id):
    """폰트 name 테이블에서 지정 nameID 문자열 추출.
    Windows(플랫폼3) 영어(1033) 우선 → 그 외 Windows → 아무거나. 없으면 None."""
    try:
        with open(path, "rb") as f:
            data = f.read()
        off0 = struct.unpack(">I", data[12:16])[0] if data[:4] == b"ttcf" else 0
        num = struct.unpack(">H", data[off0 + 4:off0 + 6])[0]
        p = off0 + 12
        name_off = None
        for _ in range(num):
            if data[p:p + 4] == b"name":
                name_off = struct.unpack(">I", data[p + 8:p + 12])[0]
                break
            p += 16
        if name_off is None:
            return None
        count, str_off = struct.unpack(">HH", data[name_off + 2:name_off + 6])
        base = name_off + str_off
        q = name_off + 6
        win_any = other = None
        for _ in range(count):
            pid, eid, lid, nid, ln, so = struct.unpack(">HHHHHH", data[q:q + 12])
            q += 12
            if nid != want_id:
                continue
            raw = data[base + so:base + so + ln]
            try:
                s = raw.decode("utf-16-be") if pid in (3, 0) else raw.decode("latin-1")
            except Exception:
                continue
            s = s.strip()
            if not s:
                continue
            if pid == 3 and lid == 1033:      # Windows 영어 — libass가 매칭하는 이름
                return s
            if pid == 3 and win_any is None:
                win_any = s
            elif other is None:
                other = s
        return win_any or other
    except Exception:
        return None


def fdir(base):
    d = Path(base) / "fonts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def family_name(path):
    """폰트 파일의 내부 family 이름(ASS Fontname으로 써야 libass가 찾음).
    ⚠️ libass는 nameID 1(전체 패밀리명, 예 'Paperlogy 7 Bold')로 매칭한다.
    PIL getname()은 nameID 16(타이포 패밀리, 예 'Paperlogy')을 반환해 스타일 단어가
    빠지면 libass가 못 찾아 기본폰트로 폴백된다 → nameID 1을 최우선으로 쓴다."""
    n1 = _sfnt_name(path, 1)          # 전체 패밀리명(스타일 포함) = libass 매칭 키
    if n1:
        return n1
    try:                             # 폴백: PIL(nameID 16)
        fam, _style = ImageFont.truetype(str(path), 20).getname()
        if (fam or "").strip():
            return fam.strip()
    except Exception:
        pass
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
