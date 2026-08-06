# -*- coding: utf-8 -*-
"""릴스 직접 생성기 — 주제 하나 → '표 한 장' 릴스 완성팩.

벤치마크(셀렉션군) 릴스는 카드뉴스의 부산물이 아니라 독립 콘텐츠다(사용자 확정):
주제 → 초구체 항목(+실측 값) 표 프레임 + 장문 캡션(푸터가 캡션으로 유도).
내용 기준 = 실측 히트작의 구체성·자극(뻔한 일반론 금지), 타겟 = 일본인 당사자화.
"""
import json
import re
from datetime import datetime
from pathlib import Path

import requests

from .reference import GEMINI_URL, _parse_json
from .reelscout import _gem_text
from .cardreel import mp4_from_frame

REEL_CONTENT_PROMPT = """너는 일본 타겟 인스타 릴스 채널(気になるマガジン)의 기획자 겸 작가다.
벤치마크 채널(한국 셀렉션 네트워크)의 실측 히트 공식대로 '표 한 장' 릴스 콘텐츠를 써라.

[실측 히트 예시 — 이 수준의 구체성·자극이 기준]
예1(♥2607): 후킹="난 아직도 못했는데 벌써 했다고?" / 제목=국가별 평균 첫 성관계 나이 10
  → 항목=국가명, 값=실제 조사 나이(15.6세…22.1세 실측치)
예2(♥917): 후킹='무식하다 욕먹는' / 제목=무례한 행동 TOP10
  → 항목="둘이 차 탈 때 뒷좌석에 앉는 것" 같은 초구체 행동(값 없음)

[주제] {topic}

[규칙]
- 시청자=일본인. 일본 생활 디테일(電車·コンビニ·LINE·職場·飲み会 등)로 당사자화.
  한국 내수 전제(한국 유명인·한국식 제도)는 쓰지 마라.
- entries {n}개: 예2처럼 한 줄에 장면이 그려지는 초구체 상황/항목.
  뻔한 일반론 금지(「マナーが悪い」❌ → 「降りる人を待たずに乗り込む」⭕). 중복 금지.
- 값(v): 주제가 실제 통계·수치 기반일 때만, 널리 알려진 실측치만.
  조금이라도 불확실하면 전부 빈 문자열(수치 날조 절대 금지).
- title_top: 시청자가 뜨끔할 인용/수식 후킹 한 줄(8~14자, 일본어).
- title_main: 주제+TOP{n} 형태(10~18자, 일본어).
- caption: 푸터 '詳しくはキャプションで'를 받는 장문 본문 — 항목들의 이유·배경 해설,
  마지막에 「あなたはいくつ当てはまった？」류 참여 질문. 400~700자, 읽기 쉬운 정중한 일본어.
- caption_ko: caption의 한국어 해석(운영자 검수용). entries_ko: 각 항목의 한국어 해석.
- template: "grid"=연애·심리·특징·사인처럼 각 항목이 '장면'인 주제(칸마다 일러스트) /
  "table"=순위·통계·비교·매너처럼 표가 어울리는 주제.
- 각 entry의 ill: 그 항목을 그린 일러스트 장면 묘사(한국어 1문장, 글자·문자 없는 장면,
  남녀 인물은 일본인 느낌). template=table이면 전부 빈 문자열.
- scene: 표 뒤에 은은하게 깔 배경 일러스트 장면 묘사 1문장(한국어, 글자 없는 장면).
- bgm: 미스터리|감동|락|무난 중 주제 무드에 맞는 것.
- risk: 초상권·차별·성인 수위 등 주의점(없으면 빈 문자열).

JSON만 출력:
{{"template": "table", "title_top": "", "title_main": "",
  "entries": [{{"t": "", "v": "", "ill": ""}}],
  "entries_ko": [""], "caption": "", "caption_ko": "", "scene": "",
  "bgm": "무난", "risk": ""}}"""


def generate_reel(cfg, base, topic, n=10, account="kininaru_mag", lang="ja",
                  sec=9.0, gen_image=True, log=print):
    """주제 → 릴스 완성팩(프레임+mp4+캡션+검수 리포트). 반환: {pack, video, ...}"""
    key = (cfg.get("gemini_api_key") or "").strip()
    if not key:
        raise RuntimeError("Gemini API 키가 없습니다")
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    body = {"contents": [{"role": "user", "parts": [
                {"text": REEL_CONTENT_PROMPT.format(topic=topic, n=n)}]}],
            "generationConfig": {"response_mime_type": "application/json",
                                 "temperature": 0.75,
                                 "maxOutputTokens": 4096,
                                 "thinkingConfig": {"thinkingBudget": 0}}}
    resp = requests.post(GEMINI_URL.format(model=model), params={"key": key},
                         json=body, timeout=180)
    if resp.status_code != 200:
        raise RuntimeError(f"릴스 콘텐츠 생성 실패 {resp.status_code}")
    data = _parse_json(_gem_text(resp)) or {}
    entries = []
    for e in (data.get("entries") or [])[:n]:
        t = re.sub(r"\s+", " ", str((e or {}).get("t", ""))).strip()
        if t:
            entries.append({"t": t, "v": str((e or {}).get("v", "")).strip(),
                            "ill": str((e or {}).get("ill", "")).strip()})
    if len(entries) < 5:
        raise RuntimeError(f"항목이 부족합니다({len(entries)}개) — 주제를 바꿔보세요")
    template = "grid" if str(data.get("template")) == "grid" else "table"
    log(f"      ✍ 콘텐츠[{template}]: {data.get('title_main')} — 항목 {len(entries)}개"
        + (f" ⚠️{data.get('risk')}" if data.get("risk") else ""))

    root = Path(base) / cfg.get("output_dir", "결과물")
    slug = re.sub(r"[^\w가-힣ぁ-ヺ一-龯]+", "_", str(topic))[:24].strip("_")
    pack = root / f"{datetime.now():%Y%m%d_%H%M}_reel_{slug}_JP"
    i = 1
    while pack.exists():
        i += 1
        pack = root / f"{datetime.now():%Y%m%d_%H%M}_reel_{slug}_JP_{i}"
    pack.mkdir(parents=True)

    cover = ""
    if gen_image and template == "table" and data.get("scene"):
        try:
            from . import genimg
            cp = pack / "_bg.jpg"
            genimg.generate_cover(cfg, str(data["scene"]), cp, theme="smag",
                                  log=log)
            cover = str(cp)
        except Exception as e:
            log(f"      (배경 일러스트 생략: {str(e)[:60]})")
    rows = [{"num": i + 1, "title": e["t"], "value": e["v"]}
            for i, e in enumerate(entries)]
    if gen_image and template == "grid":
        from . import genimg
        made = 0
        for i, e in enumerate(entries):
            if not e.get("ill"):
                continue
            try:
                cp = pack / f"cell{i:02d}.jpg"
                genimg.generate_cover(
                    cfg, "어두운 배경의 네온 퍼플·핑크 톤 애니메이션 일러스트, "
                         "심플한 구도: " + e["ill"], cp, theme="smag", log=lambda m: None)
                rows[i]["image"] = str(cp)
                made += 1
            except Exception as ex:
                log(f"      (칸 {i + 1} 일러스트 생략: {str(ex)[:50]})")
        log(f"      🎨 칸 일러스트 {made}/{len(entries)}장")

    from cardnews import render as _render
    cfg2 = dict(cfg)
    cfg2.update(card_lang=lang, _reel_handle=account,
                _reel_brand="気になるマガジン" if lang == "ja" else "")
    if cover:
        cfg2["cover_image"] = cover
    plan = {"title_top": data.get("title_top", ""),
            "title_main": data.get("title_main", "")}
    frame = pack / "reel.jpg"
    if template == "grid":
        _render.render_reel_grid(plan, rows, cfg2, frame)
    else:
        _render.render_reel_frame(plan, rows, cfg2, frame)
    import shutil
    shutil.copy2(frame, pack / "01.jpg")     # 발행 커버·UI 썸네일용

    cap = str(data.get("caption", "")).strip()
    (pack / "caption.txt").write_text(cap, encoding="utf-8")
    mood = str(data.get("bgm") or "무난")
    if mood not in ("미스터리", "감동", "락", "무난"):
        mood = "무난"
    video = mp4_from_frame(base, pack, frame, sec=sec, bgm_code=mood, log=log)

    title = f"{data.get('title_top', '')} {data.get('title_main', '')}".strip()
    meta = {"type": "cardnews", "template": "reel", "reel_template": template,
            "source": "reelgen",
            "title": title, "topic": topic, "lang": lang,
            "ig_account": account, "video": "video.mp4",
            "reel_entries": entries, "bgm": mood,
            "entries_ko": [str(x) for x in (data.get("entries_ko") or [])],
            "caption_ko": str(data.get("caption_ko", "")),
            "risk": str(data.get("risk", "")),
            "created": datetime.now().isoformat(timespec="seconds")}
    (pack / "meta.json").write_text(json.dumps(meta, ensure_ascii=False,
                                               indent=2), encoding="utf-8")
    ek = [str(x) for x in (data.get("entries_ko") or [])]
    rows_html = "".join(
        f"<tr><td>{i + 1}</td><td>{e['t']}</td><td>{e['v']}</td>"
        f"<td class='ko'>{ek[i] if i < len(ek) else ''}</td></tr>"
        for i, e in enumerate(entries))
    (pack / "review.html").write_text(f"""<!doctype html><meta charset="utf-8">
<title>{title}</title>
<style>body{{font-family:sans-serif;max-width:760px;margin:24px auto;padding:0 12px}}
img{{width:100%;border-radius:12px}}table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #ccc;padding:6px 10px}}td.ko{{color:#777}}
pre{{white-space:pre-wrap;background:#f6f6f8;padding:14px;border-radius:10px}}</style>
<h2>🎞 {title}</h2>
<p>업로드 계정: @{account} · BGM: {mood}{' · ⚠️' + meta['risk'] if meta['risk'] else ''}</p>
<img src="reel.jpg">
<h3>항목 (🇯🇵/🇰🇷)</h3><table>{rows_html}</table>
<h3>캡션 🇯🇵</h3><pre>{cap}</pre>
<h3>캡션 해석 🇰🇷</h3><pre>{meta['caption_ko']}</pre>
<p><a href="video.mp4">▶ video.mp4</a></p>""", encoding="utf-8")
    log(f"      📦 릴스 팩 완성: {pack.name}")
    return {"pack": str(pack), "video": video, "title": title,
            "risk": meta["risk"]}
