from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from services.ai_agent.ai_agent_app.agent.gemini_agent import GeminiAgent
from services.ai_agent.ai_agent_app.agent.session_flow import SessionState
from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
from services.ai_agent.ai_agent_app.agent.tool_registry import build_agent_tool_registry
from services.ai_agent.llm.gemini_provider import GeminiProviderError
from tests.test_agent_conversation_reliability import (
    FailingAgent,
    PassiveAgent,
    RecordingReadTools,
    TRIPS,
    _selected_trip_session,
    _send,
)
from tests.test_phase11_demo_features import _make_app_with_db
from tests.test_phase2_gemini_tool_loop import LoopProviderStub, function_call_response, text_response


class _SilentReadTools(RecordingReadTools):
    service = None

    def search_traveler(self, **_kwargs) -> dict:
        return {"match_status": "not_found", "traveler": None}

    def lookup_lead(self, **_kwargs) -> dict:
        return {"leads": []}

    def get_traveler_profile(self, **_kwargs) -> dict:
        return {"traveler": None}

    def get_traveler_profile_safe(self, **_kwargs) -> dict:
        return {"traveler": None}

    def get_traveler_trip_history(self, **_kwargs) -> dict:
        return {"bookings": []}

    def get_booking_status(self, **_kwargs) -> dict:
        return {"bookings": []}

    def get_trip_media(self, **_kwargs) -> dict:
        return {"media": []}


@pytest.fixture()
def audit_runtime(tmp_path: Path):
    original_env = dict(os.environ)
    os.environ["AI_AGENT_MODE"] = "tool_calling"
    _client, app = _make_app_with_db(tmp_path / uuid.uuid4().hex)
    settings = replace(app.config["SETTINGS"], ai_provider="none", gemini_api_key="")
    runtime = ToolCallingSessionRuntime(settings=settings, conversation_ai=PassiveAgent())
    runtime._read_only_tools = RecordingReadTools()
    runtime._write_executor = Mock()
    try:
        yield runtime
    finally:
        os.environ.clear()
        os.environ.update(original_env)
        shutil.rmtree(tmp_path, ignore_errors=True)


def _snapshot(session: SessionState) -> dict[str, object]:
    return {
        "stage": session.stage,
        "selected_trip_id": session.selected_trip_id,
        "trip_type": session.trip_type,
        "room_group": session.room_group,
        "room_type": session.room_type,
        "group_size": session.group_size,
        "currency": session.currency,
        "booking_completed": session.booking_completed,
        "tools_used": tuple(session.tools_used),
        "messages": len(session.messages),
    }


def _changed(before: dict[str, object], after: dict[str, object]) -> set[str]:
    return {key for key, value in before.items() if after[key] != value}


@pytest.mark.parametrize(
    ("label", "setup", "message", "expected_stage", "expected_changed"),
    [
        (
            "normal_capture",
            lambda rt: _selected_trip_session(rt, TRIPS[1]),
            "boys",
            "room_type_required",
            {"stage", "room_group", "messages"},
        ),
        (
            "invalid_capture",
            lambda rt: _selected_trip_session(rt, TRIPS[1]),
            "neither option",
            "traveler_gender_required",
            {"messages"},
        ),
        (
            "side_question",
            lambda rt: _selected_trip_session(rt, TRIPS[1]),
            "why do you need that?",
            "traveler_gender_required",
            {"messages"},
        ),
        (
            "mixed_language_capture",
            lambda rt: _selected_trip_session(rt, TRIPS[1]),
            "boys لو سمحت",
            "room_type_required",
            {"stage", "room_group", "messages"},
        ),
    ],
)
def test_phase8_one_customer_turn_has_bounded_state_transition(
    audit_runtime: ToolCallingSessionRuntime,
    label,
    setup,
    message,
    expected_stage,
    expected_changed,
) -> None:
    session = setup(audit_runtime)
    before = _snapshot(session)

    session = _send(audit_runtime, message, session)

    after = _snapshot(session)
    assert session.stage == expected_stage, label
    assert _changed(before, after) <= expected_changed, label
    assert audit_runtime._write_executor.execute.call_count == 0


def test_phase8_provider_failure_does_not_mutate_confirmed_workflow_state(
    audit_runtime: ToolCallingSessionRuntime,
) -> None:
    audit_runtime._conversation_ai = FailingAgent()
    session = _selected_trip_session(audit_runtime, TRIPS[1])
    session.stage = "waiting"
    before = _snapshot(session)

    session = _send(audit_runtime, "tell me more about the payment options", session)

    after = _snapshot(session)
    assert after["selected_trip_id"] == before["selected_trip_id"]
    assert after["trip_type"] == before["trip_type"]
    assert after["booking_completed"] is False
    assert audit_runtime._write_executor.execute.call_count == 0


def test_phase8_classifier_failure_falls_back_without_state_mutation(tmp_path: Path) -> None:
    _client, app = _make_app_with_db(tmp_path / uuid.uuid4().hex)
    settings = replace(app.config["SETTINGS"], ai_provider="gemini", gemini_api_key="fake")
    provider = LoopProviderStub([text_response("not json")])
    agent = GeminiAgent(settings=settings, provider=provider, read_only_tools=_SilentReadTools())

    decision = agent.classify_off_script_turn(
        user_message="is this family friendly?",
        session_context={
            "session_id": "phase8-classifier",
            "workflow_policy": {"required_step": "collect_room_type"},
            "selected_trip_name": "Siwa Discovery Demo",
            "trip_type": "local",
        },
    )

    assert decision == {"category": "unclear", "target_hint": "", "confidence": 0.0}


@pytest.mark.parametrize("tool_name", ["create_traveler", "update_traveler", "update_booking", "upload_passport"])
def test_phase8_llm_cannot_directly_reach_deterministic_write_executor_tools(tmp_path: Path, tool_name: str) -> None:
    _client, app = _make_app_with_db(tmp_path / uuid.uuid4().hex)
    settings = replace(app.config["SETTINGS"], ai_provider="gemini", gemini_api_key="fake")
    write_executor = Mock()
    provider = LoopProviderStub([function_call_response(tool_name, {"traveler_id": "TR100"})])
    agent = GeminiAgent(
        settings=settings,
        provider=provider,
        read_only_tools=_SilentReadTools(),
        write_tools_enabled=True,
        tool_registry=build_agent_tool_registry(include_write_tools=True, include_validation_tool=False),
    )
    agent.write_executor = write_executor

    result = agent.respond(
        user_message="please update it",
        session_context={"session_id": f"phase8-{tool_name}", "language": "en"},
    )

    assert result["error"] == "tool_request_failed"
    assert result["tool_requests"] == []
    write_executor.execute.assert_not_called()


def test_phase8_allowed_tools_blocks_registered_write_before_action_validator(tmp_path: Path) -> None:
    _client, app = _make_app_with_db(tmp_path / uuid.uuid4().hex)
    settings = replace(
        app.config["SETTINGS"],
        ai_provider="gemini",
        gemini_api_key="fake",
        agent_write_tool_enforcement=True,
        agent_tool_router_mode="enforce",
    )
    write_executor = Mock()
    provider = LoopProviderStub(
        [
            function_call_response(
                "create_booking_draft",
                {"traveler_id": "TR100", "trip_id": "RT-LOC-26-900", "room_type": "Single"},
            ),
            text_response("I cannot create a booking yet."),
        ]
    )
    agent = GeminiAgent(
        settings=settings,
        provider=provider,
        read_only_tools=_SilentReadTools(),
        write_tools_enabled=True,
        tool_registry=build_agent_tool_registry(include_write_tools=True, include_validation_tool=False),
    )
    agent.write_executor = write_executor

    result = agent.respond(
        user_message="book it now",
        session_context={
            "session_id": "phase8-allowed-tools",
            "language": "en",
            "stage": "identity_required",
            "workflow_policy": {
                "state": "identity_required",
                "required_step": "collect_whatsapp_number",
                "allowed_tools": ["find_traveler_by_phone"],
                "assistant_message": "Please share your WhatsApp number first.",
            },
        },
    )

    assert result["tool_requests"][0]["name"] == "create_booking_draft"
    assert result["tool_requests"][0]["executed"] is False
    write_executor.execute.assert_not_called()


def test_phase8_duplicate_instagram_webhook_delivery_is_deduplicated(tmp_path: Path) -> None:
    original_env = dict(os.environ)
    os.environ["META_APP_SECRET"] = "phase8-meta-secret"
    os.environ["META_VERIFY_TOKEN"] = "phase8-verify"
    try:
        client, _app = _make_app_with_db(tmp_path / uuid.uuid4().hex)
        payload = {
            "object": "instagram",
            "entry": [
                {
                    "messaging": [
                        {
                            "sender": {"id": "igsid-phase8"},
                            "recipient": {"id": "page-phase8"},
                            "timestamp": 1760000000000,
                            "message": {"mid": "phase8-mid-1", "text": "I want a trip"},
                        }
                    ]
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        signature = hmac.new(os.environ["META_APP_SECRET"].encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers = {"Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={signature}"}

        first = client.post("/rahma-agent/webhook", data=body, headers=headers).get_json()
        second = client.post("/rahma-agent/webhook", data=body, headers=headers).get_json()

        assert first["persisted"] == 1
        assert first["duplicates"] == 0
        assert second["persisted"] == 0
        assert second["duplicates"] == 1
    finally:
        os.environ.clear()
        os.environ.update(original_env)
