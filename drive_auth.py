# -*- coding: utf-8 -*-
"""구글 드라이브 OAuth 1회 로그인 — 브라우저 동의 → drive_token.json 저장.

사전:
  1) 구글 클라우드 콘솔에서 'OAuth 클라이언트 ID(데스크톱 앱)' 만들고 JSON 다운로드
  2) 그 파일을 이 폴더에 drive_client.json 으로 저장
실행:  python drive_auth.py   → 브라우저가 열리면 사장님 구글 계정으로 로그인·허용
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import drive  # noqa: E402
from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402


def main():
    cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    client = drive.client_path(BASE, cfg)
    if not Path(client).exists():
        print(f"[!] OAuth 클라이언트 파일이 없어요: {client}")
        print("    구글 클라우드에서 'OAuth 클라이언트 ID(데스크톱 앱)'를 만들어")
        print("    JSON을 이 폴더에 drive_client.json 으로 저장하세요.")
        return 1
    flow = InstalledAppFlow.from_client_secrets_file(client, drive.SCOPES)
    creds = flow.run_local_server(port=0)
    Path(drive.token_path(BASE, cfg)).write_text(creds.to_json(), encoding="utf-8")
    print("✅ 드라이브 인증 완료 → drive_token.json 저장됨. 이제 앱에서 업로드가 됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
