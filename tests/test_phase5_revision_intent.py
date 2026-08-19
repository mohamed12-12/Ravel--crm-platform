from __future__ import annotations

from services.ai_agent.ai_agent_app.agent.session_flow import SessionState
from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
from tests.test_phase12_booking_state import _by_gender, _selected_trip_session, _send, runtime as runtime


def test_off_script_classifier_accepts_field_change_category() -> None:
    from services.ai_agent.ai_agent_app.agent.gemini_agent import OFF_SCRIPT_CLASSIFIER_CATEGORIES

    assert "correction_field_change" in OFF_SCRIPT_CLASSIFIER_CATEGORIES


def _mark_booking_fields_collected(runtime: ToolCallingSessionRuntime, session: SessionState) -> None:
    runtime._update_collection_state(
        session,
        room_group=bool(session.room_group),
        room_type=bool(session.room_type),
        group_size=True,
        group_nationality_type=bool(session.group_nationality_type),
        flight_option=bool(session.flight_option),
        currency=bool(session.currency),
    )


def _confirmation_session(runtime: ToolCallingSessionRuntime) -> SessionState:
    session = _selected_trip_session(runtime)
    session.language = "en"
    session.customer_name = "Mona Ali"
    session.birthday = "1990-01-01"
    session.nationality = "Egyptian"
    session.room_group = "boys"
    session.room_type = "Single"
    session.group_size = 2
    session.group_nationality_type = "single"
    session.flight_option = "Not Applicable"
    session.currency = "EGP"
    _mark_booking_fields_collected(runtime, session)
    runtime._ensure_room_requirements_for_group(session)
    session.stage = "booking_confirmation_required"
    session.booking_confirmation_requested = True
    session.messages.append({"role": "assistant", "text": runtime._booking_confirmation_summary(session)})
    return session


def test_room_type_revision_at_confirmation_recomputes_room_requirements(runtime: ToolCallingSessionRuntime) -> None:
    session = _confirmation_session(runtime)
    assert session.room_type == "Single"

    session = _send(runtime, "I want double not single", session)

    assert session.room_type == "Double"
    assert session.booking_confirmed is False
    assert session.stage == "booking_confirmation_required"
    assert _by_gender(session)["boys"] == {"room_type": "Double", "room_group": "boys", "rooms": 1}
    assert "Updated room type" in session.messages[-1]["text"]


def test_group_size_revision_invalidates_stale_room_count(runtime: ToolCallingSessionRuntime) -> None:
    session = _confirmation_session(runtime)
    session.room_type = "Double"
    session.room_requirements = {}
    runtime._ensure_room_requirements_for_group(session)
    assert _by_gender(session)["boys"]["rooms"] == 1

    session = _send(runtime, "actually make it 4 travelers", session)

    assert session.group_size == 4
    assert _by_gender(session)["boys"] == {"room_type": "Double", "room_group": "boys", "rooms": 2}
    assert session.booking_confirmed is False


def test_gender_revision_clears_room_type_and_reasks_rooming(runtime: ToolCallingSessionRuntime) -> None:
    session = _confirmation_session(runtime)
    session.room_group = "girls"
    session.room_type = "Double"
    session.room_requirements = {}
    _mark_booking_fields_collected(runtime, session)
    runtime._ensure_room_requirements_for_group(session)

    session = _send(runtime, "boys not girls", session)

    assert session.room_group == "boys"
    assert session.room_type == ""
    assert session.room_requirements == {}
    assert session.stage == "room_type_required"
    assert session.booking_confirmation_requested is False


def test_currency_revision_keeps_booking_details_and_returns_to_confirmation(runtime: ToolCallingSessionRuntime) -> None:
    session = _confirmation_session(runtime)
    assert session.currency == "EGP"

    session = _send(runtime, "pay in USD instead", session)

    assert session.currency == "USD"
    assert session.room_type == "Single"
    assert session.group_size == 2
    assert session.stage == "booking_confirmation_required"
    assert "Updated currency" in session.messages[-1]["text"]


def test_currency_revision_handles_dollar_negation(runtime: ToolCallingSessionRuntime) -> None:
    session = _confirmation_session(runtime)
    session.currency = "USD"
    _mark_booking_fields_collected(runtime, session)

    session = _send(runtime, "EGP not $", session)

    assert session.currency == "EGP"
    assert session.stage == "booking_confirmation_required"
    assert "Updated currency" in session.messages[-1]["text"]


def test_first_time_group_size_answer_is_not_mislabeled_as_revision(runtime: ToolCallingSessionRuntime) -> None:
    session = _selected_trip_session(runtime)
    session.language = "en"
    session.room_group = "boys"
    session.room_type = "Double"
    runtime._update_collection_state(session, room_group=True, room_type=True, group_size=False)
    session.stage = "group_size_required"

    session = _send(runtime, "4", session)

    assert session.group_size == 4
    assert "Updated traveler count" not in session.messages[-1]["text"]
