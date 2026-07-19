from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from services.data_authority import load_data_authority


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _extract_sheet_id(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    marker = "/spreadsheets/d/"
    if marker in text:
        tail = text.split(marker, 1)[1]
        return tail.split("/", 1)[0].strip()
    return text


def _optional_path(base: Path, raw: str | None) -> Path | None:
    if not raw:
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate


@dataclass(frozen=True)
class SystemServiceSettings:
    repo_root: Path
    system_root: Path
    db_path: Path
    sheet_backend: str
    excel_source_workbook: Path | None
    excel_runtime_workbook: Path | None
    google_sheet_id: str = ""
    google_application_credentials: Path | None = None
    data_authority: str = "crm"
    sheet_export_enabled: bool = False
    allow_source_workbook_writes: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "repo_root", Path(self.repo_root))
        object.__setattr__(self, "system_root", Path(self.system_root))
        object.__setattr__(self, "db_path", Path(self.db_path))
        if self.excel_source_workbook is not None:
            object.__setattr__(self, "excel_source_workbook", Path(self.excel_source_workbook))
        if self.excel_runtime_workbook is not None:
            object.__setattr__(self, "excel_runtime_workbook", Path(self.excel_runtime_workbook))
        if self.google_application_credentials is not None:
            object.__setattr__(self, "google_application_credentials", Path(self.google_application_credentials))


def load_system_settings() -> SystemServiceSettings:
    repo_root = Path(__file__).resolve().parents[3]
    system_root = repo_root / "apps" / "api"

    _load_env_file(repo_root / ".env")
    _load_env_file(system_root / ".env")

    db_override = os.getenv("RAHMA_SYSTEM_DB_PATH", "").strip()
    if db_override:
        db_path = Path(db_override)
        if not db_path.is_absolute():
            db_path = repo_root / db_path
    else:
        db_path = system_root / "instance" / "rahma_traveler_dev.db"

    backend = os.getenv("SHEET_BACKEND", "excel").strip().lower()
    source_workbook = _optional_path(repo_root, os.getenv("EXCEL_SOURCE_WORKBOOK"))
    runtime_workbook = _optional_path(repo_root, os.getenv("EXCEL_RUNTIME_WORKBOOK"))
    creds = _optional_path(repo_root, os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
    authority = load_data_authority()

    return SystemServiceSettings(
        repo_root=repo_root,
        system_root=system_root,
        db_path=db_path,
        sheet_backend=backend,
        excel_source_workbook=source_workbook,
        excel_runtime_workbook=runtime_workbook,
        google_sheet_id=_extract_sheet_id(os.getenv("GOOGLE_SHEET_ID", "")),
        google_application_credentials=creds,
        data_authority=authority.authority,
        sheet_export_enabled=authority.export_enabled_for(backend),
        allow_source_workbook_writes=False,
    )


def resolve_system_db_path() -> Path:
    """Return the operational SQLite path used by CRM and the AI agent."""
    return load_system_settings().db_path


def read_sqlite_table_counts(db_path: Path | str) -> dict[str, int | None]:
    """Read core CRM table counts from a SQLite database without mutating it."""
    path = Path(db_path)
    counts: dict[str, int | None] = {
        "travelers": None,
        "trips": None,
        "trip_bookings": None,
        "booking_status_history": None,
        "leads": None,
    }
    if not path.exists():
        return counts

    connection = sqlite3.connect(path)
    try:
        cursor = connection.cursor()
        for table_name in counts:
            try:
                counts[table_name] = cursor.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
            except sqlite3.Error:
                counts[table_name] = None
    finally:
        connection.close()
    return counts


def get_database_diagnostics(db_path: Path | str | None = None) -> dict[str, object]:
    """Return the resolved database path and key CRM counts for diagnostics."""
    resolved_path = Path(db_path) if db_path is not None else resolve_system_db_path()
    if not resolved_path.is_absolute():
        resolved_path = Path(__file__).resolve().parents[3] / resolved_path
    return {
        "db_path": str(resolved_path),
        "counts": read_sqlite_table_counts(resolved_path),
    }
