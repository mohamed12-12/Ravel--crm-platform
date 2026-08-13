from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import text

from services.ai_agent.ai_agent_app.agent.session_flow import SessionState
from services.ai_agent.ai_agent_app.agent.session_store import DurableSessionStore
from services.crm.system_services import UnifiedCRMService
from services.crm.system_services.config import load_system_settings
from tests.test_phase9_production_reliability import reliability_runtime  # noqa: F401  (shared fixture)
from tests.test_phase11_demo_features import _make_app_with_db


def test_phase10_corrupted_session_payload_preserves_the_requested_session_identity(tmp_path: Path) -> None:
    """A corrupted/unparseable payload must not fabricate an unrelated session id.

    Before this fix, DurableSessionStore._session_from_payload() derived the
    returned SessionState's id from the JSON payload alone. An empty/corrupt
    payload (partial write, encoding issue, manual edit) silently produced a
    brand-new random id instead of the row's own primary key, so the very
    next save() forked an orphan row instead of updating the one requested --
    the customer's real session became permanently unrecoverable and a stray
    empty row was left behind forever.
    """

    store = DurableSessionStore(database_url=f"sqlite:///{tmp_path / 'sessions.db'}")
    session = SessionState(id="real-session-id")
    session.customer_name = "Mona Ali"
    store.save(session)

    with store.engine.begin() as connection:
        connection.execute(
            text("UPDATE ai_agent_sessions SET payload = 'not-json' WHERE session_id = :sid"),
            {"sid": "real-session-id"},
        )

    loaded = store.load("real-session-id")
    assert loaded is not None
    recovered_session, _agent_state, _version = loaded
    assert recovered_session.id == "real-session-id"

    # The recovery must reuse the existing row (UPDATE), not fork a new one.
    store.save(recovered_session, expected_version=_version)
    with store.engine.begin() as connection:
        row_count = connection.execute(text("SELECT COUNT(*) FROM ai_agent_sessions")).scalar()
    assert row_count == 1


def test_phase10_passport_attachment_upload_does_not_lose_a_concurrent_turn(reliability_runtime) -> None:
    """The passport-upload endpoint must go through the same session lock and
    version check as a chat turn, or an in-flight turn's committed data can be
    silently reverted by a concurrent, stale, unconditional passport-attachment
    write (a real gap: the old code called _persist_session() with no lock and
    no expected_version at all).
    """

    runtime, _settings = reliability_runtime
    session = runtime.create_session()
    session.stage = "awaiting_passport_upload"
    session.trip_type = "international"
    runtime._persist_session(session)

    # A concurrent, properly locked chat turn captures a field and commits.
    with runtime._session_store.session_lock(session.id):
        turn_session, _agent_state, turn_version = runtime._session_store.load(session.id)
        turn_session.customer_name = "Mona Ali"
        runtime._persist_session(turn_session, expected_version=turn_version)

    # The passport upload request is handled afterward through the locked,
    # versioned helper -- it must see the committed turn, not a stale snapshot.
    updated = runtime.apply_passport_attachment_by_id(session.id, "id.jpg")

    assert updated is not None
    assert updated.customer_name == "Mona Ali"
    assert updated.passport_attachment_ref == "id.jpg"

    recovered = runtime.get(session.id, refresh=True)
    assert recovered.customer_name == "Mona Ali"
    assert recovered.passport_attachment_ref == "id.jpg"


def test_phase10_passport_attachment_upload_honors_an_already_held_session_lock(reliability_runtime) -> None:
    runtime, settings = reliability_runtime
    session = runtime.create_session()
    session.stage = "awaiting_passport_upload"
    session.trip_type = "international"
    runtime._persist_session(session)

    from services.ai_agent.ai_agent_app.agent.session_store import SessionLockBusy
    from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
    from tests.test_agent_conversation_reliability import PassiveAgent

    other_runtime = ToolCallingSessionRuntime(settings=settings, conversation_ai=PassiveAgent())
    other_runtime._session_store.wait_seconds = 0.05

    with runtime._session_store.session_lock(session.id):
        with pytest.raises(SessionLockBusy):
            other_runtime.apply_passport_attachment_by_id(session.id, "id.jpg")


def test_phase10_concurrent_duplicate_webhook_delivery_creates_exactly_one_interaction(tmp_path: Path) -> None:
    """Two threads racing to record the same (channel, message_key) webhook
    delivery must not both succeed in creating an interaction -- the database
    unique index, not the earlier sequential SELECT-then-INSERT check, must be
    the mechanism that actually decides the winner under real concurrency.
    """

    original_env = dict(os.environ)
    try:
        _make_app_with_db(tmp_path / uuid.uuid4().hex)
        service_a = UnifiedCRMService(load_system_settings())
        service_b = UnifiedCRMService(load_system_settings())
        service_a.ensure_operational_schema()

        barrier_ready = {"a": False, "b": False}

        def call(service: UnifiedCRMService, text_suffix: str) -> dict:
            return service.record_inbound_channel_event(
                channel="Instagram",
                message_key="phase10-concurrent-mid",
                sender_id="igsid-concurrent",
                text=f"hello {text_suffix}",
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            future_a = pool.submit(call, service_a, "a")
            future_b = pool.submit(call, service_b, "b")
            result_a = future_a.result(timeout=10)
            result_b = future_b.result(timeout=10)

        created_flags = sorted([result_a["created"], result_b["created"]])
        assert created_flags == [False, True]

        with service_a.connect() as connection:
            row_count = connection.execute(
                "SELECT COUNT(*) AS n FROM interactions WHERE channel = ? AND message_key = ?",
                ("Instagram", "phase10-concurrent-mid"),
            ).fetchone()["n"]
        assert row_count == 1
    finally:
        os.environ.clear()
        os.environ.update(original_env)
