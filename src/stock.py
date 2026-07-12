# -*- coding: utf-8 -*-
"""스톡 이미지 검색 — 카드뉴스 표지/본문에 넣을 사진 후보.
Pexels(키 필요·고품질 실사진) 우선, 없으면 Openverse(무료·키 불필요, CC)로 폴백."""
import requests

OPENVERSE = "https://api.openverse.org/v1/images/"
PEXELS = "https://api.pexels.com/v1/search"
UA = {"User-Agent": "jjalfactory/1.0 (contact: local)"}


def pexels_search(cfg, query, n=8, orientation="square"):
    """Pexels 고품질 실사진 검색 → [{thumb, url, source, title, creator}].
    키 없거나 실패 시 빈 리스트(호출부에서 Openverse로 폴백)."""
    key = (cfg.get("pexels_key") or "").strip()
    q = (query or "").strip()
    if not key or not q:
        return []
    try:
        r = requests.get(PEXELS,
                         params={"query": q, "per_page": max(1, min(n, 20)),
                                 "orientation": orientation},
                         headers={"Authorization": key}, timeout=20)
        if r.status_code != 200:
            return []
        out = []
        for p in r.json().get("photos", []):
            src = p.get("src", {})
            full = src.get("large2x") or src.get("large") or src.get("original")
            if not full:
                continue
            out.append({
                "thumb": src.get("medium") or full, "url": full,
                "source": "Pexels", "title": (p.get("alt") or "")[:70],
                "creator": (p.get("photographer") or "")[:40],
            })
        return out
    except Exception:
        return []


def search_best(cfg, query, n=8, orientation="square"):
    """Pexels 우선 → 없으면 Openverse. 표지/본문 자동 사진용 통합 검색."""
    return pexels_search(cfg, query, n=n, orientation=orientation) or search(query, n=n)


def search(query, n=8):
    """주제 → 이미지 후보 리스트 [{thumb, url, source, title, creator}]."""
    q = (query or "").strip()
    if not q:
        return []
    try:
        r = requests.get(OPENVERSE,
                         params={"q": q, "page_size": max(1, min(n, 20)),
                                 "mature": "false", "orientation": "square"},
                         headers=UA, timeout=20)
        if r.status_code != 200:
            return []
        out = []
        for it in r.json().get("results", []):
            full = it.get("url") or it.get("thumbnail")
            thumb = it.get("thumbnail") or full
            if not full:
                continue
            out.append({
                "thumb": thumb, "url": full,
                "source": (it.get("source") or "")[:40],
                "title": (it.get("title") or "")[:70],
                "creator": (it.get("creator") or "")[:40],
            })
        return out
    except Exception:
        return []


def download(url, timeout=25):
    """이미지 URL → bytes (실패 시 예외)."""
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.content
