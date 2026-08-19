"""Semantic capture & natural-language understanding for required-step answers.

Extends the existing off-script classifier (GeminiAgent.classify_off_script_turn
/ ToolCallingSessionRuntime._handle_off_script_classifier) rather than adding a
second engine: three new categories --

  answer_current_step    a paraphrase/colloquial/mixed-language answer to the
                          CURRENT question. candidate_value is untrusted LLM
                          output; it only ever becomes state if it passes
                          through the exact same deterministic capture an
                          exact-format answer would go through
                          (_apply_required_step_capture) -- no new validation
                          path, no new canonical-value list.
  ask_about_current_step  a question ABOUT a presented option ("what does the
                          third one include?") -- must never be treated as a
                          selection of that option.
  recommendation_request  "what do you recommend?" -- distinct from unclear;
                          the customer is engaged, not stuck.

Both ask_about_current_step and recommendation_request route to the SAME
grounded, tool-permitted conversational turn side_question already uses
(_run_conversational_llm_turn) -- no fabrication, no second answer-generation
path.

Also covers the two deterministic extraction upgrades this feature leans on
so semantic candidates in a "good" format actually validate: relative dates
(date_parsing.normalize_relative_date_input) and worded/shorthand budget
amounts (ToolCallingSessionRuntime._private_budget_from_text).
"""
from __future__ import annotations

from datetime import date

import pytest

from services.ai_agent.ai_agent_app.agent.date_parsing import normalize_relative_date_input
from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
from tests.test_field_capture_audit import (
    _private_service_type_required_session,
    _private_trip_type_required_session,
)
from tests.test_golden_transcript_regressions import (
    _bali_flight_option_required_session,
    _bali_gender_required_session,
    _bali_room_type_required_session,
    _classification_response,
    _verified_runtime_with_switch_trips,
    _install_classifier_agent_for,
)
from tests.test_phase12_booking_state import _send, runtime as runtime
from tests.test_phase2_gemini_tool_loop import text_response


# ---------------------------------------------------------------------------
# answer_current_step: a genuine paraphrase resolves, and advances the
# workflow exactly as a typed exact-format answer would.
# ---------------------------------------------------------------------------

def test_semantic_paraphrase_resolves_full_package_for_private_service_type(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _private_service_type_required_session(runtime)
    agent = _install_classifier_agent_for(
        runtime,
        [_classification_response("answer_current_step", 0.9, candidate_value="full_package")],
    )

    session = _send(runtime, "عايز حد يرتبلي كل حاجة من أولها لآخرها", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert session.private_service_type == "full_package"
    assert session.stage == "private_destination_required"


def test_semantic_paraphrase_resolves_local_trip_type(runtime: ToolCallingSessionRuntime) -> None:
    session = _private_trip_type_required_session(runtime)
    agent = _install_classifier_agent_for(
        runtime,
        [_classification_response("answer_current_step", 0.88, candidate_value="local")],
    )

    session = _send(runtime, "مش عايزين نتعدى حدود البلد خالص", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert session.trip_type == "local"
    assert session.stage == "private_service_type_required"


def test_semantic_reference_resolves_mixed_traveler_gender(runtime: ToolCallingSessionRuntime) -> None:
    session = _bali_gender_required_session(runtime)
    agent = _install_classifier_agent_for(
        runtime,
        [_classification_response("answer_current_step", 0.87, candidate_value="mixed")],
    )

    session = _send(runtime, "احنا خليط من الناس، مش صنف واحد بس", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert session.room_group == "mixed"
    assert session.stage != "traveler_gender_required"


def test_semantic_paraphrase_resolves_single_room_type(runtime: ToolCallingSessionRuntime) -> None:
    # RT-BALI-01 (this fixture's trip) only carries single-room inventory --
    # "single" is the one candidate that can actually validate here.
    session = _bali_room_type_required_session(runtime)
    agent = _install_classifier_agent_for(
        runtime,
        [_classification_response("answer_current_step", 0.9, candidate_value="single")],
    )

    session = _send(runtime, "كل واحد يفضل لوحده، مش عايزين نتشارك حجرة", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert session.room_type == "Single"
    assert session.stage == "group_size_required"


def test_semantic_paraphrase_resolves_with_flight_option(runtime: ToolCallingSessionRuntime) -> None:
    session = _bali_flight_option_required_session(runtime)
    agent = _install_classifier_agent_for(
        runtime,
        [_classification_response("answer_current_step", 0.9, candidate_value="With Flight")],
    )

    session = _send(runtime, "لسه مش حاجزين تذاكر، عايزينكم تدبروا موضوع الطيران", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert session.flight_option == "With Flight"
    assert session.stage != "flight_option_required"


def _bali_currency_required_session(runtime: ToolCallingSessionRuntime) -> None:
    """Reaches currency_required via the current (attachment-only) passport
    flow -- test_golden_transcript_regressions.py's own fixture of the same
    purpose still walks the conversational passport-number/country/expiry
    steps Phase 2 deleted, and pytest.skip()s itself as a result."""
    _verified_runtime_with_switch_trips(runtime)
    session = runtime.create_session()
    for text in ("01554158741", "international", "1", "boys", "single", "2", "same", "2"):
        session = _send(runtime, text, session)
    assert session.stage == "awaiting_passport_upload"
    runtime.handle_passport_attachment(session, "passport-scan.pdf")
    session = _send(runtime, "done", session)
    assert session.stage == "currency_required"
    return session


def test_semantic_paraphrase_resolves_usd_currency(runtime: ToolCallingSessionRuntime) -> None:
    session = _bali_currency_required_session(runtime)
    agent = _install_classifier_agent_for(
        runtime,
        [_classification_response("answer_current_step", 0.9, candidate_value="USD")],
    )

    session = _send(runtime, "فلوسي بالخارج، هدفع بيها هي أسهل ليا", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert session.currency == "USD"
    assert session.stage != "currency_required"


def test_semantic_english_answer_resolves_full_package(runtime: ToolCallingSessionRuntime) -> None:
    """Section 17's plain-English case: the classifier path works the same
    whether the paraphrase is Arabic, English, or mixed -- candidate_value
    re-validation does not care which language produced it."""
    session = _private_service_type_required_session(runtime)
    agent = _install_classifier_agent_for(
        runtime,
        [_classification_response("answer_current_step", 0.92, candidate_value="full_package")],
    )

    session = _send(runtime, "I want you to handle absolutely everything for me.", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert session.private_service_type == "full_package"


# ---------------------------------------------------------------------------
# Fail-closed: a candidate outside the field's real domain must never be
# guessed into state, even if the classifier proposed one.
# ---------------------------------------------------------------------------

def test_answer_current_step_with_an_invalid_candidate_is_not_applied(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _private_service_type_required_session(runtime)
    _install_classifier_agent_for(
        runtime,
        [_classification_response("answer_current_step", 0.9, candidate_value="platinum_vip_tier")],
    )

    session = _send(runtime, "عايز حاجة كويسة كده", session)

    assert session.private_service_type == ""
    assert session.stage == "private_service_type_required"


def test_answer_current_step_with_an_empty_candidate_is_not_applied(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _private_service_type_required_session(runtime)
    _install_classifier_agent_for(
        runtime,
        [_classification_response("answer_current_step", 0.9, candidate_value="")],
    )

    session = _send(runtime, "مش عارف والله", session)

    assert session.private_service_type == ""
    assert session.stage == "private_service_type_required"


def test_low_confidence_answer_current_step_is_not_applied(runtime: ToolCallingSessionRuntime) -> None:
    """The existing CLASSIFIER_CONFIDENCE_THRESHOLD gate (0.55) applies to
    the new categories exactly like the old ones -- a low-confidence
    "answer_current_step" is downgraded to unclear before dispatch, so the
    candidate is never even looked at."""
    session = _private_service_type_required_session(runtime)
    _install_classifier_agent_for(
        runtime,
        [_classification_response("answer_current_step", 0.3, candidate_value="full_package")],
    )

    session = _send(runtime, "عايز حاجة كويسة كده", session)

    assert session.private_service_type == ""
    assert session.stage == "private_service_type_required"


# ---------------------------------------------------------------------------
# ask_about_current_step / recommendation_request: grounded reply, never a
# state mutation, never mistaken for a selection.
# ---------------------------------------------------------------------------

def test_ask_about_current_step_does_not_select_the_referenced_option(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _private_service_type_required_session(runtime)
    before_reply_count = len(session.messages)
    agent = _install_classifier_agent_for(
        runtime,
        [
            _classification_response("ask_about_current_step", 0.92, target_hint="full_package"),
            text_response("الباكدج الكامل معناه إننا نرتب كل حاجة من الأول للآخر. تحب نمشي عليه؟"),
        ],
    )

    session = _send(runtime, "ده يشمل حاجات زيادة عن الحجوزات ولا نفس الموضوع؟", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert session.private_service_type == ""
    assert session.stage == "private_service_type_required"
    assert len(session.messages) > before_reply_count
    # Proves the dispatch actually reached _run_conversational_llm_turn (a
    # SECOND provider call, for the grounded reply itself) rather than
    # falling straight to the generic scripted re-ask with no further LLM
    # call at all -- the reply text itself may still legitimately fall back
    # to the deterministic per-step copy if the response guard judges the
    # model's own phrasing irrelevant, so the call count is the honest signal.
    assert len(agent.provider.calls) == 2


def test_recommendation_request_does_not_pick_a_value_and_is_not_unclear(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _private_service_type_required_session(runtime)
    before_reply_count = len(session.messages)
    agent = _install_classifier_agent_for(
        runtime,
        [
            _classification_response("recommendation_request", 0.9),
            text_response("لو دي أول مرة، الفل باكدج بيريحك لأننا بنرتب كل حاجة. تحب نمشي عليه؟"),
        ],
    )

    session = _send(runtime, "مش عارف أختار، إنت شايف إيه أنسب؟", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert session.private_service_type == ""
    assert session.stage == "private_service_type_required"
    assert len(session.messages) > before_reply_count
    assert len(agent.provider.calls) == 2


# ---------------------------------------------------------------------------
# Existing categories keep working unmodified (candidate_value parsing is
# additive, not a behavior change for anything that doesn't send it).
# ---------------------------------------------------------------------------

def test_existing_side_question_category_is_unaffected_by_the_new_schema_field(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _private_service_type_required_session(runtime)
    agent = _install_classifier_agent_for(
        runtime,
        [
            _classification_response("side_question", 0.85),
            text_response("مفيش رسوم إلغاء قبل الاستشارة، محتاج تفاصيل تانية؟"),
        ],
    )

    session = _send(runtime, "لو غيرت رأيي بعدين، فيه غرامة إلغاء؟", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert session.private_service_type == ""


# ---------------------------------------------------------------------------
# Deterministic support: relative dates (section 8).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("بكره", "2026-08-20"),
        ("بكرة الصبح", "2026-08-20"),
        ("غدا", "2026-08-20"),
        ("tomorrow", "2026-08-20"),
        ("بعد يومين", "2026-08-21"),
        ("بعد 5 ايام", "2026-08-24"),
        ("in 3 days", "2026-08-22"),
        ("اخر الشهر", "2026-08-31"),
        ("end of month", "2026-08-31"),
        ("نهاية أغسطس", "2026-08-31"),
        ("end of August", "2026-08-31"),
        ("next weekend", "2026-08-26"),
        ("next week", "2026-08-26"),
    ],
)
def test_relative_date_phrases_resolve_deterministically(text: str, expected: str) -> None:
    assert normalize_relative_date_input(text, today=date(2026, 8, 19)) == expected


def test_ambiguous_bare_day_of_month_resolves_to_nearest_future_occurrence() -> None:
    # Today is the 19th, so day 25 is still ahead this month.
    assert normalize_relative_date_input("بعد يوم 25", today=date(2026, 8, 19)) == "2026-08-25"
    # Today is the 27th, so day 25 has already passed -- roll to next month.
    assert normalize_relative_date_input("بعد يوم 25", today=date(2026, 8, 27)) == "2026-09-25"


def test_genuinely_unrecognized_text_returns_no_date() -> None:
    assert normalize_relative_date_input("مش عارف لسه", today=date(2026, 8, 19)) == ""
    assert normalize_relative_date_input("", today=date(2026, 8, 19)) == ""


def test_private_dates_from_text_accepts_relative_phrases_end_to_end() -> None:
    start, end, flexible = ToolCallingSessionRuntime._private_dates_from_text("بكره")
    assert start and not flexible
    assert end == ""


# ---------------------------------------------------------------------------
# Deterministic support: worded/shorthand budget amounts (section 7).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "expected_amount", "expected_currency"),
    [
        ("5k", 5000.0, ""),
        ("5000 جنيه", 5000.0, "EGP"),
        ("خمسة آلاف", 5000.0, ""),
        ("خمس تلاف", 5000.0, ""),
        ("حوالي خمس تلاف جنيه", 5000.0, "EGP"),
        ("عشرة آلاف دولار", 10000.0, "USD"),
        ("الف جنيه", 1000.0, "EGP"),
        ("3000 USD", 3000.0, "USD"),
    ],
)
def test_budget_word_and_shorthand_amounts_resolve_deterministically(
    text: str, expected_amount: float, expected_currency: str,
) -> None:
    amount, currency = ToolCallingSessionRuntime._private_budget_from_text(text)
    assert amount == expected_amount
    assert currency == expected_currency


def test_a_worded_amount_with_no_currency_still_requires_clarification(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Section 7: never invent a currency the message didn't provide."""
    session = _private_trip_type_required_session(runtime)
    session = _send(runtime, "local", session)
    session = _send(runtime, "3", session)
    session = _send(runtime, "الغردقة", session)
    session = _send(runtime, "بكره", session)
    session = _send(runtime, "4", session)
    assert session.stage == "private_budget_required"

    session = _send(runtime, "خمس تلاف", session)

    assert session.private_budget_currency == ""
    assert session.stage == "private_budget_required"
