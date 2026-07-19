from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from flask import current_app
from sqlalchemy import or_

from app.extensions import db
from app.models import (
    CEBooking,
    CommunityEvent,
    DMCopyLibrary,
    Interaction,
    LanguageTemplate,
    Lead,
    Traveler,
    Trip,
    TripBooking,
)
from services.data_authority import load_data_authority
from services.crm.system_services.phone_normalization import normalize_phone_input


logger = logging.getLogger(__name__)

IMPORT_CONFIG = (
    {"sheet": "Language Templates", "model": LanguageTemplate, "pk_db": "template_key", "pk_excel": "Template Key", "header": 0},
    {"sheet": "DM Copy Library", "model": DMCopyLibrary, "pk_db": "message_key", "pk_excel": "Message Key", "header": 0},
    {"sheet": "Trips", "model": Trip, "pk_db": "trip_id", "pk_excel": "Trip ID", "header": 1},
    {"sheet": "Community Events", "model": CommunityEvent, "pk_db": "event_id", "pk_excel": "Event ID", "header": 0},
    {"sheet": "Travelers", "model": Traveler, "pk_db": "traveler_id", "pk_excel": "Traveler ID", "header": 0},
    {"sheet": "Leads", "model": Lead, "pk_db": "lead_id", "pk_excel": "Lead ID", "header": 0},
    {"sheet": "Interactions", "model": Interaction, "pk_db": "interaction_id", "pk_excel": "Interaction ID", "header": 0},
    {"sheet": "Trip Bookings", "model": TripBooking, "pk_db": "booking_id", "pk_excel": "Booking ID", "header": 1},
    {"sheet": "CE Bookings", "model": CEBooking, "pk_db": "booking_id", "pk_excel": "Booking ID", "header": 0},
)


class ImportPolicyError(RuntimeError):
    pass


def run_full_import(
    file_path: str | Path,
    *,
    apply: bool = False,
    approved_by: str = "",
) -> dict[str, dict[str, Any]]:
    """Classify an Excel workbook; applying new rows requires explicit development approval."""
    authority = load_data_authority()
    if not authority.import_enabled_for("excel"):
        raise ImportPolicyError("Excel imports are disabled by EXCEL_IMPORT_ENABLED.")
    path = Path(file_path)
    workbook = pd.ExcelFile(path)
    return _execute_import(
        lambda sheet, header: pd.read_excel(workbook, sheet_name=sheet, header=header),
        source_type="excel",
        source_label=path.name,
        apply=apply,
        approved_by=approved_by,
    )


def run_sheets_import(
    sheet_id: str,
    credentials_path: str,
    *,
    apply: bool = False,
    approved_by: str = "",
) -> dict[str, dict[str, Any]]:
    """Classify Google Sheet rows; Google Sheets is never an operational authority."""
    authority = load_data_authority()
    if not authority.import_enabled_for("google_sheets"):
        raise ImportPolicyError("Google Sheets imports are disabled by GOOGLE_SHEETS_IMPORT_ENABLED.")
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError as exc:
        raise ImportPolicyError("gspread and google-auth are required for Google Sheets preview.") from exc

    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file(credentials_path, scopes=scopes)
    sheet = gspread.authorize(creds).open_by_key(sheet_id)

    def get_sheet_df(sheet_name: str, header_idx: int) -> pd.DataFrame:
        worksheet = sheet.worksheet(sheet_name)
        values = worksheet.get_all_values()
        if not values:
            return pd.DataFrame()
        return pd.DataFrame(values[header_idx + 1 :], columns=values[header_idx])

    return _execute_import(
        get_sheet_df,
        source_type="google_sheets",
        source_label=sheet_id,
        apply=apply,
        approved_by=approved_by,
    )


def _normalize_frame(sheet_name: str, frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    frame = frame.copy()
    if sheet_name == "Trips" and len(frame.columns) >= 13:
        columns = list(frame.columns)
        columns[7:13] = [
            "Single Total",
            "Double Total",
            "Triple Total",
            "Single Remaining",
            "Double Remaining",
            "Triple Remaining",
        ]
        frame.columns = columns
    frame = frame.loc[:, ~frame.columns.duplicated()]
    frame.columns = [str(column).strip() for column in frame.columns]
    frame = frame.where(pd.notnull(frame), None)
    return frame.replace("", None)


def _content_fingerprint(frames: dict[str, pd.DataFrame]) -> str:
    digest = hashlib.sha256()
    for sheet_name in sorted(frames):
        digest.update(sheet_name.encode("utf-8"))
        payload = frames[sheet_name].to_json(orient="split", date_format="iso", default_handler=str)
        digest.update(payload.encode("utf-8"))
    return digest.hexdigest()


def _staging_path(fingerprint: str) -> Path:
    root = Path(current_app.instance_path) / "import-staging"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{fingerprint}.json"


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _equivalent(left: Any, right: Any) -> bool:
    if left is None and right in (None, ""):
        return True
    if right is None and left in (None, ""):
        return True
    return _json_value(left) == _json_value(right)


def _record_key(model: type, instance: Any, pk_db: str) -> tuple[str, str] | str:
    if model is LanguageTemplate:
        return (str(instance.template_key or "").strip(), str(instance.language or "").strip())
    return str(getattr(instance, pk_db, "") or "").strip()


def _existing_records(model: type, frame: pd.DataFrame, pk_db: str, pk_excel: str) -> dict[Any, Any]:
    if model is LanguageTemplate:
        keys = {
            (str(row.get("Template Key") or "").strip(), str(row.get("Language") or "").strip())
            for _, row in frame.iterrows()
            if row.get("Template Key") and row.get("Language")
        }
        if not keys:
            return {}
        clauses = [db.and_(model.template_key == key, model.language == language) for key, language in keys]
        records = db.session.query(model).filter(or_(*clauses)).all()
        return {(str(record.template_key), str(record.language)): record for record in records}

    keys = {str(value).strip() for value in frame.get(pk_excel, pd.Series(dtype=object)).tolist() if value}
    if not keys:
        return {}
    records = db.session.query(model).filter(getattr(model, pk_db).in_(keys)).all()
    return {str(getattr(record, pk_db)): record for record in records}


def _instance_data(model: type, instance: Any) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for column in model.__table__.columns:
        value = getattr(instance, column.name)
        if value is not None:
            data[column.name] = value
    if model is LanguageTemplate:
        data.pop("id", None)
    return data


def _empty_result() -> dict[str, Any]:
    return {
        "new": 0,
        "update": 0,
        "unchanged": 0,
        "conflict": 0,
        "invalid": 0,
        "duplicate": 0,
        "inserted": 0,
        "samples": [],
    }


def _traveler_phone_key(traveler: Any) -> str:
    explicit = str(
        getattr(traveler, "normalized_whatsapp", "")
        or getattr(traveler, "integrated_whatsapp", "")
        or ""
    ).strip()
    if explicit:
        return "".join(character for character in explicit if character.isdigit())
    raw_phone = str(getattr(traveler, "whatsapp_raw", "") or "").strip()
    country_code = str(getattr(traveler, "phone_code", "") or "").strip().lstrip("+")
    if not raw_phone:
        return ""
    normalized = normalize_phone_input(
        raw_phone,
        country_code,
        default_country_is_explicit=bool(country_code),
    )
    return "".join(character for character in normalized.normalized_e164 if character.isdigit())


def _execute_import(
    df_loader_func: Callable[[str, int], pd.DataFrame],
    *,
    source_type: str,
    source_label: str,
    apply: bool,
    approved_by: str,
) -> dict[str, dict[str, Any]]:
    """Persist a preview report and optionally insert only approved, non-conflicting rows."""
    frames: dict[str, pd.DataFrame] = {}
    load_errors: dict[str, str] = {}
    for config in IMPORT_CONFIG:
        sheet_name = config["sheet"]
        try:
            frames[sheet_name] = _normalize_frame(
                sheet_name,
                df_loader_func(sheet_name, config["header"]),
            )
        except Exception as exc:
            logger.warning("Could not stage import sheet %s: %s", sheet_name, exc)
            frames[sheet_name] = pd.DataFrame()
            load_errors[sheet_name] = str(exc)

    fingerprint = _content_fingerprint(frames)
    stage_path = _staging_path(fingerprint)
    authority = load_data_authority()
    if apply:
        if not authority.direct_import_apply_enabled:
            raise ImportPolicyError("Direct import apply is disabled; enable it only for an approved development migration.")
        if not approved_by.strip():
            raise ImportPolicyError("approved_by is required for import apply.")
        if not stage_path.exists():
            raise ImportPolicyError("This exact source must be previewed before apply.")

    results: dict[str, dict[str, Any]] = {
        "_meta": {
            "mode": "apply" if apply else "preview",
            "source_type": source_type,
            "source_label": source_label,
            "fingerprint": fingerprint,
            "schema_version": authority.schema_version,
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "approved_by": approved_by.strip(),
            "authority": "crm",
        }
    }

    try:
        traveler_phone_owners: dict[str, str] = {}
        for traveler in db.session.query(Traveler).all():
            phone_key = _traveler_phone_key(traveler)
            if phone_key:
                traveler_phone_owners.setdefault(phone_key, str(traveler.traveler_id))

        for config in IMPORT_CONFIG:
            sheet_name = config["sheet"]
            model = config["model"]
            pk_db = config["pk_db"]
            frame = frames[sheet_name]
            outcome = _empty_result()
            if sheet_name in load_errors:
                outcome["invalid"] = 1
                outcome["samples"].append({"classification": "load_error", "reason": load_errors[sheet_name]})
                results[sheet_name] = outcome
                continue
            if frame.empty:
                results[sheet_name] = outcome
                continue

            existing = _existing_records(model, frame, pk_db, config["pk_excel"])
            seen: set[Any] = set()
            for row_number, (_, row) in enumerate(frame.iterrows(), start=config["header"] + 2):
                try:
                    instance = model.from_excel_row(row)
                    key = _record_key(model, instance, pk_db)
                    missing_key = not all(key) if isinstance(key, tuple) else not key
                    phone_key = _traveler_phone_key(instance) if model is Traveler else ""
                    phone_owner = traveler_phone_owners.get(phone_key, "") if phone_key else ""
                    if missing_key:
                        outcome["invalid"] += 1
                        classification = {"row": row_number, "classification": "invalid", "reason": "missing stable primary key"}
                    elif model is Traveler and phone_owner and phone_owner != key:
                        seen.add(key)
                        outcome["duplicate"] += 1
                        classification = {
                            "row": row_number,
                            "key": str(key),
                            "classification": "duplicate",
                            "reason": "phone is already owned by a different CRM traveler ID",
                            "crm_traveler_id": phone_owner,
                        }
                    elif key in seen:
                        outcome["duplicate"] += 1
                        classification = {"row": row_number, "key": str(key), "classification": "duplicate", "reason": "duplicate key in source"}
                    elif key in existing:
                        seen.add(key)
                        data = _instance_data(model, instance)
                        protected = {pk_db}
                        if model is LanguageTemplate:
                            protected.update({"template_key", "language"})
                        changed_fields = [
                            name
                            for name, value in data.items()
                            if name not in protected and not _equivalent(getattr(existing[key], name), value)
                        ]
                        if changed_fields:
                            outcome["conflict"] += 1
                            classification = {
                                "row": row_number,
                                "key": str(key),
                                "classification": "conflict",
                                "reason": "CRM value differs; spreadsheet overwrite blocked",
                                "fields": changed_fields,
                            }
                        else:
                            outcome["unchanged"] += 1
                            classification = {"row": row_number, "key": str(key), "classification": "unchanged"}
                    else:
                        seen.add(key)
                        outcome["new"] += 1
                        classification = {"row": row_number, "key": str(key), "classification": "new"}
                        if model is Traveler and phone_key:
                            traveler_phone_owners[phone_key] = str(key)
                        if apply:
                            db.session.add(instance)
                            db.session.flush()
                            existing[key] = instance
                            outcome["inserted"] += 1
                    if len(outcome["samples"]) < 50 and classification["classification"] != "unchanged":
                        outcome["samples"].append(classification)
                except Exception as exc:
                    outcome["invalid"] += 1
                    if len(outcome["samples"]) < 50:
                        outcome["samples"].append({"row": row_number, "classification": "invalid", "reason": str(exc)})
            results[sheet_name] = outcome

        if apply:
            db.session.commit()
        else:
            db.session.rollback()
    except Exception:
        db.session.rollback()
        raise

    stage_path.write_text(json.dumps(results, indent=2, ensure_ascii=True, default=_json_value), encoding="utf-8")
    return results
