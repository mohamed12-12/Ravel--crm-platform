from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent / "apps" / "api"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def _reset_app_modules() -> None:
    for module_name in list(sys.modules):
        if module_name == "app" or module_name.startswith("app."):
            sys.modules.pop(module_name, None)


def test_postgres_uri_gets_a_configured_connection_pool(monkeypatch):
    """No SQLALCHEMY_ENGINE_OPTIONS existed at all before -- SQLAlchemy's
    small defaults (pool_size=5, no pre-ping, no recycle) were whatever
    happened to apply, unsized for how many gunicorn workers x concurrent
    requests would actually be running against Postgres in production.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@127.0.0.1:59999/nonexistent")
    _reset_app_modules()
    from app import create_app

    app = create_app("development")
    options = app.config.get("SQLALCHEMY_ENGINE_OPTIONS")
    assert options is not None
    assert options["pool_size"] == 5
    assert options["max_overflow"] == 10
    assert options["pool_pre_ping"] is True
    assert options["pool_recycle"] == 280
    _reset_app_modules()


def test_pool_settings_are_adjustable_via_env_vars(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@127.0.0.1:59999/nonexistent")
    monkeypatch.setenv("DB_POOL_SIZE", "12")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "24")
    monkeypatch.setenv("DB_POOL_RECYCLE_SECONDS", "600")
    _reset_app_modules()
    from app import create_app

    app = create_app("development")
    options = app.config.get("SQLALCHEMY_ENGINE_OPTIONS")
    assert options["pool_size"] == 12
    assert options["max_overflow"] == 24
    assert options["pool_recycle"] == 600
    _reset_app_modules()


def test_sqlite_uri_gets_no_engine_options(monkeypatch):
    """SQLite doesn't support pool_size/max_overflow at all -- passing them
    would raise at engine-creation time, so this must stay conditional on
    the URI actually being Postgres.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    _reset_app_modules()
    from app import create_app

    app = create_app("development")
    assert not app.config.get("SQLALCHEMY_ENGINE_OPTIONS")
    _reset_app_modules()
