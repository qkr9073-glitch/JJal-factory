# -*- coding: utf-8 -*-
"""AI 표지 이미지 생성 (Gemini 이미지 모델, 2026-08-04 실측 검증).

원칙: 글자 없는 장면만 생성 — 텍스트는 렌더러(PIL)가 얹는다 (AI 오타 리스크 0).
모델은 config card_genimg_model(기본 gemini-3.1-flash-image), 실패 시 2.5로 폴백.
"""
import base64
import io
import json
import os
from pathlib import Path

import requests

GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
              "{model}:generateContent")

STYLE_HINTS = {
    "smag": ("Dramatic candid news photograph, moody natural lighting, "
             "photorealistic, dark atmosphere suitable for white bold text "
             "overlay at the bottom. Looks like a real photo someone snapped "
             "on a phone at the scene — slightly imperfect framing, natural "
             "skin texture and grain, NOT staged, NOT cinematic CG gloss"),
    "jmag": ("Bright candid editorial photograph, natural daylight, "
             "photorealistic, magazine product/news style. Looks like a real "
             "smartphone photo from the actual scene — believable everyday "
             "detail, natural textures, slightly imperfect, NOT staged, "
             "NOT AI-art gloss"),
}

EMOTION = ("The emotional read must be instant and STRONG — the situation and "
           "facial expressions should hit the viewer in the first half second, "
           "as punchy as a viral news photo.")

# 인물 기본값 안전망: 기획이 국적을 빼먹으면 일본인 느낌으로 (시청자=일본인,
# 한국 소재는 기획이 Korean을 명시하는 규칙 — 중국풍=최악, 사용자 확정)
PEOPLE = ("If the scene includes anonymous people and no nationality is specified, "
          "depict contemporary Japanese people with modern Japanese fashion, "
          "hairstyles and urban settings (as seen in Tokyo today). "
          "Do NOT render generic pan-Asian or Chinese-styled looks, "
          "clothing or interiors unless the scene explicitly requires them.")

NEGATIVE = ("Absolutely no text, no words, no letters, no numbers, no captions, "
            "no watermark, no subtitles anywhere in the image. "
            "This includes phone screens, computer screens, signs and posters — "
            "any visible screen must show only vague blurred shapes or abstract "
            "color blocks, never readable UI text or lists. "
            "Never draw speech bubbles, comment boxes, chat bubbles, message "
            "overlays or any UI element that would contain text — express "
            "reactions through facial expressions and body language only. "
            "No trademarked logos or brand marks. No recognizable copyrighted "
            "characters, no superhero costumes or emblems, no celebrity lookalikes — "
            "use generic places, objects and anonymous people only. "
            "No nudity or sexually explicit content (suggestive styling within "
            "platform guidelines is acceptable). "
            "No red circles, arrows, highlight rings or hand-drawn annotation "
            "marks on the image — annotations are added in post-processing.")


def add_callout(cfg, image_path, target, log=print):
    """레퍼런스 채널의 '빨간 원 강조 + 돋보기 줌' 장치 재현.
    비전으로 대상 bbox를 찾아 빨간 링을 두르고, 반대편 상단에 원형 확대 인셋을 붙인다.
    성공 시 True (이미지 덮어씀), 대상 못 찾거나 부적합하면 False."""
    from PIL import Image, ImageDraw
    key = (cfg.get("gemini_api_key") or "").strip() or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return False
    p = Path(image_path)
    raw = p.read_bytes()
    prompt = (f"Find the {target} in this image. Return JSON only: "
              '{"box_2d": [ymin, xmin, ymax, xmax]} with coordinates normalized '
              'to 0-1000. If it is not clearly present, return {"box_2d": null}.')
    r = requests.post(
        GEMINI_URL.format(model=cfg.get("gemini_model", "gemini-2.5-flash")),
        params={"key": key},
        json={"contents": [{"parts": [
                  {"text": prompt},
                  {"inline_data": {"mime_type": "image/jpeg",
                                   "data": base64.b64encode(raw).decode()}}]}],
              "generationConfig": {"response_mime_type": "application/json",
                                   "temperature": 0.1,
                                   "thinkingConfig": {"thinkingBudget": 0}}},
        timeout=90)
    if r.status_code != 200:
        return False
    try:
        box = json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"]).get("box_2d")
    except Exception:
        return False
    if not box or len(box) != 4:
        return False
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    W, H = img.size
    y0, x0, y1, x1 = (max(0.0, min(1.0, v / 1000)) for v in box)
    px0, py0, px1, py1 = x0 * W, y0 * H, x1 * W, y1 * H
    area = (px1 - px0) * (py1 - py0) / (W * H)
    if area > 0.45:
        return False    # 대상이 화면 절반 수준이면 원 강조가 어색 — 생략
    # 대상이 이미 잘 보이는 크기면 링만 — 링+돋보기 이중 원은 난잡(사용자 지적 2회).
    # 돋보기 인셋은 '진짜 작은 디테일'(면적 1.5% 미만·한 변이 화면 1/4 미만)일 때만.
    # 실사고: 손에 든 과자(면적 2%대·세로 30%)에 인셋이 붙어 이중 구조 재발 → 문턱 강화.
    use_inset = (area < 0.015
                 and (px1 - px0) < W * 0.25 and (py1 - py0) < H * 0.25)
    cx, cy = (px0 + px1) / 2, (py0 + py1) / 2
    clean = img.copy()          # 줌 인셋은 링을 그리기 '전' 원본에서 뜬다 (이중 원 방지)

    def _ring(canvas, bx, width):
        """안티앨리어싱 빨간 링 — 2배 캔버스에 그려 내려서 계단 현상 제거."""
        cw, ch = canvas.size
        ov = Image.new("RGBA", (cw * 2, ch * 2), (0, 0, 0, 0))
        ImageDraw.Draw(ov).ellipse([c * 2 for c in bx],
                                   outline=(232, 28, 28, 255), width=width * 2)
        ov = ov.resize((cw, ch), Image.LANCZOS)
        canvas.paste(ov, (0, 0), ov)

    rx = max((px1 - px0) / 2 * 1.35, W * 0.05)
    ry = max((py1 - py0) / 2 * 1.35, W * 0.05)
    _ring(img, [cx - rx, cy - ry, cx + rx, cy + ry], max(6, W // 160))
    if use_inset:
        # 원형 돋보기 인셋: 깨끗한 원본에서 대상 주변을 잘라 확대, 대상 반대편 상단 코너에
        s = max(px1 - px0, py1 - py0) * 1.5
        s = max(s, W * 0.14)
        l = max(0, min(W - s, cx - s / 2))
        t = max(0, min(H - s, cy - s / 2))
        D = int(W * 0.36)
        zoom = clean.crop((int(l), int(t), int(l + s), int(t + s))).resize(
            (D, D), Image.LANCZOS)
        mask = Image.new("L", (D * 2, D * 2), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, D * 2 - 1, D * 2 - 1], fill=255)
        mask = mask.resize((D, D), Image.LANCZOS)   # 인셋 가장자리도 안티앨리어싱
        ix = int(W * 0.05) if cx > W / 2 else int(W - D - W * 0.05)
        iy = int(H * 0.12) if cy > H * 0.5 else int(H * 0.45 - D)
        iy = max(int(H * 0.05), min(iy, int(H * 0.55) - D))
        img.paste(zoom, (ix, iy), mask)
        _ring(img, [ix, iy, ix + D, iy + D], max(7, W // 140))
    img.save(p, "JPEG", quality=92)
    return True


def generate_variation(cfg, base_image_path, scene, out_path, theme="smag", log=print):
    """기준 사진과 '같은 장면·같은 인물'의 다음 순간 컷 생성 — 캐러셀 장들이
    한 사건의 연속 사진 세트처럼 보이게 (레퍼런스 채널 방식)."""
    key = (cfg.get("gemini_api_key") or "").strip() or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("Gemini API 키가 없습니다")
    raw = Path(base_image_path).read_bytes()
    prompt = (f"Using the provided photo as the exact same scene, generate the next "
              f"moment of the same event: {scene}\n"
              f"Keep the identical location, lighting, photographic style and the same "
              f"people/subjects. Different camera angle or moment is good — but it must "
              f"look like another photo from the same news photo set.\n"
              f"{EMOTION}\n{PEOPLE}\n{NEGATIVE}")
    models = [cfg.get("card_genimg_model", "gemini-3.1-flash-image"),
              "gemini-2.5-flash-image"]
    last = ""
    for model in dict.fromkeys(models):
        try:
            r = requests.post(
                GEMINI_URL.format(model=model), params={"key": key},
                json={"contents": [{"parts": [
                          {"text": prompt},
                          {"inline_data": {"mime_type": "image/jpeg",
                                           "data": base64.b64encode(raw).decode()}}]}],
                      "generationConfig": {
                          "responseModalities": ["IMAGE"],
                          "imageConfig": {"aspectRatio": "3:4"}}},
                timeout=150)
            j = r.json()
            if "error" in j:
                last = f"{model}: {j['error'].get('message', '')[:120]}"
                continue
            parts = (j.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
            img = next((p for p in parts if "inlineData" in p), None)
            if not img:
                last = f"{model}: 응답에 이미지 없음"
                continue
            data = base64.b64decode(img["inlineData"]["data"])
            Path(out_path).write_bytes(data)
            log(f"      🎨 연속 컷 생성 ({model}, {len(data) // 1024}KB)")
            return str(out_path)
        except Exception as e:
            last = f"{model}: {str(e)[:120]}"
    raise RuntimeError(f"연속 컷 생성 실패 — {last}")


def generate_recreation(cfg, ref_image_path, scene, out_path, theme="smag", log=print):
    """원본 게시물 표지를 '참조'로 주고 같은 시각 장치를 새로 촬영한 컷 생성 —
    사물 은유·모양 개그처럼 글로는 재현이 안 되는 표지용 (리메이크 기본 경로).
    원본 복제가 아니라 같은 구도·같은 개그의 새 사진을 만든다."""
    key = (cfg.get("gemini_api_key") or "").strip() or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("Gemini API 키가 없습니다")
    raw = Path(ref_image_path).read_bytes()
    prompt = (f"The provided image is a REFERENCE for composition and visual idea. "
              f"Create a completely NEW photograph that reproduces the same visual "
              f"device: the same camera angle, framing, arrangement, lighting mood "
              f"and — most importantly — the same visual joke or point the reference "
              f"makes. Scene description: {scene}\n"
              f"It must read as a fresh photo of different but equivalent subjects — "
              f"do not copy the reference pixel-for-pixel, and ignore any text or "
              f"graphic overlays in the reference (make a clean photo only).\n"
              f"Style: {STYLE_HINTS.get(theme, STYLE_HINTS['smag'])}.\n"
              f"{EMOTION}\n{PEOPLE}\n{NEGATIVE}")
    models = [cfg.get("card_genimg_model", "gemini-3.1-flash-image"),
              "gemini-2.5-flash-image"]
    last = ""
    for model in dict.fromkeys(models):
        try:
            r = requests.post(
                GEMINI_URL.format(model=model), params={"key": key},
                json={"contents": [{"parts": [
                          {"text": prompt},
                          {"inline_data": {"mime_type": "image/jpeg",
                                           "data": base64.b64encode(raw).decode()}}]}],
                      "generationConfig": {
                          "responseModalities": ["IMAGE"],
                          "imageConfig": {"aspectRatio": "3:4"}}},
                timeout=150)
            j = r.json()
            if "error" in j:
                last = f"{model}: {j['error'].get('message', '')[:120]}"
                continue
            parts = (j.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
            img = next((p for p in parts if "inlineData" in p), None)
            if not img:
                last = f"{model}: 응답에 이미지 없음"
                continue
            data = base64.b64decode(img["inlineData"]["data"])
            Path(out_path).write_bytes(data)
            log(f"      🎨 원본 참조 재현 컷 생성 ({model}, {len(data) // 1024}KB)")
            return str(out_path)
        except Exception as e:
            last = f"{model}: {str(e)[:120]}"
    raise RuntimeError(f"원본 참조 재현 실패 — {last}")


def generate_cover(cfg, scene, out_path, theme="smag", log=print):
    """scene(장면 묘사, 한국어 OK) → out_path에 JPEG 저장. 실패 시 예외."""
    key = (cfg.get("gemini_api_key") or "").strip() or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("Gemini API 키가 없습니다")
    prompt = (f"Vertical 3:4 cover image for a social media card news post.\n"
              f"Scene: {scene}\n"
              f"Style: {STYLE_HINTS.get(theme, STYLE_HINTS['smag'])}.\n"
              f"{EMOTION}\n{PEOPLE}\n{NEGATIVE}")
    models = [cfg.get("card_genimg_model", "gemini-3.1-flash-image"),
              "gemini-2.5-flash-image"]
    last = ""
    for model in dict.fromkeys(models):
        try:
            r = requests.post(
                GEMINI_URL.format(model=model), params={"key": key},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {
                          "responseModalities": ["IMAGE"],
                          "imageConfig": {"aspectRatio": "3:4"}}},
                timeout=150)
            j = r.json()
            if "error" in j:
                last = f"{model}: {j['error'].get('message', '')[:120]}"
                continue
            parts = (j.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
            img = next((p for p in parts if "inlineData" in p), None)
            if not img:
                last = f"{model}: 응답에 이미지 없음"
                continue
            data = base64.b64decode(img["inlineData"]["data"])
            Path(out_path).write_bytes(data)
            log(f"      🎨 AI 표지 생성 완료 ({model}, {len(data) // 1024}KB)")
            return str(out_path)
        except Exception as e:
            last = f"{model}: {str(e)[:120]}"
    raise RuntimeError(f"AI 표지 생성 실패 — {last}")
