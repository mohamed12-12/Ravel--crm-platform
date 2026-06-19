from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from services.ai_agent.ai_agent_app.config import Settings


REPO_ROOT = Path(__file__).resolve().parents[3]
SYSTEM_ROOT = REPO_ROOT / "apps" / "api"
if str(SYSTEM_ROOT) not in sys.path:
    sys.path.insert(0, str(SYSTEM_ROOT))

try:
    from services.crm.system_services.config import SystemServiceSettings
    from services.crm.system_services import UnifiedCRMService
except Exception:  # pragma: no cover - defensive fallback for environments missing the system package
    SystemServiceSettings = None  # type: ignore[assignment]
    UnifiedCRMService = None  # type: ignore[assignment]


def _system_db_path() -> Path:
    import os

    raw = os.getenv("RAHMA_SYSTEM_DB_PATH", "").strip()
    if raw:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = REPO_ROOT / candidate
        return candidate
    return SYSTEM_ROOT / "instance" / "rahma_traveler_dev.db"


def _google_credentials_path(settings: Settings) -> Path | None:
    if not settings.google_application_credentials:
        return None
    candidate = Path(settings.google_application_credentials)
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
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
        repo_root=REPO_ROOT,
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


def get_system_preview_result(
    full_name: str,
    raw_phone: str,
    trip_type: str | None,
    country_code: str = "",
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    """Resolve traveler identity and trip suggestions directly from the CRM database."""
    service = get_system_service(settings)
    if service is None:
        return None
    try:
        resolution = service.resolve_identity(full_name, raw_phone, country_code)
        trip_result = service.build_trip_result(trip_type) if trip_type else {"open_trips": [], "date_tbd_trips": []}
        traveler = resolution.traveler
        result: dict[str, Any] = {
            "customer_name": full_name,
            "lookup_phone": resolution.lookup_phone,
            "match_status": resolution.match_status,
            "name_match_status": resolution.name_match_status,
            "traveler": traveler,
            "handoff_required": resolution.handoff_required,
            "handoff_reason": resolution.handoff_reason,
            "actions": list(resolution.actions),
            "trip_result": None,
        }
        if trip_type and not resolution.handoff_required:
            result["trip_result"] = trip_result
            if trip_result["open_trips"]:
                result["actions"].append("show_open_trips")
            elif trip_result["date_tbd_trips"]:
                result["actions"].append("offer_date_tbd_follow_up")
            else:
                result["actions"].append("no_trip_available")
        return result
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


def check_traveler_completed_trips(settings: Settings, traveler_id: str) -> bool:
    """Return True if the traveler has at least one past booking (post-trip handoff check)."""
    service = get_system_service(settings)
    if service is None:
        return False
    try:
        return service.traveler_has_completed_trips(traveler_id)
    except Exception:
        return False
