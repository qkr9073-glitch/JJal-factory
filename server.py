# -*- coding: utf-8 -*-
"""짤공장 직원용 웹서버 — 링크/인기글 선택 → 사장님 PC가 완성팩 제작 (진행률 게이지 지원)"""
import base64
import json
import queue
import re
import shutil
import sys
import threading
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import insta  # noqa: E402
from cardnews import news as card_news  # noqa: E402
from cardnews import pipeline as card_pipeline  # noqa: E402
from src import brain, hunter, insights, pipeline, stock, storycard, styles, thumbnail, youtube  # noqa: E402
from src import insta_import  # noqa: E402

app = Flask(__name__)


@app.after_request
def _no_cache_html(resp):
    """HTML 페이지는 캐시 금지 — 직원 PC가 옛 버전을 붙들고 있어 새 기능이 안 먹는 문제 방지.
    (이미지/폰트 등 정적 파일은 각 라우트의 max_age 캐시 유지)"""
    try:
        if resp.mimetype == "text/html":
            resp.headers["Cache-Control"] = "no-store, must-revalidate"
            resp.headers["Pragma"] = "no-cache"
    except Exception:
        pass
    return resp


def _output_dir():
    """완성팩 저장 폴더 — config "output_dir"로 변경 가능 (파이프라인과 공유)"""
    try:
        cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
        return BASE / cfg.get("output_dir", "결과물")
    except Exception:
        return BASE / "결과물"


OUTPUT = _output_dir()
JOBQ = queue.Queue()  # 제작 대기줄 — 동시 2개 처리, 나머지는 순서대로 자동 시작


def _job_worker():
    while True:
        jid, fn, args = JOBQ.get()
        job = JOBS.get(jid)
        if not job or job["status"] != "queued":
            continue
        job["status"] = "running"
        job["pct"] = 5
        try:
            fn(*args)
        except Exception as e:
            job["error"] = str(e)
            job["status"] = "error"


try:
    _WORKERS = int(json.loads((BASE / "config.json").read_text(encoding="utf-8"))
                   .get("max_workers", 4))
except Exception:
    _WORKERS = 4
for _ in range(max(1, _WORKERS)):
    threading.Thread(target=_job_worker, daemon=True).start()

# ── 대기열 디스크 보존: 서버가 재시작돼도 눌러둔 지시가 증발하지 않게 ──
PENDING_F = BASE / "jobs_pending.json"
_PENDING_LOCK = threading.Lock()


def _pending_load():
    try:
        return json.loads(PENDING_F.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _pending_add(jid, url):
    with _PENDING_LOCK:
        p = _pending_load()
        p[jid] = {"url": url, "ts": time.time()}
        PENDING_F.write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")


def _pending_remove(jid):
    with _PENDING_LOCK:
        p = _pending_load()
        if jid in p:
            p.pop(jid)
            PENDING_F.write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
HUNT_CACHE = {"time": 0.0, "data": []}  # 인기글 목록 10분 캐시
NEWS_CACHE = {"time": 0.0, "data": []}  # 최신 소식 30분 캐시
INSIGHT_CACHE = {"time": 0.0, "data": None}  # AI 인사이트 30분 캐시
JOBS = {}  # job_id -> {status, pct, msg, result, error, ts}
STEP_PCT = {1: 15, 2: 45, 3: 80, 4: 92}

INDEX_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>짤공장</title>
<link rel="icon" type="image/png" href="/logo-origami.png">
<link rel="apple-touch-icon" href="/logo-origami.png">
<meta name="theme-color" content="#0a1310">
<style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
button,select,.chip,.nav a{touch-action:manipulation}
@font-face{font-family:'Pretendard';font-weight:400;font-display:swap;src:url('/fonts/Pretendard-Regular.otf') format('opentype')}
@font-face{font-family:'Pretendard';font-weight:600;font-display:swap;src:url('/fonts/Pretendard-SemiBold.otf') format('opentype')}
@font-face{font-family:'Pretendard';font-weight:800;font-display:swap;src:url('/fonts/Pretendard-ExtraBold.otf') format('opentype')}
:root{--gold:#2fd39a;--gold2:#6ee7b7;--ink:#e9f1ec;--line:#25392f;--panel:#14211b;--panel2:#0f1a15}
html{-webkit-text-size-adjust:100%}
body{background:radial-gradient(1100px 480px at 50% -10%,#16463a 0%,rgba(22,70,58,0) 62%),
linear-gradient(180deg,#0a1310 0%,#0f1c17 40%,#12211b 100%);min-height:100vh;
color:var(--ink);font-family:'Pretendard','Malgun Gothic',sans-serif;max-width:560px;margin:0 auto;padding:20px;
-webkit-font-smoothing:antialiased}
.brand{display:flex;align-items:center;gap:18px;margin:6px 0 10px}
.brand img{height:96px;filter:drop-shadow(0 8px 22px rgba(150,130,255,.38));box-shadow:none;margin:0;border-radius:0}
h1{font-size:27px;margin:0;color:var(--gold2);letter-spacing:2px;font-weight:800}
.sub{color:#8b93b8;font-size:13px;margin:6px 0 0;line-height:1.5}
input{width:100%;padding:14px 16px;border-radius:12px;border:1px solid var(--line);background:var(--panel2);color:var(--ink);font-size:16px;margin:6px 0;outline:none;transition:border-color .2s,box-shadow .2s;font-family:inherit}
input:focus{border-color:var(--gold);box-shadow:0 0 0 3px rgba(47,211,154,.18)}
input::placeholder{color:#5d7268}
button{width:100%;background:linear-gradient(180deg,#3ee0a6,#16b083);color:#08231a;border:0;padding:16px;border-radius:12px;font-size:17px;font-weight:800;cursor:pointer;margin-top:8px;font-family:inherit;transition:transform .12s,filter .2s;box-shadow:0 6px 16px rgba(0,0,0,.35)}
button:hover{filter:brightness(1.06)} button:active{transform:translateY(1px)}
button:disabled{background:#3a4166;color:#777f9f;box-shadow:none}
#hot{background:#222b4f;color:var(--ink)}
#status{margin:14px 0;color:var(--gold2);font-size:15px;white-space:pre-wrap}
.bar{background:#222a4a;border-radius:10px;height:16px;margin:10px 0;overflow:hidden;display:none;border:1px solid var(--line)}
.bar>div{background:linear-gradient(90deg,#16b083,#6ee7b7);height:100%;width:0%;transition:width .7s;border-radius:10px}
img{max-width:100%;border-radius:14px;margin:8px 0;display:block;box-shadow:0 8px 22px rgba(0,0,0,.35)}
pre{white-space:pre-wrap;background:var(--panel);padding:16px;border-radius:14px;font-family:inherit;font-size:15px;line-height:1.65;color:#e9e4d2;border:1px solid #262f55}
.small{background:var(--panel);padding:12px;font-size:14px;border-radius:12px;border:1px solid #262f55}
.small img{box-shadow:none}
.hookline{color:#9db8ff;font-size:14px;margin:12px 0 0}
a{color:#8fb6ff} h2{font-size:17px;margin-top:26px;color:var(--gold2)}
.warn{background:#5c2b2b;padding:12px;border-radius:12px}
.rthumbs{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin:10px 0}
.rth{border:3px solid #3a4166;border-radius:14px;cursor:pointer;padding:3px;position:relative;background:#171e38;transition:border-color .12s,transform .1s}
.rth:hover{border-color:#2fd39a;transform:translateY(-2px)}
.rth.sel{border-color:#2fd39a;box-shadow:0 0 0 3px rgba(47,211,154,.3)}
.rth img{width:100%;border-radius:9px;margin:0;display:block}
.rthc{text-align:center;font-size:12px;font-weight:700;padding:6px 2px 2px;color:#9aa3c8;line-height:1.3}
.rth.sel .rthc{color:#f2cf6b}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 0}
.chip{background:var(--panel);border:1px solid var(--line);color:#c5cbe6;padding:9px 14px;border-radius:20px;font-size:14px;cursor:pointer;user-select:none;transition:border-color .15s}
.chip:hover{border-color:var(--gold)}
.chip.on{background:var(--gold);border-color:var(--gold);color:#1a1a2e;font-weight:700}
.tlink{color:#cfe0ff;text-decoration:none} .tlink:active{color:#8fb6ff}
.nav{display:flex;gap:6px;margin:14px 0 18px;background:rgba(23,30,56,.65);padding:6px;border-radius:14px;border:1px solid var(--line)}
.nav a{flex:1;text-align:center;padding:11px 6px;border-radius:10px;color:#c5cbe6;text-decoration:none;font-size:14px;font-weight:700;transition:background .15s,color .15s}
.nav a.on{background:var(--gold);color:#1a1a2e}
body::before{content:'';position:fixed;inset:0;z-index:-1;pointer-events:none;background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='170' height='170'><g fill='none' stroke='%23e8b640' stroke-width='2'><path d='M42 26l9 9-9 9-9-9z' stroke-opacity='.09'/><path d='M126 118l6.5 6.5-6.5 6.5-6.5-6.5z' stroke-opacity='.05'/></g><path d='M124 34l2.4 6.2 6.2 2.4-6.2 2.4-2.4 6.2-2.4-6.2-6.2-2.4 6.2-2.4z' fill='%23e8b640' fill-opacity='.08'/><circle cx='36' cy='130' r='2.2' fill='%23e8b640' fill-opacity='.07'/><circle cx='88' cy='78' r='1.6' fill='%238fb6ff' fill-opacity='.06'/></svg>")}
body::after{content:'';position:fixed;inset:0;z-index:-1;pointer-events:none;opacity:.05;background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2'/><feColorMatrix type='saturate' values='0'/></filter><rect width='160' height='160' filter='url(%23n)'/></svg>")}
.deco{position:fixed;pointer-events:none;z-index:-1;color:var(--gold)}
@keyframes twinkle{0%,100%{opacity:.12;transform:scale(1) rotate(0deg)}50%{opacity:.4;transform:scale(1.15) rotate(22deg)}}
.foot{text-align:center;margin:52px 0 12px;font-size:12px;color:#5d6690;letter-spacing:.5px}
@media(max-width:430px){body{padding:14px}.brand img{height:74px}h1{font-size:23px}
.nav a{font-size:13px;padding:11px 3px}button{padding:15px;font-size:16px}}
</style></head><body>
<svg class="deco" style="right:26px;top:120px;animation:twinkle 7s ease-in-out infinite" width="26" height="26" viewBox="0 0 24 24" fill="currentColor"><path d="M12 1l2.4 8.6L23 12l-8.6 2.4L12 23l-2.4-8.6L1 12l8.6-2.4z"/></svg>
<svg class="deco" style="left:14px;bottom:90px;animation:twinkle 9s 2s ease-in-out infinite" width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M12 1l2.4 8.6L23 12l-8.6 2.4L12 23l-2.4-8.6L1 12l8.6-2.4z"/></svg>
<div class="brand"><img src="/logo-origami.png" alt="logo"><div>
<h1>짤공장</h1><div class="sub">커뮤니티 인기글 → 인스타 완성팩 (썸네일 3종 + 짤 + 본문)</div>
</div></div>
<div class="nav"><a class="on" href="/">🏭 짤공장</a><a href="/card">🗂 카드뉴스</a><a href="/p">📦 결과물</a></div>
<input id="code" placeholder="접속코드" type="password">
<input id="url" placeholder="링크 붙여넣기 — 커뮤니티(디시/루리웹/에펨) 또는 🎬 유튜브 쇼츠">
<div style="font-size:14px;font-weight:700;margin:10px 0 2px;color:#f0ead8">🎨 템플릿 <span style="font-weight:400;font-size:12px;color:#9aa3c8">— 눌러서 미리보기로 고르세요</span></div>
<div id="tpltiles" class="rthumbs"></div>
<div style="font-size:14px;font-weight:700;margin:12px 0 2px;color:#f0ead8">🧹 짤에 박힌 글씨 <span style="font-weight:400;font-size:12px;color:#9aa3c8">— 자막·워터마크가 있으면</span></div>
<select id="clean" onchange="localStorage.setItem('mfclean',this.value)">
<option value="none" selected>그대로 두기 (0원)</option>
<option value="bar">하단/상단 바로 가리기 (0원)</option>
<option value="ai">AI로 완전 제거 (글씨 있는 장만 · 장당 약 55원)</option>
</select>
<input id="guide" placeholder="🧭 (선택) 본문·썸네일 방향 힌트 — 예: 댓글 반응 위주로, 반전 강조, 담백하게" title="비우면 자동. 적으면 이 방향이 최우선으로 반영돼요 (인기글 클릭 전에 적어두세요)">
<button id="go" onclick="make()">완성팩 만들기</button>
<button id="hot" onclick="loadHot()">🔥 지금 인기글 불러오기 (선택만 하면 제작)</button>
<button id="packsbtn" onclick="location.href='/p'" style="background:#222b4f;color:#f0ead8">📦 결과물 보기 (썸네일 선택·수정·업로드)</button>
<button id="ftgl" type="button" onclick="toggleF()" style="background:#1f6f52;color:#fff">🌏 해외 인기글 → 한국 짤로 변환</button>
<div id="fbox" style="display:none;border:1px dashed #6fc0a0;background:rgba(31,111,82,.14);border-radius:12px;padding:12px;margin:6px 0">
<div style="font-size:13px;color:#a8d8c4;line-height:1.55;margin-bottom:8px">미국·일본 등 해외에서 터진 게시물 <b>캡처(여러 장)</b>를 올리면, AI가 이미지 속 외국어를 읽어 <b>한국 커뮤 감성으로 후킹·본문을 새로 써서</b> 짤 완성팩(썸네일 3종+짤+본문)으로 만들어줘요. 캡션 원문 있으면 더 정확.</div>
<label style="display:inline-block;cursor:pointer;background:#2a3350;color:#f0ead8;padding:10px 14px;border-radius:10px;font-size:14px;font-weight:700">📎 해외 게시물 캡처 올리기 (여러 장)
<input id="ffile" type="file" accept="image/*" multiple style="display:none" onchange="fUpload(this)"></label>
<div id="fthumbs" style="display:flex;gap:6px;flex-wrap:wrap;margin:8px 0"></div>
<textarea id="fcap" placeholder="해외 게시물 캡션 원문 붙여넣기 (선택)" style="width:100%;min-height:54px;border-radius:10px;border:1px solid #3a4166;background:#1a2038;color:#f0ead8;padding:9px;font-family:inherit;font-size:14px;margin:2px 0 8px"></textarea>
<button id="fgo" type="button" onclick="makeForeign()" style="background:#1f6f52">🌏 한국 짤로 변환하기 (1~2분)</button>
</div>
<button id="mtgl" type="button" onclick="toggleM()" style="background:#7a3aa8;color:#fff">🇯🇵 한국 스토리 → 일본판 이관 (kangaroostory.jp)</button>
<div id="mbox" style="display:none;border:1px dashed #b08fd0;background:rgba(122,58,168,.14);border-radius:12px;padding:12px;margin:6px 0">
<div style="font-size:13px;color:#d9c4ee;line-height:1.55;margin-bottom:8px">우리 <b>한국 스토리래빗 옛 게시물</b>을 일본 계정(kangaroostory.jp)으로 옮겨요. <b>완성된 카드(사진+글)를 통째로 올려도 돼요</b> — 시스템이 <b>사진 영역만 자동으로 뽑아냅니다</b>. 카드를 순서대로 올리고 <b>한국어 본문</b>을 붙여넣으면 → 옛 워터마크는 우리 걸로 덮고, 본문을 <b>자연스러운 일본어</b>로 바꿔 카드 + 캡션까지 자동 생성. (스토리팩이라 자동으로 kangaroostory.jp로만 올라감)</div>
<label style="display:inline-block;cursor:pointer;background:#3a2350;color:#f0ead8;padding:10px 14px;border-radius:10px;font-size:14px;font-weight:700">📎 옛 카드/사진 올리기 (순서대로, 여러 장)
<input id="mfile" type="file" accept="image/*" multiple style="display:none" onchange="mUpload(this)"></label>
<div id="mthumbs" style="display:flex;gap:6px;flex-wrap:wrap;margin:8px 0"></div>
<textarea id="mcap" placeholder="한국어 본문(옛 게시물 글) 전체를 붙여넣기 — 이 글이 일본어로 바뀌어요" style="width:100%;min-height:96px;border-radius:10px;border:1px solid #3a4166;background:#1a2038;color:#f0ead8;padding:9px;font-family:inherit;font-size:14px;margin:2px 0 8px"></textarea>
<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px">
<span style="font-size:13px;color:#d9c4ee">사진 속 옛 워터마크/글씨 처리:</span>
<select id="mclean" style="flex:1;min-width:160px">
<option value="none" selected>옛 워터마크는 우리 워터마크로 덮기 (0원 · 추천)</option>
<option value="ai">배경에 다른 글씨도 있으면 AI로 완전 제거 (장당 약 55원)</option>
<option value="bar">하단 바로 가리기 (0원)</option>
</select></div>
<button id="mgo" type="button" onclick="makeMigrate()" style="background:#7a3aa8">🇯🇵 일본판으로 이관하기 (1~2분)</button>
</div>
<button id="rtgl" type="button" onclick="toggleR()" style="background:#c2502a;color:#fff">🎬 영상(릴스) 자동 올리기</button>
<div id="rbox" style="display:none;border:1px dashed #e08a5a;background:rgba(194,80,42,.12);border-radius:12px;padding:12px;margin:6px 0">
<div style="font-size:13px;color:#e6b39a;line-height:1.55;margin-bottom:8px">이미 만든 <b>영상(MP4)</b>을 올리면 인스타 <b>릴스로 자동 게시</b>돼요. 영상은 서버가 잠깐 공개 링크로 인스타에 넘겨주고 <b>게시 후 삭제</b>합니다. (용량 큰 영상은 넘기는 데 시간이 좀 걸려요)</div>
<label style="display:inline-block;cursor:pointer;background:#3a2318;color:#f0ead8;padding:10px 14px;border-radius:10px;font-size:14px;font-weight:700">📎 영상 파일 고르기 (MP4)
<input id="rfile" type="file" accept="video/mp4,video/quicktime,video/*" style="display:none" onchange="rPick(this)"></label>
<div id="rname" style="font-size:13px;color:#e6b39a;margin:8px 0"></div>
<textarea id="rcap" placeholder="릴스 캡션 — 직접 쓰거나, 아래 ✨로 AI가 영상 보고 써줘요 (여기에 방향을 적고 ✨ 누르면 그 느낌으로)" style="width:100%;box-sizing:border-box;min-height:70px;border-radius:10px;border:1px solid #5a3a2a;background:#241a14;color:#f0ead8;padding:9px;font-family:inherit;font-size:14px;margin:2px 0 8px"></textarea>
<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px">
<span style="font-size:13px;color:#e6b39a">올릴 계정:</span>
<select id="racct" style="flex:1;min-width:150px">
<option value="sowho77">@sowho77 (짤공장)</option>
<option value="kangarooshort">@kangarooshort (카드뉴스)</option>
<option value="kangaroostory.jp">@kangaroostory.jp (일본)</option>
</select></div>
<button id="rcapgo" type="button" onclick="capReel()" style="background:#7a5cc2;margin-bottom:6px">✨ 영상 보고 AI가 캡션 써주기 (계정 톤 자동 · 일본계정=일본어)</button>
<button id="rgo" type="button" onclick="uploadReel()" style="background:#c2502a">🎬 릴스로 자동 게시</button>
</div>
<div id="status"></div>
<div id="jobs"></div>
<div class="bar" id="bar" style="display:none"><div id="fill"></div></div>
<div id="result"></div>
<div id="hotctrl" style="display:none">
  <div class="chips" id="siteChips"></div>
  <div class="chips" id="catChips"></div>
  <div class="chips" id="sortChips"></div>
</div>
<div id="hotlist"></div>
<div class="foot">⚙ 오늘도 무사히 공장 가동 중 — 짤은 성실하게, 업로드는 꾸준하게</div>
<script>
const $=id=>document.getElementById(id);
$('code').value = localStorage.getItem('mfcode')||'';
const esc=s=>s.replace(/&/g,'&amp;').replace(/</g,'&lt;');

let HOT=[], FLT={site:'전체', cat:'전체', sort:'recs'};
const SORTS=[['recs','👍 추천순'],['views','👀 조회순'],['replies','💬 댓글순'],['age_min','🕐 최신순'],['debate','🔥 떡밥순']];

async function loadHot(){
  const code=$('code').value.trim();
  if(!code){$('status').textContent='접속코드를 먼저 입력하세요';return;}
  localStorage.setItem('mfcode',code);
  $('hot').disabled=true; $('hot').textContent='⏳ 커뮤니티 8곳 훑는 중... (첫 로딩은 30초쯤)';
  try{
    const r=await fetch('/api/candidates',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({code})});
    const d=await r.json();
    if(!d.ok){$('status').textContent='❌ '+d.error;}
    else{HOT=d.items; buildChips(); renderHot(); $('hotctrl').style.display='block'; $('status').textContent='';}
  }catch(e){$('status').textContent='❌ 통신 오류: '+e;}
  $('hot').disabled=false; $('hot').textContent='🔥 지금 인기글 불러오기 (선택만 하면 제작)';
}
function chip(label,on,fn){const s=document.createElement('span');s.className='chip'+(on?' on':'');
  s.textContent=label;s.onclick=fn;return s;}
function buildChips(){
  const sites=['전체',...new Set(HOT.map(i=>i.site))];
  const cats=['전체',...[...new Set(HOT.map(i=>i.category))].slice(0,30)];
  const sc=$('siteChips'), cc=$('catChips'), oc=$('sortChips');
  sc.innerHTML=cc.innerHTML=oc.innerHTML='';
  sites.forEach(s=>sc.appendChild(chip(s, FLT.site===s, ()=>{FLT.site=s; buildChips(); renderHot();})));
  cats.forEach(c=>cc.appendChild(chip(c, FLT.cat===c, ()=>{FLT.cat=c; buildChips(); renderHot();})));
  SORTS.forEach(([k,l])=>oc.appendChild(chip(l, FLT.sort===k, ()=>{FLT.sort=k; buildChips(); renderHot();})));
}
function renderHot(){
  let list=HOT.filter(i=>(FLT.site==='전체'||i.site===FLT.site)&&(FLT.cat==='전체'||i.category===FLT.cat));
  list.sort((a,b)=>FLT.sort==='age_min'?a.age_min-b.age_min:(b[FLT.sort]||0)-(a[FLT.sort]||0));
  let h='';
  list.forEach(it=>{
    const parts=[];
    if(it.recs) parts.push('추천 '+it.recs);
    if(it.views) parts.push('조회 '+it.views.toLocaleString());
    if(it.replies) parts.push('댓글 '+it.replies);
    if(it.age_min<99999) parts.push(it.age_min<60?it.age_min+'분전':Math.round(it.age_min/60)+'시간전');
    if(it.debate) parts.push('🔥떡밥 '+it.debate);
    if(it.used) parts.push('✅ 제작됨');
    const fire=(it.debate>=7)?'🔥':'';
    const used=it.used?' style="opacity:.55"':'';
    const btn=it.used
      ?`<button style="width:auto;padding:8px 14px;font-size:14px;margin:0;background:#3a4166;color:#a8afd0" onclick="pick('${it.url}',this)">다시 만들기</button>`
      :`<button style="width:auto;padding:8px 14px;font-size:14px;margin:0" onclick="pick('${it.url}',this)">만들기</button>`;
    h+=`<div${used} class="small" style="margin:8px 0;display:flex;gap:10px;align-items:center">
    <div style="flex:1"><b>[${it.site}·${esc(it.category)}]</b> <a class="tlink" href="${it.url}" target="_blank" rel="noopener">${fire}${esc(it.title)} ↗</a><br><span style="color:#7d86ad">${parts.join(' · ')}</span></div>
    <div>${btn}</div></div>`;
  });
  $('hotlist').innerHTML=h||'<p style="color:#7d86ad">조건에 맞는 글이 없어요</p>';
}
function pick(u,btn){$('url').value=u; make(btn);}

const TPLS=[['classic','🏭 클래식 (게시글 헤더)'],['plain','🖼 자막형 (헤더 없음)'],['story','📖 스토리카드']];
let TPL=localStorage.getItem('mftpl')||'classic';
function renderTplTiles(){
  $('tpltiles').innerHTML=TPLS.map(([v,n])=>
    `<div class="rth${TPL===v?' sel':''}" onclick="pickTpl('${v}')"><img src="/memeprev/${v}"><div class="rthc">${TPL===v?'✅ '+n:n}</div></div>`
  ).join('');
}
function pickTpl(v){TPL=v;localStorage.setItem('mftpl',v);renderTplTiles();}
renderTplTiles();
$('clean').value = localStorage.getItem('mfclean')||'none';
const CLEAN=()=>$('clean').value;

let FIMGS=[];
function toggleF(){const b=$('fbox');const o=b.style.display==='none';b.style.display=o?'block':'none';
  $('ftgl').textContent=o?'🌏 해외 변환 닫기 ▲':'🌏 해외 인기글 → 한국 짤로 변환';}
function fThumbs(){
  $('fthumbs').innerHTML=FIMGS.map((u,i)=>
    `<div style="position:relative"><img src="${u}" style="width:66px;height:82px;object-fit:cover;border-radius:8px;display:block;margin:0">`+
    `<div onclick="fDel(${i})" style="position:absolute;top:-7px;right:-7px;width:20px;height:20px;background:#c2502a;color:#fff;border-radius:50%;text-align:center;line-height:20px;cursor:pointer;font-size:12px">×</div></div>`
  ).join('');
}
function fDel(i){FIMGS.splice(i,1);fThumbs();}
function fUpload(input){
  const files=[...input.files]; input.value='';
  files.forEach(f=>{
    const rd=new FileReader();
    rd.onload=e=>{
      const img=new Image();
      img.onload=()=>{
        const m=1280, s=Math.min(1, m/Math.max(img.width,img.height));
        const c=document.createElement('canvas');
        c.width=Math.round(img.width*s); c.height=Math.round(img.height*s);
        c.getContext('2d').drawImage(img,0,0,c.width,c.height);
        FIMGS.push(c.toDataURL('image/jpeg',0.85)); fThumbs();
      };
      img.src=e.target.result;
    };
    rd.readAsDataURL(f);
  });
}
async function makeForeign(){
  const code=$('code').value.trim();
  if(!code){$('status').textContent='접속코드를 입력하세요';return;}
  if(!FIMGS.length){$('status').textContent='해외 게시물 캡처를 1장 이상 올려주세요';return;}
  localStorage.setItem('mfcode',code);
  $('fgo').disabled=true; $('status').textContent='';
  try{
    const r=await fetch('/api/make_images',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({code,images:FIMGS,caption:$('fcap').value.trim(),localize:true,template:TPL,clean:CLEAN(),guide:(($('guide')&&$('guide').value)||'').trim()})});
    const d=await r.json();
    if(!d.ok){$('status').textContent='❌ '+d.error; $('fgo').disabled=false; return;}
    addJobRow(d.job,'🌏 해외→한국 짤');
    FIMGS=[]; fThumbs(); $('fcap').value='';
  }catch(e){$('status').textContent='❌ 통신 오류: '+e;}
  $('fgo').disabled=false;
}

let MIMGS=[];
function toggleM(){const b=$('mbox');const o=b.style.display==='none';b.style.display=o?'block':'none';
  $('mtgl').textContent=o?'🇯🇵 이관 닫기 ▲':'🇯🇵 한국 스토리 → 일본판 이관 (kangaroostory.jp)';}
function mThumbs(){
  $('mthumbs').innerHTML=MIMGS.map((u,i)=>
    `<div style="position:relative"><img src="${u}" style="width:66px;height:82px;object-fit:cover;border-radius:8px;display:block;margin:0">`+
    `<div onclick="mDel(${i})" style="position:absolute;top:-7px;right:-7px;width:20px;height:20px;background:#7a3aa8;color:#fff;border-radius:50%;text-align:center;line-height:20px;cursor:pointer;font-size:12px">×</div>`+
    `<div style="position:absolute;bottom:2px;left:3px;background:rgba(0,0,0,.6);color:#fff;font-size:10px;padding:0 4px;border-radius:6px">${i+1}</div></div>`
  ).join('');
}
function mDel(i){MIMGS.splice(i,1);mThumbs();}
function mUpload(input){
  const files=[...input.files]; input.value='';
  files.forEach(f=>{
    const rd=new FileReader();
    rd.onload=e=>{
      const img=new Image();
      img.onload=()=>{
        const m=1280, s=Math.min(1, m/Math.max(img.width,img.height));
        const c=document.createElement('canvas');
        c.width=Math.round(img.width*s); c.height=Math.round(img.height*s);
        c.getContext('2d').drawImage(img,0,0,c.width,c.height);
        MIMGS.push(c.toDataURL('image/jpeg',0.85)); mThumbs();
      };
      img.src=e.target.result;
    };
    rd.readAsDataURL(f);
  });
}
async function makeMigrate(){
  const code=$('code').value.trim();
  if(!code){$('status').textContent='접속코드를 입력하세요';return;}
  if(!MIMGS.length){$('status').textContent='카드에 넣을 사진을 1장 이상 올려주세요';return;}
  if(!$('mcap').value.trim()){$('status').textContent='한국어 본문을 붙여넣어 주세요';return;}
  localStorage.setItem('mfcode',code);
  $('mgo').disabled=true; $('status').textContent='';
  try{
    const r=await fetch('/api/make_images',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({code,images:MIMGS,caption:$('mcap').value.trim(),localize:false,template:'story',clean:$('mclean').value})});
    const d=await r.json();
    if(!d.ok){$('status').textContent='❌ '+d.error; $('mgo').disabled=false; return;}
    addJobRow(d.job,'🇯🇵 일본판 이관');
    MIMGS=[]; mThumbs(); $('mcap').value='';
  }catch(e){$('status').textContent='❌ 통신 오류: '+e;}
  $('mgo').disabled=false;
}

let QUEUED=0, DONE=0;
function refreshToast(){
  let t=$('qtoast');
  if(QUEUED===0){ if(t)t.remove(); return; }
  if(!t){
    t=document.createElement('div'); t.id='qtoast';
    t.onclick=()=>window.scrollTo({top:0,behavior:'smooth'});
    t.style.cssText='position:fixed;left:50%;transform:translateX(-50%);bottom:16px;z-index:60;'+
      'padding:11px 18px;border-radius:26px;font-size:14px;font-weight:700;color:#fff;'+
      'box-shadow:0 6px 20px rgba(0,0,0,.35);cursor:pointer;max-width:92%;text-align:center';
    document.body.appendChild(t);
  }
  const active=QUEUED-DONE;
  if(active>0){ t.style.background='#e2683c'; t.textContent=`⏳ ${active}개 제작 중${DONE?` · ${DONE} 완성`:''} — 진행상황 보기 ⬆`; }
  else{ t.style.background='#2f7d4f'; t.textContent=`✅ ${DONE}개 완성! 결과 보기 ⬆`; }
}

function setBar(pct){$('bar').style.display='block';$('fill').style.width=pct+'%';}
async function make(btn){
  const code=$('code').value.trim(), url=$('url').value.trim();
  if(!code||!url){$('status').textContent='접속코드와 링크를 입력하세요';return;}
  localStorage.setItem('mfcode',code);
  const yt=url.includes('youtube.com')||url.includes('youtu.be');
  const hit=HOT.find(i=>i.url===url);
  const label=(yt?'🎬 ':'')+((hit&&hit.title)||url.split('//').pop().slice(0,34));
  $('status').textContent='';
  if(btn){ btn.disabled=true; btn.dataset.orig=btn.textContent; btn.textContent='접수 중...'; btn.style.opacity='.8'; }
  try{
    const r=await fetch(yt?'/api/youtube/make':'/api/make',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(yt?{code,url}:{code,url,template:TPL,clean:CLEAN(),guide:(($('guide')&&$('guide').value)||'').trim()})});
    const d=await r.json();
    if(!d.ok){$('status').textContent='❌ '+d.error;
      if(btn){ btn.disabled=false; btn.textContent=btn.dataset.orig||'만들기'; btn.style.opacity=''; } return;}
    addJobRow(d.job,label); $('url').value='';
    if(btn){ btn.textContent='✅ 접수됨'; btn.style.background='#2f7d4f'; btn.style.color='#fff'; btn.style.opacity=''; }
    QUEUED++; refreshToast();
  }catch(e){$('status').textContent='❌ 통신 오류: '+e;
    if(btn){ btn.disabled=false; btn.textContent=btn.dataset.orig||'만들기'; btn.style.opacity=''; }}
}
let RVIDEO=null, RSTAGED=null;
function toggleR(){const b=$('rbox');const o=b.style.display==='none';b.style.display=o?'block':'none';$('rtgl').textContent=o?'🎬 영상 올리기 닫기 ▲':'🎬 영상(릴스) 자동 올리기';}
function rPick(inp){const f=inp.files&&inp.files[0];RVIDEO=f||null;RSTAGED=null;$('rname').textContent=f?('🎬 '+f.name+' ('+(f.size/1048576).toFixed(1)+'MB)'):'';}
async function capReel(){
  const code=$('code').value.trim();
  if(!code){$('status').textContent='접속코드를 먼저 입력하세요';return;}
  if(!RVIDEO && !RSTAGED){$('status').textContent='영상 파일을 먼저 고르세요';return;}
  localStorage.setItem('mfcode',code);
  const fd=new FormData();
  fd.append('code',code); fd.append('account',$('racct').value);
  fd.append('hint',($('rcap').value||'').trim());
  if(RSTAGED) fd.append('video_name',RSTAGED); else fd.append('video',RVIDEO);
  $('rcapgo').disabled=true; $('status').textContent='✨ AI가 영상 보는 중... (업로드+분석 20~40초)';
  try{
    const r=await fetch('/api/reel/caption',{method:'POST',body:fd});
    const d=await r.json();
    if(!d.ok){$('status').textContent='❌ '+d.error;$('rcapgo').disabled=false;return;}
    capPoll(d.job);
  }catch(e){$('status').textContent='❌ 통신 오류: '+e;$('rcapgo').disabled=false;}
}
async function capPoll(jid){
  const code=$('code').value.trim();
  try{
    const r=await fetch(`/api/job/${jid}?code=${encodeURIComponent(code)}`);
    const d=await r.json();
    if(!d.ok||d.status==='error'){$('status').textContent='❌ '+(d.error||'실패');$('rcapgo').disabled=false;return;}
    if(d.status==='done'){
      const res=d.result||{};
      if(res.caption) $('rcap').value=res.caption;
      if(res.video) RSTAGED=res.video;
      $('status').textContent='✅ AI 캡션 완성 — 확인·수정하고 게시하세요';
      $('rcapgo').disabled=false; return;
    }
    $('status').textContent='✨ '+(d.msg||'영상 분석 중...');
    setTimeout(()=>capPoll(jid),1500);
  }catch(e){setTimeout(()=>capPoll(jid),2500);}
}
async function uploadReel(){
  const code=$('code').value.trim();
  if(!code){$('status').textContent='접속코드를 먼저 입력하세요';return;}
  if(!RVIDEO && !RSTAGED){$('status').textContent='영상 파일을 먼저 고르세요';return;}
  localStorage.setItem('mfcode',code);
  const acct=$('racct').value, vname=RVIDEO?RVIDEO.name:'영상';
  if(!confirm('이 영상을 @'+acct+' 릴스로 지금 게시할까요?\\n\\n'+vname)) return;
  const fd=new FormData();
  fd.append('code',code);
  fd.append('caption',($('rcap').value||'').trim()); fd.append('account',acct);
  if(RSTAGED) fd.append('video_name',RSTAGED); else fd.append('video',RVIDEO);
  $('rgo').disabled=true; $('status').textContent=RSTAGED?'📤 릴스 게시 접수 중...':'⬆ 영상 업로드 중... (용량 크면 좀 걸려요)';
  try{
    const r=await fetch('/api/reel/upload',{method:'POST',body:fd});
    const d=await r.json();
    if(!d.ok){$('status').textContent='❌ '+d.error;$('rgo').disabled=false;return;}
    addJobRow(d.job,'🎬 릴스: '+vname.slice(0,24));
    $('status').textContent='✅ 접수됨 — 인스타가 영상 처리 후 게시해요 (아래 진행상황)';
    QUEUED++; refreshToast();
    RVIDEO=null; RSTAGED=null; $('rfile').value=''; $('rname').textContent='';
  }catch(e){$('status').textContent='❌ 통신 오류: '+e;}
  $('rgo').disabled=false;
}
function addJobRow(jid,label){
  const div=document.createElement('div');
  div.className='small'; div.id='jr-'+jid; div.style.margin='8px 0'; div.style.borderLeft='4px solid #e8b640';
  div.innerHTML=`<b>${esc(label.slice(0,30))}</b>
  <div class="bar" style="display:block;height:10px;margin:6px 0"><div style="width:2%"></div></div>
  <span class="jmsg" style="color:#f2cf6b;font-size:13px">접수됨...</span>`;
  $('jobs').prepend(div);
  pollJob(jid);
}
async function pollJob(jid){
  const code=$('code').value.trim();
  const row=$('jr-'+jid); if(!row) return;
  const fill=row.querySelector('.bar>div'), msg=row.querySelector('.jmsg');
  try{
    const r=await fetch(`/api/job/${jid}?code=${encodeURIComponent(code)}`);
    const d=await r.json();
    if(!d.ok){msg.textContent='❌ '+d.error;return;}
    if(d.status==='queued'){
      const ahead=Math.max(0,(d.pos||1)-1);
      msg.textContent=ahead?`🕐 줄 서는 중 — 앞에 ${ahead}개 (차례 오면 자동 시작)`:'🕐 곧 시작...';
    }else{
      fill.style.width=(d.pct||5)+'%'; msg.textContent='⏳ '+(d.msg||'작업 중...');
    }
    if(d.status==='error'){msg.textContent='❌ '+d.error; DONE++; refreshToast(); return;}
    if(d.status==='done'){
      fill.style.width='100%';
      if(d.result && d.result.reel){
        const pl=d.result.permalink;
        msg.innerHTML=`✅ 릴스 게시 완료! @${d.result.account||''} `+(pl?`<a href="${pl}" target="_blank">게시물 보기 ↗</a>`:'');
        DONE++; refreshToast(); return;
      }
      const pk=d.result.pack;
      msg.innerHTML=`✅ 완성! <a href="/packs/${encodeURIComponent(pk)}/review.html" target="_blank">미리보기 ↗</a>`;
      renderResult(d.result);
      DONE++; refreshToast();
      return;
    }
    setTimeout(()=>pollJob(jid),1300);
  }catch(e){setTimeout(()=>pollJob(jid),2500);}
}
let RESLEAD=null, RESD=null;
function renderResult(d){
  RESD=d;
  let h='';
  if(d.skip) h+=`<p class="warn">⚠️ AI 참고 의견: ${d.skip_reason}<br>(팩은 정상 제작됨 — 올릴지는 직접 판단)</p>`;
  RESLEAD = d.thumbs.length ? d.thumbs[0].split('/').pop() : null;
  h+=`<h2 style="margin-top:8px">① 대표 썸네일 고르기 <span style="font-size:13px;font-weight:400;color:#9aa3c8">— 카드를 <b style="color:#f2cf6b">클릭</b>하면 그게 인스타 첫 장이 돼요`+(d.lang==='ja'?' · 밑에 🇰🇷 해석 보고 고르세요':'')+`</span></h2>`;
  h+='<div class="rthumbs">';
  d.thumbs.forEach((t,i)=>{
    const fn=t.split('/').pop(), on=(i===0);
    const hk=(d.hooks&&d.hooks[i])||{};
    const ko=hk.ko?`<div style="font-size:12px;color:#9aa3c8;margin-top:3px;line-height:1.35;word-break:keep-all">🇰🇷 ${esc(hk.ko)}</div>`:'';
    h+=`<div class="rth${on?' sel':''}" data-fn="${fn}" onclick="pickRes('${fn}')">
    <img src="${t}"><div class="rthc">${on?'✅ 첫 장으로 선택됨':'👆 클릭해서 선택'}</div>${ko}</div>`;
  });
  h+='</div>';
  if(d.story){
    h+=`<div style="background:var(--panel);border:1px solid #262f55;border-radius:12px;padding:12px;margin:10px 0">
      <div style="font-size:13px;color:#9aa3c8;margin-bottom:6px">헤드라인 3개 다 별로면 다시 뽑기 · 원하는 느낌 적으면 그 감성으로 (선택)</div>
      <input id="rhhintR" placeholder="예: 더 충격적으로 / '냥이의 보은' 감성으로" style="width:100%;box-sizing:border-box;margin-bottom:6px;padding:8px;border-radius:8px;border:1px solid #3a4166;background:#1a2038;color:#f0ead8;font-family:inherit">
      <button style="width:auto;padding:8px 14px;font-size:14px" onclick="reHeadRes('${d.pack}')">🔄 헤드라인 다시 뽑기</button></div>`;
  }
  if(d.story && (d.srcs||[]).length>1){
    h+=`<h2>커버 사진 바꾸기 <span style="font-size:13px;font-weight:400;color:#9aa3c8">— 순서 헷갈릴 때 커버 사진을 골라요 (헤드라인·본문 유지)</span></h2>`;
    h+='<div style="display:flex;gap:8px;flex-wrap:wrap">';
    d.srcs.forEach(s=>{const sf=s.split('/').pop(); const on=d.thumb_src===sf||(!d.thumb_src&&sf==='src00.jpg');
      h+=`<img class="rcov" data-fn="${sf}" src="${s}" onclick="reCoverRes('${s}','${d.pack}')" title="이 사진을 커버로" style="width:78px;height:78px;object-fit:cover;border-radius:10px;cursor:pointer;border:3px solid ${on?'var(--gold)':'#3a4166'};margin:0;box-shadow:none">`;});
    h+='</div>';
  }
  h+=`<button onclick="pubPack('${d.pack}')">📤 이 썸네일로 인스타 자동 업로드</button>`;
  h+=`<p class="small">⬇ <a href="${d.zip}" download><b>zip 한 번에 받기</b></a> — 폰에 저장 후 압축 풀고 업로드<br>또는 아래 이미지 길게 눌러 저장</p>`;
  h+=`<h2>② 본문 <button style="width:auto;padding:8px 14px;font-size:14px" onclick="copyCap()">📋 복사</button></h2><pre id="cap">${esc(d.caption)}</pre>`;
  if(d.story && (d.cards_ko||[]).some(x=>x)){
    h+=`<h2>🇰🇷 본문 해석 <span style="font-size:13px;font-weight:400;color:#9aa3c8">— 일본어 본문의 뜻이에요. 오역·오류 확인</span></h2>`;
    d.cards_ko.forEach((ko,i)=>{ if(!ko) return;
      const label=i===0?'📖 커버(썸네일) 본문':('📄 '+i+'번 본문카드');
      h+=`<div style="background:var(--panel);border:1px solid #262f55;border-radius:12px;padding:12px 14px;margin:8px 0"><b style="color:var(--gold2)">${label}</b><div style="margin-top:4px;line-height:1.6;color:#e9e4d2;white-space:pre-wrap">${esc(ko)}</div></div>`;
    });
  }
  h+=`<h2>③ 짤 (순서대로)</h2>`;
  d.images.forEach(u=>{h+=`<img src="${u}">`;});
  $('result').innerHTML=h;
}
function pickRes(fn){
  RESLEAD=fn;
  document.querySelectorAll('#result .rth').forEach(el=>{
    const on=el.dataset.fn===fn;
    el.classList.toggle('sel',on);
    const c=el.querySelector('.rthc'); if(c) c.textContent=on?'✅ 첫 장으로 선택됨':'👆 클릭해서 선택';
  });
}
function copyCap(){navigator.clipboard.writeText($('cap').innerText).then(()=>alert('본문이 복사됐습니다!'));}
async function reCoverRes(srcUrl, pack){
  const fn=srcUrl.split('/').pop(), code=$('code').value.trim(), st=$('status');
  if(st) st.textContent='🖼 커버 사진 바꾸는 중...';
  try{
    const r=await fetch('/api/pack/rethumb',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code,pack,base:fn})});
    const d=await r.json();
    if(!d.ok){if(st)st.textContent='❌ '+d.error;return;}
    document.querySelectorAll('#result img').forEach(img=>{img.src=img.src.split('?')[0]+'?t='+Date.now();});
    document.querySelectorAll('#result .rcov').forEach(el=>{el.style.borderColor=(el.dataset.fn===(d.base||fn))?'var(--gold)':'#3a4166';});
    if(st)st.textContent=d.swapped?'✅ 커버 교체 — 옛 커버는 본문 사진으로 내려갔어요':'✅ 커버 사진 교체 완료';
  }catch(e){if(st)st.textContent='❌ 오류';}
}
async function reHeadRes(pack){
  const hint=($('rhhintR')&&$('rhhintR').value.trim())||'', st=$('status');
  if(st)st.textContent='🔄 헤드라인 다시 뽑는 중... (10~20초)';
  try{
    const r=await fetch('/api/pack/reheadline',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:$('code').value.trim(),pack,hint})});
    const d=await r.json();
    if(!d.ok){if(st)st.textContent='❌ '+d.error;return;}
    if(RESD){RESD.hooks=d.hooks; renderResult(RESD);
      document.querySelectorAll('#result .rth img').forEach(img=>{img.src=img.src.split('?')[0]+'?t='+Date.now();});}
    if(st)st.textContent='✅ 새 헤드라인 — ①에서 확인하고 고르세요';
  }catch(e){if(st)st.textContent='❌ 오류';}
}

async function pubPack(name, force){
  const code=$('code').value.trim();
  if(!code){$('status').textContent='접속코드를 먼저 입력하세요';return;}
  if(!force && !confirm('인스타그램에 지금 바로 업로드할까요?\\n\\n'+name)) return;
  const body={code,pack:name,force:!!force};
  if(RESLEAD) body.lead=RESLEAD;
  const r=await fetch('/api/insta/publish',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)});
  const d=await r.json();
  if(!d.ok){
    if((d.error||'').includes('이미 업로드')){
      if(confirm('이미 올렸던 팩이에요. 강제로 한 번 더 올릴까요?')) pubPack(name,true);
      return;
    }
    alert('❌ '+d.error);return;
  }
  pubPoll(d.job, code);
}
function pubPoll(jid, code){
  $('status').textContent='📤 인스타 업로드 중...';
  const t=setInterval(async()=>{
    try{
      const r=await fetch(`/api/job/${jid}?code=${encodeURIComponent(code)}`);
      const d=await r.json();
      if(!d.ok){clearInterval(t);$('status').textContent='❌ '+d.error;return;}
      $('status').textContent='📤 '+(d.msg||'업로드 중...');
      if(d.status==='error'){clearInterval(t);$('status').textContent='❌ 업로드 실패';alert('업로드 실패: '+d.error);}
      if(d.status==='done'){clearInterval(t);
        $('status').innerHTML='✅ 인스타 업로드 완료! '+(d.result&&d.result.permalink?`<a href="${d.result.permalink}" target="_blank"><b>게시물 열기 ↗</b></a>`:'');
        window.scrollTo({top:0,behavior:'smooth'});}
    }catch(e){}
  }, 2000);
}
</script></body></html>"""

CARD_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>카드뉴스 공장</title>
<link rel="icon" type="image/png" href="/logo-card.png">
<link rel="apple-touch-icon" href="/logo-card.png">
<meta name="theme-color" content="#fdf8f1">
<style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
button,select,.chip,.trend,.nav a{touch-action:manipulation}
@font-face{font-family:'Pretendard';font-weight:400;font-display:swap;src:url('/fonts/Pretendard-Regular.otf') format('opentype')}
@font-face{font-family:'Pretendard';font-weight:600;font-display:swap;src:url('/fonts/Pretendard-SemiBold.otf') format('opentype')}
@font-face{font-family:'Pretendard';font-weight:800;font-display:swap;src:url('/fonts/Pretendard-ExtraBold.otf') format('opentype')}
@font-face{font-family:'NeoDGM';font-display:swap;src:url('/fonts/neodgm.ttf') format('truetype')}
:root{--tc:#e2683c;--tc2:#c2502a;--ink:#2b2620;--line:#e6d2bd;--soft:#8a7a6c}
html{-webkit-text-size-adjust:100%}
body{background:radial-gradient(900px 420px at 50% -8%,#fff6ea 0%,rgba(255,246,234,0) 60%),
linear-gradient(180deg,#fdf8f1 0%,#f7ece1 100%);min-height:100vh;
color:var(--ink);font-family:'Pretendard','Malgun Gothic',sans-serif;max-width:560px;margin:0 auto;padding:20px;
-webkit-font-smoothing:antialiased}
h1{font-family:'NeoDGM','Pretendard',sans-serif;font-size:32px;margin:6px 0 0;color:var(--tc2);letter-spacing:0;font-weight:400}
.sub{color:var(--soft);font-size:13px;margin:8px 0 0;line-height:1.5}
input,select{width:100%;padding:14px 16px;border-radius:12px;border:1px solid var(--line);background:#fff;color:var(--ink);font-size:16px;margin:6px 0;outline:none;transition:border-color .2s,box-shadow .2s;font-family:inherit}
input:focus,select:focus{border-color:var(--tc);box-shadow:0 0 0 3px rgba(226,104,60,.15)}
input::placeholder{color:#b8a795}
button{width:100%;background:linear-gradient(180deg,#ea7a4d,#dd5f33);color:#fff;border:0;padding:16px;border-radius:12px;font-size:17px;font-weight:800;cursor:pointer;margin-top:8px;font-family:inherit;transition:transform .12s,filter .2s;box-shadow:0 6px 16px rgba(190,100,50,.28)}
button:hover{filter:brightness(1.05)} button:active{transform:translateY(1px)}
button:disabled{background:#d8c7b4;color:#a08d78;box-shadow:none}
#status{margin:14px 0;color:var(--tc2);font-size:15px;white-space:pre-wrap}
.bar{background:#f0e2d2;border-radius:10px;height:16px;margin:10px 0;overflow:hidden;display:none;border:1px solid var(--line)}
.bar>div{background:linear-gradient(90deg,#e2683c,#f2a05f);height:100%;width:0%;transition:width .7s;border-radius:10px}
img{max-width:100%;border-radius:14px;margin:8px 0;display:block;box-shadow:0 8px 22px rgba(150,90,40,.18)}
pre{white-space:pre-wrap;background:#fff;padding:16px;border-radius:14px;font-family:inherit;font-size:15px;line-height:1.65;border:1px solid #eeddcc}
.small{background:#fff;padding:12px;font-size:14px;border-radius:12px;border:1px solid #eeddcc;margin:8px 0}
a{color:var(--tc2)} h2{font-size:17px;margin-top:26px;color:var(--tc2)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 10px}
.chip{background:#fff;border:1px solid var(--line);color:#8a6a4e;padding:9px 14px;border-radius:20px;font-size:14px;cursor:pointer;user-select:none;transition:border-color .15s,background .15s}
.chip:hover{border-color:var(--tc)}
.chip.on{background:var(--tc);border-color:var(--tc);color:#fff;font-weight:700}
.trend{background:#fff;border:1px solid #eeddcc;border-radius:12px;padding:11px 13px;margin:7px 0;font-size:14px;line-height:1.5;cursor:pointer}
.trend:hover{border-color:var(--tc)}
.trend.on{border:2px solid var(--tc);background:#fff4ec}
.styrow{display:flex;gap:8px;overflow-x:auto;padding:4px 2px;margin:6px 0}
.styrow::-webkit-scrollbar{height:6px}
.sty{flex:0 0 auto;width:98px;background:#fff;border:2px solid var(--line);border-radius:12px;padding:6px;cursor:pointer;position:relative;text-align:center}
.sty.on{border-color:var(--tc);box-shadow:0 2px 10px rgba(194,80,42,.18)}
.sty img{width:100%;height:70px;object-fit:cover;border-radius:8px;margin:0;display:block}
.sty .nm{font-size:12px;font-weight:700;margin-top:5px;line-height:1.25;color:#3a2a20;word-break:keep-all}
.sty .x{position:absolute;top:-7px;right:-7px;width:20px;height:20px;border-radius:50%;background:#c2502a;color:#fff;font-size:12px;line-height:18px;text-align:center;border:2px solid #fff}
.sty.base{display:flex;flex-direction:column;justify-content:center;align-items:center;min-height:105px;color:#9a8a7a;font-size:12px;font-weight:700;line-height:1.4}
#themetiles{flex-wrap:wrap;overflow-x:visible}
#themetiles .sty{flex:0 0 calc(33.333% - 6px);width:calc(33.333% - 6px)}
#themetiles .sty img{height:auto;aspect-ratio:4/5}
.tdim{color:#b8a795;font-size:12px}
.row{display:flex;gap:8px}.row>*{flex:1}
.nav{display:flex;gap:6px;margin:14px 0 18px;background:rgba(255,255,255,.7);padding:6px;border-radius:14px;border:1px solid var(--line)}
.nav a{flex:1;text-align:center;padding:11px 6px;border-radius:10px;color:#8a6a4e;text-decoration:none;font-size:14px;font-weight:700;transition:background .15s,color .15s}
.nav a.on{background:var(--tc);color:#fff}
.brand{display:flex;align-items:center;gap:16px;margin:6px 0 0}
.brand img{height:92px;filter:drop-shadow(0 8px 18px rgba(190,120,60,.28));box-shadow:none;margin:0;border-radius:0}
body::before{content:'';position:fixed;inset:0;z-index:-1;pointer-events:none;background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='150' height='150'><g stroke='%23e2683c' stroke-width='2.4' stroke-linecap='round'><path d='M24 40h24M36 28v24M27.5 31.5l17 17M44.5 31.5l-17 17' stroke-opacity='.08'/><path d='M104 104h16M112 96v16M106.3 98.3l11.4 11.4M117.7 98.3l-11.4 11.4' stroke-opacity='.05'/></g><circle cx='118' cy='30' r='2.2' fill='%23e2683c' fill-opacity='.09'/><circle cx='30' cy='118' r='1.8' fill='%23e2683c' fill-opacity='.07'/></svg>")}
body::after{content:'';position:fixed;inset:0;z-index:-1;pointer-events:none;opacity:.04;background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='2'/><feColorMatrix type='saturate' values='0'/></filter><rect width='160' height='160' filter='url(%23n)'/></svg>")}
.deco{position:fixed;pointer-events:none;z-index:-1;color:rgba(226,104,60,.32)}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes spinr{to{transform:rotate(-360deg)}}
.foot{font-family:'NeoDGM','Pretendard',sans-serif;text-align:center;margin:52px 0 12px;font-size:14px;color:#c9a288;letter-spacing:.5px}
@media(max-width:430px){body{padding:14px}.brand img{height:70px}h1{font-size:26px}
.nav a{font-size:13px;padding:11px 3px}.row{flex-direction:column;gap:0}}
</style></head><body>
<svg class="deco" style="right:20px;top:14px;animation:spin 26s linear infinite" width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M12 2v20M2 12h20M4.9 4.9l14.2 14.2M19.1 4.9L4.9 19.1"/></svg>
<svg class="deco" style="left:12px;bottom:100px;animation:spinr 34s linear infinite" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M12 2v20M2 12h20M4.9 4.9l14.2 14.2M19.1 4.9L4.9 19.1"/></svg>
<div class="brand"><img src="/logo-card.png" alt="logo"><div>
<h1>카드뉴스 공장</h1>
<div class="sub">주제 한 줄 → 4:5 캐러셀 + 전자책 PDF + 댓글 유도 캡션</div>
</div></div>
<div class="nav"><a href="/">🏭 짤공장</a><a class="on" href="/card">🗂 카드뉴스</a><a href="/p">📦 결과물</a></div>
<input id="code" placeholder="접속코드" type="password">
<input id="topic" placeholder="주제 입력 (예: 쇼츠 조회수 터지는 후킹 문장)">
<div class="chips" id="modes">
<span class="chip on" data-m="normal">📚 일반 (전자책 포함)</span>
<span class="chip" data-m="proof">🧾 증빙 (후기·수익 캡처 삽입)</span>
<span class="chip" data-m="story">📖 내 스토리 (서사형)</span>
</div>
<button id="reftgl" type="button" onclick="toggleRef()" style="background:#fff;color:#0a7d5a;border:1px solid var(--line);box-shadow:none">🌏 해외 인기글 → 한국판으로 변환</button>
<div id="refbox" style="display:none;border:1px dashed #7fcfae;background:#f2fbf7;border-radius:12px;padding:11px;margin:6px 0">
<div class="tdim" style="margin-bottom:7px;line-height:1.5">미국 등 잘나가는 게시물의 <b>캐러셀 스샷</b>을 올리고(여러 장 OK), 캡션 원문이 있으면 붙여넣으세요. AI가 <b>소재만 뽑아 한국 타깃 새 카드</b>로 다시 만들어요. <b style="color:#0a7d5a">인스타 크롤 안 함 — 계정 안전.</b></div>
<label class="chip" style="cursor:pointer;display:inline-block">📎 해외 게시물 스샷 올리기 (여러 장)
<input id="reffile" type="file" accept="image/*" multiple style="display:none" onchange="refUpload(this)"></label>
<div id="refthumbs" class="styrow"></div>
<textarea id="refcap" placeholder="해외 게시물 캡션 원문 붙여넣기 (선택 — 있으면 더 정확)" style="width:100%;min-height:58px;border:1px solid var(--line);border-radius:10px;padding:9px;font-family:inherit;font-size:14px;margin:6px 0"></textarea>
<div class="tdim" style="margin:2px 0 6px">템플릿·전자책 개수는 아래에서 고른 게 그대로 적용돼요.</div>
<button id="refgo" type="button" onclick="localizeCard()" style="background:#0a7d5a">🌏 한국 카드로 변환하기 (2~4분)</button>
</div>
<button id="trendbtn" onclick="loadTrends()" style="background:#fff;color:#c2502a;border:1px solid var(--line);box-shadow:none">🔥 오늘의 주제 추천 (최신 소식 기반)</button>
<button id="insbtn" onclick="loadInsights()" style="background:#fff;color:#2a6fc2;border:1px solid var(--line);box-shadow:none">🤖 AI 인사이트 추천 (프롬프트·툴·자동화)</button>
<div id="insctl" style="display:none;gap:6px;margin:6px 0">
<input id="insq" placeholder="특정 주제로 좁혀 검색 (비우면 전체)" style="flex:1;margin:0" onkeydown="if(event.key==='Enter')loadInsights(true)">
<button class="mini" style="flex:0 0 auto" onclick="loadInsights(true)">🔄 다시</button></div>
<div id="trendlist"></div>
<div style="font-size:14px;font-weight:700;margin:8px 0 2px">🎨 템플릿 선택 <span class="tdim" style="font-weight:400;font-size:12px">— 눌러서 미리보기로 고르세요</span></div>
<div id="themetiles" class="styrow"></div>
<select id="theme" onchange="onTheme()" style="display:none"><option value="hunter" selected>🎮 유튜브 네온 테마 (다크)</option>
<option value="cream">🧡 크림 클래식 테마 (라이트)</option>
<option value="news">🗞 뉴스 에디토리얼 (라이트)</option>
<option value="punch">💥 다크 강펀치 (골드)</option>
<option value="info">📋 인포그래픽 체크리스트</option>
<option value="pastel">🧸 파스텔 소프트</option></select>
<div id="coverbox" style="display:none;margin:6px 0">
<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
<b style="font-size:14px">🖼 표지 이미지</b>
<span class="tdim" style="font-size:12px">주제로 스톡 검색해서 고르거나 내 사진 업로드</span>
<button type="button" class="chip" onclick="stockSearch()">🔎 주제로 검색</button>
<label class="chip" style="cursor:pointer">📎 업로드<input id="coverfile" type="file" accept="image/*" style="display:none" onchange="coverUpload(this)"></label>
<button type="button" class="chip" onclick="coverClear()">✖ 없음</button></div>
<div id="stockgrid" class="styrow"></div></div>
<div id="stylebox" style="margin:8px 0">
<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
<b style="font-size:14px">🎨 스타일 프리셋</b>
<span class="tdim" style="font-size:12px">한 번 학습하면 저장돼서 언제든 골라 반복 사용 (인스타 크롤 X, 계정 안전)</span></div>
<div id="stylelist" class="styrow"></div>
<label class="chip" style="cursor:pointer;display:inline-block">📎 레퍼런스 올려서 새 스타일 학습
<input id="styfile" type="file" accept="image/*" multiple style="display:none" onchange="analyzeStyle(this)"></label></div>
<div class="row">
<select id="items"><option value="30">전자책 30개</option><option value="40">전자책 40개</option>
<option value="60" selected>전자책 60개</option></select>
<input id="keyword" placeholder="댓글 키워드 (비우면 AI 선정)">
</div>
<label style="display:flex;align-items:center;gap:7px;font-size:13px;color:#5b5346;margin:2px 0 4px;cursor:pointer">
<input id="autocover" type="checkbox" checked style="width:16px;height:16px;flex:0 0 auto">
🖼 표지 사진 자동 (Pexels) <span style="color:#9a927f">— news·info 테마에서 제일 예뻐요. 직접 표지 고르면 그게 우선</span></label>
<label style="display:flex;align-items:center;gap:7px;font-size:13px;color:#5b5346;margin:0 0 4px;cursor:pointer">
<input id="bodyimages" type="checkbox" style="width:16px;height:16px;flex:0 0 auto">
🏞 본문 사이에도 사진 (Pexels) <span style="color:#9a927f">— 카드 중간중간 관련 사진 2~3장 끼워넣기 (캐러셀 길어짐)</span></label>
<label style="display:flex;align-items:center;gap:7px;font-size:13px;color:#b3402a;margin:0 0 6px;cursor:pointer;background:rgba(226,104,60,.09);border:1px solid rgba(226,104,60,.35);border-radius:8px;padding:7px 9px">
<input id="autoupload" type="checkbox" style="width:16px;height:16px;flex:0 0 auto">
📤 <b>생성되면 인스타 자동 게시</b> <span style="color:#c2795f">— 켜면 <b>검수 없이 바로 kangarooshort에 공개 게시</b>됩니다. 확실할 때만!</span></label>
<button id="go" onclick="makeCard()">카드뉴스 + 전자책 만들기 (2~4분)</button>
<div id="status"></div>
<div class="bar" id="bar"><div id="fill"></div></div>
<div id="result"></div>
<div class="foot">✳ 저장하고 싶은 정보가 팔로우를 만든다 ✳</div>
<script>
const $=id=>document.getElementById(id);
$('code').value = localStorage.getItem('mfcode')||'';
const esc=s=>String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;');
const escA=s=>esc(s).replace(/"/g,'&quot;');
const THEMES=[['hunter','🎮 네온'],['cream','🧡 크림'],['news','🗞 뉴스'],['punch','💥 강펀치'],['info','📋 인포'],['pastel','🧸 파스텔']];
function renderThemeTiles(){
  const cur=$('theme').value;
  $('themetiles').innerHTML=THEMES.map(([v,n])=>
    `<div class="sty${cur===v?' on':''}" onclick="pickTheme('${v}')"><img src="/themeprev/${v}"><div class="nm">${n}</div></div>`
  ).join('');
}
function pickTheme(v){ $('theme').value=v; renderThemeTiles(); onTheme(); }
renderThemeTiles();

let MODE='normal', NEWSCTX='', PICKED='', TRENDS=[], STYLE_ID='', STYLES=[], STYLE_ACCENT='';
let COVER_URL='', COVER_DATA='', STOCKS=[];
const MODE_HINT={normal:'주제 입력 (예: 쇼츠 조회수 터지는 후킹 문장)',
proof:'주제 입력 — 수익·후기 캡처가 카드 사이에 자동 삽입돼요',
story:'스토리 방향 입력 (예: 채널 말아먹고 월 2천 찍은 이야기)'};
document.querySelectorAll('#modes .chip').forEach(c=>{
  c.onclick=()=>{document.querySelectorAll('#modes .chip').forEach(x=>x.classList.remove('on'));
    c.classList.add('on'); MODE=c.dataset.m;
    $('topic').placeholder=MODE_HINT[MODE];
    $('items').disabled=(MODE==='story');
    $('items').style.opacity=(MODE==='story')?'0.45':'1';};
});
$('topic').addEventListener('input',()=>{if($('topic').value.trim()!==PICKED){NEWSCTX='';PICKED='';
  document.querySelectorAll('.trend').forEach(x=>x.classList.remove('on'));}});
$('code').addEventListener('change',loadStyles);
loadStyles();
function onTheme(){ $('coverbox').style.display='block'; }
onTheme();
async function stockSearch(){
  const code=$('code').value.trim(), q=$('topic').value.trim();
  if(!code){$('status').textContent='접속코드를 먼저 입력하세요';return;}
  if(q.length<2){$('status').textContent='주제를 먼저 입력하세요 (그걸로 이미지 검색해요)';return;}
  $('stockgrid').innerHTML='<span class="tdim" style="align-self:center;padding-left:6px">🔎 이미지 찾는 중...</span>';
  try{
    const r=await fetch('/api/card/stock',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code,query:q})});
    const d=await r.json();
    if(!d.ok){$('stockgrid').innerHTML='';$('status').textContent='❌ '+d.error;return;}
    STOCKS=d.images||[]; renderStock();
    if(!STOCKS.length) $('stockgrid').innerHTML='<span class="tdim" style="align-self:center;padding-left:6px">결과가 없어요 — 주제를 영어로 바꿔보거나 업로드하세요</span>';
  }catch(e){$('status').textContent='❌ '+e;}
}
function renderStock(){
  let h='';
  STOCKS.forEach((s,i)=>{
    const on = COVER_URL===s.url;
    h+=`<div class="sty${on?' on':''}" onclick="coverPick(${i})" title="${escA(s.title||'')}"><img src="${escA(s.thumb)}" alt=""></div>`;
  });
  if(COVER_DATA) h=`<div class="sty on"><img src="${COVER_DATA}" alt=""><div class="nm">내 사진</div></div>`+h;
  $('stockgrid').innerHTML=h;
}
function coverPick(i){ COVER_URL=STOCKS[i].url; COVER_DATA=''; renderStock(); $('status').textContent='🖼 표지 이미지 선택됨'; }
function coverClear(){ COVER_URL=''; COVER_DATA=''; renderStock(); $('status').textContent='🖼 표지 이미지 없음(텍스트만)'; }
function coverUpload(inp){
  const f=inp.files&&inp.files[0]; inp.value='';
  if(!f) return;
  const rd=new FileReader();
  rd.onload=()=>{ COVER_DATA=rd.result; COVER_URL=''; renderStock(); $('status').textContent='🖼 내 사진 표지로 설정됨'; };
  rd.readAsDataURL(f);
}

async function loadTrends(){
  const code=$('code').value.trim();
  if(!code){$('status').textContent='접속코드를 먼저 입력하세요';return;}
  localStorage.setItem('mfcode',code);
  if($('trendlist').getAttribute('data-src')==='news'){
    $('trendlist').innerHTML=''; $('trendlist').removeAttribute('data-src'); return;}
  $('trendbtn').textContent='🔥 최신 소식 불러오는 중...';
  try{
    const r=await fetch('/api/card/trends',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({code})});
    const d=await r.json();
    if(!d.ok){$('status').textContent='❌ '+d.error;return;}
    TRENDS=d.items;
    let h='';
    d.items.forEach((it,i)=>{
      const basis=it.base?`🔎 기반: ${esc(it.base)} · `:'';
      h+=`<div class="trend" onclick="pickTrend(${i},this)"><b>[${esc(it.cat)}]</b> ${esc(it.title)}<br>
      <span class="tdim">${basis}${esc(it.source)} · ${esc(it.date)}</span></div>`;
    });
    $('trendlist').innerHTML=h||'<p class="tdim">지금은 가져올 소식이 없어요</p>';
    $('trendlist').setAttribute('data-src','news');
  }finally{$('trendbtn').textContent='🔥 오늘의 주제 추천 (최신 소식 기반)';}
}
function pickTrend(i,el){
  const it=TRENDS[i];
  $('topic').value=it.title; PICKED=it.title;
  NEWSCTX=it.ctx || `- [${it.date}] ${it.title} (${it.source})`;
  document.querySelectorAll('.trend').forEach(x=>x.classList.remove('on'));
  el.classList.add('on');
  $('status').textContent=(it.cat&&it.cat.indexOf('AI')>=0)
    ?'🤖 이 인사이트를 근거로 생성해요 — 주제 문구는 자유롭게 고쳐도 됩니다'
    :'📰 이 소식을 근거로 생성해요 — 주제 문구는 자유롭게 고쳐도 됩니다';
  window.scrollTo({top:0,behavior:'smooth'});
}
async function loadInsights(reroll){
  const code=$('code').value.trim();
  if(!code){$('status').textContent='접속코드를 먼저 입력하세요';return;}
  localStorage.setItem('mfcode',code);
  const q=(($('insq')&&$('insq').value)||'').trim();
  if(!reroll && !q && $('trendlist').getAttribute('data-src')==='ai'){
    $('trendlist').innerHTML=''; $('trendlist').removeAttribute('data-src');
    $('insctl').style.display='none'; return;}
  $('insctl').style.display='flex';
  $('insbtn').textContent='🤖 인사이트 불러오는 중... (15~30초)';
  try{
    const r=await fetch('/api/card/insights',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({code,query:q,refresh:!!(reroll||q)})});
    const d=await r.json();
    if(!d.ok){$('status').textContent='❌ '+d.error;return;}
    TRENDS=d.items;
    let h='';
    if(!d.youtube_ready) h+=`<div class="tdim" style="font-size:12px;margin:4px 0 2px">▶️ 유튜브 소스는 유튜브 키 넣으면 함께 떠요 (지금은 웹검색 기반)</div>`;
    d.items.forEach((it,i)=>{
      const flag=it.origin==='해외'?'🌍 해외':'🇰🇷 국내';
      const link=it.url?` · <a href="${escA(it.url)}" target="_blank" onclick="event.stopPropagation()">${it.url_kind==='video'?'▶️ 영상↗':'🔎 원문검색↗'}</a>`:'';
      h+=`<div class="trend" onclick="pickTrend(${i},this)"><b>[${flag}·${esc(it.channel||'')}]</b> ${esc(it.title)}${link}<br>
      <span class="tdim">${esc(it.detail||'')}</span><br><span class="tdim">📌 ${esc(it.source)}</span></div>`;
    });
    $('trendlist').innerHTML=h||'<p class="tdim">결과가 없어요 — 검색어를 바꿔보세요</p>';
    $('trendlist').setAttribute('data-src','ai');
  }catch(e){$('status').textContent='❌ 통신 오류: '+e;
  }finally{$('insbtn').textContent='🤖 AI 인사이트 추천 (프롬프트·툴·자동화)';}
}

async function loadStyles(){
  const code=$('code').value.trim(); if(!code) return;
  try{
    const r=await fetch('/api/style/list',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code})});
    const d=await r.json(); if(d.ok){STYLES=d.styles||[]; renderStyles();}
  }catch(e){}
}
function renderStyles(){
  let h=`<div class="sty base${STYLE_ID?'':' on'}" onclick="pickStyle('')">✖<br>기본<br>(없음)</div>`;
  STYLES.forEach(s=>{
    const th=s.thumb?`<img src="${s.thumb}" alt="">`:`<div style="height:70px;border-radius:8px;background:#f3e9df"></div>`;
    const dot=s.accent?`<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${escA(s.accent)};margin-right:3px;vertical-align:middle"></span>`:'';
    const thi=s.theme==='hunter'?' 🌙':(s.theme==='cream'?' ☀️':'');
    h+=`<div class="sty${STYLE_ID===s.id?' on':''}" onclick="pickStyle('${s.id}')" title="${escA(s.summary)}">
    <b class="x" onclick="event.stopPropagation();delStyle('${s.id}')">✕</b>
    ${th}<div class="nm">${dot}${esc(s.name)}${thi}</div></div>`;
  });
  $('stylelist').innerHTML=h;
  if(!STYLES.length) $('stylelist').innerHTML+=`<span class="tdim" style="align-self:center;font-size:12px;padding-left:6px">아직 없어요 — 아래로 참고 스샷을 올려 첫 스타일을 만들어보세요</span>`;
}
function pickStyle(id){
  STYLE_ID=id; renderStyles();
  const s=STYLES.find(x=>x.id===id);
  STYLE_ACCENT = (s&&s.accent)?s.accent:'';
  if(s&&(s.theme==='hunter'||s.theme==='cream')) $('theme').value=s.theme;
  $('status').textContent=id?('🎨 스타일 적용: '+(s?s.name:'')+' — 톤·구성 + 테마·포인트색까지 반영해요'):'';
}
async function delStyle(id){
  if(!confirm('이 스타일 프리셋을 삭제할까요?')) return;
  const code=$('code').value.trim();
  try{await fetch('/api/style/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code,id})});}catch(e){}
  if(STYLE_ID===id) STYLE_ID='';
  loadStyles();
}
async function analyzeStyle(inp){
  const code=$('code').value.trim();
  if(!code){$('status').textContent='접속코드를 먼저 입력하세요'; inp.value=''; return;}
  const files=[...inp.files].slice(0,4); inp.value='';
  if(!files.length) return;
  localStorage.setItem('mfcode',code);
  $('status').textContent='🎨 레퍼런스 스타일 분석 중... (10~20초)';
  try{
    const imgs=await Promise.all(files.map(f=>new Promise((res,rej)=>{
      const rd=new FileReader(); rd.onload=()=>res(rd.result); rd.onerror=rej; rd.readAsDataURL(f);})));
    const r=await fetch('/api/style/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code,images:imgs})});
    const d=await r.json();
    if(!d.ok){$('status').textContent='❌ '+d.error; return;}
    STYLE_ID=d.style.id;
    await loadStyles();
    $('status').textContent='✅ 스타일 학습 완료: '+d.style.name+' — 바로 적용됨';
  }catch(e){$('status').textContent='❌ 분석 오류: '+e;}
}

function setBar(pct){$('bar').style.display='block';$('fill').style.width=pct+'%';}
let BUSY=false, POLLTOKEN=0;
async function makeCard(){
  const code=$('code').value.trim(), topic=$('topic').value.trim();
  if(!code||!topic){$('status').textContent='접속코드와 주제를 입력하세요';return;}
  if(BUSY){$('status').textContent='⏳ 이미 제작 중입니다';return;}
  if($('autoupload')&&$('autoupload').checked){
    if(!confirm('⚠️ 자동 게시가 켜져 있습니다.\\n생성이 끝나면 검수 없이 바로 kangarooshort 계정에 공개 게시됩니다.\\n계속할까요?')) return;
  }
  BUSY=true; const token=++POLLTOKEN;
  localStorage.setItem('mfcode',code);
  $('go').disabled=true; $('result').innerHTML=''; setBar(3);
  $('status').textContent='🚀 기획 시작...';
  try{
    const sty=STYLES.find(x=>x.id===STYLE_ID);
    const ctx=[NEWSCTX, sty?sty.guide:''].filter(Boolean).join('\\n\\n');
    const mkBody={code,topic,items:+$('items').value,keyword:$('keyword').value.trim(),
      theme:$('theme').value,mode:MODE,context:ctx,
      auto_cover:!($('autocover')&&!$('autocover').checked),
      body_images:!!($('bodyimages')&&$('bodyimages').checked),
      auto_upload:!!($('autoupload')&&$('autoupload').checked)};
    if(STYLE_ACCENT) mkBody.accent=STYLE_ACCENT;
    if(COVER_URL) mkBody.cover_url=COVER_URL; else if(COVER_DATA) mkBody.cover_data=COVER_DATA;
    const r=await fetch('/api/card/make',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(mkBody)});
    const d=await r.json();
    if(!d.ok){fail(d.error);return;}
    poll(d.job, code, token);
  }catch(e){fail('통신 오류: '+e);}
}
function fail(msg){$('status').textContent='❌ '+msg;$('bar').style.display='none';$('go').disabled=false;var rg=$('refgo');if(rg)rg.disabled=false;BUSY=false;}

let REFIMGS=[];
function toggleRef(){const b=$('refbox');const open=b.style.display==='none';b.style.display=open?'block':'none';
  $('reftgl').textContent=open?'🌏 해외 변환 닫기 ▲':'🌏 해외 인기글 → 한국판으로 변환';}
function refThumbs(){
  $('refthumbs').innerHTML=REFIMGS.map((u,i)=>
    `<div class="sty" style="width:74px;padding:4px"><img src="${u}" style="height:74px"><div class="x" onclick="refDel(${i})">×</div></div>`).join('');
}
function refDel(i){REFIMGS.splice(i,1);refThumbs();}
function refUpload(input){
  const files=[...input.files]; input.value='';
  files.forEach(f=>{
    const rd=new FileReader();
    rd.onload=e=>{
      const img=new Image();
      img.onload=()=>{
        const m=1280, s=Math.min(1, m/Math.max(img.width,img.height));
        const c=document.createElement('canvas');
        c.width=Math.round(img.width*s); c.height=Math.round(img.height*s);
        c.getContext('2d').drawImage(img,0,0,c.width,c.height);
        REFIMGS.push(c.toDataURL('image/jpeg',0.85)); refThumbs();
      };
      img.src=e.target.result;
    };
    rd.readAsDataURL(f);
  });
}
async function localizeCard(){
  const code=$('code').value.trim();
  if(!code){$('status').textContent='접속코드를 입력하세요';return;}
  if(!REFIMGS.length){$('status').textContent='해외 게시물 캡처를 1장 이상 올려주세요';return;}
  if(BUSY){$('status').textContent='⏳ 이미 제작 중입니다';return;}
  BUSY=true; const token=++POLLTOKEN;
  localStorage.setItem('mfcode',code);
  $('refgo').disabled=true; $('go').disabled=true; $('result').innerHTML=''; setBar(3);
  $('status').textContent='🌏 해외 레퍼런스 분석 중...';
  try{
    const r=await fetch('/api/card/localize',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({code,images:REFIMGS,caption:$('refcap').value.trim(),
        theme:$('theme').value,items:+$('items').value,keyword:$('keyword').value.trim()})});
    const d=await r.json();
    if(!d.ok){fail(d.error);return;}
    poll(d.job, code, token);
  }catch(e){fail('통신 오류: '+e);}
}
async function poll(jid, code, token){
  if(token!==POLLTOKEN) return;
  try{
    const r=await fetch(`/api/job/${jid}?code=${encodeURIComponent(code)}`);
    const d=await r.json();
    if(token!==POLLTOKEN) return;
    if(!d.ok){
      if((d.error||'').indexOf('찾을 수 없')>=0)
        fail('작업 정보가 초기화됐어요(서버 업데이트/재시작 등). 다시 [만들기]를 눌러주세요');
      else fail(d.error);
      return;
    }
    setBar(d.pct); $('status').textContent='⏳ '+(d.msg||'작업 중...');
    if(d.status==='error'){fail(d.error);return;}
    if(d.status==='done'){setBar(100);$('status').textContent='✅ 완성!';renderResult(d.result);
      $('go').disabled=false;var rg=$('refgo');if(rg)rg.disabled=false;BUSY=false;
      setTimeout(()=>{$('bar').style.display='none';},1200);return;}
    setTimeout(()=>poll(jid,code,token), 1500);
  }catch(e){setTimeout(()=>poll(jid,code,token), 2500);}
}
function renderResult(d){
  let h=`<button onclick="pubPack('${d.pack}')">📤 인스타 자동 업로드 (캐러셀+캡션 한 번에)</button>`;
  h+=`<p class="small">⬇ <a href="${d.zip}" download><b>zip 한 번에 받기</b></a>${d.ebook?`
 &nbsp;|&nbsp; 📕 <a href="${d.ebook}" download><b>전자책 PDF</b></a>`:''}
 &nbsp;|&nbsp; 댓글 키워드: <b>${esc(d.keyword)}</b></p>`;
  h+=`<h2>1) 캐러셀 (순서대로 업로드)</h2>`;
  d.cards.forEach(u=>{h+=`<img src="${u}">`;});
  h+=`<h2>2) 본문 캡션 <button style="width:auto;padding:8px 14px;font-size:14px" onclick="copyCap()">📋 복사</button></h2><pre id="cap">${esc(d.caption)}</pre>`;
  h+=`<p class="small">📌 업로드 후: 전자책 PDF를 드라이브/클라우드플레어에 올리고,
ManyChat 자동화의 댓글 키워드를 '<b>${esc(d.keyword)}</b>'로 맞춰주세요.
자세한 순서는 <b>FUNNEL-GUIDE.md</b> 참고.</p>`;
  $('result').innerHTML=h;
}
function copyCap(){navigator.clipboard.writeText($('cap').innerText).then(()=>alert('캡션이 복사됐습니다!'));}

async function pubPack(name, force){
  const code=$('code').value.trim();
  if(!code){$('status').textContent='접속코드를 먼저 입력하세요';return;}
  if(!force && !confirm('인스타그램에 지금 바로 업로드할까요?\\n\\n'+name)) return;
  const r=await fetch('/api/insta/publish',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({code,pack:name,force:!!force})});
  const d=await r.json();
  if(!d.ok){
    if((d.error||'').includes('이미 업로드')){
      if(confirm('이미 올렸던 팩이에요. 강제로 한 번 더 올릴까요?')) pubPack(name,true);
      return;
    }
    alert('❌ '+d.error);return;
  }
  pubPoll(d.job, code);
}
function pubPoll(jid, code){
  $('status').textContent='📤 인스타 업로드 중...';
  const t=setInterval(async()=>{
    try{
      const r=await fetch(`/api/job/${jid}?code=${encodeURIComponent(code)}`);
      const d=await r.json();
      if(!d.ok){clearInterval(t);$('status').textContent='❌ '+d.error;return;}
      $('status').textContent='📤 '+(d.msg||'업로드 중...');
      if(d.status==='error'){clearInterval(t);$('status').textContent='❌ 업로드 실패';alert('업로드 실패: '+d.error);}
      if(d.status==='done'){clearInterval(t);
        $('status').innerHTML='✅ 인스타 업로드 완료! '+(d.result&&d.result.permalink?`<a href="${d.result.permalink}" target="_blank"><b>게시물 열기 ↗</b></a>`:'');
        window.scrollTo({top:0,behavior:'smooth'});}
    }catch(e){}
  }, 2000);
}
</script></body></html>"""

PACKS_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>결과물 창고</title>
<link rel="icon" type="image/png" href="/logo-origami.png">
<link rel="apple-touch-icon" href="/logo-origami.png">
<meta name="theme-color" content="#0c1024">
<style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
button,select,.pk,.th,.nav a{touch-action:manipulation}
@font-face{font-family:'Pretendard';font-weight:400;font-display:swap;src:url('/fonts/Pretendard-Regular.otf') format('opentype')}
@font-face{font-family:'Pretendard';font-weight:600;font-display:swap;src:url('/fonts/Pretendard-SemiBold.otf') format('opentype')}
@font-face{font-family:'Pretendard';font-weight:800;font-display:swap;src:url('/fonts/Pretendard-ExtraBold.otf') format('opentype')}
:root{--gold:#e8b640;--gold2:#f2cf6b;--ink:#f0ead8;--line:#2c3559;--panel:#1c2340;--panel2:#171e38}
html{-webkit-text-size-adjust:100%}
body{background:radial-gradient(1100px 480px at 50% -10%,#1c2554 0%,rgba(28,37,84,0) 62%),
linear-gradient(180deg,#0c1024 0%,#141b36 40%,#171f3d 100%);min-height:100vh;
color:var(--ink);font-family:'Pretendard','Malgun Gothic',sans-serif;max-width:560px;margin:0 auto;padding:20px;
-webkit-font-smoothing:antialiased}
.brand{display:flex;align-items:center;gap:14px;margin:6px 0 10px}
.brand img{height:72px;margin:0;box-shadow:none;border-radius:0}
h1{font-size:25px;margin:0;color:var(--gold2);letter-spacing:2px;font-weight:800}
.sub{color:#8b93b8;font-size:13px;margin:6px 0 0;line-height:1.5}
h2{font-size:18px;color:var(--gold2);margin:14px 0 6px}
h3{font-size:16px;color:var(--gold2);margin:24px 0 8px}
input,select,textarea{width:100%;padding:13px 15px;border-radius:12px;border:1px solid var(--line);background:var(--panel2);color:var(--ink);font-size:16px;margin:6px 0;outline:none;font-family:inherit;line-height:1.5}
input:focus,textarea:focus{border-color:var(--gold);box-shadow:0 0 0 3px rgba(232,182,64,.18)}
input::placeholder{color:#5d6690}
label{font-size:12px;color:#8b93b8;margin-top:8px;display:block}
button{width:100%;background:linear-gradient(180deg,#f0c14f,#e2a92f);color:#1a1a2e;border:0;padding:15px;border-radius:12px;font-size:16px;font-weight:800;cursor:pointer;margin-top:10px;font-family:inherit;box-shadow:0 6px 16px rgba(0,0,0,.35)}
button:active{transform:translateY(1px)}
.ghost{background:#222b4f;color:#f0ead8;box-shadow:none}
.mini{width:auto;padding:7px 12px;font-size:13px;margin:0 0 0 8px;display:inline-block;vertical-align:middle}
img{max-width:100%;border-radius:14px;margin:8px 0;display:block;box-shadow:0 8px 22px rgba(0,0,0,.35)}
pre{white-space:pre-wrap;background:var(--panel);padding:16px;border-radius:14px;font-family:inherit;font-size:15px;line-height:1.65;color:#e9e4d2;border:1px solid #262f55}
.small{background:var(--panel);padding:12px;font-size:14px;border-radius:12px;border:1px solid #262f55;margin:8px 0;line-height:1.6}
a{color:#8fb6ff}
.nav{display:flex;gap:6px;margin:14px 0 18px;background:rgba(23,30,56,.65);padding:6px;border-radius:14px;border:1px solid var(--line)}
.nav a{flex:1;text-align:center;padding:11px 6px;border-radius:10px;color:#c5cbe6;text-decoration:none;font-size:14px;font-weight:700}
.nav a.on{background:var(--gold);color:#1a1a2e}
#status,#pstat{margin:12px 0;color:var(--gold2);font-size:15px;white-space:pre-wrap}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.pk{background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow:hidden;cursor:pointer}
.pk img{width:100%;aspect-ratio:4/5;object-fit:cover;margin:0;border-radius:0;box-shadow:none}
.pkt{padding:9px 10px;font-size:13px;line-height:1.5}
.dim{color:#7d86ad;font-size:12px}
.pub{color:#7ee0a0;font-size:11px;border:1px solid #2f5c44;background:#17301f;padding:1px 6px;border-radius:8px;white-space:nowrap}
.pk.up{box-shadow:inset 0 0 0 2px #2a6fc2}
.thumbs{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}
.th{border:3px solid #3a4166;border-radius:16px;cursor:pointer;padding:2px;transition:border-color .12s,transform .1s}
.th:hover{border-color:var(--gold2);transform:translateY(-2px)}
.th.sel{border-color:var(--gold);box-shadow:0 0 0 3px rgba(232,182,64,.3)}
.th img{margin:0}
.thc{text-align:center;font-size:12px;padding:5px 2px 2px;color:#c5cbe6;line-height:1.35}
.th.sel .thc{color:var(--gold2);font-weight:700}
.itbox{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:10px 12px;margin:8px 0}
.ig{background:#000;border-radius:14px;overflow:hidden;border:1px solid var(--line)}
.ighead{display:flex;align-items:center;gap:8px;padding:9px 12px;font-weight:700;font-size:14px;background:#12172e}
.ighead img{width:26px;height:26px;border-radius:50%;margin:0;object-fit:cover;background:#fff}
.igvp{overflow:hidden;position:relative;touch-action:pan-y}
.igtrack{display:flex;transition:transform .22s ease;will-change:transform}
.igslide{flex:0 0 100%;width:100%}
.igslide img{width:100%;aspect-ratio:4/5;object-fit:cover;margin:0;border-radius:0;display:block;user-select:none;-webkit-user-drag:none}
.igdots{display:flex;gap:5px;justify-content:center;padding:8px}
.igdots span{width:6px;height:6px;border-radius:50%;background:#3a4166;display:inline-block}
.igdots span.on{background:var(--gold)}
.ignav{position:absolute;top:45%;transform:translateY(-50%);width:38px;height:38px;border-radius:50%;background:rgba(20,25,50,.78);color:#fff;border:1px solid #3a4166;font-size:22px;line-height:1;padding:0;margin:0;z-index:2;cursor:pointer}
.igslide img{-webkit-user-drag:none}
.row{display:flex;gap:8px}.row>*{flex:1}
details{margin:8px 0;background:rgba(23,30,56,.5);border:1px solid var(--line);border-radius:12px;padding:4px 12px}
summary{cursor:pointer;color:var(--gold2);font-weight:700;padding:8px 0}
.foot{text-align:center;margin:52px 0 12px;font-size:12px;color:#5d6690;letter-spacing:.5px}
@media(max-width:430px){body{padding:14px}.brand img{height:58px}h1{font-size:21px}
.nav a{font-size:13px;padding:11px 3px}.mini{padding:8px 11px}}
</style></head><body>
<div class="brand"><img src="/logo-origami.png" alt="logo"><div>
<h1>결과물 창고</h1><div class="sub">완성팩 확인 · 썸네일 선택 · 문구 수정 · 인스타 업로드 — 폰에서도 OK</div>
</div></div>
<div class="nav"><a href="/">🏭 짤공장</a><a href="/card">🗂 카드뉴스</a><a class="on" href="/p">📦 결과물</a></div>
<div class="row"><input id="code" placeholder="접속코드" type="password"><button class="ghost" style="flex:0 0 110px;margin-top:6px" onclick="loadList()">불러오기</button></div>
<div id="status"></div>
<div id="list"></div>
<div id="detail"></div>
<div class="foot">⚙ 오늘도 무사히 공장 가동 중</div>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;');
const escA=s=>esc(s).replace(/"/g,'&quot;');
$('code').value=localStorage.getItem('mfcode')||'';
let CUR=null, LEAD=null, ARR=[], PUBBUSY=false, EDITBUSY=false, ARRDIRTY=false;
let MGRS=[], NEED=2, USED_DIR='_사용완료', ARCHVIEW=false, LASTPACKS=[], UPFILTER='all', KINDFILTER='all';

async function api(path,body){
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  return await r.json();
}
async function loadList(arch){
  const code=$('code').value.trim();
  if(!code){$('status').textContent='접속코드를 입력하세요';return;}
  localStorage.setItem('mfcode',code);
  ARCHVIEW=!!arch;
  $('status').textContent='📦 불러오는 중...';
  let d; try{d=await api('/api/packs',{code,archived:ARCHVIEW});}catch(e){$('status').textContent='통신 오류: '+e;return;}
  if(!d.ok){$('status').textContent='❌ '+d.error;return;}
  MGRS=d.managers||MGRS; NEED=d.need||NEED; USED_DIR=d.used_dir||USED_DIR;
  LASTPACKS=d.packs; $('status').textContent='';
  paintList();
}
function setUp(f){UPFILTER=f; paintList();}
function setKind(f){KINDFILTER=f; paintList();}
async function delPack(name){
  name=decodeURIComponent(name);
  if(!confirm('이 팩을 삭제할까요? (바로 안 지우고 휴지통으로 옮겨요 · 복구 가능)')) return;
  const code=$('code').value.trim();
  try{const d=await api('/api/pack/delete',{code,pack:name});
    if(!d.ok){alert('❌ '+d.error);return;}
    back(); loadList(ARCHVIEW);
  }catch(e){alert('통신 오류: '+e);}
}
const isJp=p=>p.story||p.lang==='ja';
function paintList(){
  let list=LASTPACKS.slice();
  let h=`<div class="row" style="margin:2px 0 10px">
    <button class="${ARCHVIEW?'ghost':''}" onclick="loadList(false)">📦 사용 가능</button>
    <button class="${ARCHVIEW?'':'ghost'}" onclick="loadList(true)">🗄 사용완료함</button></div>`;
  if(!ARCHVIEW){
    const nJp=LASTPACKS.filter(isJp).length, nKr=LASTPACKS.length-nJp;
    h+=`<div class="row" style="margin:0 0 8px;gap:6px">
      <button class="mini ${KINDFILTER==='all'?'':'ghost'}" onclick="setKind('all')">전체 ${LASTPACKS.length}</button>
      <button class="mini ${KINDFILTER==='kr'?'':'ghost'}" onclick="setKind('kr')">🇰🇷 한국 ${nKr}</button>
      <button class="mini ${KINDFILTER==='jp'?'':'ghost'}" onclick="setKind('jp')">🇯🇵 일본(스토리) ${nJp}</button></div>`;
    if(KINDFILTER==='kr') list=list.filter(p=>!isJp(p));
    else if(KINDFILTER==='jp') list=list.filter(isJp);
    const nUp=list.filter(p=>p.published).length, nNo=list.length-nUp;
    h+=`<div class="row" style="margin:0 0 10px;gap:6px">
      <button class="mini ${UPFILTER==='all'?'':'ghost'}" onclick="setUp('all')">전체 ${list.length}</button>
      <button class="mini ${UPFILTER==='up'?'':'ghost'}" onclick="setUp('up')">📤 업로드됨 ${nUp}</button>
      <button class="mini ${UPFILTER==='no'?'':'ghost'}" onclick="setUp('no')">🆕 미업로드 ${nNo}</button></div>`;
    if(UPFILTER==='up') list=list.filter(p=>p.published);
    else if(UPFILTER==='no') list=list.filter(p=>!p.published);
  }
  if(!list.length) h+=`<div class="dim" style="padding:14px">${ARCHVIEW?'아직 사용완료 처리된 팩이 없어요':(UPFILTER==='up'?'업로드된 팩이 없어요':UPFILTER==='no'?'미업로드 팩이 없어요':'아직 만든 완성팩이 없어요')}</div>`;
  h+='<div class="grid">';
  list.forEach(p=>{
    const b=ARCHVIEW?('/packs/'+encodeURIComponent(USED_DIR)+'/'+encodeURIComponent(p.name)):('/packs/'+encodeURIComponent(p.name));
    const up=p.published?' <span class="pub" style="background:#123a63;color:#8fc2ff;border-color:#2a6fc2">📤 업로드완료</span>':'';
    const jp=isJp(p)?' <span class="pub" style="background:#3a1440;color:#e0a0f0;border-color:#7a3aa8">🇯🇵 일본</span>':'';
    const badge=ARCHVIEW?' <span class="pub" style="background:#3a4166;color:#c8cfe6;border-color:#4a5170">🗄 보관</span>'
      :(p.used?` <span class="pub" style="background:#4a3a10;color:#f2cf6b;border-color:#7a5c12">✅ ${p.used}/${NEED} 체크</span>`:'');
    const icon=isJp(p)?'📖':(p.type==='cardnews'?'🗂':'🏭');
    h+=`<div class="pk${p.published?' up':''}" style="position:relative" onclick="openPack('${encodeURIComponent(p.name)}',${ARCHVIEW})">
    <img loading="lazy" src="${b}/${encodeURIComponent(p.thumb)}">
    <button onclick="event.stopPropagation();delPack('${encodeURIComponent(p.name)}')" title="삭제(휴지통으로)" style="position:absolute;top:5px;right:5px;background:rgba(20,15,15,.72);color:#fff;border:none;border-radius:8px;padding:2px 7px;font-size:14px;cursor:pointer;line-height:1.4">🗑</button>
    <div class="pkt">${icon} ${esc(p.title)}${jp}${up}${badge}<br>
    <span class="dim">${esc((p.created||'').replace('T',' ').slice(0,16))}</span></div></div>`;
  });
  $('list').innerHTML=h+'</div>'; $('list').style.display=''; $('detail').innerHTML='';
}
async function openPack(name,arch){
  name=decodeURIComponent(name);
  const code=$('code').value.trim();
  $('status').textContent='여는 중...';
  let d; try{d=await api('/api/pack',{code,pack:name,archived:!!arch});}catch(e){$('status').textContent='통신 오류: '+e;return;}
  if(!d.ok){$('status').textContent='❌ '+d.error;return;}
  $('status').textContent='';
  CUR=d; LEAD=d.thumbs.length?d.thumbs[0]:null;
  ARR=d.images.map(n=>({n,ex:false})); ARRDIRTY=false;
  $('list').style.display='none';
  renderDetail(); window.scrollTo({top:0});
}
function back(){$('detail').innerHTML='';$('list').style.display='';CUR=null;}
function renderDetail(){
  const d=CUR, t=Date.now();
  const base=d.arch?('/packs/'+encodeURIComponent(d.used_dir||USED_DIR)+'/'+encodeURIComponent(d.name)):('/packs/'+encodeURIComponent(d.name));
  let h='<button class="ghost" onclick="back()">← 목록으로</button>';
  h+=` <button class="ghost" onclick="delPack('${encodeURIComponent(d.name)}')" style="color:#e08484;border-color:#7a3a3a">🗑 삭제</button>`;
  h+=`<h2>${d.lang==='ja'?'📖':(d.type==='cardnews'?'🗂':'🏭')} ${esc(d.title)}</h2>`;
  h+=`<div class="dim">${esc((d.created||'').replace('T',' '))}</div>`;
  h+='<div id="pstat"></div>';
  if(d.published){
    h+=`<div class="small" style="color:#8fc2ff">📤 인스타 업로드완료 — @${esc(d.published.account||'')} · ${esc((d.published.time||'').replace('T',' '))}`;
    if(d.published.permalink) h+=` · <a href="${d.published.permalink}" target="_blank">게시물 열기 ↗</a>`;
    h+='</div>';
  }
  if(d.thumbs.length){
    h+='<h3>① 대표 썸네일 고르기 <span class="dim" style="font-weight:400">— 카드를 <b style="color:var(--gold2)">클릭</b>하면 인스타 첫 장이 돼요'+(d.lang==='ja'?' · 아래 🇰🇷 해석 보고 고르세요':'')+'</span></h3><div class="thumbs">';
    d.thumbs.forEach((tn,i)=>{
      const hk=(d.hooks&&d.hooks[i])||{};
      const hook=hk.line1?esc(hk.line1):('후보 '+(i+1));
      const ko=hk.ko?`<div style="font-size:12.5px;color:#a9b0c8;margin-top:3px;line-height:1.35;word-break:keep-all">🇰🇷 ${esc(hk.ko)}</div>`:'';
      const on=tn===LEAD;
      h+=`<div class="th${on?' sel':''}" onclick="pickLead('${tn}')">
      <img src="${base}/${tn}?t=${t}"><div class="thc">${on?'✅ 첫 장으로 선택됨':'👆 '+hook}${ko}</div></div>`;
    });
    h+='</div>';
  }
  if(d.story){
    h+=`<div class="itbox" style="margin-top:6px">
      <div class="dim" style="margin-bottom:6px">헤드라인 3개 다 별로면 다시 뽑기 · 원하는 느낌 적으면 그 감성으로 (선택)</div>
      <input id="rhhint" placeholder="예: 더 충격적으로 / '냥이의 보은' 감성으로" style="width:100%;box-sizing:border-box;margin-bottom:6px">
      <button class="ghost" onclick="reHead()">🔄 헤드라인 3개 다시 뽑기 (10~20초)</button></div>`;
  }
  if(d.story && (d.srcs||[]).length>1){
    h+=`<h3>커버 사진 바꾸기 <span class="dim" style="font-weight:400">— 순서 헷갈릴 때 커버에 쓸 사진을 골라요 (헤드라인·본문 유지)</span></h3>`;
    h+='<div style="display:flex;gap:8px;flex-wrap:wrap">';
    d.srcs.forEach(s=>{
      const on=d.thumb_src===s || (!d.thumb_src && s==='src00.jpg');
      h+=`<img src="${base}/${s}?t=${t}" onclick="reCover('${s}')" title="이 사진을 커버로" style="width:78px;height:78px;object-fit:cover;border-radius:10px;cursor:pointer;border:3px solid ${on?'var(--gold)':'#3a4166'};margin:0">`;
    });
    h+='</div>';
  }
  if(!d.arch) h+=`<button onclick="pub(false)">📤 인스타 자동 업로드${d.thumbs.length?' (선택한 썸네일이 첫 장)':''}</button>`;
  h+=`<div class="small">⬇ <a href="${base}/${encodeURIComponent(d.zip)}" download onclick="return zipDl()"><b>zip 받기</b></a>`;
  if(d.ebook) h+=` &nbsp;|&nbsp; 📕 <a href="${base}/ebook.pdf" download><b>전자책 PDF</b></a>`;
  if(d.keyword) h+=` &nbsp;|&nbsp; 댓글 키워드: <b>${esc(d.keyword)}</b>`;
  h+='</div>';
  if(d.type==='cardnews' && !d.arch && d.lang!=='ja' && d.edit){
    h+=`<button onclick="makeJP()" style="background:#c1272d">🇯🇵 일본어판 만들기 (번역+일본어 폰트로 재렌더 · 1~3분)</button>`;
  }
  if(d.lang==='ja'){
    h+=`<div class="small" style="color:#8fc2ff">🇯🇵 일본어판 팩 — 그대로 일본 계정에 올리면 돼요 (카드+캡션)</div>`;
  }
  h+=usageSection(d);
  h+=`<h3>본문 캡션 <button class="mini ghost" onclick="copyCap()">📋 복사</button><button class="mini ghost" onclick="capEdit()">✏️ 수정</button></h3>`;
  h+=`<pre id="cap">${esc(d.caption)}</pre><div id="capbox"></div>`;
  if(d.images.length){
    h+=`<h3>② 인스타 미리보기 — 실제 잘림 그대로, 옆으로 넘겨보세요</h3>`;
    h+=`<div class="ig" style="position:relative"><div class="ighead"><img src="/logo.png">${d.lang==='ja'?'kangaroostory.jp':(d.type==='cardnews'?'kangarooshort':'sowho77')}</div>
    <button class="ignav" style="left:8px" onclick="igNav(-1)">‹</button>
    <button class="ignav" style="right:8px" onclick="igNav(1)">›</button>
    <div class="igvp" id="igvp"><div class="igtrack" id="igtrack">`;
    if(LEAD) h+=`<div class="igslide"><img src="${base}/${LEAD}?t=${t}"></div>`;
    ARR.filter(a=>!a.ex).forEach(a=>{h+=`<div class="igslide"><img src="${base}/${a.n}?t=${t}"></div>`;});
    h+=`</div></div><div class="igdots" id="igdots"></div></div>`;
    if(!d.arch){
    h+=`<h3>③ 짤 순서·제외 편집 <span class="dim" style="font-weight:400">▲▼ 이동 · ✕ 제외 · 🖼 이 짤을 썸네일로 · 저장해야 반영</span></h3>`;
    ARR.forEach((a,i)=>{
      h+=`<div class="itbox" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap${a.ex?';opacity:.4':''}">
      <img src="${base}/${a.n}?t=${t}" style="width:64px;height:80px;object-fit:cover;margin:0;flex:0 0 64px;border-radius:8px">
      <div style="flex:1;min-width:70px"><b>${a.ex?'—':(ARR.slice(0,i).filter(x=>!x.ex).length+1)+'번'}</b>${a.ex?' <span style="color:#e08484">제외됨</span>':''}</div>
      ${a.ex?'':`<button class="mini ghost" onclick="reThumb('${a.n}')" title="이 짤을 썸네일 배경으로 다시 만들기">🖼 썸네일</button>`}
      ${a.ex?'':`<button class="mini ghost" onclick="openMosaic('${a.n}')" title="이 짤에 욕설·개인정보 모자이크 치기">🟦 모자이크</button>`}
      <button class="mini ghost" onclick="mv(${i},-1)">▲</button>
      <button class="mini ghost" onclick="mv(${i},1)">▼</button>
      <button class="mini ghost" onclick="tex(${i})">${a.ex?'복구':'✕'}</button></div>`;
    });
    if(ARRDIRTY) h+=`<div class="small" style="color:#e0a94a">⚠ 저장 안 한 변경이 있어요 — 아래 💾를 눌러야 zip·업로드·인스타에 실제로 반영됩니다</div>`;
    h+=`<button class="ghost" onclick="saveArr()">💾 순서·제외 저장 (미리보기·zip·업로드에 반영)</button>`;
    }
  }
  if(d.story && (d.cards_ko||[]).some(x=>x)){
    h+=`<h3>🇰🇷 본문 해석 <span class="dim" style="font-weight:400">— 일본어 본문의 뜻이에요. 오역·오류 없는지 확인하세요</span></h3>`;
    h+='<div class="itbox">';
    d.cards_ko.forEach((ko,i)=>{
      if(!ko) return;
      const label = i===0 ? '📖 커버(썸네일) 본문' : ('📄 '+i+'번 본문카드');
      h+=`<div style="margin:0 0 9px;padding-bottom:9px;${i<d.cards_ko.length-1?'border-bottom:1px solid var(--line)':''}"><b style="color:var(--gold2)">${label}</b><div style="margin-top:3px;line-height:1.55;color:#d2d7ee;white-space:pre-wrap">${esc(ko)}</div></div>`;
    });
    h+='</div>';
  }
  if(d.edit && !d.arch) h+=`<button class="ghost" onclick="openEdit()">✏️ 카드 문구 수정하고 다시 굽기 (AI 없이 10~30초)</button><div id="editbox"></div>`;
  $('detail').innerHTML=h;
  setTimeout(igInit,60);
}
function pickLead(tn){LEAD=tn;renderDetail();}
function usageSection(d){
  if(d.arch) return `<div class="itbox" style="border-color:#3a4166"><b>🗄 사용완료 보관됨</b> <span class="dim">— 담당 ${NEED}명 확인 후 이동된 팩이에요. 필요하면 위 zip으로 다시 받을 수 있습니다.</span></div>`;
  const need=(d.used&&d.used.need)||NEED, checked=(d.used&&d.used.checked)||[];
  const mgrs=(d.managers&&d.managers.length)?d.managers:MGRS;
  let s=`<h3>✅ 사용완료 체크 <span class="dim" style="font-weight:400">(담당 ${need}명이 누르면 보관함으로 자동 이동)</span></h3>`;
  s+=`<div class="itbox" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">`;
  mgrs.forEach(m=>{const on=checked.indexOf(m)>=0;
    s+=`<button class="mini ${on?'':'ghost'}" onclick="useCheck('${encodeURIComponent(m)}')">${on?'✓ ':''}${esc(m)}</button>`;});
  s+=`<span class="dim" style="margin-left:auto">${checked.length}/${need} 확인</span></div>`;
  return s;
}
async function useCheck(m){
  m=decodeURIComponent(m);
  const need=(CUR.used&&CUR.used.need)||NEED, checked=(CUR.used&&CUR.used.checked)||[];
  const has=checked.indexOf(m)>=0;
  if(!has && checked.length+1>=need){
    if(!confirm(m+' 님까지 체크하면 '+need+'/'+need+' → 이 팩은 사용완료로 보관함(🗄)에 들어갑니다.\\n계속할까요?')) return;
  }
  const d=await api('/api/pack/use',{code:$('code').value.trim(),pack:CUR.name,manager:m,undo:has});
  if(!d.ok){alert('❌ '+d.error);return;}
  if(d.archived){alert('✅ 사용완료 처리 완료! 보관함(🗄)으로 이동했습니다.');back();loadList(false);return;}
  CUR.used={checked:d.checked,need:d.need};
  if(d.warn){$('pstat').textContent='⚠ '+d.warn;}
  renderDetail();
}
function mv(i,dd){const j=i+dd;if(j<0||j>=ARR.length)return;const t2=ARR[i];ARR[i]=ARR[j];ARR[j]=t2;ARRDIRTY=true;renderDetail();}
function tex(i){ARR[i].ex=!ARR[i].ex;ARRDIRTY=true;renderDetail();}
async function reCover(src){
  $('pstat').textContent='🖼 커버 사진 바꾸는 중...';
  const d=await api('/api/pack/rethumb',{code:$('code').value.trim(),pack:CUR.name,base:src});
  if(!d.ok){$('pstat').textContent='❌ '+d.error;return;}
  $('pstat').textContent='✅ 커버 사진 교체 완료 — 위 ① 썸네일에서 확인하세요';
  openPack(CUR.name);
}
async function reThumb(fn){
  if(!confirm(fn+' 짤을 썸네일 배경으로 다시 만들까요?\\n후킹 문구 3종은 그대로, 배경만 이 짤로 교체됩니다.')) return;
  $('pstat').textContent='🖼 썸네일 다시 만드는 중...';
  const d=await api('/api/pack/rethumb',{code:$('code').value.trim(),pack:CUR.name,base:fn});
  if(!d.ok){$('pstat').textContent='❌ '+d.error;return;}
  $('pstat').textContent='✅ 썸네일 재생성 완료 — 위 ① 썸네일에서 확인하세요';
  openPack(CUR.name);
}
async function reHead(){
  const hint=($('rhhint')&&$('rhhint').value.trim())||'';
  $('pstat').textContent='🔄 헤드라인 다시 뽑는 중... (10~20초)';
  const d=await api('/api/pack/reheadline',{code:$('code').value.trim(),pack:CUR.name,hint});
  if(!d.ok){$('pstat').textContent='❌ '+d.error;return;}
  $('pstat').textContent='✅ 새 헤드라인 나왔어요 — 위 ①에서 확인하고 고르세요';
  openPack(CUR.name);
}
async function saveArr(){
  const keep=ARR.filter(a=>!a.ex).map(a=>a.n);
  if(!keep.length){alert('최소 1장은 남겨야 해요');return;}
  const d=await api('/api/pack/arrange',{code:$('code').value.trim(),pack:CUR.name,order:keep});
  if(!d.ok){alert('❌ '+d.error);return;}
  $('pstat').textContent='💾 순서 저장 완료!';
  openPack(CUR.name);
}
let MOSBOXES=[], MOSDRAW=null;
function openMosaic(fn){
  const base='/packs/'+CUR.name;
  const ov=document.createElement('div');
  ov.id='mosov';
  ov.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.86);z-index:9999;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:14px';
  ov.innerHTML='<div style="color:#f0ead8;font-size:14px;margin-bottom:8px;text-align:center;line-height:1.5">🟦 가릴 부분을 손가락/마우스로 <b>드래그</b>하세요<br><span style="color:#9aa3c8;font-size:12px">욕설·개인정보 등 · 여러 개 가능 · 적용하면 바로 반영(되돌리기 없음)</span></div>'
    +'<div id="moswrap" style="position:relative;max-width:92vw;max-height:66vh;touch-action:none;line-height:0">'
    +'<img id="mosimg" src="'+base+'/'+fn+'?t='+Date.now()+'" style="display:block;max-width:92vw;max-height:66vh;border-radius:8px;user-select:none;-webkit-user-select:none" draggable="false">'
    +'<canvas id="moscv" style="position:absolute;left:0;top:0;cursor:crosshair"></canvas></div>'
    +'<div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap;justify-content:center">'
    +'<button class="mini ghost" onclick="mosClear()">↩ 다 지우기</button>'
    +'<button class="mini ghost" onclick="closeMosaic()">취소</button>'
    +'<button class="mini" style="background:#2f7d4f;color:#fff;font-weight:700" onclick="applyMosaic(&#39;'+fn+'&#39;)">✅ 모자이크 적용</button></div>';
  document.body.appendChild(ov);
  const img=$('mosimg');
  if(img.complete && img.naturalWidth) mosSetup(); else img.onload=mosSetup;
}
function mosSetup(){
  const img=$('mosimg'), cv=$('moscv'); if(!cv) return;
  cv.width=img.clientWidth; cv.height=img.clientHeight;
  cv.style.width=img.clientWidth+'px'; cv.style.height=img.clientHeight+'px';
  MOSBOXES=[]; MOSDRAW=null; mosRedraw();
  const pos=e=>{const r=cv.getBoundingClientRect();const t=(e.touches&&e.touches[0])||e;return{x:t.clientX-r.left,y:t.clientY-r.top};};
  const start=e=>{e.preventDefault();const p=pos(e);MOSDRAW={x0:p.x,y0:p.y,x1:p.x,y1:p.y};};
  const move=e=>{if(!MOSDRAW)return;e.preventDefault();const p=pos(e);MOSDRAW.x1=p.x;MOSDRAW.y1=p.y;mosRedraw();};
  const end=()=>{if(!MOSDRAW)return;const b=MOSDRAW;MOSDRAW=null;
    const x=Math.min(b.x0,b.x1),y=Math.min(b.y0,b.y1),w=Math.abs(b.x1-b.x0),h=Math.abs(b.y1-b.y0);
    if(w>6&&h>6&&cv.width&&cv.height)MOSBOXES.push({x:x/cv.width,y:y/cv.height,w:w/cv.width,h:h/cv.height});
    mosRedraw();};
  cv.onmousedown=start;cv.onmousemove=move;cv.onmouseup=end;cv.onmouseleave=end;
  cv.ontouchstart=start;cv.ontouchmove=move;cv.ontouchend=end;
}
function mosRedraw(){
  const cv=$('moscv');if(!cv)return;const ctx=cv.getContext('2d');
  ctx.clearRect(0,0,cv.width,cv.height);
  ctx.fillStyle='rgba(40,90,220,.42)';ctx.strokeStyle='#6ea0ff';ctx.lineWidth=2;
  const drawB=(x,y,w,h)=>{ctx.fillRect(x,y,w,h);ctx.strokeRect(x,y,w,h);};
  MOSBOXES.forEach(b=>drawB(b.x*cv.width,b.y*cv.height,b.w*cv.width,b.h*cv.height));
  if(MOSDRAW){const b=MOSDRAW;drawB(Math.min(b.x0,b.x1),Math.min(b.y0,b.y1),Math.abs(b.x1-b.x0),Math.abs(b.y1-b.y0));}
}
function mosClear(){MOSBOXES=[];MOSDRAW=null;mosRedraw();}
function closeMosaic(){const o=$('mosov');if(o)o.remove();}
async function applyMosaic(fn){
  if(!MOSBOXES.length){alert('가릴 부분을 드래그로 표시하세요');return;}
  const d=await api('/api/pack/mosaic',{code:$('code').value.trim(),pack:CUR.name,base:fn,boxes:MOSBOXES});
  if(!d.ok){alert('❌ '+d.error);return;}
  closeMosaic();
  $('pstat').textContent='🟦 모자이크 적용 완료: '+fn;
  openPack(CUR.name);
}
let IGIDX=0, IGN=0;
function igDots(){
  $('igdots').innerHTML=Array.from({length:IGN},(_,i)=>`<span class="${i===IGIDX?'on':''}"></span>`).join('');
}
function igGo(i){
  IGIDX=Math.max(0,Math.min(IGN-1,i));
  const tr=$('igtrack'); if(tr) tr.style.transform=`translateX(${-IGIDX*100}%)`;
  igDots();
}
function igNav(d){igGo(IGIDX+d);}
function igInit(){
  const vp=$('igvp'), tr=$('igtrack'); if(!vp||!tr) return;
  IGN=tr.children.length; igGo(0);
  let dn=false,sx=0,dx=0,w=1;
  vp.addEventListener('pointerdown',e=>{dn=true;sx=e.clientX;dx=0;w=vp.clientWidth||1;
    tr.style.transition='none'; try{vp.setPointerCapture(e.pointerId);}catch(_){}});
  vp.addEventListener('pointermove',e=>{if(!dn)return;dx=e.clientX-sx;
    tr.style.transform=`translateX(${(-IGIDX*100)+(dx/w*100)}%)`;});
  const end=()=>{if(!dn)return;dn=false;tr.style.transition='';
    if(Math.abs(dx)>w*0.15) igGo(IGIDX-(dx>0?1:-1)); else igGo(IGIDX);};
  vp.addEventListener('pointerup',end);
  vp.addEventListener('pointercancel',end);
  let wlock=false;
  vp.addEventListener('wheel',e=>{const dd=e.deltaX||e.deltaY; if(Math.abs(dd)<6)return;
    e.preventDefault(); if(wlock)return; wlock=true; setTimeout(()=>{wlock=false;},240);
    igNav(dd>0?1:-1);},{passive:false});
}
function copyCap(){navigator.clipboard.writeText($('cap').innerText).then(()=>alert('캡션이 복사됐습니다!'));}
function capEdit(){
  $('capbox').innerHTML=`<textarea id="capta" rows="10">${esc(CUR.caption)}</textarea>
  <button class="mini" onclick="capSave()">💾 캡션 저장</button>`;
}
async function capSave(){
  const d=await api('/api/caption/save',{code:$('code').value.trim(),pack:CUR.name,caption:$('capta').value});
  if(!d.ok){alert('❌ '+d.error);return;}
  CUR.caption=$('capta').value.trim(); renderDetail();
  $('pstat').textContent='💾 캡션 저장 완료 — 다음 업로드부터 반영';
}
function zipDl(){ if(ARRDIRTY) return confirm('제외·순서 변경이 아직 저장 안 됐어요. 저장 전 zip에는 반영되지 않습니다.\\n그래도 받을까요?'); return true; }
async function pub(force){
  if(!CUR||PUBBUSY) return;
  if(ARRDIRTY){alert('⚠ 제외·순서 변경이 저장되지 않았어요.\\n먼저 "💾 순서·제외 저장"을 눌러 반영한 뒤 업로드하세요.');return;}
  if(!force && !confirm('인스타그램에 지금 바로 업로드할까요?\\n\\n'+CUR.name)) return;
  const body={code:$('code').value.trim(),pack:CUR.name,force:!!force};
  if(LEAD) body.lead=LEAD;
  const d=await api('/api/insta/publish',body);
  if(!d.ok){
    if((d.error||'').includes('이미 업로드')){
      if(confirm('이미 올렸던 팩이에요. 강제로 한 번 더 올릴까요?')) pub(true);
      return;
    }
    alert('❌ '+d.error);return;
  }
  PUBBUSY=true;
  pollJob(d.job,'📤',r=>{
    PUBBUSY=false;
    if(r&&r.permalink&&confirm('업로드 완료! 게시물을 열어볼까요?')) window.open(r.permalink,'_blank');
    openPack(CUR.name);
  });
}
function pollJob(jid,icon,onDone){
  const code=$('code').value.trim();
  const t=setInterval(async()=>{
    try{
      const r=await fetch('/api/job/'+jid+'?code='+encodeURIComponent(code));
      const d=await r.json();
      if(!d.ok){clearInterval(t);PUBBUSY=false;EDITBUSY=false;$('pstat').textContent='❌ '+d.error;return;}
      $('pstat').textContent=icon+' '+(d.msg||'작업 중...');
      if(d.status==='error'){clearInterval(t);PUBBUSY=false;EDITBUSY=false;
        $('pstat').textContent='❌ '+d.error;alert('실패: '+d.error);}
      if(d.status==='done'){clearInterval(t);$('pstat').textContent='✅ 완료!';if(onDone)onDone(d.result);}
    }catch(e){}
  },2000);
}
async function makeJP(){
  if(!CUR||PUBBUSY) return;
  if(!confirm('이 팩을 일본어판으로 만들까요?\\n번역 + 일본어 폰트로 카드가 새로 만들어져요.\\n(전자책 제외 — 카드 캐러셀 + 캡션)')) return;
  const d=await api('/api/card/translate',{code:$('code').value.trim(),pack:CUR.name,target:'ja'});
  if(!d.ok){alert('❌ '+d.error);return;}
  PUBBUSY=true; $('pstat').textContent='🇯🇵 일본어판 만드는 중...';
  pollJob(d.job,'🇯🇵',r=>{
    PUBBUSY=false;
    if(r&&r.pack){
      $('pstat').innerHTML=`✅ 일본어판 완성! <a href="/packs/${encodeURIComponent(r.pack)}/review.html" target="_blank"><b>미리보기 ↗</b></a>`;
      loadList();
      if(confirm('일본어판이 완성됐어요! 지금 열어볼까요?')) openPack(r.pack,false);
    }
  });
}
function openEdit(){
  const e=CUR.edit,p=e.plan,teaser=new Set(e.teaser||[]);
  let h='<h3>표지 문구</h3>';
  h+=`<label>윗줄 (작은 글씨)</label><input id="e_top" value="${escA(p.title_top)}">`;
  h+=`<label>메인 제목</label><input id="e_main" value="${escA(p.title_main)}">`;
  h+=`<label>부제</label><input id="e_sub" value="${escA(p.subtitle)}">`;
  h+=`<div class="row"><div><label>댓글 키워드</label><input id="e_kw" value="${escA(p.comment_keyword)}"></div>
  <div><label>전자책 제목</label><input id="e_eb" value="${escA(p.ebook_title)}"></div></div>`;
  h+=`<label>테마</label><select id="e_theme"><option value="">원래 테마 유지</option>
  <option value="hunter">🎮 유튜브 네온 (다크)</option><option value="cream">🧡 크림 클래식 (라이트)</option></select>`;
  h+=`<h3>아이템 문구 <span class="dim" style="font-weight:400">(🃏 = 카드 노출 중 · 줄 형식 "태그: 내용")</span></h3>`;
  const cats={};
  e.items.forEach(it=>{const c=it.category||'기타';(cats[c]=cats[c]||[]).push(it);});
  let ci=0;
  for(const cat in cats){
    h+=`<details${ci===0?' open':''}><summary>${esc(cat)} (${cats[cat].length}개)</summary>`;
    cats[cat].forEach(it=>{
      const lines=it.lines.map(l=>l.tag+': '+l.text).join('\\n');
      h+=`<div class="itbox" data-num="${it.num}">
      <div class="dim">${String(it.num).padStart(2,'0')}번${teaser.has(it.num)?' 🃏 카드 노출':''}</div>
      <div class="row"><input class="e_t" value="${escA(it.title)}" placeholder="제목">
      <input class="e_e" value="${escA(it.emoji||'')}" style="flex:0 0 70px;text-align:center" placeholder="😀"></div>
      <textarea class="e_l" rows="${(it.lines.length||3)+1}">${esc(lines)}</textarea></div>`;
    });
    h+='</details>'; ci++;
  }
  h+='<button onclick="saveEdit()">🔁 저장하고 다시 굽기 (카드+전자책+zip 전부 갱신)</button>';
  $('editbox').innerHTML=h;
  $('e_theme').value=e.theme||'';
  $('editbox').scrollIntoView({behavior:'smooth'});
}
async function saveEdit(){
  if(EDITBUSY) return;
  const items=[...document.querySelectorAll('.itbox')].map(b=>{
    const orig=CUR.edit.items.find(x=>String(x.num)===b.dataset.num)||{};
    const lines=b.querySelector('.e_l').value.split('\\n').map(s=>s.trim()).filter(Boolean).map(s=>{
      const m=s.match(/^(.{1,6}?)\\s*[:：]\\s*(.+)$/);
      return m?{tag:m[1].trim(),text:m[2].trim()}:{tag:'내용',text:s};
    });
    return {num:+b.dataset.num,category:orig.category||'',
      title:b.querySelector('.e_t').value.trim(),
      emoji:b.querySelector('.e_e').value.trim(),lines};
  });
  const empty=items.filter(i=>!i.lines.length);
  if(empty.length){alert('내용이 비어 있는 아이템이 있어요: '+empty.map(i=>i.num+'번').join(', '));return;}
  const plan={title_top:$('e_top').value,title_main:$('e_main').value,subtitle:$('e_sub').value,
    comment_keyword:$('e_kw').value,ebook_title:$('e_eb').value};
  EDITBUSY=true;
  $('pstat').textContent='🔁 다시 굽는 중... (전자책까지 10~30초)';
  const d=await api('/api/card/edit',{code:$('code').value.trim(),pack:CUR.name,plan,items,theme:$('e_theme').value});
  if(!d.ok){EDITBUSY=false;$('pstat').textContent='❌ '+d.error;return;}
  pollJob(d.job,'🔁',()=>{EDITBUSY=false;alert('✅ 수정 반영 완료!');openPack(CUR.name);});
}
if($('code').value) loadList();
$('code').addEventListener('keydown',e=>{if(e.key==='Enter')loadList();});
</script></body></html>"""


IG_ACCOUNTS_FILE = BASE / "ig_accounts.json"     # UI로 추가한 업로드 계정 {name:{user_id,access_token}}
YTKEYS_FILE = BASE / "youtube_keys.json"          # 회원별 유튜브 키 {code:[{key,label,units_today,units_total,day,last}]}
_admin_lock = threading.RLock()   # 재진입 가능(사용량 집계가 락 안에서 save 호출 → 데드락 방지)


def load_config():
    cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    try:  # UI로 추가한 업로드 인스타 계정 병합
        extra = json.loads(IG_ACCOUNTS_FILE.read_text(encoding="utf-8"))
        if isinstance(extra, dict) and extra:
            merged = dict(cfg.get("ig_accounts") or {})
            merged.update(extra)
            cfg["ig_accounts"] = merged
    except Exception:
        pass
    return cfg


def _ig_accounts_list(cfg):
    """설정에 실제로 연동된(빈 값 아닌) 업로드 계정 이름 목록."""
    out = []
    for name, v in (cfg.get("ig_accounts") or {}).items():
        if isinstance(v, dict) and (str(v.get("user_id", "")).strip()
                                    or str(v.get("access_token", "")).strip()):
            out.append(name)
    return out


MEMBERS_FILE = BASE / "members.json"
_members_lock = threading.Lock()

# ── 결과물 팩 소유자 (누가 만들었나) ─────────────────────────────
OWNERS_FILE = BASE / "pack_owners.json"
_owners_lock = threading.Lock()


def _owners_load():
    try:
        return json.loads(OWNERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _owner_set(pack_name, code):
    code = (code or "").strip()
    if not pack_name or not code:
        return
    with _owners_lock:
        d = _owners_load()
        if d.get(pack_name) == code:
            return
        d[pack_name] = code
        OWNERS_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2),
                               encoding="utf-8")


def _members_load(cfg=None):
    """회원코드→{name,role} 사전. 파일 없으면 config의 access_code를 관리자 1명으로 시드."""
    try:
        m = json.loads(MEMBERS_FILE.read_text(encoding="utf-8"))
        if isinstance(m, dict) and m:
            return m
    except Exception:
        pass
    cfg = cfg or load_config()
    seed = str(cfg.get("access_code", "") or "").strip()
    return {seed: {"name": "관리자", "role": "admin"}} if seed else {}


def _members_save(m):
    with _members_lock:
        MEMBERS_FILE.write_text(json.dumps(m, ensure_ascii=False, indent=2),
                                encoding="utf-8")


def _member(cfg, code):
    """유효한 회원이면 {name,role} 반환, 아니면 None. access_code는 항상 관리자로 인정."""
    code = (code or "").strip()
    if not code:
        return None
    if code == str(cfg.get("access_code", "") or "").strip():
        return {"name": "관리자", "role": "admin"}
    return _members_load(cfg).get(code)


def _check_code(cfg, code):
    return _member(cfg, code) is not None


def _role_for(cfg, code):
    m = _member(cfg, code)
    return m.get("role") if m else None


def _is_admin(cfg, code):
    return _role_for(cfg, code) == "admin"


def _pack_payload(result):
    pack = result["pack"]
    rel = pack.name
    meta = result.get("meta") or {}
    thumbs = sorted(p.name for p in pack.glob("thumb*.jpg"))
    images = sorted(p.name for p in pack.glob("[0-9][0-9].jpg"))
    srcs = sorted(p.name for p in pack.glob("src[0-9][0-9].jpg"))
    zips = list(pack.glob("*.zip"))
    return {
        "pack": rel,
        "skip": meta.get("skip", False),
        "skip_reason": meta.get("skip_reason", ""),
        "caption": result["caption"],
        "thumbs": [f"/packs/{rel}/{t}" for t in thumbs],
        "images": [f"/packs/{rel}/{i}" for i in images],
        "zip": f"/packs/{rel}/{zips[0].name}" if zips else "",
        "lang": meta.get("lang", ""),
        "story": meta.get("template") == "story",
        "hooks": meta.get("hooks", []),          # 헤드라인 한국어 해석(hk.ko) 포함
        "cards_ko": meta.get("cards_ko", []),    # 카드별 본문 한국어 해석
        "srcs": [f"/packs/{rel}/{s}" for s in srcs],  # 커버 교체용 원본사진
        "thumb_src": meta.get("thumb_src", ""),
    }


def _run_job(jid, url, cfg, stats, template=None, clean=None, guide=""):
    job = JOBS[jid]

    def log(m):
        m = str(m).strip()
        job["msg"] = m
        step = re.match(r"\[(\d)/4\]", m)
        if step:
            job["pct"] = STEP_PCT.get(int(step.group(1)), job["pct"])

    try:
        result = pipeline.build_from_url(url, cfg, BASE, log=log, stats=stats,
                                         template=template, clean=clean, guide=guide)
        job["result"] = _pack_payload(result)
        job["pct"] = 100
        job["status"] = "done"
    except Exception as e:
        job["error"] = str(e)
        job["status"] = "error"
    finally:
        _pending_remove(jid)


def _run_youtube_job(jid, url, cfg, blur=True):
    """🎬 유튜브 쇼츠 → 짤 완성팩 잡 (대본 장문 + 프레임 + 자막 블러)."""
    job = JOBS[jid]

    def log(m):
        m = str(m).strip()
        job["msg"] = m
        step = re.match(r"\[(\d)/5\]", m)
        if step:
            job["pct"] = {1: 12, 2: 35, 3: 62, 4: 84, 5: 94}.get(
                int(step.group(1)), job["pct"])

    try:
        result = youtube.build_from_youtube(url, cfg, BASE, log=log, blur=blur)
        job["result"] = _pack_payload(result)
        job["pct"] = 100
        job["status"] = "done"
    except Exception as e:
        job["error"] = str(e)
        job["status"] = "error"
    finally:
        _pending_remove(jid)


def _run_images_job(jid, image_paths, caption, localize, cfg, template=None, clean=None,
                    guide=""):
    """🌏 업로드한 해외 게시물 캡처 → 짤공장 커뮤형 팩(현지화) 잡."""
    job = JOBS[jid]

    def log(m):
        m = str(m).strip()
        job["msg"] = m
        step = re.match(r"\[(\d)/4\]", m)
        if step:
            job["pct"] = STEP_PCT.get(int(step.group(1)), job["pct"])

    try:
        result = pipeline.build_from_images(image_paths, cfg, BASE, log=log,
                                            localize=localize, caption=caption,
                                            template=template, clean=clean, guide=guide)
        job["result"] = _pack_payload(result)
        job["pct"] = 100
        job["status"] = "done"
    except Exception as e:
        job["error"] = str(e)
        job["status"] = "error"
    finally:
        for p in image_paths:
            try:
                Path(p).unlink()
            except Exception:
                pass



def _run_card_job(jid, topic, n_items, keyword, cfg, mode="normal", context=None,
                  ref_images=None, ref_caption="", auto_upload=False, make_ebook=True,
                  account=None):
    job = JOBS[jid]

    def log(m):
        m = str(m).strip()
        job["msg"] = m
        step = re.match(r"\[(\d)/4\]", m)
        if step:
            job["pct"] = STEP_PCT.get(int(step.group(1)), job["pct"])
        prog = re.search(r"집필 (\d+)/(\d+)개", m)  # 집필 단계는 45→78% 보간
        if prog and int(prog.group(2)):
            job["pct"] = 45 + int(33 * int(prog.group(1)) / int(prog.group(2)))

    try:
        if mode == "localize":
            result = card_pipeline.build_from_reference(
                ref_images or [], ref_caption, cfg, BASE, n_items=n_items,
                keyword=keyword or None, log=log)
        elif mode == "story":
            result = card_pipeline.build_story(
                topic, cfg, BASE, keyword=keyword or None, log=log)
        else:
            result = card_pipeline.build_cardnews(
                topic, cfg, BASE, n_items=n_items, keyword=keyword or None,
                proof=(mode == "proof"), context=context, make_ebook=make_ebook,
                account=account, log=log)
        rel = result["pack"].name
        job["result"] = {
            "pack": rel,
            "cards": [f"/packs/{rel}/{c}" for c in result["cards"]],
            "caption": result["caption"],
            "ebook": (f"/packs/{rel}/ebook.pdf"
                      if (result["pack"] / "ebook.pdf").exists() else ""),
            "zip": f"/packs/{rel}/{rel}.zip",
            "keyword": result["meta"]["keyword"],
            "title": result["meta"]["title"],
        }
        if auto_upload:   # 자동업로드 토글 켠 건만 — 생성 직후 바로 공개 게시
            try:
                log("[업로드] 📤 인스타 자동 게시 중...")
                pub = insta.publish_pack(cfg, BASE, result["pack"], log=log)
                job["result"]["insta"] = True
                job["result"]["permalink"] = pub.get("permalink", "")
                log("[업로드] ✅ 인스타 게시 완료")
            except Exception as e:
                job["result"]["insta_error"] = str(e)
                log(f"[업로드] ⚠️ 자동 게시 실패 (팩은 저장됨 — 수동 업로드 가능): {str(e)[:80]}")
        job["pct"] = 100
        job["status"] = "done"
    except Exception as e:
        job["error"] = str(e)
        job["status"] = "error"


def _run_translate_job(jid, pack_name, target, cfg):
    """완성 카드뉴스 팩 → 해외 발행용 번역판(예: 일본어) 제작 잡."""
    job = JOBS[jid]

    def log(m):
        m = str(m).strip()
        job["msg"] = m
        step = re.match(r"\[(\d)/4\]", m)
        if step:
            job["pct"] = STEP_PCT.get(int(step.group(1)), job["pct"])
        prog = re.search(r"번역 (\d+)/(\d+)개", m)  # 번역 단계 45→78% 보간
        if prog and int(prog.group(2)):
            job["pct"] = 45 + int(33 * int(prog.group(1)) / int(prog.group(2)))

    try:
        src = BASE / cfg.get("output_dir", "결과물") / pack_name
        if not (src / "items.json").exists():
            raise RuntimeError("원본 카드뉴스 팩을 찾을 수 없어요")
        result = card_pipeline.build_translated(src, cfg, BASE, target=target, log=log)
        rel = result["pack"].name
        job["result"] = {
            "pack": rel,
            "cards": [f"/packs/{rel}/{c}" for c in result["cards"]],
            "caption": result["caption"], "ebook": "",
            "zip": f"/packs/{rel}/{rel}.zip",
            "keyword": result["meta"]["keyword"],
            "title": result["meta"]["title"],
        }
        job["pct"] = 100
        job["status"] = "done"
    except Exception as e:
        job["error"] = str(e)
        job["status"] = "error"



@app.get("/")
def index():
    return INDEX_HTML


@app.get("/card")
def card_page():
    return CARD_HTML


@app.get("/p")
def packs_page():
    return PACKS_HTML


@app.get("/v2")
def v2_page():
    """새 PC 가로형 UI (개발 중) — 기존 /, /card, /p 는 그대로 유지."""
    try:
        return (BASE / "v2.html").read_text(encoding="utf-8")
    except Exception as e:
        return f"v2.html 로드 실패: {e}", 500


@app.post("/api/card/make")
def api_card_make():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    topic = (data.get("topic") or "").strip()
    if len(topic) < 4:
        return jsonify(ok=False, error="주제를 조금 더 구체적으로 적어주세요"), 400
    try:
        n_items = max(10, min(80, int(data.get("items") or 60)))
    except (TypeError, ValueError):
        n_items = 60
    theme = (data.get("theme") or "").strip()
    if theme in ("hunter", "cream", "news", "punch", "info", "pastel"):
        cfg = dict(cfg)
        cfg["card_theme"] = theme
    accent = (data.get("accent") or "").strip()
    if re.fullmatch(r"#?[0-9A-Fa-f]{6}", accent):
        cfg = dict(cfg)
        cfg["card_accent"] = accent if accent.startswith("#") else "#" + accent
    # 표지 이미지 (뉴스 테마): 스톡 URL 또는 업로드(base64) → 임시파일로 저장
    cover_url = (data.get("cover_url") or "").strip()
    cover_data = data.get("cover_data")
    if cover_url or cover_data:
        try:
            import io as _io
            from PIL import Image as _Img
            covdir = BASE / "_covertmp"
            covdir.mkdir(exist_ok=True)
            cpath = covdir / (uuid.uuid4().hex[:12] + ".jpg")
            raw = _decode_data_url(cover_data) if cover_data else stock.download(cover_url)
            _Img.open(_io.BytesIO(raw)).convert("RGB").save(cpath, "JPEG", quality=90)
            cfg = dict(cfg)
            cfg["cover_image"] = str(cpath)
        except Exception:
            pass
    if data.get("auto_cover") is False:   # 표지 자동사진 토글 끔
        cfg = dict(cfg)
        cfg["card_auto_cover"] = False
    if data.get("body_images") is True:   # 본문 사이 사진 토글 켬
        cfg = dict(cfg)
        cfg["card_body_images"] = True
    mode = (data.get("mode") or "normal").strip()
    if mode not in ("normal", "proof", "story"):
        mode = "normal"
    context = str(data.get("context") or "").strip()[:2000] or None
    auto_upload = bool(data.get("auto_upload"))   # 자동업로드 토글
    now = time.time()
    for k in [k for k, v in JOBS.items() if now - v["ts"] > 3600]:
        JOBS.pop(k, None)
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = {"status": "queued", "pct": 0, "msg": "대기 중...",
                 "result": None, "error": None, "ts": now}
    JOBQ.put((jid, _run_card_job,
              (jid, topic, n_items, (data.get("keyword") or "").strip(),
               cfg, mode, context, None, "", auto_upload,
               data.get("ebook") is not False,
               (data.get("account") or "").strip() or None)))
    return jsonify(ok=True, job=jid)


@app.post("/api/card/localize")
def api_card_localize():
    """🌏 해외 게시물 캡처(+캡션 원문) → 한국 타깃 카드로 변환."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    images = data.get("images") or []
    if not isinstance(images, list) or not images:
        return jsonify(ok=False, error="해외 게시물 캡처를 1장 이상 올려주세요"), 400
    import io as _io
    from PIL import Image as _Img
    refdir = BASE / "_reftmp"
    refdir.mkdir(exist_ok=True)
    paths = []
    for item in images[:8]:
        try:
            raw = _decode_data_url(item)
            rpath = refdir / (uuid.uuid4().hex[:12] + ".jpg")
            _Img.open(_io.BytesIO(raw)).convert("RGB").save(rpath, "JPEG", quality=88)
            paths.append(str(rpath))
        except Exception:
            continue
    if not paths:
        return jsonify(ok=False, error="이미지를 읽지 못했어요 — 다른 캡처로 시도해주세요"), 400
    caption = str(data.get("caption") or "").strip()[:3000]
    try:
        n_items = max(10, min(80, int(data.get("items") or 60)))
    except (TypeError, ValueError):
        n_items = 60
    theme = (data.get("theme") or "").strip()
    if theme in ("hunter", "cream", "news", "punch", "info", "pastel"):
        cfg = dict(cfg)
        cfg["card_theme"] = theme
    now = time.time()
    for k in [k for k, v in JOBS.items() if now - v["ts"] > 3600]:
        JOBS.pop(k, None)
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = {"status": "queued", "pct": 0, "msg": "대기 중...",
                 "result": None, "error": None, "ts": now}
    JOBQ.put((jid, _run_card_job,
              (jid, "", n_items, (data.get("keyword") or "").strip(),
               cfg, "localize", None, paths, caption)))
    return jsonify(ok=True, job=jid)


def _run_youtube_translate_job(jid, pack_name, target, cfg):
    """유튜브 짤 팩 → 도착 언어 번역·재렌더 잡."""
    job = JOBS[jid]

    def log(m):
        m = str(m).strip()
        job["msg"] = m
        step = re.match(r"\[(\d)/3\]", m)
        if step:
            job["pct"] = {1: 30, 2: 65, 3: 88}.get(int(step.group(1)), job["pct"])

    try:
        result = youtube.build_youtube_translated(OUTPUT / pack_name, cfg, BASE,
                                                  target=target, log=log)
        job["result"] = _pack_payload(result)
        job["pct"] = 100
        job["status"] = "done"
    except Exception as e:
        job["error"] = str(e)
        job["status"] = "error"


@app.post("/api/pack/translate")
def api_pack_translate():
    """짤·카드뉴스 팩 → 도착 언어(ja/en). source.json=유튜브짤, items.json=카드뉴스."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    pack = (data.get("pack") or "").strip()
    if not pack or "/" in pack or "\\" in pack or ".." in pack:
        return jsonify(ok=False, error="팩 이름이 올바르지 않습니다"), 400
    d = OUTPUT / pack
    target = (data.get("target") or "ja").strip()
    if target not in ("ja", "en"):
        target = "ja"
    now = time.time()
    for k in [k for k, v in JOBS.items() if now - v["ts"] > 3600]:
        JOBS.pop(k, None)
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = {"status": "queued", "pct": 0, "msg": "대기 중...",
                 "result": None, "error": None, "ts": now}
    if (d / "source.json").exists():
        JOBQ.put((jid, _run_youtube_translate_job, (jid, pack, target, cfg)))
    elif (d / "items.json").exists():
        JOBQ.put((jid, _run_translate_job, (jid, pack, target, cfg)))
    else:
        return jsonify(ok=False, error="이 팩은 수출(번역) 데이터가 없어요"), 400
    return jsonify(ok=True, job=jid)


@app.post("/api/card/translate")
def api_card_translate():
    """🇯🇵 완성된 카드뉴스 팩 → 해외 발행용 번역판(현재 일본어)."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    pack = (data.get("pack") or "").strip()
    if not pack or "/" in pack or "\\" in pack or ".." in pack:
        return jsonify(ok=False, error="팩 이름이 올바르지 않습니다"), 400
    src = BASE / cfg.get("output_dir", "결과물") / pack
    if not (src / "items.json").exists():
        return jsonify(ok=False,
                       error="이 팩은 번역 데이터가 없어요 — 카드뉴스 팩만 일본어판 제작이 가능해요"), 400
    target = (data.get("target") or "ja").strip()
    if target not in ("ja", "en"):
        target = "ja"
    now = time.time()
    for k in [k for k, v in JOBS.items() if now - v["ts"] > 3600]:
        JOBS.pop(k, None)
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = {"status": "queued", "pct": 0, "msg": "대기 중...",
                 "result": None, "error": None, "ts": now}
    JOBQ.put((jid, _run_translate_job, (jid, pack, target, cfg)))
    return jsonify(ok=True, job=jid)


@app.post("/api/card/trends")
def api_card_trends():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    now = time.time()
    if now - NEWS_CACHE["time"] > 1800 or not NEWS_CACHE["data"]:
        NEWS_CACHE["data"] = card_news.fetch_topics(cfg)
        NEWS_CACHE["time"] = now
    return jsonify(ok=True, items=NEWS_CACHE["data"])


@app.post("/api/card/insights")
def api_card_insights():
    """AI 인사이트 주제 추천 — Gemini 웹검색(A) + 유튜브 Data API(B). 30분 캐시."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    focus = (data.get("query") or "").strip()[:80] or None
    now = time.time()
    if focus or data.get("refresh"):
        d = insights.fetch(cfg, n=8, focus=focus)  # 맞춤 검색/리롤은 항상 새로
        if not focus:  # 리롤이면 기본 캐시도 갱신
            INSIGHT_CACHE["data"], INSIGHT_CACHE["time"] = d, now
    else:
        if INSIGHT_CACHE["data"] is None or now - INSIGHT_CACHE["time"] > 1800:
            INSIGHT_CACHE["data"] = insights.fetch(cfg, n=8)
            INSIGHT_CACHE["time"] = now
        d = INSIGHT_CACHE["data"]
    d = d or {"items": [], "youtube_ready": False}
    return jsonify(ok=True, items=d.get("items", []),
                   youtube_ready=d.get("youtube_ready", False))


@app.post("/api/card/stock")
def api_card_stock():
    """뉴스 표지용 스톡 이미지 후보 검색 (키리스)."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    q = (data.get("query") or "").strip()
    if not q:
        return jsonify(ok=False, error="검색어(주제)가 필요해요"), 400
    return jsonify(ok=True, images=stock.search(q, 9))


def _template(data):
    """요청의 짤공장 템플릿 선택값 (없으면 None → config 기본값 사용)"""
    t = (data.get("template") or "").strip()
    return t if t in ("classic", "plain", "story") else None


def _clean(data):
    """짤에 박힌 글씨 처리 방식 (none / bar / ai)"""
    c = (data.get("clean") or "").strip()
    return c if c in ("none", "bar", "ai") else None


def _decode_data_url(s):
    """data:URL 또는 순수 base64 문자열 → bytes"""
    s = (s or "").strip()
    if "," in s and s[:5].lower() == "data:":
        s = s.split(",", 1)[1]
    return base64.b64decode(s)


@app.post("/api/style/list")
def api_style_list():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    return jsonify(ok=True, styles=styles.load_styles(BASE))


def _pdf_to_images(pdf_bytes, max_pages=4, dpi=130):
    """PDF 바이트 → 앞쪽 페이지들을 JPEG 바이트 리스트로 래스터라이즈(PyMuPDF)."""
    import fitz  # PyMuPDF
    out = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for i in range(min(max_pages, doc.page_count)):
            pix = doc.load_page(i).get_pixmap(dpi=dpi)
            out.append(pix.tobytes("jpeg"))
    finally:
        doc.close()
    return out


def _ref_blobs(imgs):
    """이미지/PDF data URL 목록 → 이미지 바이트 리스트(PDF는 페이지 래스터, 최대 4개)."""
    blobs = []
    for x in imgs:
        raw = _decode_data_url(x)
        if (isinstance(x, str) and "application/pdf" in x[:64]) or raw[:5] == b"%PDF-":
            blobs.extend(_pdf_to_images(raw, max_pages=4))
        else:
            blobs.append(raw)
        if len(blobs) >= 4:
            break
    return blobs[:4]


@app.post("/api/style/analyze")
def api_style_analyze():
    """참고 이미지/PDF → '스타일'(내용 구성) 프리셋 저장. Gemini로 톤·구조 분석."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    imgs = data.get("images") or ([data["image"]] if data.get("image") else [])
    if not imgs:
        return jsonify(ok=False, error="참고 이미지나 PDF를 최소 1개 올려주세요"), 400
    try:
        blobs = _ref_blobs(imgs)
    except Exception:
        return jsonify(ok=False, error="이미지/PDF 형식을 읽지 못했어요"), 400
    if not blobs:
        return jsonify(ok=False, error="파일에서 이미지를 읽지 못했어요"), 400
    try:
        preset = styles.analyze_reference(blobs, cfg)
    except Exception as e:
        return jsonify(ok=False, error=f"분석 실패: {e}"), 500
    thumb = styles.make_thumb(blobs[0])
    saved = styles.save_style(BASE, preset, thumb_b64=thumb)
    return jsonify(ok=True, style=saved)


@app.post("/api/template/analyze")
def api_template_analyze():
    """참고 이미지/PDF → '템플릿'(이미지/비주얼: 테마·포인트색) 저장. 색만 뽑아 빠름."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    imgs = data.get("images") or ([data["image"]] if data.get("image") else [])
    if not imgs:
        return jsonify(ok=False, error="참고 이미지나 PDF를 최소 1개 올려주세요"), 400
    try:
        blobs = _ref_blobs(imgs)
    except Exception:
        return jsonify(ok=False, error="이미지/PDF 형식을 읽지 못했어요"), 400
    if not blobs:
        return jsonify(ok=False, error="파일에서 이미지를 읽지 못했어요"), 400
    try:
        tpl = styles.analyze_template(blobs)
    except Exception as e:
        return jsonify(ok=False, error=f"분석 실패: {e}"), 500
    thumb = styles.make_thumb(blobs[0])
    saved = styles.save_template(BASE, tpl, thumb_b64=thumb)
    return jsonify(ok=True, template=saved)


@app.post("/api/template/list")
def api_template_list():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    return jsonify(ok=True, templates=styles.load_templates(BASE))


@app.post("/api/template/delete")
def api_template_delete():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    tid = (data.get("id") or "").strip()
    if not tid:
        return jsonify(ok=False, error="삭제할 템플릿 id가 없어요"), 400
    styles.delete_template(BASE, tid)
    return jsonify(ok=True)


@app.post("/api/style/delete")
def api_style_delete():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    sid = (data.get("id") or "").strip()
    if not sid:
        return jsonify(ok=False, error="삭제할 스타일 id가 없어요"), 400
    styles.delete_style(BASE, sid)
    return jsonify(ok=True)


@app.get("/themeprev/<name>")
def themeprev(name):
    if not re.fullmatch(r"[a-z]+", name or ""):
        return "", 404
    return send_from_directory(BASE / "assets" / "themes", name + ".jpg", max_age=3600)


@app.get("/memeprev/<name>")
def memeprev(name):
    """짤공장 템플릿 미리보기 (classic / plain / story)"""
    if name not in ("classic", "plain", "story"):
        return "", 404
    return send_from_directory(BASE / "assets" / "memethemes", name + ".jpg",
                               max_age=3600)


@app.get("/logo.png")
def logo():
    return send_from_directory(BASE / "assets", "logo.png")


@app.get("/logo-origami.png")
def logo_origami():
    return send_from_directory(BASE / "assets", "logo_origami.png", max_age=86400)


@app.get("/logo-card.png")
def logo_card():
    return send_from_directory(BASE / "assets", "logo_card.png", max_age=86400)


@app.get("/favicon.ico")
def favicon():
    return send_from_directory(BASE / "assets", "logo_origami.png", max_age=86400)


@app.get("/fonts/<path:name>")
def serve_font(name):
    return send_from_directory(BASE / "assets" / "fonts", name, max_age=2592000)


@app.post("/api/make")
def api_make():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    url = (data.get("url") or "").strip()
    if not url.startswith("http"):
        return jsonify(ok=False, error="링크가 올바르지 않습니다 (http로 시작해야 함)"), 400
    # 오래된 작업 정리
    now = time.time()
    for k in [k for k, v in JOBS.items() if now - v["ts"] > 3600]:
        JOBS.pop(k, None)
    stats = next((i for i in HUNT_CACHE["data"] if i["url"] == url), None)
    template = _template(data)
    guide = (data.get("guide") or "").strip()[:500]
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = {"status": "queued", "pct": 0, "msg": "대기 중...",
                 "result": None, "error": None, "ts": now}
    _pending_add(jid, url)
    JOBQ.put((jid, _run_job, (jid, url, cfg, stats, template, _clean(data), guide)))
    return jsonify(ok=True, job=jid)


@app.post("/api/youtube/make")
def api_youtube_make():
    """🎬 유튜브 쇼츠 URL → 짤 완성팩 (대본 장문 + 프레임 + 자막 블러)."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    url = (data.get("url") or "").strip()
    if not re.search(r"(?:youtube\.com|youtu\.be)/", url):
        return jsonify(ok=False, error="유튜브 링크가 아니에요 (youtube.com / youtu.be)"), 400
    blur = data.get("blur") is not False   # 기본 켬, 명시적 false면 자막 블러 끔
    now = time.time()
    for k in [k for k, v in JOBS.items() if now - v["ts"] > 3600]:
        JOBS.pop(k, None)
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = {"status": "queued", "pct": 0, "msg": "대기 중...",
                 "result": None, "error": None, "ts": now}
    JOBQ.put((jid, _run_youtube_job, (jid, url, cfg, blur)))
    return jsonify(ok=True, job=jid)


YT_TREND_CACHE = {"data": None, "time": 0}


@app.post("/api/youtube/trending")
def api_youtube_trending():
    """인기 쇼츠 트렌딩 (한국·미국·일본 각 탑10). 6시간 캐시 + refresh."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    now = time.time()
    if data.get("refresh") or not YT_TREND_CACHE["data"] or now - YT_TREND_CACHE["time"] > 21600:
        key = _resolve_yt_key(cfg, data.get("code"))
        cfg2 = dict(cfg); cfg2["youtube_api_key"] = key
        try:
            YT_TREND_CACHE["data"] = youtube.trending_shorts(cfg2)
            YT_TREND_CACHE["time"] = now
            _yt_usage_add(data.get("code"), key, 303)   # 3지역 × (검색100+videos1) 추정
        except Exception as e:
            return jsonify(ok=False, error=str(e)), 500
    return jsonify(ok=True, items=YT_TREND_CACHE["data"] or [])


@app.post("/api/youtube/search")
def api_youtube_search():
    """검색어로 인기 쇼츠 검색(조회순, 한국어면 영/일 번역검색). count 30/50/100."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    q = (data.get("query") or "").strip()
    if not q:
        return jsonify(ok=False, error="검색어를 입력하세요"), 400
    try:
        count = max(10, min(100, int(data.get("count") or 30)))
    except (TypeError, ValueError):
        count = 30
    key = _resolve_yt_key(cfg, data.get("code"))
    cfg2 = dict(cfg); cfg2["youtube_api_key"] = key
    try:
        items = youtube.search_shorts(cfg2, q, count=count)
        _yt_usage_add(data.get("code"), key, 300)   # 검색어 1~3개 × 100 + videos 추정
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500
    return jsonify(ok=True, items=items)


@app.post("/api/make_images")
def api_make_images():
    """🌏 해외 인기글 캡처 이미지 → 짤공장 커뮤형 팩 (한국 현지화)."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    images = data.get("images") or []
    if not isinstance(images, list) or not images:
        return jsonify(ok=False, error="해외 게시물 캡처를 1장 이상 올려주세요"), 400
    import io as _io
    from PIL import Image as _Img
    tmpdir = BASE / "_memtmp"
    tmpdir.mkdir(exist_ok=True)
    paths = []
    for item in images[:12]:
        try:
            raw = _decode_data_url(item)
            rpath = tmpdir / (uuid.uuid4().hex[:12] + ".jpg")
            _Img.open(_io.BytesIO(raw)).convert("RGB").save(rpath, "JPEG", quality=92)
            paths.append(str(rpath))
        except Exception:
            continue
    if not paths:
        return jsonify(ok=False, error="이미지를 읽지 못했어요 — 다른 캡처로 시도해주세요"), 400
    caption = str(data.get("caption") or "").strip()[:3000]
    localize = data.get("localize", True) is not False
    guide = (data.get("guide") or "").strip()[:500]
    now = time.time()
    for k in [k for k, v in JOBS.items() if now - v["ts"] > 3600]:
        JOBS.pop(k, None)
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = {"status": "queued", "pct": 0, "msg": "대기 중...",
                 "result": None, "error": None, "ts": now}
    JOBQ.put((jid, _run_images_job,
              (jid, paths, caption, localize, cfg, _template(data), _clean(data), guide)))
    return jsonify(ok=True, job=jid)


def _run_reel_job(jid, video_path, video_url, caption, account, cfg):
    """업로드된 영상 → 인스타 릴스 발행 잡. 발행 완료되면 영상 파일 정리."""
    job = JOBS[jid]

    def log(m):
        m = str(m).strip()
        job["msg"] = m
        step = re.match(r"\[(\d)/3\]", m)
        if step:
            job["pct"] = {1: 20, 2: 55, 3: 85}.get(int(step.group(1)), job["pct"])

    try:
        result = insta.publish_reel(cfg, BASE, video_url, caption,
                                    account=account, log=log)
        job["result"] = {"reel": True, "insta": True,
                         "permalink": result.get("permalink", ""),
                         "account": result.get("account", "")}
        job["pct"] = 100
        job["status"] = "done"
        try:
            Path(video_path).unlink()   # 발행 완료 → 영상 파일 정리(용량)
        except OSError:
            pass
    except Exception as e:
        job["error"] = str(e)
        job["status"] = "error"


def _run_reel_caption_job(jid, video_path, video_name, kind, hint, cfg):
    """영상 → Gemini가 보고 캡션 생성 잡. 영상은 삭제 안 함(발행 때 재활용)."""
    job = JOBS[jid]

    def log(m):
        job["msg"] = str(m).strip()

    try:
        job["pct"] = 30
        cap = brain.caption_video(cfg, video_path, kind=kind, hint=hint, log=log)
        job["result"] = {"caption_only": True, "caption": cap, "video": video_name}
        job["pct"] = 100
        job["status"] = "done"
    except Exception as e:
        job["error"] = str(e)
        job["status"] = "error"


def _stage_reel_video(now):
    """요청에서 영상 확보 — 이미 올린 video_name(스테이징) 우선, 없으면 업로드 파일 저장.
    반환: (name, None) 성공 / (None, (response, status)) 에러."""
    vdir = OUTPUT / "_videos"
    vdir.mkdir(exist_ok=True)
    staged = (request.form.get("video_name") or "").strip()
    if staged and re.fullmatch(r"[0-9a-f]{16}\.mp4", staged) and (vdir / staged).exists():
        return staged, None   # 캡션 생성 때 이미 올린 영상 재활용 (이중 업로드 방지)
    f = request.files.get("video")
    if not f or not f.filename:
        return None, (jsonify(ok=False, error="영상 파일을 올려주세요"), 400)
    if Path(f.filename).suffix.lower() not in (".mp4", ".mov", ".m4v"):
        return None, (jsonify(ok=False, error="MP4/MOV 영상만 올려주세요"), 400)
    for old in vdir.glob("*.mp4"):     # 1시간 지난 옛 영상 정리(용량). 예약(sched_)은 건드리지 않음
        try:
            if not old.name.startswith("sched_") and now - old.stat().st_mtime > 3600:
                old.unlink()
        except OSError:
            pass
    name = uuid.uuid4().hex[:16] + ".mp4"
    vpath = vdir / name
    f.save(str(vpath))
    if not vpath.exists() or vpath.stat().st_size < 10000:
        try:
            vpath.unlink()
        except OSError:
            pass
        return None, (jsonify(ok=False, error="영상이 너무 작거나 저장에 실패했어요"), 400)
    return name, None


def _reel_kind(cfg, account):
    """계정 → 캡션 톤. story 계정=일본어, 카드뉴스 계정=정보형, 나머지=커뮤형."""
    story = (cfg.get("ig_route") or {}).get("story")
    if account and account == story:
        return "story_ja"
    if account == "kangarooshort":
        return "cardnews"
    return "meme"


@app.post("/api/reel/caption")
def api_reel_caption():
    """영상 업로드 → Gemini가 영상 보고 캡션 생성(계정 톤별). 영상은 스테이징해 발행 때 재활용."""
    cfg = load_config()
    if not _check_code(cfg, request.form.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    now = time.time()
    name, err = _stage_reel_video(now)
    if err:
        return err
    account = (request.form.get("account") or "").strip() or None
    hint = (request.form.get("hint") or "").strip()[:300]
    kind = _reel_kind(cfg, account)
    for k in [k for k, v in JOBS.items() if now - v["ts"] > 3600]:
        JOBS.pop(k, None)
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = {"status": "queued", "pct": 0, "msg": "대기 중...",
                 "result": None, "error": None, "ts": now}
    JOBQ.put((jid, _run_reel_caption_job,
              (jid, str(OUTPUT / "_videos" / name), name, kind, hint, cfg)))
    return jsonify(ok=True, job=jid)


@app.post("/api/reel/upload")
def api_reel_upload():
    """영상(MP4) 업로드 → 인스타 릴스 자동 게시 (multipart/form-data)."""
    cfg = load_config()
    if not _check_code(cfg, request.form.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    if not _is_admin(cfg, request.form.get("code")):
        return jsonify(ok=False, error="즉시 업로드는 관리자만 가능합니다. 예약을 이용하세요."), 403
    now = time.time()
    name, err = _stage_reel_video(now)
    if err:
        return err
    caption = (request.form.get("caption") or "").strip()
    account = (request.form.get("account") or "").strip() or None
    public = (cfg.get("public_base_url") or "https://jjal.traffic-charger.com").rstrip("/")
    video_url = f"{public}/packs/_videos/{name}"
    for k in [k for k, v in JOBS.items() if now - v["ts"] > 3600]:
        JOBS.pop(k, None)
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = {"status": "queued", "pct": 0, "msg": "대기 중...",
                 "result": None, "error": None, "ts": now}
    JOBQ.put((jid, _run_reel_job,
              (jid, str(OUTPUT / "_videos" / name), video_url, caption, account, cfg)))
    return jsonify(ok=True, job=jid)


@app.post("/api/reel/schedule")
def api_reel_schedule():
    """영상(MP4) + 시간 → 릴스 예약. 영상은 예약 시간까지 보관(sched_), 스케줄러가 게시."""
    cfg = load_config()
    code = request.form.get("code")
    if not _check_code(cfg, code):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    try:
        ts = float(request.form.get("ts") or 0)
    except (TypeError, ValueError):
        ts = 0
    if ts <= time.time() + 30:
        return jsonify(ok=False, error="예약 시간은 현재보다 미래여야 합니다"), 400
    now = time.time()
    name, err = _stage_reel_video(now)
    if err:
        return err
    # 예약 영상은 오래 보관해야 하므로 'sched_' 접두로 이름 변경(자동정리 제외)
    vdir = OUTPUT / "_videos"
    sname = "sched_" + uuid.uuid4().hex[:14] + ".mp4"
    try:
        (vdir / name).rename(vdir / sname)
    except OSError:
        sname = name
    who = _member(cfg, code) or {}
    admin = who.get("role") == "admin"
    entry = {"id": uuid.uuid4().hex[:10], "type": "reel", "pack": "",
             "video": sname,
             "account": (request.form.get("account") or "").strip(),
             "caption": (request.form.get("caption") or "").strip(),
             "ts": ts, "when": (request.form.get("when") or "").strip(),
             "title": (request.form.get("title") or "릴스 영상").strip(),
             "status": "pending" if admin else "await",
             "by_code": (code or "").strip(), "by_name": who.get("name", ""),
             "by_role": who.get("role", "user"),
             "created": datetime.now().isoformat(timespec="seconds")}
    items = _sched_load()
    items.append(entry)
    _sched_save(items)
    return jsonify(ok=True, id=entry["id"], status=entry["status"])


@app.get("/api/job/<jid>")
def api_job(jid):
    cfg = load_config()
    if not _check_code(cfg, request.args.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    job = JOBS.get(jid)
    if not job:
        return jsonify(ok=False, error="작업을 찾을 수 없습니다 (만료됐을 수 있음)"), 404
    pos = 0
    if job["status"] == "queued":
        pos = sum(1 for v in JOBS.values()
                  if v["status"] == "queued" and v["ts"] <= job["ts"])
    # 팩을 만든 잡이 끝나면, 이 잡을 폴링하는 사람(=만든 사람)을 소유자로 기록
    if job["status"] == "done" and isinstance(job.get("result"), dict):
        pk = job["result"].get("pack")
        if pk:
            _owner_set(pk, request.args.get("code"))
    return jsonify(ok=True, status=job["status"], pct=job["pct"], msg=job["msg"],
                   pos=pos, error=job["error"],
                   result=job["result"] if job["status"] == "done" else None)


@app.post("/api/candidates")
def api_candidates():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    now = time.time()
    if now - HUNT_CACHE["time"] > 600 or not HUNT_CACHE["data"]:
        items = hunter.hunt(BASE, per_site=10, block_keywords=cfg.get("block_keywords"))
        scores = brain.score_debate(cfg, [i["title"] for i in items])
        for i, s in zip(items, scores):
            i["debate"] = s
        HUNT_CACHE["data"] = items
        HUNT_CACHE["time"] = now
    else:
        seen = hunter.load_seen(BASE)  # 캐시여도 '제작됨' 표시는 최신으로
        for item in HUNT_CACHE["data"]:
            item["used"] = item["url"] in seen
    return jsonify(ok=True, items=HUNT_CACHE["data"])


@app.post("/api/packs")
def api_packs():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    packs = []
    pub = insta.load_published(BASE)
    usage = _usage_load()
    mgrs = _managers(cfg)
    need = int(cfg.get("usage_threshold", 2) or 2)
    used_dir = cfg.get("used_dir") or "_사용완료"
    show_arch = bool(data.get("archived"))
    # 일반 회원은 본인이 만든 팩만, 관리자는 전체
    is_admin = _is_admin(cfg, data.get("code"))
    mycode = (data.get("code") or "").strip()
    owners = _owners_load()
    root = _used_root(cfg) if show_arch else OUTPUT
    if root.exists():
        for d in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
            if not d.is_dir() or not (d / "review.html").exists():
                continue
            if not show_arch and d.name in (used_dir, "_휴지통"):
                continue
            if not is_admin and owners.get(d.name) != mycode:
                continue      # 내 팩이 아니면(소유자 미상 레거시 포함) 일반 회원엔 숨김
            meta = {}
            try:
                meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
            except Exception:
                pass
            thumb = "thumb.jpg" if (d / "thumb.jpg").exists() else "01.jpg"
            checked = [m for m in (usage.get(d.name, {}).get("checked_by") or [])
                       if m in mgrs]
            is_story = meta.get("template") == "story"
            nimg = len(list(d.glob("[0-9][0-9].jpg")))
            exportable = (d / "items.json").exists() or (d / "source.json").exists()
            packs.append({"name": d.name,
                          "title": meta.get("title") or d.name,
                          "created": meta.get("created", ""),
                          "n": nimg, "exportable": exportable,
                          "source": meta.get("source", ""),
                          "template": meta.get("template", ""),
                          "type": "cardnews" if meta.get("type") == "cardnews" else "meme",
                          "site": meta.get("site", "") or
                                  ("카드뉴스" if meta.get("type") == "cardnews" else ""),
                          "thumb": thumb,
                          "story": is_story,
                          "lang": meta.get("lang", "ko"),
                          "used": len(checked), "archived": show_arch,
                          "published": d.name in pub})
            if len(packs) >= 60:
                break
    return jsonify(ok=True, packs=packs, managers=mgrs, need=need,
                   used_dir=used_dir, archived=show_arch)


@app.post("/api/pack/delete")
def api_pack_delete():
    """팩 삭제 — 하드삭제 대신 결과물/_휴지통/ 으로 이동(복구 가능)."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    name = (data.get("pack") or "").strip()
    if not name or "/" in name or "\\" in name or name.startswith("_"):
        return jsonify(ok=False, error="잘못된 팩 이름이에요"), 400
    src = OUTPUT / name
    if not src.is_dir():                       # 보관함(사용완료)에 있는 팩도 삭제 가능
        src = _used_root(cfg) / name
    if not src.is_dir():
        return jsonify(ok=False, error="팩을 찾을 수 없어요"), 404
    trash = OUTPUT / "_휴지통"
    try:
        trash.mkdir(exist_ok=True)
        dst = trash / name
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        shutil.move(str(src), str(dst))
    except Exception as e:
        return jsonify(ok=False, error=f"삭제 실패: {e}"), 500
    return jsonify(ok=True)


@app.post("/api/pack")
def api_pack_detail():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    pack = (data.get("pack") or "").strip()
    arch = bool(data.get("archived"))
    d = (_used_root(cfg) / pack) if arch else (OUTPUT / pack)
    if not pack or "/" in pack or "\\" in pack or not d.is_dir():
        return jsonify(ok=False, error="팩을 찾을 수 없습니다"), 404
    meta = {}
    try:
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    caption = ""
    try:
        caption = (d / "caption.txt").read_text(encoding="utf-8")
    except OSError:
        pass
    zips = list(d.glob("*.zip"))
    payload = {
        "name": d.name,
        "type": "cardnews" if meta.get("type") == "cardnews" else "meme",
        "title": meta.get("title") or d.name,
        "created": meta.get("created", ""),
        "keyword": meta.get("keyword", ""),
        "lang": meta.get("lang", ""),
        "caption": caption,
        "thumbs": sorted(p.name for p in d.glob("thumb*.jpg")),
        "images": sorted(p.name for p in d.glob("[0-9][0-9].jpg")),
        "zip": zips[0].name if zips else "",
        "ebook": (d / "ebook.pdf").exists(),
        "hooks": meta.get("hooks", []),
        "cards_ko": meta.get("cards_ko", []),   # 카드별 본문 한국어 해석(오역 확인용)
        "story": meta.get("template") == "story",
        "srcs": sorted(p.name for p in d.glob("src[0-9][0-9].jpg")),  # 커버 교체용 원본사진
        "thumb_src": meta.get("thumb_src", ""),
        "published": insta.load_published(BASE).get(d.name),
        "arch": arch,
        "used_dir": cfg.get("used_dir") or "_사용완료",
        "managers": _managers(cfg),
        "used": {"checked": [m for m in
                             (_usage_load().get(d.name, {}).get("checked_by") or [])
                             if m in _managers(cfg)],
                 "need": int(cfg.get("usage_threshold", 2) or 2)},
    }
    if (d / "items.json").exists():
        try:
            idata = json.loads((d / "items.json").read_text(encoding="utf-8"))
            plan = idata.get("plan", {})
            if idata.get("items"):
                payload["edit"] = {
                    "plan": {k: plan.get(k, "") for k in
                             ("title_top", "title_main", "subtitle",
                              "comment_keyword", "ebook_title")},
                    "teaser": plan.get("teaser", []),
                    "items": idata.get("items", []),
                    "theme": meta.get("theme", ""),
                }
        except Exception:
            pass
    return jsonify(ok=True, **payload)


@app.post("/api/caption/save")
def api_caption_save():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    pack = (data.get("pack") or "").strip()
    pack_dir = OUTPUT / pack
    if not pack or "/" in pack or "\\" in pack or not pack_dir.is_dir():
        return jsonify(ok=False, error="팩을 찾을 수 없습니다"), 404
    cap = str(data.get("caption") or "").strip()
    if not cap:
        return jsonify(ok=False, error="캡션이 비어 있어요"), 400
    (pack_dir / "caption.txt").write_text(cap, encoding="utf-8")
    return jsonify(ok=True)


# ── 사용완료 체크 → 보관함 이동 시스템 ─────────────────────────
USAGE_F = BASE / "usage.json"
_USAGE_LOCK = threading.Lock()


def _usage_load():
    try:
        return json.loads(USAGE_F.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _usage_save(data):
    USAGE_F.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")


def _managers(cfg):
    return [str(m).strip() for m in (cfg.get("managers") or []) if str(m).strip()]


def _used_root(cfg):
    return OUTPUT / (cfg.get("used_dir") or "_사용완료")


@app.post("/api/pack/use")
def api_pack_use():
    """담당자 사용완료 체크. 정족수(usage_threshold) 채우면 보관함으로 이동 + 이름표."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    pack = (data.get("pack") or "").strip()
    manager = (data.get("manager") or "").strip()
    d = OUTPUT / pack
    if not pack or "/" in pack or "\\" in pack or not d.is_dir():
        return jsonify(ok=False, error="팩을 찾을 수 없습니다"), 404
    mgrs = _managers(cfg)
    if manager not in mgrs:
        return jsonify(ok=False, error="담당자 명단에 없는 이름이에요"), 400
    need = int(cfg.get("usage_threshold", 2) or 2)
    with _USAGE_LOCK:
        allu = _usage_load()
        u = allu.get(pack) or {"checked_by": []}
        checked = [m for m in u.get("checked_by", []) if m in mgrs]
        if data.get("undo"):
            checked = [m for m in checked if m != manager]
        elif manager not in checked:
            checked.append(manager)
        u["checked_by"] = checked
        allu[pack] = u
        _usage_save(allu)  # 체크는 먼저 확정 저장
        if len(checked) < need:
            return jsonify(ok=True, archived=False, checked=checked, need=need)
        # 정족수 도달 → 보관함으로 이동 + 이름표
        root = _used_root(cfg)
        prefix = cfg.get("used_prefix") or "✅사용완료_"
        try:
            root.mkdir(exist_ok=True)
            newname = pack if pack.startswith(prefix) else f"{prefix}{pack}"
            target = root / newname
            k = 1
            while target.exists():
                target = root / f"{newname}_{k}"
                k += 1
            d.rename(target)
        except Exception as e:
            return jsonify(ok=True, archived=False, checked=checked, need=need,
                           warn=f"체크는 저장됐지만 보관함 이동 실패(파일 사용 중일 수 있어요): {e}")
        u["archived"] = True
        u["archived_at"] = time.time()
        u["archived_name"] = target.name
        allu[pack] = u
        _usage_save(allu)
        return jsonify(ok=True, archived=True, archived_name=target.name,
                       checked=checked, need=need)


def _rebuild_pack_zip(d):
    """짤 순서 변경 후 zip 재생성 (썸네일+짤+캡션+PDF)"""
    zips = list(d.glob("*.zip"))
    zp = zips[0] if zips else d / (d.name + ".zip")
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(d.glob("thumb*.jpg")) + sorted(d.glob("[0-9][0-9].jpg")):
            zf.write(f, f.name)
        for extra in ("caption.txt", "ebook.pdf"):
            if (d / extra).exists():
                zf.write(d / extra, extra)


def _refresh_review_images(d, count):
    """review.html의 짤 이미지 태그를 새 순서로 갱신"""
    rv = d / "review.html"
    if not rv.exists():
        return
    html = rv.read_text(encoding="utf-8")
    tags = re.findall(r'<img [^>]*src="\d{2}\.jpg[^"]*"[^>]*>', html)
    if not tags:
        return
    pos = html.find(tags[0])
    head, tail = html[:pos], html[pos:]
    tail = re.sub(r'<img [^>]*src="\d{2}\.jpg[^"]*"[^>]*>\s*', "", tail)
    new = "\n".join(f'<img src="{i + 1:02d}.jpg">' for i in range(count))
    rv.write_text(head + new + tail, encoding="utf-8")


@app.post("/api/pack/arrange")
def api_pack_arrange():
    """짤 순서 변경/제외 — order에 남길 파일명을 새 순서대로. 제외분은 '제외' 폴더 보관"""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    pack = (data.get("pack") or "").strip()
    d = OUTPUT / pack
    if not pack or "/" in pack or "\\" in pack or not d.is_dir():
        return jsonify(ok=False, error="팩을 찾을 수 없습니다"), 404
    order = [str(n) for n in (data.get("order") or [])]
    valid = {p.name for p in d.glob("[0-9][0-9].jpg")}
    if not order:
        return jsonify(ok=False, error="남길 짤이 최소 1장은 있어야 해요"), 400
    if len(set(order)) != len(order) or any(n not in valid for n in order):
        return jsonify(ok=False, error="짤 목록이 실제 파일과 달라요 — 새로고침 후 다시 시도"), 400
    for i, n in enumerate(order):
        (d / n).rename(d / f"tmp_arr_{i:02d}.jpg")
    exdir = d / "제외"
    for n in sorted(valid - set(order)):
        exdir.mkdir(exist_ok=True)
        target = exdir / n
        k = 1
        while target.exists():
            target = exdir / f"{n[:-4]}_{k}.jpg"
            k += 1
        (d / n).rename(target)
    for i in range(len(order)):
        (d / f"tmp_arr_{i:02d}.jpg").rename(d / f"{i + 1:02d}.jpg")
    _rebuild_pack_zip(d)
    _refresh_review_images(d, len(order))
    return jsonify(ok=True, images=[f"{i + 1:02d}.jpg" for i in range(len(order))])


def _rerender_base_with_blur(d, base, cfg, new_boxes):
    """유튜브/릴스 팩(source.json+frame)이면 프레임 배경에만 블러하고 텍스트를 재렌더한다.
    → 완성 텍스트(후킹/워터마크)는 선명 유지. 프레임 기반 아니면 None(기존 평면 블러로)."""
    src_f = d / "source.json"
    if not src_f.exists():
        return None
    try:
        src = json.loads(src_f.read_text(encoding="utf-8"))
    except Exception:
        return None
    if src.get("source") not in ("youtube", "reel"):
        return None
    try:
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        meta = {}
    lang = meta.get("lang", "ko")
    wm = cfg.get("watermark", "")
    # 누적 블러 영역(_blur.json) — 여러 번 그려도 합쳐서 재렌더
    blur_file = d / "_blur.json"
    try:
        acc = json.loads(blur_file.read_text(encoding="utf-8"))
        if not isinstance(acc, dict):
            acc = {}
    except Exception:
        acc = {}
    regions = list(acc.get(base) or [])
    for b in new_boxes[:40]:
        try:
            regions.append({"x": float(b.get("x", 0)), "y": float(b.get("y", 0)),
                            "w": float(b.get("w", 0)), "h": float(b.get("h", 0))})
        except (TypeError, ValueError):
            continue
    orig_dir = d / "_orig"
    orig_dir.mkdir(exist_ok=True)
    if not (orig_dir / base).exists() and (d / base).exists():
        shutil.copy(str(d / base), str(orig_dir / base))   # 최초 원본 백업
    m = re.fullmatch(r"(\d{2})\.jpg", base)
    if m:                                     # 뒷장 카드
        n = int(m.group(1))
        frame = d / f"frame{n:02d}.jpg"
        cards = src.get("cards") or []
        if not frame.exists() or n < 1 or n > len(cards):
            return None
        youtube.render_back_card(str(frame), cards[n - 1].get("text", ""), wm,
                                 str(d / base), cfg, lang, blur_regions=regions)
    else:                                     # 대표/후보 썸네일
        tm = re.fullmatch(r"thumb(\d*)\.jpg", base)
        if not tm:
            return None
        idx = 0 if tm.group(1) == "" else (int(tm.group(1)) - 1)
        hooks = src.get("hooks") or []
        if idx < 0 or idx >= len(hooks):
            return None
        bg = d / "ytthumb.jpg"
        if not bg.exists():
            bg = d / "frame01.jpg"
        hk = hooks[idx]
        youtube.render_youtube_thumb(str(bg) if bg.exists() else None,
                                     hk.get("line1", ""), hk.get("line2", ""), wm,
                                     str(d / base), lang, blur_regions=regions)
    acc[base] = regions
    blur_file.write_text(json.dumps(acc, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(new_boxes)


@app.post("/api/pack/mosaic")
def api_pack_mosaic():
    """짤/썸네일에 블러 박스 적용 — boxes=[{x,y,w,h}] 정규화(0~1) 영역을 가우시안 블러.
    유튜브/릴스 짤은 프레임에만 블러하고 텍스트를 재렌더(글자 선명 유지)."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    pack = (data.get("pack") or "").strip()
    d = OUTPUT / pack
    if not pack or "/" in pack or "\\" in pack or not d.is_dir():
        return jsonify(ok=False, error="팩을 찾을 수 없습니다"), 404
    base = (data.get("base") or "").strip()
    # 본문 짤(NN.jpg) + 대표/후보 썸네일(thumb.jpg, thumb2.jpg …) 모두 허용
    if not re.fullmatch(r"(?:\d{2}|thumb\d*)\.jpg", base) or not (d / base).exists():
        return jsonify(ok=False, error="이미지 파일을 찾을 수 없어요"), 404
    boxes = data.get("boxes") or []
    if not isinstance(boxes, list) or not boxes:
        return jsonify(ok=False, error="가릴 영역을 최소 1개 그려주세요"), 400
    try:   # 유튜브/릴스 짤: 프레임에만 블러 + 텍스트 재렌더(글자 선명)
        re_applied = _rerender_base_with_blur(d, base, cfg, boxes)
        if re_applied is not None:
            _rebuild_pack_zip(d)
            return jsonify(ok=True, image=base, applied=re_applied, rerendered=True)
    except Exception as e:
        return jsonify(ok=False, error=f"재렌더 블러 실패: {str(e)[:100]}"), 500
    try:   # 그 외(카드뉴스/커뮤 짤): 완성 이미지에 평면 블러
        from PIL import Image as _Img
        from PIL import ImageFilter as _F
        img = _Img.open(d / base).convert("RGB")
        W0, H0 = img.size
        applied = 0
        for b in boxes[:40]:
            try:
                x = max(0.0, min(1.0, float(b.get("x", 0))))
                y = max(0.0, min(1.0, float(b.get("y", 0))))
                bw = max(0.0, min(1.0, float(b.get("w", 0))))
                bh = max(0.0, min(1.0, float(b.get("h", 0))))
            except (TypeError, ValueError):
                continue
            px, py = int(x * W0), int(y * H0)
            px2, py2 = min(W0, int((x + bw) * W0)), min(H0, int((y + bh) * H0))
            if px2 - px < 4 or py2 - py < 4:   # 너무 작은 박스 무시
                continue
            region = img.crop((px, py, px2, py2))
            radius = max(8, min(region.width, region.height) // 5)  # 영역 크기에 비례한 블러
            region = region.filter(_F.GaussianBlur(radius))
            img.paste(region, (px, py))
            applied += 1
        if not applied:
            return jsonify(ok=False, error="유효한 영역이 없어요 — 조금 더 크게 드래그해주세요"), 400
        orig_dir = d / "_orig"          # 최초 상태 백업(첫 편집 때만) → 원본 복구용
        orig_dir.mkdir(exist_ok=True)
        if not (orig_dir / base).exists():
            shutil.copy(str(d / base), str(orig_dir / base))
        img.save(d / base, "JPEG", quality=92)
        _rebuild_pack_zip(d)
        return jsonify(ok=True, image=base, applied=applied)
    except Exception as e:
        return jsonify(ok=False, error=f"블러 실패: {str(e)[:80]}"), 500


@app.post("/api/pack/restore")
def api_pack_restore():
    """편집(블러) 전 최초 생성 상태로 복구. base 지정=그 이미지만, 없으면 전체."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    pack = (data.get("pack") or "").strip()
    d = OUTPUT / pack
    if not pack or "/" in pack or "\\" in pack or not d.is_dir():
        return jsonify(ok=False, error="팩을 찾을 수 없습니다"), 404
    orig_dir = d / "_orig"
    if not orig_dir.is_dir():
        return jsonify(ok=False, error="복구할 원본이 없어요 (아직 편집 안 한 팩)"), 404
    base = (data.get("base") or "").strip()
    blur_file = d / "_blur.json"
    try:
        acc = json.loads(blur_file.read_text(encoding="utf-8")) if blur_file.exists() else {}
        if not isinstance(acc, dict):
            acc = {}
    except Exception:
        acc = {}
    restored = 0
    if base:
        if not re.fullmatch(r"(?:\d{2}|thumb\d*)\.jpg", base) or not (orig_dir / base).exists():
            return jsonify(ok=False, error="이 이미지의 원본이 없어요"), 404
        shutil.copy(str(orig_dir / base), str(d / base))
        acc.pop(base, None)     # 누적 블러 기록도 초기화
        restored = 1
    else:
        for p in orig_dir.glob("*.jpg"):
            shutil.copy(str(p), str(d / p.name))
            restored += 1
        acc = {}
    try:
        blur_file.write_text(json.dumps(acc, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    _rebuild_pack_zip(d)
    return jsonify(ok=True, restored=restored)


@app.post("/api/pack/rethumb")
def api_pack_rethumb():
    """고른 짤을 배경으로 썸네일(후킹 3종) 재렌더 — 후킹 문구는 그대로 유지."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    pack = (data.get("pack") or "").strip()
    d = OUTPUT / pack
    if not pack or "/" in pack or "\\" in pack or not d.is_dir():
        return jsonify(ok=False, error="팩을 찾을 수 없습니다"), 404
    base = (data.get("base") or "").strip()
    meta = {}
    try:
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    hooks = [h for h in (meta.get("hooks") or [])
             if (h.get("line1") or h.get("line2"))][:3]
    if not hooks:
        hooks = [{"line1": (meta.get("title") or "")[:13], "line2": ""}]

    # ── 스토리팩: 커버 사진 '스왑' — 고른 srcNN을 커버로 올리고, 옛 커버 사진은
    #    그 사진이 있던 본문 자리로 내린다(사진만 교환, 각 자리의 텍스트는 그대로). ──
    if meta.get("template") == "story":
        if not re.fullmatch(r"src[0-9]{2}\.jpg", base) or not (d / base).exists():
            return jsonify(ok=False, error="커버로 쓸 원본 사진을 찾을 수 없어요"), 400
        cards = meta.get("cards") or []
        lang = meta.get("lang", "ja")
        srcs = sorted(p.name for p in d.glob("src[0-9][0-9].jpg"))
        # 위치별 사진 매핑(위치0=커버, 1~=본문 01.jpg~). 없거나 어긋나면 기본 매핑
        slots = meta.get("photo_slots")
        if not slots or sorted(slots) != srcs:
            slots = list(srcs)
            ts = meta.get("thumb_src")   # 레거시 팩: 옛 방식으로 커버가 바뀐 상태 보정
            if ts in slots and slots and ts != slots[0]:
                slots.remove(ts)
                slots.insert(0, ts)
        cur_cover = slots[0] if slots else "src00.jpg"
        swapped = False
        if base != cur_cover and base in slots:   # 고른 사진이 커버가 아니면 교환
            posY = slots.index(base)
            slots[0], slots[posY] = slots[posY], slots[0]
            body_file = d / f"{posY:02d}.jpg"      # 그 자리 본문카드에 옛 커버 사진을
            if posY >= 1 and body_file.exists():
                paras = (cards[posY].get("paragraphs")
                         if posY < len(cards) else None) or []
                storycard.render_card(str(d / cur_cover), "", paras, cfg,
                                      str(body_file), lang=lang)
            swapped = True
        # 커버(썸네일 3종) 재렌더 — 사진 = slots[0](=고른 사진), 헤드라인 + cards[0] 본문
        paras0 = (cards[0].get("paragraphs") if cards else None) or []
        thumbs = []
        for i, h in enumerate(hooks):
            name = "thumb.jpg" if i == 0 else f"thumb{i + 1}.jpg"
            storycard.render_card(str(d / slots[0]), h.get("line1", ""), paras0,
                                  cfg, str(d / name), lang=lang)
            thumbs.append(name)
        for old in d.glob("thumb*.jpg"):
            if old.name not in thumbs:
                try:
                    old.unlink()
                except OSError:
                    pass
        meta["thumb_src"] = slots[0]
        meta["photo_slots"] = slots
        (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
        _rebuild_pack_zip(d)
        return jsonify(ok=True, thumbs=thumbs, base=slots[0], swapped=swapped)

    # ── 클래식/짤팩: 고른 짤을 배경으로 후킹 썸네일 재렌더 ──
    if not re.fullmatch(r"[0-9]{2}\.jpg", base) or not (d / base).exists():
        return jsonify(ok=False, error="썸네일로 쓸 짤을 찾을 수 없어요"), 400
    # 헤더(가짜 통계): 저장돼 있으면 그대로 재사용, 없으면 재생성
    header = meta.get("header") or pipeline._make_header(
        cfg, meta.get("title") or hooks[0].get("line1") or "", None)
    thumbs = []
    for i, h in enumerate(hooks):
        name = "thumb.jpg" if i == 0 else f"thumb{i + 1}.jpg"
        thumbnail.render(str(d / base), h.get("line1", ""), h.get("line2", ""),
                         cfg.get("watermark", ""), str(d / name), header=header)
        thumbs.append(name)
    for old in d.glob("thumb*.jpg"):  # 후킹 수 줄었을 때 남는 옛 썸네일 정리
        if old.name not in thumbs:
            try:
                old.unlink()
            except OSError:
                pass
    if header:
        meta["header"] = header  # 다음 재렌더 때 동일 통계 유지되도록 저장
    meta["thumb_base"] = base
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    _rebuild_pack_zip(d)
    return jsonify(ok=True, thumbs=thumbs, base=base)


@app.post("/api/pack/reheadline")
def api_pack_reheadline():
    """스토리팩 표지 헤드라인 3개 리롤(재생성). hint(운영자 방향/예시)로 감성 지정 가능."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    pack = (data.get("pack") or "").strip()
    d = OUTPUT / pack
    if not pack or "/" in pack or "\\" in pack or not d.is_dir():
        return jsonify(ok=False, error="팩을 찾을 수 없습니다"), 404
    try:
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        return jsonify(ok=False, error="메타를 읽을 수 없어요"), 400
    if meta.get("template") != "story":
        return jsonify(ok=False, error="스토리팩만 헤드라인 리롤이 돼요"), 400
    srcs = sorted(p.name for p in d.glob("src[0-9][0-9].jpg"))
    if not srcs:
        return jsonify(ok=False, error="예전 팩이라 원본 사진이 없어 헤드라인 교체가 안 돼요"), 400
    hint = (data.get("hint") or "").strip()[:300]
    cards = meta.get("cards") or []
    lang = meta.get("lang", "ja")
    res = brain.reroll_headlines(cfg, cards, lang=lang, hint=hint)
    if not res or not res.get("headlines"):
        return jsonify(ok=False, error="헤드라인 생성 실패 — 다시 시도해주세요"), 500
    heads, kos = res["headlines"], res.get("headlines_ko", [])
    slots = meta.get("photo_slots")
    cover_src = (slots[0] if slots else None) or meta.get("thumb_src") or srcs[0]
    if not (d / cover_src).exists():
        cover_src = srcs[0]
    paras0 = (cards[0].get("paragraphs") if cards else None) or []
    thumbs = []
    for i, h in enumerate(heads[:3]):
        name = "thumb.jpg" if i == 0 else f"thumb{i + 1}.jpg"
        storycard.render_card(str(d / cover_src), h, paras0, cfg, str(d / name), lang=lang)
        thumbs.append(name)
    for old in d.glob("thumb*.jpg"):
        if old.name not in thumbs:
            try:
                old.unlink()
            except OSError:
                pass
    meta["hooks"] = [{"line1": h, "line2": "", "ko": (kos[i] if i < len(kos) else "")}
                     for i, h in enumerate(heads[:3])]
    meta["title"] = heads[0]
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    _rebuild_pack_zip(d)
    return jsonify(ok=True, hooks=meta["hooks"], thumbs=thumbs)


def _run_card_edit_job(jid, pack_dir, plan_patch, items, caption, theme, cfg):
    job = JOBS[jid]

    def log(m):
        m = str(m).strip()
        job["msg"] = m
        step = re.match(r"\[(\d)/4\]", m)
        if step:
            job["pct"] = {3: 35, 4: 75}.get(int(step.group(1)), job["pct"])

    try:
        result = card_pipeline.rerender_pack(cfg, pack_dir, plan_patch=plan_patch,
                                             items=items, caption=caption,
                                             theme=theme, log=log)
        rel = result["pack"].name
        job["result"] = {"pack": rel,
                         "cards": [f"/packs/{rel}/{c}" for c in result["cards"]],
                         "caption": result["caption"]}
        job["pct"] = 100
        job["status"] = "done"
    except Exception as e:
        job["error"] = str(e)
        job["status"] = "error"



@app.post("/api/card/edit")
def api_card_edit():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    pack = (data.get("pack") or "").strip()
    pack_dir = OUTPUT / pack
    if (not pack or "/" in pack or "\\" in pack
            or not (pack_dir / "items.json").exists()):
        return jsonify(ok=False, error="수정 가능한 카드뉴스 팩이 아니에요"), 404
    theme = (data.get("theme") or "").strip()
    if theme not in ("hunter", "cream"):
        theme = None
    now = time.time()
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = {"status": "queued", "pct": 0, "msg": "대기 중...",
                 "result": None, "error": None, "ts": now}
    JOBQ.put((jid, _run_card_edit_job,
              (jid, pack_dir, data.get("plan"), data.get("items"),
               data.get("caption"), theme, cfg)))
    return jsonify(ok=True, job=jid)


def _run_insta_job(jid, pack_dir, lead, force, cfg, account=None):
    job = JOBS[jid]

    def log(m):
        m = str(m).strip()
        job["msg"] = m
        step = re.match(r"\[(\d)/3\]", m)
        if step:
            job["pct"] = {1: 25, 2: 60, 3: 85}.get(int(step.group(1)), job["pct"])

    try:
        result = insta.publish_pack(cfg, BASE, pack_dir, lead=lead, force=force,
                                    account=account, log=log)
        job["result"] = {"insta": True, "permalink": result.get("permalink", "")}
        job["pct"] = 100
        job["status"] = "done"
    except Exception as e:
        job["error"] = str(e)
        job["status"] = "error"


@app.post("/api/insta/publish")
def api_insta_publish():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    if not _is_admin(cfg, data.get("code")):
        return jsonify(ok=False, error="즉시 업로드는 관리자만 가능합니다. 예약을 이용하세요."), 403
    pack = (data.get("pack") or "").strip()
    pack_dir = OUTPUT / pack
    if not pack or "/" in pack or "\\" in pack or not pack_dir.is_dir():
        return jsonify(ok=False, error="팩을 찾을 수 없습니다"), 404
    lead = (data.get("lead") or "").strip() or None
    if lead and not re.fullmatch(r"thumb\d*\.jpg", lead):
        lead = None
    now = time.time()
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = {"status": "running", "pct": 10, "msg": "인스타 업로드 준비...",
                 "result": None, "error": None, "ts": now}
    threading.Thread(target=_run_insta_job,
                     args=(jid, pack_dir, lead,
                           bool(data.get("force")), cfg,
                           (data.get("account") or "").strip() or None), daemon=True).start()
    return jsonify(ok=True, job=jid)


@app.post("/api/pack/drive")
def api_pack_drive():
    """전자책 PDF → 구글 드라이브 업로드 → '링크 있는 사람 보기' 공유 링크."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    pack = (data.get("pack") or "").strip()
    d = OUTPUT / pack
    if not pack or "/" in pack or "\\" in pack or not d.is_dir():
        return jsonify(ok=False, error="팩을 찾을 수 없습니다"), 404
    pdf = d / "ebook.pdf"
    if not pdf.exists():
        return jsonify(ok=False, error="이 팩엔 전자책 PDF가 없어요"), 400
    try:
        import drive
        title = pack
        try:
            title = json.loads((d / "meta.json").read_text(encoding="utf-8")).get("title") or pack
        except Exception:
            pass
        r = drive.upload_pdf(cfg, BASE, str(pdf), name=f"{title}.pdf")
        return jsonify(ok=True, link=r["link"])
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@app.get("/packs/<path:subpath>")
def packs(subpath):
    return send_from_directory(OUTPUT, subpath)


# ──────────────────── 예약 업로드 스케줄러 ────────────────────
SCHED_FILE = BASE / "schedule.json"
_sched_lock = threading.Lock()


def _sched_load():
    try:
        return json.loads(SCHED_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _sched_save(items):
    with _sched_lock:
        SCHED_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2),
                              encoding="utf-8")


def _scheduler_loop():
    """예약 큐 주기 확인 → 시간 되면 자동 게시. 서버 꺼져 놓친 건 시작 시 '놓침' 처리."""
    grace = 600   # 10분 이상 지난 pending = (서버 꺼졌던 것) → 놓침
    try:
        items = _sched_load()
        now = time.time()
        ch = False
        for e in items:
            if e.get("status") == "pending" and e.get("ts", 0) < now - grace:
                e["status"] = "missed"
                ch = True
        if ch:
            _sched_save(items)
    except Exception:
        pass
    while True:
        try:
            items = _sched_load()
            now = time.time()
            due = [e for e in items
                   if e.get("status") == "pending" and e.get("ts", 0) <= now]
            if due:
                cfg = load_config()
                for e in due:
                    try:
                        if e.get("type") == "reel":          # 릴스 예약: 영상 URL로 발행
                            vn = e.get("video") or ""
                            vpath = OUTPUT / "_videos" / vn
                            if not vn or not vpath.exists():
                                raise RuntimeError("예약 영상 없음(만료/삭제됨?)")
                            public = (cfg.get("public_base_url")
                                      or "https://jjal.traffic-charger.com").rstrip("/")
                            video_url = f"{public}/packs/_videos/{vn}"
                            r = insta.publish_reel(cfg, BASE, video_url,
                                                   e.get("caption") or "",
                                                   account=e.get("account") or None)
                            try:
                                vpath.unlink()               # 게시 후 영상 정리
                            except OSError:
                                pass
                        else:                                # 게시물(캐러셀) 예약
                            pack_dir = OUTPUT / e["pack"]
                            if not pack_dir.is_dir():
                                raise RuntimeError("팩 없음(삭제됨?)")
                            r = insta.publish_pack(cfg, BASE, pack_dir,
                                                   lead=e.get("lead") or None,
                                                   account=e.get("account") or None)
                        e["status"] = "done"
                        e["permalink"] = r.get("permalink", "")
                    except Exception as ex:
                        e["status"] = "failed"
                        e["error"] = str(ex)[:200]
                    e["posted_at"] = datetime.now().isoformat(timespec="seconds")
                _sched_save(items)
        except Exception:
            pass
        time.sleep(45)


@app.post("/api/schedule/add")
def api_schedule_add():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    pack = (data.get("pack") or "").strip()
    if not pack or "/" in pack or "\\" in pack or not (OUTPUT / pack).is_dir():
        return jsonify(ok=False, error="팩을 찾을 수 없습니다"), 404
    try:
        ts = float(data.get("ts") or 0)
    except (TypeError, ValueError):
        ts = 0
    if ts <= 0:
        return jsonify(ok=False, error="예약 시간이 올바르지 않습니다"), 400
    if ts <= time.time() + 30:
        return jsonify(ok=False, error="예약 시간은 현재보다 미래여야 합니다"), 400
    who = _member(cfg, data.get("code")) or {}
    admin = who.get("role") == "admin"
    entry = {"id": uuid.uuid4().hex[:10], "pack": pack,
             "account": (data.get("account") or "").strip(),
             "lead": (data.get("lead") or "").strip(),
             "ts": ts, "when": (data.get("when") or "").strip(),
             "title": (data.get("title") or pack).strip(),
             # 관리자 예약은 바로 확정(pending), 일반 회원은 승인 대기(await)
             "status": "pending" if admin else "await",
             "by_code": (data.get("code") or "").strip(),
             "by_name": who.get("name", ""),
             "by_role": who.get("role", "user"),
             "created": datetime.now().isoformat(timespec="seconds")}
    items = _sched_load()
    items.append(entry)
    _sched_save(items)
    return jsonify(ok=True, id=entry["id"], status=entry["status"])


@app.post("/api/schedule/list")
def api_schedule_list():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    items = _sched_load()
    # 일반 회원은 본인이 만든 예약만, 관리자는 전체
    if not _is_admin(cfg, data.get("code")):
        mycode = (data.get("code") or "").strip()
        items = [e for e in items if e.get("by_code") == mycode]
    for e in items:
        if e.get("type") == "reel":                 # 릴스 예약: 영상 미리보기
            vn = e.get("video") or ""
            e["video"] = (f"/packs/_videos/{vn}"
                          if vn and (OUTPUT / "_videos" / vn).exists() else "")
            e["images"] = []
            e["thumb"] = ""
            continue
        pd = OUTPUT / e.get("pack", "")
        e["thumb"] = f"/packs/{e['pack']}/thumb.jpg" if (pd / "thumb.jpg").exists() else ""
        try:      # 게시될 캡션(예약 상세에서 확인용) — 게시물은 caption.txt
            cap_f = pd / "caption.txt"
            e["caption"] = cap_f.read_text(encoding="utf-8") if cap_f.exists() else ""
        except Exception:
            e["caption"] = ""
        # 미리보기용 이미지/영상 목록 — 실제 게시 순서와 동일(썸네일/lead가 첫 장, 그다음 본문)
        if pd.is_dir():
            numbered = [p.name for p in sorted(pd.glob("[0-9][0-9].jpg"))]
            order = []
            if (pd / "thumb.jpg").exists():        # 짤/유튜브 팩: 썸네일이 첫 장
                lead = e.get("lead") or ""
                order.append(lead if lead and (pd / lead).exists() else "thumb.jpg")
            order += numbered
            e["images"] = [f"/packs/{e['pack']}/{n}" for n in order]
            vids = sorted(pd.glob("*.mp4"))
            e["video"] = f"/packs/{e['pack']}/{vids[0].name}" if vids else ""
        else:
            e["images"] = []
            e["video"] = ""
    items.sort(key=lambda x: x.get("ts", 0))
    return jsonify(ok=True, items=items, admin=_is_admin(cfg, data.get("code")))


@app.post("/api/schedule/approve")
def api_schedule_approve():
    """승인 대기(await) 예약을 확정(pending)으로. 관리자 전용."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _is_admin(cfg, data.get("code")):
        return jsonify(ok=False, error="관리자만 승인할 수 있습니다"), 403
    sid = (data.get("id") or "").strip()
    items = _sched_load()
    hit = False
    for e in items:
        if e.get("id") == sid and e.get("status") == "await":
            e["status"] = "pending"
            e["approved_at"] = datetime.now().isoformat(timespec="seconds")
            hit = True
    _sched_save(items)
    return jsonify(ok=True, changed=hit)


@app.post("/api/schedule/reject")
def api_schedule_reject():
    """승인 대기 예약을 반려. 관리자 전용. (기록 남기려 status=rejected)"""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _is_admin(cfg, data.get("code")):
        return jsonify(ok=False, error="관리자만 반려할 수 있습니다"), 403
    sid = (data.get("id") or "").strip()
    items = _sched_load()
    for e in items:
        if e.get("id") == sid and e.get("status") == "await":
            e["status"] = "rejected"
            e["reject_reason"] = (data.get("reason") or "").strip()
            e["rejected_at"] = datetime.now().isoformat(timespec="seconds")
            _cleanup_reel_video(e)        # 반려된 릴스 영상 정리
    _sched_save(items)
    return jsonify(ok=True)


@app.post("/api/schedule/reschedule")
def api_schedule_reschedule():
    """예약 시간 변경. 본인 예약이거나 관리자만. 과거 시간 불가."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    sid = (data.get("id") or "").strip()
    mycode = (data.get("code") or "").strip()
    try:
        ts = float(data.get("ts") or 0)
    except (TypeError, ValueError):
        ts = 0
    if ts <= time.time() + 30:
        return jsonify(ok=False, error="예약 시간은 현재보다 미래여야 합니다"), 400
    items = _sched_load()
    hit = False
    for e in items:
        if e.get("id") == sid:
            if not _is_admin(cfg, mycode) and e.get("by_code") != mycode:
                return jsonify(ok=False, error="본인 예약만 변경할 수 있습니다"), 403
            if e.get("status") not in ("pending", "await"):
                return jsonify(ok=False, error="이미 처리된 예약은 변경할 수 없습니다"), 400
            e["ts"] = ts
            e["when"] = (data.get("when") or "").strip()
            hit = True
    _sched_save(items)
    return jsonify(ok=hit, error=None if hit else "예약을 찾을 수 없습니다")


def _cleanup_reel_video(entry):
    """릴스 예약 취소/반려 시 보관하던 영상 파일 정리(고아 영상 방지)."""
    if entry and entry.get("type") == "reel":
        vn = entry.get("video") or ""
        if vn.startswith("sched_"):
            try:
                (OUTPUT / "_videos" / vn).unlink()
            except OSError:
                pass


@app.post("/api/schedule/cancel")
def api_schedule_cancel():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    sid = (data.get("id") or "").strip()
    items = _sched_load()
    for e in items:                       # 취소되는 릴스 예약 영상 정리
        if e.get("id") == sid:
            _cleanup_reel_video(e)
    _sched_save([e for e in items if e.get("id") != sid])
    return jsonify(ok=True)


# ── 계정 시스템: 로그인 확인 · 회원 관리(관리자 전용) ──────────────
@app.post("/api/auth/me")
def api_auth_me():
    """접속코드 확인 → 이름·권한 반환. 프런트가 관리자/일반 UI를 가른다."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    m = _member(cfg, data.get("code"))
    if not m:
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    return jsonify(ok=True, name=m.get("name", ""), role=m.get("role", "user"),
                   admin=(m.get("role") == "admin"))


@app.post("/api/members/list")
def api_members_list():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _is_admin(cfg, data.get("code")):
        return jsonify(ok=False, error="관리자만 접근할 수 있습니다"), 403
    m = _members_load(cfg)
    admin_code = str(cfg.get("access_code", "") or "").strip()
    items = [{"code": c, "name": v.get("name", ""), "role": v.get("role", "user"),
              "locked": (c == admin_code)}
             for c, v in sorted(m.items())]
    return jsonify(ok=True, items=items)


@app.post("/api/members/add")
def api_members_add():
    """회원 추가/수정 (관리자 전용). 4자리 숫자 코드."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _is_admin(cfg, data.get("code")):
        return jsonify(ok=False, error="관리자만 접근할 수 있습니다"), 403
    ncode = (data.get("ncode") or "").strip()
    if not re.fullmatch(r"\d{4}", ncode):
        return jsonify(ok=False, error="회원코드는 숫자 4자리여야 합니다"), 400
    role = "admin" if data.get("role") == "admin" else "user"
    name = (data.get("name") or "").strip() or "회원"
    m = _members_load(cfg)
    m[ncode] = {"name": name, "role": role}
    _members_save(m)
    return jsonify(ok=True)


@app.post("/api/members/remove")
def api_members_remove():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _is_admin(cfg, data.get("code")):
        return jsonify(ok=False, error="관리자만 접근할 수 있습니다"), 403
    ncode = (data.get("ncode") or "").strip()
    if ncode == str(cfg.get("access_code", "") or "").strip():
        return jsonify(ok=False, error="기본 관리자 코드는 삭제할 수 없습니다"), 400
    m = _members_load(cfg)
    if ncode in m:
        m.pop(ncode)
        _members_save(m)
    return jsonify(ok=True)


# ── 업로드 인스타 계정 목록(드롭다운용) + 관리(관리자) ─────────────
@app.post("/api/accounts")
def api_accounts():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    return jsonify(ok=True, accounts=_ig_accounts_list(cfg))


def _ig_extra_load():
    try:
        d = json.loads(IG_ACCOUNTS_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _ig_extra_save(d):
    with _admin_lock:
        IG_ACCOUNTS_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2),
                                    encoding="utf-8")


@app.post("/api/admin/ig/list")
def api_admin_ig_list():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _is_admin(cfg, data.get("code")):
        return jsonify(ok=False, error="관리자만 접근할 수 있습니다"), 403
    extra = _ig_extra_load()
    base = {k for k, v in (json.loads((BASE / "config.json").read_text(encoding="utf-8"))
            .get("ig_accounts") or {}).items()
            if isinstance(v, dict) and (str(v.get("user_id", "")).strip()
                                        or str(v.get("access_token", "")).strip())}
    items = []
    for name, v in extra.items():
        items.append({"name": name, "user_id": str(v.get("user_id", "")),
                      "has_token": bool(str(v.get("access_token", "")).strip()),
                      "removable": True})
    for name in sorted(base):
        if name not in extra:
            items.append({"name": name, "user_id": "", "has_token": True,
                          "removable": False})   # config.json 직접 설정분
    return jsonify(ok=True, items=items)


@app.post("/api/admin/ig/add")
def api_admin_ig_add():
    """업로드용 인스타 계정 추가/수정 (관리자). ig_accounts.json 에 저장."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _is_admin(cfg, data.get("code")):
        return jsonify(ok=False, error="관리자만 접근할 수 있습니다"), 403
    name = (data.get("name") or "").strip().lstrip("@")
    if not name:
        return jsonify(ok=False, error="계정 이름(핸들)을 입력하세요"), 400
    extra = _ig_extra_load()
    extra[name] = {"user_id": (data.get("user_id") or "").strip(),
                   "access_token": (data.get("access_token") or "").strip()}
    _ig_extra_save(extra)
    return jsonify(ok=True)


@app.post("/api/admin/ig/remove")
def api_admin_ig_remove():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _is_admin(cfg, data.get("code")):
        return jsonify(ok=False, error="관리자만 접근할 수 있습니다"), 403
    name = (data.get("name") or "").strip().lstrip("@")
    extra = _ig_extra_load()
    if name in extra:
        extra.pop(name)
        _ig_extra_save(extra)
    return jsonify(ok=True)


# ── 회원별 유튜브 API 키 (여러 개 등록 + 사용량 집계) ──────────────
def _ytkeys_load():
    try:
        d = json.loads(YTKEYS_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _ytkeys_save(d):
    with _admin_lock:
        YTKEYS_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2),
                               encoding="utf-8")


def _today_str():
    return datetime.now().strftime("%Y-%m-%d")


def _resolve_yt_key(cfg, code):
    """이 회원의 유튜브 키 중 오늘 할당량(1만) 남은 것 하나. 없으면 config 전역 키."""
    day = _today_str()
    keys = _ytkeys_load().get((code or "").strip(), [])
    for rec in keys:
        if rec.get("day") != day:
            rec["units_today"] = 0
            rec["day"] = day
        if rec.get("units_today", 0) < 10000 and rec.get("key"):
            return rec["key"]
    return youtube._yt_key(cfg)   # 전역 폴백


def _yt_usage_add(code, key, units):
    """추정 사용량 누적(우리 앱이 호출한 유닛). 구글 공식 집계 아님."""
    code = (code or "").strip()
    if not code or not key:
        return
    day = _today_str()
    with _admin_lock:
        d = _ytkeys_load()
        for rec in d.get(code, []):
            if rec.get("key") == key:
                if rec.get("day") != day:
                    rec["units_today"] = 0
                    rec["day"] = day
                rec["units_today"] = rec.get("units_today", 0) + units
                rec["units_total"] = rec.get("units_total", 0) + units
                rec["last"] = datetime.now().isoformat(timespec="seconds")
                _ytkeys_save(d)
                return


@app.post("/api/admin/ytkeys/list")
def api_admin_ytkeys_list():
    """회원별 유튜브 키 + 사용량. 관리자 전용."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _is_admin(cfg, data.get("code")):
        return jsonify(ok=False, error="관리자만 접근할 수 있습니다"), 403
    day = _today_str()
    d = _ytkeys_load()
    members = _members_load(cfg)
    out = []
    for mcode, minfo in sorted(members.items()):
        recs = []
        for rec in d.get(mcode, []):
            ut = rec.get("units_today", 0) if rec.get("day") == day else 0
            recs.append({"label": rec.get("label", ""),
                         "key_tail": (rec.get("key", "")[-6:] if rec.get("key") else ""),
                         "units_today": ut, "units_total": rec.get("units_total", 0),
                         "last": rec.get("last", "")})
        out.append({"code": mcode, "name": minfo.get("name", ""),
                    "role": minfo.get("role", "user"), "keys": recs})
    return jsonify(ok=True, members=out, daily_quota=10000)


@app.post("/api/admin/ytkeys/add")
def api_admin_ytkeys_add():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _is_admin(cfg, data.get("code")):
        return jsonify(ok=False, error="관리자만 접근할 수 있습니다"), 403
    mcode = (data.get("member") or "").strip()
    key = (data.get("key") or "").strip()
    if not mcode or not key:
        return jsonify(ok=False, error="회원과 API 키를 입력하세요"), 400
    d = _ytkeys_load()
    lst = d.setdefault(mcode, [])
    if any(r.get("key") == key for r in lst):
        return jsonify(ok=False, error="이미 등록된 키예요"), 400
    lst.append({"key": key, "label": (data.get("label") or "").strip() or "키",
                "units_today": 0, "units_total": 0, "day": _today_str(), "last": ""})
    _ytkeys_save(d)
    return jsonify(ok=True)


@app.post("/api/admin/ytkeys/remove")
def api_admin_ytkeys_remove():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _is_admin(cfg, data.get("code")):
        return jsonify(ok=False, error="관리자만 접근할 수 있습니다"), 403
    mcode = (data.get("member") or "").strip()
    tail = (data.get("key_tail") or "").strip()
    d = _ytkeys_load()
    if mcode in d:
        d[mcode] = [r for r in d[mcode] if (r.get("key", "")[-6:] != tail)]
        _ytkeys_save(d)
    return jsonify(ok=True)


# ── 인스타 수입: 지정 계정에서 이미지 캐러셀 수집(릴스 제외) ──────
# ── 브라우저 확장(짤공장 릴스 헌터)이 보낸 수집 데이터 ────────────
IG_COLLECTED_FILE = BASE / "ig_collected.json"
_collect_lock = threading.Lock()


def _collected_load():
    try:
        d = json.loads(IG_COLLECTED_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _collected_save(items):
    try:
        IG_COLLECTED_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _tr_safe(s):
    s = re.sub(r"[^\w.\-가-힣]+", "_", str(s or ""), flags=re.UNICODE).strip("._-")
    return s[:60] or "reel"


def _tr_views(n):
    n = int(n or 0)
    if n >= 10000:
        return f"{round(n / 10000)}만뷰"
    if n >= 1000:
        return f"{round(n / 1000)}천뷰"
    return f"{n}뷰" if n else "조회수미상"


def _run_transcripts_job(jid, cfg, sel):
    """고른 릴스 여러 개 → 각 대본 추출 → 개별 TXT + 합본 TXT + ZIP. 대본은 수집항목에도 저장(학습용)."""
    import shutil
    import tempfile
    from datetime import datetime
    job = JOBS[jid]
    outroot = BASE / "_transcripts"
    outroot.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="tr_", dir=outroot))
    try:
        total = len(sel)
        job.update(status="running", pct=3, msg=f"대본 {total}개 추출 준비…")
        collected = _collected_load()
        by_sc = {x.get("shortcode"): x for x in collected}
        txts, ok = [], 0
        for i, it in enumerate(sel, 1):
            sc = (it.get("shortcode") or f"reel{i}")
            url = it.get("url", "")
            job["msg"] = f"{i}/{total} 대본 추출 중… {sc}"
            job["pct"] = int(3 + (i - 1) / max(1, total) * 92)
            text, lang, summary, err = "", "", "", ""
            vid = work / f"{_tr_safe(sc)}.mp4"
            try:
                youtube.download_video(url, str(vid), cookies=youtube._reel_cookies(cfg))
                res = youtube.transcribe_reel_text(cfg, str(vid))
                text, lang, summary = res.get("transcript", ""), res.get("lang", ""), res.get("summary", "")
                if text:
                    ok += 1
            except Exception as e:
                err = str(e)[:120]
            finally:
                try:
                    vid.unlink(missing_ok=True)
                except Exception:
                    pass
            tgt = by_sc.get(it.get("shortcode"))
            if tgt is not None and text:
                tgt["transcript"] = text
                tgt["transcript_lang"] = lang
            views = it.get("viewCount") or 0
            head = (f"# 릴스 대본  {sc}\nURL: {url}\n조회수: {views}\n언어: {lang or '미상'}"
                    f"\n요지: {summary or '-'}\n\n" + ("=" * 30) + "\n\n")
            fn = f"{i:02d}_{_tr_views(views)}_{_tr_safe(sc)}.txt"
            (work / fn).write_text(head + (text or f"(대본 없음/추출 실패: {err})") + "\n", encoding="utf-8")
            txts.append(work / fn)
        if any(by_sc.values()):
            _collected_save(collected)
        combined = work / "_합본_대본.txt"
        combined.write_text(
            f"# 릴스 대본 합본 · {total}개 · {datetime.now():%Y-%m-%d %H:%M}\n"
            "# (구조·어투 학습용 참고자료. 그대로 복붙 금지)\n\n"
            + "\n\n".join(f"----- {p.name} -----\n" + p.read_text(encoding="utf-8") for p in txts),
            encoding="utf-8")
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        zp = outroot / f"릴스대본_{total}개_{stamp}.zip"
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(combined, combined.name)
            for p in txts:
                zf.write(p, f"개별대본/{p.name}")
        job["result"] = {"zip": f"/api/ie/insta/transcripts/download/{zp.name}",
                         "count": total, "ok": ok, "name": zp.name}
        job.update(status="done", pct=100, msg=f"완료 — {ok}/{total}개 대본 추출")
    except Exception as e:
        job.update(status="error", error=str(e)[:200])
    finally:
        shutil.rmtree(work, ignore_errors=True)


@app.post("/api/ie/insta/transcripts")
def api_ie_insta_transcripts():
    """수집한 릴스 여러 개(shortcodes) → 대본만 추출해 ZIP. 대본은 수집항목에도 저장(재사용·학습용)."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    want = [str(s).strip() for s in (data.get("shortcodes") or []) if str(s).strip()]
    if not want:
        return jsonify(ok=False, error="대본 뽑을 릴스를 선택하세요"), 400
    coll = _collected_load()
    by_sc = {x.get("shortcode"): x for x in coll}
    sel = []
    for sc in want[:30]:
        it = by_sc.get(sc)
        if it and it.get("url"):
            sel.append(it)
    if not sel:
        return jsonify(ok=False, error="선택한 릴스의 URL을 찾을 수 없어요"), 404
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = {"status": "queued", "pct": 0, "msg": "대기 중…",
                 "result": None, "error": None, "ts": time.time()}
    threading.Thread(target=_run_transcripts_job, args=(jid, cfg, sel), daemon=True).start()
    return jsonify(ok=True, job=jid)


@app.get("/api/ie/insta/transcripts/download/<path:name>")
def api_ie_insta_transcripts_download(name):
    """추출한 대본 ZIP 다운로드."""
    safe = Path(name).name
    fp = (BASE / "_transcripts" / safe)
    if not fp.exists() or fp.suffix.lower() != ".zip":
        return jsonify(ok=False, error="파일 없음"), 404
    return send_from_directory(str(BASE / "_transcripts"), safe, as_attachment=True)


@app.post("/api/ie/insta/collect")
def api_ie_insta_collect():
    """확장 → 수집 항목 수신(인증 없음, 로컬 전용). shortcode 기준 dedup·조회수 최댓값 병합."""
    data = request.get_json(silent=True) or {}
    incoming = data.get("items") or []
    if not isinstance(incoming, list):
        return jsonify(ok=False, error="items 형식 오류"), 400
    with _collect_lock:
        cur = _collected_load()
        by_key = {(it.get("platform", ""), it.get("shortcode") or it.get("url")): it for it in cur}
        added = 0
        for it in incoming[:500]:
            if not isinstance(it, dict):
                continue
            key = (it.get("platform", ""), it.get("shortcode") or it.get("url"))
            if not key[1]:
                continue
            it["collected_at"] = datetime.now().isoformat(timespec="seconds")
            prev = by_key.get(key)
            if prev:   # 조회수는 0 아닌 최신값 우선, 나머지 최신으로 덮음
                it["viewCount"] = max(int(it.get("viewCount", 0) or 0), int(prev.get("viewCount", 0) or 0))
                if not it.get("imageUrls") and prev.get("imageUrls"):
                    it["imageUrls"] = prev["imageUrls"]
            else:
                added += 1
            by_key[key] = it
        merged = list(by_key.values())
        merged.sort(key=lambda x: (int(x.get("viewCount", 0) or 0), int(x.get("likeCount", 0) or 0)), reverse=True)
        merged = merged[:600]
        IG_COLLECTED_FILE.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify(ok=True, total=len(merged), added=added)


@app.post("/api/ie/insta/collected")
def api_ie_insta_collected():
    """수집 항목 목록(우리 UI용). 조회수/좋아요순."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    kind = (data.get("kind") or "all").strip()   # all|reel|image
    items = _collected_load()
    out = []
    for it in items:
        k = it.get("kind", "")
        if kind == "reel" and k != "reel":
            continue
        if kind == "image" and k not in ("image", "carousel"):
            continue
        imgs = it.get("imageUrls") or []
        out.append({
            "platform": it.get("platform", ""), "kind": k,
            "url": it.get("url", ""), "shortcode": it.get("shortcode", ""),
            "caption": (it.get("caption", "") or "")[:200],
            "viewCount": int(it.get("viewCount", 0) or 0),
            "likeCount": int(it.get("likeCount", 0) or 0),
            "n_img": len(imgs), "thumb": imgs[0] if imgs else it.get("thumbUrl", ""),
            "collected_at": it.get("collected_at", ""),
        })
    return jsonify(ok=True, items=out[:400])


@app.post("/api/ie/insta/collected_clear")
def api_ie_insta_collected_clear():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    sc = (data.get("shortcode") or "").strip()
    with _collect_lock:
        if sc:   # 한 건만 제거
            items = [x for x in _collected_load() if x.get("shortcode") != sc]
        else:    # 전체 비우기
            items = []
        IG_COLLECTED_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify(ok=True, total=len(items))


@app.post("/api/ie/insta/collect_import")
def api_ie_insta_collect_import():
    """수집한 이미지 게시물(캐러셀) → 그 이미지 URL로 완성팩 생성(소재로 바로 쓰기)."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    sc = (data.get("shortcode") or "").strip()
    item = next((x for x in _collected_load() if x.get("shortcode") == sc), None)
    if not item:
        return jsonify(ok=False, error="수집 항목을 찾을 수 없어요"), 404
    imgs = item.get("imageUrls") or []
    if not imgs:
        return jsonify(ok=False, error="이 게시물은 이미지 URL이 없어요 (릴스는 짤 만들기로)"), 400
    post = {"image_urls": imgs, "username": item.get("shortcode", ""),
            "caption": item.get("caption", ""), "url": item.get("url", ""),
            "likes": item.get("likeCount", 0)}
    now = time.time()
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = {"status": "queued", "pct": 0, "msg": "대기 중…",
                 "result": None, "error": None, "ts": now}
    threading.Thread(target=_run_ie_import, args=(jid, cfg, post), daemon=True).start()
    return jsonify(ok=True, job=jid)


def _run_reel_make_job(jid, url, cfg, hint="", blur=True, clean="none"):
    """인스타 릴스 URL → 영상 대본 짤 완성팩."""
    job = JOBS[jid]

    def log(m):
        m = str(m).strip()
        job["msg"] = m
        step = re.match(r"\[(\d)/5\]", m)
        if step:
            job["pct"] = {1: 10, 2: 35, 3: 60, 4: 82, 5: 92}.get(int(step.group(1)), job["pct"])

    try:
        job.update(status="running", pct=6)
        result = youtube.build_from_reel(url, cfg, BASE, caption_hint=hint, log=log,
                                         blur=blur, clean=clean)
        job["result"] = _pack_payload(result)
        job["pct"] = 100
        job["status"] = "done"
        job["msg"] = "완료"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)[:220]


@app.post("/api/ie/insta/collect_make")
def api_ie_insta_collect_make():
    """수집한 이미지 게시물 → 이미지 내려받아 '해외→한국 현지화' 완성팩(템플릿 적용) 생성."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    sc = (data.get("shortcode") or "").strip()
    item = next((x for x in _collected_load() if x.get("shortcode") == sc), None)
    if not item:
        return jsonify(ok=False, error="수집 항목을 찾을 수 없어요"), 404
    blur = data.get("blur") is not False   # 자막 블러(릴스), 기본 켬
    guide = (data.get("guide") or "").strip()
    imgs = item.get("imageUrls") or []
    if not imgs:
        # 릴스(영상) → 영상 다운로드 + Gemini 영상대본으로 짤 (유튜브 짤 품질)
        url = item.get("url", "")
        if not url:
            return jsonify(ok=False, error="릴스 URL이 없어요"), 400
        now = time.time()
        jid = uuid.uuid4().hex[:10]
        JOBS[jid] = {"status": "queued", "pct": 0, "msg": "대기 중…",
                     "result": None, "error": None, "ts": now}
        threading.Thread(target=_run_reel_make_job,
                         args=(jid, url, cfg, guide, blur, _clean(data)), daemon=True).start()
        return jsonify(ok=True, job=jid)
    import requests as _rq
    tmpdir = BASE / "_covertmp"
    tmpdir.mkdir(exist_ok=True)
    paths = []
    try:
        for i, u in enumerate(imgs[:10]):
            r = _rq.get(u, timeout=40)
            r.raise_for_status()
            p = tmpdir / (uuid.uuid4().hex[:12] + ".jpg")
            from PIL import Image as _Img
            import io as _io
            _Img.open(_io.BytesIO(r.content)).convert("RGB").save(p, "JPEG", quality=90)
            paths.append(str(p))
    except Exception as e:
        return jsonify(ok=False, error=f"이미지 내려받기 실패: {str(e)[:100]}"), 502
    now = time.time()
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = {"status": "queued", "pct": 0, "msg": "대기 중…",
                 "result": None, "error": None, "ts": now}
    JOBQ.put((jid, _run_images_job,
              (jid, paths, item.get("caption", ""), True, cfg,
               _template(data), _clean(data), guide)))
    return jsonify(ok=True, job=jid)


@app.post("/api/ie/insta/targets")
def api_ie_insta_targets():
    """지정 크롤 계정 목록 조회/추가/삭제. (누구나 조회, 관리자만 수정)"""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    action = (data.get("action") or "list").strip()
    if action != "list" and not _is_admin(cfg, data.get("code")):
        return jsonify(ok=False, error="관리자만 계정을 편집할 수 있습니다"), 403
    lst = insta_import.load_targets(BASE)
    if action == "add":
        many = data.get("usernames")
        if isinstance(many, list) and many:      # 팔로우 목록 등에서 일괄 추가
            adds = [str(u).strip().lstrip("@") for u in many if str(u).strip()]
            lst = insta_import.save_targets(BASE, lst + adds)
        else:
            u = (data.get("username") or "").strip().lstrip("@")
            if not u:
                return jsonify(ok=False, error="계정 아이디를 입력하세요"), 400
            lst = insta_import.save_targets(BASE, lst + [u])
    elif action == "remove":
        u = (data.get("username") or "").strip().lstrip("@").lower()
        lst = insta_import.save_targets(BASE, [x for x in lst if x.lower() != u])
    login = (cfg.get("ig_import_login") or {}).get("username", "")
    return jsonify(ok=True, targets=lst, login=login)


def _run_ie_following(jid, cfg):
    job = JOBS[jid]
    try:
        job.update(status="running", pct=20, msg="부계정 팔로우 목록 불러오는 중…")
        lst = insta_import.list_following(cfg, BASE,
                                          log=lambda m: job.update(msg=str(m)[:80]))
        job.update(status="done", pct=100, msg="완료", result={"following": lst})
    except Exception as e:
        job.update(status="error", error=str(e)[:200])


@app.post("/api/ie/insta/following")
def api_ie_insta_following():
    """부계정이 팔로우한 계정 목록 → 대상 계정 일괄 등록용. (관리자 전용)"""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _is_admin(cfg, data.get("code")):
        return jsonify(ok=False, error="대상 계정 편집은 관리자만 가능합니다"), 403
    now = time.time()
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = {"status": "queued", "pct": 0, "msg": "대기 중…",
                 "result": None, "error": None, "ts": now}
    threading.Thread(target=_run_ie_following, args=(jid, cfg), daemon=True).start()
    return jsonify(ok=True, job=jid)


def _run_ie_fetch(jid, cfg, usernames, per):
    job = JOBS[jid]
    try:
        job.update(status="running", pct=15, msg="인스타 접속·수집 중…")
        posts = insta_import.fetch_many(cfg, BASE, usernames, per=per,
                                        log=lambda m: job.update(msg=str(m)[:80]))
        for p in posts:
            p.pop("image_urls", None)   # 프런트로 원본 CDN URL은 안 보냄(썸네일만)
        job.update(status="done", pct=100, msg="완료", result={"posts": posts})
    except Exception as e:
        job.update(status="error", error=str(e)[:200])


@app.post("/api/ie/insta/fetch")
def api_ie_insta_fetch():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    usernames = insta_import.load_targets(BASE)
    only = (data.get("username") or "").strip().lstrip("@")
    if only:
        usernames = [only]
    if not usernames:
        return jsonify(ok=False, error="먼저 수집할 인스타 계정을 등록하세요"), 400
    per = max(2, min(12, int(data.get("per") or 4)))
    now = time.time()
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = {"status": "queued", "pct": 0, "msg": "대기 중…",
                 "result": None, "error": None, "ts": now}
    threading.Thread(target=_run_ie_fetch, args=(jid, cfg, usernames, per),
                     daemon=True).start()
    return jsonify(ok=True, job=jid)


def _run_ie_import(jid, cfg, post):
    job = JOBS[jid]
    try:
        job.update(status="running", pct=20, msg="이미지 내려받는 중…")
        r = insta_import.import_post(cfg, BASE, post,
                                     log=lambda m: job.update(msg=str(m)[:80]))
        job.update(status="done", pct=100, msg="완료",
                   result=_pack_payload(r))
    except Exception as e:
        job.update(status="error", error=str(e)[:200])


@app.post("/api/ie/insta/import")
def api_ie_insta_import():
    """수집 목록에서 고른 게시물(shortcode) → 이미지 재조회 후 완성팩 생성."""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if not _check_code(cfg, data.get("code")):
        return jsonify(ok=False, error="접속코드가 틀렸습니다"), 403
    shortcode = (data.get("shortcode") or "").strip()
    username = (data.get("username") or "").strip().lstrip("@")
    caption = data.get("caption") or ""
    if not shortcode:
        return jsonify(ok=False, error="게시물이 없습니다"), 400
    # shortcode로 이미지 URL 재조회(만료 대비)
    try:
        import instaloader
        L, _ = insta_import._loader(cfg, BASE)
        p = instaloader.Post.from_shortcode(L.context, shortcode)
        urls = []
        if p.typename == "GraphSidecar":
            urls = [n.display_url for n in p.get_sidecar_nodes() if not n.is_video]
        elif not p.is_video:
            urls = [p.url]
        if not urls:
            return jsonify(ok=False, error="이미지 게시물이 아닙니다"), 400
        post = {"image_urls": urls, "username": username or p.owner_username,
                "caption": caption or (p.caption or ""), "url": f"https://www.instagram.com/p/{shortcode}/",
                "likes": int(p.likes or 0)}
    except Exception as e:
        return jsonify(ok=False, error=f"게시물 조회 실패: {str(e)[:120]}"), 502
    now = time.time()
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = {"status": "queued", "pct": 0, "msg": "대기 중…",
                 "result": None, "error": None, "ts": now}
    threading.Thread(target=_run_ie_import, args=(jid, cfg, post), daemon=True).start()
    return jsonify(ok=True, job=jid)


if __name__ == "__main__":
    import logging
    (BASE / "logs").mkdir(exist_ok=True)
    _h = logging.FileHandler(BASE / "logs" / "server.log", encoding="utf-8")
    _h.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%m-%d %H:%M:%S"))
    for _name in ("werkzeug", "server"):
        _lg = logging.getLogger(_name)
        _lg.addHandler(_h)
        _lg.setLevel(logging.INFO)
    logging.getLogger("server").info("=== 짤공장 서버 시작 ===")
    cfg = load_config()
    _restore = _pending_load()
    logging.getLogger("server").info(
        f"복구 점검: pending {len(_restore)}건 / 파일존재 {PENDING_F.exists()} / 경로 {PENDING_F}")
    if _restore:
        for _jid, _info in list(_restore.items()):
            if time.time() - _info.get("ts", 0) > 86400:
                _pending_remove(_jid)
                continue
            JOBS[_jid] = {"status": "queued", "pct": 0,
                          "msg": "대기 중 (재시작 후 자동 재개)...",
                          "result": None, "error": None,
                          "ts": _info.get("ts", time.time())}
            JOBQ.put((_jid, _run_job, (_jid, _info["url"], cfg, None)))
        logging.getLogger("server").info(f"재시작 복구: 미완 작업 {len(_restore)}건 자동 재개")
    threading.Thread(target=_scheduler_loop, daemon=True).start()   # 예약 자동 게시
    app.run(host="0.0.0.0", port=int(cfg.get("server_port", 8777)), threaded=True)
