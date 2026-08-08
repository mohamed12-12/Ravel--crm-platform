"""Picks which of the 3 sheet-backend implementations (SHEET_BACKEND env
var) this app instance actually uses: local-file Excel (excel_gateway.py),
the same workbook synced through Google Drive (google_drive_gateway.py,
a subclass of the Excel one), or the Google Sheets API directly
(google_sheets_api_gateway.py, no local file at all -- see
sheets_adapter.py for how it fakes an openpyxl-shaped interface so the
rest of the code doesn't need to know which backend is active).
"""
from __future__ import annotations

from services.ai_agent.ai_agent_app.config import Settings
from services.ai_agent.ai_agent_app.sheets.excel_gateway import ExcelSheetGateway
from services.ai_agent.ai_agent_app.sheets.google_drive_gateway import GoogleDriveWorkbookGateway


def build_sheet_gateway(settings: Settings) -> ExcelSheetGateway:
    if settings.sheet_backend == "excel":
        return ExcelSheetGateway(settings)
    if settings.sheet_backend == "google":
        return GoogleDriveWorkbookGateway(settings)
    if settings.sheet_backend == "google_sheets":
        from services.ai_agent.ai_agent_app.sheets.google_sheets_api_gateway import GoogleSheetsApiGateway
        return GoogleSheetsApiGateway(settings)
    raise ValueError(f"Unsupported sheet backend: {settings.sheet_backend}")
