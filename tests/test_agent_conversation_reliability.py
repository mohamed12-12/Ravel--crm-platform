from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from services.ai_agent.ai_agent_app.agent.session_flow import SessionState
from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
from tests.test_phase11_demo_features import _make_app_with_db


TRIPS = [
    {
        "trip_id": "RT-INT-26-001",
        "trip_name": "Istanbul Explorer",
        "type": "International",
        "trip_type": "international",
        "start_date": "2026-10-01",
        "end_date": "2026-10-08",
        "public_price": "2500 USD",
        "public_description": "Verified Istanbul program.",
        "available_single": 2,
    },
    {
        "trip_id": "RT-LOC-26-900",
        "trip_name": "Siwa Discovery Demo",
        "type": "Local",
        "trip_type": "local",
        "start_date": "2026-09-10",
        "end_date": "2026-09-14",
        "public_price": "2000 EGP",
        "public_description": "Verified Siwa program.",
        "available_single": 2,
    },
    {
        "trip_id": "RT-LOC-26-901",
        "trip_name": "Siwa Wellness Demo",
        "type": "Local",
        "trip_type": "local",
        "start_date": "2026-09-20",
        "end_date": "2026-09-24",
        "public_price": "2200 EGP",
        "public_description": "Verified Siwa wellness program.",
        "available_single": 2,
    },
    {
        "trip_id": "RT-INT-26-AR",
        "trip_name": "رحلة اسطنبول",
        "type": "International",
        "trip_type": "international",
        "start_date": "2026-11-01",
        "end_date": "2026-11-07",
        "public_price": "2400 USD",
        "public_description": "برنامج اسطنبول المؤكد.",
        "available_single": 2,
    },
]


class RecordingReadTools:
    def __init__(self, *, trips: list[dict] | None = None, fail_search: bool = False, identity: dict | None = None) -> None:
        self.trips = [dict(trip) for trip in (trips or TRIPS)]
        self.fail_search = fail_search
        self.identity = identity
        self.calls: list[dict] = []

    def search_trips(self, *, trip_type: str = "", query: str = "") -> dict:
        self.calls.append({"name": "search_trips", "trip_type": trip_type, "query": query})
        if self.fail_search:
            raise RuntimeError("database exploded")
        normalized_query = ToolCallingSessionRuntime._normalize_trip_reference(query)
        normalized_type = str(trip_type or "").strip().lower()
        trips = []
        for trip in self.trips:
            if normalized_type and str(trip.get("trip_type") or "").lower() != normalized_type:
                continue
            if normalized_query:
                haystack = ToolCallingSessionRuntime._normalize_trip_reference(
                    f"{trip.get('trip_id')} {trip.get('trip_name')}"
                )
                if normalized_query not in haystack:
                    continue
            trips.append(dict(trip))
        return {
            "open_trips": [trip for trip in trips if trip.get("start_date")],
            "date_tbd_trips": [trip for trip in trips if not trip.get("start_date")],
            "trips": trips,
        }

    def find_traveler_by_phone(self, *, raw_phone: str, country_code: str = "") -> dict:
        self.calls.append({"name": "find_traveler_by_phone", "raw_phone": raw_phone, "country_code": country_code})
        if self.identity:
            return {"status": "found", "match_status": "single_match", "traveler": dict(self.identity), "handoff_required": False}
        return {"status": "not_found", "match_status": "not_found", "traveler": None, "handoff_required": False}

    def get_trip_details(self, *, trip_id: str) -> dict:
        self.calls.append({"name": "get_trip_details", "trip_id": trip_id})
        trip = next((trip for trip in self.trips if trip["trip_id"] == trip_id), None)
        return {"status": "found" if trip else "not_found", "trip": dict(trip) if trip else None}

    def get_passport_status(self, **_kwargs) -> dict:
        return {"traveler": {}, "documents": []}


class PassiveAgent:
    def __init__(self, reply: str = "Please share your WhatsApp number so I can check your Rahma Traveler profile safely.") -> None:
        self.reply = reply
        self.calls: list[dict] = []

    def respond(self, *, user_message: str, session_context: dict | None = None, conversation_history: list[dict] | None = None) -> dict:
        self.calls.append(
            {
                "user_message": user_message,
                "session_context": dict(session_context or {}),
                "conversation_history": list(conversation_history or []),
            }
        )
        return {"reply": self.reply, "tool_requests": [], "mode": "tool_calling"}


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


def _selected_trip_session(rt: ToolCallingSessionRuntime, trip: dict | None = None) -> SessionState:
    trip = dict(trip or TRIPS[1])
    session = rt.create_session()
    session.raw_phone = "01112223333"
    session.country_code = "20"
    session.trip_type = str(trip.get("trip_type") or trip.get("type") or "").lower()
    session.selected_trip_id = str(trip["trip_id"])
    session.selected_trip_name = str(trip["trip_name"])
    session.preview = {
        "traveler": {"traveler_id": "TR100", "full_name": "Mona Ali", "status": "Active"},
        "workflow": {
            "identity_verified": True,
            "verified_traveler": {"traveler_id": "TR100", "full_name": "Mona Ali", "status": "Active"},
            "verified_status": "Active",
        },
        "trip_result": {"open_trips": [trip], "date_tbd_trips": []},
        "trip_reference": trip,
        "collection_state": {"trip_type": True, "selected_trip": True},
    }
    session.stage = "traveler_gender_required"
    return session


@pytest.mark.parametrize(
    ("text", "trip_id"),
    [
        ("I want the Istanbul Explorer trip", "RT-INT-26-001"),
        ("Discovery Demo", "RT-LOC-26-900"),
        ("RT-INT-26-001", "RT-INT-26-001"),
        ("انا عايز رحلة اسطنبول", "RT-INT-26-AR"),
        ("Siwa Discovery Demo trip", "RT-LOC-26-900"),
    ],
)
def test_direct_trip_references_resolve_to_verified_crm_trip(runtime: ToolCallingSessionRuntime, text: str, trip_id: str) -> None:
    session = _send(runtime, text)

    assert session.selected_trip_id == trip_id
    assert session.tools_used == ["search_trips"]
    assert "matching trip" in session.messages[-1]["text"] or "وجدت رحلة" in session.messages[-1]["text"]
    assert "local trip or an international trip" not in session.messages[-1]["text"].lower()
    assert any(call["name"] == "search_trips" for call in runtime._read_only_tools.calls)
    assert "Traceback" not in session.messages[-1]["text"]
    assert not runtime._write_executor.execute.called


def test_abbreviation_int_sets_trip_type_without_inventing_trip(runtime: ToolCallingSessionRuntime) -> None:
    session = _send(runtime, "int.")

    assert session.trip_type == "international"
    assert session.selected_trip_id == ""
    assert all(call["name"] != "search_trips" for call in runtime._read_only_tools.calls)
    assert not runtime._write_executor.execute.called


def test_unknown_trip_asks_one_clarification_without_technical_error(runtime: ToolCallingSessionRuntime) -> None:
    session = _send(runtime, "Atlantis trip")

    assert session.selected_trip_id == ""
    assert session.tools_used == ["search_trips"]
    assert "exact trip name or trip ID" in session.messages[-1]["text"]
    assert "database" not in session.messages[-1]["text"].lower()
    assert not runtime._write_executor.execute.called


def test_multiple_matching_trips_show_numbered_choices(runtime: ToolCallingSessionRuntime) -> None:
    session = _send(runtime, "Siwa demo trip")

    assert session.selected_trip_id == ""
    assert session.stage == "trip_selection_required"
    assert "1. Siwa Discovery Demo" in session.messages[-1]["text"]
    assert "2. Siwa Wellness Demo" in session.messages[-1]["text"]
    assert not runtime._write_executor.execute.called


def test_numbered_choice_after_multiple_match_confirms_selected_trip(runtime: ToolCallingSessionRuntime) -> None:
    session = _send(runtime, "Siwa demo trip")
    session = _send(runtime, "2", session)

    assert session.selected_trip_id == "RT-LOC-26-901"
    assert session.trip_type == "local"
    assert session.preview["collection_state"]["selected_trip"] is True
    assert not runtime._write_executor.execute.called


def test_returning_and_new_traveler_identity_state(tmp_path: Path) -> None:
    original_env = dict(os.environ)
    os.environ["AI_AGENT_MODE"] = "tool_calling"
    client, app = _make_app_with_db(tmp_path / uuid.uuid4().hex)
    settings = replace(app.config["SETTINGS"], ai_provider="none", gemini_api_key="")
    returning = ToolCallingSessionRuntime(settings=settings, conversation_ai=PassiveAgent("Profile found."))
    returning._read_only_tools = RecordingReadTools(
        identity={"traveler_id": "TR100", "full_name": "Mona Ali", "status": "Active"}
    )
    returning_session = _send(returning, "01112223333")
    new = ToolCallingSessionRuntime(settings=settings, conversation_ai=PassiveAgent("Please share your name."))
    new._read_only_tools = RecordingReadTools(identity=None)
    new_session = _send(new, "01099999999")
    os.environ.clear()
    os.environ.update(original_env)

    assert returning_session.preview["workflow"]["identity_verified"] is True
    assert returning_session.preview["traveler"]["traveler_id"] == "TR100"
    assert new_session.preview["workflow"]["lookup_status"] == "not_found"
    assert "Traceback" not in returning_session.messages[-1]["text"]
    assert "Traceback" not in new_session.messages[-1]["text"]


def test_mid_conversation_language_change_is_explicit(runtime: ToolCallingSessionRuntime) -> None:
    session = _send(runtime, "انا عايز رحلة اسطنبول")
    assert session.language == "ar"
    session = _send(runtime, "Istanbul Explorer", session)
    assert session.language == "ar"
    session = _send(runtime, "reply in English", session)
    assert session.language == "en"


def test_repeated_trip_information_does_not_clear_confirmed_selection(runtime: ToolCallingSessionRuntime) -> None:
    session = _send(runtime, "Istanbul Explorer")
    first_reply_count = len(session.messages)
    session = _send(runtime, "Istanbul Explorer", session)

    assert session.selected_trip_id == "RT-INT-26-001"
    assert len(session.messages) >= first_reply_count
    assert "local trip or an international trip" not in session.messages[-1]["text"].lower()
    assert not runtime._write_executor.execute.called


def test_details_request_for_current_preview_trip_uses_that_trip(runtime: ToolCallingSessionRuntime) -> None:
    session = runtime.create_session()
    trip = dict(TRIPS[1])
    session.raw_phone = "01112223333"
    session.country_code = "20"
    session.preview = {
        "traveler": {"traveler_id": "TR100", "full_name": "Mona Ali", "status": "Active"},
        "workflow": {
            "identity_verified": True,
            "verified_traveler": {"traveler_id": "TR100", "full_name": "Mona Ali", "status": "Active"},
            "verified_status": "Active",
        },
        "trip_result": {"open_trips": [trip], "date_tbd_trips": []},
        "collection_state": {"trip_type": True},
    }

    session = _send(runtime, "tell me more details for this trip", session)

    reply = session.messages[-1]["text"]
    assert session.selected_trip_id == "RT-LOC-26-900"
    assert "Siwa Discovery Demo" in reply
    assert "Verified Siwa program" in reply
    assert "could not find" not in reply.lower()
    assert "boys/male" in reply.lower()
    assert runtime._read_only_tools.calls == []
    assert not runtime._write_executor.execute.called


def test_hostile_reply_gets_agentic_recovery_without_exact_prompt_repeat(runtime: ToolCallingSessionRuntime) -> None:
    session = _selected_trip_session(runtime)
    session.messages.append(
        {
            "role": "assistant",
            "text": "Are the travelers boys/male or girls/female?\n\n1) Boys / Male\n2) Girls / Female",
        }
    )

    session = _send(runtime, "fuck u", session)

    reply = session.messages[-1]["text"]
    assert "I hear you" in reply
    assert "boys/male" in reply.lower()
    assert reply != "Are the travelers boys/male or girls/female?\n\n1) Boys / Male\n2) Girls / Female"
    assert session.room_group == ""
    assert session.stage == "traveler_gender_required"
    assert not runtime._write_executor.execute.called
    assert "Traceback" not in reply


def test_arabic_explanation_interruption_is_answered_without_prompt_replay(runtime: ToolCallingSessionRuntime) -> None:
    session = _selected_trip_session(runtime)
    previous_prompt = "Are the travelers boys/male or girls/female?\n\n1) Boys / Male\n2) Girls / Female"
    session.messages.append({"role": "assistant", "text": previous_prompt})

    session = _send(runtime, "\u0644\u064a\u0647\u061f", session)

    reply = session.messages[-1]["text"]
    assert reply != previous_prompt
    assert "CRM" in reply
    assert "1" in reply and "2" in reply
    assert session.language == "ar"
    assert session.selected_trip_id == "RT-LOC-26-900"
    assert not runtime._write_executor.execute.called


def test_identity_question_answers_then_guides_back_to_missing_booking_detail(runtime: ToolCallingSessionRuntime) -> None:
    session = _selected_trip_session(runtime)

    session = _send(runtime, "who are u", session)

    reply = session.messages[-1]["text"]
    assert "Rahma Traveler" in reply
    assert "AI sales assistant" in reply
    assert "boys/male" in reply.lower()
    assert session.room_group == ""
    assert session.selected_trip_id == "RT-LOC-26-900"
    assert not runtime._write_executor.execute.called


def test_repeated_offtrack_messages_vary_response_while_preserving_state(runtime: ToolCallingSessionRuntime) -> None:
    session = _selected_trip_session(runtime)

    session = _send(runtime, "fuck u", session)
    first_reply = session.messages[-1]["text"]
    session = _send(runtime, "who are u", session)
    second_reply = session.messages[-1]["text"]

    assert first_reply != second_reply
    assert "boys/male" in first_reply.lower()
    assert "boys/male" in second_reply.lower()
    assert session.room_group == ""
    assert session.selected_trip_id == "RT-LOC-26-900"
    assert not runtime._write_executor.execute.called


def test_tool_failure_recovers_without_customer_visible_exception(runtime: ToolCallingSessionRuntime) -> None:
    runtime._read_only_tools = RecordingReadTools(fail_search=True)
    session = _send(runtime, "Istanbul trip")

    assert session.selected_trip_id == ""
    assert "exact trip name or trip ID" in session.messages[-1]["text"]
    assert "database exploded" not in session.messages[-1]["text"]
    assert "RuntimeError" not in session.messages[-1]["text"]


def test_explicit_human_request_is_preserved_for_workflow(runtime: ToolCallingSessionRuntime) -> None:
    session = _send(runtime, "I want human agent")

    assert runtime._conversation_ai.calls[-1]["session_context"]["candidate_human_request"] is True
    assert "human" in session.messages[-1]["text"].lower() or "WhatsApp" in session.messages[-1]["text"]
    assert not runtime._write_executor.execute.called


def test_booking_confirmation_and_cancellation_prevent_write(runtime: ToolCallingSessionRuntime) -> None:
    session = runtime.create_session()
    session.preview = {
        "traveler": {"traveler_id": "TR100", "full_name": "Mona Ali", "status": "Active"},
        "workflow": {
            "identity_verified": True,
            "verified_traveler": {"traveler_id": "TR100", "full_name": "Mona Ali", "status": "Active"},
            "verified_status": "Active",
        },
        "trip_result": {"open_trips": [TRIPS[0]], "date_tbd_trips": []},
        "collection_state": {
            "trip_type": True,
            "selected_trip": True,
            "room_group": True,
            "room_type": True,
            "group_size": True,
            "flight_option": True,
        },
    }
    session.raw_phone = "01112223333"
    session.country_code = "20"
    session.trip_type = "international"
    session.selected_trip_id = "RT-INT-26-001"
    session.selected_trip_name = "Istanbul Explorer"
    session.room_group = "boys"
    session.room_type = "Single"
    session.group_size = 1
    session.flight_option = "Without Flight"
    session.passport_attachment_ref = "passport/file.jpg"

    session = _send(runtime, "continue", session)
    assert session.stage == "booking_confirmation_required"
    assert "confirm" in session.messages[-1]["text"].lower()
    assert not runtime._write_executor.execute.called

    session = _send(runtime, "no", session)
    assert session.stage == "waiting"
    assert not session.booking_confirmed
    assert not runtime._write_executor.execute.called
