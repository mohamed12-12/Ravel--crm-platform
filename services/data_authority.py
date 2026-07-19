from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


def _flag(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    value = env.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _path(value: str, *, repo_root: Path | None = None) -> Path | None:
    raw = str(value or "").strip().strip('"').strip("'")
    if not raw:
        return None
    candidate = Path(raw)
    if not candidate.is_absolute() and repo_root is not None:
        candidate = repo_root / candidate
    return candidate.resolve()


@dataclass(frozen=True)
class DataAuthoritySettings:
    authority: str = "crm"
    schema_version: str = "1"
    agent_access_mode: str = "shared_service"
    crm_api_base_url: str = ""
    crm_api_token: str = ""
    excel_import_enabled: bool = True
    excel_export_enabled: bool = True
    google_sheets_import_enabled: bool = False
    google_sheets_export_enabled: bool = False
    direct_import_apply_enabled: bool = False
    sheet_mirror_enabled: bool = False
    demo_data_mode: bool = False

    @property
    def operational_writer(self) -> str:
        return self.authority

    def export_enabled_for(self, backend: str) -> bool:
        normalized = str(backend or "").strip().lower()
        if normalized == "excel":
            return self.excel_export_enabled and self.sheet_mirror_enabled
        if normalized in {"google", "google_sheets"}:
            return self.google_sheets_export_enabled and self.sheet_mirror_enabled
        return False

    def import_enabled_for(self, source: str) -> bool:
        normalized = str(source or "").strip().lower()
        if normalized == "excel":
            return self.excel_import_enabled
        if normalized in {"google", "google_sheets"}:
            return self.google_sheets_import_enabled
        return False

    def validate(
        self,
        *,
        environment: str,
        database_url: str = "",
        system_db_path: str = "",
        excel_source_workbook: str = "",
        excel_runtime_workbook: str = "",
    ) -> list[str]:
        errors: list[str] = []
        env_name = str(environment or "development").strip().lower()
        if self.authority != "crm":
            errors.append("DATA_AUTHORITY must be 'crm'; spreadsheet operational authority is unsupported.")
        if self.agent_access_mode not in {"api", "shared_service"}:
            errors.append("CRM_ACCESS_MODE must be 'api' or 'shared_service'.")
        if self.direct_import_apply_enabled and env_name == "production":
            errors.append("DIRECT_IMPORT_APPLY_ENABLED cannot be enabled in production; approved staged apply is required.")
        if self.demo_data_mode and env_name == "production":
            errors.append("DEMO_DATA_MODE must be false in production.")
        if env_name == "production":
            if not str(database_url or "").strip():
                errors.append("DATABASE_URL is required in production.")
            if self.agent_access_mode != "api":
                errors.append("CRM_ACCESS_MODE must be 'api' in production.")
            if not self.crm_api_base_url:
                errors.append("CRM_API_BASE_URL is required for production agent access.")
            if not self.crm_api_token:
                errors.append("CRM_API_TOKEN is required for production agent access.")

        source = _path(excel_source_workbook)
        runtime = _path(excel_runtime_workbook)
        if env_name == "production" and source is not None and runtime is not None and source == runtime:
            errors.append("EXCEL_SOURCE_WORKBOOK and EXCEL_RUNTIME_WORKBOOK must be different files.")

        db_url = str(database_url or "").strip()
        if env_name == "production" and self.agent_access_mode == "shared_service" and db_url.startswith("sqlite:///") and system_db_path:
            url_path = _path(db_url.removeprefix("sqlite:///"))
            service_path = _path(system_db_path)
            if url_path is not None and service_path is not None and url_path != service_path:
                errors.append("RAHMA_SYSTEM_DB_PATH must resolve to the same SQLite database as DATABASE_URL.")
        return errors


def load_data_authority(
    env: Mapping[str, str] | None = None,
    *,
    environment: str | None = None,
) -> DataAuthoritySettings:
    values = env or os.environ
    env_name = str(environment or values.get("APP_ENV") or values.get("FLASK_CONFIG") or "development").strip().lower()
    return DataAuthoritySettings(
        authority=str(values.get("DATA_AUTHORITY", "crm")).strip().lower() or "crm",
        schema_version=str(values.get("DATA_SCHEMA_VERSION", "1")).strip() or "1",
        agent_access_mode=str(values.get("CRM_ACCESS_MODE", "shared_service")).strip().lower() or "shared_service",
        crm_api_base_url=str(values.get("CRM_API_BASE_URL", "")).strip().rstrip("/"),
        crm_api_token=str(values.get("CRM_API_TOKEN", "")).strip(),
        excel_import_enabled=_flag(values, "EXCEL_IMPORT_ENABLED", True),
        excel_export_enabled=_flag(values, "EXCEL_EXPORT_ENABLED", True),
        google_sheets_import_enabled=_flag(values, "GOOGLE_SHEETS_IMPORT_ENABLED", False),
        google_sheets_export_enabled=_flag(values, "GOOGLE_SHEETS_EXPORT_ENABLED", False),
        direct_import_apply_enabled=_flag(values, "DIRECT_IMPORT_APPLY_ENABLED", False),
        sheet_mirror_enabled=_flag(values, "CRM_SHEET_MIRROR_ENABLED", False),
        demo_data_mode=_flag(values, "DEMO_DATA_MODE", env_name != "production"),
    )
