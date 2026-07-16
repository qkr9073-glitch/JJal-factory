# -*- coding: utf-8 -*-
"""구글 드라이브 업로드 (OAuth 방식 — 개인 구글 계정용).

개인 '내 드라이브'는 서비스 계정으로 업로드 불가(용량 없음) → 사장님 계정 OAuth 1회 로그인.
세팅:
  1) config drive_client (기본 drive_client.json) = OAuth 데스크톱 클라이언트 비밀 JSON
  2) `python drive_auth.py` 1회 실행 → 브라우저 동의 → drive_token.json 생성
  3) config drive_folder_id = 업로드할 폴더 ID
전자책 PDF → 그 폴더에 업로드 → '링크 있는 사람 보기' 공유 링크 반환.
"""
import os

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _p(base_dir, cfg, key, default):
    v = cfg.get(key) or default
    return v if os.path.isabs(v) else os.path.join(str(base_dir), v)


def token_path(base_dir, cfg):
    return _p(base_dir, cfg, "drive_token", "drive_token.json")


def client_path(base_dir, cfg):
    return _p(base_dir, cfg, "drive_client", "drive_client.json")


def is_configured(cfg, base_dir):
    return os.path.exists(token_path(base_dir, cfg))


def _creds(cfg, base_dir):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    tp = token_path(base_dir, cfg)
    if not os.path.exists(tp):
        raise RuntimeError("드라이브 인증이 안 됐어요 — `python drive_auth.py` 로 1회 로그인하세요")
    creds = Credentials.from_authorized_user_file(tp, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(tp, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    return creds


def _service(cfg, base_dir):
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=_creds(cfg, base_dir), cache_discovery=False)


def upload_pdf(cfg, base_dir, path, name=None, log=print):
    """PDF를 drive_folder_id 폴더에 업로드 + '링크 있는 사람 보기' 공유.
    반환: {"link": 공유링크, "file_id": id}. 실패 시 예외."""
    from googleapiclient.http import MediaFileUpload
    if not os.path.exists(path):
        raise RuntimeError("업로드할 파일이 없습니다")
    folder = (cfg.get("drive_folder_id") or "").strip()
    svc = _service(cfg, base_dir)
    name = name or os.path.basename(path)
    meta = {"name": name}
    if folder:
        meta["parents"] = [folder]
    media = MediaFileUpload(path, mimetype="application/pdf", resumable=False)
    log(f"      드라이브 업로드 중: {name}")
    f = svc.files().create(body=meta, media_body=media, fields="id,webViewLink").execute()
    fid = f["id"]
    try:
        svc.permissions().create(fileId=fid,
                                 body={"role": "reader", "type": "anyone"}).execute()
    except Exception as e:
        log(f"      (공유 권한 설정 실패: {e})")
    link = f.get("webViewLink") or f"https://drive.google.com/file/d/{fid}/view"
    log(f"      ✅ 업로드 완료: {link}")
    return {"link": link, "file_id": fid}
