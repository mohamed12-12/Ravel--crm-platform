"""Regression tests pinned directly to real live-chat transcripts.

Each test here reproduces an actual bug a human found by testing the deployed
agent (see project memory for the full transcripts / diagnosis). They exist so
a future change cannot silently reintroduce any of these failures.
"""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from services.ai_agent.ai_agent_app.agent.gemini_agent import GeminiAgent
from services.ai_agent.ai_agent_app.agent.response_format import sanitize_traveler_reply
from services.ai_agent.ai_agent_app.agent.response_guard import (
    guard_customer_response,
    response_guard_issue,
)
from services.ai_agent.ai_agent_app.agent.session_flow import SessionState
from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
from services.ai_agent.ai_agent_app.agent.write_response_gating import response_claims_write_success
from tests.test_agent_conversation_reliability import PassiveAgent, RecordingReadTools
from tests.test_phase11_demo_features import _make_app_with_db
from tests.test_phase2_gemini_tool_loop import LoopProviderStub, text_response


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
# Bug: a new traveler who finished the intake questions (name, nationality,
# birthday, currency) got stuck in a permanent loop repeating "I need one more
# detail" forever, because required_step=save_new_traveler_lead had no
# deterministic handler and the collected details were never saved.
# ---------------------------------------------------------------------------
def test_new_traveler_intake_saves_lead_instead_of_looping(runtime: ToolCallingSessionRuntime) -> None:
    runtime._write_executor.execute.return_value = {
        "executed": True,
        "result_id": "LD00003",
        "assistant_message": "Lead saved.",
        "lead_update": {"lead_id": "LD00003"},
        "write_result_contract": {
            "status": "success",
            "record_id": "LD00003",
            "record_type": "lead",
            "executed": True,
        },
    }
    session = runtime.create_session()
    for text in ("01270482380", "MAGED MAGED MAGED", "EGY", "28/4/2006"):
        session = _send(runtime, text, session)
    assert session.stage == "currency_required"

    session = _send(runtime, "1", session)

    reply = session.messages[-1]["text"]
    assert session.currency == "EGP"
    assert session.new_traveler_lead_saved is True
    assert "LD00003" in reply
    assert "I need one more detail" not in reply
    runtime._write_executor.execute.assert_called_once()
    assert runtime._write_executor.execute.call_args.kwargs["action"] == "create_lead"

    # The loop must not resurface on the following turn either: the runtime
    # should have moved on to the next step (trip type) rather than re-asking
    # for a currency it already has.
    session = _send(runtime, "1", session)
    assert "Which payment currency" not in session.messages[-1]["text"]
    # Guard against double-saving the same lead.
    assert runtime._write_executor.execute.call_count == 1


def test_new_traveler_lead_save_failure_does_not_falsely_claim_success(runtime: ToolCallingSessionRuntime) -> None:
    runtime._write_executor.execute.return_value = {"executed": False, "assistant_message": "blocked"}
    session = runtime.create_session()
    for text in ("01270482380", "MAGED MAGED MAGED", "EGY", "28/4/2006"):
        session = _send(runtime, text, session)

    session = _send(runtime, "1", session)

    assert session.new_traveler_lead_saved is False
    reply = session.messages[-1]["text"]
    assert "LD" not in reply  # no fabricated lead id


# ---------------------------------------------------------------------------
# Bug: a returning tester who already had an open lead on file got stuck in
# an infinite "Which payment currency do you prefer?" loop. The action
# validator correctly rejects create_lead as a duplicate and hands back the
# existing lead_id (write_result_contract status="duplicate", executed=False)
# but _execute_new_traveler_lead treated any executed=False as an outright
# failure, discarding the existing lead_id and falling through to a live
# model call that produced an empty/rejected reply every single turn.
# ---------------------------------------------------------------------------
def test_new_traveler_intake_completes_when_lead_already_exists_for_this_traveler(runtime: ToolCallingSessionRuntime) -> None:
    runtime._write_executor.execute.return_value = {
        "executed": False,
        "result_id": "LD00004",
        "reply": "Your request LD00004 is already recorded. We will follow up with you.",
        "assistant_message": "Your request LD00004 is already recorded. We will follow up with you.",
        "lead_update": {"lead_id": "LD00004", "lead_stage": "New"},
        "write_result_contract": {
            "status": "duplicate",
            "reused": True,
            "record_id": "LD00004",
            "record_type": "lead",
            "executed": False,
        },
        "session_update": {"lead_status": "New", "final_result": {"lead_id": "LD00004"}},
    }
    session = runtime.create_session()
    for text in ("01270482380", "MAGED MAGED MAGED", "EGY", "28/4/2006"):
        session = _send(runtime, text, session)
    assert session.stage == "currency_required"

    session = _send(runtime, "1", session)

    reply = session.messages[-1]["text"]
    assert session.new_traveler_lead_saved is True
    assert "LD00004" in reply
    assert "I need one more detail" not in reply
    runtime._write_executor.execute.assert_called_once()


# ---------------------------------------------------------------------------
# Bug: the agent told a customer "سأقوم بتحويلك الآن" / "جاري تحويلك" (I'm
# transferring you now) with no create_handoff ever executed, and it never
# appeared in the CRM's handoff queue. The false-write-success guard existed
# but its vocabulary only covered "created/saved/recorded", not "transfer".
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "reply_text",
    [
        "حاضر، سأقوم بتحويلك الآن إلى أحد موظفينا لمتابعة طلبك مباشرة. شكراً لك.",
        "نعم، جاري تحويلك الآن إلى أحد موظفينا لمتابعة طلبك.",
        "Sure, I'm transferring you now to one of our team members.",
    ],
)
def test_unverified_transfer_claim_is_blocked_by_response_guard(reply_text: str) -> None:
    assert response_claims_write_success(reply_text) is True
    issue, _terms = response_guard_issue(reply_text, write_result=None, record_type="")
    assert issue == "false_write_success_claim"

    guarded = guard_customer_response(reply_text, language="ar", write_result=None, record_type="")
    assert guarded.fallback_used is True
    assert guarded.message != reply_text


def test_real_handoff_confirmation_still_passes_the_guard() -> None:
    reply_text = "تم تسجيل طلب التواصل مع موظف H-0007. سيتابع معك الفريق."
    write_result = {"write_result_contract": {"status": "success", "record_id": "H-0007"}}
    issue, _terms = response_guard_issue(reply_text, write_result=write_result, record_type="handoff")
    assert issue == ""


# ---------------------------------------------------------------------------
# Bug: the room/seat-availability guard blocked the agent's own required
# answer type (real CRM room counts) because allow_inventory_counts was never
# passed as True anywhere in the codebase.
# ---------------------------------------------------------------------------
def test_room_availability_answer_is_not_blocked_when_grounded_in_a_tool_result() -> None:
    reply_text = "2 rooms available for this trip."
    issue, _terms = response_guard_issue(reply_text, allow_inventory_counts=True)
    assert issue == ""


def test_room_availability_answer_is_blocked_without_a_grounding_signal() -> None:
    reply_text = "2 rooms available for this trip."
    issue, _terms = response_guard_issue(reply_text, allow_inventory_counts=False)
    assert issue == "inventory_quantity_leak"


# ---------------------------------------------------------------------------
# Bug: "مواصفات الرحلة؟" and "وصف الرحلة؟" (two of the most common Egyptian
# Arabic ways to ask for a trip description) were not recognized as a
# trip-details request, so the customer's question fell through to the
# canned "trip not found by that name" reply.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["ايه مواصفات الرحلة؟", "وصف الرحلة؟", "tell me more details"])
def test_trip_details_request_recognizes_common_arabic_phrasings(text: str) -> None:
    assert ToolCallingSessionRuntime._is_trip_details_request(text) is True


@pytest.mark.parametrize("text", ["هل الرحلة كويسة؟", "يستاهل الرحلة دي؟", "is it worth it?"])
def test_trip_quality_question_is_recognized_as_a_conversational_interruption(text: str) -> None:
    assert ToolCallingSessionRuntime._is_trip_quality_question(text) is True
    assert ToolCallingSessionRuntime._is_conversational_interruption(text) is True


# ---------------------------------------------------------------------------
# Bug: hostility detection only matched English profanity, so the Arabic
# de-escalation reply that already existed in the code could never fire for
# an Arabic-speaking customer.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["انت غبي", "يا حمار", "زفت اوي كده", "fuck this bot"])
def test_hostile_message_detects_arabic_and_english_profanity(text: str) -> None:
    assert ToolCallingSessionRuntime._is_hostile_message(text) is True


@pytest.mark.parametrize("text", ["كلبي معايا في الرحلة", "شكرا جدا", "مصر جميلة"])
def test_hostile_message_does_not_false_positive_on_normal_text(text: str) -> None:
    assert ToolCallingSessionRuntime._is_hostile_message(text) is False


# ---------------------------------------------------------------------------
# Bug: sanitize_traveler_reply always substituted the English wording for
# CRM/database/backend regardless of the reply's language, so Arabic replies
# reached customers as mixed sentences ("... في our records").
# ---------------------------------------------------------------------------
def test_technical_term_sanitizer_keeps_arabic_replies_in_arabic() -> None:
    reply = sanitize_traveler_reply("لم أجد رحلة مؤكدة بهذا الاسم في CRM. من فضلك أرسل اسم الرحلة.")
    assert "our records" not in reply
    assert "سجلاتنا" in reply


def test_technical_term_sanitizer_keeps_english_replies_in_english() -> None:
    reply = sanitize_traveler_reply("I could not find that trip in the CRM database.")
    assert "سجلاتنا" not in reply
    assert "our records" in reply


# ---------------------------------------------------------------------------
# Grounding gap (found while writing this suite, not from a live transcript):
# _ground_reply's fact check flattens every trip ever shown into one blob, so
# a real price for trip A also "grounds" that same price misattributed to
# trip B once the customer has locked in trip B. Entity-scoping closes this.
# ---------------------------------------------------------------------------
def _two_trip_context(selected_trip_id: str | None) -> dict:
    context = {
        "workflow_policy": {"identity_verified": True},
        "trip_result": {
            "open_trips": [
                {"trip_id": "RT-LOC-26-900", "trip_name": "Siwa Discovery Demo", "public_price": "2000 EGP", "start_date": "2026-09-10"},
                {"trip_id": "RT-LOC-26-901", "trip_name": "Siwa Wellness Demo", "public_price": "2200 EGP", "start_date": "2026-09-20"},
            ],
            "date_tbd_trips": [],
        },
    }
    if selected_trip_id:
        context["selected_trip_id"] = selected_trip_id
    return context


def test_ground_reply_blocks_a_price_that_belongs_to_a_different_trip() -> None:
    agent = GeminiAgent.__new__(GeminiAgent)
    reply = "The price for your selected trip is 2200 EGP."
    grounded = agent._ground_reply(reply=reply, session_context=_two_trip_context("RT-LOC-26-900"), tool_events=[])
    assert grounded != reply


def test_ground_reply_allows_the_selected_trips_own_correct_price() -> None:
    agent = GeminiAgent.__new__(GeminiAgent)
    reply = "The price for your selected trip is 2000 EGP."
    grounded = agent._ground_reply(reply=reply, session_context=_two_trip_context("RT-LOC-26-900"), tool_events=[])
    assert grounded == reply


def test_ground_reply_allows_browsing_multiple_trips_before_selection() -> None:
    agent = GeminiAgent.__new__(GeminiAgent)
    reply = "Siwa Wellness Demo starts 2026-09-20 and costs 2200 EGP."
    grounded = agent._ground_reply(reply=reply, session_context=_two_trip_context(None), tool_events=[])
    assert grounded == reply


# ---------------------------------------------------------------------------
# Bug: a customer asked "what is details you need" (meaning: what info does
# the bot still need from me) mid-intake and got the "I can't share another
# traveler's details in this chat" privacy block instead of an answer. The
# bare word "details" alone was enough to trip AgentPrivacyPolicy's
# other-traveler classifier, which fires on any bound session.
# ---------------------------------------------------------------------------
def test_asking_what_details_are_needed_is_not_treated_as_another_traveler_request() -> None:
    from services.ai_agent.ai_agent_app.agent.privacy_policy import AgentPrivacyPolicy

    bound_context = {"customer_name": "Maged Maged Maged", "raw_phone": "01270482380"}
    response = AgentPrivacyPolicy.evaluate_user_message("what is details you need", bound_context)
    assert response is None


def test_asking_about_own_profile_or_data_is_not_blocked() -> None:
    from services.ai_agent.ai_agent_app.agent.privacy_policy import AgentPrivacyPolicy

    bound_context = {"customer_name": "Maged Maged Maged", "raw_phone": "01270482380"}
    for text in ("can you show my trip details", "what data do you have on me", "tell me more details"):
        assert AgentPrivacyPolicy.evaluate_user_message(text, bound_context) is None


def test_actual_other_traveler_request_is_still_blocked() -> None:
    from services.ai_agent.ai_agent_app.agent.privacy_policy import AgentPrivacyPolicy

    bound_context = {"customer_name": "Maged Maged Maged", "raw_phone": "01270482380"}
    response = AgentPrivacyPolicy.evaluate_user_message("please share another customer's booking status", bound_context)
    assert response is not None
    assert response.intent == "other_traveler_data_request"


# ---------------------------------------------------------------------------
# Bug: right after a duplicate-lead reuse, the very next assistant turn (a
# conversational-interruption reply generated live by Gemini) hit
# finish_reason=MAX_TOKENS, got rejected, retried once, and the retry ALSO
# hit MAX_TOKENS — surfacing the generic "couldn't prepare that response"
# apology to the customer. The retry call requested only 384 output tokens
# with no way to stop the model spending them on invisible reasoning first,
# so a retry meant to recover from a token-budget failure was, if anything,
# more likely to hit the same wall again. The retry offers no tools, so —
# unlike the shared tool-loop config, which must keep thinking on to
# preserve function-call thought signatures — it can safely turn thinking
# off entirely so the whole budget goes to the visible reply.
# ---------------------------------------------------------------------------
def test_max_tokens_retry_disables_thinking_and_has_a_real_token_budget() -> None:
    provider = LoopProviderStub([text_response("I can help you with Today Demo. What would you like next?", "resp-retry")])
    agent = GeminiAgent.__new__(GeminiAgent)
    agent.provider = provider

    replacement, response_id, issue = agent._regenerate_complete_reply(
        package=SimpleNamespace(system_prompt="You are Rahvel Agent.", prompt_id="prompt-tokens"),
        session_context={"session_id": "sess-tokens", "language": "en", "selected_trip_name": "Today Demo"},
        user_message="how can you help?",
        partial_reply="I can help you with Today Demo and",
        reason="token_limit_finish",
    )

    assert replacement == "I can help you with Today Demo. What would you like next?"
    assert response_id == "resp-retry"
    assert issue == ""
    retry_config = provider.calls[0]["generation_config"]
    assert retry_config["thinkingConfig"] == {"thinkingBudget": 0}
    assert retry_config["maxOutputTokens"] >= 512
