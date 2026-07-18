# -*- coding: utf-8 -*-
"""짤에 박힌 글씨(자막·제목·워터마크) 제거

모드
  none : 아무것도 안 함 (기본)
  bar  : 글씨가 있는 쪽(상단/하단)을 불투명 바로 덮음 — 0원
  ai   : Gemini 2.5 Flash Image 로 글씨만 지우고 배경 복원 — 장당 약 55원

비용 절감: 먼저 무료 비전(2.5 Flash)으로 '박힌 글씨 유무'를 판정하고,
있는 이미지에 대해서만 처리한다. 실패하면 원본을 그대로 쓴다(작업 중단 없음).
"""
import base64
import io
import json
import os
import re
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageStat

GEMINI = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
IMG_MODEL = "gemini-2.5-flash-image"
COST_PER_IMAGE_KRW = 55  # $0.039 × 환율 ≒ 55원 (출력 1290토큰)

# ⚠️ 프롬프트에 "people/person" 을 넣으면 인물 안전필터에 차단된다(실측).
#    텍스트 제거만 담백하게 지시할 것.
EDIT_PROMPTS = [
    "Remove ALL overlaid text from this image: subtitles, captions, titles, and any "
    "semi-transparent watermark or logo text (including a faint watermark in the center). "
    "Naturally reconstruct the background where the text was. Keep everything else the same. "
    "Output the edited image.",
    "Erase every letter, caption, subtitle, and watermark overlay in this image, including "
    "translucent center watermarks and corner logos, filling those areas so they blend "
    "seamlessly with the surrounding image. Output the edited image only.",
]

DETECT_PROMPT = (
    '이 이미지에 나중에 덧입힌 글씨(자막/제목/워터마크/로고텍스트)가 있는지 판정하라. '
    '사진 안에 원래 찍힌 간판·표지판 글자는 제외한다.\n'
    '반드시 JSON만: {"text": true|false, "where": "bottom"|"top"|"middle"|"none"}'
)


def _key(cfg):
    return (cfg.get("gemini_api_key") or "").strip() or os.environ.get("GEMINI_API_KEY", "")


def _b64(path):
    return base64.b64encode(Path(path).read_bytes()).decode()


def _inline(path, max_side=1024):
    img = Image.open(path).convert("RGB")
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return {"inline_data": {"mime_type": "image/jpeg",
                            "data": base64.b64encode(buf.getvalue()).decode()}}


def detect_text(cfg, path):
    """박힌 글씨 유무 + 위치. 실패하면 {'text': False} (=비용 안 씀)"""
    key = _key(cfg)
    if not key:
        return {"text": False, "where": "none"}
    body = {"contents": [{"role": "user", "parts": [
        {"text": DETECT_PROMPT}, _inline(path)]}],
        "generationConfig": {"response_mime_type": "application/json",
                             "temperature": 0.1,
                             "thinkingConfig": {"thinkingBudget": 0}}}
    try:
        r = requests.post(GEMINI.format(m=cfg.get("gemini_model", "gemini-2.5-flash")),
                          params={"key": key}, json=body, timeout=60)
        cand = r.json()["candidates"][0]
        raw = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
        d = json.loads(raw)
        return {"text": bool(d.get("text")), "where": str(d.get("where") or "none")}
    except Exception:
        return {"text": False, "where": "none"}


def remove_text_ai(cfg, path):
    """Gemini 이미지편집으로 글씨 제거 → 원본 크기로 덮어쓰기. 실패 시 예외."""
    key = _key(cfg)
    if not key:
        raise RuntimeError("Gemini API 키 없음")
    orig = Image.open(path).convert("RGB")
    o_stat = ImageStat.Stat(orig.resize((48, 48)))
    o_var = sum(o_stat.stddev) / 3          # 원본의 대비(분산)
    last = ""
    for prompt in EDIT_PROMPTS:
        for _ in range(2):
            body = {"contents": [{"role": "user", "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg", "data": _b64(path)}}]}],
                "generationConfig": {"responseModalities": ["IMAGE"]}}
            r = requests.post(GEMINI.format(m=IMG_MODEL), params={"key": key},
                              json=body, timeout=180)
            if r.status_code != 200:
                last = f"HTTP {r.status_code}"
                continue
            cand = (r.json().get("candidates") or [{}])[0]
            if "content" not in cand:  # 안전필터 등으로 차단
                last = f"차단(finishReason={cand.get('finishReason')})"
                continue
            for p in cand["content"].get("parts", []):
                d = p.get("inline_data") or p.get("inlineData")
                if d:
                    img = Image.open(io.BytesIO(base64.b64decode(d["data"]))).convert("RGB")
                    # ⚠️ AI가 과도하게 지워 '거의 백지/단색'을 뱉는 경우가 있음(사람 사진서 잦음)
                    #    → 그대로 저장하면 카드가 하얗게 됨. 백지스럽거나 대비가 급감하면 거부.
                    s = ImageStat.Stat(img.resize((48, 48)))
                    var = sum(s.stddev) / 3
                    mean = sum(s.mean) / 3
                    if var < 10 or (var < o_var * 0.4 and mean > 232):
                        last = f"결과가 백지에 가까움(과도삭제, var={var:.0f}) — 원본 유지"
                        continue
                    img.resize(orig.size, Image.LANCZOS).save(path, "JPEG", quality=93)
                    return path
            last = "이미지 파트 없음"
    raise RuntimeError(last or "알 수 없는 실패")


def cover_bar(path, where="bottom", ratio=0.30, color=(16, 18, 24)):
    """글씨가 있는 쪽을 불투명 바로 덮음 (0원). middle이면 처리 불가 → False 반환."""
    if where not in ("bottom", "top"):
        return False
    img = Image.open(path).convert("RGB")
    w, h = img.size
    bh = int(h * ratio)
    d = ImageDraw.Draw(img)
    if where == "bottom":
        d.rectangle([0, h - bh, w, h], fill=color)
    else:
        d.rectangle([0, 0, w, bh], fill=color)
    img.save(path, "JPEG", quality=93)
    return True


def clean_images(cfg, paths, mode="none", log=print):
    """이미지 목록에서 박힌 글씨 제거. (처리된 경로 목록, AI과금장수) 반환.
    paths 는 임시 작업폴더 파일이라 제자리(in-place) 수정한다."""
    if mode not in ("bar", "ai"):
        return list(paths), 0
    paid, done, skipped = 0, 0, 0
    for p in paths:
        info = detect_text(cfg, p)
        if not info["text"]:
            skipped += 1
            continue
        try:
            if mode == "ai":
                remove_text_ai(cfg, p)
                paid += 1
                done += 1
            else:
                if cover_bar(p, info["where"]):
                    done += 1
                else:
                    log(f"      ↳ {Path(p).name}: 글씨가 가운데라 바로는 못 가려요 "
                        f"(AI 제거 권장) — 원본 유지")
        except Exception as e:
            log(f"      ↳ {Path(p).name}: 글씨 제거 실패 — 원본 사용 ({e})")
    log(f"      글씨 제거: {done}장 처리 · {skipped}장 글씨없음"
        + (f" · AI {paid}장 (약 {paid * COST_PER_IMAGE_KRW}원)" if paid else " · 비용 0원"))
    return list(paths), paid
