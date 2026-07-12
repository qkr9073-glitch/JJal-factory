# -*- coding: utf-8 -*-
"""파이프라인 코어 — CLI(make.py)와 웹서버(server.py)가 공용으로 사용"""
import random
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image

from . import brain, cleanup, extractors, hunter, packer, storycard, thumbnail

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
THUMB_W, THUMB_H = 1080, 1350


def _output_root(cfg, base_dir):
    """완성팩 저장 폴더 (config "output_dir", 기본 '결과물') — 없으면 생성"""
    root = Path(base_dir) / cfg.get("output_dir", "결과물")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _row_activity(img):
    """각 가로줄의 '내용 밀도' — 윗줄과의 픽셀 차이 평균 (0 = 균일한 배경)"""
    from PIL import ImageChops
    g = img.convert("L").resize((120, img.height))
    diff = ImageChops.difference(
        g.crop((0, 0, 120, img.height - 1)),
        g.crop((0, 1, 120, img.height)))
    return list(diff.resize((1, img.height - 1), Image.BOX).getdata())


def _smart_cuts(img, n):
    """균등 지점 근처에서 텍스트가 없는 '여백 줄'을 찾아 자를 위치 결정"""
    H = img.height
    act = _row_activity(img)
    # 약간 부드럽게 (±3줄 평균)
    sm = [sum(act[max(0, i - 3):i + 4]) / len(act[max(0, i - 3):i + 4])
          for i in range(len(act))]
    seg = H / n
    win = max(20, int(seg * 0.28))  # 목표점 ±28% 범위에서 여백 탐색
    cuts = [0]
    for i in range(1, n):
        target = int(i * seg)
        lo = max(cuts[-1] + int(seg * 0.5), target - win)
        hi = min(H - int(seg * 0.4), target + win)
        if lo >= hi:
            cuts.append(target)
            continue
        best, best_score = target, None
        for y in range(lo, hi):
            score = sm[min(y, len(sm) - 1)] + 8.0 * abs(y - target) / win
            if best_score is None or score < best_score:
                best, best_score = y, score
        cuts.append(best)
    cuts.append(H)
    return cuts


def _split_tall(image_paths, max_total=18):
    """세로로 긴 스크린샷(비율 1.8 초과)을 4:5 여러 장으로 분할 — '국수가락' 방지.
    자를 위치는 문단 사이 여백(균일한 배경 줄)을 찾아서 결정 (글자 반토막 방지)."""
    out = []
    for p in image_paths:
        p = Path(p)
        img = Image.open(p).convert("RGB")
        ratio = img.height / max(1, img.width)
        if ratio <= 1.8 or len(out) >= max_total:
            out.append(str(p))
            continue
        n = min(10, max(2, -(-int(ratio / 1.25 * 100) // 100)))  # ceil(ratio/1.25)
        try:
            cuts = _smart_cuts(img, n)
        except Exception:
            cuts = [int(i * img.height / n) for i in range(n)] + [img.height]
        for i in range(len(cuts) - 1):
            if len(out) >= max_total:
                break
            seg = img.crop((0, cuts[i], img.width, cuts[i + 1]))
            sp = p.with_name(f"{p.stem}_s{i + 1}.jpg")
            seg.save(sp, "JPEG", quality=92)
            out.append(str(sp))
        p.unlink()  # 원본 통짜는 제거 (조각으로 대체)
    return out


def _letterbox_all(image_paths):
    """모든 짤을 1080x1350 검은 캔버스에 원본 비율 그대로 배치 (잘림 방지)"""
    for p in image_paths:
        img = Image.open(p).convert("RGB")
        if (img.width, img.height) == (THUMB_W, THUMB_H):
            continue
        scale = min(THUMB_W / img.width, THUMB_H / img.height)
        nw = max(1, int(img.width * scale))
        nh = max(1, int(img.height * scale))
        canvas = Image.new("RGB", (THUMB_W, THUMB_H), (0, 0, 0))
        canvas.paste(img.resize((nw, nh), Image.LANCZOS),
                     ((THUMB_W - nw) // 2, (THUMB_H - nh) // 2))
        canvas.save(p, "JPEG", quality=92)


def _make_header(cfg, title, stats=None):
    """썸네일 상단 가짜 게시글 헤더 데이터 (가상 작성자 + 최근 날짜, 통계는 실측 우선)"""
    if not cfg.get("fake_header", True) or not title:
        return None
    stats = stats or {}
    views = stats.get("views") or 0
    recs = stats.get("recs") or 0
    replies = stats.get("replies") or 0
    if views < 50000:  # 실측이 약하면 그럴듯하게 부풀림 (참고 계정 방식)
        views = random.randint(150000, 550000)
    if recs < 300:
        recs = random.randint(500, 1900)
    if replies < 100:
        replies = random.randint(150, 800)
    dt = datetime.now() - timedelta(hours=random.randint(2, 30))
    return {"title": title, "views": views, "recs": recs, "replies": replies,
            "date": dt.strftime("%Y.%m.%d %H:%M")}


def build_from_url(url, cfg, base_dir, mock=False, log=print, stats=None,
                   template=None, clean=None, guide=""):
    log(f"[1/4] 게시물 추출 중... {url}")
    data = extractors.extract(url)
    log(f"      제목: {data['title']}")
    log(f"      이미지 {len(data['image_urls'])}개 발견, 다운로드 중...")
    work = Path(tempfile.mkdtemp(prefix="mf_", dir=_output_root(cfg, base_dir)))
    try:
        images = extractors.download_images(
            data["image_urls"], data["referer"], work, cfg.get("max_images", 10))
        if not images:
            raise RuntimeError("사용할 만한 이미지가 없습니다 (영상 위주 글일 수 있어요). "
                               "다른 글을 고르거나 캡처해서 이미지 모드로 시도해 보세요.")
        log(f"      이미지 {len(images)}개 확보")
        return _finish(cfg, base_dir, data, images, work, mock, log, stats,
                       template=template, clean=clean, guide=guide)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def build_from_images(files, cfg, base_dir, mock=False, log=print,
                      localize=False, caption="", template=None, clean=None, guide=""):
    files = [Path(f) for f in files]
    log(f"[1/4] {'해외 현지화' if localize else '이미지'} 모드: {len(files)}개 투입")
    work = Path(tempfile.mkdtemp(prefix="mf_", dir=_output_root(cfg, base_dir)))
    try:
        images = []
        tpl = template or cfg.get("meme_template", "classic")
        for i, f in enumerate(files, 1):
            dst = work / f"{i:02d}.jpg"
            Image.open(f).convert("RGB").save(dst, "JPEG", quality=92)
            if tpl == "story":   # 카드 스샷이면 사진 영역만 자동 추출(헤더/본문텍스트 버림)
                storycard.extract_photo(str(dst))
            images.append(str(dst))
        if tpl == "story":
            log("      🖼 업로드본에서 사진 영역 자동 추출 완료")
        data = {"site": "해외" if localize else "manual", "url": "", "title": "",
                "text": (caption or "")[:1800], "comments": []}
        return _finish(cfg, base_dir, data, images, work, mock, log, None,
                       localize=localize, template=template, clean=clean, guide=guide)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _finish_story(cfg, base_dir, data, images, work, mock, log, localize=False, guide=""):
    """📖 스토리카드 템플릿 — 썸네일 3종 = 헤드라인 3안 얹은 1번 카드,
    나머지 카드(2~N)가 캐러셀 본문. 짤 원본 대신 '카드'가 업로드된다."""
    lang = cfg.get("story_lang", "ja")   # 스토리 계정은 일본 타겟 → 기본 일본어
    model = "모의" if mock else cfg.get("gemini_model", "gemini-2.5-flash")
    log(f"[2/4] AI 작가 실행 ({model}) · 스토리카드"
        + (" · 🇯🇵 일본어" if lang == "ja" else "")
        + (" · 해외→한국 현지화" if (localize and lang != "ja") else "") + "...")
    n = max(1, min(len(images), 8))
    story = brain.write_story(cfg, data["title"], data["text"], data["comments"],
                              images[:n], mock=mock, localize=localize, lang=lang,
                              guide=guide)
    if lang == "ja" and not mock and cfg.get("story_polish", True):
        log("      ✍️ 일본어 네이티브 감수 (번역투 자동 교정)...")
        story = brain.polish_story_ja(cfg, story, log=log)
    heads, cards = story["headlines"], story["cards"]
    heads_ko = story.get("headlines_ko") or []
    cards_ko = [str(c.get("ko", "")).strip() for c in cards]  # 카드별 본문 한국어 해석
    if story.get("skip"):
        log(f"[참고] AI 의견: 민감 소재일 수 있음 — {story.get('skip_reason')} (판단은 직접)")
    for i, h in enumerate(heads[:3], 1):
        log(f"      헤드라인 후보{i}: {h}")

    log(f"[3/4] 스토리카드 렌더링 — 썸네일 3종 + 본문카드 {max(0, n - 1)}장")
    thumb_paths = []
    for i, h in enumerate(heads[:3]):
        tp = work / ("thumb.jpg" if i == 0 else f"thumb{i + 1}.jpg")
        storycard.render_card(images[0], h, cards[0]["paragraphs"], cfg, tp, lang=lang)
        thumb_paths.append(tp)
    card_paths = []
    for i in range(1, n):
        cp = work / f"card{i:02d}.jpg"
        storycard.render_card(images[i], "", cards[i]["paragraphs"], cfg, cp, lang=lang)
        card_paths.append(str(cp))

    log("[4/4] 완성팩 패키징...")
    # 캡션 꼬리 — 스토리 계정은 일본 타겟이라 짤공장(@sowho77) 서명이 섞이면 안 됨.
    # story_hashtags / story_signature 만 붙인다(없으면 캡션 그대로).
    caption_full = (story.get("caption") or "").strip()
    if cfg.get("story_hashtags"):
        caption_full += "\n\n" + cfg["story_hashtags"]
    if cfg.get("story_signature"):
        caption_full += "\n\n" + cfg["story_signature"]
    meta = {
        "title": heads[0],
        "site": data["site"], "url": data["url"],
        "template": "story", "lang": lang,
        "hooks": [{"line1": h, "line2": "",
                   "ko": (heads_ko[i] if i < len(heads_ko) else "")}
                  for i, h in enumerate(heads[:3])],
        # 카드별 본문 한국어 해석(오역 확인용). idx 0=커버(썸네일)본문, 1~=본문카드(01.jpg~)
        "cards_ko": cards_ko,
        "cards": cards,   # 커버 사진 교체·재렌더용 (일본어 본문 구조 보존)
        "skip": bool(story.get("skip")),
        "skip_reason": story.get("skip_reason", ""),
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    pack = packer.build_pack(_output_root(cfg, base_dir), meta, card_paths,
                             thumb_paths, caption_full)
    # 커버 사진 교체용: 깨끗한 원본 사진을 팩에 보관 (src 접두 → 캐러셀·업로드엔 안 나옴)
    try:
        for i, im in enumerate(images[:n]):
            if Path(im).exists():
                shutil.copy(str(im), str(Path(pack) / f"src{i:02d}.jpg"))
    except Exception as e:
        log(f"      (원본 사진 보관 실패, 무시): {e}")
    if data.get("url"):
        try:
            hunter.mark_seen(base_dir, data["url"], meta["created"])
        except Exception:
            pass
    return {"pack": pack, "meta": meta, "caption": caption_full,
            "num_images": len(card_paths), "num_thumbs": len(thumb_paths)}


def _finish(cfg, base_dir, data, images, work, mock, log, stats=None, localize=False,
            template=None, clean=None, guide=""):
    template = template or cfg.get("meme_template", "classic")
    clean = clean or cfg.get("text_removal", "none")
    if clean in ("bar", "ai") and not mock:
        log(f"      🧹 짤에 박힌 글씨 확인·제거 중 "
            f"({'AI 완전제거' if clean == 'ai' else '바로 가리기'})...")
        images, _paid = cleanup.clean_images(cfg, images, clean, log)
    if template == "story":
        return _finish_story(cfg, base_dir, data, images, work, mock, log, localize,
                             guide=guide)
    model = "모의" if mock else cfg.get("gemini_model", "gemini-2.5-flash")
    log(f"[2/4] AI 작가 실행 ({model}){' · 해외→한국 현지화' if localize else ''}"
        f"{' · 🧭 방향지시 반영' if (guide and guide.strip()) else ''}...")
    copy = brain.write_copy(cfg, data["title"], data["text"],
                            data["comments"], images, mock=mock, localize=localize,
                            guide=guide)
    hooks = copy.get("hooks") or [
        {"line1": copy.get("thumb_line1", ""), "line2": copy.get("thumb_line2", "")}]
    hooks = [h for h in hooks if (h.get("line1") or h.get("line2"))][:3]

    if copy.get("skip"):
        log(f"[참고] AI 의견: 민감 소재일 수 있음 — {copy.get('skip_reason')} (판단은 직접)")
        if not hooks:
            hooks = [{"line1": (data["title"] or "판정 보류")[:13], "line2": ""}]
        if not copy.get("caption"):
            copy["caption"] = f"[AI 참고 의견] {copy.get('skip_reason')}"

    for i, h in enumerate(hooks, 1):
        log(f"      후킹 후보{i}: {h['line1']} / {h['line2']}")

    log(f"[3/4] 썸네일 {len(hooks)}장 렌더링...")
    # plain(자막형) = 상단 가짜 게시글 헤더 바 없이 사진 풀블리드 + 자막만
    header = None if template == "plain" else _make_header(
        cfg, data.get("title") or (hooks[0]["line1"] if hooks else ""), stats)
    thumb_paths = []
    for i, h in enumerate(hooks):
        tp = work / ("thumb.jpg" if i == 0 else f"thumb{i + 1}.jpg")
        thumbnail.render(images[0], h["line1"], h["line2"],
                         cfg.get("watermark", ""), tp, header=header)
        thumb_paths.append(tp)

    # 인스타 캐러셀은 첫 장 비율로 전부 강제되므로, 모든 짤을 4:5 캔버스에 레터박스
    if cfg.get("uniform_size", True):
        if cfg.get("split_tall", True):
            images = _split_tall(images)
        _letterbox_all(images)

    log("[4/4] 완성팩 패키징...")
    caption_full = copy["caption"]
    if cfg.get("hashtags"):
        caption_full += "\n\n" + cfg["hashtags"]
    if cfg.get("signature"):
        caption_full += "\n\n" + cfg["signature"]
    meta = {
        "title": data["title"] or hooks[0]["line1"],
        "site": data["site"], "url": data["url"],
        "template": template,
        "hooks": hooks,
        "skip": bool(copy.get("skip")),
        "skip_reason": copy.get("skip_reason", ""),
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    pack = packer.build_pack(_output_root(cfg, base_dir), meta, images,
                             thumb_paths, caption_full)
    if data.get("url"):
        try:
            hunter.mark_seen(base_dir, data["url"], meta["created"])
        except Exception:
            pass
    return {
        "pack": pack,
        "meta": meta,
        "caption": caption_full,
        "num_images": len(images),
        "num_thumbs": len(thumb_paths),
    }
