"""Tier 2 gap-remediation fixes: strict full-name validation (2.1), nationality
validation against a maintained reference list + currency-code rejection (2.2),
and conversational passport number/expiry/country collection with expiry
validation (2.3, reworked from the spec's OCR-based version since this codebase
has no OCR -- see conversation for the scoping decision).
"""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from services.ai_agent.ai_agent_app.agent.date_parsing import add_months
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


# ---------------------------------------------------------------------------
# Task 2.1 -- full name validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "expected_name"),
    [
        ("Mohamed Ashraf Safwat", "Mohamed Ashraf Safwat"),
        ("Al Ali Ahmed", "Al Ali Ahmed"),  # 2-char tokens must be accepted, not just 3+
    ],
)
def test_valid_names_are_accepted(text: str, expected_name: str) -> None:
    name, reason = ToolCallingSessionRuntime._validate_name_tokens(text)
    assert name == expected_name
    assert reason == ""


@pytest.mark.parametrize("text", ["seko seko seko", "SEKO SEKO SEKO", "محمد محمد محمد"])
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
    reply = session.messages[-1]["text"]
    assert "doesn't look like a full name" in reply

    session = _send(runtime, "Mohamed Ashraf Safwat", session)
    assert session.customer_name == "Mohamed Ashraf Safwat"


# ---------------------------------------------------------------------------
# Task 2.2 -- nationality validation
# ---------------------------------------------------------------------------

def test_recognized_nationalities_resolve_in_english_and_arabic() -> None:
    assert resolve_nationality("Egyptian") == "Egyptian"
    assert resolve_nationality("egyptian") == "Egyptian"
    assert resolve_nationality("مصري") == "Egyptian"


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
    reply = session.messages[-1]["text"]
    assert "currency" in reply.lower()

    session = _send(runtime, "USD", session)
    assert session.nationality == ""
    reply = session.messages[-1]["text"]
    assert "currency" in reply.lower()


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
    assert "currency" not in reply  # distinct from the currency-specific message
    assert "don't recognize" in reply or "recognize" in reply

    session = _send(runtime, "Egyptian", session)
    assert session.nationality == "Egyptian"


# ---------------------------------------------------------------------------
# Task 2.3 -- conversational passport number/expiry/country + expiry validation
# ---------------------------------------------------------------------------

def _passport_ready_session(rt: ToolCallingSessionRuntime) -> SessionState:
    trip = dict(TRIPS[0])  # RT-INT-26-001, international, start_date 2026-10-01
    session = _selected_trip_session(rt, trip)
    session.room_group = "boys"
    session.room_type = "Single"
    session.group_size = 1
    session.flight_option = "Without Flight"
    session.passport_attachment_ref = "passport/scan.jpg"
    session.preview["collection_state"].update(
        {"room_group": True, "room_type": True, "group_size": True, "flight_option": True}
    )
    session.stage = "passport_number_required"
    return session


def test_passport_expiry_in_the_past_is_rejected_not_silently_stored(runtime: ToolCallingSessionRuntime) -> None:
    session = _passport_ready_session(runtime)
    session = _send(runtime, "A1234567", session)
    assert session.stage == "passport_expiry_required"

    session = _send(runtime, "01/01/2020", session)
    assert session.passport_expiry == ""
    assert session.stage == "passport_expiry_required"
    reply = session.messages[-1]["text"].lower()
    assert "expired" in reply or "past" in reply


def test_passport_expiry_within_six_months_of_trip_is_flagged_not_blocked(runtime: ToolCallingSessionRuntime) -> None:
    session = _passport_ready_session(runtime)
    session = _send(runtime, "A1234567", session)

    # Trip starts 2026-10-01; an expiry a few weeks after that is valid (in the
    # future) but well under the 6-month runway airlines/countries often require.
    session = _send(runtime, "15/11/2026", session)
    assert session.passport_expiry == "2026-11-15"
    assert session.stage == "passport_country_required"
    reply = session.messages[-1]["text"].lower()
    assert "6 month" in reply or "6 أشهر" in session.messages[-1]["text"]


def test_passport_expiry_comfortably_valid_passes_silently(runtime: ToolCallingSessionRuntime) -> None:
    session = _passport_ready_session(runtime)
    session = _send(runtime, "A1234567", session)

    session = _send(runtime, "01/06/2032", session)
    assert session.passport_expiry == "2032-06-01"
    assert session.stage == "passport_country_required"
    reply = session.messages[-1]["text"].lower()
    assert "6 month" not in reply


def test_unparseable_passport_expiry_is_reasked_not_silently_stored_as_null(runtime: ToolCallingSessionRuntime) -> None:
    session = _passport_ready_session(runtime)
    session = _send(runtime, "A1234567", session)

    session = _send(runtime, "whenever it expires", session)
    assert session.passport_expiry == ""
    assert session.stage == "passport_expiry_required"


def test_passport_country_mismatch_with_stated_nationality_does_not_block(runtime: ToolCallingSessionRuntime) -> None:
    session = _passport_ready_session(runtime)
    session.nationality = "Egyptian"
    session = _send(runtime, "A1234567", session)
    session = _send(runtime, "01/06/2032", session)
    assert session.stage == "passport_country_required"

    session = _send(runtime, "Saudi", session)
    assert session.passport_nationality == "Saudi"
    # The point of this test is that a passport country differing from the
    # stated nationality must not BLOCK the booking. The workflow legitimately
    # collects payment currency before booking confirmation, so assert it moved
    # on to that remaining step rather than getting stuck re-asking passport
    # country, then finish the flow to prove confirmation is still reachable.
    assert session.stage == "currency_required"

    session = _send(runtime, "EGP", session)
    assert session.stage == "booking_confirmation_required"


if __name__ == "__main__":
    import unittest

    unittest.main()
