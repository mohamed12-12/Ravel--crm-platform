from __future__ import annotations

from dataclasses import replace
from pathlib import Path


def test_ai_agent_production_rejects_weak_or_unsafe_env(tmp_path: Path, monkeypatch):
    from services.ai_agent.ai_agent_app.config import load_settings

    source = tmp_path / "source.xlsx"
    runtime = tmp_path / "runtime.xlsx"
    source.write_bytes(b"placeholder")
    runtime.write_bytes(b"placeholder")

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_SECRET_KEY", "demo-secret")
    monkeypatch.setenv("AI_AGENT_MODE", "deterministic")
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_MODEL", "")
    monkeypatch.setenv("AGENT_WRITE_TOOL_ENFORCEMENT", "false")
    monkeypatch.setenv("DEMO_RESET_ON_START", "true")
    monkeypatch.setenv("APP_DEBUG", "true")
    monkeypatch.setenv("APP_USE_RELOADER", "true")
    monkeypatch.setenv("DATA_AUTHORITY", "crm")
    monkeypatch.setenv("CRM_ACCESS_MODE", "api")
    monkeypatch.setenv("CRM_API_BASE_URL", "https://crm.example.test")
    monkeypatch.setenv("CRM_API_TOKEN", "strong-token-value-for-tests")
    monkeypatch.setenv("DATABASE_URL", "postgresql://rahma:secret@db/rahma")
    monkeypatch.setenv("DEMO_DATA_MODE", "false")
    monkeypatch.setenv("SHEET_BACKEND", "excel")
    monkeypatch.setenv("EXCEL_SOURCE_WORKBOOK", str(source))
    monkeypatch.setenv("EXCEL_RUNTIME_WORKBOOK", str(runtime))

    errors = load_settings().validate()

    assert any("APP_SECRET_KEY" in error for error in errors)
    assert any("AI_AGENT_MODE" in error for error in errors)
    assert any("GEMINI_API_KEY" in error for error in errors)
    assert any("GEMINI_MODEL" in error for error in errors)
    assert any("AGENT_WRITE_TOOL_ENFORCEMENT" in error for error in errors)
    assert any("DEMO_RESET_ON_START" in error for error in errors)
    assert any("APP_DEBUG" in error for error in errors)
    assert any("APP_USE_RELOADER" in error for error in errors)


def test_ai_agent_production_accepts_strong_safe_env(tmp_path: Path, monkeypatch):
    from services.ai_agent.ai_agent_app.config import load_settings

    source = tmp_path / "source.xlsx"
    runtime = tmp_path / "runtime.xlsx"
    source.write_bytes(b"placeholder")
    runtime.write_bytes(b"placeholder")

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_SECRET_KEY", "strong-app-secret-value-32-bytes-min")
    monkeypatch.setenv("AI_AGENT_MODE", "tool_calling")
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key-placeholder")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-pro")
    monkeypatch.setenv("AGENT_TOOL_ROUTER_MODE", "dry_run")
    monkeypatch.setenv("AGENT_WRITE_TOOL_ENFORCEMENT", "true")
    monkeypatch.setenv("DEMO_RESET_ON_START", "false")
    monkeypatch.setenv("APP_DEBUG", "false")
    monkeypatch.setenv("APP_USE_RELOADER", "false")
    monkeypatch.setenv("DATA_AUTHORITY", "crm")
    monkeypatch.setenv("CRM_ACCESS_MODE", "api")
    monkeypatch.setenv("CRM_API_BASE_URL", "https://crm.example.test")
    monkeypatch.setenv("CRM_API_TOKEN", "strong-crm-token-value-32-bytes")
    monkeypatch.setenv("DATABASE_URL", "postgresql://rahma:secret@db/rahma")
    monkeypatch.setenv("DEMO_DATA_MODE", "false")
    monkeypatch.setenv("SHEET_BACKEND", "excel")
    monkeypatch.setenv("EXCEL_SOURCE_WORKBOOK", str(source))
    monkeypatch.setenv("EXCEL_RUNTIME_WORKBOOK", str(runtime))

    assert load_settings().validate() == []


def test_crm_api_production_rejects_weak_secret_debug_and_disabled_auth(monkeypatch):
    from apps.api.app.config import validate_config

    monkeypatch.setenv("SECRET_KEY", "dev-key")
    monkeypatch.setenv("CRM_AUTH_ENABLED", "false")
    monkeypatch.setenv("FLASK_DEBUG", "true")
    monkeypatch.setenv("DATA_AUTHORITY", "crm")
    monkeypatch.setenv("CRM_ACCESS_MODE", "api")
    monkeypatch.setenv("CRM_API_BASE_URL", "https://crm.example.test")
    monkeypatch.setenv("CRM_API_TOKEN", "strong-token-value-for-tests")
    monkeypatch.setenv("DATABASE_URL", "postgresql://rahma:secret@db/rahma")
    monkeypatch.setenv("DEMO_DATA_MODE", "false")

    errors = validate_config("production")

    assert any("SECRET_KEY" in error for error in errors)
    assert any("CRM_AUTH_ENABLED" in error for error in errors)
    assert any("FLASK_DEBUG" in error for error in errors)
