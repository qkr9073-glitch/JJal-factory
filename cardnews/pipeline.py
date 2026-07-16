# -*- coding: utf-8 -*-
"""카드뉴스 파이프라인 — 주제 → 기획 → 집필 → 카드 렌더 → 전자책 PDF → 완성팩.
CLI(make_card.py)와 웹서버(server.py)가 공용으로 사용. 진행 로그는 [n/4] 형식(게이지 연동)."""
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

from . import brain, drawer, ebook, render

REVIEW_TEMPLATE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body{{background:#faf5ee;color:#2b2620;font-family:'Malgun Gothic',sans-serif;max-width:640px;margin:0 auto;padding:20px}}
h1{{font-size:20px}} h2{{font-size:16px;margin-top:26px}} a{{color:#c2502a}}
img{{max-width:100%;border-radius:12px;margin:8px 0;display:block;box-shadow:0 4px 14px rgba(120,80,40,.15)}}
pre{{white-space:pre-wrap;background:#fff;padding:16px;border-radius:12px;font-family:inherit;font-size:15px;line-height:1.6;border:1px solid #eeddcc}}
button{{background:#e2683c;color:#fff;border:0;padding:14px 22px;border-radius:10px;font-size:16px;cursor:pointer;font-weight:bold}}
.badge{{display:inline-block;background:#f3e3d3;padding:4px 10px;border-radius:8px;font-size:12px;margin-right:6px}}
</style></head><body>
<h1>🗂 {title}</h1>
<p><span class="badge">카드 {num_cards}장</span><span class="badge">전자책 {num_pages}p</span>
<span class="badge">댓글 키워드: {keyword}</span></p>
<p>⬇ <a href="{zip_name}" download><b>zip 한 번에 받기</b></a> &nbsp;|&nbsp;
📕 <a href="ebook.pdf" download><b>전자책 PDF</b></a></p>
<h2>1) 캐러셀 (순서대로 업로드)</h2>
{cards_html}
<h2>2) 본문 캡션 <button onclick="copyCap()">📋 캡션 복사</button></h2>
<pre id="cap">{caption}</pre>
<h2>3) 운영 체크</h2>
<pre>· 업로드 후 ManyChat 자동화의 댓글 키워드가 '{keyword}' 인지 확인
· 전자책 PDF를 링크(드라이브/클라우드플레어)로 올리고 DM 메시지에 연결
· 자세한 순서는 프로젝트 폴더의 FUNNEL-GUIDE.md 참고</pre>
<script>
function copyCap(){{navigator.clipboard.writeText(document.getElementById('cap').innerText)
.then(()=>alert('캡션이 복사됐습니다!'));}}
</script></body></html>"""


def _slug(title, limit=18):
    s = re.sub(r"[^0-9A-Za-z가-힣]+", "_", title or "").strip("_")
    return s[:limit] or "cardnews"


def build_cardnews(topic, cfg, base_dir, n_items=None, keyword=None,
                   teaser_count=None, mock=False, log=print,
                   proof=False, context=None, context_kind="news", make_ebook=True,
                   account=None):
    """주제 → 완성팩. 반환 dict: pack, meta, caption, cards, ebook_pages
    proof=True: 자료서랍에서 증빙 캡처를 골라 CTA 앞에 증빙 카드 삽입.
    context: 근거 텍스트 (기획 프롬프트에 삽입)
    context_kind: 'news'(최신 소식) 또는 'ref'(해외 레퍼런스 현지화)"""
    base_dir = Path(base_dir)
    n_items = int(n_items or cfg.get("card_items", 60))
    teaser_count = int(teaser_count or cfg.get("card_teaser", 8))
    per_card = int(cfg.get("card_items_per_card", 2))

    model = "모의" if mock else cfg.get("gemini_model", "gemini-2.5-flash")
    log(f"[1/4] AI 기획 중 ({model}) — 주제: {topic}")
    plan = brain.plan(cfg, topic, n_items=n_items, teaser_count=teaser_count,
                      keyword=keyword, mock=mock, context=context,
                      context_kind=context_kind)
    log(f"      표지: {plan['title_top']} / {plan['title_main']}")
    log(f"      카테고리 {len(plan['categories'])}개 · 아이템 {plan['n_items']}개 · "
        f"댓글 키워드 '{plan['comment_keyword']}'")

    # 표지 자동 사진 (Pexels) — 수동 표지 없고 auto_cover 켜졌을 때만
    if cfg.get("card_auto_cover", True) and not cfg.get("cover_image") and not mock:
        iq = plan.get("image_query") or plan.get("title_main") or topic
        try:
            from src import stock
            from PIL import Image as _Img
            import io as _io
            import uuid as _uuid
            cands = stock.search_best(cfg, iq, n=5, orientation="square")
            if cands:
                covdir = Path(base_dir) / "_covertmp"
                covdir.mkdir(exist_ok=True)
                cpath = covdir / (_uuid.uuid4().hex[:12] + ".jpg")
                raw = stock.download(cands[0]["url"])
                _Img.open(_io.BytesIO(raw)).convert("RGB").save(cpath, "JPEG", quality=90)
                cfg = dict(cfg)
                cfg["cover_image"] = str(cpath)
                log(f"      🖼 표지 자동 사진: \"{iq}\" (Pexels)")
            else:
                log(f"      (표지 자동 사진 후보 없음: \"{iq}\" — 텍스트 표지로)")
        except Exception as e:
            log(f"      (표지 자동 사진 건너뜀: {str(e)[:60]})")

    log(f"[2/4] AI 집필 중 — 아이템 {plan['n_items']}개 (10개 단위 배치)")
    items = brain.write_items(cfg, plan, log=log, mock=mock)
    if not mock and cfg.get("card_polish", True):
        log("      🧐 깐깐한 편집장 2차 감수 — 밍밍한 아이템 골라 다시 쓰기")
        items = brain.polish_items(cfg, items, log=log)
    by_num = {it["num"]: it for it in items}
    teaser_items = [by_num[n] for n in plan["teaser"] if n in by_num]
    plan["preview_titles"] = [it["title"] for it in teaser_items[:3]]

    proofs = []
    if proof:
        k = int(cfg.get("card_proof_count", 2))
        proofs = drawer.pick(cfg, base_dir, topic, k=k, log=log, mock=mock)
        log(f"      증빙 자료 {len(proofs)}개 선택"
            + ("" if proofs else " — 자료서랍이 비었거나 어울리는 게 없어요"))

    # 본문 사이 사진 카드 준비 (card_body_images 토글) — Pexels
    body_photos = []
    if cfg.get("card_body_images", False) and not mock:
        try:
            from src import stock
            from PIL import Image as _Img
            import io as _io
            import uuid as _uuid
            bdir = Path(base_dir) / "_covertmp"
            bdir.mkdir(exist_ok=True)
            for bi in (plan.get("body_images") or [])[:3]:
                cands = stock.search_best(cfg, bi.get("query", ""), n=3,
                                          orientation="portrait")
                if not cands:
                    continue
                bpath = bdir / (_uuid.uuid4().hex[:12] + ".jpg")
                _Img.open(_io.BytesIO(stock.download(cands[0]["url"]))).convert(
                    "RGB").save(bpath, "JPEG", quality=90)
                body_photos.append((bi.get("caption", ""), str(bpath)))
            if body_photos:
                log(f"      🖼 본문 사진 {len(body_photos)}장 준비 (Pexels)")
        except Exception as e:
            log(f"      (본문 사진 준비 건너뜀: {str(e)[:50]})")

    log(f"[3/4] 카드 렌더링 — 표지 + 아이템카드"
        + (f" + 본문사진 {len(body_photos)}장" if body_photos else "")
        + (" + 증빙" if proofs else "") + " + CTA")
    pack = _make_pack_dir(base_dir / cfg.get("output_dir", "결과물"), plan)
    cards = []
    p = pack / "01.jpg"
    render.render_cover(plan, cfg, p)
    cards.append(p)
    item_groups = [teaser_items[i:i + per_card]
                   for i in range(0, len(teaser_items), per_card)]
    for gi, grp in enumerate(item_groups):
        p = pack / f"{len(cards) + 1:02d}.jpg"
        render.render_items_card(plan, grp, cfg, p)
        cards.append(p)
        if gi < len(body_photos):   # 이 아이템 카드 뒤에 본문 사진 카드 끼우기
            cap, bpath = body_photos[gi]
            p = pack / f"{len(cards) + 1:02d}.jpg"
            render.render_photo_card(plan, bpath, cap, cfg, p)
            cards.append(p)
    for pr in proofs:
        p = pack / f"{len(cards) + 1:02d}.jpg"
        render.render_proof_card(plan, pr, cfg, p)
        cards.append(p)
    p = pack / f"{len(cards) + 1:02d}.jpg"
    render.render_cta(plan, cfg, p)
    cards.append(p)
    log(f"      카드 {len(cards)}장 완성")

    if make_ebook and account:      # 전자책 아웃트로 = 업로드 계정의 인스타 프로필(사진·소개)
        try:
            import insta
            prof = insta.fetch_profile(cfg, account=account,
                                       dest_photo=(pack / "_profile.jpg"), log=log)
            if prof:
                cfg = dict(cfg)
                if prof.get("photo_path"):
                    cfg["card_profile_photo"] = prof["photo_path"]
                bio_lines = [ln for ln in (prof.get("biography") or "").splitlines()
                             if ln.strip()][:3]
                if bio_lines:
                    cfg["card_profile_lines"] = bio_lines
                log(f"      전자책 아웃트로: @{prof.get('username', '')} 프로필 반영")
        except Exception as e:
            log(f"      (프로필 반영 실패, config 값 사용: {str(e)[:60]})")
    if make_ebook:
        log("[4/4] 전자책 PDF + 패키징...")
        num_pages = ebook.build_ebook(plan, items, cfg, pack / "ebook.pdf", log=log)
    else:
        log("[4/4] 패키징... (전자책 미포함)")
        num_pages = 0

    caption = _final_caption(plan, cfg)
    (pack / "caption.txt").write_text(caption, encoding="utf-8")
    meta = {
        "type": "cardnews",
        "mode": "proof" if proofs else "normal",
        "theme": cfg.get("card_theme", "hunter"),
        "topic": topic,
        "title": f"{plan['title_top']} {plan['title_main']}",
        "keyword": plan["comment_keyword"],
        "ebook_title": plan["ebook_title"],
        "n_items": plan["n_items"],
        "categories": [{"name": c["name"], "count": len(c["items"])}
                       for c in plan["categories"]],
        "teaser": plan["teaser"],
        "ebook": bool(make_ebook and num_pages),
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    (pack / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    (pack / "items.json").write_text(
        json.dumps({"plan": {k: v for k, v in plan.items() if k != "caption"},
                    "items": items,
                    "proofs": [{k: v for k, v in pr.items() if k != "file"}
                               for pr in proofs]},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    zip_path = pack / f"{pack.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for c in cards:
            zf.write(c, c.name)
        zf.write(pack / "caption.txt", "caption.txt")
        if make_ebook and (pack / "ebook.pdf").exists():
            zf.write(pack / "ebook.pdf", "ebook.pdf")

    cards_html = "\n".join(f'<img src="{c.name}">' for c in cards)
    (pack / "review.html").write_text(REVIEW_TEMPLATE.format(
        title=meta["title"], num_cards=len(cards), num_pages=num_pages,
        keyword=meta["keyword"], zip_name=zip_path.name,
        caption=caption.replace("&", "&amp;").replace("<", "&lt;"),
        cards_html=cards_html), encoding="utf-8")

    return {"pack": pack, "meta": meta, "caption": caption,
            "cards": [c.name for c in cards], "ebook_pages": num_pages}


def build_from_reference(image_paths, caption, cfg, base_dir, n_items=None,
                         keyword=None, mock=False, log=print):
    """해외 게시물 캡처(+캡션) → 한국 타깃 완성팩.
    비전으로 핵심 소재를 뽑아(reference_topic) 현지화 기획(context_kind='ref')으로 제작."""
    log("[1/4] 해외 레퍼런스 분석 중 (AI 비전) — 핵심 소재 추출")
    ref = brain.reference_topic(cfg, image_paths, caption or "")
    topic = ref.get("topic") or "해외 인기 게시물 한국판"
    ctx = []
    if ref.get("key_points"):
        ctx.append("핵심 포인트:\n" + "\n".join(f"- {p}" for p in ref["key_points"]))
    if ref.get("hook_angle"):
        ctx.append("새 후킹 각도: " + str(ref["hook_angle"]).strip())
    if ref.get("avoid"):
        ctx.append("원본 티 제거(치환 대상): " + str(ref["avoid"]).strip())
    context = "\n".join(ctx)[:2000]
    log(f"      소재 추출 완료 · 원문 {ref.get('source_lang', '외국어')} → 주제: {topic}")
    return build_cardnews(topic, cfg, base_dir, n_items=n_items, keyword=keyword,
                          context=context, context_kind="ref", mock=mock, log=log)


LANG_LABEL = {"ja": "일본어", "en": "영어"}
LANG_TAG = {"ja": "JP", "en": "EN"}


def build_translated(pack_dir, cfg, base_dir, target="ja", log=print):
    """완성된 한국어 카드뉴스 팩 → 현지 언어로 번역·현지화 + 현지 폰트로 재렌더한 새 팩.
    카드 캐러셀 + 캡션 중심(전자책/증빙 제외). 반환은 build_cardnews와 같은 형태."""
    src = Path(pack_dir)
    base_dir = Path(base_dir)
    data = json.loads((src / "items.json").read_text(encoding="utf-8"))
    plan = dict(data["plan"])
    items = data.get("items", [])
    try:
        smeta = json.loads((src / "meta.json").read_text(encoding="utf-8"))
    except OSError:
        smeta = {}
    theme = smeta.get("theme", cfg.get("card_theme", "hunter"))
    lang_label = LANG_LABEL.get(target, "일본어")
    per_card = int(cfg.get("card_items_per_card", 2))

    log(f"[1/4] {lang_label} 현지화 번역 — 표지·캡션·카테고리")
    orig_cats = [c.get("name", "") for c in plan.get("categories", [])]
    tp = brain.translate_plan(cfg, plan, lang=lang_label)
    for k in ("title_top", "title_main", "subtitle", "ebook_title",
              "caption", "comment_keyword"):
        if tp.get(k):
            plan[k] = str(tp[k]).strip()
    cats = tp.get("categories") or []
    cat_map = {}  # 원본 KO 카테고리명 → 번역명 (아이템 eyebrow에도 반영)
    for i, c in enumerate(plan.get("categories", [])):
        if i < len(cats) and cats[i]:
            cat_map[orig_cats[i]] = str(cats[i]).strip()
            c["name"] = str(cats[i]).strip()

    log(f"[2/4] {lang_label} 현지화 번역 — 아이템 {len(items)}개")
    titems = brain.translate_items(cfg, items, lang=lang_label, log=log)
    for it in titems:
        oc = (it.get("category") or "").strip()
        if oc in cat_map:
            it["category"] = cat_map[oc]
    by_num = {it["num"]: it for it in titems}
    teaser_items = [by_num[n] for n in plan.get("teaser", []) if n in by_num]
    if not teaser_items:
        teaser_items = titems[:min(len(titems), int(cfg.get("card_teaser", 8)))]
    pv = [str(x).strip() for x in (tp.get("preview_titles") or []) if str(x).strip()]
    plan["preview_titles"] = pv[:3] or [it["title"] for it in teaser_items[:3]]

    cfg2 = dict(cfg)
    cfg2["card_theme"] = theme
    cfg2["card_lang"] = target

    log(f"[3/4] {lang_label} 카드 렌더링 — 표지 + 아이템 + CTA")
    root = base_dir / cfg.get("output_dir", "결과물")
    root.mkdir(parents=True, exist_ok=True)
    name, pack, n = f"{src.name}_{LANG_TAG.get(target, 'JP')}", None, 1
    pack = root / name
    while pack.exists():
        n += 1
        pack = root / f"{name}_{n}"
    pack.mkdir()

    cards = []
    p = pack / "01.jpg"
    render.render_cover(plan, cfg2, p)
    cards.append(p)
    for i in range(0, len(teaser_items), per_card):
        p = pack / f"{len(cards) + 1:02d}.jpg"
        render.render_items_card(plan, teaser_items[i:i + per_card], cfg2, p)
        cards.append(p)
    p = pack / f"{len(cards) + 1:02d}.jpg"
    render.render_cta(plan, cfg2, p)
    cards.append(p)
    log(f"      카드 {len(cards)}장 완성")

    log("[4/4] 캡션 + 패키징...")
    kw = plan.get("comment_keyword", "")
    caption = (plan.get("caption") or "").strip()
    if kw and kw not in caption and target == "ja":
        caption += f"\n\n📌 コメントに「{kw}」と書いてください。\nDMでまとめPDFをお送りします。"
    if target == "ja" and cfg.get("card_hashtags_ja"):
        caption += "\n\n" + cfg["card_hashtags_ja"]
    (pack / "caption.txt").write_text(caption, encoding="utf-8")

    meta = {
        "type": "cardnews", "mode": "translated", "lang": target,
        "theme": theme, "source_pack": src.name,
        "topic": smeta.get("topic", ""),
        "title": f"{plan['title_top']} {plan['title_main']}",
        "keyword": kw, "ebook_title": plan.get("ebook_title", ""),
        "n_items": plan.get("n_items", len(items)),
        "teaser": plan.get("teaser", []),
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    (pack / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    (pack / "items.json").write_text(
        json.dumps({"plan": {k: v for k, v in plan.items() if k != "caption"},
                    "items": titems}, ensure_ascii=False, indent=2), encoding="utf-8")

    zip_path = pack / f"{pack.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for c in cards:
            zf.write(c, c.name)
        zf.write(pack / "caption.txt", "caption.txt")

    cards_html = "\n".join(f'<img src="{c.name}">' for c in cards)
    (pack / "review.html").write_text(REVIEW_TEMPLATE.format(
        title=meta["title"], num_cards=len(cards), num_pages=0,
        keyword=meta["keyword"], zip_name=zip_path.name,
        caption=caption.replace("&", "&amp;").replace("<", "&lt;"),
        cards_html=cards_html), encoding="utf-8")

    return {"pack": pack, "meta": meta, "caption": caption,
            "cards": [c.name for c in cards], "ebook_pages": 0}


def rerender_pack(cfg, pack_dir, plan_patch=None, items=None, caption=None,
                  theme=None, log=print):
    """완성팩의 items.json으로 카드+전자책+zip 재생성 — Gemini 호출 없이 문구 수정 반영.
    plan_patch(표지 문구 등)/items(아이템 문구)/caption/theme 를 주면 덮어쓰고 저장.
    반환은 build_cardnews와 같은 형태."""
    pack = Path(pack_dir)
    data = json.loads((pack / "items.json").read_text(encoding="utf-8"))
    plan = data["plan"]
    try:
        meta = json.loads((pack / "meta.json").read_text(encoding="utf-8"))
    except OSError:
        meta = {"type": "cardnews"}

    if plan_patch:
        for k in ("title_top", "title_main", "subtitle", "comment_keyword", "ebook_title"):
            v = str(plan_patch.get(k) or "").strip()
            if v:
                plan[k] = v
    if items:
        clean = []
        for it in items:
            try:
                n = int(it.get("num"))
            except (TypeError, ValueError):
                continue
            lines = [{"tag": str(l.get("tag") or "").strip()[:6] or "내용",
                      "text": str(l.get("text") or "").strip()}
                     for l in it.get("lines", []) if str(l.get("text") or "").strip()]
            if not lines:
                raise ValueError(f"{n}번 아이템 내용이 비어 있어요 — 한 줄 이상 남겨주세요")
            clean.append({"num": n,
                          "title": str(it.get("title") or "").strip() or f"아이템 {n}",
                          "emoji": str(it.get("emoji") or "").strip()[:2],
                          "category": str(it.get("category") or "").strip(),
                          "lines": lines[:4]})
        clean.sort(key=lambda x: x["num"])
        data["items"] = clean
    items_all = data["items"]

    cfg = dict(cfg)
    cfg["card_theme"] = theme or meta.get("theme") or cfg.get("card_theme", "hunter")

    by_num = {it["num"]: it for it in items_all}
    teaser_items = [by_num[n] for n in plan.get("teaser", []) if n in by_num]
    plan["preview_titles"] = [it["title"] for it in teaser_items[:3]]
    per_card = int(cfg.get("card_items_per_card", 2))

    log("[3/4] 카드 다시 렌더링...")
    for old in pack.glob("[0-9][0-9].jpg"):
        old.unlink()
    cards = []
    p = pack / "01.jpg"
    render.render_cover(plan, cfg, p)
    cards.append(p)
    for i in range(0, len(teaser_items), per_card):
        p = pack / f"{len(cards) + 1:02d}.jpg"
        render.render_items_card(plan, teaser_items[i:i + per_card], cfg, p)
        cards.append(p)
    for pr in data.get("proofs", []):
        f = drawer.drawer_dir(cfg, pack.parent.parent) / pr.get("name", "")
        if f.exists():
            p = pack / f"{len(cards) + 1:02d}.jpg"
            render.render_proof_card(plan, {**pr, "file": str(f)}, cfg, p)
            cards.append(p)
    p = pack / f"{len(cards) + 1:02d}.jpg"
    render.render_cta(plan, cfg, p)
    cards.append(p)

    log("[4/4] 전자책 PDF + 패키징 다시 만드는 중...")
    num_pages = ebook.build_ebook(plan, items_all, cfg, pack / "ebook.pdf", log=log)

    if caption is not None and str(caption).strip():
        cap = str(caption).strip()
    else:
        try:
            cap = (pack / "caption.txt").read_text(encoding="utf-8").strip()
        except OSError:
            cap = ""
    kw = plan.get("comment_keyword", "")
    if kw and kw not in cap:  # 키워드를 바꿨는데 캡션에 없으면 CTA 보험
        cap += (f"\n\n📌 댓글에 '{kw}' 라고 남겨주세요.\n"
                f"전체 {plan.get('n_items', 0)}개를 정리한 PDF를 DM으로 보내드려요.")
    (pack / "caption.txt").write_text(cap, encoding="utf-8")

    meta.update({"title": f"{plan.get('title_top', '')} {plan['title_main']}".strip(),
                 "keyword": kw, "ebook_title": plan.get("ebook_title", ""),
                 "theme": cfg["card_theme"],
                 "edited": datetime.now().isoformat(timespec="seconds")})
    (pack / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    (pack / "items.json").write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                     encoding="utf-8")

    zip_path = pack / f"{pack.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for c in cards:
            zf.write(c, c.name)
        zf.write(pack / "caption.txt", "caption.txt")
        zf.write(pack / "ebook.pdf", "ebook.pdf")

    cards_html = "\n".join(f'<img src="{c.name}">' for c in cards)
    (pack / "review.html").write_text(REVIEW_TEMPLATE.format(
        title=meta.get("title", pack.name), num_cards=len(cards), num_pages=num_pages,
        keyword=kw, zip_name=zip_path.name,
        caption=cap.replace("&", "&amp;").replace("<", "&lt;"),
        cards_html=cards_html), encoding="utf-8")

    log("      ✅ 수정 반영 완료")
    return {"pack": pack, "meta": meta, "caption": cap,
            "cards": [c.name for c in cards], "ebook_pages": num_pages}


def _make_pack_dir(output_root, plan):
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    base = f"{datetime.now():%Y%m%d_%H%M}_card_{_slug(plan['title_main'])}"
    pack, n = root / base, 1
    while pack.exists():
        n += 1
        pack = root / f"{base}_{n}"
    pack.mkdir()
    return pack


STORY_REVIEW = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body{{background:#faf5ee;color:#2b2620;font-family:'Malgun Gothic',sans-serif;max-width:640px;margin:0 auto;padding:20px}}
h1{{font-size:20px}} h2{{font-size:16px;margin-top:26px}}
img{{max-width:100%;border-radius:12px;margin:8px 0;display:block;box-shadow:0 4px 14px rgba(120,80,40,.15)}}
pre{{white-space:pre-wrap;background:#fff;padding:16px;border-radius:12px;font-family:inherit;font-size:15px;line-height:1.6;border:1px solid #eeddcc}}
.badge{{display:inline-block;background:#f3e3d3;padding:4px 10px;border-radius:8px;font-size:12px;margin-right:6px}}
a{{color:#c2502a}}
</style></head><body>
<h1>📖 {title}</h1>
<p><span class="badge">스토리 카드 {num_cards}장</span>
<span class="badge">댓글 키워드: {keyword}</span></p>
<p>⬇ <a href="{zip_name}" download><b>zip 한 번에 받기</b></a>{ebook_html}</p>
<h2>1) 캐러셀 (순서대로 업로드)</h2>
{cards_html}
<h2>2) 본문 캡션</h2>
<pre>{caption}</pre>
</body></html>"""


def build_story(topic, cfg, base_dir, keyword=None, mock=False, log=print):
    """스토리 원장 기반 서사형 카드뉴스 (전자책 없음).
    반환 dict: pack, meta, caption, cards, ebook_pages=0"""
    base_dir = Path(base_dir)
    story_path = Path(cfg.get("card_story_file") or
                      (Path(__file__).resolve().parent / "스토리원장.md"))
    if not story_path.exists():
        raise RuntimeError("스토리 원장이 없습니다 — cardnews\\스토리원장.md 를 "
                           "먼저 만들어주세요")
    story_text = story_path.read_text(encoding="utf-8")

    model = "모의" if mock else cfg.get("gemini_model", "gemini-2.5-flash")
    log(f"[1/4] 스토리 기획 중 ({model}) — 방향: {topic}")
    plan = brain.plan_story(cfg, topic, story_text, keyword=keyword, mock=mock)
    log(f"      표지: {plan['title_top']} / {plan['title_main']} · "
        f"장면 {len(plan['scenes'])}개 · 키워드 '{plan['comment_keyword']}'")
    log("[2/4] 장면 구성 완료")

    log("[3/4] 카드 렌더링 — 표지 + 장면 + CTA")
    pack = _make_pack_dir(base_dir / cfg.get("output_dir", "결과물"), plan)
    total = len(plan["scenes"])
    cards = [pack / "01.jpg"]
    render.render_story_cover(plan, cfg, cards[0])
    for sc in plan["scenes"]:
        p = pack / f"{len(cards) + 1:02d}.jpg"
        render.render_story_scene(plan, sc, total, cfg, p)
        cards.append(p)
    p = pack / f"{len(cards) + 1:02d}.jpg"
    render.render_story_cta(plan, cfg, p)
    cards.append(p)
    log(f"      카드 {len(cards)}장 완성")

    log("[4/4] 전자책 PDF + 패키징...")
    num_pages = 0
    if plan.get("lessons"):  # 스토리에서 배우는 실전 교훈 → DM용 리드마그넷
        num_pages = ebook.build_ebook(plan, plan["lessons"], cfg,
                                      pack / "ebook.pdf", log=log)

    caption = (plan.get("caption") or "").strip()
    kw = plan["comment_keyword"]
    if kw not in caption:
        caption += (f"\n\n📌 댓글에 '{kw}' 라고 남겨주세요.\n"
                    f"「{plan.get('ebook_title', '실전 노트')}」 PDF를 DM으로 보내드려요.")
    if cfg.get("card_hashtags"):
        caption += "\n\n" + cfg["card_hashtags"]
    (pack / "caption.txt").write_text(caption, encoding="utf-8")

    meta = {
        "type": "cardnews",
        "mode": "story",
        "theme": cfg.get("card_theme", "hunter"),
        "topic": topic,
        "title": f"{plan['title_top']} {plan['title_main']}",
        "keyword": kw,
        "ebook_title": plan.get("ebook_title", ""),
        "n_items": plan.get("n_items", 0),
        "n_scenes": total,
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    (pack / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    (pack / "story.json").write_text(
        json.dumps({"plan": {k: v for k, v in plan.items() if k != "caption"}},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    zip_path = pack / f"{pack.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for c in cards:
            zf.write(c, c.name)
        zf.write(pack / "caption.txt", "caption.txt")
        if num_pages:
            zf.write(pack / "ebook.pdf", "ebook.pdf")

    cards_html = "\n".join(f'<img src="{c.name}">' for c in cards)
    ebook_html = (' &nbsp;|&nbsp; 📕 <a href="ebook.pdf" download>'
                  '<b>전자책 PDF</b></a>' if num_pages else "")
    (pack / "review.html").write_text(STORY_REVIEW.format(
        title=meta["title"], num_cards=len(cards), keyword=kw,
        zip_name=zip_path.name, ebook_html=ebook_html,
        caption=caption.replace("&", "&amp;").replace("<", "&lt;"),
        cards_html=cards_html), encoding="utf-8")

    return {"pack": pack, "meta": meta, "caption": caption,
            "cards": [c.name for c in cards], "ebook_pages": num_pages}


def _final_caption(plan, cfg):
    caption = (plan.get("caption") or "").strip()
    kw = plan["comment_keyword"]
    if kw not in caption:  # AI가 CTA를 빼먹었을 때 보험
        caption += (f"\n\n📌 댓글에 '{kw}' 라고 남겨주세요.\n"
                    f"전체 {plan.get('n_items', 0)}개를 정리한 PDF를 DM으로 보내드려요.")
    if cfg.get("card_hashtags"):
        caption += "\n\n" + cfg["card_hashtags"]
    return caption
