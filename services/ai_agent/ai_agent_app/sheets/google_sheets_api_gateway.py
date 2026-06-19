from __future__ import annotations

import json
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import gspread
from google.oauth2.service_account import Credentials

from services.ai_agent.ai_agent_app.config import Settings
from services.ai_agent.ai_agent_app.sheets.excel_gateway import ExcelSheetGateway, get_demo_stats_from_wb, crm_preview_from_wb
from services.ai_agent.ai_agent_app.logger import sheet_logger
from services.ai_agent.ai_agent_app.system_bridge import get_system_preview_result, get_system_trip_result
from services.ai_agent.ai_agent_app.sheets.sheets_adapter import FakeWorkbook, SheetRowAdapter
from scripts.phase1_readonly_agent import build_agent_response_from_wb

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",  # for workbook_info only
]

CACHE_TTL = 600  # seconds — 10 minutes cache TTL with in-memory write mutation


@dataclass
class _CachedSheet:
    rows: list[list[str]]
    fetched_at: float = field(default_factory=time.monotonic)


class GoogleSheetsApiGateway(ExcelSheetGateway):
    """
    Live Google Sheets API v4 gateway.
    Reads data directly from the spreadsheet on each request (with TTL cache).
    Writes are appended / updated via the Sheets API — no file ever touches disk.
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.spreadsheet_id = settings.google_sheet_id
        creds_path = Path(settings.google_application_credentials)
        if not creds_path.is_absolute():
            creds_path = Path(__file__).resolve().parent.parent.parent / creds_path

        creds = Credentials.from_service_account_file(str(creds_path), scopes=SCOPES)
        self._gc = gspread.authorize(creds)
        self._spreadsheet = self._gc.open_by_key(self.spreadsheet_id)
        self._cache: dict[str, _CachedSheet] = {}
        self._worksheets: dict[str, gspread.Worksheet] = {}
        self._cache_ttl = CACHE_TTL

    # ── Internal helpers ────────────────────────────────────────────────────

    def _get_ws(self, sheet_name: str) -> gspread.Worksheet:
        if sheet_name not in self._worksheets:
            sheet_logger.debug(f"Resolving worksheet metadata: {sheet_name}")
            self._worksheets[sheet_name] = self._spreadsheet.worksheet(sheet_name)
        return self._worksheets[sheet_name]

    def _fetch_ws(self, sheet_name: str) -> SheetRowAdapter:
        """Return a SheetRowAdapter, using TTL cache and retry on 429 Quota errors."""
        cached = self._cache.get(sheet_name)
        if cached and (time.monotonic() - cached.fetched_at) < self._cache_ttl:
            sheet_logger.debug(f"Cache hit: {sheet_name}")
            return SheetRowAdapter(cached.rows, title=sheet_name)

        sheet_logger.info(f"Fetching live rows: {sheet_name}")
        ws = self._get_ws(sheet_name)
        
        # Robust retry with exponential backoff for Google API 429 errors
        retries = 3
        delay = 2.0
        for attempt in range(retries):
            try:
                rows = ws.get_all_values()
                break
            except gspread.exceptions.APIError as e:
                if attempt == retries - 1 or "429" not in str(e):
                    raise
                sheet_logger.warning(f"Google Sheets 429 Quota error on '{sheet_name}'. Retrying in {delay}s...")
                time.sleep(delay)
                delay *= 2.0

        self._cache[sheet_name] = _CachedSheet(rows=rows)
        return SheetRowAdapter(rows, title=sheet_name)

    def _invalidate(self, *sheet_names: str) -> None:
        for name in sheet_names:
            self._cache.pop(name, None)

    def _make_wb(self, *sheet_names: str) -> FakeWorkbook:
        return FakeWorkbook({name: self._fetch_ws(name) for name in sheet_names})

    # ── Write helpers ───────────────────────────────────────────────────────

    def _append_row(self, sheet_name: str, values: list) -> int:
        """
        Append a row to Google Sheets and update local cache in-memory.
        """
        ws = self._get_ws(sheet_name)
        
        # Robust retry with exponential backoff for Google API 429 errors
        retries = 5
        delay = 2.0
        for attempt in range(retries):
            try:
                result = ws.append_row(values, value_input_option="USER_ENTERED")
                break
            except gspread.exceptions.APIError as e:
                if attempt == retries - 1 or "429" not in str(e):
                    raise
                sheet_logger.warning(f"Google Sheets 429 Quota error on append '{sheet_name}'. Retrying in {delay}s...")
                time.sleep(delay)
                delay *= 2.0
                
        updated_range = result["updates"]["updatedRange"]
        row_num = int(updated_range.split("!")[1].split(":")[0].lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
        
        # Update cache in-place rather than invalidating to prevent 429 read errors
        if sheet_name in self._cache:
            str_vals = [str(v) if v is not None else "" for v in values]
            self._cache[sheet_name].rows.append(str_vals)
            
        return row_num

    def _update_row(self, sheet_name: str, row: int, col_values: dict[int, Any]) -> None:
        """
        Update specific columns in an existing row (col is 1-indexed) and update local cache.
        """
        ws = self._get_ws(sheet_name)
        cell_list = [gspread.Cell(row, col, val) for col, val in col_values.items()]
        
        # Robust retry with exponential backoff for Google API 429 errors
        retries = 5
        delay = 2.0
        for attempt in range(retries):
            try:
                ws.update_cells(cell_list, value_input_option="USER_ENTERED")
                break
            except gspread.exceptions.APIError as e:
                if attempt == retries - 1 or "429" not in str(e):
                    raise
                sheet_logger.warning(f"Google Sheets 429 Quota error on update '{sheet_name}'. Retrying in {delay}s...")
                time.sleep(delay)
                delay *= 2.0
        
        # Update cache in-place rather than invalidating to prevent 429 read errors
        if sheet_name in self._cache:
            cached_rows = self._cache[sheet_name].rows
            while len(cached_rows) < row:
                cached_rows.append([])
            for col, val in col_values.items():
                while len(cached_rows[row - 1]) < col:
                    cached_rows[row - 1].append("")
                cached_rows[row - 1][col - 1] = str(val) if val is not None else ""

    def _find_row_by_key(self, sheet_name: str, key_col: int, key_value: str) -> int | None:
        """
        Search for a row where column key_col == key_value.
        Returns 1-based row index or None.
        """
        ws_adapter = self._fetch_ws(sheet_name)
        for row_idx in range(1, ws_adapter.max_row + 1):
            if str(ws_adapter.cell(row_idx, key_col).value or "") == key_value:
                return row_idx
        return None

    # ── Gateway interface ───────────────────────────────────────────────────

    def workbook_info(self) -> dict[str, str]:
        return {
            "sourceWorkbook": "Google Sheets (live)",
            "runtimeWorkbook": "Google Sheets (live)",
            "googleSheetId": self.spreadsheet_id,
        }

    def ensure_runtime_workbook(self, reset: bool = False) -> None:
        if reset:
            self._cache.clear()

    def reset_runtime_workbook(self) -> None:
        self._cache.clear()
        sheet_logger.info("Sheets cache cleared (reset).")

    def preview_customer(
        self,
        full_name: str,
        raw_phone: str,
        trip_type: str | None = None,
        country_code: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            system_preview = get_system_preview_result(full_name, raw_phone, trip_type, country_code, self.settings)
            if system_preview is not None:
                return system_preview
            wb = self._make_wb("Travelers", "Trips")
            trip_result_override = get_system_trip_result(trip_type, self.settings)
            return build_agent_response_from_wb(
                wb=wb,
                full_name=full_name,
                raw_phone=raw_phone,
                trip_type=trip_type,
                country_code=country_code,
                trip_result_override=trip_result_override,
            )

    def get_demo_stats(self) -> dict[str, Any]:
        with self._lock:
            wb = self._make_wb("Travelers", "Interactions", "Leads", "Trips")
            return get_demo_stats_from_wb(wb)

    def crm_preview(self, limit: int = 15) -> list[dict[str, Any]]:
        with self._lock:
            wb = self._make_wb("Travelers")
            return crm_preview_from_wb(wb, limit=limit)

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
            self._invalidate("Travelers", "Interactions", "Leads", "Booking Event Trail")
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
    ) -> dict[str, Any]:
        with self._lock:
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
            )
            self._invalidate("Trips", "Trip Bookings", "Interactions", "Leads", "Booking Event Trail")
            return result
