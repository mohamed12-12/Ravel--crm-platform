from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def resolve_creds_path(raw: str) -> Path:
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate


def extract_sheet_id(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    marker = "/spreadsheets/d/"
    if marker in text:
        tail = text.split(marker, 1)[1]
        return tail.split("/", 1)[0].strip()
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Google service account key and optional sheet access.")
    parser.add_argument("--sheet-id", default="", help="Google Sheet file ID to test access against.")
    args = parser.parse_args()

    load_env(PROJECT_ROOT / ".env")

    raw_creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if not raw_creds:
        raise SystemExit("GOOGLE_APPLICATION_CREDENTIALS is empty in .env")

    creds_path = resolve_creds_path(raw_creds)
    if not creds_path.exists():
        raise SystemExit(f"Credentials file not found: {creds_path}")

    payload = json.loads(creds_path.read_text(encoding="utf-8"))
    client_email = str(payload.get("client_email", "")).strip()
    project_id = str(payload.get("project_id", "")).strip()
    private_key = str(payload.get("private_key", "")).strip()

    if not client_email or not project_id or not private_key:
        raise SystemExit("Credential JSON is missing required fields (client_email/project_id/private_key).")

    print("credentials_valid=true")
    print(f"project_id={project_id}")
    print(f"service_account_email={client_email}")

    target_sheet_id = extract_sheet_id(args.sheet_id.strip() or os.getenv("GOOGLE_SHEET_ID", "").strip())
    if not target_sheet_id:
        print("sheet_access_checked=false")
        print("note=Set GOOGLE_SHEET_ID or pass --sheet-id to validate sheet access.")
        return

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except Exception:
        raise SystemExit("Missing dependencies. Install with: pip install google-api-python-client google-auth")

    # Ignore dead local proxy defaults that break token exchange in some setups.
    dead_proxy = {"http://127.0.0.1:9", "https://127.0.0.1:9", "127.0.0.1:9"}
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        value = (os.getenv(key) or "").strip().lower()
        if value in dead_proxy:
            os.environ.pop(key, None)

    drive_scopes = ["https://www.googleapis.com/auth/drive"]
    drive_credentials = service_account.Credentials.from_service_account_file(
        str(creds_path),
        scopes=drive_scopes,
    )

    drive = build("drive", "v3", credentials=drive_credentials, cache_discovery=False)
    try:
        file_meta = (
            drive.files()
            .get(fileId=target_sheet_id, fields="id,name,mimeType,owners(displayName,emailAddress)", supportsAllDrives=True)
            .execute()
        )
    except Exception as exc:
        raise SystemExit(
            "Google Drive access failed for this file ID. Share the file with the service account and verify file ID. "
            f"Error: {exc}"
        )
    mime_type = str(file_meta.get("mimeType", ""))
    print("sheet_access_checked=true")
    print(f"sheet_id={target_sheet_id}")
    print(f"file_name={str(file_meta.get('name', ''))}")
    print(f"file_mime_type={mime_type}")

    if mime_type == "application/vnd.google-apps.spreadsheet":
        sheets_scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
        sheets_credentials = service_account.Credentials.from_service_account_file(
            str(creds_path),
            scopes=sheets_scopes,
        )
        sheets = build("sheets", "v4", credentials=sheets_credentials, cache_discovery=False)
        metadata = sheets.spreadsheets().get(spreadsheetId=target_sheet_id).execute()
        title = str(metadata.get("properties", {}).get("title", ""))
        tabs = metadata.get("sheets", [])
        print("integration_mode=google_sheets_native")
        print(f"sheet_title={title}")
        print(f"sheet_tab_count={len(tabs)}")
    elif mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        print("integration_mode=drive_xlsx_sync")
        print("note=This file is Office format. Use Drive download/upload sync instead of Sheets cell API.")
    else:
        print("integration_mode=unknown")
        print("note=Unexpected file MIME type. Manual review recommended.")


if __name__ == "__main__":
    main()
