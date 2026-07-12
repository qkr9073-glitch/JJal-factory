# -*- coding: utf-8 -*-
"""소재 수집기 — 6개 커뮤니티 베스트에서 인기글을 긁어 후보 목록 생성
필드: site, category, title, url, views, recs, replies, age_min(경과분, 정렬용)"""
import json
import re
from datetime import datetime
from pathlib import Path

from .extractors import fetch_html

BIG_AGE = 99999  # 시간 파싱 실패 시 (최신순에서 뒤로)


def _num(s):
    digits = re.sub(r"[^\d]", "", (s or "").split("/")[0])
    return int(digits) if digits else 0


def _txt(el):
    return el.get_text(" ", strip=True) if el else ""


def _parse_age(text):
    """'3분 전'/'2 시간 전'/'15:04'/'07.05'/'2026.07.05' → 대략 몇 분 전인지"""
    t = (text or "").strip()
    if not t:
        return BIG_AGE
    m = re.search(r"(\d+)\s*분", t)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*시간", t)
    if m:
        return int(m.group(1)) * 60
    m = re.search(r"(\d+)\s*일", t)
    if m:
        return int(m.group(1)) * 1440
    now = datetime.now()
    m = re.match(r"^(\d{1,2}):(\d{2})$", t)  # 오늘 HH:MM
    if m:
        mins = (now.hour - int(m.group(1))) * 60 + (now.minute - int(m.group(2)))
        return max(0, mins)
    m = re.match(r"^(?:(\d{4})[.\-/])?(\d{1,2})[.\-/](\d{1,2})\.?$", t)  # [YYYY.]MM.DD
    if m:
        y = int(m.group(1) or now.year)
        try:
            d = datetime(y, int(m.group(2)), int(m.group(3)))
            return max(0, int((now - d).total_seconds() // 60))
        except ValueError:
            return BIG_AGE
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", t)  # YYYY-MM-DD( HH:MM)
    if m:
        try:
            d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return max(0, int((now - d).total_seconds() // 60))
        except ValueError:
            return BIG_AGE
    return BIG_AGE


def _item(site, category, title, url, views=0, recs=0, replies=0, age=BIG_AGE):
    return {"site": site, "category": category or "기타", "title": title,
            "url": url, "views": views, "recs": recs, "replies": replies,
            "age_min": age}


# ── 사이트별 수집 ────────────────────────────────────────────

def hunt_dc(n):
    soup = fetch_html("https://gall.dcinside.com/board/lists/?id=dcbest",
                      "https://gall.dcinside.com/")
    out = []
    for tr in soup.select("tr.ub-content"):
        if "icon_notice" in (tr.get("data-type") or ""):
            continue
        a = tr.select_one("td.gall_tit a")
        if not a:
            continue
        m = re.search(r"id=([^&]+)&(?:amp;)?no=(\d+)", a.get("href", ""))
        if not m:
            continue
        title = _txt(a)
        cat = ""
        cm = re.match(r"^\[(.{1,8}?)\]", title)
        if cm:
            cat = cm.group(1)
            title = title[cm.end():].strip()
        date_el = tr.select_one("td.gall_date")
        age = _parse_age(date_el.get("title") or _txt(date_el)) if date_el else BIG_AGE
        out.append(_item(
            "디시실베", cat, title,
            f"https://gall.dcinside.com/board/view/?id={m.group(1)}&no={m.group(2)}",
            views=_num(_txt(tr.select_one("td.gall_count"))),
            recs=_num(_txt(tr.select_one("td.gall_recommend"))),
            replies=_num(_txt(tr.select_one(".reply_num")).strip("[]")),
            age=age))
    out.sort(key=lambda x: -x["recs"])
    return out[:n]


def hunt_ruliweb(n):
    soup = fetch_html("https://bbs.ruliweb.com/best/humor",
                      "https://bbs.ruliweb.com/")
    out, seen_urls = [], set()
    for tr in soup.select("tr.table_body"):
        a = tr.select_one("a.subject_link") or tr.select_one(".subject a")
        if not a:
            continue
        href = (a.get("href") or "").split("?")[0]
        if href.startswith("/"):
            href = "https://bbs.ruliweb.com" + href
        if "/best/board/" not in href or href in seen_urls:
            continue
        seen_urls.add(href)
        title = re.sub(r"\(\d+\)\s*$", "", _txt(a)).strip()
        out.append(_item(
            "루리웹", "유머", title, href,
            views=_num(_txt(tr.select_one(".hit"))),
            recs=_num(_txt(tr.select_one(".recomd"))),
            replies=_num(_txt(tr.select_one(".num_reply")).strip("()")),
            age=_parse_age(_txt(tr.select_one(".time")))))
    out.sort(key=lambda x: -x["recs"])
    return out[:n]


def hunt_fmkorea(n):
    soup = fetch_html("https://www.fmkorea.com/best",
                      "https://www.fmkorea.com/")
    out, seen_urls = [], set()
    for li in soup.select('li[class*="li_best2"]'):
        if "li_best2_politics1" in " ".join(li.get("class") or []):
            continue  # 에펨이 직접 달아주는 정치글 태그
        title_el = li.select_one("h3.title .ellipsis-target")
        link = li.select_one("h3.title a")
        if not title_el or not link:
            continue
        m = re.search(r"document_srl=(\d+)", link.get("href", "")) \
            or re.match(r"^/(\d{7,})$", link.get("href", ""))
        if not m or m.group(1) in seen_urls:
            continue
        seen_urls.add(m.group(1))
        cat = _txt(li.select_one("span.category a")).strip("/")
        out.append(_item(
            "에펨포텐", cat, _txt(title_el),
            f"https://www.fmkorea.com/{m.group(1)}",
            recs=_num(_txt(li.select_one(".pc_voted_count .count"))),
            replies=_num(_txt(li.select_one(".comment_count")).strip("[]")),
            age=_parse_age(_txt(li.select_one("span.regdate")))))
    out.sort(key=lambda x: -x["recs"])
    return out[:n]


def hunt_dogdrip(n):
    soup = fetch_html("https://www.dogdrip.net/dogdrip",
                      "https://www.dogdrip.net/")
    out, seen_urls, seen_titles = [], set(), set()
    for a in soup.select("a.ed.title-link"):
        url = (a.get("href") or "").split("?")[0]
        if url.startswith("/"):
            url = "https://www.dogdrip.net" + url
        if not re.search(r"/\d{6,}$", url) or url in seen_urls:
            continue
        # 공지 영역/중복 제목 스킵
        anc, is_notice = a, False
        for _ in range(6):
            anc = anc.parent
            if anc is None:
                break
            if "notice" in " ".join(anc.get("class") or []):
                is_notice = True
                break
        title = _txt(a)
        if is_notice or title in seen_titles:
            continue
        seen_titles.add(title)
        seen_urls.add(url)
        replies = _num(_txt(a.find_next_sibling("span")))
        recs, age = 0, BIG_AGE
        meta = a
        for _ in range(4):  # 상위 컨테이너에서 추천수·시간 찾기
            meta = meta.parent
            if meta is None:
                break
            lm = meta.select_one(".list-meta")
            if lm:
                nums = [_num(_txt(s)) for s in lm.select("span.text-primary")]
                recs = max(nums) if nums else 0
                clock = lm.find("i", class_="fa-clock")
                if clock and clock.parent:
                    age = _parse_age(_txt(clock.parent))
                break
        out.append(_item("개드립", "개드립", title, url,
                         recs=recs, replies=replies, age=age))
    out.sort(key=lambda x: -x["recs"])
    return out[:n]


def hunt_theqoo(n):
    soup = fetch_html("https://theqoo.net/hot", "https://theqoo.net/")
    out, seen_urls = [], set()
    for tr in soup.select("table tbody tr"):
        if "notice" in " ".join(tr.get("class") or []):
            continue
        a = tr.select_one("td.title a")
        cate = _txt(tr.select_one("td.cate"))
        if not a or cate in ("공지", "이벤트"):
            continue
        href = (a.get("href") or "").split("#")[0].split("?")[0]
        if not re.search(r"/hot/\d+", href) or href in seen_urls:
            continue
        seen_urls.add(href)
        out.append(_item(
            "더쿠", cate, _txt(a), "https://theqoo.net" + href,
            views=_num(_txt(tr.select_one("td.m_no"))),
            replies=_num(_txt(tr.select_one("a.replyNum"))),
            age=_parse_age(_txt(tr.select_one("td.time")))))
    out.sort(key=lambda x: -x["views"])
    return out[:n]


def hunt_inven(n):
    soup = fetch_html("https://www.inven.co.kr/board/webzine/2097",
                      "https://www.inven.co.kr/")
    out, seen_urls = [], set()
    for tr in soup.select("table tbody tr"):
        if "notice" in " ".join(tr.get("class") or []):
            continue
        a = tr.select_one("a.subject-link")
        if not a:
            continue
        if not tr.select_one(".con-icon.photo, .con-icon.board-img"):
            continue  # 사진 없는 글은 짤 재료가 안 됨
        url = (a.get("href") or "").split("?")[0]
        if url in seen_urls:
            continue
        seen_urls.add(url)
        cat = _txt(tr.select_one("span.category")).strip("[]")
        title = re.sub(r"^\[.+?\]\s*", "", _txt(a))
        out.append(_item(
            "인벤", cat or "오픈이슈", title, url,
            views=_num(_txt(tr.select_one("td.view"))),
            recs=_num(_txt(tr.select_one("td.reco"))),
            replies=_num(_txt(tr.select_one(".con-comment")).strip("[]")),
            age=_parse_age(_txt(tr.select_one("td.date")))))
    out.sort(key=lambda x: -x["views"])
    return out[:n]


def hunt_instiz(n):
    soup = fetch_html("https://www.instiz.net/pt", "https://www.instiz.net/")
    out, seen_urls = [], set()
    for tr in soup.select("table tr"):
        if "notice" in " ".join(tr.get("class") or []):
            continue
        sub = tr.select_one("td.listsubject")
        if not sub:
            continue
        a = sub.select_one("a[href]")
        if not a:
            continue
        href = (a.get("href") or "").split("?")[0]
        m = re.search(r"/pt/(\d+)$", href)
        if not m or href in seen_urls:
            continue
        seen_urls.add(href)
        listnos = [_txt(td) for td in tr.select("td.listno")]
        age = _parse_age(listnos[0]) if listnos else BIG_AGE
        views = _num(listnos[1]) if len(listnos) > 1 else 0
        replies = _num(_txt(sub.select_one('span[class^="cmt"]')))
        for s in a.select('span[class^="cmt"]'):
            s.extract()  # 제목 끝에 댓글수 섞이는 것 방지
        out.append(_item(
            "인스티즈", "이슈", _txt(a), "https://www.instiz.net" + href
            if href.startswith("/") else href,
            views=views, replies=replies, age=age))
    out.sort(key=lambda x: -x["views"])
    return out[:n]


def hunt_humoruniv(n):
    soup = fetch_html("http://web.humoruniv.com/board/humor/list.html?table=pds",
                      "http://web.humoruniv.com/")
    out, seen_urls = [], set()
    for a in soup.select('a[href*="read.html?table=pds"]'):
        href = a.get("href") or ""
        m = re.search(r"number=(\d+)", href)
        if not m or m.group(1) in seen_urls:
            continue
        tr = a.find_parent("tr")
        if tr is None:
            continue
        raw = _txt(a)
        title = re.sub(r"\s*\[\d+\].*$", "", raw).strip()  # [댓글수] 이후 꼬리표 제거
        if not title:
            continue  # 같은 글의 썸네일 링크 (제목 링크가 따로 옴)
        if re.match(r"^19\s", title):
            continue  # 성인 마크 글은 인스타 소재로 부적합
        seen_urls.add(m.group(1))
        unds = [_num(_txt(td)) for td in tr.select("td.li_und")]
        rm = re.search(r"\[(\d+)\]", raw)
        out.append(_item(
            "웃긴대학", "웃대", title,
            f"http://web.humoruniv.com/board/humor/read.html?table=pds&number={m.group(1)}",
            views=unds[0] if unds else 0,
            recs=unds[1] if len(unds) > 1 else 0,
            replies=int(rm.group(1)) if rm else 0,
            age=_parse_age(_txt(tr.select_one("td.li_date")).split(" ")[0])))
    out.sort(key=lambda x: -x["recs"])
    return out[:n]


# ── seen(제작 이력) ──────────────────────────────────────────

def load_seen(base_dir):
    p = Path(base_dir) / "seen.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def mark_seen(base_dir, url, when):
    p = Path(base_dir) / "seen.json"
    seen = load_seen(base_dir)
    seen[url] = when
    p.write_text(json.dumps(seen, ensure_ascii=False, indent=1), encoding="utf-8")


# ── 통합 ────────────────────────────────────────────────────

# 정치/민감 소재는 목록에서 제외 (config.json "block_keywords" 로 덮어쓰기 가능)
DEFAULT_BLOCK = ["이재명", "윤석열", "대통령", "민주당", "국민의힘", "국힘", "한동훈",
                 "이진숙", "나경원", "이준석", "조국", "김문수", "선거", "국회", "의원",
                 "장관", "탄핵", "북한", "극우", "극좌", "일베",
                 "페미", "정치", "시위", "집회",
                 "살인", "사망", "자살", "참사", "성폭행", "성범죄", "학대"]

SOURCES = [hunt_dc, hunt_ruliweb, hunt_fmkorea, hunt_dogdrip, hunt_theqoo,
           hunt_inven, hunt_instiz, hunt_humoruniv]


def hunt(base_dir, per_site=12, block_keywords=None):
    """6개 커뮤 인기글 수집. 정치 키워드 제외, 제작된 글은 used 표시."""
    block = block_keywords if block_keywords is not None else DEFAULT_BLOCK
    seen = load_seen(base_dir)
    columns = []
    for fn in SOURCES:
        try:
            items = fn(per_site * 2)
            items = [it for it in items
                     if not any(k in it["title"] for k in block)][:per_site]
            columns.append(items)
        except Exception:
            columns.append([])
    merged = []
    for i in range(per_site):
        for col in columns:
            if i < len(col):
                item = col[i]
                item["used"] = item["url"] in seen
                merged.append(item)
    return merged
