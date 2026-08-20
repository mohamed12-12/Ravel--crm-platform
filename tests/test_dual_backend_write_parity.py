"""Parametrized dual-backend parity tests (Phase 4 / Task B.1).

For every shared write action, run the same logical scenario against both
UnifiedCRMService (SQLite) and PostgresAgentBridgeService (Postgres bridge --
exercised here by pointing the same Flask app at a SQLite-backed engine
after construction, so the ORM-level code path is real even though the SQL
dialect isn't; case-sensitivity-specific divergences are covered separately
by test_postgres_agent_bridge.py's own regression test) and assert the
*normalized* outcome is identical. This is what would have caught the
"created" vs "success" status divergence (Section 3.10 of the discovery
report) before it ever reached production.

Each backend's Flask app is created, exercised, and FULLY torn down
(engine disposed, sys.modules/os.environ restored to an exact snapshot)
before the other backend's app is ever created -- the two are never alive
at the same time in this process. An earlier version of this file kept
both apps alive simultaneously and, via some combination of SQLAlchemy's
per-app declarative class/metaclass identity and Flask's app-context
stack, that corrupted later, unrelated test files' own fresh `from app
import ...` (test_phase4_traveler_management.py etc. would see writes
"succeed" -- 302 -- but their own subsequent query would return 0 rows).
Never repeat that pattern without full sequential teardown.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest

from services.ai_agent.ai_agent_app.agent.write_result import (
    WriteOutcome,
    normalize_write_result,
)

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))


SCENARIOS = [
    (
        "create_traveler",
        lambda: {"full_name": "New Parity Person", "raw_phone": f"010{uuid.uuid4().hex[:8]}", "nationality": "Egyptian"},
    ),
    (
        "create_lead",
        lambda: {"traveler_id": "TRPAR001", "customer_name": "Parity Traveler", "raw_phone": "01099998888", "preferred_trip_type": "Local"},
    ),
    (
        "update_lead_stage",
        lambda: {"lead_id": "LDPAR001", "requested_stage": "Qualified"},
    ),
    (
        "create_booking_draft",
        lambda: {"traveler_id": "TRPAR001", "trip_id": "RTPAR001", "room_type": "Single", "channel": "web", "lead_id": "LDPAR001"},
    ),
    (
        "create_handoff",
        lambda: {"traveler_id": "TRPAR001", "reason_code": "customer_requested_human", "reason_text": "Parity test handoff.", "user_requested_human": True},
    ),
    (
        "set_guardian_consent",
        lambda: {"traveler_id": "TRPAR001", "is_minor": True, "guardian_name": "Parity Guardian", "guardian_phone": "01000000001"},
    ),
    (
        # Live-bug regression: apps/api/app/routes/crm.py's AGENT_WRITE_ACTIONS
        # (the /api/crm/agent/write allowlist) never included this action even
        # though every other layer (validator, workflow policy, write
        # executor, PostgresAgentBridgeService) already implemented it --
        # every real private-trip save in CRM_ACCESS_MODE=api production
        # failed with a 422 before ever reaching the write code. This
        # scenario would have failed on `assert response.status_code == 200`
        # before that fix.
        "create_private_trip_request",
        lambda: {"traveler_id": "TRPAR001", "service_type": "consultation", "trip_scope": "Local", "destination": "Parity Destination", "party_size": 2},
    ),
    (
        "flag_lead_guardian_approval",
        lambda: {"lead_id": "LDPAR001", "requires_guardian_approval": True},
    ),
]


def _run_scenario_against_backend(tmpdir: Path, *, as_postgres: bool, action: str, payload: dict, session_id: str) -> dict:
    """Create one Flask app, run one write, and fully tear it down before
    returning -- never leaves a live app/engine/module state behind.
    """
    original_env = dict(os.environ)
    original_app_modules = {
        name: module for name, module in sys.modules.items() if name == "app" or name.startswith("app.")
    }
    try:
        for name in list(sys.modules):
            if name == "app" or name.startswith("app."):
                del sys.modules[name]

        db_path = (tmpdir / "parity.db").resolve()
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(db_path)
        os.environ["DATA_AUTHORITY"] = "crm"
        os.environ["CRM_ACCESS_MODE"] = "shared_service"
        os.environ["CRM_AUTH_ENABLED"] = "false"

        from app import create_app
        from app.extensions import db
        from app.models import Lead, Traveler, Trip

        app = create_app("development")
        app.config.update(TESTING=True)
        with app.app_context():
            db.drop_all()
            db.create_all()
            db.session.add(
                Traveler(
                    traveler_id="TRPAR001",
                    full_name="Parity Traveler",
                    status="Active",
                    whatsapp_raw="01099998888",
                    integrated_whatsapp="+201099998888",
                    normalized_whatsapp="+201099998888",
                    phone_lookup_key="20:1099998888",
                )
            )
            db.session.add(
                Trip(
                    trip_id="RTPAR001",
                    trip_name="Parity Trip",
                    type="Local",
                    sales_status="Open",
                    start_date=date.today() + timedelta(days=10),
                    end_date=date.today() + timedelta(days=13),
                    single_total=5,
                    single_remaining=5,
                    double_total=5,
                    double_remaining=5,
                    triple_total=5,
                    triple_remaining=5,
                    public_price="$100",
                )
            )
            db.session.add(
                Lead(
                    lead_id="LDPAR001",
                    customer_name="Parity Traveler",
                    raw_phone="01099998888",
                    traveler_id="TRPAR001",
                    lead_stage="New Lead",
                )
            )
            db.session.commit()

        if as_postgres:
            app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://staging/redacted"

        client = app.test_client()
        session_context_extra = {"booking_confirmed": True} if action == "create_booking_draft" else {}
        response = client.post(
            "/api/crm/agent/write",
            json={
                "action": action,
                "payload": payload,
                "session_context": {"session_id": session_id, "language": "en", **session_context_extra},
            },
        )
        assert response.status_code == 200, response.get_json()
        result = response.get_json()["result"]

        with app.app_context():
            db.session.remove()
            db.engine.dispose()

        return result
    finally:
        for name in list(sys.modules):
            if name == "app" or name.startswith("app."):
                del sys.modules[name]
        sys.modules.update(original_app_modules)
        os.environ.clear()
        os.environ.update(original_env)


@pytest.fixture()
def scenario_tmpdirs(tmp_path: Path):
    sqlite_dir = tmp_path / "sqlite"
    postgres_dir = tmp_path / "postgres"
    sqlite_dir.mkdir(parents=True, exist_ok=True)
    postgres_dir.mkdir(parents=True, exist_ok=True)
    return sqlite_dir, postgres_dir


@pytest.mark.parametrize("action,payload_builder", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_write_action_normalizes_to_the_same_outcome_on_both_backends(scenario_tmpdirs, action, payload_builder):
    sqlite_dir, postgres_dir = scenario_tmpdirs
    session_id = f"parity-{action}-{uuid.uuid4().hex[:8]}"
    payload = payload_builder()

    sqlite_result = _run_scenario_against_backend(sqlite_dir, as_postgres=False, action=action, payload=payload, session_id=session_id)
    postgres_result = _run_scenario_against_backend(postgres_dir, as_postgres=True, action=action, payload=payload, session_id=session_id)

    sqlite_outcome = normalize_write_result(sqlite_result, backend="sqlite")
    postgres_outcome = normalize_write_result(postgres_result, backend="postgres")

    assert sqlite_outcome.outcome is WriteOutcome.SUCCESS, (action, sqlite_result)
    assert postgres_outcome.outcome is WriteOutcome.SUCCESS, (action, postgres_result)
    assert sqlite_outcome.outcome == postgres_outcome.outcome
