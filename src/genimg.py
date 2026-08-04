# -*- coding: utf-8 -*-
"""AI 표지 이미지 생성 (Gemini 이미지 모델, 2026-08-04 실측 검증).

원칙: 글자 없는 장면만 생성 — 텍스트는 렌더러(PIL)가 얹는다 (AI 오타 리스크 0).
모델은 config card_genimg_model(기본 gemini-3.1-flash-image), 실패 시 2.5로 폴백.
"""
import base64
import json
import os
from pathlib import Path

import requests

GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
              "{model}:generateContent")

STYLE_HINTS = {
    "smag": ("Dramatic editorial news photograph, moody cinematic lighting, "
             "photorealistic, high detail, dark atmosphere suitable for white "
             "bold text overlay at the bottom"),
    "jmag": ("Bright clean editorial photograph, natural daylight, "
             "photorealistic, magazine product/news style, crisp detail"),
}

NEGATIVE = ("Absolutely no text, no words, no letters, no numbers, no captions, "
            "no watermark, no logo, no subtitles anywhere in the image.")


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
              f"look like another photo from the same news photo set.\n{NEGATIVE}")
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


def generate_cover(cfg, scene, out_path, theme="smag", log=print):
    """scene(장면 묘사, 한국어 OK) → out_path에 JPEG 저장. 실패 시 예외."""
    key = (cfg.get("gemini_api_key") or "").strip() or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("Gemini API 키가 없습니다")
    prompt = (f"Vertical 3:4 cover image for a social media card news post.\n"
              f"Scene: {scene}\n"
              f"Style: {STYLE_HINTS.get(theme, STYLE_HINTS['smag'])}.\n"
              f"{NEGATIVE}")
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
