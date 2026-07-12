# -*- coding: utf-8 -*-
"""카드뉴스 두뇌 — Gemini가 주제 하나로 기획(표지/캡션/목차) + 아이템 전체를 집필.
클로드 의존 없음: gemini-2.5-flash REST 호출만 사용 (짤공장 brain.py와 동일 방식).
호출 구조: 기획 1회 → 아이템 상세 10개 단위 배치 N회 (JSON 잘림 방지)."""
import base64
import io
import json
import os
import re
import time

import requests

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

DEFAULT_BRAND = ("유튜브 쇼츠 수익화를 가르치는 강사 '캥거루쇼츠'의 정보형 인스타그램 채널. "
                 "독학으로 쇼츠를 시작해 월 5000만원을 달성했고, 수강생에게 쇼츠 제작·알고리즘·"
                 "수익화 노하우를 알려준다. 타깃 독자는 쇼츠로 부수입을 만들고 싶은 초보 크리에이터.")

CAPTION_EXAMPLES = """예시1)
하루를 통째로 비워도, 일은 줄어 있습니다.

'AI를 잘 쓰는 법'을 찾기보다, 일을 대신 할 '구조'를 한 번 깔아두는 쪽이 빨라요.
방법은 단순해요 👉 [기능] = [도구 연결] + [프로젝트 지침]
도구 두어 개를 연결하고 지침 프롬프트 하나만 붙여넣으면, 그 업무가 알아서 돌아갑니다. ⚙️

그렇게 만든 자동화를 프롬프트 60개로 모았어요.

- 견적·클레임 메일만 골라 답장 초안까지
- 회의 녹취록을 결정/할 일/보류로 정리해 노션에 저장
- 경쟁사 가격을 매일 수집해 변동만 빨갛게

지금 가장 손이 많이 가는 일 하나만 골라, 그 프롬프트를 복붙해 보세요. ✨

예시2)
AI를 잘 쓰는 사람은
프롬프트보다 먼저 "구조"를 만듭니다.

메일 정리, 회의록, 자료 수집, 리포트 작성까지
도구를 연결하면 반복 업무가 비서처럼 돌아갑니다.

이번에 정리한 건
24시간 일하는 AI 비서 조합 30개입니다.

30개를 다 할 필요는 없고,
지금 제일 귀찮은 일 하나만 골라 따라 하면 됩니다."""

PLAN_PROMPT = """너는 정보형 인스타그램 카드뉴스 채널의 총괄 에디터다.
아래 주제로 '캐러셀 카드뉴스 + 무료 배포용 전자책(PDF) 리드마그넷' 한 세트를 기획하라.

## 채널 정보
{brand}

## 주제
{topic}
{context_block}
## 총 아이템 수
{n}개 (전자책에 전부 수록. 캐러셀에는 대표 {teaser}개만 공개)

## 출력 (반드시 이 JSON 형식만, 다른 텍스트 금지)
{{
  "title_top": "표지 제목 첫 줄 (7~13자) — 주제의 가장 흥미로운 후킹(의외의 사실·반전·호기심)으로. 뜬금 수익 금액 금지",
  "title_main": "표지 제목 둘째 줄 (7~13자) — 그 흥미가 주는 이득/개수로 이어받기(예 '~ {n}개'). 과장된 수익액을 헤드라인 주인공 삼지 마라",
  "subtitle": "표지 부제 한 줄 (15~25자, '복붙해서 바로 쓰는 ~' 같은 실용 어필)",
  "comment_keyword": "{keyword_rule}",
  "ebook_title": "전자책 제목 (표지 제목과 동일 계열, 12~20자)",
  "caption": "인스타 본문 전체",
  "image_query": "표지 사진 검색용 영어 키워드 2~4단어 (주제를 상징하는 구체 사물·장면·직업, 사람/추상 말고 촬영 가능한 대상. 예: youtube studio microphone, smartphone video editing)",
  "body_images": [{{"query": "본문 사이 사진 검색용 영어 키워드 2~4단어 (촬영 가능한 구체 장면)", "caption": "그 사진에 얹을 한국어 캡션 한 줄 (12~22자, 내용과 자연스럽게 연결)"}}],
  "categories": [
    {{"name": "카테고리명 (2~6자)", "items": ["아이템 제목 (8~18자)", "..."]}}
  ],
  "teaser": [대표로 캐러셀에 보여줄 아이템 번호 {teaser}개, 1부터 시작하는 정수]
}}

## 기획 규칙
- **표지는 주제의 흥미로 후킹하라(호기심·의외성·반전).** 근거 없는 큰 수익 금액(월 5000만원 등)을
  표지·헤드라인에 뜬금없이 박지 마라 — 강사 본인 수익을 독자 기대치로 단정 금지. 사람은 흥미로
  들어와서 본문·캡션에서 수익화·팁으로 자연스럽게 이어질 때 반응한다. (수익 얘기는 캡션·본문에서)
    ✗ 나쁜 표지: "월 5000만원 버는 법 40개" (주제 후킹 버리고 뜬금 금액)
    ✓ 좋은 표지: "'김부장'이 사람이 아니었다?" / "AI로 쇼츠 만드는 법 40" (주제 후킹 살림)
- categories: 4~6개 카테고리로 나누고, 전체 아이템 제목 합계가 정확히 {n}개여야 한다.
- 아이템 제목은 전부 서로 다른 각도. **제목부터 '앵글'이어야 한다** — 뭘 하는지 나열식
  말고, 궁금하게 만들거나 이득이 딱 보이게. 추상론·일반론 금지.
    ✗ 나쁜 제목: "경쟁 채널 AI 분석 툴 활용법", "트렌드 예측으로 바이럴 발굴"
    ✓ 좋은 제목: "터진 남의 영상, 30초 만에 '성공 공식' 역산하기",
                 "조회수 안 나오는 진짜 이유는 '첫 3초'가 아니다"
- 제목에 툴 이름만 넣지 마라(활용법/최적화/가이드 같은 맹탕 꼬리표 금지).
- teaser: 카테고리가 골고루 섞이게, 가장 후킹되는 것들로 {teaser}개 선정.
- comment_keyword: 한글 2~4자, 이 주제를 대표하는 쉬운 단어.

## 본문(caption) 규칙 — 아래 예시 톤을 그대로 따라할 것
- 구조: 강한 첫 줄(스크롤 멈춤) → 공감/문제 제기 2~3줄 → 이번 자료 소개 →
  대표 아이템 3~5개 불릿(✔ 또는 -) → "다 할 필요 없고 하나만 골라 해보라"는 부담 낮추기 →
  마지막에 반드시: 댓글에 '{{키워드}}' 남기면 DM으로 전자책 PDF를 보내준다 + 저장·팔로우 유도.
- 이모지는 문단당 최대 1개, 과하지 않게. 해시태그 금지(따로 붙임). 존댓말.
- 길이: 공백 포함 350~700자.

{caption_examples}
"""

ITEMS_PROMPT = """너는 정보형 카드뉴스의 본문 작가다.
카드뉴스 세트 "{pack_title}" 의 카테고리 "{category}" 에 들어갈 아이템들의 상세 내용을 작성하라.

## 채널 정보
{brand}

## 작성할 아이템 (번호. 제목)
{titles}

## 출력 (반드시 이 JSON 형식만)
{{
  "items": [
    {{
      "num": 아이템 번호(정수),
      "title": "아이템 제목 (받은 제목을 더 날카롭게 다듬어도 됨, 8~18자)",
      "emoji": "아이템 내용을 상징하는 이모지 딱 1개 (아이템끼리 최대한 겹치지 않게)",
      "lines": [
        {{"tag": "태그", "text": "내용 한 줄"}},
        {{"tag": "태그", "text": "내용 한 줄"}},
        {{"tag": "태그", "text": "내용 한 줄"}}
      ]
    }}
  ]
}}

## ★ 절대 규칙 — "밍밍하면 실패다" ★
독자가 "이건 나도 알아" 하면 그 카드는 죽은 카드다. 아이템 하나하나가
**당장 복붙해서 써먹거나, 남들이 안 알려주는 앵글**이어야 한다.

각 아이템의 lines(3~4줄)는 아래 셋 중 **최소 하나 이상**을 반드시 포함:
  (A) 복붙 실전 — 실제로 복사해 쓰는 프롬프트 전문·대본·문장·수식을 큰따옴표로.
      예) "내 채널 최근 영상 10개 제목이야: […]. 조회수 터진 3개의 공통 후킹
          패턴을 뽑고, 그 패턴으로 새 제목 20개 만들어줘"
  (B) 구체 수치·고유명사 — 막연한 '많이/높게'가 아니라 실제 숫자·비율·시간·이름.
      예) "썸네일 A/B를 48시간 돌리면 CTR 4%→7%대까지 갈림"
  (C) 역발상 앵글 — 남들 다 하는 소리 말고 의외의/반대 관점.
      예) "잘된 영상을 복제하지 말고, 일부러 '망하게' 리메이크해 알고리즘 중복을 피함"

## ✗ 금지 (이렇게 쓰면 전부 다시 써라)
- 툴 이름만 던지기: "VidIQ로 경쟁 채널을 분석하세요" (어떻게? 뭘 보고? 가 없음) → 금지
- 추상 동사 남발: 파악/활용/벤치마킹/승부/누리다/극대화/최적화 로 끝내기 → 금지
- "~하세요" 명령조만 반복 → 금지. 담백한 정보체·설명체로, 근거·수치를 곁들여라.
- 뻔한 일반론("좋은 콘텐츠가 중요합니다"), 연도 박힌 낡은 예시(2024 등) → 금지

## 형식 규칙
- lines 3~4줄. tag는 2~3글자 (공식/예시/상황/포인트/주의/실행/대본/수치/함정/역발상 등
  — 내용에 맞게 자유 선택, 한 아이템 안에서 중복 금지).
- text는 30~60자. 예문·대사·프롬프트는 반드시 큰따옴표로 인용.
- 독자는 초보다. 전문용어가 나오면 괄호로 짧게 풀어라.
- 같은 카테고리 안에서 아이템끼리 내용·표현이 겹치지 않게(각자 다른 각도).
- 모든 아이템({count}개)을 빠짐없이 작성하라.
"""


def _key(cfg):
    return (cfg.get("gemini_api_key") or "").strip() or os.environ.get("GEMINI_API_KEY", "")


def _parse_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(_repair_quotes(text))


_QUOTE_LINE = re.compile(r'^(\s*(?:"[^"]+"\s*:\s*)?")(.*)("\s*,?\s*)$')


def _repair_quotes(text):
    """모델이 문자열 값 안의 큰따옴표(인용 예문)를 이스케이프하지 않은 경우
    라인 단위로 보정 (pretty-print JSON 전제)"""
    fixed = []
    for line in text.split("\n"):
        m = _QUOTE_LINE.match(line)
        if m and '"' in m.group(2):
            mid = m.group(2).replace('\\"', '"').replace('"', '\\"')
            line = m.group(1) + mid + m.group(3)
        fixed.append(line)
    return "\n".join(fixed)


def _call_parts(cfg, parts, max_tokens=8192, temperature=0.8, thinking=0, retries=2):
    """parts(텍스트+inline_data 이미지 혼합) → JSON 응답. 멀티모달 공용 호출."""
    key = _key(cfg)
    if not key:
        raise RuntimeError(
            "Gemini API 키가 없습니다. config.json 의 gemini_api_key 를 확인하세요. "
            "(키 없이 테스트하려면 --mock)")
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "thinkingConfig": {"thinkingBudget": thinking},
        },
    }
    last_err = None
    for attempt in range(max(retries, 3)):
        if attempt:  # 503(혼잡)/429(한도) 대비 점증 대기
            time.sleep(4 * attempt)
        resp = requests.post(GEMINI_URL.format(model=model),
                             params={"key": key}, json=body, timeout=240)
        if resp.status_code != 200:
            last_err = RuntimeError(f"Gemini API 오류 {resp.status_code}: {resp.text[:300]}")
            continue
        try:
            cand = resp.json()["candidates"][0]
            # 응답이 여러 조각(parts)으로 나뉠 수 있어 전부 이어붙임 (첫 조각만 읽으면 잘림)
            raw = "".join(p.get("text", "")
                          for p in cand.get("content", {}).get("parts", []))
            fr = cand.get("finishReason")
            if fr == "MAX_TOKENS":
                last_err = RuntimeError("응답이 토큰 한도로 잘림(MAX_TOKENS) — 항목 수를 줄이거나 재시도")
                continue
            if not raw and fr not in (None, "STOP"):
                raise RuntimeError(f"응답 없음(finishReason={fr})")
            return _parse_json(raw)
        except Exception as e:
            last_err = e
    raise RuntimeError(f"Gemini 응답 파싱 실패: {last_err}")


def _call(cfg, prompt, max_tokens=8192, temperature=0.8, thinking=0, retries=2):
    return _call_parts(cfg, [{"text": prompt}], max_tokens=max_tokens,
                       temperature=temperature, thinking=thinking, retries=retries)


def _inline_image(path, max_side=1024):
    """이미지 파일 → Gemini inline_data part (JPEG 축소)."""
    from PIL import Image
    img = Image.open(path).convert("RGB")
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return {"inline_data": {"mime_type": "image/jpeg",
                            "data": base64.b64encode(buf.getvalue()).decode()}}


# ---------------------------------------------------------------- 해외 레퍼런스 소재 추출

REF_PROMPT = """아래 이미지는 해외(주로 미국) 인스타그램에서 반응이 좋았던 캐러셀 게시물의 캡처다.
이 게시물의 '핵심 소재'를 파악해서, 한국 인스타 타깃을 위한 카드뉴스로 재구성할 재료를 뽑아라.

## 캡션 원문 (있으면 참고, 없으면 이미지만으로 판단)
{caption}

## 출력 (반드시 이 JSON 형식만, 다른 텍스트 금지)
{{
  "source_lang": "원문 추정 언어 (예: 영어)",
  "topic": "한국어 카드뉴스 주제 한 문장 (구체적, 이득/숫자 포함, 12~40자)",
  "key_points": ["현지화해서 살릴 핵심 포인트 4~7개 (한국어, 각 10~40자)"],
  "hook_angle": "한국 타깃에 맞는 새 후킹 각도 한 줄",
  "avoid": "원본 티 나지 않게 바꿔야 할 요소 (미국 특정 브랜드/사례/제도 등)"
}}

## 규칙
- 직역 절대 금지. 소재의 정보·노하우 '알맹이'는 유지하되 표현은 완전히 새로.
- 이미지 속 텍스트가 외국어면 뜻을 읽어서 한국어로 요약.
- 카드뉴스로 만들 만한 '정보형/노하우형' 소재로 정리 (단순 밈/감성글이면 그 안의 정보 각도를 찾아라).
- 미국 특정 브랜드/제도/화폐는 한국 실정(원화·국내 서비스)으로 바꿀 것을 avoid에 명시."""


def reference_topic(cfg, image_paths, caption=""):
    """해외 게시물 캡처(+캡션 원문) → 한국 현지화용 topic/key_points 추출 (비전).
    반환: {source_lang, topic, key_points[], hook_angle, avoid}"""
    parts = [{"text": REF_PROMPT.format(caption=(caption or "(캡션 없음)")[:2000])}]
    n = 0
    for p in image_paths[:8]:
        try:
            parts.append(_inline_image(p))
            n += 1
        except Exception:
            continue
    if not n:
        raise RuntimeError("업로드한 이미지를 읽지 못했어요 — 캡처 파일을 확인해주세요")
    d = _call_parts(cfg, parts, max_tokens=4096, temperature=0.6, thinking=0)
    topic = str(d.get("topic", "")).strip()
    if not topic:
        raise RuntimeError("레퍼런스에서 주제를 뽑지 못했어요 — 캡처가 선명한지 확인해주세요")
    d["topic"] = topic
    d["key_points"] = [str(x).strip() for x in d.get("key_points", []) if str(x).strip()]
    return d


# ---------------------------------------------------------------- 해외 발행(번역·현지화)

TRANSLATE_SYS = ("너는 한국어 인스타그램 카드뉴스를 {lang} 현지 감성에 맞게 옮기는 로컬라이저다. "
                 "직역 금지 — 그 나라 사람이 쓴 것처럼 자연스러운 카피로. "
                 "숫자·핵심 의미·이모지는 유지하고, 문화적으로 어색한 표현은 현지식으로 바꿔라.")


def translate_plan(cfg, plan, lang="일본어"):
    """표지/부제/캡션/카테고리명/미리보기/댓글키워드를 현지 언어로 번역."""
    payload = {
        "title_top": plan.get("title_top", ""),
        "title_main": plan.get("title_main", ""),
        "subtitle": plan.get("subtitle", ""),
        "ebook_title": plan.get("ebook_title", ""),
        "caption": plan.get("caption", ""),
        "comment_keyword": plan.get("comment_keyword", ""),
        "preview_titles": plan.get("preview_titles", []),
        "categories": [c.get("name", "") for c in plan.get("categories", [])],
    }
    prompt = (TRANSLATE_SYS.format(lang=lang) +
              f"\n\n아래 JSON의 각 값을 {lang}로 현지화해 '똑같은 구조의 JSON'만 출력하라.\n"
              "- title_top/title_main/subtitle: 표지 카피 톤(짧고 임팩트, 원문 글자수 느낌 유지).\n"
              f"- caption: {lang} 인스타 본문 톤(정중체 기본, 이모지 적당히, 해시태그는 넣지 마라).\n"
              f"- comment_keyword: 현지인이 댓글로 치기 쉬운 짧은 단어({lang} 또는 영문).\n"
              "- preview_titles / categories: 배열의 순서와 개수를 그대로 유지.\n\n"
              + json.dumps(payload, ensure_ascii=False))
    return _call(cfg, prompt, max_tokens=4096, temperature=0.7, thinking=0)


def translate_items(cfg, items, lang="일본어", batch_size=8, log=print):
    """아이템(제목/태그/본문 라인)을 현지 언어로 번역. num 기준 매칭."""
    out, total = [], len(items)
    for start in range(0, total, batch_size):
        chunk = items[start:start + batch_size]
        payload = [{"num": it["num"], "title": it.get("title", ""),
                    "lines": [{"tag": l.get("tag", ""), "text": l.get("text", "")}
                              for l in it.get("lines", [])]}
                   for it in chunk]
        prompt = (TRANSLATE_SYS.format(lang=lang) +
                  f"\n\n아래 카드뉴스 아이템들을 {lang}로 현지화하라. "
                  '반드시 {"items":[...]} 구조의 JSON만 출력.\n'
                  "- num 정수 그대로 유지. title/tag/text 를 자연스러운 " + lang + "로.\n"
                  "- tag는 2~4자로 짧게. 숫자·고유명사 의미 유지, 직역 금지.\n\n"
                  + json.dumps({"items": payload}, ensure_ascii=False))
        try:
            d = _call(cfg, prompt, max_tokens=8192, temperature=0.6, thinking=0)
            by = {}
            for x in d.get("items", []):
                try:
                    by[int(x.get("num"))] = x
                except (TypeError, ValueError):
                    continue
        except Exception:
            by = {}
        for it in chunk:
            nit = dict(it)
            tr = by.get(it["num"])
            if tr:
                nit["title"] = str(tr.get("title") or it.get("title", "")).strip()
                lines = [{"tag": str(l.get("tag", ""))[:6],
                          "text": str(l.get("text", "")).strip()}
                         for l in tr.get("lines", []) if str(l.get("text", "")).strip()]
                if lines:
                    nit["lines"] = lines
            out.append(nit)
        log(f"      번역 {min(len(out), total)}/{total}개...")
    out.sort(key=lambda x: x["num"])
    return out


# ---------------------------------------------------------------- 기획

def plan(cfg, topic, n_items=60, teaser_count=9, keyword=None, mock=False,
         context=None, context_kind="news"):
    """주제 → 표지/캡션/카테고리별 아이템 제목/댓글 키워드 기획.
    context: 근거 텍스트 (있으면 프롬프트에 삽입)
    context_kind: 'news'(최신 소식 근거) 또는 'ref'(해외 레퍼런스 현지화)"""
    if mock:
        return _mock_plan(topic, n_items, teaser_count, keyword)

    context_block = ""
    if context and context_kind == "ref":
        context_block = (
            "\n## 해외 레퍼런스 소재 (한국 타깃으로 현지화할 것)\n"
            f"{context}\n"
            "- 위는 해외에서 반응이 좋았던 게시물의 핵심 소재다. 정보 '알맹이'는 살리되, "
            "후킹 문구·구성 순서·예시는 한국 정서에 맞게 완전히 새로 써서 벤치마킹 티를 없애라.\n"
            "- 미국 특정 브랜드/제도/화폐는 한국 실정(원화·국내 서비스·국내 사례)으로 치환하라.\n"
            "- 독자가 바로 써먹는 한국형 실용 팁으로 아이템을 구성하라.\n")
    elif context:
        context_block = (
            "\n## 최신 참고 소식 (이 소식을 근거로 기획할 것)\n"
            f"{context}\n"
            "- 소식은 배경 근거로만 쓰고, 아이템은 독자가 바로 써먹는 실용 팁으로 변환하라.\n"
            "- 시점 언급이 필요하면 소식의 날짜 기준으로 쓰되, 소식에 없는 사실을 지어내지 마라.\n")
    result = _call(cfg, PLAN_PROMPT.format(
        brand=cfg.get("card_brand_context", DEFAULT_BRAND),
        topic=topic, n=n_items, teaser=teaser_count,
        keyword_rule=(f"반드시 '{keyword}' 그대로 사용" if keyword
                      else "한글 2~4자 대표 키워드"),
        caption_examples=CAPTION_EXAMPLES, context_block=context_block,
    ), max_tokens=16384, temperature=0.8, thinking=1024)

    for k in ("title_top", "title_main", "subtitle", "comment_keyword",
              "ebook_title", "caption", "categories"):
        if not result.get(k):
            raise RuntimeError(f"기획 결과에 {k} 가 없습니다")
    if keyword:
        result["comment_keyword"] = keyword
    result["image_query"] = str(result.get("image_query", "")).strip()
    bi = []
    for x in (result.get("body_images") or [])[:3]:
        if isinstance(x, dict) and str(x.get("query", "")).strip():
            bi.append({"query": str(x["query"]).strip(),
                       "caption": str(x.get("caption", "")).strip()})
    result["body_images"] = bi

    # 아이템 수 보정: 초과분 잘라내고, 부족하면 부족한 대로 진행 (렌더는 개수 무관)
    total = 0
    for cat in result["categories"]:
        cat["items"] = [str(t).strip() for t in cat.get("items", []) if str(t).strip()]
        keep = max(0, n_items - total)
        cat["items"] = cat["items"][:keep]
        total += len(cat["items"])
    result["categories"] = [c for c in result["categories"] if c["items"]]
    result["n_items"] = total

    teaser = [t for t in result.get("teaser", []) if isinstance(t, int) and 1 <= t <= total]
    if len(teaser) < teaser_count:  # 폴백: 카테고리별로 앞에서부터 고르게
        teaser = _spread_teaser(result["categories"], teaser_count)
    result["teaser"] = sorted(set(teaser))[:teaser_count]
    return result


def _spread_teaser(categories, count):
    picks, offset = [], 0
    per = max(1, count // max(1, len(categories)))
    for cat in categories:
        picks += [offset + i + 1 for i in range(min(per, len(cat["items"])))]
        offset += len(cat["items"])
    i = 1
    while len(picks) < count and i <= offset:
        if i not in picks:
            picks.append(i)
        i += 1
    return picks[:count]


# ---------------------------------------------------------------- 집필

def write_items(cfg, plan_result, log=print, mock=False, batch_size=10):
    """기획된 카테고리별 아이템 제목 → 상세 내용(태그 라인) 전체 집필.
    반환: [{num, category, title, lines:[{tag,text}]}] (num 순서 정렬)"""
    pack_title = f"{plan_result['title_top']} {plan_result['title_main']}"
    all_items, num = [], 0
    total = sum(len(c["items"]) for c in plan_result["categories"])
    for cat in plan_result["categories"]:
        titles = [(num + i + 1, t) for i, t in enumerate(cat["items"])]
        num += len(titles)
        for start in range(0, len(titles), batch_size):
            batch = titles[start:start + batch_size]
            if mock:
                items = [_mock_item(n, t) for n, t in batch]
            else:
                numbered = "\n".join(f"{n}. {t}" for n, t in batch)
                result = _call(cfg, ITEMS_PROMPT.format(
                    brand=cfg.get("card_brand_context", DEFAULT_BRAND),
                    pack_title=pack_title, category=cat["name"],
                    titles=numbered, count=len(batch),
                ), max_tokens=8192, temperature=0.7, thinking=0)
                items = _validate_items(result.get("items", []), batch)
            for it in items:
                it["category"] = cat["name"]
            all_items += items
            log(f"      집필 {min(len(all_items), total)}/{total}개...")
    all_items.sort(key=lambda x: x["num"])
    return all_items


def _validate_items(items, batch):
    """응답 검증 + 누락분은 제목만으로 채움"""
    by_num = {}
    for it in items:
        try:
            n = int(it.get("num"))
        except (TypeError, ValueError):
            continue
        lines = [{"tag": str(l.get("tag", ""))[:4], "text": str(l.get("text", "")).strip()}
                 for l in it.get("lines", []) if str(l.get("text", "")).strip()]
        if lines:
            by_num[n] = {"num": n, "title": str(it.get("title", "")).strip(),
                         "emoji": str(it.get("emoji", "")).strip()[:2], "lines": lines[:4]}
    out = []
    for n, title in batch:
        got = by_num.get(n)
        if got:
            got["title"] = got["title"] or title
            out.append(got)
        else:
            out.append({"num": n, "title": title,
                        "lines": [{"tag": "내용", "text": "상세 내용 생성 실패 — 재실행 권장"}]})
    return out


# ---------------------------------------------------------------- 깐깐한 편집장(2차 감수)

POLISH_PROMPT = """너는 정보형 카드뉴스의 '깐깐한 편집장'이다.
아래는 이미 집필된 아이템들이다. 이 중 **밍밍한 것만** 골라내 다시 써라.
멀쩡하게 구체적인 아이템은 절대 손대지 마라(fixes에 넣지 마라).

## '밍밍하다'의 기준 (하나라도 걸리면 다시 써야 함)
- 툴 이름만 던지고 '어떻게'가 없다 ("VidIQ로 분석하세요")
- 추상 동사로 때운다 (파악/활용/벤치마킹/승부/누리다/극대화/최적화)
- "~하세요" 명령조만 반복, 근거·수치·예문이 없다
- 누구나 아는 뻔한 일반론, 낡은 연도(2024 등)가 박혀 있다

## 다시 쓸 때 (아래 셋 중 최소 하나를 반드시 넣어라)
(A) 복붙 실전 — 실제 복사해 쓰는 프롬프트/대본/문장을 큰따옴표로
(B) 구체 수치·고유명사 — 실제 숫자·비율·시간·이름
(C) 역발상 앵글 — 남들 안 하는 의외의/반대 관점
제목도 맹탕이면 날카롭게 고쳐라. text는 30~60자, tag는 2~3자(한 아이템 내 중복 금지).

## 검토할 아이템
{items}

## 출력 (반드시 이 JSON, 다시 쓴 것만)
{{"fixes": [
  {{"num": 번호(정수),
    "title": "고친 제목",
    "lines": [{{"tag": "태그", "text": "다시 쓴 한 줄"}}, ...]}}
]}}
- 손댈 게 없으면 {{"fixes": []}} 를 출력하라. 멀쩡한 것까지 억지로 바꾸지 마라.
"""


def _item_digest(it):
    lines = " / ".join(f"{l.get('tag','')}:{l.get('text','')}" for l in it.get("lines", []))
    return f"{it.get('num')}. [{it.get('title','')}] {lines}"


def polish_items(cfg, items, log=print, batch_size=20):
    """집필된 아이템을 '깐깐한 편집장'이 재감수 — 밍밍한 것만 골라 다시 씀.
    num 기준으로 title/lines만 교체(emoji·category 유지). 실패 시 원본 유지."""
    if not items:
        return items
    by_num = {it["num"]: it for it in items}
    fixed_cnt = 0
    for start in range(0, len(items), batch_size):
        chunk = items[start:start + batch_size]
        digest = "\n".join(_item_digest(it) for it in chunk)
        try:
            r = _call(cfg, POLISH_PROMPT.format(items=digest),
                      max_tokens=8192, temperature=0.75, thinking=512)
        except Exception as e:
            log(f"      (감수 배치 건너뜀: {str(e)[:50]})")
            continue
        for fx in r.get("fixes", []):
            try:
                n = int(fx.get("num"))
            except (TypeError, ValueError):
                continue
            it = by_num.get(n)
            if not it:
                continue
            lines = [{"tag": str(l.get("tag", ""))[:4] or "포인트",
                      "text": str(l.get("text", "")).strip()}
                     for l in fx.get("lines", []) if str(l.get("text", "")).strip()]
            if not lines:
                continue
            it["lines"] = lines[:4]
            nt = str(fx.get("title", "")).strip()
            if nt:
                it["title"] = nt
            fixed_cnt += 1
        log(f"      편집장 감수 {min(start + batch_size, len(items))}/{len(items)}개...")
    log(f"      ✍️ 밍밍한 {fixed_cnt}개 아이템을 실전형으로 다시 씀")
    return items


# ---------------------------------------------------------------- 스토리 기획

STORY_PROMPT = """너는 인스타그램 '스토리텔링 카드뉴스' 전문 에디터다.
아래 인물의 실화 원장에서 주제에 맞는 대목을 골라, 위기→전환→결과 서사의
캐러셀 카드뉴스 한 세트를 기획하라.

## 채널 정보
{brand}

## 주제/방향 (운영자가 지정)
{topic}

## 스토리 원장 (아래 사실만 사용, 지어내기 절대 금지)
{story}

## 출력 (반드시 이 JSON 형식만, 다른 텍스트 금지)
{{
  "title_top": "표지 첫 줄 (7~14자, 호기심 자극)",
  "title_main": "표지 둘째 줄 (7~14자, 반전이나 수치)",
  "subtitle": "표지 부제 (15~25자)",
  "comment_keyword": "{keyword_rule}",
  "caption": "인스타 본문 전체",
  "scenes": [
    {{"no": 1, "label": "장면 라벨 (2~4자: 시작/위기/바닥/전환/결과/교훈 등)",
      "heading": "장면 제목 (8~16자)",
      "lines": ["본문 문장 (20~45자)", "본문 문장", "본문 문장(선택)"],
      "quote": "그 시절 실제 심정/어록 한 줄 (없으면 빈 문자열)"}}
  ],
  "cta_line": "마지막 카드 핵심 한 줄 (15~25자)",
  "ebook_title": "DM으로 줄 전자책 제목 (12~20자, 스토리에서 배우는 실전 노하우 느낌)",
  "lessons": [
    {{"num": 1, "title": "실전 교훈/행동 지침 제목 (8~18자)",
      "emoji": "이모지 딱 1개",
      "lines": [
        {{"tag": "태그 (2~3자: 상황/실행/포인트/주의 등)", "text": "내용 한 줄 (25~55자)"}},
        {{"tag": "태그", "text": "내용 한 줄"}},
        {{"tag": "태그", "text": "내용 한 줄"}}
      ]}}
  ]
}}

## 기획 규칙
- scenes는 5~7개. 1번은 평온/시작, 중반에 위기·바닥, 후반에 전환·결과(수치 필수), 마지막은 교훈.
- 결과 장면에는 원장의 '검증된 수치'를 반드시 하나 이상 사용 (지어내기 금지).
- 문장은 짧고 구어체. 1인칭 시점("저는", "그때"). 감정이 드러나게.
- quote는 원장의 어록/말투 섹션을 참고해 실제 말투로.
- lessons는 10~12개 — 이 스토리에서 독자가 바로 따라할 수 있는 실행 지침.
  스토리·원장의 사실에 근거하고, 원장에 없는 수치는 절대 지어내지 않는다.
- caption: 강한 첫 줄 → 스토리 요약 3~5줄 → 댓글에 '{{키워드}}' 남기면 DM으로
  전자책 PDF를 보내준다 + 저장·팔로우 유도. 350~600자, 존댓말, 해시태그 금지.
"""


def plan_story(cfg, topic, story_text, keyword=None, mock=False):
    """스토리 원장 + 주제 → 서사형 카드뉴스 기획 (표지/장면들/CTA/캡션)"""
    if mock:
        result = _mock_story(topic, keyword)
    else:
        result = _call(cfg, STORY_PROMPT.format(
            brand=cfg.get("card_brand_context", DEFAULT_BRAND),
            topic=topic, story=story_text[:24000],
            keyword_rule=(f"반드시 '{keyword}' 그대로 사용" if keyword
                          else "한글 2~4자 대표 키워드"),
        ), max_tokens=16384, temperature=0.9, thinking=1024)
        for k in ("title_top", "title_main", "subtitle", "comment_keyword",
                  "caption", "scenes"):
            if not result.get(k):
                raise RuntimeError(f"스토리 기획 결과에 {k} 가 없습니다")
        if keyword:
            result["comment_keyword"] = keyword
    scenes = []
    for i, sc in enumerate(result["scenes"][:8], 1):
        lines = [str(l).strip() for l in sc.get("lines", []) if str(l).strip()]
        if not lines:
            continue
        scenes.append({"no": i, "label": str(sc.get("label", "")).strip()[:6] or "장면",
                       "heading": str(sc.get("heading", "")).strip(),
                       "lines": lines[:3],
                       "quote": str(sc.get("quote", "")).strip()})
    if len(scenes) < 3:
        raise RuntimeError("스토리 장면이 너무 적게 생성됐어요 — 다시 시도해주세요")
    result["scenes"] = scenes

    # 전자책용 교훈 아이템 (댓글→DM 리드마그넷)
    cat_name = "스토리에서 배운 것"
    lessons = []
    for i, ls in enumerate(result.get("lessons", [])[:14], 1):
        lines = [{"tag": str(l.get("tag", ""))[:4] or "실행",
                  "text": str(l.get("text", "")).strip()}
                 for l in ls.get("lines", []) if str(l.get("text", "")).strip()]
        if not lines:
            continue
        lessons.append({"num": i, "title": str(ls.get("title", "")).strip() or f"교훈 {i}",
                        "emoji": str(ls.get("emoji", "")).strip()[:2],
                        "category": cat_name, "lines": lines[:4]})
    result["lessons"] = lessons
    result["ebook_title"] = (str(result.get("ebook_title", "")).strip()
                             or f"{result['title_main']} — 실전 노트")
    result["n_items"] = len(lessons)
    result["categories"] = ([{"name": cat_name,
                              "items": [l["title"] for l in lessons]}]
                            if lessons else [])
    result["cta_line"] = str(result.get("cta_line", "")).strip()
    return result


def _mock_story(topic, keyword):
    return {
        "title_top": "채널 3개 말아먹고",
        "title_main": "월 2천 찍은 이야기",
        "subtitle": "포기 직전에 바꾼 딱 한 가지",
        "comment_keyword": keyword or "스토리",
        "caption": (f"모의 스토리 캡션입니다.\n\n주제: {topic}\n\n"
                    f"댓글에 '{keyword or '스토리'}' 남기면 DM으로 보내드려요."),
        "scenes": [
            {"no": 1, "label": "시작", "heading": "다들 쉽다길래 시작했죠",
             "lines": ["퇴근하고 새벽까지 영상을 만들었습니다", "조회수는 늘 두 자리였어요"],
             "quote": "이게 되는 게 맞나?"},
            {"no": 2, "label": "바닥", "heading": "채널 3개를 말아먹었습니다",
             "lines": ["수익 0원, 시간만 갈아 넣던 시절", "주변에선 그만하라고 했습니다"],
             "quote": ""},
            {"no": 3, "label": "전환", "heading": "딱 하나를 바꿨습니다",
             "lines": ["감이 아니라 데이터로 소재를 골랐습니다", "터진 영상의 공식을 역산했어요"],
             "quote": ""},
            {"no": 4, "label": "결과", "heading": "월 2,000만 원",
             "lines": ["같은 노력, 완전히 다른 결과", "이제는 수강생들이 같은 길을 갑니다"],
             "quote": "노력의 방향이 전부였습니다"},
        ],
        "cta_line": "당신의 채널에도 공식은 있습니다",
        "ebook_title": "바닥에서 배운 실전 노트",
        "lessons": [{"num": i, "title": f"모의 교훈 {i}", "emoji": "🔥",
                     "lines": [{"tag": "실행", "text": "모의 모드 교훈 내용 한 줄"},
                               {"tag": "포인트", "text": "모의 모드 포인트 한 줄"}]}
                    for i in range(1, 11)],
    }


# ---------------------------------------------------------------- 모의 모드

def _mock_plan(topic, n, teaser_count, keyword):
    cats, made = [], 0
    names = ["기획", "대본", "편집", "알고리즘", "수익화", "운영"]
    per = max(1, (n + len(names) - 1) // len(names))
    for name in names:
        items = [f"{name} 꿀팁 {i + 1} — {topic[:8]}" for i in range(min(per, n - made))]
        made += len(items)
        if items:
            cats.append({"name": name, "items": items})
        if made >= n:
            break
    plan_result = {
        "title_top": "쇼츠 조회수 2배",
        "title_main": f"실전 공식 {n}개",
        "subtitle": "복붙해서 바로 쓰는 쇼츠 실전 노하우",
        "comment_keyword": keyword or "쇼츠",
        "ebook_title": f"쇼츠 실전 공식 {n}",
        "caption": (f"모의 실행으로 생성된 캡션입니다.\n\n주제: {topic}\n\n"
                    f"✔ 대표 아이템 불릿 1\n✔ 대표 아이템 불릿 2\n✔ 대표 아이템 불릿 3\n\n"
                    f"댓글에 '{keyword or '쇼츠'}' 남기면 전체 {n}개 PDF를 DM으로 보내드려요."),
        "categories": cats,
        "n_items": made,
    }
    plan_result["teaser"] = _spread_teaser(cats, teaser_count)
    return plan_result


def _mock_item(n, title):
    pool = ["🔥", "🎯", "⚡", "💰", "📈", "🎬", "✂️", "🧠", "📌", "🚀"]
    return {"num": n, "title": title, "emoji": pool[n % len(pool)], "lines": [
        {"tag": "공식", "text": "모의 모드 예시 문장 — 실제로는 Gemini가 작성합니다"},
        {"tag": "예시", "text": "\"이렇게 큰따옴표 인용 예문이 들어갑니다\""},
        {"tag": "포인트", "text": "숫자와 구체 수치를 살린 실행 팁 한 줄 (25~55자)"},
    ]}
