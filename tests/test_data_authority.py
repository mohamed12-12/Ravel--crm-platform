from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from services.data_authority import DataAuthoritySettings, load_data_authority


APP_ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def _app_with_database(tmp_path: Path, monkeypatch, *, auth: bool = False):
    db_path = (tmp_path / "authority.db").resolve()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("RAHMA_SYSTEM_DB_PATH", str(db_path))
    monkeypatch.setenv("DATA_AUTHORITY", "crm")
    monkeypatch.setenv("CRM_ACCESS_MODE", "shared_service")
    monkeypatch.setenv("CRM_AUTH_ENABLED", "true" if auth else "false")
    monkeypatch.setenv("CRM_API_TOKEN", "authority-test-token" if auth else "")
    from app import create_app
    from app.extensions import db

    app = create_app("development")
    app.config.update(TESTING=True)
    with app.app_context():
        db.create_all()
    return app, db


def test_ambiguous_authority_configuration_fails_validation(tmp_path: Path):
    same_workbook = tmp_path / "same.xlsx"
    settings = DataAuthoritySettings(authority="excel", agent_access_mode="shared_service")
    errors = settings.validate(
        environment="production",
        database_url=f"sqlite:///{(tmp_path / 'crm.db').as_posix()}",
        system_db_path=str(tmp_path / "other.db"),
        excel_source_workbook=str(same_workbook),
        excel_runtime_workbook=str(same_workbook),
    )
    assert any("DATA_AUTHORITY" in error for error in errors)
    assert any("same" in error or "different files" in error for error in errors)
    assert any("RAHMA_SYSTEM_DB_PATH" in error for error in errors)


def test_production_requires_agent_api_and_disables_demo():
    settings = DataAuthoritySettings(
        authority="crm",
        agent_access_mode="shared_service",
        demo_data_mode=True,
    )
    errors = settings.validate(environment="production", database_url="sqlite:///crm.db")
    assert any("CRM_ACCESS_MODE" in error for error in errors)
    assert any("DEMO_DATA_MODE" in error for error in errors)
    assert any("CRM_API_BASE_URL" in error for error in errors)


def test_import_preview_blocks_spreadsheet_conflicts(tmp_path: Path, monkeypatch):
    app, db = _app_with_database(tmp_path, monkeypatch)
    from app.models import Traveler, Trip
    from app.services.importer import _execute_import

    frames = {
        "Travelers": pd.DataFrame(
            [{"Traveler ID": "TR00999", "Full Name": "Sheet Name", "Status": "Inactive"}]
        ),
        "Trips": pd.DataFrame(
            [{
                "Trip ID": "RT-TEST",
                "Trip Name": "Sheet Trip",
                "Sales Status": "Open",
                "Single Remaining": 99,
            }]
        ),
    }

    with app.app_context():
        db.session.add(Traveler(traveler_id="TR00999", full_name="CRM Name", status="Active"))
        db.session.add(
            Trip(
                trip_id="RT-TEST",
                trip_name="CRM Trip",
                sales_status="Closed",
                single_remaining=3,
            )
        )
        db.session.commit()

        result = _execute_import(
            lambda sheet, _header: frames.get(sheet, pd.DataFrame()),
            source_type="excel",
            source_label="authority-test.xlsx",
            apply=False,
            approved_by="",
        )

        assert result["Travelers"]["conflict"] == 1
        assert result["Trips"]["conflict"] == 1
        assert db.session.get(Traveler, "TR00999").full_name == "CRM Name"
        assert db.session.get(Trip, "RT-TEST").single_remaining == 3
        assert Path(app.instance_path, "import-staging", f"{result['_meta']['fingerprint']}.json").exists()


def test_import_apply_is_disabled_by_default(tmp_path: Path, monkeypatch):
    app, _db = _app_with_database(tmp_path, monkeypatch)
    from app.services.importer import ImportPolicyError, _execute_import

    loader = lambda sheet, _header: (
        pd.DataFrame([{"Traveler ID": "TR00888", "Full Name": "New Traveler", "Status": "Active"}])
        if sheet == "Travelers"
        else pd.DataFrame()
    )
    with app.app_context():
        _execute_import(
            loader,
            source_type="excel",
            source_label="apply-disabled.xlsx",
            apply=False,
            approved_by="",
        )
        with pytest.raises(ImportPolicyError, match="disabled"):
            _execute_import(
                loader,
                source_type="excel",
                source_label="apply-disabled.xlsx",
                apply=True,
                approved_by="qa@example.com",
            )


def test_import_detects_duplicate_phone_under_different_id(tmp_path: Path, monkeypatch):
    app, db = _app_with_database(tmp_path, monkeypatch)
    from app.models import Traveler
    from app.services.importer import _execute_import

    loader = lambda sheet, _header: (
        pd.DataFrame(
            [{
                "Traveler ID": "TR00502",
                "Full Name": "Duplicate Identity",
                "Phone Code": "20",
                "WhatsApp (raw)": "01554158741",
            }]
        )
        if sheet == "Travelers"
        else pd.DataFrame()
    )
    with app.app_context():
        db.session.add(
            Traveler(
                traveler_id="TR00501",
                full_name="CRM Identity",
                phone_code="20",
                whatsapp_raw="01554158741",
                normalized_whatsapp="+201554158741",
            )
        )
        db.session.commit()
        result = _execute_import(
            loader,
            source_type="excel",
            source_label="duplicate-phone.xlsx",
            apply=False,
            approved_by="",
        )
        assert result["Travelers"]["duplicate"] == 1
        assert result["Travelers"]["new"] == 0


def test_validated_import_preserves_id_and_replay_is_idempotent(tmp_path: Path, monkeypatch):
    app, db = _app_with_database(tmp_path, monkeypatch)
    from app.models import Traveler
    from app.services.importer import _execute_import

    loader = lambda sheet, _header: (
        pd.DataFrame([{"Traveler ID": "TR00444", "Full Name": "Legacy ID", "Status": "Active"}])
        if sheet == "Travelers"
        else pd.DataFrame()
    )
    with app.app_context():
        _execute_import(loader, source_type="excel", source_label="legacy.xlsx", apply=False, approved_by="")
        monkeypatch.setenv("DIRECT_IMPORT_APPLY_ENABLED", "true")
        applied = _execute_import(
            loader,
            source_type="excel",
            source_label="legacy.xlsx",
            apply=True,
            approved_by="migration-owner",
        )
        replayed = _execute_import(
            loader,
            source_type="excel",
            source_label="legacy.xlsx",
            apply=True,
            approved_by="migration-owner",
        )
        assert applied["Travelers"]["inserted"] == 1
        assert replayed["Travelers"]["inserted"] == 0
        assert db.session.query(Traveler).filter_by(traveler_id="TR00444").count() == 1


def test_import_does_not_generate_missing_external_ids(tmp_path: Path, monkeypatch):
    app, db = _app_with_database(tmp_path, monkeypatch)
    from app.models import Traveler
    from app.services.importer import _execute_import

    loader = lambda sheet, _header: (
        pd.DataFrame([{"Traveler ID": "", "Full Name": "No External ID"}])
        if sheet == "Travelers"
        else pd.DataFrame()
    )
    with app.app_context():
        result = _execute_import(loader, source_type="excel", source_label="missing-id.xlsx", apply=False, approved_by="")
        assert result["Travelers"]["invalid"] == 1
        assert db.session.query(Traveler).count() == 0


def test_changed_source_requires_a_new_preview_before_apply(tmp_path: Path, monkeypatch):
    app, _db = _app_with_database(tmp_path, monkeypatch)
    from app.services.importer import ImportPolicyError, _execute_import

    def loader_for(name):
        return lambda sheet, _header: (
            pd.DataFrame([{"Traveler ID": "TR00333", "Full Name": name}])
            if sheet == "Travelers"
            else pd.DataFrame()
        )

    with app.app_context():
        _execute_import(loader_for("Preview Value"), source_type="excel", source_label="stale.xlsx", apply=False, approved_by="")
        monkeypatch.setenv("DIRECT_IMPORT_APPLY_ENABLED", "true")
        with pytest.raises(ImportPolicyError, match="previewed"):
            _execute_import(
                loader_for("Changed After Preview"),
                source_type="excel",
                source_label="stale.xlsx",
                apply=True,
                approved_by="migration-owner",
            )


def test_google_import_is_disabled_before_network_access(monkeypatch):
    monkeypatch.setenv("GOOGLE_SHEETS_IMPORT_ENABLED", "false")
    from app.services.importer import ImportPolicyError, run_sheets_import

    with pytest.raises(ImportPolicyError, match="disabled"):
        run_sheets_import("sheet-id", "missing-credentials.json")


def test_agent_write_executor_routes_approved_write_to_crm_api(monkeypatch):
    from services.ai_agent.ai_agent_app.agent.read_only_tools import ReadOnlyCRMTools
    from services.ai_agent.ai_agent_app.agent.write_tool_executor import GeminiWriteToolExecutor
    from services.ai_agent.ai_agent_app.config import load_settings
    from services.ai_agent.validation import APPROVED, ValidationResult

    class FakeApiClient:
        def __init__(self):
            self.calls = []

        def read(self, action, payload):
            return {"action": action, "payload": payload}

        def write(self, action, payload, session_context):
            self.calls.append((action, payload, session_context))
            return {"executed": True, "result_id": "LD-API", "assistant_message": "Saved by CRM API"}

    class ApprovingValidator:
        @staticmethod
        def validate_action(*, action, payload, session_context):
            return ValidationResult(action=action, decision=APPROVED, session_id=session_context.get("session_id", ""))

    settings = replace(load_settings(), crm_access_mode="api", crm_api_base_url="http://crm.test", crm_api_token="token")
    api_client = FakeApiClient()
    tools = ReadOnlyCRMTools(settings, api_client=api_client)
    executor = GeminiWriteToolExecutor(settings=settings, read_only_tools=tools, action_validator=ApprovingValidator())
    result = executor.execute(
        action="create_lead",
        payload={"customer_name": "API Lead"},
        session_context={"session_id": "session-1"},
    )
    assert result["executed"] is True
    assert api_client.calls == [
        ("create_lead", {"customer_name": "API Lead"}, {"session_id": "session-1"})
    ]


def test_passport_metadata_routes_to_crm_api_in_api_mode(monkeypatch):
    from services.ai_agent.ai_agent_app.agent.crm_api_client import CRMApiClient
    from services.ai_agent.ai_agent_app.config import load_settings
    from services.ai_agent.ai_agent_app.system_bridge import save_system_traveler_passport

    captured = {}

    def fake_save(self, traveler_id, payload):
        captured.update({"traveler_id": traveler_id, "payload": payload})
        return {"traveler_id": traveler_id, "saved": True}

    monkeypatch.setattr(CRMApiClient, "save_passport", fake_save)
    settings = replace(load_settings(), crm_access_mode="api", crm_api_base_url="http://crm.test", crm_api_token="token")
    result = save_system_traveler_passport(
        settings,
        "TR-PASSPORT",
        passport_attachment_ref="uploads/passport.pdf",
        passport_status="provided",
    )
    assert result["saved"] is True
    assert captured["traveler_id"] == "TR-PASSPORT"
    assert captured["payload"]["passport_attachment_ref"] == "uploads/passport.pdf"


def test_disabled_sheet_mirror_cannot_write_source_or_runtime(tmp_path: Path):
    from services.crm.system_services.config import SystemServiceSettings
    from services.crm.system_services.unified_service import UnifiedCRMService

    source = tmp_path / "source.xlsx"
    runtime = tmp_path / "runtime.xlsx"
    settings = SystemServiceSettings(
        repo_root=tmp_path,
        system_root=tmp_path,
        db_path=tmp_path / "crm.db",
        sheet_backend="excel",
        excel_source_workbook=source,
        excel_runtime_workbook=runtime,
        data_authority="crm",
        sheet_export_enabled=False,
        allow_source_workbook_writes=False,
    )
    result = UnifiedCRMService(settings).sync_trip_to_sheet("RT-NOT-WRITTEN")
    assert result["status"] == "disabled"
    assert not source.exists()
    assert not runtime.exists()


def test_demo_reset_is_blocked_when_demo_mode_is_disabled(tmp_path: Path, monkeypatch):
    from openpyxl import Workbook
    from services.ai_agent.ai_agent_app.config import load_settings
    from services.ai_agent.ai_agent_app.server import create_app

    source = tmp_path / "source.xlsx"
    runtime = tmp_path / "runtime.xlsx"
    workbook = Workbook()
    workbook.active.title = "Travelers"
    workbook.create_sheet("Trips")
    workbook.save(source)
    settings = replace(
        load_settings(),
        ai_agent_mode="deterministic",
        sheet_backend="excel",
        excel_source_workbook=source,
        excel_runtime_workbook=runtime,
        demo_data_mode=False,
    )
    app = create_app(settings=settings)
    response = app.test_client().post("/api/reset")
    assert response.status_code == 409
    assert response.get_json()["error"] == "demo_reset_disabled"


def test_agent_crm_api_reads_same_authoritative_database(tmp_path: Path, monkeypatch):
    app, db = _app_with_database(tmp_path, monkeypatch, auth=True)
    from app.models import Traveler

    with app.app_context():
        db.session.add(Traveler(traveler_id="TR00777", full_name="API Traveler", status="Active"))
        db.session.commit()

    client = app.test_client()
    denied = client.post(
        "/api/crm/agent/read",
        json={"action": "get_traveler_profile", "payload": {"traveler_id": "TR00777"}},
    )
    assert denied.status_code == 401

    response = client.post(
        "/api/crm/agent/read",
        json={"action": "get_traveler_profile", "payload": {"traveler_id": "TR00777"}},
        headers={"Authorization": "Bearer authority-test-token", "X-CRM-Role": "agent"},
    )
    assert response.status_code == 200
    assert response.get_json()["result"]["traveler"]["full_name"] == "API Traveler"


def test_traveler_export_is_crm_owned_and_formula_safe(tmp_path: Path, monkeypatch):
    app, db = _app_with_database(tmp_path, monkeypatch)
    from app.models import Traveler

    with app.app_context():
        db.session.add(Traveler(traveler_id="TR00666", full_name="=CMD()", status="Active"))
        db.session.commit()

    response = app.test_client().get("/travelers/export")
    assert response.status_code == 200
    assert response.headers["X-Data-Authority"] == "crm"
    assert response.headers["X-Data-Schema-Version"] == load_data_authority().schema_version
    assert "'=CMD()" in response.get_data(as_text=True)
