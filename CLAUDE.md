# meme-factory (짤공장 + 카드뉴스 공장)

인스타그램 콘텐츠 공장 2종:
1. **짤공장** (커뮤형) — 커뮤니티 유머글 → 완성팩(썸네일+짤+캡션). you_like_car_ 스타일. 2026-07-06 구축.
2. **카드뉴스 공장** (정보형) — 주제 한 줄 → 4:5 카드뉴스 캐러셀 + 전자책 PDF + 댓글 유도 캡션.
   쇼츠수익화 채널용 리드마그넷 퍼널. 2026-07-06 구축. **클로드 의존 없음(Gemini만 사용)**.

## ⚠️ 위치 (중요)
- **유일본: `E:\짤공장 (게시물자동화)`** — 서버·cloudflared 터널·watchdog 전부 여기서 실행.
- **고정 공개 주소(named tunnel, 2026-07-06 개통): `https://jjal.traffic-charger.com`** (카드뉴스는 /card).
  터널명 factory, 설정 `C:\Users\qkr90\.cloudflared\config.yml`(+credentials json — 절대 삭제 금지).
  trycloudflare 임시주소 시대 종료 — START-SERVER.cmd가 named 터널을 띄움. 트래픽차저 본체 사이트와 무관(DNS 레코드 1개).
  (D:\meme-factory 개발본은 2026-07-06 검증 후 삭제됨 — 여기서 바로 수정하고 RESTART-SERVER.cmd로 반영)
- 코드 수정 반영법: 파일 수정 → `RESTART-SERVER.cmd` 더블클릭 (감시견이 ~35초 내 새 코드로 부활).
- 완성팩 폴더는 **`결과물/`** (config `output_dir`, 기본값 결과물). 폴더명 임의 변경 금지 —
  server.py(_output_dir)와 src/pipeline.py·cardnews/pipeline.py(_output_root)가 같은 config를 읽는다.
- `_backup_20260706_card/` — 카드뉴스 통합 직전 백업(server.py 등). 한동안 문제없으면 삭제 가능.

## 파이프라인 (짤공장)
1. **URL 모드**: 게시물 링크 → 제목/본문/댓글/짤 추출 → Gemini가 캡션 작성 → 썸네일 렌더 → 완성팩
2. **이미지 모드**(폴백): 캡처 짤 투입 → Gemini가 이미지 읽고 작성 → 첫 짤로 썸네일

업로드는 반자동: 완성팩의 review.html 열어서 캡션 복사 → 인스타에 수동 업로드.
(완전자동 전환 시 Meta Graph API — 아직 미구현, 사용자 결정 사항)

## 파이프라인 (카드뉴스 공장)
주제 입력 → [1/4] Gemini 기획(표지/캡션/카테고리별 아이템 제목/댓글 키워드)
→ [2/4] 집필(10개 단위 배치, JSON 잘림 방지) → [3/4] 카드 렌더(표지+아이템카드+CTA)
→ [4/4] 전자책 PDF + 완성팩. 캐러셀엔 티저 8개만, PDF엔 전체(기본 60개) 수록.
댓글→DM 자동전송은 ManyChat으로 — **FUNNEL-GUIDE.md** 참고.

**3가지 모드(2026-07-07)** — /card UI 칩으로 선택, API {mode}:
- `normal` 일반 (위 파이프라인 그대로)
- `proof` 증빙 — `자료서랍/`의 후기·수익 캡처를 Gemini가 골라(cardnews/drawer.py)
  CTA 앞에 증빙 카드 삽입(card_proof_count, 기본 2). 자료서랍에 이미지 넣으면
  비전으로 자동 색인(index.json). 40장 사전 구축됨(수익 9/후기 28/스토리 3).
- `story` 내 스토리 — `cardnews/스토리원장.md`(에피소드 16개, 수치 출처 표기)를 근거로
  서사형 카드(표지+장면 5~7+CTA, 전자책 없음, story.json 저장, UI 수정 불가가 정상).
  원장에 없는 사실은 지어내지 않게 프롬프트로 잠금.
**오늘의 주제 추천** — 🔥 버튼(/api/card/trends, cardnews/news.py 구글뉴스 RSS 30분 캐시).
헤드라인 클릭 → 주제 채움 + 그 소식이 기획 프롬프트 근거(context)로 들어감.
Gemini JSON에서 인용 큰따옴표 미이스케이프는 brain._repair_quotes가 자동 보정,
503/429는 점증 백오프 재시도.

## 구조
- `server.py` — 직원용 웹서버(Flask, 포트 8777). `/` 짤공장, `/card` 카드뉴스 공장,
  `/p` 결과물 창고(폰 최적화 갤러리: 썸네일 3종 탭 선택→lead 업로드, 카드뉴스 문구
  수정→재생성, 캡션 수정, 업로드됨 배지).
  POST /api/make {code,url}, POST /api/card/make {code,topic,items,keyword},
  GET /api/job/<jid> (공용 진행률), POST /api/packs (지난 완성팩),
  POST /api/pack {code,pack} (상세+edit데이터), POST /api/card/edit
  {code,pack,plan,items,caption?,theme?} (Gemini 없이 재생성 — cardnews/pipeline.py
  rerender_pack, items.json 필수라 items.json 없는 팩은 수정 불가),
  POST /api/caption/save, /packs/ 서빙.
  접속코드=config.json access_code. 동시 생성 2건 제한(Semaphore).
  외부 공개는 cloudflared quick tunnel(bin\cloudflared.exe) — **외부 공개·자동시작 등록은
  보안 가드에 걸리므로 사용자가 직접 실행/승인해야 함** (README 참고).
- `src/pipeline.py` — 짤공장 코어 (CLI make.py와 server.py 공용). 후킹 후보 3개 → 썸네일 3장
  (thumb.jpg/thumb2/thumb3) 렌더. 제작 완료 시 seen.json에 URL 기록(중복 방지).
- `src/hunter.py` — 소재 수집기. 디시실베+루리웹유머베스트+에펨포텐 인기글을 추천순으로
  긁어 번갈아 배치. 정치 키워드 필터(DEFAULT_BLOCK, config "block_keywords"로 덮어쓰기),
  에펨은 politics1 클래스도 활용. 서버 /api/candidates (10분 캐시) → UI "인기글 불러오기"
  버튼 → 항목별 [만들기] 클릭이면 제작 시작, 제작된 글은 ✅표시.
- `make.py` — 짤공장 CLI. `python make.py <URL|이미지들|폴더> [--mock] [--no-open] [--debug]`
- `make_card.py` — 카드뉴스 CLI. `python make_card.py "주제" [--items 60] [--keyword 후킹]
  [--teaser 8] [--mock] [--no-open]` (stdout UTF-8 강제 — CP949 콘솔 대응)
- `src/extractors.py` — 디시/루리웹/에펨 전용 파서 + 범용 폴백. 모바일 URL 자동 변환.
  - 디시 이미지는 Referer 필수. 220px 미만 이미지는 아이콘으로 보고 버림.
- `src/brain.py` — Gemini REST 호출(gemini-2.5-flash). 스타일 few-shot은 STYLE_EXAMPLE 상수.
  - JSON 강제(response_mime_type). skip 판정: 정치/성적/참사조롱/신상.
- `src/thumbnail.py` — 1080x1350, Black Han Sans, 하단 그라데이션+카피 2줄+워터마크.
- `src/packer.py` — 결과물/날짜_제목/ 에 thumb.jpg, 01~NN.jpg, caption.txt, meta.json, review.html.
  캡션은 clip.exe로 클립보드 자동 복사(UTF-16 BOM 필수).
- `cardnews/brain.py` — 카드뉴스 두뇌. plan(기획 1회) + write_items(10개 배치 집필).
  검증/보정 포함(아이템 수 초과 잘라내기, 누락 채움, teaser 폴백).
- `cardnews/render.py` — 4:5 카드 렌더러(2160x2700). 표지/아이템(카드당 2개, 폰트 자동축소)/CTA.
  **테마 2종(config card_theme / UI·CLI 선택)**: ①hunter(기본)=유튜브 네온 다크 — 채널바+구독버튼+
  플레이어 목업+영상카드 썸네일(네온 그라데이션+이모지+가짜 조회수)+고정댓글 CTA, 쇼츠헌터 로고
  (assets/logo_hunter.png, card_logo) ②cream=크림+테라코타+픽셀 타이틀(구버전).
  ⚠️RGBA 캔버스에 draw.rounded_rectangle(fill=(r,g,b,a)) 반투명 직접 드로잉 금지 — RGB 변환 시
  알파가 버려져 원색이 됨. 반투명은 레이어+alpha_composite로.
- `cardnews/ebook.py` — 전자책 PDF(A4@150dpi, Pillow 멀티페이지 저장 — 외부 라이브러리 불필요).
  표지/사용법·목차/PART 구분/아이템 2개씩/아웃트로 = 프로필 누끼 사진(card_profile_photo,
  E:\프로필\누끼) + 카톡방·네이버카페·인스타 유입 링크(card_links).
- `cardnews/pipeline.py` — 오케스트레이션 + 완성팩(카드 NN.jpg, caption.txt, ebook.pdf,
  items.json, review.html, zip). 캡션에 댓글 키워드 CTA 누락 시 자동 보강.
- `insta.py` — **인스타 자동 업로드** (Meta 공식 Graph API, Instagram Login 방식 — 정책 안전).
  publish_pack(팩폴더)→캐러셀 컨테이너→발행. 이미지 공개 URL은 public_base_url(고정 터널) 기반.
  60일 토큰 7일마다 자동 연장(refresh_access_token). published.json으로 중복 방지.
  POST /api/insta/publish {code,pack,lead?,force?} + UI 📤 버튼(짤 결과/카드 결과/결과물 보기).
  연동 세팅(최초 1회 10분)은 **INSTA-SETUP.md**. ⚠️유튜브 커뮤니티 게시물은 공식 API 없음 —
  자동화는 약관 위반 소지라 미구현(YouTube Studio 예약 기능으로 반자동).
- `MAKE-URL.cmd` — URL 복사 후 더블클릭. `MAKE-IMAGES.cmd` — 짤 드래그&드롭.

## 설정 (config.json)
- `gemini_api_key` — https://aistudio.google.com/apikey (비었으면 --mock만 가능)
- `output_dir` — 완성팩 폴더명 (기본 "결과물")
- 짤공장: `watermark`(커뮤 계정 핸들), `hashtags`, `signature`, `fake_header` 등
- 카드뉴스: `card_watermark`/`card_handle`(정보 채널 핸들), `card_accent`(#E2683C),
  `card_label`(표지 라벨), `card_items`(60), `card_teaser`(8), `card_items_per_card`(2),
  `card_title_font`("pixel"|"black"), `card_brand_context`(기획 프롬프트에 들어가는 채널 소개),
  `card_profile_lines`(전자책 아웃트로), `card_hashtags`
- 폰트: assets/fonts — BlackHanSans, neodgm(픽셀), Pretendard Regular/SemiBold/ExtraBold

## 주의
- 접근 확인된 소스: 디시(gall.dcinside.com), 루리웹, 에펨코리아. 개드립/더쿠/인스티즈는 이 PC에서 접속 불가였음.
- 커뮤니티 스크래핑은 집 IP라서 되는 것 — 클라우드로 옮기면 차단될 수 있음. 로컬 실행 유지.
- .cmd 주석은 영문만 (CP949 규칙). 파이썬은 UTF-8. 콘솔 print에 em-dash(—) 등 특수문자 조심.
- 저작권/운영 리스크: 타인 게시물 재가공 업로드는 그레이존. skip 판정 무시하지 말 것.
- 카드뉴스 PDF도 업로드 전 내용 한 번 훑기 (items.json에 원자료).

## 👥 팀 협업 (다른 개발자가 다른 컴퓨터에서 기여)
- **운영 vs 개발 분리**: 실제 크롤·인스타 게시·서버는 **사장님 PC(이 유일본)에서만** 돈다.
  다른 개발자는 자기 컴에서 **코드만** 만들어 `git push` → 사장님이 검토 후 **`배포.cmd`**(git pull + 재시작)로 반영. 반자동 배포.
- 신규 개발자·그의 클로드코드는 **`개발자-온보딩.md`** 를 먼저 읽는다(설치·기여 흐름·절대 규칙).
- git 제외 대상(`.gitignore`): `config.json`(키 뭉치), `자료서랍/`(사장님·어머님 **실제 수익 스크린샷** — 개인정보), `결과물/`, `bin/`, 런타임 상태파일. → 개발자에겐 `config.example.json` 템플릿만 간다.
- 🚨 개발자가 인스타 업로드/릴스 함수를 테스트로 돌리면 **사장님 실계정에 진짜 올라간다** — 로컬 config의 인스타 항목을 비워 원천 차단, 실제 게시 검증은 사장님과 함께.
- **운영만** 할 직원은 개발 불필요: 공개 주소 + `access_code` 로 브라우저에서 다 됨(결과·업로드 전부 사장님 계정).

## 백로그
- 베스트글 자동 수집기(hunt.py) — 실베/루리웹베스트/포텐 주기 크롤 → 후보 자동 생성
- 폰 알림(ntfy) / 완전자동 업로드(Graph API) / 업로드 성과 추적
- 카드뉴스: 표지 시안 2~3종 후보 / 다크 테마 / PDF 자동 호스팅(Cloudflare Pages)
