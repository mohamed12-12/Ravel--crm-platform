"""Part B of the exploratory-question fix: a general safety net so future
variants of the trip-type corruption bug (see
test_agent_conversation_reliability.py's exploratory-question tests)
degrade gracefully instead of reaching a full conversational breakdown.
"""

from __future__ import annotations

from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime

from tests.test_agent_conversation_reliability import (
    TRIPS,
    FakeHandoffExecutor,
    RecordingReadTools,
    _selected_trip_session,
    _send,
    runtime,  # noqa: F401 -- pytest fixture
)


def test_hypothetical_room_type_question_does_not_mutate_state(runtime: ToolCallingSessionRuntime) -> None:
    """Same default-to-read-only discipline as trip type, applied to room
    type: a hypothetical mention must not be captured as a decision.
    """
    session = _selected_trip_session(runtime, TRIPS[1])
    session.room_group = "boys"
    session.stage = "room_type_required"

    session = _send(runtime, "لو عايز غرفة triple هل تكون متاحة؟", session)

    assert session.room_type == ""


def test_hypothetical_room_group_question_does_not_mutate_state(runtime: ToolCallingSessionRuntime) -> None:
    session = _selected_trip_session(runtime, TRIPS[1])
    session.room_group = ""
    session.stage = "traveler_gender_required"

    session = _send(runtime, "لو حجزنا غرفة بنات هل السعر يختلف؟", session)

    assert session.room_group == ""


def test_hypothetical_flight_option_question_does_not_mutate_state(runtime: ToolCallingSessionRuntime) -> None:
    session = _selected_trip_session(runtime, TRIPS[0])  # international trip -- flight is relevant
    session.stage = "flight_option_required"

    session = _send(runtime, "لو حجزت بدون طيران هل السعر يقل؟", session)

    assert session.flight_option == ""


def test_two_consecutive_contradictions_auto_escalate_to_human_handoff(runtime: ToolCallingSessionRuntime) -> None:
    """B.1/B.2: a reply that contradicts a confirmed session fact (the
    exact shape from the real incident -- claiming "no trips of type X"
    while a trip of the OPPOSITE type is already selected) must never be
    sent as-is. The first occurrence gets a grounding recovery reply; a
    second consecutive one auto-escalates to human handoff instead of
    continuing to loop on the customer.
    """
    executor = FakeHandoffExecutor(succeeds=True)
    runtime._write_executor = executor
    local_trip = dict(TRIPS[1])
    session = _selected_trip_session(runtime, local_trip)
    assert session.trip_type == "local"

    runtime._append_authoritative_reply(
        session, message_key="test.contradiction", base_text="Sorry, no international trips available right now."
    )
    first_reply = session.messages[-1]["text"]
    assert "no international trip" not in first_reply.lower()
    assert session.contradiction_strikes == 1
    assert len(executor.calls) == 0

    runtime._append_authoritative_reply(
        session, message_key="test.contradiction", base_text="Sorry, no international trips available right now."
    )
    second_reply = session.messages[-1]["text"]
    assert session.contradiction_strikes == 0
    assert len(executor.calls) == 1
    assert executor.calls[0]["action"] == "create_handoff"
    assert second_reply != first_reply
    assert "team" in second_reply.lower() or "ravel" in second_reply.lower()


def test_contradiction_detection_resets_after_a_clean_reply(runtime: ToolCallingSessionRuntime) -> None:
    """A single blocked reply must not, by itself, escalate -- only 2
    CONSECUTIVE contradictions should. A clean reply in between resets the
    counter.
    """
    executor = FakeHandoffExecutor(succeeds=True)
    runtime._write_executor = executor
    session = _selected_trip_session(runtime, TRIPS[1])

    runtime._append_authoritative_reply(
        session, message_key="test.contradiction", base_text="Sorry, no international trips available right now."
    )
    assert session.contradiction_strikes == 1

    runtime._append_authoritative_reply(session, message_key="test.clean", base_text="All good, let's continue.")
    assert session.contradiction_strikes == 0

    runtime._append_authoritative_reply(
        session, message_key="test.contradiction", base_text="Sorry, no international trips available right now."
    )
    assert session.contradiction_strikes == 1
    assert len(executor.calls) == 0
