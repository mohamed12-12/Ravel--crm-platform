"""Tier 2 gap-remediation fixes.

Covers strict full-name validation (2.1), nationality validation against the
reference list plus currency-code rejection (2.2), and attachment-only passport
collection (2.3).
"""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from services.ai_agent.ai_agent_app.agent.nationality_reference import looks_like_currency_code, resolve_nationality
from services.ai_agent.ai_agent_app.agent.session_flow import SessionState
from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
from tests.test_agent_conversation_reliability import TRIPS, PassiveAgent, RecordingReadTools, _selected_trip_session
from tests.test_phase11_demo_features import _make_app_with_db


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


@pytest.mark.parametrize(
    ("text", "expected_name"),
    [
        ("Mohamed Ashraf Safwat", "Mohamed Ashraf Safwat"),
        ("Al Ali Ahmed", "Al Ali Ahmed"),
    ],
)
def test_valid_names_are_accepted(text: str, expected_name: str) -> None:
    name, reason = ToolCallingSessionRuntime._validate_name_tokens(text)
    assert name == expected_name
    assert reason == ""


@pytest.mark.parametrize(
    "text",
    [
        "seko seko seko",
        "SEKO SEKO SEKO",
        "\u0645\u062d\u0645\u062f \u0645\u062d\u0645\u062f \u0645\u062d\u0645\u062f",
    ],
)
def test_all_identical_tokens_are_rejected_regardless_of_case_or_language(text: str) -> None:
    name, reason = ToolCallingSessionRuntime._validate_name_tokens(text)
    assert name == ""
    assert reason == "repeated_tokens"


def test_new_traveler_name_rejection_gives_a_specific_reason_not_a_generic_reask(runtime: ToolCallingSessionRuntime) -> None:
    session = runtime.create_session()
    session = _send(runtime, "01270482380", session)
    assert session.stage == "traveler_not_found"

    session = _send(runtime, "Mohamed Mohamed Mohamed", session)
    assert session.customer_name == ""
    assert session.stage == "traveler_not_found"
    assert "doesn't look like a full name" in session.messages[-1]["text"]

    session = _send(runtime, "Mohamed Ashraf Safwat", session)
    assert session.customer_name == "Mohamed Ashraf Safwat"


def test_recognized_nationalities_resolve_in_english_and_arabic() -> None:
    assert resolve_nationality("Egyptian") == "Egyptian"
    assert resolve_nationality("egyptian") == "Egyptian"
    assert resolve_nationality("\u0645\u0635\u0631\u064a") == "Egyptian"


def test_currency_codes_are_detected_as_such() -> None:
    assert looks_like_currency_code("EGP") is True
    assert looks_like_currency_code("usd") is True
    assert looks_like_currency_code("asdkfj") is False


def test_currency_code_typed_as_nationality_gets_the_currency_specific_message(runtime: ToolCallingSessionRuntime) -> None:
    session = runtime.create_session()
    for text in ("01270482380", "Mohamed Ashraf Safwat"):
        session = _send(runtime, text, session)
    assert session.stage == "nationality_required"

    session = _send(runtime, "EGP", session)
    assert session.nationality == ""
    assert "currency" in session.messages[-1]["text"].lower()

    session = _send(runtime, "USD", session)
    assert session.nationality == ""
    assert "currency" in session.messages[-1]["text"].lower()


def test_unrecognized_nationality_is_rejected_with_a_generic_message_not_silently_accepted(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = runtime.create_session()
    for text in ("01270482380", "Mohamed Ashraf Safwat"):
        session = _send(runtime, text, session)
    assert session.stage == "nationality_required"

    session = _send(runtime, "asdkfj", session)
    assert session.nationality == ""
    reply = session.messages[-1]["text"].lower()
    assert "currency" not in reply
    assert "don't recognize" in reply or "recognize" in reply

    session = _send(runtime, "Egyptian", session)
    assert session.nationality == "Egyptian"


def _passport_attachment_required_session(rt: ToolCallingSessionRuntime) -> SessionState:
    trip = dict(TRIPS[0])
    session = _selected_trip_session(rt, trip)
    session.room_group = "boys"
    session.room_type = "Single"
    session.group_size = 1
    session.flight_option = "Without Flight"
    session.preview["collection_state"].update(
        {"room_group": True, "room_type": True, "group_size": True, "flight_option": True}
    )
    session.stage = "awaiting_passport_upload"
    return session


def test_typed_passport_details_do_not_skip_required_attachment(runtime: ToolCallingSessionRuntime) -> None:
    session = _passport_attachment_required_session(runtime)

    session = _send(runtime, "A1234567", session)

    assert session.stage == "awaiting_passport_upload"
    assert session.passport_number == ""
    assert session.passport_expiry == ""
    assert session.passport_nationality == ""


def test_passport_attachment_moves_directly_to_currency(runtime: ToolCallingSessionRuntime) -> None:
    session = _passport_attachment_required_session(runtime)

    runtime.handle_passport_attachment(session, "passport/scan.jpg")
    session = _send(runtime, "done", session)

    assert session.passport_attachment_ref == "passport/scan.jpg"
    assert session.stage == "currency_required"

    session = _send(runtime, "EGP", session)
    assert session.stage == "booking_confirmation_required"
