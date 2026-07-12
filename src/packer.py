# -*- coding: utf-8 -*-
"""완성팩 패키저 — 폴더 정리 + caption.txt + meta.json + review.html 생성"""
import json
import re
import shutil
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path

REVIEW_TEMPLATE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body{{background:#111;color:#eee;font-family:'Malgun Gothic',sans-serif;max-width:640px;margin:0 auto;padding:20px}}
h1{{font-size:18px}} a{{color:#7ab8ff}}
img{{max-width:100%;border-radius:12px;margin:8px 0;display:block}}
pre{{white-space:pre-wrap;background:#1c1c1f;padding:16px;border-radius:12px;font-family:inherit;font-size:15px;line-height:1.6}}
button{{background:#4a7dff;color:#fff;border:0;padding:14px 22px;border-radius:10px;font-size:16px;cursor:pointer;font-weight:bold}}
.badge{{display:inline-block;background:#333;padding:4px 10px;border-radius:8px;font-size:12px;margin-right:6px}}
.warn{{background:#5c2b2b;padding:12px;border-radius:10px}}
</style></head><body>
<h1>📦 {title}</h1>
<p><span class="badge">{site}</span><a href="{url}" target="_blank">원본 글 열기</a>
&nbsp;|&nbsp; <a href="{zip_name}" download>⬇ zip 다운로드 (폰 전송용)</a></p>
{skip_html}
<h2>1) 썸네일 — 마음에 드는 걸로 첫 장 (후보 {num_thumbs}개)</h2>
{thumbs_html}
<h2>2) 본문 캡션 <button onclick="copyCap()">📋 캡션 복사</button></h2>
<pre id="cap">{caption}</pre>
<h2>3) 나머지 짤 (순서대로 올리기)</h2>
{images_html}
<script>
function copyCap(){{navigator.clipboard.writeText(document.getElementById('cap').innerText)
.then(()=>alert('캡션이 복사됐습니다!'));}}
</script></body></html>"""


def _slug(title, limit=18):
    s = re.sub(r"[^0-9A-Za-z가-힣]+", "_", title or "").strip("_")
    return s[:limit] or "post"


def build_pack(output_root, meta, image_paths, thumb_paths, caption_full):
    """완성팩 폴더 확정 + 부속 파일 생성. 팩 경로 반환.
    thumb_paths: 썸네일 후보 리스트 (1번이 대표, thumb.jpg / thumb2.jpg / ...)"""
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    pack = root / f"{datetime.now():%Y%m%d_%H%M}_{_slug(meta.get('title'))}"
    n = 1
    while pack.exists():
        n += 1
        pack = root / f"{datetime.now():%Y%m%d_%H%M}_{_slug(meta.get('title'))}_{n}"
    pack.mkdir()

    # 이미지 배치: thumb.jpg + 01.jpg, 02.jpg ...
    final_images = []
    for i, p in enumerate(image_paths, 1):
        p = Path(p)
        dst = pack / f"{i:02d}.jpg"
        if p != dst:
            shutil.move(str(p), str(dst))
        final_images.append(dst)
    final_thumbs = []
    for i, tp in enumerate(thumb_paths):
        dst = pack / ("thumb.jpg" if i == 0 else f"thumb{i + 1}.jpg")
        if Path(tp) != dst:
            shutil.move(str(tp), str(dst))
        final_thumbs.append(dst)

    (pack / "caption.txt").write_text(caption_full, encoding="utf-8")
    (pack / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    skip_html = ""
    if meta.get("skip"):
        skip_html = (f'<p class="warn">⚠️ AI 판정: 올리지 않는 게 좋습니다 — '
                     f'{meta.get("skip_reason", "")}</p>')
    # 전송용 zip (폰으로 한 방에 보내기: 썸네일 후보들+짤+캡션)
    zip_path = pack / f"{pack.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for t in final_thumbs:
            zf.write(t, t.name)
        for p in final_images:
            zf.write(p, p.name)
        zf.write(pack / "caption.txt", "caption.txt")

    hooks = meta.get("hooks") or []
    thumbs_html = "\n".join(
        f'<p><b>후보 {i + 1}</b> — {h.get("line1", "")} / {h.get("line2", "")}</p>'
        f'<img src="{t.name}">'
        for i, (t, h) in enumerate(zip(final_thumbs, hooks + [{}] * 3)))
    images_html = "\n".join(f'<img src="{p.name}">' for p in final_images)
    html = REVIEW_TEMPLATE.format(
        title=meta.get("title") or "완성팩",
        site=meta.get("site", ""),
        url=meta.get("url", "#"),
        zip_name=zip_path.name,
        num_thumbs=len(final_thumbs),
        thumbs_html=thumbs_html,
        skip_html=skip_html,
        caption=(caption_full.replace("&", "&amp;").replace("<", "&lt;")),
        images_html=images_html,
    )
    (pack / "review.html").write_text(html, encoding="utf-8")
    return pack


def copy_to_clipboard(text):
    try:
        subprocess.run(["clip"], input=text.encode("utf-16"), check=True,
                       creationflags=0x08000000)
        return True
    except Exception:
        return False
