"""Regression tests for the systematic field-capture audit.

Unlike tests/test_golden_transcript_regressions.py (each test pinned to one
literal live customer message), these tests pin the GENERAL POLICY every
required-step capture branch in _apply_required_step_capture must follow,
parameterized across the fields the policy applies to:

  A. A clarification question ("يعني ايه"/"what do you mean?") must never
     become the field's literal value.
  B. A natural-language answer (Arabic or English) must still be accepted --
     the audit's fixes must not force customers onto button/number replies.
  C. A numbered-choice answer must resolve from a bare digit ("3"), a
     labelled digit ("رقم 3"/"اختيار 3"), or an ordinal word ("التالت"),
     wherever the field presents a genuine fixed menu.
  D. A side question asked while a field is pending must not mutate that
     field -- it belongs to the existing off-script router.
  E. A correction to an already-answered field must still reset the right
     downstream state (regression guard on the audit's option_number ->
     menu_option_number rename not touching this mechanism).
  F. Off-topic text that happens to match an existing interruption/
     explanation lexicon entry must not be captured as the field's value.

See _is_not_a_field_value / _extract_option_number in tool_calling_runtime.py
and OPTION_NUMBER_PREFIX_TERMS / OPTION_ORDINAL_TERMS /
EXPLANATION_REQUEST_EXACT_TERMS in validation/lexicon.py for the shared
implementation these tests pin.
"""
from __future__ import annotations

import pytest

from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
from tests.test_phase12_booking_state import _send, runtime as runtime
from tests.test_phase6_private_trips import _private_verified_session


def _private_trip_type_required_session(rt: ToolCallingSessionRuntime):
    session = _private_verified_session(rt)
    session = _send(rt, "I want a private trip", session)
    assert session.stage == "trip_type_required"
    return session


def _private_service_type_required_session(rt: ToolCallingSessionRuntime):
    session = _private_trip_type_required_session(rt)
    session = _send(rt, "Local", session)
    assert session.stage == "private_service_type_required"
    return session


def _private_destination_required_session(rt: ToolCallingSessionRuntime):
    session = _private_service_type_required_session(rt)
    session = _send(rt, "3", session)
    assert session.stage == "private_destination_required"
    return session


# ---------------------------------------------------------------------------
# A. Clarification questions must never become the field's literal value.
# ---------------------------------------------------------------------------
CLARIFICATION_PHRASES = [
    "يعني ايه",
    "يعني إيه",
    "زي ايه",
    "مثل ايه",
    "تقصد ايه",
    "what do you mean?",
    "what does that mean?",
    "can you explain?",
]


@pytest.mark.parametrize("phrase", CLARIFICATION_PHRASES)
def test_clarification_question_is_not_captured_as_private_destination(
    runtime: ToolCallingSessionRuntime, phrase: str
) -> None:
    session = _private_destination_required_session(runtime)

    session = _send(runtime, phrase, session)

    assert session.stage == "private_destination_required"
    assert session.private_destination == ""


@pytest.mark.parametrize("phrase", CLARIFICATION_PHRASES)
def test_clarification_question_is_not_captured_as_a_traveler_name(
    runtime: ToolCallingSessionRuntime, phrase: str
) -> None:
    session = runtime.create_session()
    session = _send(runtime, "01270482380", session)
    assert session.stage == "traveler_not_found"

    session = _send(runtime, phrase, session)

    assert session.stage == "traveler_not_found"
    assert session.customer_name == ""


def test_an_explanation_request_without_a_question_mark_is_not_captured_as_a_name(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Every CLARIFICATION_PHRASES entry above happens to also be caught by
    the token-count ("يعني ايه" is 2 words) or the "?" check ("what do you
    mean?") that already existed before this audit -- neither exercises the
    _is_not_a_field_value consolidation itself. "what do you need" is 4
    tokens, has no "?", and contains no trip vocabulary, so only the
    consolidated _is_conversational_interruption check (via
    _is_explanation_request's lexicon entry) can reject it. (A phrase
    naming a human agent, e.g. "موظف", is not used here -- handle_message
    intercepts _is_human_agent_request globally before any stage-specific
    capture runs at all, so it would not exercise this branch either.)"""
    session = runtime.create_session()
    session = _send(runtime, "01270482380", session)
    assert session.stage == "traveler_not_found"

    session = _send(runtime, "what do you need", session)

    assert session.stage == "traveler_not_found"
    assert session.customer_name == ""


@pytest.mark.parametrize("phrase", CLARIFICATION_PHRASES)
def test_clarification_question_is_not_captured_as_trip_type(
    runtime: ToolCallingSessionRuntime, phrase: str
) -> None:
    session = _private_trip_type_required_session(runtime)

    session = _send(runtime, phrase, session)

    assert session.stage == "trip_type_required"
    assert session.trip_type == ""


# ---------------------------------------------------------------------------
# B. A natural-language answer, in Arabic or English, must still be
#    accepted -- the audit must not force customers onto buttons/numbers.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("answer", "expected_trip_type"),
    [("محلي", "local"), ("local", "local"), ("دولية", "international"), ("international", "international")],
)
def test_trip_type_accepts_a_natural_language_answer(
    runtime: ToolCallingSessionRuntime, answer: str, expected_trip_type: str
) -> None:
    session = _private_trip_type_required_session(runtime)

    session = _send(runtime, answer, session)

    assert session.trip_type == expected_trip_type
    assert session.stage == "private_service_type_required"


@pytest.mark.parametrize(
    ("answer", "expected_type"),
    [
        ("استشارة", "consultation"),
        ("consultation", "consultation"),
        ("برنامج كامل", "full_package"),
        ("مرافقة", "chaperone"),
    ],
)
def test_private_service_type_accepts_a_natural_language_answer(
    runtime: ToolCallingSessionRuntime, answer: str, expected_type: str
) -> None:
    """Previously Arabic-only: normalize_private_service_type had zero
    Arabic aliases, so a customer answering with the Arabic service name
    instead of a digit never resolved at all (see private_trips.py)."""
    session = _private_service_type_required_session(runtime)

    session = _send(runtime, answer, session)

    assert session.private_service_type == expected_type
    assert session.stage == "private_destination_required"


@pytest.mark.parametrize(("answer", "expected_currency"), [("دولار", "USD"), ("usd", "USD"), ("جنيه", "EGP")])
def test_currency_accepts_a_natural_language_answer(
    runtime: ToolCallingSessionRuntime, answer: str, expected_currency: str
) -> None:
    from tests.test_phase12_booking_state import _selected_trip_session

    session = _selected_trip_session(runtime)
    for text in ("boys", "double", "2", "same"):
        session = _send(runtime, text, session)
    assert session.stage == "currency_required"

    session = _send(runtime, answer, session)

    assert session.currency == expected_currency


# ---------------------------------------------------------------------------
# C. A numbered-choice answer must resolve from a bare digit, a labelled
#    digit, or an ordinal word, wherever the field is a genuine fixed menu.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("answer", ["3", "رقم 3", "اختيار 3", "التالت", "الثالث"])
def test_private_service_type_resolves_every_numbered_option_form(
    runtime: ToolCallingSessionRuntime, answer: str
) -> None:
    session = _private_service_type_required_session(runtime)

    session = _send(runtime, answer, session)

    assert session.private_service_type == "full_package"
    assert session.stage == "private_destination_required"


@pytest.mark.parametrize(("answer", "expected"), [("1", "local"), ("رقم 1", "local"), ("الاول", "local"), ("2", "international"), ("رقم 2", "international"), ("التاني", "international")])
def test_trip_type_resolves_every_numbered_option_form(
    runtime: ToolCallingSessionRuntime, answer: str, expected: str
) -> None:
    session = _private_trip_type_required_session(runtime)

    session = _send(runtime, answer, session)

    assert session.trip_type == expected


@pytest.mark.parametrize(("answer", "expected"), [("استمر", "continue"), ("كمل", "continue"), ("1", "continue"), ("جديد", "new"), ("2", "new")])
def test_duplicate_lead_choice_accepts_arabic_and_numbered_answers(
    runtime: ToolCallingSessionRuntime, answer: str, expected: str
) -> None:
    """_duplicate_lead_prompt (workflow_policy.py) asks this bilingually,
    but the answer-matching was English-word-only -- an Arabic "استمر"/
    "كمل" answer to a bilingual question never resolved."""
    session = runtime.create_session()
    session.stage = "duplicate_lead_choice_required"
    session._open_lead_id = "LD00001"

    session = _send(runtime, answer, session)

    assert session.duplicate_lead_choice == expected


def test_a_labelled_digit_inside_an_unrelated_sentence_is_not_captured() -> None:
    """"رقم 2" is a real menu answer on its own, but "الاوضة رقم 2 في
    الفيلا" ("room number 2 in the villa") must not resolve to option 2 --
    the same whole-answer discipline the bare-digit case already enforced
    (see the currency_required Phase 3C comment in tool_calling_runtime.py)
    must also cover the newly added labelled-digit/ordinal forms."""
    assert ToolCallingSessionRuntime._extract_option_number("الاوضة رقم 2 في الفيلا") == 0
    assert ToolCallingSessionRuntime._extract_option_number("room 2 please call me") == 0
    assert ToolCallingSessionRuntime._extract_option_number("رقم 2") == 2


def test_group_size_is_not_widened_by_the_menu_option_recognizer(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """group_size/private_party_size deliberately keep using the original
    bare-digit-only option_number, not the widened menu recognizer -- an
    ordinal word has no sensible meaning as a headcount, and "التالت" must
    not silently become "3 travelers"."""
    session = _private_service_type_required_session(runtime)
    session = _send(runtime, "3", session)
    session = _send(runtime, "Siwa", session)
    session = _send(runtime, "flexible", session)
    assert session.stage == "private_party_size_required"

    session = _send(runtime, "التالت", session)

    assert session.stage == "private_party_size_required"
    assert session.private_party_size in (0, None, "")


# ---------------------------------------------------------------------------
# D. A side question asked while a field is pending must not mutate it.
# ---------------------------------------------------------------------------
def test_side_question_during_private_service_type_does_not_mutate_the_field(
    runtime: ToolCallingSessionRuntime,
) -> None:
    from tests.test_golden_transcript_regressions import _classification_response, _install_classifier_agent_for
    from tests.test_phase2_gemini_tool_loop import text_response

    session = _private_service_type_required_session(runtime)
    agent = _install_classifier_agent_for(
        runtime,
        [_classification_response("side_question", 0.85), text_response("Sure! Which service would you like?")],
    )

    session = _send(runtime, "how many days is the trip usually?", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert session.private_service_type == ""
    assert session.stage == "private_service_type_required"


# ---------------------------------------------------------------------------
# E. A correction to an already-answered field must still reset the right
#    downstream state -- regression guard on the option_number ->
#    menu_option_number rename not touching _handle_revision_intent.
# ---------------------------------------------------------------------------
def test_correcting_currency_after_it_was_answered_still_works(
    runtime: ToolCallingSessionRuntime,
) -> None:
    from tests.test_phase12_booking_state import _selected_trip_session

    session = _selected_trip_session(runtime)
    for text in ("boys", "double", "2", "same", "EGP"):
        session = _send(runtime, text, session)
    assert session.currency == "EGP"

    session = _send(runtime, "actually, USD instead", session)

    assert session.currency == "USD"


# ---------------------------------------------------------------------------
# F. Off-topic text that matches an existing interruption/explanation
#    lexicon entry must not be captured as the field's value.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("phrase", ["مش فاهم", "thanks", "are you a bot?"])
def test_off_topic_interruption_is_not_captured_as_private_destination(
    runtime: ToolCallingSessionRuntime, phrase: str
) -> None:
    session = _private_destination_required_session(runtime)

    session = _send(runtime, phrase, session)

    assert session.stage == "private_destination_required"
    assert session.private_destination == ""
