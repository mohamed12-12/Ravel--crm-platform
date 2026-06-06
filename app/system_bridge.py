from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from app.config import Settings


SYSTEM_ROOT = Path(__file__).resolve().parent.parent / "rahma-traveler"
if str(SYSTEM_ROOT) not in sys.path:
    sys.path.insert(0, str(SYSTEM_ROOT))

try:
    from system_services.config import SystemServiceSettings
    from system_services import UnifiedCRMService
except Exception:  # pragma: no cover - defensive fallback for environments missing the system package
    SystemServiceSettings = None  # type: ignore[assignment]
    UnifiedCRMService = None  # type: ignore[assignment]


def _system_db_path() -> Path:
    import os

    raw = os.getenv("RAHMA_SYSTEM_DB_PATH", "").strip()
    if raw:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = Path(__file__).resolve().parent.parent / candidate
        return candidate
    return SYSTEM_ROOT / "instance" / "rahma_traveler_dev.db"


def _google_credentials_path(settings: Settings) -> Path | None:
    if not settings.google_application_credentials:
        return None
    candidate = Path(settings.google_application_credentials)
    if not candidate.is_absolute():
        candidate = Path(__file__).resolve().parent.parent / candidate
    return candidate


def _service_settings(settings: Settings | None = None):
    if SystemServiceSettings is None:
        return None
    if settings is None:
        return None

    # The Drive-xlsx gateway downloads to the runtime workbook and uploads after
    # the superclass returns, so system sync should edit the local workbook.
    sheet_backend = "excel" if settings.sheet_backend == "google" else settings.sheet_backend
    return SystemServiceSettings(
        repo_root=Path(__file__).resolve().parent.parent,
        system_root=SYSTEM_ROOT,
        db_path=_system_db_path(),
        sheet_backend=sheet_backend,
        excel_source_workbook=settings.excel_source_workbook,
        excel_runtime_workbook=settings.excel_runtime_workbook,
        google_sheet_id=settings.google_sheet_id,
        google_application_credentials=_google_credentials_path(settings),
    )


def get_system_service(settings: Settings | None = None):
    if UnifiedCRMService is None:
        return None
    service_settings = _service_settings(settings)
    if service_settings is None:
        return UnifiedCRMService()
    return UnifiedCRMService(service_settings)


def get_system_trip_result(
    trip_type: str | None,
    settings: Settings | None = None,
) -> dict[str, list[dict[str, Any]]] | None:
    if UnifiedCRMService is None or not trip_type:
        return None
    try:
        service = get_system_service(settings)
        if service is None:
            return None
        return service.build_trip_result(trip_type)
    except Exception:
        return None


def run_system_sales_cycle(settings: Settings, **payload: Any) -> dict[str, Any]:
    service = get_system_service(settings)
    if service is None:
        raise RuntimeError("System DB write-through is unavailable; refusing sheet-only agent outcome write.")
    return service.record_agent_outcome(**payload)


def create_system_booking(settings: Settings, **payload: Any) -> dict[str, Any]:
    service = get_system_service(settings)
    if service is None:
        raise RuntimeError("System DB write-through is unavailable; refusing sheet-only booking write.")
    return service.create_booking_draft(**payload)


def qualify_system_lead(settings: Settings, lead_id: str) -> bool:
    service = get_system_service(settings)
    if service is None:
        raise RuntimeError("System DB write-through is unavailable; refusing sheet-only lead qualification.")
    return service.qualify_lead(lead_id)
