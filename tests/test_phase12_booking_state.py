"""Phase 12 -- booking state correctness for group/room composition.

Reproduces a real production transcript: once a trip is selected, a customer
saying "احنا جروب 2 بنات و2 رجال فعايزين غرفتين دابل بنات وبرضو رجال" (we are
a group of 2 girls and 2 men, so we want two double rooms, girls and also
men) should produce group_size=4, 2 boys + 2 girls, and TWO room
requirements (1 Double Boys + 1 Double Girls). Instead the system produced
only "1 غرفة Double بنات" -- the boys room silently vanished.

Root cause traced to `_extract_room_requirements`/`_extract_mixed_people_counts`:

- "رجال" (men) was entirely absent from every boys/male term list --
  "رجال" mentioned alongside "بنات" always produced a girls-only result.
- Python's `\\b` treats an Arabic letter and a following digit as the same
  "word" character class, so "و2" (an extremely common glued Arabic
  connector + number, "and 2") never matched a leading `\\b(number)` pattern
  at all -- "2 بنات و2 رجال" only ever saw the FIRST "2".
- Arabic number WORDS ("اتنين"/"اثنين"/...) were not recognized by these
  extractors' own number patterns (digits and English words only).
- `session.room_requirements` was replaced wholesale by whatever the
  current turn parsed (`_merge_hints`), so a second turn adding one more
  room for a different gender silently discarded the first one.

See PHASE_12_BOOKING_STATE_AUDIT.md for the full trace. The summary
(`_room_requirements_summary`), booking payload
(`_execute_booking_draft`'s payload), and pricing breakdown
(`workflow_policy.py::_pricing_breakdown_text`) already correctly consume
the full `requirements` list once it is populated correctly -- confirmed
here with payload-level assertions, not just displayed text.
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
from tests.test_agent_conversation_reliability import PassiveAgent, RecordingReadTools, TRIPS
from tests.test_phase11_demo_features import _make_app_with_db


MIXED_INVENTORY_TRIP = {
    "trip_id": "RT-LOC-MIXED-P12",
    "trip_name": "Phase 12 Mixed Inventory Demo",
    "type": "Local",
    "trip_type": "local",
    "start_date": "2026-08-25",
    "end_date": "2026-08-30",
    "available_single": 3,
    "boys_double": 3,
    "girls_double": 3,
    "boys_triple": 2,
    "girls_triple": 2,
}


@pytest.fixture()
def runtime(tmp_path: Path):
    original_env = dict(os.environ)
    os.environ["AI_AGENT_MODE"] = "tool_calling"
    client, app = _make_app_with_db(tmp_path / uuid.uuid4().hex)
    settings = replace(app.config["SETTINGS"], ai_provider="none", gemini_api_key="")
    rt = ToolCallingSessionRuntime(settings=settings, conversation_ai=PassiveAgent())
    rt._read_only_tools = RecordingReadTools(trips=[MIXED_INVENTORY_TRIP, *TRIPS])
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
    trip = dict(trip or MIXED_INVENTORY_TRIP)
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
    rt._read_only_tools.identity = {"traveler_id": "TR100", "full_name": "Mona Ali", "status": "Active"}
    session.stage = "traveler_gender_required"
    return session


def _requirements(session: SessionState) -> list[dict]:
    data = session.room_requirements if isinstance(session.room_requirements, dict) else {}
    return list(data.get("requirements") or [])


def _by_gender(session: SessionState) -> dict[str, dict]:
    return {str(item.get("room_group") or "").lower(): item for item in _requirements(session)}


# ===========================================================================
# 1. Two different-gender double rooms, stated in one message
# ===========================================================================
def test_two_different_gender_double_rooms_in_one_message(runtime: ToolCallingSessionRuntime) -> None:
    session = _selected_trip_session(runtime)

    session = _send(runtime, "عايز غرفة دابل للبنات وغرفة دابل للشباب", session)

    by_gender = _by_gender(session)
    assert by_gender.keys() == {"boys", "girls"}
    assert by_gender["boys"] == {"room_type": "Double", "room_group": "boys", "rooms": 1}
    assert by_gender["girls"] == {"room_type": "Double", "room_group": "girls", "rooms": 1}
    assert session.room_requirements["boys_rooms_requested"] == 1
    assert session.room_requirements["girls_rooms_requested"] == 1


# ===========================================================================
# 2. Two room requirements preserved (not collapsed to one)
# ===========================================================================
def test_two_room_requirements_are_not_collapsed_to_one(runtime: ToolCallingSessionRuntime) -> None:
    session = _selected_trip_session(runtime)

    session = _send(runtime, "غرفتين دابل، واحدة للبنات وواحدة للرجالة", session)

    assert len(_requirements(session)) == 2


# ===========================================================================
# 3. Adding a second room on a later turn
# ===========================================================================
def test_adding_a_second_room_on_a_later_turn(runtime: ToolCallingSessionRuntime) -> None:
    session = _selected_trip_session(runtime)

    session = _send(runtime, "غرفة دابل بنات", session)
    assert _by_gender(session).keys() == {"girls"}

    session = _send(runtime, "وكمان غرفة دابل رجال", session)

    by_gender = _by_gender(session)
    assert by_gender.keys() == {"boys", "girls"}
    assert by_gender["girls"] == {"room_type": "Double", "room_group": "girls", "rooms": 1}
    assert by_gender["boys"] == {"room_type": "Double", "room_group": "boys", "rooms": 1}


# ===========================================================================
# 4. Replacing an existing room (explicit correction signal)
# ===========================================================================
def test_replacing_an_existing_room_with_a_correction_signal(runtime: ToolCallingSessionRuntime) -> None:
    session = _selected_trip_session(runtime)

    session = _send(runtime, "غرفة دابل بنات", session)
    assert _by_gender(session)["girls"]["room_type"] == "Double"

    session = _send(runtime, "لا خليها غرفة تريبل بنات", session)

    by_gender = _by_gender(session)
    assert by_gender.keys() == {"girls"}
    assert by_gender["girls"] == {"room_type": "Triple", "room_group": "girls", "rooms": 1}


# ===========================================================================
# 5. Correcting one room while a second, unrelated room is untouched
# ===========================================================================
def test_correcting_one_room_leaves_the_other_untouched(runtime: ToolCallingSessionRuntime) -> None:
    session = _selected_trip_session(runtime)
    session = _send(runtime, "غرفة دابل بنات وغرفة دابل رجال", session)
    assert _by_gender(session).keys() == {"boys", "girls"}

    session = _send(runtime, "لا خليها غرفة تريبل رجال", session)

    by_gender = _by_gender(session)
    assert by_gender["boys"] == {"room_type": "Triple", "room_group": "boys", "rooms": 1}
    assert by_gender["girls"] == {"room_type": "Double", "room_group": "girls", "rooms": 1}


def test_correcting_both_rooms_at_once_to_a_single_gender(runtime: ToolCallingSessionRuntime) -> None:
    """Task 4's last case: "لا، الغرفتين شباب" ("no, both rooms [are]
    boys") is a global correction applying to every existing requirement at
    once, not one more addition.
    """
    session = _selected_trip_session(runtime)
    session = _send(runtime, "غرفة دابل بنات وغرفة دابل رجال", session)
    assert _by_gender(session).keys() == {"boys", "girls"}

    session = _send(runtime, "لا، الغرفتين شباب", session)

    data = session.room_requirements
    requirements = data["requirements"]
    assert all(item["room_group"] == "boys" for item in requirements)
    assert data["girls_rooms_requested"] == 0
    assert sum(item["rooms"] for item in requirements) == 2
    assert data["boys_rooms_requested"] == 2


# ===========================================================================
# 6. Group composition + rooms together (the exact reported phrasing)
# ===========================================================================
def test_group_composition_and_rooms_together(runtime: ToolCallingSessionRuntime) -> None:
    session = _selected_trip_session(runtime)

    session = _send(runtime, "احنا جروب 2 بنات و2 رجال فعايزين غرفتين دابل بنات وبرضو رجال", session)

    assert session.group_size == 4
    assert session.room_group == "mixed"
    by_gender = _by_gender(session)
    assert by_gender["boys"] == {"room_type": "Double", "room_group": "boys", "rooms": 1}
    assert by_gender["girls"] == {"room_type": "Double", "room_group": "girls", "rooms": 1}


def test_mixed_group_counts_and_no_family_units_derive_gender_rooms(runtime: ToolCallingSessionRuntime) -> None:
    session = _selected_trip_session(runtime)

    session = _send(runtime, "3", session)
    assert session.stage == "gender_counts_required"

    session = _send(runtime, "2 boys and 2 girls", session)
    assert session.stage == "family_units_required"
    assert session.boys_count == 2
    assert session.girls_count == 2
    assert session.group_size == 4

    session = _send(runtime, "0", session)
    assert session.stage == "room_type_required"
    assert session.family_units == 0

    session = _send(runtime, "double", session)

    by_gender = _by_gender(session)
    assert by_gender["boys"] == {"room_type": "Double", "room_group": "boys", "rooms": 1}
    assert by_gender["girls"] == {"room_type": "Double", "room_group": "girls", "rooms": 1}
    assert session.room_requirements["boys_rooms_requested"] == 1
    assert session.room_requirements["girls_rooms_requested"] == 1


def test_mixed_family_units_share_rooms_before_gender_remainders(runtime: ToolCallingSessionRuntime) -> None:
    session = _selected_trip_session(runtime)

    session = _send(runtime, "mixed", session)
    session = _send(runtime, "2 boys and 2 girls", session)
    session = _send(runtime, "1 couple", session)
    session = _send(runtime, "double", session)

    by_gender = _by_gender(session)
    assert by_gender["family"] == {"room_type": "Double", "room_group": "family", "rooms": 1}
    assert by_gender["boys"] == {"room_type": "Double", "room_group": "boys", "rooms": 1}
    assert by_gender["girls"] == {"room_type": "Double", "room_group": "girls", "rooms": 1}
    assert session.room_requirements["family_units"] == 1


def test_mixed_group_gendered_capacity_shortage_escalates(runtime: ToolCallingSessionRuntime) -> None:
    trip = {
        **MIXED_INVENTORY_TRIP,
        "available_double": 4,
        "boys_double": 3,
        "girls_double": 0,
    }
    session = _selected_trip_session(runtime, trip)

    session = _send(runtime, "mixed", session)
    session = _send(runtime, "2 boys and 2 girls", session)
    session = _send(runtime, "0", session)
    session = _send(runtime, "double", session)

    assert session.stage == "capacity_handoff_required"
    assert session.handoff_state in {"handoff_pending", "handed_off", "handoff_failed", ""}


# ===========================================================================
# 7. Interruption between room selections (identity question) preserves both
# ===========================================================================
def test_interruption_between_room_selections_preserves_both(runtime: ToolCallingSessionRuntime) -> None:
    session = _selected_trip_session(runtime)
    session = _send(runtime, "غرفة دابل بنات", session)

    session = _send(runtime, "مين انت؟", session)
    assert _by_gender(session).keys() == {"girls"}, "the interruption must not touch existing room state"

    session = _send(runtime, "وكمان غرفة دابل رجال", session)

    assert _by_gender(session).keys() == {"boys", "girls"}


# ===========================================================================
# 8. Unrelated question between room selections preserves both
# ===========================================================================
def test_unrelated_question_between_room_selections_preserves_both(runtime: ToolCallingSessionRuntime) -> None:
    session = _selected_trip_session(runtime)
    session = _send(runtime, "غرفة دابل بنات", session)

    session = _send(runtime, "الفندق فين؟", session)
    assert _by_gender(session).keys() == {"girls"}

    session = _send(runtime, "وكمان غرفة دابل رجال", session)

    assert _by_gender(session).keys() == {"boys", "girls"}


# ===========================================================================
# 9. Trip change after room selection resets room requirements
# ===========================================================================
def test_trip_change_after_room_selection_resets_requirements(runtime: ToolCallingSessionRuntime) -> None:
    session = _selected_trip_session(runtime)
    session = _send(runtime, "غرفة دابل بنات وغرفة دابل رجال", session)
    assert len(_requirements(session)) == 2

    session = _send(runtime, f"Actually show me {TRIPS[0]['trip_name']} instead", session)

    assert session.selected_trip_id == TRIPS[0]["trip_id"]
    assert _requirements(session) == []


# ===========================================================================
# 10 & 11. Final booking payload and confirmation summary contain all rooms
# ===========================================================================
def _ready_for_confirmation_session(runtime: ToolCallingSessionRuntime) -> SessionState:
    session = _selected_trip_session(runtime)
    session = _send(runtime, "احنا جروب 2 بنات و2 رجال فعايزين غرفتين دابل بنات وبرضو رجال", session)
    session.flight_option = "Without Flight"
    session.currency = "USD"
    session.nationality = "Egyptian"
    session.group_nationality_type = "single"
    session.booking_confirmation_requested = True
    session.stage = "booking_confirmation_required"
    return session


def test_final_confirmation_summary_contains_both_rooms(runtime: ToolCallingSessionRuntime) -> None:
    session = _ready_for_confirmation_session(runtime)

    summary = runtime._booking_confirmation_summary(session)

    assert "عدد المسافرين: 4" in summary
    assert "Double" in summary
    boys_label = "شباب" if "الغرفة:" in summary else "boys"
    girls_label = "بنات" if "الغرفة:" in summary else "girls"
    assert boys_label in summary
    assert girls_label in summary


def test_final_booking_payload_contains_both_rooms(runtime: ToolCallingSessionRuntime) -> None:
    session = _ready_for_confirmation_session(runtime)
    session.booking_confirmed = True
    runtime._write_executor.execute.return_value = {
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
    }

    session = _send(runtime, "yes", session)

    assert runtime._write_executor.execute.called
    call_kwargs = runtime._write_executor.execute.call_args.kwargs
    assert call_kwargs["action"] == "create_booking_draft"
    payload = call_kwargs["payload"]
    assert payload["boys_rooms_requested"] == 1
    assert payload["girls_rooms_requested"] == 1
    requirements = payload["room_requirements"]["requirements"]
    by_gender = {item["room_group"]: item for item in requirements}
    assert by_gender["boys"] == {"room_type": "Double", "room_group": "boys", "rooms": 1}
    assert by_gender["girls"] == {"room_type": "Double", "room_group": "girls", "rooms": 1}
    assert payload["group_size"] == 4


# ===========================================================================
# 12. No room requirement is silently overwritten by an unrelated mention
# ===========================================================================
def test_no_room_requirement_silently_overwritten_without_a_correction_signal(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _selected_trip_session(runtime)
    session = _send(runtime, "غرفة دابل بنات", session)
    assert _by_gender(session)["girls"]["room_type"] == "Double"

    # Mentions "تريبل" and "بنات" together but with NO explicit correction
    # signal ("لا"/"خليها"/"instead"/...) -- must not silently replace the
    # already-recorded Double.
    session = _send(runtime, "هل غرفة تريبل بنات فيها تكييف؟", session)

    by_gender = _by_gender(session)
    assert by_gender["girls"] == {"room_type": "Double", "room_group": "girls", "rooms": 1}


# ===========================================================================
# Task 10 -- exact production transcript reproduction
# ===========================================================================
def test_production_transcript_reproduction(runtime: ToolCallingSessionRuntime) -> None:
    session = _selected_trip_session(runtime)

    session = _send(runtime, "احنا جروب 2 بنات و2 رجال فعايزين غرفتين دابل بنات وبرضو رجال", session)

    assert session.group_size == 4
    by_gender = _by_gender(session)
    assert by_gender["boys"] == {"room_type": "Double", "room_group": "boys", "rooms": 1}
    assert by_gender["girls"] == {"room_type": "Double", "room_group": "girls", "rooms": 1}

    session.flight_option = "Without Flight"
    session.currency = "USD"
    session.nationality = "Egyptian"
    session.group_nationality_type = "single"
    session.booking_confirmation_requested = True
    session.stage = "booking_confirmation_required"
    summary = runtime._booking_confirmation_summary(session)
    assert "عدد المسافرين: 4" in summary

    session.booking_confirmed = True
    runtime._write_executor.execute.return_value = {
        "executed": True,
        "result_id": "BK000002",
        "booking_id": "BK000002",
        "booking_status": "Draft",
        "write_result_contract": {
            "status": "success",
            "executed": True,
            "reused": False,
            "record_type": "booking",
            "record_id": "BK000002",
        },
    }
    session = _send(runtime, "yes", session)

    payload = runtime._write_executor.execute.call_args.kwargs["payload"]
    requirements = payload["room_requirements"]["requirements"]
    assert len(requirements) == 2
    by_gender = {item["room_group"]: item for item in requirements}
    assert by_gender["boys"]["rooms"] == 1
    assert by_gender["girls"]["rooms"] == 1
