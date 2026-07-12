# -*- coding: utf-8 -*-
"""자료서랍 — 후기/수익지표 캡처를 카드뉴스 증빙 카드로 자동 활용.
폴더에 이미지를 넣어두면 Gemini(비전)가 내용을 읽어 index.json에 색인하고,
카드 생성 시 주제에 맞는 캡처를 골라 후킹 문구와 함께 반환한다."""
import base64
import io
import json
from pathlib import Path

from PIL import Image

from . import brain

IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
KINDS = ("수익지표", "수강생후기", "스토리", "기타")

DESCRIBE_PROMPT = """이 이미지는 유튜브 쇼츠 수익화 강사의 증빙 자료(수익 캡처/수강생 후기 등)다.
이미지를 보고 아래 JSON만 출력하라.
{
  "kind": "수익지표|수강생후기|스토리|기타 중 하나",
  "headline": "카드뉴스 후킹에 쓸 한 줄 (예: 57세에 시작해 3개월 만에 첫 수익)",
  "desc": "이미지 내용 1~2문장",
  "numbers": "이미지 속 핵심 수치 그대로 (예: 월 2,000만 원 / 조회수 1,200만). 없으면 빈 문자열",
  "person": "본인|수강생 이름/호칭|빈 문자열"
}
규칙: 이미지에 실제로 보이는 내용만. 안 보이는 수치를 지어내지 마라. 전부 한국어."""

PICK_PROMPT = """너는 정보형 인스타 카드뉴스의 에디터다.
아래 '증빙 자료 목록'에서 주제와 가장 잘 어울리는 자료를 {k}개 이내로 골라,
각 자료에 얹을 후킹 문구를 써라.

## 주제
{topic}

## 증빙 자료 목록
{catalog}

## 출력 (반드시 이 JSON 형식만)
{{
  "picks": [
    {{"file": "파일명 그대로",
      "hook": "캡처 위에 크게 얹을 후킹 한 줄 (10~20자, 수치 살리기)",
      "sub": "캡처 아래 붙일 설명 한 줄 (15~35자, 신뢰 강화)"}}
  ]
}}

## 규칙
- 주제와 정말 어울리는 것만. 어울리는 게 없으면 picks를 빈 배열로.
- hook/sub는 자료의 headline·numbers에 있는 사실만 사용 (지어내기 금지).
- 같은 인물/같은 종류만 {k}개 겹치지 않게, 종류(수익지표/후기)를 섞어라."""


def drawer_dir(cfg, base_dir):
    return Path(cfg.get("drawer_dir") or (Path(base_dir) / "자료서랍"))


def load_index(drawer):
    try:
        return json.loads((drawer / "index.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_index(drawer, index):
    (drawer / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def _img_part(path, max_side=1280):
    """이미지를 축소 JPEG로 인코딩해 Gemini inline_data 파트로 변환"""
    img = Image.open(path)
    img = img.convert("RGB")
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=82)
    return {"inline_data": {"mime_type": "image/jpeg",
                            "data": base64.b64encode(buf.getvalue()).decode()}}


def refresh_index(cfg, base_dir, log=print, mock=False):
    """새로 들어온 이미지를 Gemini 비전으로 색인, 사라진 파일은 정리.
    반환: 최신 index dict"""
    drawer = drawer_dir(cfg, base_dir)
    if not drawer.exists():
        return {}
    index = load_index(drawer)
    files = {p.name: p for p in drawer.iterdir()
             if p.suffix.lower() in IMG_EXTS and not p.name.startswith("_")}

    removed = [k for k in index if k not in files]
    for k in removed:
        index.pop(k)

    new_files = [n for n in sorted(files) if n not in index]
    for i, name in enumerate(new_files, 1):
        if mock:
            index[name] = {"kind": "수익지표", "headline": f"모의 색인 — {name}",
                           "desc": "모의 모드", "numbers": "", "person": ""}
            continue
        try:
            log(f"      자료서랍 색인 {i}/{len(new_files)} — {name}")
            info = brain._call_parts(
                cfg, [{"text": DESCRIBE_PROMPT}, _img_part(files[name])],
                max_tokens=2048, temperature=0.3, thinking=0)
            index[name] = {
                "kind": info.get("kind") if info.get("kind") in KINDS else "기타",
                "headline": str(info.get("headline", "")).strip(),
                "desc": str(info.get("desc", "")).strip(),
                "numbers": str(info.get("numbers", "")).strip(),
                "person": str(info.get("person", "")).strip(),
            }
        except Exception as e:
            log(f"      색인 실패(건너뜀) {name}: {str(e)[:80]}")
    if removed or new_files:
        save_index(drawer, index)
    return index


def pick(cfg, base_dir, topic, k=2, log=print, mock=False):
    """주제에 맞는 증빙 자료 k개 + 후킹 문구 선택.
    반환: [{file(절대경로), name, hook, sub, kind, headline, numbers}]"""
    drawer = drawer_dir(cfg, base_dir)
    index = refresh_index(cfg, base_dir, log=log, mock=mock)
    if not index:
        return []

    if mock:
        names = list(index)[:k]
        return [{"file": str(drawer / n), "name": n, "hook": "모의 후킹 문구",
                 "sub": "모의 설명 한 줄", **{x: index[n].get(x, "")
                 for x in ("kind", "headline", "numbers")}} for n in names]

    catalog = "\n".join(
        f"- {n} | {v.get('kind','')} | {v.get('headline','')}"
        f"{' | ' + v['numbers'] if v.get('numbers') else ''}"
        f"{' | ' + v['person'] if v.get('person') else ''}"
        for n, v in index.items())
    try:
        result = brain._call(cfg, PICK_PROMPT.format(topic=topic, k=k,
                                                     catalog=catalog),
                             max_tokens=2048, temperature=0.6, thinking=0)
        picks = result.get("picks", [])
    except Exception as e:
        log(f"      증빙 자료 선택 실패(증빙 없이 진행): {str(e)[:80]}")
        return []

    out = []
    for p in picks[:k]:
        name = str(p.get("file", "")).strip()
        if name not in index or not (drawer / name).exists():
            continue
        out.append({"file": str(drawer / name), "name": name,
                    "hook": str(p.get("hook", "")).strip() or index[name].get("headline", ""),
                    "sub": str(p.get("sub", "")).strip(),
                    "kind": index[name].get("kind", ""),
                    "headline": index[name].get("headline", ""),
                    "numbers": index[name].get("numbers", "")})
    return out
