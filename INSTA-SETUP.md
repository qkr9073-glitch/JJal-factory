# 인스타 자동 업로드 연동 (최초 1회, 약 10분)

완성팩의 **[📤 인스타 자동 업로드]** 버튼을 살리는 방법.
Meta **공식 API**(Instagram Login 방식)라 계정 정지 걱정 없는 정상 루트입니다.

## 준비물
- 인스타 계정이 **프로페셔널(크리에이터/비즈니스)** 이어야 함 (설정 → 계정 유형 전환)
- 페이스북 페이지는 **필요 없음** (신형 Instagram Login 방식)

## 1) Meta 개발자 앱 만들기
1. https://developers.facebook.com → 로그인 → **My Apps → Create App**
2. 앱 이름: 아무거나 (예: `jjal-factory`) → 사용 사례에서 **"Instagram"** 선택 → 앱 생성
3. 왼쪽 메뉴 **Instagram → API setup with Instagram login** 클릭

## 2) 인스타 계정 연결 + 토큰 발급
1. **"Generate access tokens"** 섹션 → **Add account** → 올릴 인스타 계정으로 로그인/승인
2. 계정이 목록에 뜨면 **Generate token** 클릭 → 나오는 **긴 토큰 문자열 복사**
   (이게 60일짜리 장기 토큰. 발행 시 서버가 7일마다 자동 연장해줌)
3. 같은 화면에 나오는 **Instagram user ID** (숫자) 복사

## 3) config.json에 붙여넣기
`E:\짤공장 (게시물자동화)\config.json` 에서:
```json
"ig_user_id": "1784...(숫자)",
"ig_access_token": "IGAA...(긴 문자열)",
```
저장 → `RESTART-SERVER.cmd` 더블클릭.

## 4) 테스트
1. 웹 접속 → 📦 결과물 보기 → 아무 팩의 **📤** 버튼
2. 1~2분 안에 "✅ 업로드 완료"가 뜨고 게시물 링크가 열리면 성공

## 동작 원리 / 주의
- 이미지가 공개 URL(`https://jjal.traffic-charger.com/packs/...`)로 서빙되는 걸
  Meta 서버가 가져가서 게시 → **PC와 터널이 켜져 있어야 업로드 가능**
- 캐러셀 최대 10장 (넘으면 앞 10장만), 캡션 2,200자 한도 (자동 컷)
- 한도: 계정당 24시간에 100개 게시 — 넘칠 일 없음
- 중복 방지: 한 번 올린 팩은 published.json에 기록 → 재업로드 시 확인창
- 토큰이 완전히 만료(60일+)됐으면 2)번만 다시 하면 됨
