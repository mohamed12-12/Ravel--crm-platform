"""Minor/guardian-consent branch (spec gap analysis Task 1.1).

Before this fix, date of birth was parsed and stored but age was never computed,
so a traveler under 18 flowed through the exact same path as an adult -- no
guardian name/phone was ever collected, and no is_minor/guardian flag was ever
persisted. These tests pin: the age boundary (17 triggers, 18 does not), that
the branch fires identically whether the traveler is brand new or already on
file in CRM, and that guardian details are actually written to the traveler
(and the lead is flagged) rather than only kept in conversation state.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import uuid
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest

from services.ai_agent.ai_agent_app.agent.session_flow import SessionState
from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
from services.crm.system_services.unified_service import UnifiedCRMService
from tests.test_agent_conversation_reliability import PassiveAgent, RecordingReadTools
from tests.test_golden_transcript_regressions import _write_results_by_action
from tests.test_phase11_demo_features import _make_app_with_db


def _birth_date_for_age(age: int) -> date:
    today = date.today()
    try:
        return today.replace(year=today.year - age)
    except ValueError:
        return today.replace(month=2, day=28, year=today.year - age)


def _birthday_for_age(age: int) -> str:
    """DD/MM/YYYY, as a customer would type it in chat."""
    return _birth_date_for_age(age).strftime("%d/%m/%Y")


def _birthday_iso_for_age(age: int) -> str:
    """ISO YYYY-MM-DD, matching how CRM stores an existing traveler's birthday."""
    return _birth_date_for_age(age).isoformat()


@pytest.fixture()
def runtime(tmp_path: Path):
    original_env = dict(os.environ)
    os.environ["AI_AGENT_MODE"] = "tool_calling"
    client, app = _make_app_with_db(tmp_path / uuid.uuid4().hex)
    settings = replace(app.config["SETTINGS"], ai_provider="none", gemini_api_key="")
    rt = ToolCallingSessionRuntime(settings=settings, conversation_ai=PassiveAgent())
    rt._read_only_tools = RecordingReadTools()
    rt._write_executor = Mock()
    try:
        yield rt
    finally:
        os.environ.clear()
        os.environ.update(original_env)
        shutil.rmtree(tmp_path, ignore_errors=True)


def _send(rt: ToolCallingSessionRuntime, text: str, session: SessionState | None = None) -> SessionState:
    session = session or rt.create_session()
    return rt.handle_message(session, text, gateway=None)


NEW_MINOR_TRAVELER_WRITE = {
    "executed": True,
    "result_id": "TR00777",
    "traveler": {"traveler_id": "TR00777", "full_name": "Ahmed Sami Youssef", "status": "Active"},
    "write_result": {"created_traveler": {"traveler_id": "TR00777", "status": "Active"}},
    "write_result_contract": {"status": "success", "record_id": "TR00777", "record_type": "traveler", "executed": True},
}

NEW_MINOR_LEAD_WRITE = {
    "executed": True,
    "result_id": "LD00777",
    "assistant_message": "Lead saved.",
    "lead_update": {"lead_id": "LD00777"},
    "write_result_contract": {"status": "success", "record_id": "LD00777", "record_type": "lead", "executed": True},
}


def test_minor_new_traveler_triggers_guardian_branch_before_currency(runtime: ToolCallingSessionRuntime) -> None:
    session = runtime.create_session()
    for text in ("01270482380", "Ahmed Sami Youssef", "Egyptian", _birthday_for_age(17)):
        session = _send(runtime, text, session)
    assert session.stage == "guardian_name_required"


def test_adult_new_traveler_at_exactly_18_skips_guardian_branch(runtime: ToolCallingSessionRuntime) -> None:
    session = runtime.create_session()
    for text in ("01270482380", "Ahmed Sami Youssef", "Egyptian", _birthday_for_age(18)):
        session = _send(runtime, text, session)
    assert session.stage == "currency_required"


def test_new_minor_traveler_guardian_consent_persists_after_lead_save(runtime: ToolCallingSessionRuntime) -> None:
    runtime._write_executor.execute.side_effect = _write_results_by_action(
        create_traveler=NEW_MINOR_TRAVELER_WRITE,
        create_lead=NEW_MINOR_LEAD_WRITE,
    )
    session = runtime.create_session()
    for text in ("01270482380", "Ahmed Sami Youssef", "Egyptian", _birthday_for_age(16)):
        session = _send(runtime, text, session)
    assert session.stage == "guardian_name_required"

    session = _send(runtime, "Sami Youssef Ahmed", session)
    assert session.guardian_name == "Sami Youssef Ahmed"
    assert session.stage == "guardian_phone_required"
    # Guardian consent is not persisted yet -- the traveler does not exist in
    # CRM until the lead-save step runs, so there is nothing to attach it to.
    runtime._write_executor.record_guardian_consent.assert_not_called()

    session = _send(runtime, "01009998877", session)
    assert session.guardian_phone == "01009998877"
    assert session.stage == "currency_required"

    session = _send(runtime, "1", session)
    assert session.currency == "EGP"
    assert session.new_traveler_lead_saved is True
    runtime._write_executor.record_guardian_consent.assert_called_once_with(
        traveler_id="TR00777",
        is_minor=True,
        guardian_name="Sami Youssef Ahmed",
        guardian_phone="01009998877",
    )
    runtime._write_executor.record_lead_guardian_flag.assert_called_once_with(
        lead_id="LD00777",
        requires_guardian_approval=True,
    )


def test_existing_minor_traveler_from_crm_lookup_triggers_guardian_branch(runtime: ToolCallingSessionRuntime) -> None:
    """A returning traveler with a minor's DOB already on file is not exempt --
    the branch must fire even though the intake questions (name/nationality/DOB)
    are normally skipped entirely for a known traveler."""

    identity = {
        "traveler_id": "TR00778",
        "full_name": "Youssef Kamal",
        "status": "Active",
        "birthday": _birthday_iso_for_age(15),
    }
    runtime._read_only_tools = RecordingReadTools(identity=identity)
    session = runtime.create_session()
    session = _send(runtime, "01270482380", session)
    assert session.stage == "guardian_name_required"

    session = _send(runtime, "Kamal Youssef Ahmed", session)
    assert session.stage == "guardian_phone_required"
    runtime._write_executor.record_guardian_consent.assert_not_called()

    session = _send(runtime, "01001234567", session)
    runtime._write_executor.record_guardian_consent.assert_called_once_with(
        traveler_id="TR00778",
        is_minor=True,
        guardian_name="Kamal Youssef Ahmed",
        guardian_phone="01001234567",
    )
    # No lead has been created yet in this session -- only the traveler flag is written.
    runtime._write_executor.record_lead_guardian_flag.assert_not_called()


def test_set_guardian_consent_and_lead_flag_persist_to_real_db(tmp_path: Path) -> None:
    """Verify against a fresh DB read, not the write call's own return value.

    `record_guardian_consent`/`record_lead_guardian_flag` are asserted elsewhere
    only by their call arguments (the mocked write executor never touches a real
    database). This test exercises the actual SQL in unified_service.py and
    reads the row back with a brand new connection, matching how the pipeline
    spec requires every "saved" claim to be backed by a real read-back.
    """
    original_env = dict(os.environ)
    os.environ["AI_AGENT_MODE"] = "tool_calling"
    db_dir = tmp_path / uuid.uuid4().hex
    try:
        _make_app_with_db(db_dir)
        db_path = os.environ["RAHMA_SYSTEM_DB_PATH"]
        service = UnifiedCRMService()
        service.ensure_operational_schema()
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO travelers (traveler_id, full_name, status) VALUES (?, ?, ?)",
                ("TR00999", "Test Minor", "Active"),
            )
            conn.execute(
                "INSERT INTO leads (lead_id, customer_name, traveler_id, raw_phone, created_at, lead_stage) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("LD00999", "Test Minor", "TR00999", "01112223333", "2026-08-06T10:00:00", "New Lead"),
            )
            conn.commit()

        service.set_guardian_consent(
            "TR00999",
            is_minor=True,
            guardian_name="Parent Name",
            guardian_phone="01009998877",
        )
        service.flag_lead_guardian_approval("LD00999", requires_guardian_approval=True)

        with sqlite3.connect(db_path) as fresh_connection:
            fresh_connection.row_factory = sqlite3.Row
            traveler_row = fresh_connection.execute(
                "SELECT is_minor, guardian_name, guardian_phone FROM travelers WHERE traveler_id = ?",
                ("TR00999",),
            ).fetchone()
            lead_row = fresh_connection.execute(
                "SELECT requires_guardian_approval FROM leads WHERE lead_id = ?",
                ("LD00999",),
            ).fetchone()

        assert bool(traveler_row["is_minor"]) is True
        assert traveler_row["guardian_name"] == "Parent Name"
        assert traveler_row["guardian_phone"] == "01009998877"
        assert bool(lead_row["requires_guardian_approval"]) is True
    finally:
        os.environ.clear()
        os.environ.update(original_env)
        shutil.rmtree(db_dir, ignore_errors=True)


if __name__ == "__main__":
    import unittest

    unittest.main()
