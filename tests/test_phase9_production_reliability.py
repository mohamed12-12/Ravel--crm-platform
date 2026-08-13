from __future__ import annotations

import os
import shutil
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from services.ai_agent.ai_agent_app.agent.session_store import SessionLockBusy
from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
from services.crm.system_services import UnifiedCRMService
from services.crm.system_services.config import load_system_settings
from tests.test_agent_conversation_reliability import PassiveAgent, RecordingReadTools
from tests.test_phase11_demo_features import _make_app_with_db


@pytest.fixture()
def reliability_runtime(tmp_path: Path):
    original_env = dict(os.environ)
    os.environ["AI_AGENT_MODE"] = "tool_calling"
    os.environ["AI_AGENT_SESSION_LOCK_WAIT_SECONDS"] = "1"
    _client, app = _make_app_with_db(tmp_path / uuid.uuid4().hex)
    settings = replace(app.config["SETTINGS"], ai_provider="none", gemini_api_key="")
    runtime = ToolCallingSessionRuntime(settings=settings, conversation_ai=PassiveAgent())
    runtime._read_only_tools = RecordingReadTools()
    runtime._write_executor = Mock()
    try:
        yield runtime, settings
    finally:
        os.environ.clear()
        os.environ.update(original_env)
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_phase9_session_state_recovers_after_process_restart(reliability_runtime) -> None:
    runtime, settings = reliability_runtime
    session = runtime.create_session()

    restored = runtime.handle_message_by_id(session.id, "01112223333", gateway=None)
    assert restored is not None
    assert restored.raw_phone == "01112223333"

    new_runtime = ToolCallingSessionRuntime(settings=settings, conversation_ai=PassiveAgent())
    recovered = new_runtime.get(session.id)

    assert recovered is not None
    assert recovered.id == session.id
    assert recovered.raw_phone == restored.raw_phone
    assert recovered.stage == restored.stage
    assert recovered.messages[-1]["role"] == "assistant"


def test_phase9_session_lock_is_cross_runtime_and_durable(reliability_runtime) -> None:
    runtime, settings = reliability_runtime
    session = runtime.create_session()
    other_runtime = ToolCallingSessionRuntime(settings=settings, conversation_ai=PassiveAgent())
    other_runtime._session_store.wait_seconds = 0.05

    with runtime._session_store.session_lock(session.id):
        with pytest.raises(SessionLockBusy):
            other_runtime.handle_message_by_id(session.id, "01112223333", gateway=None)


def test_phase9_concurrent_same_session_messages_are_serialized(reliability_runtime) -> None:
    runtime, _settings = reliability_runtime
    session = runtime.create_session()
    original_handle = runtime.handle_message
    first_entered = threading.Event()
    release_first = threading.Event()
    call_count = 0
    call_lock = threading.Lock()

    def slow_first_handle(session_obj, text, gateway):
        nonlocal call_count
        with call_lock:
            call_count += 1
            is_first = call_count == 1
        if is_first:
            first_entered.set()
            assert release_first.wait(timeout=5)
        return original_handle(session_obj, text, gateway)

    runtime.handle_message = slow_first_handle  # type: ignore[method-assign]

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(runtime.handle_message_by_id, session.id, "01112223333", None)
        assert first_entered.wait(timeout=5)
        second = pool.submit(runtime.handle_message_by_id, session.id, "Maged Samir Adly", None)
        release_first.set()
        first_result = first.result(timeout=10)
        second_result = second.result(timeout=10)

    assert first_result is not None
    assert second_result is not None
    recovered = runtime.get(session.id, refresh=True)
    assert recovered is not None
    assert recovered.raw_phone == "01112223333"
    assert recovered.customer_name == "Maged Samir Adly"
    assert len([message for message in recovered.messages if message.get("role") == "user"]) == 2


def test_phase9_retry_after_session_persistence_failure_does_not_duplicate_write(reliability_runtime) -> None:
    runtime, _settings = reliability_runtime
    session = runtime.create_session()
    runtime._write_executor.execute.side_effect = [
        {
            "executed": True,
            "result_id": "BK000001",
            "booking_id": "BK000001",
            "booking_status": "Draft",
            "write_result_contract": {
                "status": "success",
                "executed": True,
                "reused": False,
                "record_type": "booking",
                "record_id": "BK000001",
            },
        },
        {
            "executed": False,
            "result_id": "BK000001",
            "booking_id": "BK000001",
            "booking_status": "Draft",
            "write_result_contract": {
                "status": "duplicate",
                "executed": False,
                "reused": True,
                "record_type": "booking",
                "record_id": "BK000001",
            },
        },
    ]
    session.raw_phone = "01112223333"
    session.country_code = "20"
    session.customer_name = "Mona Ali"
    session.trip_type = "local"
    session.selected_trip_id = "RT-LOC-26-900"
    session.selected_trip_name = "Siwa Discovery Demo"
    session.room_group = "boys"
    session.room_type = "Single"
    session.group_size = 1
    session.currency = "EGP"
    session.booking_confirmed = True
    session.stage = "booking_confirmation_required"
    session.preview = {
        "traveler": {"traveler_id": "TR100", "full_name": "Mona Ali", "status": "Active"},
        "workflow": {
            "identity_verified": True,
            "verified_traveler": {"traveler_id": "TR100", "full_name": "Mona Ali", "status": "Active"},
            "verified_status": "Active",
        },
        "trip_result": {"open_trips": [RecordingReadTools().trips[1]], "date_tbd_trips": []},
        "trip_reference": RecordingReadTools().trips[1],
        "collection_state": {
            "trip_type": True,
            "selected_trip": True,
            "room_group": True,
            "room_type": True,
            "group_size": True,
            "currency": True,
        },
    }
    runtime._read_only_tools.identity = {"traveler_id": "TR100", "full_name": "Mona Ali", "status": "Active"}
    runtime._persist_session(session)

    real_save = runtime._session_store.save
    fail_once = {"done": False}

    def flaky_save(*args, **kwargs):
        if not fail_once["done"]:
            fail_once["done"] = True
            raise RuntimeError("simulated persistence failure after CRM write")
        return real_save(*args, **kwargs)

    runtime._session_store.save = flaky_save  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="simulated persistence failure"):
        runtime.handle_message_by_id(session.id, "yes", gateway=None)

    runtime._session_store.save = real_save  # type: ignore[method-assign]
    retry_result = runtime.handle_message_by_id(session.id, "yes", gateway=None)

    assert retry_result is not None
    assert runtime._write_executor.execute.call_count == 2
    assert runtime._write_executor.execute.call_args_list[1].kwargs["action"] == "create_booking_draft"
    assert retry_result.final_result["booking_id"] == "BK000001"
    assert retry_result.stage == "post_booking_support"


def test_phase9_webhook_message_key_has_schema_level_uniqueness(tmp_path: Path) -> None:
    original_env = dict(os.environ)
    try:
        _make_app_with_db(tmp_path / uuid.uuid4().hex)
        db_path = Path(os.environ["RAHMA_SYSTEM_DB_PATH"])
        service = UnifiedCRMService(load_system_settings())
        service.ensure_operational_schema()
        first = service.record_inbound_channel_event(
            channel="Instagram",
            message_key="phase9-mid",
            sender_id="igsid-1",
            text="hello",
        )
        second = service.record_inbound_channel_event(
            channel="Instagram",
            message_key="phase9-mid",
            sender_id="igsid-1",
            text="hello again",
        )
        assert first["created"] is True
        assert second["created"] is False
        with sqlite3.connect(db_path) as connection:
            duplicate_insert_failed = False
            try:
                connection.execute(
                    """
                    INSERT INTO interactions (
                        interaction_id, channel, message_key
                    ) VALUES (?, ?, ?)
                    """,
                    ("INT-PHASE9-DUP", "Instagram", "phase9-mid"),
                )
            except sqlite3.IntegrityError:
                duplicate_insert_failed = True
        assert duplicate_insert_failed is True
    finally:
        os.environ.clear()
        os.environ.update(original_env)
