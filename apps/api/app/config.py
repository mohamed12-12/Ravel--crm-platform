import os
from pathlib import Path

from services.data_authority import load_data_authority


def _load_env_file(path):
    if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("PYTEST_ADDOPTS"):
        # Never let the real dev .env leak into a test process: it defeats
        # monkeypatch.setenv/delenv and os.environ.pop for any key .env also defines.
        return
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


SYSTEM_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = SYSTEM_ROOT.parent.parent
_load_env_file(REPO_ROOT / ".env")
_load_env_file(SYSTEM_ROOT / ".env")


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return default


def _looks_weak_secret(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return True
    if len(normalized) < 24:
        return True
    weak_markers = {"changeme", "change-me", "default", "dev-key", "demo", "password"}
    return normalized in weak_markers or any(marker in normalized for marker in weak_markers)


def _default_sqlite_uri() -> str:
    db_path = (SYSTEM_ROOT / "instance" / "rahma_traveler_dev.db").resolve()
    return f"sqlite:///{db_path.as_posix()}"

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', '')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    USD_TO_EGP_RATE = _env_float("USD_TO_EGP_RATE", 50.0)
    # Google Sheets integration
    GOOGLE_SHEET_ID   = os.environ.get('GOOGLE_SHEET_ID', '')
    GOOGLE_CREDS_PATH = os.environ.get('GOOGLE_CREDS_PATH', 'rahma-496108-a27c767efdaf.json')

class DevelopmentConfig(Config):
    DEBUG = _env_flag("FLASK_DEBUG", default=False)
    # TODO(production): migrate demo SQLite data to PostgreSQL and require DATABASE_URL in deploy environments.
    # NOTE: Evaluated as a class property so that tests can override DATABASE_URL
    # via os.environ *before* calling create_app() and get the correct path.
    @classmethod
    def get_sqlalchemy_uri(cls) -> str:  # type: ignore[override]
        return os.environ.get('DATABASE_URL', _default_sqlite_uri())


class ProductionConfig(Config):
    DEBUG = False

    @classmethod
    def get_sqlalchemy_uri(cls) -> str:  # type: ignore[override]
        return os.environ.get('DATABASE_URL', '')

config = {
    'development': DevelopmentConfig,
    'production':  ProductionConfig,
    'default':     DevelopmentConfig
}

def validate_config(config_name: str) -> list[str]:
    errors: list[str] = []
    resolved_config = config.get(config_name, DevelopmentConfig)
    if config_name == "production":
        if _looks_weak_secret(os.environ.get("SECRET_KEY", "")):
            errors.append("SECRET_KEY must be a strong non-default secret in production.")
        if not _env_flag("CRM_AUTH_ENABLED", default=True):
            errors.append("CRM_AUTH_ENABLED cannot be false in production.")
        if _env_flag("FLASK_DEBUG", default=False):
            errors.append("FLASK_DEBUG cannot be true in production.")
    authority = load_data_authority(environment=config_name)
    errors.extend(
        authority.validate(
            environment=config_name,
            database_url=(resolved_config.get_sqlalchemy_uri() if hasattr(resolved_config, "get_sqlalchemy_uri") else ""),
            system_db_path=os.environ.get("RAHMA_SYSTEM_DB_PATH", ""),
            excel_source_workbook=os.environ.get("EXCEL_SOURCE_WORKBOOK", ""),
            excel_runtime_workbook=os.environ.get("EXCEL_RUNTIME_WORKBOOK", ""),
        )
    )
    return errors
