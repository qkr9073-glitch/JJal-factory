# -*- coding: utf-8 -*-
"""게시물 URL → 제목/본문/댓글/이미지 추출기 (디시·루리웹·에펨 전용 + 범용 폴백)"""
import hashlib
import io
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
TIMEOUT = 25
MIN_W, MIN_H = 220, 220  # 아이콘/이모티콘 걸러내는 최소 크기


def normalize_url(url: str) -> str:
    url = url.strip().strip('"').strip("'")
    # 디시 모바일 → 데스크톱
    m = re.match(r"https?://m\.dcinside\.com/board/([^/?#]+)/(\d+)", url)
    if m:
        return f"https://gall.dcinside.com/board/view/?id={m.group(1)}&no={m.group(2)}"
    url = url.replace("://m.ruliweb.com", "://bbs.ruliweb.com")
    url = url.replace("://m.fmkorea.com", "://www.fmkorea.com")
    return url


def fetch_html(url: str, referer: str | None = None) -> BeautifulSoup:
    headers = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5"}
    if referer:
        headers["Referer"] = referer
    r = requests.get(url, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    # 웃대 등 charset 헤더 없는 옛날 사이트 → 실제 인코딩 추정 (EUC-KR 깨짐 방지)
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding or "utf-8"
    return BeautifulSoup(r.text, "html.parser")


def _og(soup, prop):
    tag = soup.find("meta", attrs={"property": f"og:{prop}"})
    return (tag.get("content") or "").strip() if tag else ""


def _img_srcs(container, base_url):
    """컨테이너 안의 이미지 URL 수집 (lazyload 속성 포함)"""
    urls = []
    for img in container.find_all("img"):
        src = (img.get("data-original") or img.get("data-src")
               or img.get("data-lazy-src") or img.get("src") or "")
        src = src.strip()
        if not src or src.startswith("data:"):
            continue
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = urljoin(base_url, src)
        if not src.startswith("http"):
            continue
        urls.append(src)
    # 순서 유지하며 중복 제거
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _clean_text(container, limit=2500):
    text = container.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:limit]


# ── 사이트별 추출기 ──────────────────────────────────────────

def _dc_comments(url, soup, limit=15):
    """디시 댓글 AJAX(board/comment/)로 실제 댓글 '내용'을 수집.
    페이지의 e_s_n_o 토큰이 필요. 실패하면 빈 리스트(본문 생성은 정상 진행)."""
    try:
        m = re.search(r"[?&]id=([^&]+)&no=(\d+)", url)
        if not m:
            return []
        gid, no = m.group(1), m.group(2)
        esno_el = soup.select_one("#e_s_n_o")
        esno = esno_el.get("value") if esno_el else ""
        gt = re.search(r"_GALLTYPE_\s*[=:]\s*['\"]([^'\"]+)", str(soup))
        gtype = gt.group(1) if gt else "G"
        payload = {"id": gid, "no": no, "cmt_id": gid, "cmt_no": no,
                   "e_s_n_o": esno, "comment_page": "1", "sort": "", "_GALLTYPE_": gtype}
        r = requests.post("https://gall.dcinside.com/board/comment/", data=payload,
                          headers={"User-Agent": UA, "Referer": url,
                                   "X-Requested-With": "XMLHttpRequest",
                                   "Accept": "application/json, text/javascript, */*; q=0.01"},
                          timeout=TIMEOUT)
        out = []
        for c in (r.json().get("comments") or []):
            memo = str(c.get("memo", ""))
            if "<img" in memo.lower():          # 디시콘(스티커) 댓글 제외
                continue
            txt = re.sub(r"<[^>]+>", " ", memo)              # HTML 태그 제거
            txt = re.sub(r"\s+", " ", txt).strip()           # 공백 먼저 정리
            txt = re.sub(r"^@\S+\s*", "", txt).strip()       # @닉(아이피) 멘션 제거
            if len(txt) >= 2:
                out.append(txt[:150])                        # 너무 긴 댓글은 컷
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []


def extract_dc(url, soup):
    title_el = soup.select_one("span.title_subject")
    title = title_el.get_text(strip=True) if title_el else _og(soup, "title")
    content = soup.select_one("div.write_div")
    if content is None:
        raise RuntimeError("디시 본문(write_div)을 찾지 못했습니다")
    return {
        "site": "dcinside",
        "title": title,
        "text": _clean_text(content),
        "comments": _dc_comments(url, soup),   # 실제 댓글 내용 (AJAX)
        "image_urls": _img_srcs(content, url),
        "referer": "https://gall.dcinside.com/",
    }


def extract_ruliweb(url, soup):
    title = _og(soup, "title")
    if not title:
        el = soup.select_one("span.subject_text, .subject_inner_text")
        title = el.get_text(strip=True) if el else ""
    content = soup.select_one("div.view_content")
    if content is None:
        raise RuntimeError("루리웹 본문(view_content)을 찾지 못했습니다")
    comments = [c.get_text(" ", strip=True)
                for c in soup.select(".comment_view .text, .comment_element .text,"
                                     " .comment_table .text_wrapper")][:10]
    return {
        "site": "ruliweb",
        "title": title,
        "text": _clean_text(content),
        "comments": [c for c in comments if c][:10],
        "image_urls": _img_srcs(content, url),
        "referer": "https://bbs.ruliweb.com/",
    }


def extract_fmkorea(url, soup):
    title = _og(soup, "title")
    content = soup.select_one('div[class*="document_"][class*="xe_content"]')
    if content is None:
        content = soup.select_one("article div.xe_content, div.rd_body")
    if content is None:
        raise RuntimeError("에펨 본문(xe_content)을 찾지 못했습니다")
    comments = [c.get_text(" ", strip=True)
                for c in soup.select('div[class*="comment_"][class*="xe_content"]')][:10]
    return {
        "site": "fmkorea",
        "title": title,
        "text": _clean_text(content),
        "comments": [c for c in comments if c][:10],
        "image_urls": _img_srcs(content, url),
        "referer": "https://www.fmkorea.com/",
    }


def extract_generic(url, soup):
    title = _og(soup, "title") or (soup.title.get_text(strip=True) if soup.title else "")
    title = re.sub(r"\s*[-|·]\s*DogDrip\.Net.*$", "", title)
    title = re.sub(r"^더쿠\s*-\s*", "", title).strip()
    desc = _og(soup, "description")
    # 본문 후보: article > 특정 클래스 > body 전체
    content = (soup.select_one("#powerbbsContent")  # 인벤
               or soup.select_one("#memo_content_1, .memo_content")  # 인스티즈
               or soup.select_one("#wrap_body, #cnts")  # 웃긴대학
               or soup.select_one(".xe_content, .view_content, .board_view, #article_1")
               or soup.select_one("article")
               or soup.body or soup)
    image_urls = _img_srcs(content, url)
    og_image = _og(soup, "image")
    if og_image and og_image not in image_urls:
        image_urls.append(og_image)
    return {
        "site": urlparse(url).netloc,
        "title": title,
        "text": (desc + "\n" + _clean_text(content, 1500)).strip(),
        "comments": [],
        "image_urls": image_urls,
        "referer": url,
    }


def extract(url: str) -> dict:
    url = normalize_url(url)
    host = urlparse(url).netloc
    if "dcinside.com" in host:
        soup = fetch_html(url, "https://gall.dcinside.com/")
        data = extract_dc(url, soup)
    elif "ruliweb.com" in host:
        soup = fetch_html(url, "https://bbs.ruliweb.com/")
        data = extract_ruliweb(url, soup)
    elif "fmkorea.com" in host:
        soup = fetch_html(url, "https://www.fmkorea.com/")
        data = extract_fmkorea(url, soup)
    else:
        soup = fetch_html(url)
        data = extract_generic(url, soup)
    data["url"] = url
    return data


# ── 이미지 다운로드 ──────────────────────────────────────────

def download_images(image_urls, referer, dest_dir, max_images=10):
    """이미지 내려받고 작은 아이콘 제거, 전부 JPG로 통일. 저장된 경로 리스트 반환."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": UA, "Referer": referer,
               "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"}
    saved, hashes = [], set()
    for u in image_urls:
        if len(saved) >= max_images:
            break
        try:
            r = requests.get(u, headers=headers, timeout=TIMEOUT)
            r.raise_for_status()
            raw = r.content
            h = hashlib.md5(raw).hexdigest()
            if h in hashes:
                continue
            img = Image.open(io.BytesIO(raw))
            if getattr(img, "is_animated", False):
                img.seek(0)  # 움짤은 첫 프레임만
            img = img.convert("RGB")
            if img.width < MIN_W or img.height < MIN_H:
                continue
            hashes.add(h)
            path = dest / f"{len(saved) + 1:02d}.jpg"
            img.save(path, "JPEG", quality=92)
            saved.append(str(path))
        except Exception:
            continue  # 이미지 하나 실패는 무시하고 계속
    return saved
