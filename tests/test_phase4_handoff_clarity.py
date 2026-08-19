from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
API_ROOT = ROOT / "apps" / "api"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))


def _reset_app_modules() -> None:
    for module_name in list(sys.modules):
        if module_name == "app" or module_name.startswith("app."):
            sys.modules.pop(module_name, None)


@pytest.fixture()
def handoff_app():
    original_env = dict(os.environ)
    tmpdir = ROOT / ".tmp-test-workdirs" / f"handoff-clarity-{uuid.uuid4().hex}"
    tmpdir.mkdir(parents=True, exist_ok=True)
    db_path = tmpdir / "app.db"
    os.environ["CRM_AUTH_ENABLED"] = "false"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.resolve().as_posix()}"
    os.environ["RAHMA_SYSTEM_DB_PATH"] = str(db_path)
    os.environ["SHEET_BACKEND"] = "excel"
    _reset_app_modules()
    from app import create_app
    from app.extensions import db

    app = create_app("development")
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with app.app_context():
        db.drop_all()
        db.create_all()
    try:
        yield app, db
    finally:
        os.environ.clear()
        os.environ.update(original_env)
        shutil.rmtree(tmpdir, ignore_errors=True)
        _reset_app_modules()


def test_handoff_notes_parser_extracts_postgres_json_without_raw_metadata() -> None:
    from app.routes.handoffs import _parse_handoff_notes

    raw = json.dumps({
        "notes": "Call the traveler after checking alternatives.",
        "reason_code": "room_capacity",
        "reason_text": "Girls double rooms are unavailable.",
        "customer_summary": "We need two double rooms.",
        "agent_summary": "Capacity validation blocked the booking.",
        "metadata": {
            "session_id": "session-123",
            "validation": {"action": "create_handoff", "decision": "APPROVED"},
        },
    })

    parsed = _parse_handoff_notes(raw, reason="Fallback reason", flow_key="tool_calling:session-123")

    assert parsed["reason_code"] == "room_capacity"
    assert parsed["reason_text"] == "Girls double rooms are unavailable."
    assert parsed["customer_summary"] == "We need two double rooms."
    assert parsed["agent_summary"] == "Capacity validation blocked the booking."
    assert parsed["resolution_notes"] == "Call the traveler after checking alternatives."
    assert parsed["step"] == "create_handoff"
    assert "metadata" not in parsed


def test_handoff_notes_parser_handles_legacy_context_blob() -> None:
    from app.routes.handoffs import _parse_handoff_notes

    context = json.dumps({
        "reason_code": "duplicate_phone_match",
        "reason_text": "This WhatsApp number matched multiple travelers.",
        "customer_summary": "My number is 01012345678.",
        "agent_summary": "Identity lookup found more than one profile.",
        "metadata": {"current_step": "run_traveler_lookup"},
    }, separators=(",", ":"))
    raw = (
        "This WhatsApp number matched multiple travelers.\n\n"
        "Recommended actions:\n- Confirm the correct traveler profile.\n\n"
        "Employee notes:\nAlready called once.\n\n"
        f"handoff_context={context}"
    )

    parsed = _parse_handoff_notes(raw, reason="", flow_key="identity")

    assert parsed["reason_code"] == "duplicate_phone_match"
    assert parsed["display_notes"] == (
        "This WhatsApp number matched multiple travelers.\n\n"
        "Recommended actions:\n- Confirm the correct traveler profile."
    )
    assert parsed["resolution_notes"] == "Already called once."
    assert parsed["step"] == "run_traveler_lookup"


def test_handoff_page_renders_structured_modal_data_without_raw_notes(handoff_app) -> None:
    app, db = handoff_app
    from app.models import HandoffQueue, Traveler

    raw_notes = json.dumps({
        "notes": "Call after inventory review.",
        "reason_code": "room_capacity",
        "reason_text": "Girls double rooms are unavailable.",
        "customer_summary": "We are 2 boys and 2 girls.",
        "agent_summary": "The agent was checking gendered double-room capacity.",
        "metadata": {
            "session_id": "handoff-session-1",
            "validation": {"action": "create_handoff", "decision": "APPROVED"},
        },
    })
    with app.app_context():
        db.session.add(Traveler(traveler_id="TR-HAND-1", full_name="Handoff Traveler", whatsapp_raw="01000000000"))
        db.session.add(HandoffQueue(
            handoff_id="H-CLARITY1",
            traveler_id="TR-HAND-1",
            lead_id="L-HAND-1",
            trip_id="RT-HAND-1",
            flow_key="tool_calling:handoff-session-1",
            reason="Girls double rooms are unavailable.",
            priority="High",
            channel="web",
            status="Pending",
            notes=raw_notes,
            idempotency_key="handoff:TR-HAND-1:room_capacity:handoff-session-1",
        ))
        db.session.commit()

    html = app.test_client().get("/admin/handoffs/").get_data(as_text=True)

    assert "Why This Escalated" in html
    assert "What The Customer Said" in html
    assert "What The Agent Was Doing" in html
    assert "room_capacity" in html
    assert "We are 2 boys and 2 girls." in html
    assert "Call after inventory review." in html
    assert "/travelers/TR-HAND-1" in html
    assert "/leads/L-HAND-1" in html
    assert "/trips/RT-HAND-1" in html
    assert "/interactions/sessions/handoff-session-1" in html
    assert '"metadata"' not in html
    assert '"validation"' not in html


def test_resolution_notes_update_preserves_structured_handoff_context(handoff_app) -> None:
    app, db = handoff_app
    from app.models import HandoffQueue

    raw_notes = json.dumps({
        "notes": "",
        "reason_code": "customer_requested_human",
        "reason_text": "Customer asked for an agent.",
        "customer_summary": "Please let me talk to someone.",
        "agent_summary": "The customer requested a human.",
        "metadata": {"session_id": "handoff-session-2"},
    })
    with app.app_context():
        db.session.add(HandoffQueue(
            handoff_id="H-RESOLVE1",
            reason="Customer asked for an agent.",
            priority="Medium",
            status="Pending",
            notes=raw_notes,
        ))
        db.session.commit()

    response = app.test_client().put(
        "/admin/handoffs/H-RESOLVE1",
        json={"status": "Resolved", "resolution_notes": "Called and confirmed next steps."},
    )

    assert response.status_code == 200
    with app.app_context():
        handoff = db.session.get(HandoffQueue, "H-RESOLVE1")
        payload = json.loads(handoff.notes)
        assert handoff.status == "Resolved"
        assert payload["reason_code"] == "customer_requested_human"
        assert payload["metadata"]["session_id"] == "handoff-session-2"
        assert payload["notes"] == "Called and confirmed next steps."
        assert payload["resolution_notes"] == "Called and confirmed next steps."
