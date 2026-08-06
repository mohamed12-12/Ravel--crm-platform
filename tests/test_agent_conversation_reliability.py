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
from services.ai_agent.ai_agent_app.server import _route_live_message_with_gemini, _sanitize_gemini_reply
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


class BroadSearchReadTools(RecordingReadTools):
    def search_trips(self, *, trip_type: str = "", query: str = "") -> dict:
        self.calls.append({"name": "search_trips", "trip_type": trip_type, "query": query})
        if self.fail_search:
            raise RuntimeError("database exploded")
        normalized_type = str(trip_type or "").strip().lower()
        trips = []
        for trip in self.trips:
            if normalized_type and str(trip.get("trip_type") or "").lower() != normalized_type:
                continue
            trips.append(dict(trip))
        return {
            "open_trips": [trip for trip in trips if trip.get("start_date")],
            "date_tbd_trips": [trip for trip in trips if not trip.get("start_date")],
            "trips": trips,
            "query": query,
        }


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


class FailingAgent(PassiveAgent):
    def respond(self, *, user_message: str, session_context: dict | None = None, conversation_history: list[dict] | None = None) -> dict:
        self.calls.append(
            {
                "user_message": user_message,
                "session_context": dict(session_context or {}),
                "conversation_history": list(conversation_history or []),
            }
        )
        return {"reply": "", "tool_requests": [], "mode": "tool_calling", "error": "model unavailable"}


class RewritingAgent(PassiveAgent):
    def __init__(self, reply: str = "Model chat reply.", rewrite_reply: str | None = None) -> None:
        super().__init__(reply=reply)
        self.rewrite_reply = rewrite_reply
        self.rewrite_calls: list[dict] = []

    def rewrite_message(self, **kwargs) -> str:
        self.rewrite_calls.append(kwargs)
        base = str(kwargs.get("base_text") or "")
        if self.rewrite_reply is not None:
            return self.rewrite_reply
        return f"AI drafted: {base}"


class FakeHandoffExecutor:
    def __init__(self, *, succeeds: bool = True) -> None:
        self.succeeds = succeeds
        self.calls: list[dict] = []

    def execute(self, *, action: str, payload: dict, session_context: dict) -> dict:
        self.calls.append({"action": action, "payload": dict(payload), "session_context": dict(session_context)})
        if not self.succeeds:
            return {"executed": False, "assistant_message": "I will send this request to the Ravel"}
        handoff_id = f"H-TEST-{len(self.calls):04d}"
        return {
            "executed": True,
            "result_id": handoff_id,
            "assistant_message": "I will send this request to the Ravel",
            "handoff_case": {"handoff_id": handoff_id, "status": "Open"},
            "session_update": {
                "handoff_state": "handed_off",
                "stage": "handed_off",
                "final_result": {
                    "handoff_id": handoff_id,
                    "handoff_required": True,
                    "handoff_reason": "room_capacity",
                    "write_result": {"handoff_case": {"handoff_id": handoff_id, "status": "Open"}},
                },
            },
        }


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


def _trip_selection_session(rt: ToolCallingSessionRuntime, trip: dict | None = None) -> SessionState:
    trip = dict(trip or TRIPS[1])
    session = rt.create_session()
    session.raw_phone = "01112223333"
    session.country_code = "20"
    session.trip_type = str(trip.get("trip_type") or trip.get("type") or "").lower()
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
    session.messages.append(
        {
            "role": "assistant",
            "text": (
                "Here are the local trips currently available:\n\n"
                "1) Siwa Discovery Demo\n"
                "Dates: 2026-09-10 to 2026-09-14\n\n"
                "Please reply with the trip number or exact trip name."
            ),
        }
    )
    return session


def _completed_booking_session(rt: ToolCallingSessionRuntime, *, language: str = "ar") -> SessionState:
    trip = dict(TRIPS[0])
    session = _selected_trip_session(rt, trip)
    session.language = language
    session.stage = "post_booking_support"
    session.booking_completed = True
    session.booking_status = "Draft"
    session.handoff_state = "completed"
    session.room_group = "boys"
    session.room_type = "Single"
    session.group_size = 1
    session.booking_result = {
        "booking_id": "B-TEST-0001",
        "trip_id": trip["trip_id"],
        "trip_name": trip["trip_name"],
        "booking_status": "Draft",
        "room_type": "Single",
        "traveler_id": "TR100",
    }
    session.final_result = {
        "booking_id": "B-TEST-0001",
        "write_result": {"booking_draft": dict(session.booking_result)},
    }
    session.messages.append(
        {
            "role": "assistant",
            "text": "\u062a\u0645 \u0625\u0646\u0634\u0627\u0621 \u0645\u0633\u0648\u062f\u0629 \u0627\u0644\u062d\u062c\u0632 \u0628\u0646\u062c\u0627\u062d.",
        }
    )
    return session


def _verified_trip_search_session(rt: ToolCallingSessionRuntime, *, trip_type: str = "international") -> SessionState:
    session = rt.create_session()
    session.raw_phone = "01112223333"
    session.country_code = "20"
    session.trip_type = trip_type
    session.preview = {
        "traveler": {"traveler_id": "TR100", "full_name": "Mona Ali", "status": "Active"},
        "workflow": {
            "identity_verified": True,
            "verified_traveler": {"traveler_id": "TR100", "full_name": "Mona Ali", "status": "Active"},
            "verified_status": "Active",
        },
        "collection_state": {"trip_type": True},
    }
    session.stage = "trip_search_ready"
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
    assert "exact trip name" in session.messages[-1]["text"]
    assert "trip ID" not in session.messages[-1]["text"]
    assert "database" not in session.messages[-1]["text"].lower()
    assert not runtime._write_executor.execute.called


def test_trip_selection_no_is_treated_as_rejection_not_completion_failure(runtime: ToolCallingSessionRuntime) -> None:
    session = _trip_selection_session(runtime)

    session = _send(runtime, "no", session)

    reply = session.messages[-1]["text"].lower()
    assert session.stage == "trip_selection_required"
    assert session.selected_trip_id == ""
    assert "not continue" in reply or "another option" in reply
    assert "response was not completed correctly" not in reply
    assert "couldn't prepare" not in reply
    assert "here are the local trips" not in reply
    assert not session.fallback_used


def test_trip_selection_clarification_answers_need_without_repeating_list(runtime: ToolCallingSessionRuntime) -> None:
    session = _trip_selection_session(runtime)

    session = _send(runtime, "what do u need", session)

    reply = session.messages[-1]["text"].lower()
    assert session.stage == "trip_selection_required"
    assert session.selected_trip_id == ""
    assert "choose one of the listed trips" in reply
    assert "number or name" in reply
    assert "here are the local trips" not in reply
    assert "response was not completed correctly" not in reply
    assert not session.fallback_used


def test_multiple_matching_trips_show_numbered_choices(runtime: ToolCallingSessionRuntime) -> None:
    session = _send(runtime, "Siwa demo trip")

    assert session.selected_trip_id == ""
    assert session.stage == "trip_selection_required"
    assert "1) Siwa Discovery Demo" in session.messages[-1]["text"]
    assert "2) Siwa Wellness Demo" in session.messages[-1]["text"]
    assert not runtime._write_executor.execute.called


def test_destination_query_does_not_show_unrelated_broad_search_results(runtime: ToolCallingSessionRuntime) -> None:
    runtime._read_only_tools = BroadSearchReadTools(
        trips=[
            {
                "trip_id": "RT-INT-DEMO",
                "trip_name": "DEmo",
                "type": "International",
                "trip_type": "international",
                "start_date": "2026-07-28",
                "end_date": "2026-08-10",
                "public_price": "1000$",
            },
            {
                "trip_id": "RT-INT-TODAY",
                "trip_name": "Today Demo",
                "type": "International",
                "trip_type": "international",
                "start_date": "2026-07-28",
                "end_date": "2026-07-30",
                "public_price": "500$",
            },
        ]
    )
    session = _verified_trip_search_session(runtime)

    session = _send(runtime, "i want to go to turkey", session)
    reply = session.messages[-1]["text"]
    trip_result = session.preview["trip_result"]

    assert session.trip_query == "Turkey"
    assert session.selected_trip_id == ""
    assert session.selected_trip_name == ""
    assert "do not have any available international trips" in reply.lower()
    assert "Reason for escalation" in reply
    assert "DEmo" not in reply
    assert "Today Demo" not in reply
    assert trip_result["open_trips"] == []
    assert trip_result["date_tbd_trips"] == []
    assert not runtime._write_executor.execute.called


def test_destination_query_matches_verified_destination_fields_only(runtime: ToolCallingSessionRuntime) -> None:
    runtime._read_only_tools = BroadSearchReadTools(
        trips=[
            {
                "trip_id": "RT-INT-TR-001",
                "trip_name": "Istanbul Explorer",
                "type": "International",
                "trip_type": "international",
                "destination": "Turkey",
                "start_date": "2026-10-01",
                "end_date": "2026-10-08",
                "public_price": "2500 USD",
            },
            {
                "trip_id": "RT-INT-TODAY",
                "trip_name": "Today Demo",
                "type": "International",
                "trip_type": "international",
                "destination": "Georgia",
                "start_date": "2026-07-28",
                "end_date": "2026-07-30",
                "public_price": "500$",
            },
        ]
    )
    session = _verified_trip_search_session(runtime)

    session = _send(runtime, "i want to go to turkeya", session)
    reply = session.messages[-1]["text"]
    open_trips = session.preview["trip_result"]["open_trips"]

    assert session.trip_query == "Turkey"
    assert session.selected_trip_id == ""
    assert "Istanbul Explorer" in reply
    assert "Today Demo" not in reply
    assert [trip["trip_id"] for trip in open_trips] == ["RT-INT-TR-001"]
    assert not runtime._write_executor.execute.called


def test_birthday_reply_does_not_overwrite_confirmed_whatsapp(runtime: ToolCallingSessionRuntime) -> None:
    session = runtime.create_session()
    session.language = "ar"
    session.stage = "birthday_required"
    session.raw_phone = "01264587566"
    session.pending_raw_phone = "01264587566"
    session.customer_name = "تيست تيست 2"
    session.nationality = "الأردنيه"

    session = _send(runtime, "2003-05-26", session)

    assert session.birthday == "2003-05-26"
    assert session.raw_phone == "01264587566"
    assert session.pending_raw_phone == "01264587566"
    assert session.preview["collection_state"]["birthday"] is True
    assert not runtime._write_executor.execute.called


def test_name_clarification_does_not_advance_to_nationality(runtime: ToolCallingSessionRuntime) -> None:
    session = runtime.create_session()
    session.language = "ar"
    session.stage = "traveler_not_found"
    session.raw_phone = "01264587566"

    session = _send(runtime, "\u0644\u0627\u0632\u0645 \u0643\u0627\u0645\u0644\u061f", session)

    reply = session.messages[-1]["text"]
    assert session.customer_name == ""
    assert session.stage == "traveler_not_found"
    assert "\u0627\u0633\u0645\u0643 \u0627\u0644\u062b\u0644\u0627\u062b\u064a" in reply
    assert "\u062c\u0646\u0633\u064a\u062a\u0643" not in reply


def test_asking_known_name_does_not_invent_or_advance(runtime: ToolCallingSessionRuntime) -> None:
    session = runtime.create_session()
    session.language = "ar"
    session.stage = "traveler_not_found"
    session.raw_phone = "01264587566"

    session = _send(runtime, "\u0637\u0628 \u0627\u0646\u0627 \u0627\u0633\u0645\u064a \u0627\u064a\u0647\u061f", session)

    reply = session.messages[-1]["text"]
    assert session.customer_name == ""
    assert session.stage == "traveler_not_found"
    assert "\u0644\u0627 \u064a\u0645\u0643\u0646\u0646\u064a \u0645\u0639\u0631\u0641\u062a\u0647" in reply
    assert "\u062c\u0646\u0633\u064a\u062a\u0643" not in reply


@pytest.mark.parametrize("name", ["\u0645\u062d\u0645\u062f", "\u0645\u062d\u0645\u062f \u0623\u0634\u0631\u0641", "Mohamed", "Mohamed Ashraf"])
def test_short_full_name_is_rejected(runtime: ToolCallingSessionRuntime, name: str) -> None:
    session = runtime.create_session()
    session.language = "ar" if any("\u0600" <= ch <= "\u06ff" for ch in name) else "en"
    session.stage = "traveler_not_found"
    session.raw_phone = "01264587566"

    session = _send(runtime, name, session)

    assert session.customer_name == ""
    assert session.stage == "traveler_not_found"
    assert session.nationality == ""


@pytest.mark.parametrize(
    "name",
    [
        "\u0645\u062d\u0645\u062f \u0623\u0634\u0631\u0641 \u0635\u0641\u0648\u062a",
        "Mohamed Ashraf Safwat",
    ],
)
def test_valid_three_part_name_advances_only_to_nationality(runtime: ToolCallingSessionRuntime, name: str) -> None:
    session = runtime.create_session()
    session.language = "ar" if any("\u0600" <= ch <= "\u06ff" for ch in name) else "en"
    session.stage = "traveler_not_found"
    session.raw_phone = "01264587566"

    session = _send(runtime, name, session)

    assert session.customer_name == name
    assert session.stage == "nationality_required"
    assert session.nationality == ""
    assert session.birthday == ""


def test_nationality_sent_before_name_is_not_stored_or_used_to_skip(runtime: ToolCallingSessionRuntime) -> None:
    session = runtime.create_session()
    session.stage = "traveler_not_found"
    session.raw_phone = "01264587566"

    session = _send(runtime, "Egyptian", session)
    assert session.customer_name == ""
    assert session.nationality == ""
    assert session.stage == "traveler_not_found"

    session = _send(runtime, "Mohamed Ashraf Safwat", session)
    assert session.customer_name == "Mohamed Ashraf Safwat"
    assert session.nationality == ""
    assert session.stage == "nationality_required"


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("26/05/2003", "2003-05-26"),
        ("May 26, 2003", "2003-05-26"),
        ("2003.05.26", "2003-05-26"),
        ("26 05 03", "2003-05-26"),
    ],
)
def test_birthday_accepts_flexible_clear_formats(
    runtime: ToolCallingSessionRuntime,
    reply: str,
    expected: str,
) -> None:
    session = runtime.create_session()
    session.stage = "birthday_required"
    session.raw_phone = "01264587566"
    session.customer_name = "Test Traveler Name"
    session.nationality = "Egyptian"

    session = _send(runtime, reply, session)

    assert session.birthday == expected
    assert session.preview["collection_state"]["birthday"] is True


def test_invalid_flexible_birthday_does_not_advance(runtime: ToolCallingSessionRuntime) -> None:
    session = runtime.create_session()
    session.stage = "birthday_required"
    session.raw_phone = "01264587566"
    session.customer_name = "Test Traveler Name"
    session.nationality = "Egyptian"

    session = _send(runtime, "31/02/2003", session)

    assert session.birthday == ""
    assert session.stage == "birthday_required"
    assert not session.preview.get("collection_state", {}).get("birthday")


def test_arabic_male_room_group_uses_backend_numbered_inventory(runtime: ToolCallingSessionRuntime) -> None:
    trip = {
        "trip_id": "RT-INT-TODAY",
        "trip_name": "Today Demo",
        "type": "International",
        "trip_type": "international",
        "start_date": "2026-07-28",
        "end_date": "2026-07-30",
        "public_price": "500$",
        "available_single": 8,
        "boys_double": 4,
        "boys_triple": 4,
        "girls_double": 0,
        "girls_triple": 0,
    }
    session = _selected_trip_session(runtime, trip)
    session.language = "ar"

    session = _send(runtime, "ذكور", session)
    reply = session.messages[-1]["text"]

    assert session.room_group == "boys"
    assert session.stage == "room_type_required"
    assert "1)" in reply
    assert "2)" in reply
    assert "3)" in reply
    assert reply.count("1)") == 1
    assert not runtime._write_executor.execute.called


def test_mixed_traveler_group_does_not_collapse_to_boys_inventory(runtime: ToolCallingSessionRuntime) -> None:
    trip = {
        "trip_id": "RT-INT-MIXED",
        "trip_name": "Mixed Inventory Demo",
        "type": "International",
        "trip_type": "international",
        "start_date": "2026-08-25",
        "end_date": "2026-08-30",
        "available_single": 3,
        "available_double": 2,
        "boys_double": 1,
        "girls_double": 1,
        "boys_triple": 2,
        "girls_triple": 1,
    }
    session = _selected_trip_session(runtime, trip)

    session = _send(runtime, "1 boy 1 girl", session)
    reply = session.messages[-1]["text"]

    assert session.room_group == "mixed"
    assert session.group_size == 2
    assert "separately for boys and girls" in reply
    assert "1 double boys room and 1 double girls room" in reply
    assert "Double boys" in reply
    assert "Double girls" in reply
    assert not runtime._write_executor.execute.called


@pytest.mark.parametrize("reply_text", ["mix", "mixed group"])
def test_plain_mixed_group_reply_uses_separated_room_inventory(
    runtime: ToolCallingSessionRuntime,
    reply_text: str,
) -> None:
    trip = {
        "trip_id": "RT-INT-MIXED-PLAIN",
        "trip_name": "Mixed Plain Inventory Demo",
        "type": "International",
        "trip_type": "international",
        "start_date": "2026-08-25",
        "end_date": "2026-08-30",
        "available_single": 3,
        "boys_double": 1,
        "girls_double": 1,
        "boys_triple": 2,
        "girls_triple": 1,
    }
    session = _selected_trip_session(runtime, trip)

    session = _send(runtime, reply_text, session)
    reply = session.messages[-1]["text"]

    assert session.room_group == "mixed"
    assert session.stage == "room_type_required"
    assert not session.preview["collection_state"].get("group_size")
    assert "separately for boys and girls" in reply
    assert "Double boys" in reply
    assert "Double girls" in reply
    assert not runtime._write_executor.execute.called


def test_mixed_room_request_is_stored_structurally(runtime: ToolCallingSessionRuntime) -> None:
    session = _selected_trip_session(runtime)
    session.room_group = "mixed"
    session.stage = "room_type_required"
    session.preview["collection_state"].update({"room_group": True, "room_type": False})

    session = _send(runtime, "1 double boys room and 1 double girls room", session)

    assert session.room_group == "mixed"
    assert session.room_type == "Double"
    assert session.room_requirements["boys_rooms_requested"] == 1
    assert session.room_requirements["girls_rooms_requested"] == 1
    assert session.room_requirements["requirements"] == [
        {"room_type": "Double", "room_group": "boys", "rooms": 1},
        {"room_type": "Double", "room_group": "girls", "rooms": 1},
    ]
    assert not runtime._write_executor.execute.called


def test_arabic_mixed_room_request_is_extracted() -> None:
    hints = ToolCallingSessionRuntime._extract_hints(
        "\u0639\u0627\u064a\u0632 \u063a\u0631\u0641\u0629 \u0644\u0644\u0634\u0628\u0627\u0628 \u0648\u063a\u0631\u0641\u0629 \u0644\u0644\u0628\u0646\u0627\u062a",
        stage="room_type_required",
    )

    requirements = hints["candidate_room_requirements"]
    assert requirements["boys_rooms_requested"] == 1
    assert requirements["girls_rooms_requested"] == 1


def test_numbered_choice_after_multiple_match_confirms_selected_trip(runtime: ToolCallingSessionRuntime) -> None:
    session = _send(runtime, "Siwa demo trip")
    session = _send(runtime, "2", session)

    assert session.selected_trip_id == "RT-LOC-26-901"
    assert session.trip_type == "local"
    assert session.preview["collection_state"]["selected_trip"] is True
    assert not runtime._write_executor.execute.called


def test_flight_choice_does_not_reopen_trip_search_or_clear_selection(runtime: ToolCallingSessionRuntime) -> None:
    trip = dict(TRIPS[0])
    session = _selected_trip_session(runtime, trip)
    session.room_group = "girls"
    session.room_type = "Double"
    session.group_size = 2
    session.flight_option = ""
    session.preview["collection_state"].update(
        {"room_group": True, "room_type": True, "group_size": True, "flight_option": False}
    )
    session.stage = "flight_option_required"

    session = _send(runtime, "no flight", session)

    reply = session.messages[-1]["text"].lower()
    assert session.selected_trip_id == trip["trip_id"]
    assert session.stage == "awaiting_passport_upload"
    assert "check the available trips" not in reply
    assert "passport" in reply
    assert not runtime._write_executor.execute.called


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("withot", "Without Flight"),
        ("without", "Without Flight"),
        ("without flight", "Without Flight"),
        ("2", "Without Flight"),
        ("1", "With Flight"),
    ],
)
def test_flight_choice_accepts_predictable_typos_and_numbers(
    runtime: ToolCallingSessionRuntime,
    message: str,
    expected: str,
) -> None:
    trip = dict(TRIPS[0])
    session = _selected_trip_session(runtime, trip)
    session.room_group = "boys"
    session.room_type = "Single"
    session.group_size = 1
    session.flight_option = ""
    session.preview["collection_state"].update(
        {"room_group": True, "room_type": True, "group_size": True, "flight_option": False}
    )
    session.stage = "flight_option_required"

    session = _send(runtime, message, session)

    assert session.flight_option == expected
    assert session.selected_trip_id == trip["trip_id"]
    assert session.stage != "error"
    assert "response was not completed correctly" not in session.messages[-1]["text"].lower()


def test_trip_search_reply_hides_internal_terms_and_room_quantities(runtime: ToolCallingSessionRuntime) -> None:
    trip = {
        **TRIPS[0],
        "remaining_places": 9,
        "available_double": 4,
        "boys_double": 3,
        "girls_double": 1,
    }
    session = _selected_trip_session(runtime, trip)
    session.selected_trip_id = ""
    session.trip_type = "international"

    reply = runtime._canonical_trip_search_reply(session)

    assert "CRM" not in reply
    assert "verified" not in reply.lower()
    assert "9" not in reply
    assert "3 available" not in reply
    assert "Here are the international trips currently available" in reply


def test_model_context_converts_room_inventory_to_availability_status(runtime: ToolCallingSessionRuntime) -> None:
    trip = {
        **TRIPS[0],
        "available_single": 2,
        "available_double": 0,
        "boys_double": 3,
        "girls_double": 1,
        "remaining_places": 12,
    }
    context = {"selected_trip": trip, "trip_result": {"open_trips": [trip], "date_tbd_trips": []}}

    safe = runtime._traveler_safe_context(context)

    selected = safe["selected_trip"]
    assert "available_single" not in selected
    assert "boys_double" not in selected
    assert "remaining_places" not in selected
    assert selected["single_availability"] == "Available"
    assert selected["double_availability"] == "Unavailable"


def test_without_flight_from_passport_step_clears_stale_passport_state(runtime: ToolCallingSessionRuntime) -> None:
    trip = {**TRIPS[0], "passport_required_with_flight": True}
    session = _selected_trip_session(runtime, trip)
    session.stage = "awaiting_passport_upload"
    session.room_group = "girls"
    session.room_type = "Double"
    session.group_size = 2
    session.flight_option = "With Flight"
    session.passport_attachment_ref = "passport/file.jpg"
    session.preview["collection_state"].update(
        {"room_group": True, "room_type": True, "group_size": True, "flight_option": True}
    )

    session = _send(runtime, "I do not want a flight anymore", session)

    assert session.flight_option == "Without Flight"
    assert session.passport_attachment_ref == ""
    assert session.stage == "booking_confirmation_required"
    assert "Without Flight" in session.messages[-1]["text"]
    assert not runtime._write_executor.execute.called


def test_domestic_trip_without_flight_support_skips_flight_and_passport(runtime: ToolCallingSessionRuntime) -> None:
    trip = {**TRIPS[1], "supports_flights": False}
    session = _selected_trip_session(runtime, trip)
    session.stage = "group_size_required"
    session.room_group = "boys"
    session.room_type = "Single"
    session.preview["collection_state"].update({"room_group": True, "room_type": True, "group_size": False})

    session = _send(runtime, "2 travelers", session)

    assert session.flight_option == "Not Applicable"
    assert session.passport_attachment_ref == ""
    assert session.stage == "booking_confirmation_required"
    assert "Flight option: Not Applicable" in session.messages[-1]["text"]
    assert "passport" not in session.messages[-1]["text"].lower()
    assert not runtime._write_executor.execute.called


def test_local_trip_without_flight_metadata_skips_flight_and_passport(runtime: ToolCallingSessionRuntime) -> None:
    trip = dict(TRIPS[1])
    trip.pop("supports_flights", None)
    session = _selected_trip_session(runtime, trip)
    session.stage = "group_size_required"
    session.room_group = "boys"
    session.room_type = "Single"
    session.preview["collection_state"].update({"room_group": True, "room_type": True, "group_size": False})

    session = _send(runtime, "1", session)

    assert session.flight_option == "Not Applicable"
    assert session.passport_attachment_ref == ""
    assert session.stage == "booking_confirmation_required"
    assert "Do you want this trip with flights" not in session.messages[-1]["text"]
    assert "Flight option: Not Applicable" in session.messages[-1]["text"]
    assert not runtime._write_executor.execute.called


def test_arabic_group_size_capture_preserves_context_and_never_exposes_output_failure(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """A valid group-size answer must advance the workflow without an LLM rewrite."""
    trip = {**TRIPS[1], "available_double": 2, "boys_double": 2}
    session = _selected_trip_session(runtime, trip)
    session.language = "ar"
    session.stage = "group_size_required"
    session.room_group = "boys"
    session.room_type = "Double"
    session.preview["collection_state"].update({"room_group": True, "room_type": True, "group_size": False})
    runtime._conversation_ai = RewritingAgent(rewrite_reply="I will")

    session = _send(runtime, "2", session)

    assert session.group_size == 2
    assert session.stage == "booking_confirmation_required"
    assert session.flight_option == "Not Applicable"
    assert "معلش" not in session.messages[-1]["text"]
    assert "Do you want this trip with flights" not in session.messages[-1]["text"]

    session = _send(runtime, "2 مسافرين", session)

    assert session.stage == "booking_confirmation_required"
    assert "معلش" not in session.messages[-1]["text"]
    assert "الحجز" in session.messages[-1]["text"]


def test_arabic_group_size_clarification_and_booking_intent_are_contextual(
    runtime: ToolCallingSessionRuntime,
) -> None:
    trip = dict(TRIPS[1])
    session = _selected_trip_session(runtime, trip)
    session.language = "ar"
    session.stage = "group_size_required"
    session.room_group = "boys"
    session.room_type = "Double"
    session.preview["collection_state"].update({"room_group": True, "room_type": True, "group_size": False})

    session = _send(runtime, "يعني ايه", session)
    clarification = session.messages[-1]["text"]
    assert session.stage == "group_size_required"
    assert "معلش" not in clarification
    assert "المسافرين" in clarification
    assert "2" in clarification

    session = _send(runtime, "انا عايز احجز", session)
    booking_reply = session.messages[-1]["text"]
    assert session.stage == "group_size_required"
    assert "معلش" not in booking_reply
    assert "المسافرين" in booking_reply


def test_backend_required_step_reply_is_authored_by_ai(tmp_path: Path) -> None:
    original_env = dict(os.environ)
    os.environ["AI_AGENT_MODE"] = "tool_calling"
    client, app = _make_app_with_db(tmp_path / uuid.uuid4().hex)
    settings = replace(app.config["SETTINGS"], ai_provider="none", gemini_api_key="")
    ai = RewritingAgent(rewrite_reply="AI drafted: Are the travelers boys/male or girls/female?")
    rt = ToolCallingSessionRuntime(settings=settings, conversation_ai=ai)
    rt._read_only_tools = RecordingReadTools()
    rt._write_executor = Mock()
    session = _selected_trip_session(rt)

    session = _send(rt, "continue", session)

    os.environ.clear()
    os.environ.update(original_env)
    assert "boys" in session.messages[-1]["text"].lower()
    assert ai.rewrite_calls == []
    assert session.stage == "traveler_gender_required"
    assert not rt._write_executor.execute.called


def test_identity_policy_reply_bypasses_ai_for_prompt_injection_safety(runtime: ToolCallingSessionRuntime) -> None:
    ai = RewritingAgent(rewrite_reply="AI drafted: I am Ravel Traveler's AI sales assistant.")
    runtime._conversation_ai = ai

    session = _send(runtime, "who created you")

    assert not session.messages[-1]["text"].startswith("AI drafted:")
    assert ai.rewrite_calls == []
    assert "nanovate.io" in session.messages[-1]["text"]


def test_unsafe_ai_rewrite_falls_back_to_sanitized_authoritative_base(runtime: ToolCallingSessionRuntime) -> None:
    ai = RewritingAgent(rewrite_reply='{"selected_trip_id":"RT-1","assistant_message":"leak"}')
    runtime._conversation_ai = ai

    session = _selected_trip_session(runtime)
    session = _send(runtime, "continue", session)

    reply = session.messages[-1]["text"]
    assert "selected_trip_id" not in reply
    assert "assistant_message" not in reply
    assert "boys" in reply.lower()
    assert ai.rewrite_calls == []


def test_incomplete_ai_rewrite_is_not_saved_as_final_response(runtime: ToolCallingSessionRuntime) -> None:
    ai = RewritingAgent(rewrite_reply="I will")
    runtime._conversation_ai = ai
    session = _selected_trip_session(runtime)

    session = _send(runtime, "continue", session)

    reply = session.messages[-1]["text"]
    assert reply != "I will"
    assert "boys" in reply.lower()
    assert session.messages[-1].get("state") == "completed"
    assert ai.rewrite_calls == []


def test_room_capacity_review_creates_handoff_before_confirming(runtime: ToolCallingSessionRuntime) -> None:
    executor = FakeHandoffExecutor(succeeds=True)
    runtime._write_executor = executor
    trip = {**TRIPS[1], "available_single": 1}
    session = _selected_trip_session(runtime, trip)
    session.stage = "group_size_required"
    session.room_group = "boys"
    session.room_type = "Single"
    session.preview["collection_state"].update({"room_group": True, "room_type": True, "group_size": False})

    session = _send(runtime, "3 travelers", session)

    reply = session.messages[-1]["text"]
    assert len(executor.calls) == 1
    assert executor.calls[0]["action"] == "create_handoff"
    assert session.handoff_state == "handed_off"
    assert session.final_result["handoff_id"] == "H-TEST-0001"
    assert "sent" in reply.lower()
    assert "I will" not in reply
    assert "Ravel team" in reply


def test_handoff_failure_does_not_false_confirm(runtime: ToolCallingSessionRuntime) -> None:
    executor = FakeHandoffExecutor(succeeds=False)
    runtime._write_executor = executor
    trip = {**TRIPS[1], "available_single": 1}
    session = _selected_trip_session(runtime, trip)
    session.stage = "group_size_required"
    session.room_group = "boys"
    session.room_type = "Single"
    session.preview["collection_state"].update({"room_group": True, "room_type": True, "group_size": False})

    session = _send(runtime, "3 travelers", session)

    reply = session.messages[-1]["text"]
    assert len(executor.calls) == 1
    assert "couldn't submit" in reply.lower()
    assert "I've sent" not in reply
    assert session.handoff_state != "handed_off"


def test_handoff_acknowledgement_does_not_create_duplicate(runtime: ToolCallingSessionRuntime) -> None:
    executor = FakeHandoffExecutor(succeeds=True)
    runtime._write_executor = executor
    trip = {**TRIPS[1], "available_single": 1}
    session = _selected_trip_session(runtime, trip)
    session.stage = "group_size_required"
    session.room_group = "boys"
    session.room_type = "Single"
    session.preview["collection_state"].update({"room_group": True, "room_type": True, "group_size": False})

    session = _send(runtime, "3 travelers", session)
    session = _send(runtime, "okay do it", session)

    reply = session.messages[-1]["text"]
    assert len(executor.calls) == 1
    assert "already" in reply.lower()
    assert "review" in reply.lower()
    assert session.handoff_state == "handed_off"


def test_arabic_booking_intent_after_completed_booking_asks_choice_without_duplicate(runtime: ToolCallingSessionRuntime) -> None:
    session = _completed_booking_session(runtime, language="ar")
    original_booking = dict(session.booking_result or {})

    session = _send(runtime, "\u0639\u0627\u064a\u0632 \u0627\u062d\u062c\u0632", session)

    reply = session.messages[-1]["text"]
    assert "\u062d\u062c\u0632 \u062c\u062f\u064a\u062f" in reply
    assert "\u0627\u0644\u062d\u062c\u0632 \u0627\u0644\u062d\u0627\u0644\u064a" in reply
    assert "I need one more detail" not in reply
    assert session.stage == "post_booking_support"
    assert session.booking_result == original_booking
    assert session.booking_completed is True
    assert not runtime._write_executor.execute.called


def test_explicit_new_booking_after_completion_resets_intake_and_archives_previous_booking(runtime: ToolCallingSessionRuntime) -> None:
    """Regression: booking_result/booking_completed used to be left pointing
    at the FIRST booking after "book again" -- _linked_ids() and
    _execute_booking_draft()'s completed guard both read those fields as
    "this session already has a booking on file", so a second booking could
    never actually be created (every later "yes" would silently no-op
    forever). previous_booking_result is the archive; booking_result/
    booking_completed must actually clear so a new booking can complete.
    """
    session = _completed_booking_session(runtime, language="ar")
    original_booking = dict(session.booking_result or {})

    session = _send(runtime, "\u0639\u0627\u064a\u0632 \u0627\u062d\u062c\u0632 \u062a\u0627\u0646\u064a", session)

    reply = session.messages[-1]["text"]
    assert "\u0646\u0641\u0633 \u0627\u0644\u0631\u062d\u0644\u0629" in reply
    assert "\u0631\u062d\u0644\u0629 \u0623\u062e\u0631\u0649" in reply
    assert session.stage == "new_booking_intent"
    assert session.previous_booking_result == original_booking
    assert session.booking_result is None
    assert session.booking_completed is False
    assert session.selected_trip_id == ""
    assert session.room_type == ""
    assert session.trip_type == ""
    assert not runtime._write_executor.execute.called


def test_second_booking_actually_completes_after_book_again(runtime: ToolCallingSessionRuntime) -> None:
    """End-to-end regression for the same bug: not just that state resets,
    but that a full second booking cycle -- driven through real conversation
    turns, reaching confirmation and saying "yes" again -- actually creates
    a new booking rather than the completed guard silently refusing forever.
    """
    session = _completed_booking_session(runtime, language="en")
    session.language = "en"

    session = _send(runtime, "I want to book again", session)
    assert session.stage == "new_booking_intent"
    assert not session.booking_completed

    runtime._write_executor.execute.side_effect = None
    runtime._write_executor.execute.return_value = {
        "executed": True,
        "result_id": "B-TEST-0002",
        "booking_result": {"booking_id": "B-TEST-0002", "booking_status": "Draft"},
        "write_result_contract": {
            "status": "success",
            "record_id": "B-TEST-0002",
            "record_type": "booking",
            "executed": True,
        },
        "session_update": {
            "booking_result": {"booking_id": "B-TEST-0002", "booking_status": "Draft"},
            "booking_status": "Draft",
        },
    }

    for text in ("local", "Siwa Discovery Demo", "boys", "single", "1", "yes"):
        session = _send(runtime, text, session)

    assert session.booking_completed is True
    assert (session.booking_result or {}).get("booking_id") == "B-TEST-0002"
    reply = session.messages[-1]["text"]
    assert "B-TEST-0002" in reply
    runtime._write_executor.execute.assert_called_once()
    assert runtime._write_executor.execute.call_args.kwargs["action"] == "create_booking_draft"


def test_human_agent_request_after_booking_still_reaches_manual_handoff(runtime: ToolCallingSessionRuntime) -> None:
    """Regression: once stage == "post_booking_support" it re-sets itself
    every turn, so _handle_post_booking_message's `session.stage in {...}`
    branch matched unconditionally and swallowed every later message --
    including a genuine "I want to talk to a human" request, which never
    reached handle_message's real _execute_manual_handoff dispatch. A
    customer who completed a booking could never escalate to a human again.
    """
    executor = FakeHandoffExecutor(succeeds=True)
    runtime._write_executor = executor
    session = _completed_booking_session(runtime, language="en")

    session = _send(runtime, "I want to talk to a human", session)

    assert len(executor.calls) == 1
    assert executor.calls[0]["action"] == "create_handoff"
    assert session.handoff_state == "handed_off"
    reply = session.messages[-1]["text"]
    assert "sent" in reply.lower()
    assert "I need one more detail" not in reply


def test_post_booking_arabic_negative_is_contextual_clarification(runtime: ToolCallingSessionRuntime) -> None:
    session = _completed_booking_session(runtime, language="ar")

    session = _send(runtime, "\u0645\u0641\u064a\u0634", session)

    reply = session.messages[-1]["text"]
    assert "\u0644\u0627 \u062a\u0631\u064a\u062f" in reply
    assert "\u062d\u062c\u0632 \u062c\u062f\u064a\u062f" in reply
    assert "I need one more detail" not in reply
    assert session.stage == "post_booking_support"
    assert not runtime._write_executor.execute.called


def test_post_booking_status_uses_stored_booking_record(runtime: ToolCallingSessionRuntime) -> None:
    session = _completed_booking_session(runtime, language="ar")

    session = _send(runtime, "\u062d\u062c\u0632\u064a \u0627\u062a\u0633\u062c\u0644\u061f", session)

    reply = session.messages[-1]["text"]
    assert "B-TEST-0001" in reply
    assert "Draft" in reply
    assert "\u0645\u062a\u0633\u062c\u0644" in reply
    assert not runtime._write_executor.execute.called


def test_post_booking_acknowledgement_does_not_restart_or_duplicate(runtime: ToolCallingSessionRuntime) -> None:
    session = _completed_booking_session(runtime, language="ar")

    session = _send(runtime, "\u062a\u0645\u0627\u0645", session)

    reply = session.messages[-1]["text"]
    assert "\u0623\u0646\u0627 \u0645\u0639\u0627\u0643" in reply
    assert session.stage == "post_booking_support"
    assert session.selected_trip_id == "RT-INT-26-001"
    assert not runtime._write_executor.execute.called


def test_gemini_route_handles_arabic_post_booking_before_model_call(runtime: ToolCallingSessionRuntime) -> None:
    session = _completed_booking_session(runtime, language="ar")
    ai = PassiveAgent(reply="I need one more detail so I can help you correctly.")

    _route_live_message_with_gemini(session, "\u0639\u0627\u064a\u0632 \u0627\u062d\u062c\u0632", ai)

    reply = session.messages[-1]["text"]
    assert ai.calls == []
    assert "\u062d\u062c\u0632 \u062c\u062f\u064a\u062f" in reply
    assert "\u0627\u0644\u062d\u062c\u0632 \u0627\u0644\u062d\u0627\u0644\u064a" in reply
    assert "I need one more detail" not in reply
    assert session.stage == "post_booking_support"


def test_empty_gemini_arabic_fallback_is_arabic() -> None:
    reply = _sanitize_gemini_reply("", "ar")

    assert "\u0645\u062d\u062a\u0627\u062c" in reply
    assert "I need one more detail" not in reply


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


def test_arabic_greeting_while_phone_required_does_not_fall_to_model_error(runtime: ToolCallingSessionRuntime) -> None:
    ai = FailingAgent()
    runtime._conversation_ai = ai
    session = runtime.create_session()

    session = _send(runtime, "\u0647\u0644\u0627", session)

    reply = session.messages[-1]["text"]
    assert ai.calls == []
    assert session.language == "ar"
    assert session.stage == "identity_required"
    assert "\u0648\u0627\u062a\u0633\u0627\u0628" in reply
    assert "\u0623\u0647\u0644\u0627" in reply
    assert "trouble completing" not in reply.lower()
    assert not session.fallback_used


@pytest.mark.parametrize("message", ["hello", "yes", "book"])
def test_identity_required_non_phone_text_stays_phone_first(runtime: ToolCallingSessionRuntime, message: str) -> None:
    ai = FailingAgent()
    runtime._conversation_ai = ai
    session = runtime.create_session()

    session = _send(runtime, message, session)

    reply = session.messages[-1]["text"]
    assert ai.calls == []
    assert session.stage == "identity_required"
    assert "whatsapp" in reply.lower()
    assert "trouble completing" not in reply.lower()
    assert runtime._read_only_tools.calls == []
    assert not runtime._write_executor.execute.called
    assert not session.fallback_used


def test_invalid_long_phone_is_rejected_before_identity_lookup(runtime: ToolCallingSessionRuntime) -> None:
    session = _send(runtime, "012922823692000")

    reply = session.messages[-1]["text"]
    assert session.stage == "identity_required"
    assert "valid whatsapp" in reply.lower()
    assert runtime._read_only_tools.calls == []
    workflow = (session.preview or {}).get("workflow") or {}
    assert workflow.get("lookup_status") == "invalid_phone"


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
    assert "boys" in reply.lower()
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
    assert "boys" in reply.lower()
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
    assert "CRM" not in reply
    # An Arabic reply must use the Arabic replacement; substituting the English
    # wording here produced mixed sentences like "... في our records".
    assert "سجلاتنا" in reply
    assert "our records" not in reply
    assert "1" in reply and "2" in reply
    assert session.language == "ar"
    assert session.selected_trip_id == "RT-LOC-26-900"
    assert not runtime._write_executor.execute.called


def test_unrecognized_group_size_input_never_enters_output_failure_loop(runtime: ToolCallingSessionRuntime) -> None:
    session = _selected_trip_session(runtime)
    session.language = "ar"
    session.stage = "group_size_required"
    session.room_group = "boys"
    session.room_type = "Double"
    session.preview["collection_state"].update({"room_group": True, "room_type": True, "group_size": False})
    runtime._conversation_ai = RewritingAgent(rewrite_reply="I will")

    session = _send(runtime, "\u0627\u0645\u0643 \u062d\u0644\u0648\u0647", session)

    reply = session.messages[-1]["text"]
    assert session.stage == "group_size_required"
    assert session.fallback_used is False
    assert "\u0645\u0639\u0644\u0634" not in reply
    assert "\u0627\u0644\u0645\u0633\u0627\u0641\u0631\u064a\u0646" in reply
    assert runtime._conversation_ai.rewrite_calls == []

def test_identity_question_answers_then_guides_back_to_missing_booking_detail(runtime: ToolCallingSessionRuntime) -> None:
    session = _selected_trip_session(runtime)

    session = _send(runtime, "who are u", session)

    reply = session.messages[-1]["text"]
    assert "Ravel Traveler" in reply
    assert "AI sales assistant" in reply
    assert "boys" in reply.lower()
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
    assert "exact trip name" in session.messages[-1]["text"]
    assert "trip ID" not in session.messages[-1]["text"]
    assert "database exploded" not in session.messages[-1]["text"]
    assert "RuntimeError" not in session.messages[-1]["text"]


def test_explicit_human_request_is_preserved_for_workflow(runtime: ToolCallingSessionRuntime) -> None:
    executor = FakeHandoffExecutor(succeeds=True)
    runtime._write_executor = executor
    session = _send(runtime, "I want human agent")

    assert len(executor.calls) == 1
    assert executor.calls[0]["action"] == "create_handoff"
    assert executor.calls[0]["payload"]["user_requested_human"] is True
    assert session.handoff_state == "handed_off"
    assert "sent" in session.messages[-1]["text"].lower()


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
    session.passport_number = "A1234567"
    session.passport_expiry = "2032-06-01"
    session.passport_nationality = "Egyptian"

    session = _send(runtime, "continue", session)
    assert session.stage == "booking_confirmation_required"
    assert "confirm" in session.messages[-1]["text"].lower()
    assert not runtime._write_executor.execute.called

    session = _send(runtime, "no", session)
    assert session.stage == "waiting"
    assert not session.booking_confirmed
    assert not runtime._write_executor.execute.called


def test_booking_confirmation_requested_handles_unclear_reply_without_model_fallback(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = runtime.create_session()
    session.stage = "collecting_context"
    session.booking_confirmation_requested = True
    session.raw_phone = "01112223333"
    session.country_code = "20"
    session.trip_type = "local"
    session.selected_trip_id = "RT-LOC-26-DEM"
    session.selected_trip_name = "DEMOO3"
    session.room_group = "girls"
    session.room_type = "Double"
    session.group_size = 2
    session.flight_option = "Not Applicable"
    runtime._conversation_ai = RewritingAgent(rewrite_reply="this model path should not run")

    session = _send(runtime, "without flight", session)

    assert session.stage == "booking_confirmation_required"
    assert session.booking_confirmation_requested is True
    assert not session.booking_confirmed
    assert not runtime._write_executor.execute.called
    assert "yes" in session.messages[-1]["text"].lower()
    assert "couldn't prepare" not in session.messages[-1]["text"].lower()

    session.stage = "collecting_context"
    session.booking_confirmation_requested = False
    session.messages[-1] = {
        "role": "assistant",
        "text": "Before I create the booking draft, please confirm these details. Do you confirm creating the booking draft?",
    }

    session = _send(runtime, "without flight", session)

    assert session.stage == "booking_confirmation_required"
    assert not session.booking_confirmed
    assert "yes" in session.messages[-1]["text"].lower()
    assert "couldn't prepare" not in session.messages[-1]["text"].lower()
