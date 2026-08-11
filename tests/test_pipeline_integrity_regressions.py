"""Guards for the two gap-analysis items already confirmed solid before this
change (traveler_id never null before trip selection; get_trips filters CLOSED
trips out server-side). Tier 1/2 fixes must not regress either one.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from services.ai_agent.ai_agent_app.agent.session_flow import SessionState
from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
from services.crm.system_services.unified_service import UnifiedCRMService
from tests.test_agent_conversation_reliability import PassiveAgent, RecordingReadTools
from tests.test_golden_transcript_regressions import _write_results_by_action
from tests.test_phase11_demo_features import _make_app_with_db


# ---------------------------------------------------------------------------
# Item 9: get_trips must filter CLOSED/CANCELLED/ARCHIVED trips server-side.
# ---------------------------------------------------------------------------

def test_get_trips_excludes_closed_and_cancelled_trips(tmp_path: Path) -> None:
    original_env = dict(os.environ)
    db_dir = tmp_path / uuid.uuid4().hex
    try:
        _make_app_with_db(db_dir)
        db_path = os.environ["RAHMA_SYSTEM_DB_PATH"]
        with sqlite3.connect(db_path) as conn:
            for trip_id, status in (("RT-LOC-CLOSED", "Closed"), ("RT-LOC-CANCELLED", "Cancelled")):
                conn.execute(
                    """
                    INSERT INTO trips (
                        trip_id, trip_name, type, year, start_date, end_date, sales_status,
                        single_total, double_total, triple_total,
                        single_remaining, double_remaining, triple_remaining,
                        draft_holds_single, draft_holds_double, draft_holds_triple
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (trip_id, f"{status} Trip", "Local", 2026, "2026-09-20", "2026-09-24", status, 5, 5, 5, 5, 5, 5, 0, 0, 0),
                )
            conn.commit()

        service = UnifiedCRMService()
        result = service.build_trip_result("local")
        trip_ids = {trip["trip_id"] for trip in [*result["open_trips"], *result["date_tbd_trips"]]}

        assert "RT-LOC-26-001" in trip_ids  # the Open trip seeded by _make_app_with_db
        assert "RT-LOC-CLOSED" not in trip_ids
        assert "RT-LOC-CANCELLED" not in trip_ids
    finally:
        os.environ.clear()
        os.environ.update(original_env)
        shutil.rmtree(db_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Item 7: traveler_id must never be null once trip selection is reached.
# ---------------------------------------------------------------------------

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


def test_new_traveler_reaches_trip_type_step_with_a_resolved_traveler_id(runtime: ToolCallingSessionRuntime) -> None:
    runtime._write_executor.execute.side_effect = _write_results_by_action(
        create_traveler={
            "executed": True,
            "result_id": "TR00500",
            "traveler": {"traveler_id": "TR00500", "full_name": "Nadia Samir Fouad", "status": "Active"},
            "write_result": {"created_traveler": {"traveler_id": "TR00500", "status": "Active"}},
            "write_result_contract": {"status": "success", "record_id": "TR00500", "record_type": "traveler", "executed": True},
        },
        create_lead={
            "executed": True,
            "result_id": "LD00500",
            "assistant_message": "Lead saved.",
            "lead_update": {"lead_id": "LD00500"},
            "write_result_contract": {"status": "success", "record_id": "LD00500", "record_type": "lead", "executed": True},
        },
    )
    session = runtime.create_session()
    for text in ("01270482380", "Nadia Samir Fouad", "Egyptian", "10/03/1995"):
        session = _send(runtime, text, session)
    assert session.stage == "trip_type_required"
    assert ToolCallingSessionRuntime._linked_ids(session)["traveler_id"] == "TR00500"


if __name__ == "__main__":
    import unittest

    unittest.main()
