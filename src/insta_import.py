# -*- coding: utf-8 -*-
"""인스타 '수입': 지정한 공개 계정에서 이미지 캐러셀 게시물을 수집(릴스/영상 제외).

- 로그인은 버너(부계정) 세션 권장 — 본계정/비즈니스 계정 보호.
- 세션 파일을 재사용해 요청을 최소화(차단 위험 감소). 소량·좋아요순.
- 인스타는 이미지 게시물의 '조회수'를 공개하지 않아 인기순은 '좋아요' 기준.
"""
import json
import os
import shutil
import tempfile
from pathlib import Path

from . import packer


# ── 지정 계정 목록 (ig_targets.json, gitignore) ─────────────────
def _targets_file(base):
    return Path(base) / "ig_targets.json"


def load_targets(base):
    try:
        d = json.loads(_targets_file(base).read_text(encoding="utf-8"))
        return [str(x).strip().lstrip("@") for x in d if str(x).strip()] if isinstance(d, list) else []
    except Exception:
        return []


def save_targets(base, lst):
    clean, seen = [], set()
    for x in lst:
        u = str(x).strip().lstrip("@").lower()
        if u and u not in seen:
            seen.add(u)
            clean.append(u)
    _targets_file(base).write_text(json.dumps(clean, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    return clean


# ── 로그인/세션 ────────────────────────────────────────────────
def _upload_usernames(cfg):
    """업로드(게시)용으로 설정된 계정 식별자 모음 — 수집 로그인에 절대 못 쓰게 비교용."""
    names = set()
    accts = cfg.get("ig_accounts") or {}
    for key, v in accts.items():
        names.add(str(key).strip().lstrip("@").lower())
        if isinstance(v, dict):
            for f in ("username", "handle", "user_id"):
                if v.get(f):
                    names.add(str(v[f]).strip().lstrip("@").lower())
    names.discard("")
    return names


def _assert_not_upload_account(cfg, user):
    """수집 로그인 계정이 업로드 계정과 겹치면 즉시 거부(메인/업로드 계정 보호)."""
    if user and user.lower() in _upload_usernames(cfg):
        raise RuntimeError(
            f"'@{user}'는 업로드(게시)용 계정입니다. 수집은 반드시 별도 부계정으로만 하세요. "
            "config의 ig_import_login에 업로드 계정을 넣지 마세요.")


def _loader(cfg, base, log=print):
    import instaloader
    L = instaloader.Instaloader(
        quiet=True, download_pictures=False, download_videos=False,
        download_video_thumbnails=False, download_geotags=False,
        download_comments=False, save_metadata=False, compress_json=False,
        max_connection_attempts=1)
    login = cfg.get("ig_import_login") or {}
    user = (login.get("username") or "").strip().lstrip("@")
    pwd = (login.get("password") or "").strip()
    _assert_not_upload_account(cfg, user)   # 업로드 계정으로 수집 로그인 절대 금지
    if not user:
        log("      (인스타 크롤 로그인 미설정 — 익명 시도, 대부분 차단됨)")
        return L, ""
    sess_dir = Path(base) / "ig_session"
    sess_dir.mkdir(exist_ok=True)
    sess_file = sess_dir / user
    try:
        if sess_file.exists():
            L.load_session_from_file(user, str(sess_file))
            log(f"      IG 세션 재사용: @{user}")
        elif pwd:
            L.login(user, pwd)
            L.save_session_to_file(str(sess_file))
            log(f"      IG 로그인 성공 → 세션 저장: @{user}")
        else:
            log(f"      (@{user} 세션 없음 + 비밀번호 없음 → 익명 시도)")
    except Exception as e:
        log(f"      IG 로그인 실패({str(e)[:90]}) — 익명으로 시도")
    return L, user


# ── 게시물 조회 ────────────────────────────────────────────────
def fetch_posts(cfg, base, username, limit=6, log=print):
    """@username 최근 게시물 중 '이미지 전용'(단일/캐러셀, 영상·릴스 제외)만 좋아요순 반환."""
    import instaloader
    L, _ = _loader(cfg, base, log)
    username = username.strip().lstrip("@")
    try:
        prof = instaloader.Profile.from_username(L.context, username)
    except Exception as e:
        raise RuntimeError(f"@{username} 프로필 조회 실패: {str(e)[:120]}")
    out, seen = [], 0
    for post in prof.get_posts():
        seen += 1
        if len(out) >= limit or seen > limit * 5:
            break
        try:
            if post.is_video:                       # 단일 영상/릴스 제외
                continue
            imgs = []
            if post.typename == "GraphSidecar":     # 캐러셀
                ok = True
                for node in post.get_sidecar_nodes():
                    if node.is_video:               # 영상 섞인 캐러셀 제외
                        ok = False
                        break
                    imgs.append(node.display_url)
                if not ok:
                    continue
            else:                                    # 단일 이미지
                imgs = [post.url]
            if not imgs:
                continue
            out.append({
                "shortcode": post.shortcode,
                "url": f"https://www.instagram.com/p/{post.shortcode}/",
                "username": username,
                "caption": post.caption or "",
                "likes": int(post.likes or 0),
                "n": len(imgs),
                "image_urls": imgs,
                "thumb": imgs[0],
            })
        except Exception as e:
            log(f"      (게시물 건너뜀: {str(e)[:60]})")
            continue
    out.sort(key=lambda p: p["likes"], reverse=True)
    return out


def fetch_many(cfg, base, usernames, per=4, log=print):
    """여러 지정 계정에서 이미지 게시물 수집 → 좋아요순 합침."""
    allp = []
    for u in usernames:
        try:
            log(f"[수집] @{u} ...")
            allp.extend(fetch_posts(cfg, base, u, limit=per, log=log))
        except Exception as e:
            log(f"      @{u} 실패: {str(e)[:100]}")
            continue
    allp.sort(key=lambda p: p["likes"], reverse=True)
    return allp


# ── 선택 게시물 → 완성팩(결과물) ───────────────────────────────
def import_post(cfg, base, post, log=print):
    """이미지 URL들을 내려받아 완성팩 생성. 캡션은 원문 그대로. packer 재사용."""
    import requests
    from datetime import datetime
    root = Path(base) / cfg.get("output_dir", "결과물")
    root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="ig_", dir=root))
    try:
        urls = post.get("image_urls") or []
        if not urls:
            raise RuntimeError("이미지가 없습니다")
        img_paths = []
        for i, u in enumerate(urls, 1):
            r = requests.get(u, timeout=40)
            r.raise_for_status()
            p = tmp / f"img_{i:02d}.jpg"
            _to_jpg(r.content, p)
            img_paths.append(str(p))
        thumb_tmp = tmp / "thumb_src.jpg"
        shutil.copy(img_paths[0], str(thumb_tmp))
        uname = post.get("username", "")
        caption = (post.get("caption") or "").strip()
        title = f"@{uname} 수입" if uname else "인스타 수입"
        meta = {
            "title": title, "site": f"인스타 @{uname}",
            "type": "meme", "source": "insta_import",
            "src_url": post.get("url", ""), "src_user": uname,
            "src_likes": post.get("likes", 0),
            "created": datetime.now().isoformat(timespec="seconds"),
        }
        pack = packer.build_pack(root, meta, img_paths, [str(thumb_tmp)], caption)
        log(f"[완료] 수입팩 생성: {Path(pack).name}")
        return {"pack": pack, "meta": meta, "caption": caption,
                "num_images": len(img_paths), "num_thumbs": 1}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _to_jpg(raw_bytes, dest):
    """어떤 포맷이든 RGB JPEG로 저장(투명/webp 대비)."""
    from io import BytesIO

    from PIL import Image
    im = Image.open(BytesIO(raw_bytes)).convert("RGB")
    im.save(dest, "JPEG", quality=92)
    return dest
