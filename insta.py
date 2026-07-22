# -*- coding: utf-8 -*-
"""인스타그램 자동 업로드 — Meta 공식 Graph API(Instagram Login 방식) 사용.
정책 100% 준수 루트: 캐러셀 컨테이너 생성 → 처리 대기 → 발행.

멀티 계정: config "ig_accounts" {계정키: {user_id, access_token, refreshed}} +
"ig_route" {"meme": 키, "cardnews": 키} — 팩 meta.json의 type으로 자동 분기.
이미지는 공개 URL로 서빙되어야 함 → public_base_url (고정 터널 주소) 사용."""
import json
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

import requests

API_VER = "v23.0"


def _base(cfg):
    return cfg.get("ig_api_base", "https://graph.instagram.com") + "/" + API_VER


def _accounts(cfg):
    accs = cfg.get("ig_accounts") or {}
    if not accs and (cfg.get("ig_access_token") or "").strip():  # 구버전 단일 키 호환
        accs = {"default": {"user_id": str(cfg.get("ig_user_id", "")),
                            "access_token": cfg["ig_access_token"],
                            "refreshed": cfg.get("ig_token_refreshed", "")}}
    return accs


def _pack_kind(pack_dir):
    try:
        meta = json.loads((Path(pack_dir) / "meta.json").read_text(encoding="utf-8"))
        if meta.get("template") == "story":   # 스토리카드 → 스토리 계정
            return "story"
        return "cardnews" if meta.get("type") == "cardnews" else "meme"
    except Exception:
        return "meme"


def resolve_account(cfg, pack_dir=None, account=None):
    """업로드에 쓸 계정 선택: 명시 지정 > ig_route[팩종류] > 첫 계정.
    ⚠️ 스토리카드 격리(양방향): 스토리팩은 오직 story 계정으로만 가고,
       story 계정은 오직 스토리팩만 받는다 — 짤·카드뉴스와 절대 안 섞임."""
    accs = _accounts(cfg)
    if not accs:
        raise RuntimeError("인스타 연동이 아직 안 됐어요 — INSTA-SETUP.md 대로 "
                           "ig_accounts 를 config.json에 넣어주세요")
    def _pick(k):
        acc = accs.get(k)
        if acc and not str(acc.get("access_token") or "").strip():
            raise RuntimeError(
                f"'{k}' 계정은 아직 연동 전이에요 — INSTA-SETUP.md 2번(토큰 발급)을 하고 "
                f"config.json ig_accounts['{k}'] 의 user_id/access_token 을 넣어주세요.")
        return acc
    route = cfg.get("ig_route") or {}
    story_key = route.get("story")
    kind = _pack_kind(pack_dir) if pack_dir else None

    # ── 스토리카드 전용 격리 ─────────────────────────────────────────
    if kind == "story":
        # 스토리팩은 무조건 story 계정으로만 (수동 account 지정도 무시)
        if not story_key or story_key not in accs:
            raise RuntimeError("스토리 계정(ig_route.story)이 아직 연동 안 됐어요.")
        return story_key, _pick(story_key)
    # 여기부터 비-스토리 팩 → story 계정으로는 절대 못 간다
    if account and account in accs:
        if account == story_key:
            raise RuntimeError(
                f"'{story_key}'는 스토리카드 전용 계정이에요 — 짤·카드뉴스는 올릴 수 없어요.")
        return account, _pick(account)
    key = route.get(kind) if kind else None
    key = key or route.get("default")
    if key == story_key:      # 방어: 기본 라우팅이 story 계정을 가리켜도 차단
        key = None
    if key in accs:
        return key, _pick(key)
    for k, a in accs.items():  # 첫 계정 폴백 — story 계정은 건너뜀
        if k != story_key:
            return k, a
    raise RuntimeError("업로드할 수 있는 일반 계정이 없어요.")


def refresh_token_if_due(cfg, base_dir, key, acc, log=print, force=False):
    """장기 토큰(60일) 자동 연장 — 마지막 갱신 7일 지났으면 연장 시도.
    성공 시 config.json의 해당 계정 항목 갱신. 실패해도 기존 토큰으로 계속."""
    last = acc.get("refreshed", "")
    try:
        days = (date.today() - date.fromisoformat(last)).days if last else 999
    except ValueError:
        days = 999
    if not force and days < 7:
        return acc
    try:
        r = requests.get("https://graph.instagram.com/refresh_access_token",
                         params={"grant_type": "ig_refresh_token",
                                 "access_token": acc["access_token"]}, timeout=30)
        data = r.json()
        if r.status_code == 200 and data.get("access_token"):
            path = Path(base_dir) / "config.json"
            disk = json.loads(path.read_text(encoding="utf-8"))
            entry = disk.setdefault("ig_accounts", {}).setdefault(key, dict(acc))
            entry["access_token"] = data["access_token"]
            entry["refreshed"] = date.today().isoformat()
            path.write_text(json.dumps(disk, ensure_ascii=False, indent=2),
                            encoding="utf-8")
            log(f"      [{key}] 토큰 자동 연장 완료 (60일)")
            return entry
        log(f"      [{key}] 토큰 연장 실패(무시하고 진행): {str(data)[:120]}")
    except Exception as e:
        log(f"      [{key}] 토큰 연장 오류(무시하고 진행): {e}")
    return acc


def _wait_ready(cfg, container_id, token, log, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = requests.get(f"{_base(cfg)}/{container_id}",
                         params={"fields": "status_code", "access_token": token},
                         timeout=30)
        status = r.json().get("status_code", "")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise RuntimeError(f"컨테이너 처리 실패: {r.text[:200]}")
        time.sleep(3)
    raise RuntimeError("컨테이너 처리 대기 시간 초과 (이미지 URL 접근 가능한지 확인)")


def _post(url, data, what):
    r = requests.post(url, data=data, timeout=60)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or "id" not in body:
        msg = body.get("error", {}).get("message", r.text[:200])
        raise RuntimeError(f"{what} 실패: {msg}")
    return body["id"]


def _publish_story(cfg, base, uid, token, image_url, key, log):
    """단일 이미지를 스토리로 발행 (피드와 같은 계정·토큰). 반환: story media_id.
    ⚠️ 스토리는 캡션/링크 미지원 — 이미지 한 장만 올라감."""
    log("[스토리] 컨테이너 생성...")
    cid = _post(f"{base}/{uid}/media",
                {"image_url": image_url, "media_type": "STORIES",
                 "access_token": token}, "스토리 컨테이너")
    _wait_ready(cfg, cid, token, log)
    log("[스토리] 발행 중...")
    sid = _post(f"{base}/{uid}/media_publish",
                {"creation_id": cid, "access_token": token}, "스토리 발행")
    log(f"      ✅ @{key} 스토리도 발행 완료!")
    return sid


def publish_carousel(cfg, base_dir, image_urls, caption, pack_dir=None,
                     account=None, log=print):
    """이미지 URL들 + 캡션 → 인스타 발행. 반환: {media_id, permalink, account}"""
    key, acc = resolve_account(cfg, pack_dir=pack_dir, account=account)
    acc = refresh_token_if_due(cfg, base_dir, key, acc, log)
    uid, token = str(acc.get("user_id", "")).strip(), acc.get("access_token", "").strip()
    if not uid or not token:
        raise RuntimeError(f"[{key}] 계정 설정이 비어 있어요 (user_id/access_token)")
    log(f"      업로드 계정: @{key}")
    caption = (caption or "")[:2150]
    base = _base(cfg)

    if len(image_urls) < 2:
        log("[1/3] 이미지 컨테이너 생성...")
        cid = _post(f"{base}/{uid}/media",
                    {"image_url": image_urls[0], "caption": caption,
                     "access_token": token}, "이미지 컨테이너")
        _wait_ready(cfg, cid, token, log)
    else:
        if len(image_urls) > 10:
            log(f"      캐러셀 한도 10장 — {len(image_urls)}장 중 앞 10장만 업로드")
            image_urls = image_urls[:10]
        log(f"[1/3] 캐러셀 아이템 {len(image_urls)}개 등록...")
        children = []
        for i, u in enumerate(image_urls, 1):
            children.append(_post(f"{base}/{uid}/media",
                                  {"image_url": u, "is_carousel_item": "true",
                                   "access_token": token}, f"아이템 {i}"))
            log(f"      아이템 {i}/{len(image_urls)} 등록")
        for child in children:
            _wait_ready(cfg, child, token, log)
        log("[2/3] 캐러셀 컨테이너 생성...")
        cid = _post(f"{base}/{uid}/media",
                    {"media_type": "CAROUSEL", "children": ",".join(children),
                     "caption": caption, "access_token": token}, "캐러셀 컨테이너")
        _wait_ready(cfg, cid, token, log)

    log("[3/3] 발행 중...")
    media_id = _post(f"{base}/{uid}/media_publish",
                     {"creation_id": cid, "access_token": token}, "발행")
    permalink = ""
    try:
        r = requests.get(f"{base}/{media_id}",
                         params={"fields": "permalink", "access_token": token},
                         timeout=30)
        permalink = r.json().get("permalink", "")
    except Exception:
        pass
    log(f"      ✅ @{key} 발행 완료! {permalink}")
    result = {"media_id": media_id, "permalink": permalink, "account": key}
    # 피드 발행 성공 후 커버(첫 장)를 스토리로도 자동 발행 (세 계정 공통)
    if cfg.get("auto_story", True) and image_urls:
        try:
            result["story_id"] = _publish_story(
                cfg, base, uid, token, image_urls[0], key, log)
        except Exception as e:
            log(f"      ⚠️ 스토리 자동 발행은 건너뜀(피드는 정상 발행됨): {e}")
    return result


def publish_reel(cfg, base_dir, video_url, caption, account=None,
                 share_to_feed=True, cover_url=None, log=print):
    """영상 URL + 캡션 → 인스타 릴스(Reels) 발행. 반환: {media_id, permalink, account}.
    릴스는 '팩'이 아니라서 스토리 전용 격리 없이, 지정한 계정으로 바로 보낸다."""
    accs = _accounts(cfg)
    if not accs:
        raise RuntimeError("인스타 연동이 아직 안 됐어요 — config.json 의 ig_accounts 확인")
    key = account if (account and account in accs) else \
        ((cfg.get("ig_route") or {}).get("default") or next(iter(accs)))
    acc = accs.get(key)
    if not acc or not str(acc.get("access_token") or "").strip():
        raise RuntimeError(f"'{key}' 계정이 아직 연동 전이에요 (user_id/access_token)")
    acc = refresh_token_if_due(cfg, base_dir, key, acc, log)
    uid = str(acc.get("user_id", "")).strip()
    token = acc.get("access_token", "").strip()
    log(f"      업로드 계정: @{key}")
    caption = (caption or "")[:2150]
    base = _base(cfg)
    log("[1/3] 릴스 컨테이너 생성...")
    params = {"media_type": "REELS", "video_url": video_url, "caption": caption,
              "share_to_feed": "true" if share_to_feed else "false",
              "access_token": token}
    if cover_url:                       # 선택한 대표컷을 릴스 커버(썸네일)로 지정
        params["cover_url"] = cover_url
        log(f"      커버 지정: {cover_url}")
    cid = _post(f"{base}/{uid}/media", params, "릴스 컨테이너")
    log("[2/3] 인스타가 영상 처리 중... (수십 초~몇 분, 기다려주세요)")
    _wait_ready(cfg, cid, token, log, timeout=360)   # 영상은 처리 시간이 길다
    log("[3/3] 릴스 발행 중...")
    media_id = _post(f"{base}/{uid}/media_publish",
                     {"creation_id": cid, "access_token": token}, "릴스 발행")
    permalink = ""
    try:
        r = requests.get(f"{base}/{media_id}",
                         params={"fields": "permalink", "access_token": token}, timeout=30)
        permalink = r.json().get("permalink", "")
    except Exception:
        pass
    log(f"      ✅ @{key} 릴스 발행 완료! {permalink}")
    return {"media_id": media_id, "permalink": permalink, "account": key}


def post_comment(cfg, media_id, text, account=None, log=print):
    """발행된 게시물/릴스에 '본인 계정'으로 댓글 작성(고정댓글 CTA용).
    권한: 토큰에 instagram_business_manage_comments 필요. 실패 시 예외."""
    text = (text or "").strip()
    if not media_id or not text:
        return None
    accs = _accounts(cfg)
    key = account if (account and account in accs) else \
        ((cfg.get("ig_route") or {}).get("default") or next(iter(accs), None))
    acc = accs.get(key) or {}
    token = str(acc.get("access_token") or "").strip()
    if not token:
        raise RuntimeError(f"'{key}' 계정 토큰이 없어요")
    r = requests.post(f"{_base(cfg)}/{media_id}/comments",
                      data={"message": text[:900], "access_token": token}, timeout=30)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or "id" not in body:
        msg = body.get("error", {}).get("message", r.text[:150])
        raise RuntimeError(f"댓글 실패: {msg}")
    log(f"      💬 @{key} 자동 댓글 달림")
    return body["id"]


def fetch_profile(cfg, account=None, dest_photo=None, log=print):
    """선택 계정의 인스타 프로필(유저명·이름·소개·사진) 조회. dest_photo 경로 주면 사진 다운로드.
    반환 {username, name, biography, photo_path} 또는 None(미연동/실패)."""
    try:
        key, acc = resolve_account(cfg, account=account)
    except Exception as e:
        log(f"      (프로필 조회 건너뜀: {e})")
        return None
    uid = str(acc.get("user_id", "")).strip()
    token = str(acc.get("access_token", "")).strip()
    if not uid or not token:
        return None
    try:
        r = requests.get(f"{_base(cfg)}/{uid}",
                         params={"fields": "username,name,profile_picture_url,biography",
                                 "access_token": token}, timeout=30)
        if r.status_code != 200:
            log(f"      (프로필 조회 실패 {r.status_code})")
            return None
        j = r.json()
        prof = {"username": j.get("username", ""), "name": j.get("name", ""),
                "biography": j.get("biography", ""), "photo_path": None}
        purl = j.get("profile_picture_url")
        if purl and dest_photo:
            try:
                import io
                from PIL import Image
                pr = requests.get(purl, timeout=30)
                if pr.status_code == 200:
                    Image.open(io.BytesIO(pr.content)).convert("RGB").save(
                        str(dest_photo), "JPEG", quality=90)
                    prof["photo_path"] = str(dest_photo)
            except Exception:
                pass
        return prof
    except Exception as e:
        log(f"      (프로필 조회 오류: {str(e)[:80]})")
        return None


# ---------------------------------------------------------------- 완성팩 → 발행

def pack_image_urls(cfg, pack_dir, lead=None):
    """완성팩 폴더 → 공개 이미지 URL 목록 (카드팩=NN.jpg 순서, 짤팩=썸네일+짤)"""
    pack_dir = Path(pack_dir)
    public = (cfg.get("public_base_url") or "https://jjal.traffic-charger.com").rstrip("/")
    names = []
    numbered = sorted(p.name for p in pack_dir.glob("[0-9][0-9].jpg"))
    if (pack_dir / "thumb.jpg").exists():  # 짤공장 팩: 썸네일이 첫 장
        names.append(lead if lead and (pack_dir / lead).exists() else "thumb.jpg")
    names += numbered
    if not names:
        raise RuntimeError("팩에 업로드할 이미지가 없습니다")
    rel = quote(pack_dir.name)
    return [f"{public}/packs/{rel}/{quote(n)}" for n in names]


def load_published(base_dir):
    p = Path(base_dir) / "published.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def mark_published(base_dir, pack_name, result):
    p = Path(base_dir) / "published.json"
    data = load_published(base_dir)
    data[pack_name] = {**result, "time": datetime.now().isoformat(timespec="seconds")}
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def publish_pack(cfg, base_dir, pack_dir, lead=None, account=None, force=False,
                 caption=None, log=print):
    """완성팩 통째로 발행 (계정 자동 라우팅 + 중복 방지). caption 주면 caption.txt 대신 사용."""
    pack_dir = Path(pack_dir)
    if not force and pack_dir.name in load_published(base_dir):
        raise RuntimeError("이미 업로드된 팩입니다 (다시 올리려면 강제 재업로드)")
    if not caption:
        caption = ""
        cap = pack_dir / "caption.txt"
        if cap.exists():
            caption = cap.read_text(encoding="utf-8")
    urls = pack_image_urls(cfg, pack_dir, lead=lead)
    log(f"      이미지 {len(urls)}장 · 캡션 {len(caption)}자")
    result = publish_carousel(cfg, base_dir, urls, caption,
                              pack_dir=pack_dir, account=account, log=log)
    mark_published(base_dir, pack_dir.name, result)
    return result
