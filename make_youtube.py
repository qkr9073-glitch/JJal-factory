# -*- coding: utf-8 -*-
"""유튜브 → 짤 파이프라인 검증 CLI (통합 전 테스트용).

사용법:
  python make_youtube.py <유튜브 URL> [--mock] [--interval 0.7]

동작: 메타/썸네일 → (병렬) Gemini 대본·구성안 + 비디오 다운로드 → 프레임 풀 추출
      → 카드별 프레임 배정 → out/youtube_test/<id>/preview.html 로 결과 확인.
"""
import json
import sys
import threading
import time
import webbrowser
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from src import youtube  # noqa: E402


def esc(s):
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if not args:
        print(__doc__)
        return 1
    url = args[0]
    mock = "--mock" in flags
    interval = 0.7
    for f in flags:
        if f.startswith("--interval"):
            try:
                interval = float(f.split("=")[1])
            except Exception:
                pass
    cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8"))

    T0 = time.time()
    print(f"[1] 메타 조회...")
    meta = youtube.fetch_meta(url)
    print(f"    제목='{meta['title']}'  길이={meta['duration']}s  채널={meta['uploader']}")

    out = BASE / "out" / "youtube_test" / (meta["id"] or "vid")
    out.mkdir(parents=True, exist_ok=True)
    vid = out / "video.mp4"

    # 썸네일 저장
    try:
        youtube.save_thumbnail(meta["thumbnail"], out / "thumb.jpg")
    except Exception as e:
        print(f"    (썸네일 저장 실패: {e})")

    # 병렬: 대본(Gemini) + 다운로드
    result = {}
    errors = {}

    def _transcript():
        t = time.time()
        try:
            result["outline"] = youtube.transcribe_and_outline(
                cfg, url, duration=meta["duration"], mock=mock)
            print(f"[2] 대본·구성안 완료 ({time.time() - t:.1f}s) — 카드 {len(result['outline'].get('cards', []))}장")
        except Exception as e:
            errors["outline"] = e
            print(f"[2] 대본 실패: {e}")

    def _download():
        t = time.time()
        try:
            youtube.download_video(url, str(vid))
            print(f"[3] 다운로드 완료 ({time.time() - t:.1f}s)")
        except Exception as e:
            errors["download"] = e
            print(f"[3] 다운로드 실패: {e}")

    th = [threading.Thread(target=_transcript), threading.Thread(target=_download)]
    print("[2/3] 대본 + 다운로드 병렬 시작...")
    for t in th:
        t.start()
    for t in th:
        t.join()

    if "outline" not in result:
        print("대본 생성 실패로 중단.")
        return 1
    outline = result["outline"]

    # 프레임 풀 + 배정
    pool = []
    if vid.exists():
        print("[4] 프레임 풀 추출 + 선명도 필터...")
        try:
            pool = youtube.extract_frame_pool(str(vid), str(out / "pool"), interval=interval)
            youtube.assign_frames(outline["cards"], pool)
        except Exception as e:
            print(f"    프레임 추출 실패(템플릿 폴백 대상): {e}")
    else:
        print("[4] 영상 없음 → 프레임 없이 템플릿 폴백 경로")

    # 미리보기 HTML
    html = _render_preview(meta, outline, pool, out)
    (out / "preview.html").write_text(html, encoding="utf-8")
    print(f"\n[완료] 총 {time.time() - T0:.1f}s")
    print(f"       {out / 'preview.html'}")
    if "--no-open" not in flags:
        webbrowser.open((out / "preview.html").as_uri())
    return 0


def _render_preview(meta, outline, pool, out):
    def rel(p):
        try:
            return Path(p).resolve().relative_to(out.resolve()).as_posix()
        except Exception:
            return Path(p).name
    hooks = outline.get("hooks", [])
    cards = outline.get("cards", [])
    cardhtml = ""
    for i, c in enumerate(cards):
        frame = c.get("frame")
        img = f'<img src="{rel(frame)}">' if frame else '<div class="noimg">템플릿 생성 예정</div>'
        cardhtml += f"""<div class="card">
          <div class="thumb">{img}<div class="ov">{esc(c.get('text'))}</div>
            <span class="t">{c.get('t', 0):.1f}s</span></div>
          <div class="ci">뒷장 {i + 1}</div></div>"""
    poolhtml = "".join(
        f'<img src="{rel(p["path"])}" title="{p["t"]:.1f}s · 선명도 {p["sharp"]:.0f}">'
        for p in pool[:40])
    hookhtml = "".join(
        f'<div class="hook"><b>{esc(h.get("line1"))}</b><br>{esc(h.get("line2"))}</div>'
        for h in hooks)
    return f"""<!doctype html><meta charset="utf-8"><title>유튜브 짤 미리보기</title>
<style>
body{{background:#0e1016;color:#e9ebf2;font-family:'Malgun Gothic',sans-serif;margin:0;padding:22px}}
h1{{font-size:18px}} h2{{font-size:14px;color:#8bffcf;margin:22px 0 8px}}
.meta{{color:#9aa0b3;font-size:13px;margin-bottom:6px}}
.hooks{{display:flex;gap:10px;flex-wrap:wrap}}
.hook{{background:#1c2029;border:1px solid #2a3f38;border-radius:10px;padding:12px 14px;font-size:14px;min-width:150px}}
.hook b{{color:#34d399;font-size:15px}}
.cards{{display:flex;gap:14px;flex-wrap:wrap}}
.card{{width:190px}}
.thumb{{position:relative;aspect-ratio:4/5;border-radius:10px;overflow:hidden;background:#000;border:1px solid #2a2f3c}}
.thumb img{{width:100%;height:100%;object-fit:cover}}
.ov{{position:absolute;left:0;right:0;bottom:0;padding:10px;font-weight:800;font-size:15px;
    background:linear-gradient(0deg,rgba(0,0,0,.8),transparent);white-space:pre-wrap;line-height:1.3}}
.t{{position:absolute;top:6px;right:8px;background:rgba(0,0,0,.6);font-size:11px;padding:2px 7px;border-radius:20px}}
.noimg{{width:100%;height:100%;display:flex;align-items:center;justify-content:center;color:#6b7284;font-size:13px}}
.ci{{font-size:12px;color:#9aa0b3;margin-top:6px;text-align:center}}
.pool{{display:flex;gap:5px;flex-wrap:wrap}}
.pool img{{width:66px;aspect-ratio:9/16;object-fit:cover;border-radius:5px;border:1px solid #2a2f3c}}
pre{{background:#161922;border:1px solid #2a2f3c;border-radius:10px;padding:14px;white-space:pre-wrap;font-size:13.5px;line-height:1.6}}
</style>
<h1>▶️ {esc(meta['title'])}</h1>
<div class="meta">채널 {esc(meta['uploader'])} · 길이 {meta['duration']}s · 원어 {esc(outline.get('lang_src'))}
 {'· ⚠️ skip 판정: ' + esc(outline.get('skip_reason')) if outline.get('skip') else ''}</div>
<img src="thumb.jpg" style="width:200px;border-radius:10px">
<h2>① 대표 썸네일 후킹 3안</h2><div class="hooks">{hookhtml}</div>
<h2>② 뒷장 (대본 시점별 프레임 자동 배정 · 🎲 리롤 예정)</h2><div class="cards">{cardhtml}</div>
<h2>③ 대본</h2><pre>{esc(outline.get('transcript'))}</pre>
<h2>④ 인스타 캡션</h2><pre>{esc(outline.get('caption'))}</pre>
<h2>⑤ 프레임 풀 (선명도 필터 통과 {len(pool)}장)</h2><div class="pool">{poolhtml}</div>
"""


if __name__ == "__main__":
    sys.exit(main())
