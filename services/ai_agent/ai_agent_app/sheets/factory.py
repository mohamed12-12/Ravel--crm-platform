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
