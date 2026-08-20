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
from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime, normalize_trip_type
from tests.test_field_capture_audit import (
    _private_service_type_required_session,
    _private_trip_type_required_session,
)
from tests.test_golden_transcript_regressions import (
    _bali_flight_option_required_session,
    _bali_gender_required_session,
    _bali_room_type_required_session,
    _classification_response,
    _new_traveler_nationality_required_session,
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
    """Strengthened (semantic coverage audit, section F/PC-implementation
    phase 1, item 5): the original version of this test never captured the
    `agent` return value, so it never asserted classify_off_script_turn
    was actually invoked -- it would have passed unchanged even if the
    entire answer_current_step dispatch branch were deleted, since "no
    branch matches -> fall through to the generic unclear copy" produces
    the exact same observable state as "branch ran, candidate rejected".
    The call_count assertion is what makes this test prove the SPECIFIC
    claim its name makes.
    """
    session = _private_service_type_required_session(runtime)
    agent = _install_classifier_agent_for(
        runtime,
        [_classification_response("answer_current_step", 0.9, candidate_value="platinum_vip_tier")],
    )

    session = _send(runtime, "محتار مش عارف أحدد حاجة معينة", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert session.private_service_type == ""
    assert session.stage == "private_service_type_required"


def test_answer_current_step_with_an_empty_candidate_is_not_applied(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Strengthened -- see test_answer_current_step_with_an_invalid_candidate_
    is_not_applied's docstring for why the call_count assertion matters."""
    session = _private_service_type_required_session(runtime)
    agent = _install_classifier_agent_for(
        runtime,
        [_classification_response("answer_current_step", 0.9, candidate_value="")],
    )

    session = _send(runtime, "مش عارف والله", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert session.private_service_type == ""
    assert session.stage == "private_service_type_required"


def test_low_confidence_answer_current_step_is_not_applied(runtime: ToolCallingSessionRuntime) -> None:
    """The existing CLASSIFIER_CONFIDENCE_THRESHOLD gate (0.55) applies to
    the new categories exactly like the old ones -- a low-confidence
    "answer_current_step" is downgraded to unclear before dispatch, so the
    candidate is never even looked at.

    Strengthened (semantic coverage audit phase 1, item 5): the
    call_count assertion proves the classifier WAS actually called (and
    therefore that the confidence gate specifically is what rejected it),
    as opposed to the message simply never reaching the classifier at all
    -- the original version could not tell those two cases apart.
    """
    session = _private_service_type_required_session(runtime)
    agent = _install_classifier_agent_for(
        runtime,
        [_classification_response("answer_current_step", 0.3, candidate_value="full_package")],
    )

    session = _send(runtime, "محتار مش عارف أحدد حاجة معينة", session)

    assert agent.classify_off_script_turn.call_count == 1
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


# ---------------------------------------------------------------------------
# PC-1 (semantic coverage audit, phase 1): private-flow trip-type correction.
#
# Investigating this exposed two independent, pre-existing bugs, both fixed
# alongside the actual PC-1 gap (missing downstream-state clearing):
#
#   1. "ليه" ("why") in EXPLANATION_REQUEST_SUBSTRING_TERMS was a blind
#      substring match with no word boundary -- it matched inside "خليها"
#      ("make it"), "عليها"/"عليه" ("on it"), swallowing the task's own
#      example ("خليها دولية مش محلية") into a generic explanation reply
#      before the classifier/merge_hints path ever ran.
#   2. normalize_trip_type had no negation awareness -- a sentence naming
#      BOTH types ("دولية ... مش محلية") returned whichever alias
#      TRIP_TYPE_ALIASES happened to iterate to first (dict insertion
#      order), which was "local" -- the exact opposite of what "مش محلية"
#      ("not local") says.
#
# Both fixes are narrow, deterministic, and were verified against every
# existing pinned case for their respective functions before being trusted
# here (see validation_rules.py/normalize_trip_type's own inline checks and
# EXPLANATION_REQUEST_SUBSTRING_TERMS's comment in lexicon.py).
# ---------------------------------------------------------------------------

def _private_flow_with_all_fields_answered(runtime: ToolCallingSessionRuntime):
    """Local private-trip session with every field up to (not including)
    budget already answered -- sitting at private_budget_required, still
    inside _BACKEND_OWNED_COLLECTION_STEPS so the classifier can be reached.
    """
    session = _private_service_type_required_session(runtime)
    for text in ("3", "الغردقة", "بكره", "4"):
        session = _send(runtime, text, session)
    assert session.stage == "private_budget_required"
    assert session.trip_type == "local"
    assert session.private_service_type == "full_package"
    assert session.private_destination == "الغردقة"
    assert session.private_start_date_pref
    assert session.private_party_size == 4
    return session


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("خليها دولية مش محلية", "international"),
        ("مش محلية خليها دولية", "international"),
        ("لا مش دولية عايز محلية", "local"),
        # Existing pinned behavior (validation_rules.py / test_agent_conversation_reliability.py)
        # must survive the negation-awareness change unchanged.
        ("محلية", "local"),
        ("المحلية", "local"),
        ("خلينا في المحلية", "local"),
        ("الدولية", "international"),
        ("family", ""),
    ],
)
def test_normalize_trip_type_resolves_negated_and_dual_mention_sentences(text: str, expected: str) -> None:
    assert normalize_trip_type(text) == expected


def test_explanation_request_word_boundary_does_not_swallow_khaleha_phrasing() -> None:
    # The exact false positive PC-1 investigation found: "ليه" ("why") is a
    # real word, but a blind substring match also fires inside "خليها".
    assert not ToolCallingSessionRuntime._is_explanation_request("خليها دولية مش محلية")
    assert not ToolCallingSessionRuntime._is_explanation_request("عليها التزامات كتير")
    # Genuine explanation requests using the same three letters as a
    # standalone word must still be recognized.
    assert ToolCallingSessionRuntime._is_explanation_request("ليه محتاج الاسم؟")
    assert ToolCallingSessionRuntime._is_explanation_request("طب ليه؟")


def test_private_flow_trip_type_correction_via_classifier_dispatch_clears_downstream_state(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Unit-level test of _handle_off_script_classifier's own
    correction_trip_type_switch branch, called directly rather than through
    a full turn.

    Discovered while writing this test: for a PRIVATE-flow session there is
    no full-turn phrasing that actually reaches this branch. Every
    trip-type mention _extract_hints can resolve at all gets applied by
    _merge_hints's unconditional fallthrough (tool_calling_runtime.py
    around "if trip_type and session.trip_type != trip_type:") BEFORE the
    classifier is ever consulted, because that fallthrough's only gate --
    "is a trip already selected" (session.selected_trip_id) -- is never
    true for a private-trip session (there is no trip inventory to select
    from). See test_private_flow_trip_type_correction_via_merge_hints_
    also_clears_downstream_state for the path that actually fires in
    practice; this test instead proves the classifier's OWN dispatch code
    is correct in isolation, since both paths end up calling the same
    shared _apply_trip_type_change / _clear_private_trip_downstream_state.
    """
    session = _private_flow_with_all_fields_answered(runtime)
    clean_text = "دولية مش محلية"
    context = runtime._build_context(session, clean_text)
    decision = runtime._workflow_policy.evaluate(context)
    assert decision.required_step == "collect_private_budget"
    _install_classifier_agent_for(
        runtime,
        [_classification_response("correction_trip_type_switch", 0.85, target_hint="international")],
    )

    handled = runtime._handle_off_script_classifier(session, decision, clean_text)

    assert handled is True
    assert session.trip_type == "international"
    # No stale private data survives the correction.
    assert session.private_service_type == ""
    assert session.private_destination == ""
    assert session.private_start_date_pref == ""
    assert session.private_end_date_pref == ""
    assert session.private_dates_flexible is False
    assert session.private_party_size == 0
    assert session.private_budget_amount == 0.0
    assert session.private_budget_currency == ""
    # Workflow resumes at the correct (first) required private step.
    assert session.stage == "private_service_type_required"
    assert session.private_trip_active is True


def test_private_flow_trip_type_correction_via_merge_hints_also_clears_downstream_state(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """The OTHER entry point into the same shared _apply_trip_type_change
    (the deterministic "خليها"/RESTART_SIGNAL_TERMS shortcut in
    _merge_hints) must clear the same private-flow state -- proving the
    fix lives at the shared choke point, not duplicated per caller.
    """
    session = _private_flow_with_all_fields_answered(runtime)

    session = _send(runtime, "خليها دولية مش محلية", session)

    assert session.trip_type == "international"
    assert session.private_service_type == ""
    assert session.private_destination == ""
    assert session.private_party_size == 0
    assert session.stage == "private_service_type_required"


def test_regular_flow_trip_type_correction_is_unaffected_by_private_state_clearing(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """A regular-flow (non-private) session has every private_* field empty
    already, so _clear_private_trip_downstream_state must be an observable
    no-op for it -- confirms the shared function change does not alter
    regular-flow behavior at all, independent of the existing
    test_off_script_correction_trip_type_switch_resolves_deterministically
    coverage in test_golden_transcript_regressions.py.
    """
    from tests.test_golden_transcript_regressions import _bali_selected_mid_flow_session

    session = _bali_selected_mid_flow_session(runtime)
    assert session.private_trip_active is False
    agent = _install_classifier_agent_for(
        runtime,
        [_classification_response("correction_trip_type_switch", 0.82, target_hint="local")],
    )

    session = _send(runtime, "you know what, switch me to a local trip please", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert session.trip_type == "local"
    assert session.selected_trip_id == ""
    assert session.room_type == ""
    assert session.group_size == 1
    assert session.private_service_type == ""
    assert session.private_party_size == 0


# ---------------------------------------------------------------------------
# PC-4 (semantic coverage audit, phase 1): Arabic nationality sentence
# capture. resolve_nationality() is a whole-string exact lookup; the only
# fallback was an English-only trigger-phrase regex, so "أنا مصري" never
# resolved even though "i am egyptian" already did. Fixed with the Arabic
# equivalents of the SAME triggers, against the SAME nationality_reference
# list -- no new mapping, no broadened inference.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("أنا مصري", "Egyptian"),  # Arabic male demonym
        ("أنا مصرية", "Egyptian"),  # Arabic female demonym
        ("جنسيتي سعودي", "Saudi"),
        ("جواز سفري من مصر", "Egyptian"),  # country-name sentence
        ("أنا مصري وعايز أحجز الرحلة بسرعة", "Egyptian"),  # mixed w/ unrelated text
        ("انا سعودي", "Saudi"),  # no hamza on alef, common typing
        ("مصري", "Egyptian"),  # bare word still works (pre-existing)
        ("i am egyptian", "Egyptian"),  # English trigger still works (pre-existing)
    ],
)
def test_arabic_nationality_sentence_resolves_to_canonical_value(text: str, expected: str) -> None:
    assert ToolCallingSessionRuntime._extract_nationality_hint(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "أنا مش متأكد من جنسيتي",  # unsupported/ambiguous -- no nationality named
        "أنا عايز رحلة",  # trigger word present, but nothing nationality-shaped follows
        "أنا من كوكب بعيد",  # nonsense country -- must not be guessed
    ],
)
def test_arabic_nationality_sentence_fails_closed_when_unsupported(text: str) -> None:
    assert ToolCallingSessionRuntime._extract_nationality_hint(text) == ""


def test_arabic_nationality_sentence_resolves_through_the_real_capture_path(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """End-to-end: the exact same nationality_required branch and
    session.nationality mutation an exact-format answer ("Egyptian") would
    use -- no new validation path, no new state field.
    """
    session = _new_traveler_nationality_required_session(runtime)

    session = _send(runtime, "أنا مصري وعايز أكمل باقي البيانات", session)

    assert session.nationality == "Egyptian"
    assert session.stage == "birthday_required"


def test_arabic_nationality_sentence_with_no_real_answer_stays_pending(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _new_traveler_nationality_required_session(runtime)

    session = _send(runtime, "أنا مش متأكد من جنسيتي دلوقتي", session)

    assert session.nationality == ""
    assert session.stage == "nationality_required"


# ---------------------------------------------------------------------------
# Independent deterministic bug found while building PC-6's currency
# ask_about_current_step test below (task's own example: "أدفع بالمصري ولا
# بالدولار؟"): the currency capture branch checked USD's match first and
# committed to it unconditionally, so a question naming BOTH currencies
# with neither negated resolved to "USD" as if it were an answer -- the
# exact "question misread as a selection" failure mode this whole audit is
# about, at the deterministic layer rather than the semantic one.
# ---------------------------------------------------------------------------

def test_currency_question_naming_both_currencies_is_not_captured_as_an_answer(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = runtime.create_session()
    session.stage = "currency_required"

    captured = runtime._apply_required_step_capture(session, "أدفع بالمصري ولا بالدولار؟")

    assert captured is False
    assert session.currency == ""


# ---------------------------------------------------------------------------
# PC-6 (semantic coverage audit, phase 1): recommendation_request and
# ask_about_current_step were only ever proven end-to-end for
# collect_private_service_type. The dispatch code is field-agnostic (the
# same two lines in _handle_off_script_classifier handle every required_step
# for both categories), so this verifies the mechanism actually generalizes
# -- for each of the 5 remaining enum fields, neither category may mutate
# the field or advance the stage, and both must actually reach the
# classifier (call_count == 1) and the grounded conversational turn
# (provider.calls == 2), not merely "fall through to the same unclear copy
# regardless of whether the dispatch branch exists" (the exact weakness
# section F of the audit flagged in three OTHER tests).
#
# Two of the task's own literal example phrasings could not be used as
# given -- both exposed real, independent deterministic-capture bugs (not
# semantic-layer bugs) that were fixed above and are pinned by their own
# tests: "يعني إيه mixed؟" (gender) and "أدفع بالمصري ولا بالدولار؟"
# (currency, fixed above). The gender one is used here in its fixed form
# once a matching fix is out of scope for this phase (see the final
# report); a differently-worded but equally genuine question is used
# instead, and the currency one is used exactly as given now that its
# blocker is fixed.
# ---------------------------------------------------------------------------

_RECOMMENDATION_REQUEST_TEXT = "مش عارف أختار، إنت شايف إيه أنسب؟"


def _bali_trip_type_required_session(runtime: ToolCallingSessionRuntime):
    _verified_runtime_with_switch_trips(runtime)
    session = runtime.create_session()
    session = _send(runtime, "01554158741", session)
    assert session.stage == "trip_type_required"
    return session


_PC6_FIELDS = {
    "gender": {
        "session_fn": _bali_gender_required_session,
        "ask_about_text": "الاختيار التالت ده معناه إيه؟",
        "field_attr": "room_group",
        "ask_about_via_full_turn": True,
    },
    "room_type": {
        "session_fn": _bali_room_type_required_session,
        "ask_about_text": "الغرفة المزدوجة يعني إيه؟",
        "field_attr": "room_type",
        # Was False (classifier-only) until _is_comparison_or_definition_
        # question closed the deterministic-capture gap this test's
        # docstring documents -- now exercised via a real full turn too.
        "ask_about_via_full_turn": True,
    },
    "flight": {
        "session_fn": _bali_flight_option_required_session,
        "ask_about_text": "الطيران شامل ولا لأ؟",
        "field_attr": "flight_option",
        "ask_about_via_full_turn": True,
    },
    "currency": {
        "session_fn": _bali_currency_required_session,
        "ask_about_text": "أدفع بالمصري ولا بالدولار؟",
        "field_attr": "currency",
        "ask_about_via_full_turn": True,
    },
    "trip_type": {
        "session_fn": _bali_trip_type_required_session,
        "ask_about_text": "إيه الفرق بين المحلي والدولي؟",
        "field_attr": "trip_type",
        # Was False (classifier-only) until _is_comparison_or_definition_
        # question closed the deterministic-capture gap this test's
        # docstring documents -- now exercised via a real full turn too.
        "ask_about_via_full_turn": True,
    },
}


@pytest.mark.parametrize("field", sorted(_PC6_FIELDS))
def test_recommendation_request_does_not_mutate_the_field(
    runtime: ToolCallingSessionRuntime, field: str,
) -> None:
    spec = _PC6_FIELDS[field]
    session = spec["session_fn"](runtime)
    before_value = getattr(session, spec["field_attr"])
    before_stage = session.stage
    agent = _install_classifier_agent_for(
        runtime,
        [
            _classification_response("recommendation_request", 0.9),
            text_response("مبدئيًا كل الخيارات متاحة، تحب أوصف لك كل واحد بسرعة؟"),
        ],
    )

    session = _send(runtime, _RECOMMENDATION_REQUEST_TEXT, session)

    assert agent.classify_off_script_turn.call_count == 1
    assert len(agent.provider.calls) == 2
    assert getattr(session, spec["field_attr"]) == before_value
    assert session.stage == before_stage


@pytest.mark.parametrize("field", sorted(_PC6_FIELDS))
def test_ask_about_current_step_does_not_select_an_option_merely_mentioned(
    runtime: ToolCallingSessionRuntime, field: str,
) -> None:
    """room_type and trip_type used to be exercised via a direct call to
    _handle_off_script_classifier instead of a full _send turn, because the
    task's own literal example phrasings ("الغرفة المزدوجة يعني إيه؟" and
    "إيه الفرق بين المحلي والدولي؟") never reached the classifier at all --
    both were mis-captured upstream, as a deterministic field value, before
    the off-script classifier ever ran:

    - _merge_hints applied _extract_hints' candidate_room_type (a bare
      substring check -- "مزدوج" matches inside "المزدوجة") whenever
      session.room_type was not already set; its is_exploratory guard
      existed but never fired for this phrasing (no hypothetical "لو"
      marker -- see _is_exploratory_question).
    - normalize_trip_type's PC-1 negation fix correctly stops picking the
      dict-iteration-order match, but did not detect "compare both, with
      NEITHER negated" as ambiguous -- "المحلي" and "الدولي" both appearing
      with no "مش" resolved to whichever the earlier one in the text is,
      not "unresolved".

    Both are now closed by _is_comparison_or_definition_question (a
    narrowly-scoped sibling of _is_exploratory_question, deliberately NOT
    merged into it -- see that method's docstring), wired into _merge_hints'
    trip_type/room_type gates and _apply_required_step_capture's
    collect_trip_type branch. Both fields now run ask_about_via_full_turn
    like every other field, proving the fix end-to-end rather than only at
    the classifier-dispatch level, the same way PC-1's classifier-dispatch
    test does for the private-flow trip-type correction.
    """
    spec = _PC6_FIELDS[field]
    session = spec["session_fn"](runtime)
    before_value = getattr(session, spec["field_attr"])
    before_stage = session.stage

    if spec["ask_about_via_full_turn"]:
        agent = _install_classifier_agent_for(
            runtime,
            [
                _classification_response("ask_about_current_step", 0.9, target_hint="option"),
                text_response("أوضح لك الفرق بسرعة وبعدين نكمل الاختيار."),
            ],
        )
        session = _send(runtime, spec["ask_about_text"], session)
        assert agent.classify_off_script_turn.call_count == 1
        assert len(agent.provider.calls) == 2
    else:
        context = runtime._build_context(session, spec["ask_about_text"])
        decision = runtime._workflow_policy.evaluate(context)
        _install_classifier_agent_for(
            runtime,
            [
                _classification_response("ask_about_current_step", 0.9, target_hint="option"),
                text_response("أوضح لك الفرق بسرعة وبعدين نكمل الاختيار."),
            ],
        )
        handled = runtime._handle_off_script_classifier(session, decision, spec["ask_about_text"])
        assert handled is True

    assert getattr(session, spec["field_attr"]) == before_value
    assert session.stage == before_stage


# ---------------------------------------------------------------------------
# Semantic coverage audit, deferred item (now closed): trip_type/room_type
# same-sentence-comparison questions with no negation marker were silently
# mis-captured as a real decision, entirely upstream of the classifier --
# these pin the deterministic-capture fix directly (no classifier/agent
# involved at all), independent of the PC-6 parametrized test above which
# proves the classifier's own dispatch is ALSO correct once the field is
# genuinely unresolved.
# ---------------------------------------------------------------------------
def test_trip_type_comparison_question_does_not_select_local(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _bali_trip_type_required_session(runtime)

    session = _send(runtime, "إيه الفرق بين المحلي والدولي؟", session)

    assert session.trip_type == ""
    assert session.stage == "trip_type_required"


def test_room_type_definition_question_does_not_select_double(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _bali_room_type_required_session(runtime)

    session = _send(runtime, "الغرفة المزدوجة يعني إيه؟", session)

    assert session.room_type == ""
    assert session.stage == "room_type_required"


# ---------------------------------------------------------------------------
# PC-5 (semantic coverage audit, phase 1): numeric/breakdown domain hints.
# collect_group_size and collect_private_party_size already had a hint
# (bare integer, already proven end-to-end above); this adds
# collect_gender_counts, collect_family_units, and
# collect_group_nationality_counts -- no new validation, no new state
# mutation, the candidate still goes through _apply_required_step_capture
# exactly like every other field.
#
# The two-count fields (gender_counts, group_nationality_counts) need
# their counts attached to a group WORD -- confirmed against the real
# _extract_mixed_people_counts / _extract_nationality_group_counts before
# writing the hint text, not guessed. "أنا ومراتي وولدين" (4 people total,
# unambiguous) is safe for group_size/party_size, which only need a total.
# It is deliberately NOT used for gender_counts: "ولدين" is genuinely
# ambiguous between "two boys" and "two children" in Egyptian colloquial,
# and inventing a boys/girls split from it would be exactly the kind of
# invented fact section 13 forbids -- unclear is the CORRECT outcome for
# that phrase at this field, not a gap.
# ---------------------------------------------------------------------------

def _bali_gender_counts_required_session(runtime: ToolCallingSessionRuntime):
    session = _bali_gender_required_session(runtime)
    session = _send(runtime, "mixed", session)
    assert session.stage == "gender_counts_required"
    return session


def _bali_family_units_required_session(runtime: ToolCallingSessionRuntime):
    session = _bali_gender_counts_required_session(runtime)
    session = _send(runtime, "2 boys 2 girls", session)
    assert session.stage == "family_units_required"
    return session


def _bali_group_nationality_counts_required_session(runtime: ToolCallingSessionRuntime):
    session = _bali_room_type_required_session(runtime)
    session = _send(runtime, "single", session)
    session = _send(runtime, "2", session)
    assert session.stage == "group_nationality_type_required"
    session = _send(runtime, "mixed", session)
    assert session.stage == "group_nationality_counts_required"
    return session


def test_semantic_candidate_resolves_gender_counts(runtime: ToolCallingSessionRuntime) -> None:
    session = _bali_gender_counts_required_session(runtime)
    agent = _install_classifier_agent_for(
        runtime,
        [_classification_response("answer_current_step", 0.9, candidate_value="2 boys 2 girls")],
    )

    session = _send(runtime, "معايا شب واحد وبنتين، مش عارف أكتب العدد إزاي", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert session.boys_count == 2
    assert session.girls_count == 2
    assert session.stage == "family_units_required"


def test_semantic_candidate_resolves_family_units(runtime: ToolCallingSessionRuntime) -> None:
    session = _bali_family_units_required_session(runtime)
    agent = _install_classifier_agent_for(
        runtime,
        [_classification_response("answer_current_step", 0.9, candidate_value="0")],
    )

    session = _send(runtime, "لا مفيش حد هيتشارك غرفة مع حد تاني", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert session.family_units == 0
    assert session.stage != "family_units_required"


def test_semantic_candidate_resolves_group_nationality_counts(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _bali_group_nationality_counts_required_session(runtime)
    agent = _install_classifier_agent_for(
        runtime,
        [_classification_response("answer_current_step", 0.9, candidate_value="1 egyptian 1 foreigner")],
    )

    session = _send(runtime, "واحد مصري والتاني مش مصري", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert session.group_nationality_counts == {"egyptian": 1, "foreigner": 1}
    assert session.stage == "group_nationality_counts_required" or session.stage == "flight_option_required"


@pytest.mark.parametrize("text", ["4", "4 أشخاص"])
def test_party_size_exact_and_natural_forms_resolve_deterministically(
    runtime: ToolCallingSessionRuntime, text: str,
) -> None:
    """The bare-digit half of section 6/PC-5's test matrix -- these two
    already resolve WITHOUT the semantic layer (option_number / the
    group-context digit fallback in _extract_hints already cover them),
    confirmed still true, unmodified, after this phase's other changes."""
    session = _private_service_type_required_session(runtime)
    session = _send(runtime, "3", session)
    session = _send(runtime, "الغردقة", session)
    session = _send(runtime, "بكره", session)
    assert session.stage == "private_party_size_required"

    session = _send(runtime, text, session)

    assert session.private_party_size == 4
    assert session.stage == "private_budget_required"


@pytest.mark.parametrize("text", ["أربعة", "احنا أربعة"])
def test_party_size_arabic_number_words_above_three_need_the_semantic_layer(
    runtime: ToolCallingSessionRuntime, text: str,
) -> None:
    """The OTHER half of the same matrix, and a genuine, specific finding:
    _extract_hints' Arabic number-word dict only covers واحد/اتنين/تلاتة
    (1-3) -- "أربعة" (4) is not in it, so this is NOT deterministically
    resolved at all (confirmed below), unlike "4"/"4 أشخاص" above. This is
    exactly the shape of message the semantic layer exists for: the
    classifier is expected to recognize "أربعة" means 4 and hand that
    integer to the SAME bare-integer resolver "4" itself would use.
    """
    assert ToolCallingSessionRuntime._extract_hints(
        text, stage="group_size_required"
    ).get("candidate_group_size") in (0, None, "")
    session = _private_service_type_required_session(runtime)
    session = _send(runtime, "3", session)
    session = _send(runtime, "الغردقة", session)
    session = _send(runtime, "بكره", session)
    assert session.stage == "private_party_size_required"
    agent = _install_classifier_agent_for(
        runtime,
        [_classification_response("answer_current_step", 0.9, candidate_value="4")],
    )

    session = _send(runtime, text, session)

    assert agent.classify_off_script_turn.call_count == 1
    assert session.private_party_size == 4
    assert session.stage == "private_budget_required"


def test_family_style_headcount_resolves_via_semantic_candidate_for_party_size(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """"أنا ومراتي وولدين" (me + my wife + two kids = 4) has no dedicated
    deterministic parser (section 6's own example -- neither
    _extract_mixed_people_counts nor _extract_hints' candidate_group_size
    counts family nouns). The semantic layer covers it instead: the
    classifier is expected to total the household to 4 and hand that
    total to the SAME bare-integer resolver an exact "4" would use -- no
    new validation path.
    """
    session = _private_service_type_required_session(runtime)
    session = _send(runtime, "3", session)
    session = _send(runtime, "الغردقة", session)
    session = _send(runtime, "بكره", session)
    assert session.stage == "private_party_size_required"
    # Confirms the premise: the deterministic parser alone does NOT resolve
    # this phrase (otherwise this test would not be exercising the
    # semantic-capture path at all).
    assert ToolCallingSessionRuntime._extract_hints(
        "أنا ومراتي وولدين", stage="group_size_required"
    ).get("candidate_group_size") in (0, None, "")
    agent = _install_classifier_agent_for(
        runtime,
        [_classification_response("answer_current_step", 0.9, candidate_value="4")],
    )

    session = _send(runtime, "أنا ومراتي وولدين", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert session.private_party_size == 4
    assert session.stage == "private_budget_required"


def test_ambiguous_family_phrase_is_not_forced_into_a_gender_breakdown(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """P3 boundary, not a bug: "ولدين" is genuinely ambiguous between "two
    boys" and "two children" in Egyptian colloquial. At gender_counts_required
    specifically (which needs a boys/girls SPLIT, not a total), a classifier
    that (correctly) refuses to guess the split must leave the field
    pending -- this is the expected fail-closed outcome, not a gap to close.
    """
    session = _bali_gender_counts_required_session(runtime)
    _install_classifier_agent_for(
        runtime,
        [_classification_response("unclear", 0.2)],
    )

    session = _send(runtime, "أنا ومراتي وولدين", session)

    assert session.boys_count == 0
    assert session.girls_count == 0
    assert session.stage == "gender_counts_required"


@pytest.mark.parametrize(
    "invalid_candidate",
    ["", "a few", "several", "-1", "boys and girls"],
)
def test_invalid_or_ambiguous_candidates_are_rejected_for_gender_counts(
    runtime: ToolCallingSessionRuntime, invalid_candidate: str,
) -> None:
    session = _bali_gender_counts_required_session(runtime)
    _install_classifier_agent_for(
        runtime,
        [_classification_response("answer_current_step", 0.9, candidate_value=invalid_candidate)],
    )

    session = _send(runtime, "معرفش أعبر عن العدد", session)

    assert session.boys_count == 0
    assert session.girls_count == 0
    assert session.stage == "gender_counts_required"
