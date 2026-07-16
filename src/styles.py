# -*- coding: utf-8 -*-
"""스타일 프리셋 — 참고 캐러셀 스크린샷을 Gemini 비전으로 분석해
카드뉴스 생성기(context)에 주입할 '스타일 지침'으로 저장/관리한다.
인스타 크롤 없이(계정 안전) 레퍼런스를 늘려 생성 방식을 다양화하는 용도."""
import base64
import colorsys
import io
import json
import os
import re
import threading
import uuid
from pathlib import Path

import requests
from PIL import Image

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_LOCK = threading.Lock()
MAX_KEEP = 40

ANALYZE_PROMPT = """당신은 인스타그램 카드뉴스/캐러셀 콘텐츠 분석가다.
첨부된 참고 캐러셀(또는 게시물) 스크린샷을 분석해서, 이 스타일을 우리 카드뉴스
생성기가 참고할 수 있는 '콘텐츠 지침'으로 정리하라.

중요: 우리 생성기는 색상·폰트 테마가 이미 고정돼 있다. 그러니 색/폰트 자체보다
**콘텐츠 전략과 구성 방식**에 집중하라. 특정 계정을 베끼는 게 아니라 톤·구성만 참고한다.

분석 관점:
- 말투/톤앤매너 (반말·존댓말, 유머·진지·자극·공감 등)
- 표지(첫 장) 후킹 방식 (숫자, 질문, 도발, 공감, 반전 등)
- 본문 카드 전개 구조 (리스트형, 스토리형, 비교형, 문답형, 단계형 등)
- 카드 1장당 정보량 (한 문장 임팩트형 vs 촘촘한 설명형)
- 마무리/CTA 방식
- 전반적 무드 (참고용)

JSON만 출력:
{
  "name": "이 스타일을 부를 이름 (한글 10자 이내, 예: '도발형 리스트', '따뜻한 공감')",
  "summary": "한 줄 요약 (40자 이내)",
  "tone": "말투/톤 한 줄",
  "cover": "표지 후킹 방식 한 줄",
  "flow": "본문 전개 구조 한 줄",
  "density": "카드당 정보량 한 줄",
  "cta": "마무리/CTA 방식 한 줄",
  "guide": "카드뉴스 생성 프롬프트에 그대로 주입할 지침 문단. '[참고 스타일] 이번 카드뉴스는 다음 톤·구성을 참고해서 작성하라:' 로 시작하고 위 요소를 3~7개 불릿(- )으로. 내용/주제는 우리 것을 쓰되 스타일만 참고하라는 점을 명시."
}"""


def _inline(image_bytes, max_side=1024):
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=82)
    return {"inline_data": {"mime_type": "image/jpeg",
                            "data": base64.b64encode(buf.getvalue()).decode()}}


def _parse_json(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(text)


def extract_visual(image_bytes):
    """레퍼런스에서 (테마 hunter/cream, 포인트색 hex) 추출 — 디자인 반영용."""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        img.thumbnail((110, 140))
        px = list(img.getdata())
        if not px:
            return "", ""
        avg = sum(sum(p) for p in px) / (len(px) * 3)
        theme = "cream" if avg >= 140 else "hunter"  # 밝으면 라이트, 어두우면 다크
        # 포인트색: 채도 높은 픽셀을 색상(hue)별로 모아 최다 그룹의 대표색
        buckets = {}
        for r, g, b in px:
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            if s > 0.4 and 0.2 < v < 0.98:
                hb = round(h * 12)
                buckets.setdefault(hb, []).append((r, g, b))
        if not buckets:
            return theme, ""
        best = max(buckets.values(), key=len)
        n = len(best)
        rgb = (sum(p[0] for p in best) // n, sum(p[1] for p in best) // n,
               sum(p[2] for p in best) // n)
        # 렌더에서 잘 보이게 채도·명도 보정
        h, s, v = colorsys.rgb_to_hsv(*[c / 255 for c in rgb])
        s = max(s, 0.5)
        v = min(max(v, 0.45), 0.8)
        rr, gg, bb = colorsys.hsv_to_rgb(h, s, v)
        return theme, "#%02X%02X%02X" % (int(rr * 255), int(gg * 255), int(bb * 255))
    except Exception:
        return "", ""


def analyze_reference(image_bytes_list, cfg):
    """참고 이미지(1~여러 장) → 스타일 프리셋 dict. id/created는 호출측에서 부여."""
    key = (cfg.get("gemini_api_key") or "").strip() or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("Gemini API 키가 없습니다")
    if not image_bytes_list:
        raise RuntimeError("분석할 이미지가 없습니다")
    parts = [{"text": ANALYZE_PROMPT}]
    for b in image_bytes_list[:4]:
        parts.append(_inline(b))
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.4,
            "maxOutputTokens": 2048,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    resp = requests.post(GEMINI_URL.format(model=model),
                         params={"key": key}, json=body, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini 오류 {resp.status_code}: {resp.text[:200]}")
    raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    d = _parse_json(raw)
    name = (d.get("name") or "새 스타일").strip()[:20]
    guide = (d.get("guide") or "").strip()
    if not guide:
        raise RuntimeError("스타일 지침을 뽑지 못했어요 — 더 선명한 캡처로 다시 시도")
    # 스타일 = '내용 구성'만. 색/테마(비주얼)는 템플릿이 담당 → theme/accent 미포함.
    return {
        "name": name,
        "summary": (d.get("summary") or "").strip()[:60],
        "tone": (d.get("tone") or "").strip()[:80],
        "cover": (d.get("cover") or "").strip()[:80],
        "flow": (d.get("flow") or "").strip()[:80],
        "density": (d.get("density") or "").strip()[:80],
        "cta": (d.get("cta") or "").strip()[:80],
        "guide": guide[:1500],
        "kind": "style",
    }


def analyze_template(image_bytes_list):
    """참고 이미지 → '템플릿'(이미지/비주얼) 프리셋. 색·무드만 뽑는다(내용 X, Gemini 불필요).
    렌더 테마(밝기)+포인트색을 추출해 커스텀 템플릿으로 저장."""
    if not image_bytes_list:
        raise RuntimeError("분석할 이미지가 없습니다")
    theme, accent = extract_visual(image_bytes_list[0])
    theme = theme or "hunter"
    mood = "라이트" if theme == "cream" else "다크"
    name = (mood + " 커스텀") if not accent else f"{mood}·{accent}"
    return {"name": name[:20], "theme": theme, "accent": accent,
            "summary": f"참고 이미지 기반 {mood} 템플릿" + (f" · 포인트 {accent}" if accent else "")}


def _path(base):
    return Path(base) / "styles.json"


def _tpath(base):
    return Path(base) / "templates.json"


def load_templates(base):
    try:
        return json.loads(_tpath(base).read_text(encoding="utf-8"))
    except Exception:
        return []


def save_template(base, tpl, thumb_b64=None):
    with _LOCK:
        items = load_templates(base)
        tpl = dict(tpl)
        tpl["id"] = uuid.uuid4().hex[:8]
        if thumb_b64:
            tpl["thumb"] = thumb_b64
        items.insert(0, tpl)
        items = items[:MAX_KEEP]
        _tpath(base).write_text(json.dumps(items, ensure_ascii=False, indent=2),
                                encoding="utf-8")
    return tpl


def delete_template(base, tid):
    with _LOCK:
        items = [t for t in load_templates(base) if t.get("id") != tid]
        _tpath(base).write_text(json.dumps(items, ensure_ascii=False, indent=2),
                                encoding="utf-8")
    return True


def load_styles(base):
    try:
        return json.loads(_path(base).read_text(encoding="utf-8"))
    except Exception:
        return []


def save_style(base, preset, thumb_b64=None):
    """프리셋 저장 (id/created 부여). 최신 MAX_KEEP개만 유지."""
    with _LOCK:
        items = load_styles(base)
        preset = dict(preset)
        preset["id"] = uuid.uuid4().hex[:8]
        if thumb_b64:
            preset["thumb"] = thumb_b64  # data URL (미리보기용, 선택)
        items.insert(0, preset)
        items = items[:MAX_KEEP]
        _path(base).write_text(json.dumps(items, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    return preset


def delete_style(base, sid):
    with _LOCK:
        items = [s for s in load_styles(base) if s.get("id") != sid]
        _path(base).write_text(json.dumps(items, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    return True


def make_thumb(image_bytes, side=200):
    """프리셋 카드에 보여줄 작은 썸네일 data URL."""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        img.thumbnail((side, side))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=70)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None
