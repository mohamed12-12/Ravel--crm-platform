"""Tier 3 gap-remediation fixes: strict enum matching (3.1), duplicate-lead
dialogue (3.2), distinct empty-trip-result messaging (3.3), and same-turn
room-capacity refresh (3.4).
"""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from services.ai_agent.ai_agent_app.agent.session_flow import SessionState
from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
from services.ai_agent.ai_agent_app.agent.workflow_policy import ConversationWorkflowPolicy
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
# Task 3.1 -- strict enum matching for gender/room/flight
# ---------------------------------------------------------------------------

def test_gender_numeric_and_exact_label_both_work(runtime: ToolCallingSessionRuntime) -> None:
    trip = dict(TRIPS[1])
    session = _selected_trip_session(runtime, trip)
    session.stage = "traveler_gender_required"
    session = _send(runtime, "1", session)
    assert session.room_group == "boys"

    session2 = _selected_trip_session(runtime, trip)
    session2.stage = "traveler_gender_required"
    session2 = _send(runtime, "girls", session2)
    assert session2.room_group == "girls"


def test_gender_synonym_word_buried_in_unrelated_sentence_is_not_guessed(runtime: ToolCallingSessionRuntime) -> None:
    # "man" was one of the old fuzzy boys_terms substring matches; a longer
    # unrelated sentence containing it as a substring ("mailman") must not be
    # guessed as an answer to the gender question. This deliberately avoids the
    # literal words "boys"/"girls", which a *separate* opportunistic hint
    # extractor (unrelated to this fix) still detects anywhere in a message.
    trip = dict(TRIPS[1])
    session = _selected_trip_session(runtime, trip)
    session.stage = "traveler_gender_required"

    session = _send(runtime, "not sure, my mailman came by earlier, what do you need?", session)

    assert session.room_group == ""
    assert session.stage == "traveler_gender_required"


def test_room_type_exact_answer_still_works_after_tightening(runtime: ToolCallingSessionRuntime) -> None:
    # Note: `_apply_required_step_capture`'s own aliases lookup is now a whole-
    # answer match (`aliases.get(lowered, "")` instead of substring-in-lowered),
    # which is the fix in scope here. The separate opportunistic hint extractor
    # (`_extract_hints`, used to pre-fill fields from a rich first message) still
    # does its own "single"/"double"/"triple" substring detection independently
    # of this capture path and was intentionally left alone, matching the
    # decision to preserve natural-language pre-fill and only tighten guessing
    # at the point a specific question's answer is captured.
    trip = {**TRIPS[1], "available_single": 2, "available_double": 4, "available_triple": 0}
    session = _selected_trip_session(runtime, trip)
    session.room_group = "boys"
    session.stage = "room_type_required"

    session = _send(runtime, "double", session)
    assert session.room_type == "Double"


# ---------------------------------------------------------------------------
# Task 3.2 -- duplicate-lead dialogue
# ---------------------------------------------------------------------------

class _OpenLeadReadTools(RecordingReadTools):
    def __init__(self, *, open_lead: dict | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.open_lead = open_lead

    def lookup_lead(self, *, lead_id: str = "", traveler_id: str = "", raw_phone: str = "", country_code: str = "") -> dict:
        self.calls.append({"name": "lookup_lead", "traveler_id": traveler_id, "raw_phone": raw_phone})
        return {"leads": [self.open_lead] if self.open_lead else []}


def _existing_traveler_session(rt: ToolCallingSessionRuntime, identity: dict) -> SessionState:
    rt._read_only_tools.identity = identity
    session = rt.create_session()
    return session


def test_existing_open_lead_triggers_choice_dialogue(runtime: ToolCallingSessionRuntime) -> None:
    identity = {"traveler_id": "TR00777", "full_name": "Youssef Kamal", "status": "Active"}
    runtime._read_only_tools = _OpenLeadReadTools(
        identity=identity,
        open_lead={"lead_id": "LD00500", "lead_stage": "Qualified", "preferred_trip_type": "international"},
    )
    session = runtime.create_session()

    session = _send(runtime, "01270482380", session)

    assert session.stage == "duplicate_lead_choice_required"
    assert "LD00500" in session.messages[-1]["text"]


def test_choosing_continue_resumes_existing_lead_and_skips_trip_type(runtime: ToolCallingSessionRuntime) -> None:
    identity = {"traveler_id": "TR00777", "full_name": "Youssef Kamal", "status": "Active"}
    runtime._read_only_tools = _OpenLeadReadTools(
        identity=identity,
        open_lead={"lead_id": "LD00500", "lead_stage": "Qualified", "preferred_trip_type": "international"},
    )
    session = runtime.create_session()
    session = _send(runtime, "01270482380", session)
    assert session.stage == "duplicate_lead_choice_required"

    session = _send(runtime, "1", session)

    assert session.duplicate_lead_choice == "continue"
    assert session.resumed_lead_id == "LD00500"
    # The old lead already had a trip-type preference on file -- it must not be
    # re-asked, only genuinely new information is collected from here.
    assert session.trip_type == "international"
    assert session.stage != "trip_type_required"


def test_choosing_new_proceeds_to_a_second_lead_deliberately(runtime: ToolCallingSessionRuntime) -> None:
    identity = {"traveler_id": "TR00777", "full_name": "Youssef Kamal", "status": "Active"}
    runtime._read_only_tools = _OpenLeadReadTools(
        identity=identity,
        open_lead={"lead_id": "LD00500", "lead_stage": "Qualified", "preferred_trip_type": "international"},
    )
    session = runtime.create_session()
    session = _send(runtime, "01270482380", session)
    assert session.stage == "duplicate_lead_choice_required"

    session = _send(runtime, "2", session)

    assert session.duplicate_lead_choice == "new"
    assert session.resumed_lead_id == ""
    assert session.stage == "trip_type_required"


# ---------------------------------------------------------------------------
# Task 3.3 -- distinct empty-trip-result messaging
# ---------------------------------------------------------------------------

def test_workflow_policy_distinguishes_not_yet_searched_from_zero_results() -> None:
    """Direct policy-level test isolating the exact mechanism (Task 3.3):
    a trip_result dict with both list keys present but empty means the search
    ran and found nothing; a missing/absent trip_result means it hasn't run yet.
    These must produce different states, not the same generic "checking" one."""
    policy = ConversationWorkflowPolicy()
    base_context = {
        "workflow": {
            "lookup_status": "found",
            "identity_verified": True,
            "verified_status": "Active",
            "verified_traveler": {"traveler_id": "TR00999", "status": "Active", "full_name": "Youssef Kamal"},
        },
        "known_traveler": {"traveler_id": "TR00999", "status": "Active", "full_name": "Youssef Kamal"},
        "trip_type": "local",
    }

    not_yet_searched = policy.evaluate({**base_context, "trip_result": {}})
    assert not_yet_searched.state == "trip_search_ready"
    assert not_yet_searched.required_step == "search_matching_trips"

    zero_results = policy.evaluate(
        {**base_context, "trip_result": {"open_trips": [], "date_tbd_trips": []}}
    )
    assert zero_results.state == "no_trips_available"
    assert zero_results.required_step == "handle_empty_trip_results"
    assert zero_results.state != not_yet_searched.state

    has_results = policy.evaluate(
        {**base_context, "trip_result": {"open_trips": [{"trip_id": "RT-LOC-1"}], "date_tbd_trips": []}}
    )
    assert has_results.state == "trip_selection_required"


def test_search_zero_results_reply_names_the_trip_type(runtime: ToolCallingSessionRuntime) -> None:
    identity = {"traveler_id": "TR00777", "full_name": "Youssef Kamal", "status": "Active"}
    read_tools = RecordingReadTools(identity=identity)
    read_tools.trips = []  # `trips=[]` in the constructor falls back to the TRIPS default (falsy check)
    runtime._read_only_tools = read_tools
    session = runtime.create_session()
    session = _send(runtime, "01270482380", session)

    session = _send(runtime, "1", session)  # local

    assert session.stage == "no_trips_available"
    reply = session.messages[-1]["text"].lower()
    assert "local" in reply
    assert "no active inventory" in reply or "do not have any available" in reply


def test_search_run_with_results_shows_the_trip_list(runtime: ToolCallingSessionRuntime) -> None:
    identity = {"traveler_id": "TR00777", "full_name": "Youssef Kamal", "status": "Active"}
    runtime._read_only_tools = RecordingReadTools(identity=identity)
    session = runtime.create_session()
    session = _send(runtime, "01270482380", session)

    session = _send(runtime, "1", session)  # local -> RT-LOC-26-900/901 exist

    assert session.stage == "trip_selection_required"
    assert "no local trips" not in session.messages[-1]["text"].lower()


# ---------------------------------------------------------------------------
# Task 3.4 -- same-turn room-capacity refresh
# ---------------------------------------------------------------------------

def test_headcount_capture_refreshes_trip_capacity_from_crm(runtime: ToolCallingSessionRuntime) -> None:
    trip = {**TRIPS[1], "available_single": 5}
    session = _selected_trip_session(runtime, trip)
    session.room_group = "boys"
    session.room_type = "Single"
    session.stage = "group_size_required"

    # CRM capacity has since dropped to 1 -- simulate a concurrent booking that
    # happened after the original trip search cached "available_single": 5.
    runtime._read_only_tools.trips = [{**trip, "available_single": 1}]

    session = _send(runtime, "3", session)

    calls = [c for c in runtime._read_only_tools.calls if c["name"] == "get_trip_details"]
    assert calls, "expected a fresh get_trip_details call when headcount was captured"
    refreshed = runtime._selected_trip(session)
    assert refreshed["available_single"] == 1


if __name__ == "__main__":
    import unittest

    unittest.main()
