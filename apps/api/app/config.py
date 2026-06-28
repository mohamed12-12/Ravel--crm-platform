import os
from pathlib import Path


def _load_env_file(path):
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


def _default_sqlite_uri() -> str:
    db_path = (SYSTEM_ROOT / "instance" / "rahma_traveler_dev.db").resolve()
    return f"sqlite:///{db_path.as_posix()}"

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', '')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Google Sheets integration
    GOOGLE_SHEET_ID   = os.environ.get('GOOGLE_SHEET_ID', '')
    GOOGLE_CREDS_PATH = os.environ.get('GOOGLE_CREDS_PATH', 'rahma-496108-a27c767efdaf.json')

class DevelopmentConfig(Config):
    DEBUG = True
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
    if config_name == "production" or getattr(resolved_config, "DEBUG", False) is False:
        if not os.environ.get("SECRET_KEY", "").strip():
            errors.append("SECRET_KEY is required in production.")
        if not os.environ.get("GOOGLE_SHEET_ID", "").strip():
            errors.append("GOOGLE_SHEET_ID is required in production.")
    return errors
