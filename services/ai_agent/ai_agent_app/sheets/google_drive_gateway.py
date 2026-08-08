"""SHEET_BACKEND=google: subclasses ExcelSheetGateway, adding
download-before-read/upload-after-write sync of the actual .xlsx file to
Google Drive around the same file-based logic -- still fundamentally
openpyxl-on-a-local-file underneath, just with Drive as the durable copy.
Distinct from google_sheets_api_gateway.py, which talks to the Sheets
API directly with no local file at all.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
import time
import random

from services.ai_agent.ai_agent_app.config import Settings
from services.ai_agent.ai_agent_app.sheets.excel_gateway import ExcelSheetGateway
from services.ai_agent.ai_agent_app.logger import sheet_logger


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _clear_dead_local_proxy() -> None:
    dead_proxy_values = {
        "http://127.0.0.1:9",
        "https://127.0.0.1:9",
        "127.0.0.1:9",
    }
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        value = (os.getenv(key) or "").strip().lower()
        if value in dead_proxy_values:
            os.environ.pop(key, None)


class GoogleDriveWorkbookGateway(ExcelSheetGateway):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.sheet_file_id = settings.google_sheet_id
        creds_candidate = Path(settings.google_application_credentials)
        if not creds_candidate.is_absolute():
            creds_candidate = Path(__file__).resolve().parent.parent.parent / creds_candidate
        self.credentials_path = creds_candidate
        self._drive_service = None

    def workbook_info(self) -> dict[str, str]:
        info = super().workbook_info()
        info["googleSheetId"] = self.sheet_file_id
        return info

    def ensure_runtime_workbook(self, reset: bool = False) -> None:
        with self._lock:
            if reset or not self.runtime_path.exists():
                self._download_from_drive(destination=self.runtime_path)

    def reset_runtime_workbook(self) -> None:
        with self._lock:
            self._download_from_drive(destination=self.runtime_path)

    def preview_customer(
        self,
        full_name: str,
        raw_phone: str,
        trip_type: str | None = None,
        country_code: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            self._download_from_drive(destination=self.runtime_path)
            return super().preview_customer(
                full_name=full_name,
                raw_phone=raw_phone,
                trip_type=trip_type,
                country_code=country_code,
            )

    def run_sales_cycle(
        self,
        *,
        full_name: str,
        raw_phone: str,
        country_code: str,
        trip_type: str | None,
        channel: str,
        source: str,
        agent_notes: str,
        birthday: str = "",
        gender: str = "",
        nationality: str = "",
        preferred_trip_id: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            self._download_from_drive(destination=self.runtime_path)
            result = super().run_sales_cycle(
                full_name=full_name,
                raw_phone=raw_phone,
                country_code=country_code,
                trip_type=trip_type,
                channel=channel,
                source=source,
                agent_notes=agent_notes,
                birthday=birthday,
                gender=gender,
                nationality=nationality,
                preferred_trip_id=preferred_trip_id,
            )
            self._upload_to_drive(source=self.runtime_path)
        return result

    def create_booking(
        self,
        *,
        traveler_id: str,
        traveler_name: str,
        trip_id: str,
        room_type: str,
        channel: str,
        lead_id: str,
        source: str,
        agent_notes: str,
        flight_option: str = "",
        date_option: str = "",
        currency: str = "",
        passport_required: bool = False,
        passport_status: str = "",
        group_size: int | str = 1,
    ) -> dict[str, Any]:
        with self._lock:
            self._download_from_drive(destination=self.runtime_path)
            result = super().create_booking(
                traveler_id=traveler_id,
                traveler_name=traveler_name,
                trip_id=trip_id,
                room_type=room_type,
                channel=channel,
                lead_id=lead_id,
                source=source,
                agent_notes=agent_notes,
                flight_option=flight_option,
                date_option=date_option,
                currency=currency,
                passport_required=passport_required,
                passport_status=passport_status,
                group_size=group_size,
            )
            self._upload_to_drive(source=self.runtime_path)
        return result

    def get_demo_stats(self) -> dict[str, Any]:
        with self._lock:
            self._download_from_drive(destination=self.runtime_path)
            return super().get_demo_stats()

    def crm_preview(self, limit: int = 15) -> list[dict[str, Any]]:
        with self._lock:
            self._download_from_drive(destination=self.runtime_path)
            return super().crm_preview(limit=limit)

    def qualify_lead(self, lead_id: str) -> bool:
        with self._lock:
            self._download_from_drive(destination=self.runtime_path)
            ok = super().qualify_lead(lead_id)
            if ok:
                self._upload_to_drive(source=self.runtime_path)
        return ok

    def _drive(self):
        if self._drive_service is not None:
            return self._drive_service

        _clear_dead_local_proxy()
        scopes = [
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/spreadsheets",
        ]
        credentials = service_account.Credentials.from_service_account_file(
            str(self.credentials_path),
            scopes=scopes,
        )
        self._drive_service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        return self._drive_service

    def _download_from_drive(self, destination: Path) -> None:
        """Download the Drive xlsx file directly to destination.

        All callers hold self._lock (RLock) for the entire download+read
        cycle, so no concurrent thread can access destination — making an
        atomic rename unnecessary and eliminating the Windows WinError 5
        PermissionError caused by antivirus briefly locking temp files.
        """
        sheet_logger.info(f"Downloading workbook from Drive: {self.sheet_file_id}")
        destination.parent.mkdir(parents=True, exist_ok=True)

        max_retries = 3
        for attempt in range(max_retries):
            try:
                # File is an uploaded .xlsx in Drive (rtpof=true in the URL).
                # Use get_media() — export_media() only works for native Google Sheets.
                request = self._drive().files().get_media(
                    fileId=self.sheet_file_id,
                    supportsAllDrives=True,
                )
                # Write directly to destination — no temp file / rename needed.
                # The RLock guarantees exclusive access for this entire download+read
                # block, so WinError 5 (rename over open file) cannot occur.
                with open(destination, "wb") as fh:
                    downloader = MediaIoBaseDownload(fh, request)
                    done = False
                    while not done:
                        _, done = downloader.next_chunk()

                size = destination.stat().st_size
                if size < 100:
                    sheet_logger.error(f"Downloaded file too small ({size} bytes). Possibly corrupted.")
                    destination.unlink(missing_ok=True)
                    continue

                sheet_logger.info(f"Downloaded {size} bytes → {destination.name}")
                return

            except HttpError as exc:
                if exc.resp.status in [429, 500, 502, 503, 504] and attempt < max_retries - 1:
                    wait = (2 ** attempt) + random.random()
                    sheet_logger.warning(f"Drive download failed ({exc.resp.status}), retrying in {wait:.1f}s…")
                    time.sleep(wait)
                    continue
                sheet_logger.error(f"Drive download fatal error: {exc}")
                raise RuntimeError(
                    "Failed to download workbook from Google Drive. "
                    f"Ensure the file is shared with: {self._service_account_email()}."
                ) from exc

    def _upload_to_drive(self, source: Path) -> None:
        sheet_logger.info(f"Uploading workbook to Drive: {self.sheet_file_id}")
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Upload as xlsx; Drive will re-import the data into the native
                # Google Sheet in-place, preserving the file ID and sharing settings.
                media = MediaFileUpload(str(source), mimetype=XLSX_MIME, resumable=True)
                self._drive().files().update(
                    fileId=self.sheet_file_id,
                    media_body=media,
                    supportsAllDrives=True,
                    fields="id,name",
                ).execute()
                return
            except HttpError as exc:
                if exc.resp.status in [429, 500, 502, 503, 504] and attempt < max_retries - 1:
                    wait = (2 ** attempt) + random.random()
                    sheet_logger.warning(f"Drive upload failed (status {exc.resp.status}), retrying in {wait:.1f}s...")
                    time.sleep(wait)
                    continue
                sheet_logger.error(f"Drive upload fatal error: {exc}")
                raise RuntimeError(
                    "Failed to upload workbook to Google Drive. Confirm file permissions for service account: "
                    f"{self._service_account_email()}."
                ) from exc

    def _service_account_email(self) -> str:
        try:
            payload = json.loads(self.credentials_path.read_text(encoding="utf-8"))
            return str(payload.get("client_email", "unknown-service-account"))
        except Exception:
            return "unknown-service-account"
