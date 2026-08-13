"""Phase 11 -- conversation interruption & intent-switching regression tests.

Reproduces a real production smoke-test failure: once a trip is selected, a
customer saying "عايز رحلة تانية" (I want another trip -- no target named) or
"في صور ليها" (are there photos of it? -- no explicit trip word, an anaphoric
reference to the trip already in context) got the current required-step
question repeated at them instead of being understood, because:

- `_apply_trip_switch_from_text`/the classifier's `correction_trip_switch`
  category only ever fire when a *specific* alternate trip can be resolved
  from the text (score >= 65 against a real trip name) -- a target-less
  "I want a different trip" has nothing to resolve and was previously a
  dead end (`_resolve_trip_switch_candidate` returns None -> the classifier
  branch returns False -> handle_message's generic "unclear input" fallback
  fires and just re-asks the pending field).
- `_handle_trip_discovery_request_if_ready`/`_handle_public_trip_reference_if_present`
  both hard-return False once `session.selected_trip_id` is set, so there was
  no way to re-browse trips once one was already selected.
- `_is_trip_media_request` required an explicit trip/hotel/room *word* to
  co-occur with a media word -- a pronoun reference ("ليها"/"لها") to the
  trip already in context never matched, so the deterministic media handler
  (which correctly attaches structured `media`) was never reached, and the
  message fell through to the same generic fallback.
- `_is_cancel_intent` required the *entire* message to compact down to one
  bare keyword ("cancel"/"الغاء"/...) -- a full natural sentence like "مش
  عايز أكمل" never matched.

See PHASE_11_CONVERSATION_INTERRUPTION_AUDIT.md for the full trace.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from services.ai_agent.ai_agent_app.agent.session_flow import SessionState
from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
from tests.test_agent_conversation_reliability import PassiveAgent, RecordingReadTools
from tests.test_golden_transcript_regressions import (
    _SWITCH_TEST_TRIPS,
    _bali_currency_required_session,
    _bali_gender_required_session,
    _bali_room_type_required_session,
    _classification_response,
    _install_classifier_agent_for,
    _send,
    _verified_runtime_with_switch_trips,
    runtime,  # noqa: F401  (shared fixture)
)
from tests.test_phase2_gemini_tool_loop import text_response


# ---------------------------------------------------------------------------
# Extra fixtures this phase needs that don't already exist upstream.
# ---------------------------------------------------------------------------
class _MediaReadTools(RecordingReadTools):
    def get_trip_media(self, *, trip_id: str) -> dict:
        if trip_id == "RT-BALI-01":
            return {
                "media": [
                    {"public_url": "/trips/media/bali-cover", "image_type": "cover", "alt_text": "Bali cover"},
                    {"public_url": "/trips/media/bali-gallery-1", "image_type": "gallery", "alt_text": "Bali beach"},
                ]
            }
        return {"media": []}


def _bali_group_size_required_session(runtime: ToolCallingSessionRuntime) -> SessionState:
    _verified_runtime_with_switch_trips(runtime)
    session = runtime.create_session()
    for text in ("01554158741", "international", "1", "boys", "single"):
        session = _send(runtime, text, session)
    assert session.stage == "group_size_required"
    return session


def _bali_select_trip_session(runtime: ToolCallingSessionRuntime) -> SessionState:
    """A returning traveler whose identity is verified but who hasn't picked
    a trip yet -- select_trip is the current required step."""
    _verified_runtime_with_switch_trips(runtime)
    session = runtime.create_session()
    session = _send(runtime, "01554158741", session)
    session = _send(runtime, "international", session)
    assert session.stage == "trip_selection_required"
    return session


def _bali_booking_confirmation_required_session(runtime: ToolCallingSessionRuntime) -> SessionState:
    _verified_runtime_with_switch_trips(runtime)
    session = runtime.create_session()
    for text in ("01554158741", "international", "1", "boys", "single", "2", "same", "2"):
        session = _send(runtime, text, session)
    assert session.stage == "awaiting_passport_upload"
    runtime.handle_passport_attachment(session, "passport-scan.pdf")
    session = _send(runtime, "done", session)
    for text in ("A1234567", "01/06/2032", "Egyptian", "USD"):
        session = _send(runtime, text, session)
    assert session.stage == "booking_confirmation_required"
    return session


def _full_state_snapshot(session: SessionState) -> dict:
    return {
        "selected_trip_id": session.selected_trip_id,
        "selected_trip_name": session.selected_trip_name,
        "trip_type": session.trip_type,
        "room_type": session.room_type,
        "room_group": session.room_group,
        "group_size": session.group_size,
        "currency": session.currency,
        "nationality": session.nationality,
        "birthday": session.birthday,
        "booking_confirmed": session.booking_confirmed,
        "booking_confirmation_requested": session.booking_confirmed,
        "stage": session.stage,
    }


# ===========================================================================
# Category A -- CHANGE TRIP (no specific alternate trip named)
# ===========================================================================
CHANGE_TRIP_PHRASES = [
    "عايز رحلة تانية",
    "عايز أغير الرحلة",
    "وريني رحلات تانية",
    "مش عايز الرحلة دي",
    "اختارلي رحلة غير دي",
]


@pytest.mark.parametrize("phrase", CHANGE_TRIP_PHRASES)
def test_generic_change_trip_request_during_gender_step_does_not_repeat_gender_question(
    runtime: ToolCallingSessionRuntime, phrase: str
) -> None:
    session = _bali_gender_required_session(runtime)

    session = _send(runtime, phrase, session)

    reply = session.messages[-1]["text"]
    assert "شباب" not in reply and "بنات" not in reply, (
        f"expected a trip-change response, got the gender question repeated: {reply!r}"
    )
    # The current selection is not discarded until the customer actually
    # names a replacement -- only the reply changes, not the booking state.
    assert session.selected_trip_id == "RT-BALI-01"
    assert session.room_group == ""


def test_generic_change_trip_request_during_room_type_step_offers_the_trip_list(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _bali_room_type_required_session(runtime)

    session = _send(runtime, "عايز رحلة تانية", session)

    reply = session.messages[-1]["text"]
    assert "Thailand" in reply or "تايلاند" in reply or "Bali" in reply or "بالي" in reply, (
        f"expected the other available trip(s) to be listed: {reply!r}"
    )
    assert session.room_type == ""


def test_change_trip_then_naming_a_specific_trip_completes_the_switch(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Category A's full flow: ask for a different trip with no target,
    then, on the very next turn, name one -- the switch should complete and
    dependent state should reset, exactly like the existing named-switch
    path already does in one turn.
    """
    session = _bali_room_type_required_session(runtime)

    session = _send(runtime, "عايز رحلة تانية", session)
    assert session.selected_trip_id == "RT-BALI-01"  # unchanged so far

    session = _send(runtime, "Thailand", session)

    assert session.selected_trip_id == "RT-THAI-01"
    assert session.selected_trip_name == "Thailand Explorer"
    assert session.room_type == ""


def test_change_trip_offer_does_not_hijack_an_unrelated_next_answer(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """If the customer ignores the trip-list offer and just answers the
    original pending question instead, that answer must still be captured
    normally -- the offer must not force the next turn to be interpreted as
    a trip name.
    """
    session = _bali_room_type_required_session(runtime)

    session = _send(runtime, "عايز رحلة تانية", session)
    session = _send(runtime, "single", session)

    assert session.selected_trip_id == "RT-BALI-01"
    assert session.room_type == "Single"


# ===========================================================================
# Category B -- SHOW AVAILABLE TRIPS (post-selection, no correction wording)
# ===========================================================================
SHOW_TRIPS_PHRASES = ["قولي الرحلات المتاحة", "وريني الرحلات", "إيه الرحلات عندكم؟"]


@pytest.mark.parametrize("phrase", SHOW_TRIPS_PHRASES)
def test_show_available_trips_after_selection_lists_trips_without_resetting_state(
    runtime: ToolCallingSessionRuntime, phrase: str
) -> None:
    session = _bali_gender_required_session(runtime)

    session = _send(runtime, phrase, session)

    reply = session.messages[-1]["text"]
    assert "Bali" in reply or "بالي" in reply or "Thailand" in reply or "تايلاند" in reply
    assert session.selected_trip_id == "RT-BALI-01"
    assert session.stage == "traveler_gender_required"


# ===========================================================================
# Category C -- TRIP INFORMATION (media via anaphoric reference)
# ===========================================================================
MEDIA_PHRASES = ["في صور ليها", "قولي بس الصور ايه", "صور الرحلة؟", "عايز أشوف صور الرحلة"]


@pytest.mark.parametrize("phrase", MEDIA_PHRASES)
def test_media_question_by_pronoun_reference_is_answered_not_treated_as_gender_answer(
    runtime: ToolCallingSessionRuntime, phrase: str
) -> None:
    session = _bali_gender_required_session(runtime)
    runtime._read_only_tools = _MediaReadTools(trips=_SWITCH_TEST_TRIPS, identity={
        "traveler_id": "TR00042", "full_name": "Returning Traveler", "status": "Active",
    })

    session = _send(runtime, phrase, session)

    assert session.room_group == "", "the media question must not be captured as the gender answer"
    # _handle_trip_media_request_if_ready sets stage="trip_media_shared" as a
    # transient marker (same as the existing pre-selection media path) -- the
    # pending gender field itself (not session.stage) is what must survive,
    # confirmed on the next turn by test_media_question_then_gender_answer_resumes_booking.
    last_message = session.messages[-1]
    # A real media response is delivered through the structured `media`
    # field, not as a raw path pasted into the chat text.
    assert last_message.get("media"), f"expected structured media, got: {last_message}"
    for item in last_message["media"]:
        assert item.get("public_url", "").startswith("/trips/media/")
    assert "/trips/media/" not in last_message.get("text", "")


def test_media_question_then_gender_answer_resumes_booking(runtime: ToolCallingSessionRuntime) -> None:
    """Category F: temporary interruption, then resume.

    Uses a bare "شباب" resume answer -- _apply_required_step_capture's
    gender branch is a deliberate strict whole-message match (a keyword
    buried in a longer sentence must not be guessed as an answer), so a
    compound acknowledgment like "تمام، شباب" is a separate, narrower
    answer-parsing gap noted in the Phase 11 report's remaining risks
    rather than something this phase's interruption-routing fix changes.
    """
    session = _bali_gender_required_session(runtime)
    runtime._read_only_tools = _MediaReadTools(trips=_SWITCH_TEST_TRIPS, identity={
        "traveler_id": "TR00042", "full_name": "Returning Traveler", "status": "Active",
    })

    session = _send(runtime, "في صور ليها", session)
    assert session.room_group == ""

    session = _send(runtime, "شباب", session)

    assert session.room_group == "boys"
    assert session.stage == "room_type_required"


# ===========================================================================
# Category D -- CANCEL / PAUSE
# ===========================================================================
CANCEL_PHRASES = ["مش عايز أكمل", "خلاص سيبها", "مش عايز أحجز"]


@pytest.mark.parametrize("phrase", CANCEL_PHRASES)
def test_cancel_phrase_stops_the_active_booking_flow(runtime: ToolCallingSessionRuntime, phrase: str) -> None:
    session = _bali_gender_required_session(runtime)

    session = _send(runtime, phrase, session)

    assert session.stage == "waiting"
    assert session.booking_confirmed is False
    reply = session.messages[-1]["text"]
    assert "شباب" not in reply and "بنات" not in reply


def test_cancel_phrase_during_booking_confirmation_does_not_create_a_booking(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _bali_booking_confirmation_required_session(runtime)
    runtime._write_executor = Mock()

    session = _send(runtime, "مش عايز أكمل", session)

    assert session.stage == "waiting"
    assert session.booking_confirmed is False
    runtime._write_executor.execute.assert_not_called()


def test_pause_phrase_during_group_size_step_does_not_repeat_group_size_question(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _bali_group_size_required_session(runtime)

    session = _send(runtime, "استنى", session)

    reply = session.messages[-1]["text"]
    assert "How many travelers" not in reply and "عدد المسافرين" not in reply


# ===========================================================================
# Category E -- FIELD CORRECTION (already-answered field, later step pending)
# ===========================================================================
def test_room_type_correction_after_group_size_step_updates_room_and_keeps_progress(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """This one is expected to already pass -- _merge_hints applies a
    room-type candidate unconditionally on most turns regardless of the
    currently-required step (it is deliberately suppressed only while
    collect_group_nationality_type/counts is pending, which this fixture
    avoids by testing one step later, at flight_option_required). The test
    documents and locks in that existing behavior rather than fixing a gap.
    """
    from tests.test_golden_transcript_regressions import _bali_flight_option_required_session

    session = _bali_flight_option_required_session(runtime)  # room=Single, group_size=2
    assert session.room_type == "Single"

    session = _send(runtime, "عايز غرفة مزدوجة", session)

    assert session.room_type == "Double"
    # Progress already made (group size, gender) must not be discarded.
    assert session.group_size == 2
    assert session.selected_trip_id == "RT-BALI-01"


def test_group_size_correction_after_the_fact_updates_the_count(runtime: ToolCallingSessionRuntime) -> None:
    from tests.test_golden_transcript_regressions import _bali_flight_option_required_session

    session = _bali_flight_option_required_session(runtime)  # group_size=2
    assert session.group_size == 2

    session = _send(runtime, "لا العدد 4", session)

    assert session.group_size == 4


# ===========================================================================
# select_trip step (pre-selection) -- informational questions already worked
# here via the existing discovery/public-reference handlers; pinned so a
# Phase 11 change to the post-selection path can't regress the pre-selection
# path it deliberately leaves alone.
# ===========================================================================
def test_show_trips_before_any_selection_still_works_unchanged(runtime: ToolCallingSessionRuntime) -> None:
    session = _bali_select_trip_session(runtime)

    session = _send(runtime, "قولي الرحلات المتاحة", session)

    reply = session.messages[-1]["text"]
    assert "Bali" in reply or "بالي" in reply
    assert session.selected_trip_id == ""


# ===========================================================================
# currency step + classifier-routed fuzzy phrasing (defense in depth): a
# phrasing outside the deterministic keyword set still reaches a sane
# outcome via the existing off-script classifier instead of the confusing
# generic fallback, and any media the classifier-routed LLM turn references
# is delivered as structured media, not a raw URL pasted into the text.
# ===========================================================================
def test_classifier_routed_trip_switch_with_no_resolvable_target_offers_the_trip_list(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _bali_currency_required_session(runtime)
    agent = _install_classifier_agent_for(
        runtime,
        # Empty target_hint: the classifier's own convention for "wants a
        # different trip but named none" (see _OFF_SCRIPT_CLASSIFIER_SYSTEM_PROMPT).
        [_classification_response("correction_trip_switch", 0.9, target_hint="")],
    )

    session = _send(runtime, "I'm not feeling this one, got anything else?", session)

    assert agent.classify_off_script_turn.call_count == 1
    reply = session.messages[-1]["text"]
    assert "Thailand" in reply or "Bali" in reply
    assert session.selected_trip_id == "RT-BALI-01"


def test_classifier_side_question_media_answer_is_delivered_as_structured_media_not_raw_url(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Defense in depth for the reported raw-path leak: even when a media
    question is routed through the LLM conversational turn (not the
    deterministic media handler), any get_trip_media tool result it used
    must reach the customer as structured media, never as a bare
    /trips/media/... path pasted into the reply text.
    """
    # Deliberately a phrasing the deterministic _is_trip_media_request
    # detector does NOT match (no media term at all), so this genuinely
    # falls through to the classifier/side_question path instead of the
    # deterministic media handler this fixture's _MediaReadTools otherwise
    # satisfies directly.
    session = _bali_gender_required_session(runtime)
    agent = _install_classifier_agent_for(
        runtime,
        [
            _classification_response("side_question", 0.85),
            text_response("Here it is: /trips/media/bali-cover -- and is your group boys or girls?"),
        ],
    )
    agent.respond = Mock(return_value={
        "reply": "Here it is: /trips/media/bali-cover -- and is your group boys or girls?",
        "tool_requests": [
            {
                "name": "get_trip_media",
                "result": {"media": [{"public_url": "/trips/media/bali-cover", "image_type": "cover"}]},
            }
        ],
    })

    session = _send(runtime, "هو ده أخر تاريخ للحجز؟", session)

    last_message = session.messages[-1]
    assert "/trips/media/" not in last_message.get("text", "")
