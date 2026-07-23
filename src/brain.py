# -*- coding: utf-8 -*-
"""Gemini 두뇌 — 짤/본문을 읽고 썸네일 카피 2줄 + 인스타 본문을 작성"""
import base64
import io
import json
import os
import re
import time

import requests
from PIL import Image

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

STYLE_EXAMPLE = """노가다 단톡방 근황
셔틀 질문 하나로 방이 터짐

한 노가다 단톡방 대화 캡처가 온라인에서 화제가 됐다.

사진에는 한 사람이 새벽부터 "셔틀 어디서 타요"를 반복해서 묻는 장면이 담겼다. 처음에는 단순히 출근 셔틀 위치를 모르는 사람처럼 보였지만, 답변이 늦어지자 분위기는 점점 이상해졌다.

다른 사람들이 "지역이 어디냐", "대림노선이다"처럼 답해주려 했지만, 해당 인물은 계속 같은 질문을 반복하거나 갑자기 다른 사람들에게 짜증 섞인 말을 하기 시작했다.

심지어 "셔틀 놓치게 해놓고 정상 운행했다고 하면 노동청에 신고하겠다"는 말까지 나오면서, 단톡방은 순식간에 출근 안내방이 아니라 실시간 멘탈 붕괴 현장이 되어버렸다.

댓글에서는 "직원한테 물어보면 되지 왜 단톡방을 터뜨리냐", "한 명 때문에 난리 난 건데 직업 전체로 일반화하면 안 된다" 같은 반응이 이어졌다.

결국 이 짤의 웃긴 포인트는 필요한 정보는 셔틀 위치 하나였는데, 대화는 분노와 반복 질문으로 끝없이 굴러갔다는 점이다. 셔틀은 아직 타지도 못했는데, 단톡방은 이미 과열 운행 중이었다."""

PROMPT = """너는 커뮤니티 유머글을 큐레이션하는 인스타그램 계정의 전속 작가다.
아래 커뮤니티 게시물(제목/본문/댓글)과 첨부된 짤 이미지들을 보고, 인스타 게시물용 글을 작성하라.
이미지 속 한국어 텍스트(채팅 캡처, 댓글 캡처 등)를 꼼꼼히 읽고 내용 파악에 활용하라.

## 출력 (반드시 이 JSON 형식만)
{{
  "skip": false,
  "skip_reason": "",
  "hooks": [
    {{"line1": "썸네일 첫 줄", "line2": "썸네일 둘째 줄"}},
    {{"line1": "다른 스타일 후보", "line2": "다른 스타일 후보"}},
    {{"line1": "또 다른 스타일 후보", "line2": "또 다른 스타일 후보"}}
  ],
  "caption": "인스타 본문 전체"
}}

## 썸네일 후킹(hooks) 규칙 — 서로 다른 공식으로 3개
- line1: 6~13자, line2: 6~16자. 두 줄이 한 세트로 낚시가 완성돼야 함
- 반드시 서로 다른 공식 3가지를 쓸 것:
  ① 근황/현장형: "OO 근황", "OO 대참사", "OO 논란" + 핵심 대사 인용
  ② 질문/가정형: 뒷내용이 궁금해지는 질문이나 "만약 ~했다면?"
  ③ 반전/의외형: 평범한 첫 줄 → 둘째 줄에서 예상을 깨는 포인트
- 대사 인용은 큰따옴표. 숫자가 있으면 살릴 것 (구체적 숫자 = 후킹력)
- 잘 팔린 실제 예시 (이 감각을 복제할 것):
  "노가다 단톡방 근황" / "셔틀 어디서 타요"
  "미국 주식 최초 개장 때" / "1달러를 투자했다면?"
  "여사친이 보낸 은밀한 사진" / "뭐야 이거 어디서 구한거야"
  "햄버거 여러 개 시켰더니" / "영수증에 햄최몇? 적혔다"
  "기숙사 룸메가 쓴 쪽지" / "가정환경이 궁금해 진다"
  "반박불가 인류의 친구" / "개가 진짜 GOAT인 이유"

## 본문(caption) 규칙
- 구조: 1행 헤드라인 → 2행 서브 한줄 요약 → 빈 줄 → 짧은 문단 3~5개
- 첫 문단 첫 문장이 승부처: 가장 웃긴 지점을 살짝 보여주되 다 말하지 말 것 (스크롤 멈추게)
- 문체: 가벼운 기사체. 상황을 담백하게 전달하다가 슬쩍 비트는 유머
- 짤 속의 웃긴 대사는 요약하지 말고 큰따옴표로 그대로 인용할 것 (원문 대사가 제일 웃기다)
- 댓글 반응이 있으면 제일 웃긴 댓글 1~2개를 그대로 인용하는 문단 포함
- 마지막 문단은 반드시 상황을 비꼬는 한 줄 드립으로 마무리 (본문에서 쓴 단어를 비틀어 재활용하면 더 좋음)
- 소재가 찬반이 갈릴 만한 주제면, 마지막 드립 뒤에 독자 의견을 묻는 짧은 한 줄을 덧붙일 것 (예: "여러분이라면 어느 쪽입니까?") — 댓글을 유도한다
- 이모지·해시태그·욕설 금지, 실명/전화번호/신상 언급 금지
- 길이: 공백 포함 400~800자

## 스타일 예시 (이 톤을 그대로 따라할 것)
{style_example}

## skip 판정 (참고 의견일 뿐 — 글은 무조건 전부 작성할 것)
아래에 해당하면 skip=true 로 표시하고 이유를 적어라. 단, skip이어도
hooks 3개와 caption은 평소처럼 전부 작성하라. 업로드 여부는 사람이 결정한다:
- 정치인/정당/선거가 소재의 핵심
- 성적으로 수위가 높음
- 실제 사망/참사/범죄 피해자 조롱
- 특정인 신상털이·혐오 표현이 웃음 포인트

## 게시물 정보
제목: {title}
본문 텍스트:
{text}

베스트 댓글:
{comments}
"""

MOCK_RESULT = {
    "skip": False,
    "skip_reason": "",
    "hooks": [
        {"line1": "테스트 썸네일 후보1", "line2": "\"모의 실행 모드입니다\""},
        {"line1": "테스트 후보2 질문형", "line2": "이게 만약 실전이었다면?"},
        {"line1": "테스트 후보3 반전형", "line2": "사실 아무 일도 없었다"},
    ],
    "caption": ("테스트 헤드라인\n모의 실행으로 생성된 본문입니다\n\n"
                "이 글은 Gemini API 키 없이 --mock 옵션으로 생성됐다.\n\n"
                "실제 운영에서는 이 자리에 짤을 읽고 쓴 기사체 유머 본문이 들어간다.\n\n"
                "결국 테스트인데도 완성팩은 만들어졌다는 게 이 파이프라인의 웃긴 포인트다."),
}


def _inline_image(path, max_side=1024):
    img = Image.open(path).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return {"inline_data": {"mime_type": "image/jpeg",
                            "data": base64.b64encode(buf.getvalue()).decode()}}


_QUOTE_LINE = re.compile(r'^(\s*(?:"[^"]+"\s*:\s*)?")(.*)("\s*,?\s*)$')


def _repair_quotes(text):
    """모델이 문자열 값 안의 큰따옴표(예: 본문 속 "골판지 드론")를 이스케이프하지
    않아 JSON이 깨질 때, 줄 단위로 내부 따옴표만 escape (pretty-print JSON 전제)."""
    fixed = []
    for line in text.split("\n"):
        m = _QUOTE_LINE.match(line)
        if m and '"' in m.group(2):
            mid = m.group(2).replace('\\"', '"').replace('"', '\\"')
            line = m.group(1) + mid + m.group(3)
        fixed.append(line)
    return "\n".join(fixed)


def _parse_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(_repair_quotes(text))


def score_debate(cfg, titles):
    """제목 목록 → 댓글 갑론을박(떡밥) 유발 가능성 0~10 점수 리스트. 실패 시 0들."""
    key = (cfg.get("gemini_api_key") or "").strip() or os.environ.get("GEMINI_API_KEY", "")
    if not key or not titles:
        return [0] * len(titles)
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(titles))
    prompt = (
        "아래는 커뮤니티 게시물 제목들이다. 각 제목이 인스타에 올라갔을 때 "
        "댓글창에서 의견 대립/갑론을박(떡밥)이 벌어질 가능성을 0~10 정수로 평가하라.\n"
        "10 = 확실한 찬반 논쟁 유발 (예: '15억 받고 한식 포기 가능?'), "
        "0 = 논쟁 여지 없음 (그냥 귀여움/신기함).\n"
        f'JSON만 출력: {{"scores": [숫자 {len(titles)}개]}}\n\n{numbered}')
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json",
                             "temperature": 0.2, "maxOutputTokens": 8192,
                             # 단순 채점이라 생각(thinking) 비활성 — 토큰 잘림 방지
                             "thinkingConfig": {"thinkingBudget": 0}},
    }
    try:
        resp = requests.post(
            GEMINI_URL.format(model=cfg.get("gemini_model", "gemini-2.5-flash")),
            params={"key": key}, json=body, timeout=90)
        cand = resp.json()["candidates"][0]
        raw = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
        scores = _parse_json(raw)["scores"]
        scores = [max(0, min(10, int(s))) for s in scores]
        return (scores + [0] * len(titles))[:len(titles)]
    except Exception:
        return [0] * len(titles)


STORY_PROMPT = """너는 인스타그램 '스토리카드' 캐러셀 채널의 전속 작가다.
아래 게시물(제목/본문/댓글)과 첨부 이미지들을 보고, 이미지 {n}장에 각각 얹을
카드 {n}장 분량의 서사를 작성하라. 이미지 속 텍스트도 꼼꼼히 읽어 활용하라.

## 출력 (반드시 이 JSON 형식만)
{{
  "skip": false,
  "skip_reason": "",
  "headlines": ["1번 카드 제목 후보", "다른 스타일 후보", "또 다른 후보"],
  "headlines_ko": ["1번 후보의 한국어 뜻", "2번 후보의 한국어 뜻", "3번 후보의 한국어 뜻"],
  "cards": [
    {{"paragraphs": [
      {{"text": "한 줄\\n두 줄\\n세 줄", "bold": false}},
      {{"text": "강조하고 싶은 한두 줄", "bold": true}}
    ], "ko": "이 카드 본문 전체의 한국어 뜻"}}
  ],
  "caption": "인스타 본문 전체"
}}

## 규칙
- cards 는 정확히 {n}개. i번째 카드는 i번째 이미지 아래에 붙는다.
- 각 카드: 문단 2~4개. 문단 안에서 '\\n' 으로 줄을 끊어라 (한 줄 15~32자, 한 문장=한 줄).
- 카드마다 bold=true 문단은 최대 1개 (가장 결정적인 대목만).
- 서사 흐름: 1번=상황 제시로 후킹 → 중간=전개와 반전 → 마지막=결말 + 여운 한 줄.
- 담백한 서술체("~했음", "~한다", "~했습니다" 중 하나로 통일). 과장·이모지·해시태그 금지.
- headlines: 3개 모두 서로 다른 공식(질문형/반전형/선언형). 각 10~22자. 1번 카드 위에 얹힌다.
- headlines_ko: 각 headline을 운영자가 고를 수 있게 '한국어 뜻'으로 번역(개수·순서 동일). 일본어 헤드라인이면 그 의미를 자연스러운 한국어로 옮겨라.
- 각 카드의 "ko": 그 카드 본문(paragraphs 전부)을 자연스러운 한국어로 옮긴 것. 운영자(한국인)가 본문의 오역·오류를 확인하는 용도이니, 카드에 실제로 쓴 문장과 의미가 정확히 일치해야 한다(내용 추가·생략·과장 금지). 카드 개수만큼 빠짐없이.
- caption: 인스타 본문. 카드에 쓴 내용을 요약하지 말고, 짧은 도입 2~3줄 + 마지막에
  독자 의견을 묻는 한 줄. 200~400자. 해시태그 금지(따로 붙임).

## 게시물 정보
제목: {title}
본문 텍스트:
{text}

베스트 댓글:
{comments}
"""

LOCALIZE_PREFIX = """[해외 → 한국 현지화 모드]
첨부 이미지는 해외(미국·일본 등)에서 화제가 된 게시물의 캡처다.
이미지 속 '외국어 텍스트'를 읽어 뜻을 파악하고, 한국 커뮤니티 감성에 맞게
유머·후킹·본문을 완전히 새로 '현지화'해서 써라. 아래를 지켜라:
- 직역 금지. 한국 사람이 보고 바로 웃긴 한국식 표현·밈으로 바꿔라.
- 해외 특정 인물/브랜드/제도/화폐는 한국에서 통하는 맥락으로 치환하거나 짧게 풀어라.
- 원문을 베낀 티가 나지 않게 후킹 문구와 구성을 새로 짜라.
- 짤의 '웃긴 핵심'은 유지하되, 대사 인용이 외국어면 자연스러운 한국어 의역으로.
- 아래 '게시물 정보'가 비어 있으면 이미지만으로 판단하라.

"""

JP_STORY_PREFIX = """[일본어 스토리카드 — 네이티브 재현 모드 (언어·톤 지시가 최우선)]
이 채널은 일본 인스타그램에서 '흥미로운 사연·비하인드'를 재미있게 풀어 반응을 얻는
커뮤니티형 큐레이션 계정이다. 소재를 '번역'하지 말고, 처음부터 일본인 작가가 일본
독자를 위해 쓴 것처럼 새로 써라. 일본인이 읽어 번역티·어색함이 조금도 없어야 한다.
이 지시가 아래 본문 규칙보다 우선한다.

[가장 중요 — 카드 본문 톤: 커뮤니티에서 반응 터지는 '흥미·몰입' 말투]
- 카드 본문은 딱딱한 설명체·뉴스체가 절대 아니다. 커뮤니티·인스타에서 읽는 맛 나고
  몰입되는 말투로: 후킹 → 긴장 고조 → 반전 → 한 방. (원 소재의 재미 포인트를 반드시 살려라.)
- 흥미 장치를 적극 써라: 「まさかの〜」「なんと〜」「衝撃の〜」「実は〜」같은 후킹어,
  다음 카드가 궁금해지는 클리프행어(예: 「〜だったのですが…」로 끊기), 살짝 던지는 감탄·리액션.
- 결정적 대목은 짧고 굵게 — 체언止め(명사·동사로 딱 끊는 끝맺음)·단문으로 임팩트. bold 문단이 여기.
- 단, 품위 유지: 2ch식 과한 슬랭·「w」「草」남발·오글거림 금지(싸구려로 보임).
  '재치있고 몰입되지만 깔끔한' 선을 지켜라.
- 소재가 감동 사연이면 흥미는 유지하되 뭉클함도 살려라(톤을 소재에 맞춰 조절).
- 마지막 카드는 임팩트 있는 한 줄로 여운·반전을 남겨라.

[일본 네이티브 스타일 견본 — 톤·리듬·표현 감각만 흡수(내용·소재는 절대 베끼지 마라)]
아래는 이 계정이 지향하는 '일본 커뮤니티/인스타 사연' 말투다. 문장은 매번 새로 써라.
■ 헤드라인 예: 「実は一度、姿を消しかけた"あの定番商品"」
■ 본문 예:
今や知らない人はいない、超ロングセラー商品。
でも実は発売直後は全然売れず、
生産終了の一歩手前まで追い込まれていたんです。
そんな絶体絶命のなか、担当者が打った——
まさかの一手とは…？
（다음 카드）なんと、変えたのは"パッケージの色"だけ。
たったそれだけで、売上は一気に10倍に跳ね上がったのです。
→ 포인트: 実は/でも実は/なんと/まさかの〜とは…？(끊기)/たったそれだけで 같은 네이티브 후킹,
   だったんです·跳ね上がったのです 같은 자연스러운 어미, ダッシュ(——)·체언止め로 임팩트.

[일본어 자연스러움 — 번역투 박멸]
- 기본은 です・ます체(친근하게), 임팩트 줄은 체언止め·짧은 단문 허용. 한국어 어순·직역투
  절대 금지('~라고요/~거든요/~더라/~함/~음' 같은 한국어 말투를 그대로 옮기지 마라).
- 조사·접속·문장 리듬이 네이티브처럼 자연스럽게. 일본에서 실제 쓰는 표현·한자를 써라.
- 인명은 가타카나(예: フレデリック・スミス), 지명·고유명사도 일본식 표기.
- 한국 고유의 인물/지명/제도는 일본 독자가 알기 쉽게 풀거나 치환.
- ⚠️금액 환산 주의(실수 최다): 円 환산 시 자릿수(10배·100배) 실수 절대 금지 — 반드시 검산.
  기준 1엔≒9.5원 (1만원≒1050엔 / 1억원≒1050만엔 / 13억원≒1.3억엔 / 1000만달러≒15억엔).
  확실치 않으면 원문 통화(원·달러)만 써라. **헤드라인에는 환산 금액을 넣지 마라**(원문 통화
  또는 생략) — 円 환산은 본문에서만, 「원문(약 円환산)」 형식으로 병기.
- 줄바꿈('\\n')은 구·절 단위로 자연스럽게(렌더가 폭에 맞춰 이어붙임).

[캡션 — 커뮤니티 말투 절대 금지, 차분한 '정보·기사체'로]
- caption은 카드 본문의 캐주얼한 커뮤 말투와 완전히 다르게, 신문 기사처럼 차분하고 정확한
  '정보성 문체'로 써라(なんと·まさかの 같은 후킹어·이모지·구어체 금지).
- 구성: ①배경(연도·장소·사건명·인물 등)을 짚고 → ②핵심 사실·경위를 구체적으로 →
  ③의미·결말·후일담까지. 2~4문단으로 매끄럽게(카드처럼 짧게 끊지 말 것). 350~650자.
- 사실에 충실히. 유명한 실화면 알려진 사실(연도·수치·기관·고유명)을 정확히 활용하되,
  확실치 않은 구체 수치·날짜·명칭은 지어내지 마라(틀린 사실 삽입 절대 금지).
- 금액은 원문 통화 + 괄호로 円 환산. 마지막에 독자 반응을 부르는 한 줄(과하지 않게, 담백히).
- 해시태그 금지(따로 붙음).

- headlines(각 8~18자, 궁금증 유발형)·cards·caption 전부 일본어. 단 headlines_ko와 각
  카드의 "ko"만은 '한국어 뜻'(운영자가 헤드라인 선택·본문 오역 확인용). JSON 키 이름은 영어 그대로.

"""


GUIDE_PREFIX = (
    "[운영자 방향 지시 — 다른 어떤 규칙보다 최우선]\n"
    "아래는 이 게시물의 썸네일·본문을 어떤 방향/톤/앵글로 뽑을지에 대한 운영자의 직접\n"
    "지시다. 이 지시를 가장 먼저·강하게 반영하라(단, 사실 왜곡·없는 사실 지어내기 금지).\n"
    "▶ 지시: {guide}\n\n")


def write_copy(cfg, title, text, comments, image_paths, mock=False, localize=False,
               guide=""):
    if mock:
        result = json.loads(json.dumps(MOCK_RESULT))
        if title:
            result["hooks"][0]["line1"] = title[:13]
        return result

    key = (cfg.get("gemini_api_key") or "").strip() or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError(
            "Gemini API 키가 없습니다.\n"
            "https://aistudio.google.com/apikey 에서 키를 만들어\n"
            "D:\\meme-factory\\config.json 의 \"gemini_api_key\" 에 붙여넣어 주세요.\n"
            "(키 없이 테스트하려면 --mock 옵션)")

    model = cfg.get("gemini_model", "gemini-2.5-flash")
    prompt = PROMPT.format(
        style_example=STYLE_EXAMPLE,
        title=title or "(제목 없음)",
        text=(text or "(본문 텍스트 없음 — 이미지가 본문임)")[:1800],
        comments="\n".join(f"- {c}" for c in comments[:10]) or "(없음)",
    )
    if localize:
        prompt = LOCALIZE_PREFIX + prompt
    if guide and guide.strip():
        prompt = GUIDE_PREFIX.replace("{guide}", guide.strip()) + prompt
    parts = [{"text": prompt}]
    for p in image_paths[:6]:
        try:
            parts.append(_inline_image(p))
        except Exception:
            continue

    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.9,
            "maxOutputTokens": 8192,
            # 생각(thinking)이 토큰을 다 먹어 JSON이 초반에 잘리는 것 방지
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    last_err = None
    for _ in range(2):  # JSON 파싱 실패 시 1회 재시도
        resp = requests.post(GEMINI_URL.format(model=model),
                             params={"key": key}, json=body, timeout=180)
        if resp.status_code != 200:
            raise RuntimeError(f"Gemini API 오류 {resp.status_code}: {resp.text[:300]}")
        try:
            cand = resp.json()["candidates"][0]
            # 응답이 여러 조각(parts)으로 나뉠 수 있어 전부 이어붙임 (첫 조각만 읽으면 잘림)
            raw = "".join(p.get("text", "")
                          for p in cand.get("content", {}).get("parts", []))
            if not raw and cand.get("finishReason") not in (None, "STOP"):
                raise RuntimeError(f"응답 없음(finishReason={cand.get('finishReason')})")
            result = _parse_json(raw)
            if "skip" not in result:
                raise KeyError("skip")
            if result.get("skip"):  # skip 판정이면 hooks/caption 없어도 정상
                result.setdefault("hooks", [])
                result.setdefault("caption", "")
                return result
            for k in ("hooks", "caption"):
                if k not in result:
                    raise KeyError(k)
            if not isinstance(result["hooks"], list) or not result["hooks"]:
                raise ValueError("hooks 비어있음")
            return result
        except Exception as e:
            last_err = e
    raise RuntimeError(f"Gemini 응답 파싱 실패: {last_err}")


def _validate_cards(cards, n):
    """카드 수 보정 + 문단 정규화 ({text, bold})"""
    out = []
    for c in (cards or [])[:n]:
        paras = []
        for p in (c.get("paragraphs") or []):
            t = str(p.get("text", "")).strip()
            if t:
                paras.append({"text": t, "bold": bool(p.get("bold"))})
        out.append({"paragraphs": paras[:5] or [{"text": "", "bold": False}],
                    "ko": str(c.get("ko", "")).strip()})   # 본문 한국어 해석(오역 확인용)
    while len(out) < n:  # 부족하면 빈 카드로 채움 (이미지만 노출)
        out.append({"paragraphs": [{"text": "", "bold": False}], "ko": ""})
    return out


def write_story(cfg, title, text, comments, image_paths, mock=False, localize=False,
                lang="ko", guide=""):
    """스토리카드 캐러셀용: 헤드라인 3안 + 이미지 수만큼 카드 서사 + 캡션.
    lang="ja" 면 일본 타겟 계정용으로 전부 일본어로 생성한다."""
    n = max(1, min(len(image_paths), 8))
    if mock:
        return {"skip": False, "skip_reason": "",
                "headlines": [f"모의 헤드라인 {i + 1}" for i in range(3)],
                "cards": [{"paragraphs": [{"text": f"모의 카드 {i + 1} 본문", "bold": False}]}
                          for i in range(n)],
                "caption": "모의 캡션입니다."}

    key = (cfg.get("gemini_api_key") or "").strip() or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("Gemini API 키가 없습니다. config.json 의 gemini_api_key 를 확인하세요.")
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    prompt = STORY_PROMPT.format(
        n=n, title=title or "(제목 없음)",
        text=(text or "(본문 텍스트 없음 — 이미지가 본문임)")[:1800],
        comments="\n".join(f"- {c}" for c in (comments or [])[:10]) or "(없음)")
    if lang == "ja":            # 일본 타겟 → 일본어로 (한국 현지화보다 우선)
        prompt = JP_STORY_PREFIX + prompt
    elif localize:
        prompt = LOCALIZE_PREFIX + prompt
    if guide and guide.strip():
        prompt = GUIDE_PREFIX.replace("{guide}", guide.strip()) + prompt
    parts = [{"text": prompt}]
    for p in image_paths[:n]:
        try:
            parts.append(_inline_image(p))
        except Exception:
            continue

    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.85,
            "maxOutputTokens": 8192,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    last_err = None
    for _ in range(2):
        resp = requests.post(GEMINI_URL.format(model=model),
                             params={"key": key}, json=body, timeout=180)
        if resp.status_code != 200:
            raise RuntimeError(f"Gemini API 오류 {resp.status_code}: {resp.text[:300]}")
        try:
            cand = resp.json()["candidates"][0]
            raw = "".join(p.get("text", "")
                          for p in cand.get("content", {}).get("parts", []))
            r = _parse_json(raw)
            heads = [str(h).strip() for h in (r.get("headlines") or []) if str(h).strip()]
            if not heads:
                raise KeyError("headlines")
            r["headlines"] = (heads + heads * 3)[:3]
            r["cards"] = _validate_cards(r.get("cards"), n)
            r.setdefault("caption", "")
            r["skip"] = bool(r.get("skip"))
            r.setdefault("skip_reason", "")
            return r
        except Exception as e:
            last_err = e
    raise RuntimeError(f"Gemini 스토리 응답 파싱 실패: {last_err}")


JP_POLISH_PROMPT = """너는 일본어 원어민 카피에디터다. 아래는 일본 인스타그램 '사연·비하인드'
계정에 올릴 카드 문구(headlines/cards/caption)다. 여기서 '번역투·어색한 어순·일본인이
안 쓰는 표현'만 자연스러운 일본 네이티브 표현으로 고쳐라.

## 규칙
- 뜻·내용·구성·bold·카드 수·줄바꿈(\\n)·대략 길이는 그대로 유지. 어색한 표현만 손본다.
- 이미 자연스러운 문장은 절대 바꾸지 마라(불필요한 재작성 금지).
- 한국어 어순 잔재·직역투를 특히 잡아라. '커뮤니티에서 반응 터지는 흥미·몰입 말투'는
  유지·강화하라(딱딱한 설명체로 만들지 마라).
- 인명·고유명사·금액(円 환산 포함)·숫자는 건드리지 마라.
- 출력은 입력과 똑같은 JSON 구조로만.

## 입력
{payload}

## 출력 (이 JSON만)
{{"headlines": ["..."], "cards": [{{"paragraphs": [{{"text": "...", "bold": false}}]}}], "caption": "..."}}
"""


def polish_story_ja(cfg, story, log=print):
    """생성된 일본어 story를 원어민 에디터 관점에서 번역투 교정(2차 패스).
    headlines / cards.paragraphs / caption 만 교체하고 ko(한국어 해석)는 원본 유지.
    실패하면 원본 story 그대로 반환(작업 안 죽음)."""
    try:
        key = (cfg.get("gemini_api_key") or "").strip() or os.environ.get("GEMINI_API_KEY", "")
        if not key:
            return story
        model = cfg.get("gemini_model", "gemini-2.5-flash")
        payload = json.dumps({
            "headlines": story.get("headlines", []),
            "cards": [{"paragraphs": c.get("paragraphs", [])} for c in story.get("cards", [])],
            "caption": story.get("caption", ""),
        }, ensure_ascii=False)
        body = {
            "contents": [{"role": "user",
                          "parts": [{"text": JP_POLISH_PROMPT.format(payload=payload)}]}],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.3,
                "maxOutputTokens": 8192,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        resp = requests.post(GEMINI_URL.format(model=model),
                             params={"key": key}, json=body, timeout=120)
        if resp.status_code != 200:
            log(f"      (일본어 감수 건너뜀: API {resp.status_code})")
            return story
        cand = resp.json()["candidates"][0]
        raw = "".join(p.get("text", "")
                      for p in cand.get("content", {}).get("parts", []))
        r = _parse_json(raw)
        heads = [str(h).strip() for h in (r.get("headlines") or []) if str(h).strip()]
        if len(heads) >= len(story.get("headlines", [])) and heads:
            story["headlines"] = heads[:len(story["headlines"])]
        pol = r.get("cards") or []
        for i, c in enumerate(story.get("cards", [])):   # ko 유지, 문단만 교체(정렬 보존)
            if i < len(pol):
                paras = []
                for p in (pol[i].get("paragraphs") or []):
                    t = str(p.get("text", "")).strip()
                    if t:
                        paras.append({"text": t, "bold": bool(p.get("bold"))})
                if paras:
                    c["paragraphs"] = paras[:5]
        cap = str(r.get("caption", "")).strip()
        if cap:
            story["caption"] = cap
        return story
    except Exception as e:
        log(f"      (일본어 감수 건너뜀, 원본 유지: {e})")
        return story


REHEADLINE_PROMPT = """너는 인스타그램 '사연·비하인드' 스토리카드 채널의 카피라이터다.
아래 스토리의 표지(1번 카드) 위에 얹을 '후킹 헤드라인' 3개를 새로 뽑아라.

## 스토리 내용
{story}

## 톤 (매우 중요)
- 커뮤니티에서 반응 터지는 흥미·몰입 후킹. 궁금증·의외성·반전으로 스크롤을 멈추게 하라.
- 3개 모두 서로 다른 공식(질문형 / 반전·의외형 / 선언·수치형). 각 10~22자.
- 밍밍한 설명체·뉴스체 금지. {ja_note}후킹어(まさかの·実は·なんと 등)·클리프행어를 살려라.
{hint_block}## 출력 (이 JSON만, 다른 텍스트 금지)
{{"headlines": ["후보1", "후보2", "후보3"], "headlines_ko": ["후보1 한국어뜻", "후보2 한국어뜻", "후보3 한국어뜻"]}}
{lang_rule}
"""


def reroll_headlines(cfg, cards, lang="ja", hint="", log=print):
    """스토리 내용 기반으로 표지 헤드라인 3개를 새로 생성(리롤).
    hint(운영자 방향/예시, 한국어)가 있으면 그 감성을 최우선으로. 실패 시 None."""
    try:
        key = (cfg.get("gemini_api_key") or "").strip() or os.environ.get("GEMINI_API_KEY", "")
        if not key:
            return None
        model = cfg.get("gemini_model", "gemini-2.5-flash")
        texts = []
        for c in (cards or [])[:8]:
            for p in (c.get("paragraphs") or []):
                t = str(p.get("text", "")).strip()
                if t:
                    texts.append(t)
        story_txt = " ".join(texts)[:1800] or "(내용 없음)"
        ja = (lang == "ja")
        ja_note = "일본어 " if ja else ""
        lang_rule = ("- headlines는 전부 자연스러운 일본어. headlines_ko는 그 한국어 뜻(개수·순서 동일)."
                     if ja else "- headlines·headlines_ko 모두 한국어(같은 내용).")
        hint_block = ""
        if str(hint).strip():
            hint_block = (
                "## 운영자 방향/예시 (이 감성을 최우선으로 살려라)\n"
                f"{str(hint).strip()}\n"
                "- 이 방향/감성을 3개 모두에 반영하라. 운영자 예시가 완성된 문구면 그 뜻·감성을\n"
                "  살린 버전을 반드시 하나 포함하고 나머지는 변주로.\n\n")
        prompt = REHEADLINE_PROMPT.format(story=story_txt, ja_note=ja_note,
                                          hint_block=hint_block, lang_rule=lang_rule)
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 1.0,   # 리롤이라 다양성 높게
                "maxOutputTokens": 2048,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        resp = requests.post(GEMINI_URL.format(model=model),
                             params={"key": key}, json=body, timeout=90)
        if resp.status_code != 200:
            log(f"      (헤드라인 리롤 실패: API {resp.status_code})")
            return None
        cand = resp.json()["candidates"][0]
        raw = "".join(p.get("text", "")
                      for p in cand.get("content", {}).get("parts", []))
        r = _parse_json(raw)
        heads = [str(h).strip() for h in (r.get("headlines") or []) if str(h).strip()]
        kos = [str(h).strip() for h in (r.get("headlines_ko") or [])]
        if not heads:
            return None
        heads = (heads + heads * 3)[:3]
        kos = (kos + [""] * 3)[:3]
        return {"headlines": heads, "headlines_ko": kos}
    except Exception as e:
        log(f"      (헤드라인 리롤 오류: {str(e)[:60]})")
        return None


_FILES_BASE = "https://generativelanguage.googleapis.com/v1beta"
_FILES_UPLOAD = "https://generativelanguage.googleapis.com/upload/v1beta/files"
_VCAP_PROMPTS = {
    "meme": ("이 영상은 한국 커뮤니티/인스타 릴스다. 영상을 보고 재미있고 흥미로운 한국어 인스타 "
             "캡션을 써라. 첫 줄 강한 후킹 → 짧은 설명/가벼운 드립 → 마무리. 존댓말, 이모지 적당히. "
             "마지막 줄에 관련 해시태그 5~7개. 3~5문장. 영상에 없는 사실은 지어내지 마라."),
    "cardnews": ("이 영상을 보고 정보형 인스타 릴스 캡션을 한국어로 써라. 첫 줄 후킹 → 핵심 포인트 "
                 "2~3개 → 저장·팔로우 유도 마무리. 존댓말, 과한 드립 금지. 해시태그 5~7개. "
                 "영상에 없는 사실은 지어내지 마라."),
    "story_ja": ("この動画を見て、日本語のInstagramリール用キャプションを書いてください。"
                 "最初の一行で強くフック → 簡単な説明 → 軽い締め。自然な日本語で（翻訳調は絶対NG）、"
                 "絵文字は控えめに。最後の行に関連ハッシュタグを5〜7個。動画にない事実は作らないこと。"),
}


def caption_video(cfg, video_path, kind="meme", hint="", log=print):
    """영상을 Gemini가 직접 보고(File API) 인스타 캡션을 생성. 계정 톤별(kind).
    kind='story_ja'면 일본어 캡션. 실패하면 예외를 던진다."""
    key = (cfg.get("gemini_api_key") or "").strip() or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("Gemini API 키가 없습니다 (config.json gemini_api_key)")
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    with open(video_path, "rb") as f:
        data = f.read()
    log("      영상 업로드 중 (Gemini)...")
    r = requests.post(_FILES_UPLOAD, params={"key": key},
                      headers={"X-Goog-Upload-Protocol": "resumable",
                               "X-Goog-Upload-Command": "start",
                               "X-Goog-Upload-Header-Content-Length": str(len(data)),
                               "X-Goog-Upload-Header-Content-Type": "video/mp4",
                               "Content-Type": "application/json"},
                      json={"file": {"display_name": "reel"}}, timeout=60)
    up = r.headers.get("X-Goog-Upload-URL")
    if not up:
        raise RuntimeError(f"영상 업로드 시작 실패 ({r.status_code})")
    r2 = requests.post(up, headers={"X-Goog-Upload-Offset": "0",
                                    "X-Goog-Upload-Command": "upload, finalize"},
                       data=data, timeout=300)
    fobj = r2.json().get("file", {}) if r2.headers.get("content-type", "").startswith("application/json") else {}
    name, uri, state = fobj.get("name"), fobj.get("uri"), fobj.get("state")
    if not name:
        raise RuntimeError(f"영상 업로드 실패: {r2.text[:150]}")
    try:
        log("      Gemini가 영상 보는 중...")
        t0 = time.time()
        while state != "ACTIVE" and time.time() - t0 < 120:
            time.sleep(2)
            j = requests.get(f"{_FILES_BASE}/{name}", params={"key": key}, timeout=30).json()
            state = j.get("state")
            uri = j.get("uri", uri)
            if state == "FAILED":
                raise RuntimeError("영상 처리 실패(FAILED) — 다른 영상으로 시도해주세요")
        if state != "ACTIVE":
            raise RuntimeError("영상 처리 시간 초과")
        prompt = _VCAP_PROMPTS.get(kind, _VCAP_PROMPTS["meme"])
        if str(hint).strip():
            prompt = f"[운영자 방향 — 최우선 반영]: {str(hint).strip()}\n\n" + prompt
        prompt += ('\n\n반드시 아래 JSON만 출력하라. 설명·인사("알겠습니다" 등)·서두·마크다운'
                   '("**", "---")·라벨 절대 금지, 인스타에 그대로 올릴 캡션 본문만 담아라:\n'
                   '{"caption": "캡션 본문 전체(줄바꿈·해시태그 포함)"}')
        gr = requests.post(GEMINI_URL.format(model=model), params={"key": key},
                           json={"contents": [{"parts": [
                               {"file_data": {"mime_type": "video/mp4", "file_uri": uri}},
                               {"text": prompt}]}],
                               "generationConfig": {"temperature": 0.9,
                                                    "maxOutputTokens": 1024,
                                                    "response_mime_type": "application/json",
                                                    "thinkingConfig": {"thinkingBudget": 0}}},
                           timeout=120)
        cand = gr.json()["candidates"][0]
        raw = "".join(p.get("text", "")
                      for p in cand.get("content", {}).get("parts", [])).strip()
        try:
            cap = str(_parse_json(raw).get("caption", "")).strip()
        except Exception:
            cap = raw
        if not cap:
            raise RuntimeError("캡션 생성 실패(빈 응답) — 다시 시도해주세요")
        return cap
    finally:
        try:
            requests.delete(f"{_FILES_BASE}/{name}", params={"key": key}, timeout=20)
        except Exception:
            pass


# ─────────────── 주제 헌터: 스레드(Threads) 2단 분할 카피 ───────────────
_THREAD_PROMPT = """너는 쿠팡 파트너스로 수익을 내는 스레드(Threads) 계정의 전속 카피라이터다.
아래 '소재'(틱톡/유튜브 인기 영상·짤)와 '제품'을 엮어, 스레드에 그대로 올릴 2단 분할 게시물을 써라.
{lang_rule}
## 성공 공식 (이 톤을 그대로 복제)
- 1편(후킹): 제품을 절대 팔지 마라. "와 미친.. 일본에서 난리난..", "나 왜 이제 알았냐"처럼
  발견·충격·자기경험 말투로 소재(영상 내용)를 침 고이게 소개한다. 구어체·반말, 말줄임(;;) 허용.
  맨 끝 줄에 "1/2".
- 2편(전환): 첫 줄에 반드시 제휴 고지("쿠팡 파트너스 활동으로 수수료를 제공받아요." 류)를 넣고,
  이어서 소재를 제품으로 자연스럽게 연결하는 짧은 셀링 2~3줄(집에서 10분·가격 부담 없다 류),
  제품을 콕 집어준다. 맨 끝 줄에 "2/2". (링크는 넣지 마라 — 사람이 직접 붙인다.)

## 규칙
- 이모지 0~2개. 해시태그·실명·전화번호 금지.
- 과장된 확정 의학효과("무조건 낫는다") 금지 — "도움 됨/라인 잡힘" 수준으로.
- 각 편 3~6줄.

## 출력 (반드시 이 JSON만)
{{"title":"소재 한 줄 요약(인박스 표시용)","part1":"1편 본문 전체","part2":"2편 본문 전체"}}

## 제품
{product}

## 소재 (고른 후보)
{sources}
"""


def write_thread(cfg, sources, product="", lang="ko", extra="", mock=False):
    """소재(영상/짤 후보) + 제품 → 스레드 2단 분할(1편 후킹 / 2편 제품).
    반환 {title, part1, part2}. 링크는 넣지 않는다(사람이 붙임)."""
    if mock:
        return {"title": "(모의) 소재 요약",
                "part1": "와 미친.. 이거 일본에서 난리났다는데;;\n나 왜 이제 알았냐\n1/2",
                "part2": "쿠팡 파트너스 활동으로 수수료를 제공받아요.\n집에서 하루 10분이면 라인 싹 바뀜\n2/2"}
    key = (cfg.get("gemini_api_key") or "").strip() or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("Gemini API 키가 없습니다 (config.json gemini_api_key)")
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    lang_rule = ""
    if lang == "ja":
        lang_rule = "\n[언어] 반드시 자연스러운 일본어 SNS 말투로 작성하라. 제휴 고지도 일본어로.\n"
    prompt = _THREAD_PROMPT.format(
        lang_rule=lang_rule,
        product=(product or "(제품 미지정 — 소재에 맞는 제품 카테고리를 제안)").strip()[:400],
        sources=(sources or "(소재 없음)").strip()[:2000],
    )
    if str(extra).strip():
        prompt = f"[운영자 방향 — 최우선]: {str(extra).strip()}\n\n" + prompt
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json",
                                 "temperature": 0.95, "maxOutputTokens": 2048,
                                 "thinkingConfig": {"thinkingBudget": 0}}}
    last_err = None
    for _ in range(2):
        resp = requests.post(GEMINI_URL.format(model=model),
                             params={"key": key}, json=body, timeout=90)
        if resp.status_code != 200:
            raise RuntimeError(f"Gemini API 오류 {resp.status_code}: {resp.text[:200]}")
        try:
            cand = resp.json()["candidates"][0]
            raw = "".join(p.get("text", "")
                          for p in cand.get("content", {}).get("parts", []))
            j = _parse_json(raw)
            p1 = str(j.get("part1", "")).strip()
            p2 = str(j.get("part2", "")).strip()
            if not p1 or not p2:
                raise ValueError("part 비어있음")
            return {"title": str(j.get("title", "")).strip()[:80], "part1": p1, "part2": p2}
        except Exception as e:
            last_err = e
    raise RuntimeError(f"스레드 생성 실패: {last_err}")
