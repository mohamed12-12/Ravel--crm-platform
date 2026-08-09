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
def _write_results_by_action(**results: dict) -> object:
    """Route a mocked write executor by action, mirroring the real dispatch."""

    def _execute(*, action: str, payload: dict, session_context: dict) -> dict:
        if action not in results:
            raise AssertionError(f"unexpected write action: {action}")
        return results[action]

    return _execute


NEW_TRAVELER_WRITE = {
    "executed": True,
    "result_id": "TR00007",
    "traveler": {"traveler_id": "TR00007", "full_name": "Maged Samir Adly", "status": "Active"},
    "write_result": {"created_traveler": {"traveler_id": "TR00007", "status": "Active"}},
    "write_result_contract": {
        "status": "success",
        "record_id": "TR00007",
        "record_type": "traveler",
        "executed": True,
    },
}


def test_new_traveler_intake_saves_lead_instead_of_looping(runtime: ToolCallingSessionRuntime) -> None:
    runtime._write_executor.execute.side_effect = _write_results_by_action(
        create_traveler=NEW_TRAVELER_WRITE,
        create_lead={
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
        },
    )
    session = runtime.create_session()
    for text in ("01270482380", "Maged Samir Adly", "Egyptian", "28/4/2006"):
        session = _send(runtime, text, session)
    assert session.stage == "currency_required"

    session = _send(runtime, "1", session)

    reply = session.messages[-1]["text"]
    assert session.currency == "EGP"
    assert session.new_traveler_lead_saved is True
    assert "LD00003" in reply
    assert "I need one more detail" not in reply
    # A new traveler must get a Traveler record before the lead is written, so the
    # lead is linked and the same customer is recognized in every later session.
    actions = [call.kwargs["action"] for call in runtime._write_executor.execute.call_args_list]
    assert actions == ["create_traveler", "create_lead"]

    # The loop must not resurface on the following turn either: the runtime
    # should have moved on to the next step (trip type) rather than re-asking
    # for a currency it already has.
    session = _send(runtime, "1", session)
    assert "Which payment currency" not in session.messages[-1]["text"]
    # Guard against double-saving the same lead / traveler.
    assert [call.kwargs["action"] for call in runtime._write_executor.execute.call_args_list] == [
        "create_traveler",
        "create_lead",
    ]


def test_new_traveler_lead_save_failure_does_not_falsely_claim_success(runtime: ToolCallingSessionRuntime) -> None:
    """Regression: a failed create_lead write used to `return False` with no
    customer-facing message at all, falling through to the unvetted model
    path for that turn -- which, with no live model configured, meant the
    customer saw the test double's unrelated canned reply ("please share
    your WhatsApp number", even though they'd already given it minutes
    earlier) instead of an honest, specific failure message.
    """
    runtime._write_executor.execute.return_value = {"executed": False, "assistant_message": "blocked"}
    session = runtime.create_session()
    for text in ("01270482380", "Maged Samir Adly", "Egyptian", "28/4/2006"):
        session = _send(runtime, text, session)

    session = _send(runtime, "1", session)

    assert session.new_traveler_lead_saved is False
    reply = session.messages[-1]["text"]
    assert "LD" not in reply  # no fabricated lead id
    assert "could not save your details" in reply.lower()
    assert "whatsapp number" not in reply.lower()


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
    runtime._write_executor.execute.side_effect = _write_results_by_action(
        create_traveler=NEW_TRAVELER_WRITE,
        create_lead={
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
        },
    )
    session = runtime.create_session()
    for text in ("01270482380", "Maged Samir Adly", "Egyptian", "28/4/2006"):
        session = _send(runtime, text, session)
    assert session.stage == "currency_required"

    session = _send(runtime, "1", session)

    reply = session.messages[-1]["text"]
    assert session.new_traveler_lead_saved is True
    assert "LD00004" in reply
    assert "I need one more detail" not in reply
    assert [call.kwargs["action"] for call in runtime._write_executor.execute.call_args_list] == [
        "create_traveler",
        "create_lead",
    ]


# ---------------------------------------------------------------------------
# Bug: create_handoff is called with deduplicate_open=True, so a customer who
# asks for a human twice reuses their existing open handoff instead of
# creating a duplicate (write_result_contract status="reused", executed=False
# by design -- nothing new was written). _execute_manual_handoff read the raw
# executed flag and treated that as an outright failure, telling the customer
# their handoff request failed even though a valid handoff_id already exists.
# ---------------------------------------------------------------------------
def test_repeated_human_agent_request_reuses_existing_handoff_instead_of_claiming_failure(
    runtime: ToolCallingSessionRuntime,
) -> None:
    runtime._write_executor.execute.return_value = {
        "executed": False,
        "result_id": "H-0009",
        "handoff_case": {"handoff_id": "H-0009", "deduplicated": True},
        "write_result_contract": {
            "status": "reused",
            "reused": True,
            "record_id": "H-0009",
            "record_type": "handoff",
            "executed": False,
        },
    }
    session = runtime.create_session()
    session.customer_name = "Maged Maged Maged"
    session.raw_phone = "01270482380"

    session = _send(runtime, "I want to talk to a human please", session)

    reply = session.messages[-1]["text"]
    assert session.handoff_state == "handed_off"
    assert "failed" not in reply.lower()
    assert session.final_result.get("handoff_id") == "H-0009"


# ---------------------------------------------------------------------------
# Bug: a returning traveler with an existing open lead asked for "support"
# ("الدعم") and got stuck -- _is_human_agent_request only recognized
# human/real agent/person/employee/call me (and their Arabic equivalents), so
# this message fell through to Gemini, which the router then blocked from an
# unrelated pre-booking state. The classifier now also recognizes
# support/help/دعم/مساعدة, so this reaches the deterministic handoff path
# directly instead of depending on Gemini/the router at all.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "message_text",
    [
        "I need support with my booking",
        "can someone help me please",
        "عايز الدعم الفني",
        "محتاج مساعدة",
    ],
)
def test_existing_lead_traveler_support_request_reaches_handoff(
    runtime: ToolCallingSessionRuntime, message_text: str
) -> None:
    runtime._write_executor.execute.return_value = {
        "executed": True,
        "result_id": "H-0042",
        "handoff_case": {"handoff_id": "H-0042"},
        "write_result_contract": {
            "status": "success",
            "record_id": "H-0042",
            "record_type": "handoff",
            "executed": True,
        },
    }
    session = runtime.create_session()
    session.customer_name = "Returning Traveler"
    session.raw_phone = "01270482380"
    # Simulate a returning traveler with an already-linked traveler and open lead.
    session.final_result = {
        "traveler": {"traveler_id": "TR00099", "full_name": "Returning Traveler", "status": "Active"},
        "lead_id": "LD00001",
    }

    session = _send(runtime, message_text, session)

    reply = session.messages[-1]["text"]
    assert session.handoff_state == "handed_off"
    assert "failed" not in reply.lower()
    assert session.final_result.get("handoff_id") == "H-0042"
    runtime._write_executor.execute.assert_called_once()
    call_kwargs = runtime._write_executor.execute.call_args.kwargs
    assert call_kwargs["action"] == "create_handoff"
    assert call_kwargs["payload"]["lead_id"] == "LD00001"


# ---------------------------------------------------------------------------
# Bug: a write handler raising "not found" (e.g. update_lead_stage given a
# lead_id that no longer exists) was classified with error_code="not_found"
# but _safe_failed_write_message ignored the error_code and returned the same
# "please try again" text as every other lead failure -- misleading, since
# retrying with the same bad id fails identically every time. Separately, the
# Arabic strings for this method were corrupted to literal "?" characters in
# the source (a real, live customer-facing bug, found while reading this
# code, unrelated to the not_found gap).
# ---------------------------------------------------------------------------
def test_not_found_write_failure_gives_a_distinct_honest_message_not_generic_retry() -> None:
    from services.ai_agent.ai_agent_app.agent.write_tool_executor import GeminiWriteToolExecutor

    error_code = GeminiWriteToolExecutor._safe_error_code_for_exception(ValueError("Lead not found: LD-BAD"))
    assert error_code == "not_found"

    message = GeminiWriteToolExecutor._safe_failed_write_message("lead", error_code, "en")
    assert "try again" not in message.lower()
    assert "couldn't find" in message.lower()


def test_arabic_write_failure_messages_are_not_mojibake() -> None:
    from services.ai_agent.ai_agent_app.agent.write_tool_executor import GeminiWriteToolExecutor

    for record_type, error_code in (
        ("booking", "capacity_unavailable"),
        ("booking", ""),
        ("handoff", ""),
        ("lead", ""),
        ("write", ""),
    ):
        message = GeminiWriteToolExecutor._safe_failed_write_message(record_type, error_code, "ar")
        assert "?" not in message
        assert any("؀" <= ch <= "ۿ" for ch in message)


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


# ===========================================================================
# Live transcript, 2026-08-05 (new traveler 01554158741 / "Mohamed Ashraf
# Safwat"). One session produced six separate failures in a row: the name was
# asked twice, the trip-type answer dead-ended on "your request LD00001 is
# already recorded", a plain "which local trips do you have?" was answered
# with "I could not find a trip with that name", and two follow-up questions
# came back as the generic "sorry, I couldn't prepare that response" apology.
# ===========================================================================


def _verified_runtime(runtime: ToolCallingSessionRuntime, **traveler: str) -> ToolCallingSessionRuntime:
    identity = {"traveler_id": "TR00042", "full_name": "Returning Traveler", "status": "Active"}
    identity.update(traveler)
    runtime._read_only_tools = RecordingReadTools(identity=identity)
    return runtime


# Bug 1: "Mohamed Ashraf Safwat." (with the sentence-ending period people type)
# failed the three-part-name check, so the agent asked the identical question
# again as if the customer had sent nothing.
@pytest.mark.parametrize(
    "typed_name",
    ["Mohamed Ashraf Safwat.", "Mohamed Ashraf Safwat!", "محمد أشرف صفوت."],
)
def test_full_name_with_trailing_punctuation_is_accepted(typed_name: str) -> None:
    extracted = ToolCallingSessionRuntime._extract_valid_full_name(typed_name)
    assert extracted
    assert extracted == typed_name.rstrip(".!")


def test_new_traveler_name_question_is_not_repeated_after_a_valid_answer(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = runtime.create_session()
    session = _send(runtime, "01554158741", session)
    name_prompt = session.messages[-1]["text"]

    session = _send(runtime, "Mohamed Ashraf Safwat.", session)

    assert session.customer_name == "Mohamed Ashraf Safwat"
    assert session.messages[-1]["text"] != name_prompt
    assert session.stage == "nationality_required"


# Bug 2: once the new-traveler lead was saved, the stored workflow still said
# lookup_status="not_found", so every later turn re-entered the new-traveler
# intake branch, asked the model to save an already-saved lead, and the write
# gate replaced the whole reply with "your request LD00001 is already
# recorded" -- forever. The traveler could never reach the trip questions.
def test_saved_new_traveler_lead_continues_into_the_trip_workflow(
    runtime: ToolCallingSessionRuntime,
) -> None:
    runtime._write_executor.execute.side_effect = _write_results_by_action(
        create_traveler=NEW_TRAVELER_WRITE,
        create_lead={
            "executed": True,
            "result_id": "LD00001",
            "lead_update": {"lead_id": "LD00001"},
            "write_result_contract": {
                "status": "success",
                "record_id": "LD00001",
                "record_type": "lead",
                "executed": True,
            },
        },
    )
    session = runtime.create_session()
    for text in ("01554158741", "Mohamed Ashraf Safwat.", "Egyptian", "28/4/2003", "1"):
        session = _send(runtime, text, session)
    assert session.new_traveler_lead_saved is True
    # The traveler is now a verified CRM identity, not a "new traveler" any more.
    assert session.preview["workflow"]["identity_verified"] is True
    assert session.preview["traveler"]["traveler_id"] == "TR00007"
    assert session.stage == "trip_type_required"

    session = _send(runtime, "local", session)

    reply = session.messages[-1]["text"]
    assert session.trip_type == "local"
    assert "already recorded" not in reply.lower()
    assert "مسجل بالفعل" not in reply
    # Real verified trips from CRM, offered for selection.
    assert "Siwa Discovery Demo" in reply
    assert session.stage == "trip_selection_required"
    # And no second lead write was attempted for the same customer.
    assert [call.kwargs["action"] for call in runtime._write_executor.execute.call_args_list] == [
        "create_traveler",
        "create_lead",
    ]


def test_workflow_policy_does_not_reask_intake_for_a_saved_new_traveler() -> None:
    from services.ai_agent.ai_agent_app.agent.workflow_policy import ConversationWorkflowPolicy

    context = {
        "workflow": {"lookup_status": "not_found"},
        "known_traveler": {"traveler_id": "TR00007", "status": "Active"},
        "new_traveler_lead_saved": True,
        "customer_name": "Mohamed Ashraf Safwat",
        "trip_type": "",
    }
    decision = ConversationWorkflowPolicy().evaluate(context)
    assert decision.required_step == "collect_trip_type"
    assert decision.identity_verified is True


# Bug 3: "ايه الرحلات الداخليه ؟" (which local trips do you have?) was routed
# through the trip-name resolver, matched nothing, and was answered with "I
# could not find a confirmed trip with that name" -- which reads as if Ravel
# has no trips at all.
@pytest.mark.parametrize(
    "question",
    [
        "ايه الرحلات الداخليه ؟",
        "في رحلات متاحة؟",
        "what trips do you have?",
        "show me the available trips",
    ],
)
def test_trip_discovery_questions_are_recognized(question: str) -> None:
    assert ToolCallingSessionRuntime._is_trip_discovery_request(question) is True


@pytest.mark.parametrize("question", ["رحلة اسطنبول", "Siwa Discovery Demo", "1"])
def test_trip_name_references_are_not_treated_as_discovery(question: str) -> None:
    assert ToolCallingSessionRuntime._is_trip_discovery_request(question) is False


def test_trip_discovery_question_lists_verified_trips(runtime: ToolCallingSessionRuntime) -> None:
    _verified_runtime(runtime)
    session = runtime.create_session()
    session = _send(runtime, "01554158741", session)

    session = _send(runtime, "ايه الرحلات الداخليه ؟", session)

    reply = session.messages[-1]["text"]
    assert "لم أجد" not in reply
    assert "could not find" not in reply.lower()
    assert "Siwa Discovery Demo" in reply
    assert "Siwa Wellness Demo" in reply
    # Arabic session -> Arabic framing around the verified trip names.
    assert "الرحلات" in reply


def test_trip_list_is_written_in_arabic_for_an_arabic_session() -> None:
    session = SessionState(id="s1", agent_mode="tool_calling", language="ar", trip_type="local")
    session.preview = {
        "trip_result": {
            "open_trips": [
                {
                    "trip_id": "RT-LOC-26-900",
                    "trip_name": "Siwa Discovery Demo",
                    "start_date": "2026-09-10",
                    "end_date": "2026-09-14",
                    "public_price": "2000 EGP",
                }
            ],
            "date_tbd_trips": [],
        }
    }

    reply = ToolCallingSessionRuntime._canonical_trip_search_reply(session)

    assert "Here are the" not in reply
    assert "Please reply" not in reply
    assert "التاريخ" in reply
    assert "السعر" in reply
    assert "Siwa Discovery Demo" in reply


# Bug 4: after the lead was saved, any later turn where the agent truthfully
# mentioned the saved request ("تم تسجيل طلبك") was rejected by the
# false-write-success guard, because that guard only ever looked at the
# current turn's tool results. The customer saw the generic apology instead.
def test_reply_may_reference_a_record_saved_on_an_earlier_turn() -> None:
    reply_text = "تم تسجيل طلبك LD00001 وسنتابع معك خطوات الرحلة."
    issue, _terms = response_guard_issue(reply_text, write_result=None, record_type="")
    assert issue == "false_write_success_claim"

    issue, _terms = response_guard_issue(
        reply_text,
        write_result=None,
        record_type="",
        known_record_ids={"lead": "LD00001"},
    )
    assert issue == ""


def test_write_success_claim_is_still_blocked_without_any_saved_record() -> None:
    issue, _terms = response_guard_issue(
        "تم تسجيل طلب الحجز الخاص بك.",
        write_result=None,
        record_type="",
        known_record_ids={},
    )
    assert issue == "false_write_success_claim"


# Bug 5: the Arabic boys/girls question (the gender step that gates room
# availability) was stored double-encoded in the source, so Arabic customers
# were shown mojibake -- and the response guard's own mojibake check would
# reject any reply that echoed it.
def test_traveler_gender_and_room_prompts_are_valid_arabic() -> None:
    from services.ai_agent.ai_agent_app.agent.workflow_policy import ConversationWorkflowPolicy

    policy = ConversationWorkflowPolicy()
    decision = policy._post_identity_decision(
        {"language": "ar", "trip_type": "local", "selected_trip_id": "RT-LOC-26-900"},
        traveler={"traveler_id": "TR00042", "status": "Active"},
        status="Active",
    )
    assert decision.required_step == "collect_traveler_gender"
    for message in (
        decision.assistant_message,
        policy._room_inventory_prompt({"available_single": 0}, room_group="boys", arabic=True),
    ):
        issue, _terms = response_guard_issue(message)
        assert issue != "mojibake_text", message
        assert any("؀" <= ch <= "ۿ" for ch in message)


# Bug 6: nothing in the runtime ever created the booking draft. The workflow
# collected every answer, asked for confirmation, and then handed the turn to
# the model and hoped it would call create_booking_draft on its own. When it
# did not, the traveler had confirmed a booking that was never written to the
# booking page.
BOOKING_DRAFT_WRITE = {
    "executed": True,
    "result_id": "BK00001",
    "booking_result": {"booking_id": "BK00001", "booking_status": "Draft"},
    "write_result_contract": {
        "status": "success",
        "record_id": "BK00001",
        "record_type": "booking",
        "executed": True,
    },
    "session_update": {
        "booking_result": {"booking_id": "BK00001", "booking_status": "Draft"},
        "booking_status": "Draft",
    },
}


def _answer_booking_questions(runtime: ToolCallingSessionRuntime) -> SessionState:
    """Walk the full verified-traveler booking flow up to the confirmation step."""

    _verified_runtime(runtime)
    session = runtime.create_session()
    for text in ("01554158741", "local", "1", "boys", "single", "2"):
        session = _send(runtime, text, session)
    return session


def test_full_booking_flow_asks_every_step_in_order_then_confirms(
    runtime: ToolCallingSessionRuntime,
) -> None:
    runtime._write_executor.execute.side_effect = _write_results_by_action(
        create_booking_draft=BOOKING_DRAFT_WRITE
    )
    _verified_runtime(runtime)
    session = runtime.create_session()

    session = _send(runtime, "01554158741", session)
    assert session.stage == "trip_type_required"
    session = _send(runtime, "local", session)
    assert session.stage == "trip_selection_required"
    assert "Siwa Discovery Demo" in session.messages[-1]["text"]
    session = _send(runtime, "1", session)
    assert session.selected_trip_id == "RT-LOC-26-900"
    # Gender is asked before the room options, because room inventory is split
    # by boys/girls in CRM.
    assert session.stage == "traveler_gender_required"
    session = _send(runtime, "boys", session)
    assert session.stage == "room_type_required"
    session = _send(runtime, "single", session)
    assert session.stage == "group_size_required"
    session = _send(runtime, "2", session)
    assert session.stage == "booking_confirmation_required"
    summary = session.messages[-1]["text"]
    assert "Siwa Discovery Demo" in summary
    assert "confirm" in summary.lower()


def test_confirmed_booking_writes_the_draft_without_relying_on_the_model(
    runtime: ToolCallingSessionRuntime,
) -> None:
    runtime._write_executor.execute.side_effect = _write_results_by_action(
        create_booking_draft=BOOKING_DRAFT_WRITE
    )
    session = _answer_booking_questions(runtime)
    assert session.stage == "booking_confirmation_required"

    session = _send(runtime, "yes", session)

    reply = session.messages[-1]["text"]
    assert "BK00001" in reply
    assert session.booking_completed is True
    assert session.booking_status == "Draft"
    call = runtime._write_executor.execute.call_args_list[-1]
    assert call.kwargs["action"] == "create_booking_draft"
    payload = call.kwargs["payload"]
    assert payload["trip_id"] == "RT-LOC-26-900"
    assert payload["room_type"] == "Single"
    assert payload["room_group"] == "boys"
    assert payload["group_size"] == 2
    # The room count is derived from the traveler count, which is collected
    # after the room type: 2 travelers in single rooms means 2 rooms, not the
    # 1 room that was derived while the group size was still unknown.
    assert payload["boys_rooms_requested"] == 2
    assert payload["traveler_id"] == "TR00042"
    # A second "yes" must not create a duplicate booking.
    session = _send(runtime, "yes", session)
    assert sum(
        1 for entry in runtime._write_executor.execute.call_args_list if entry.kwargs["action"] == "create_booking_draft"
    ) == 1


# ===========================================================================
# Bug: a live transcript hit "yes" -> "I could not complete the request right
# now" -> "yes" again -> the SAME confirmation question repeated -> "confirm"
# -> success. "yes" and "confirm" are matched identically (both in
# _AFFIRMATIVE_REPLIES), so this was never a synonym-coverage gap. Reproduced
# directly: when create_booking_draft's write genuinely fails, the failure
# handler reset session.stage to whatever pre-write state the workflow
# decision had captured (e.g. "booking_ready"), which
# _handle_booking_confirmation_reply does not recognize as "awaiting a
# confirmation reply" -- so the very next "yes" fell through that handler
# entirely and the workflow policy re-asked the full question from scratch
# instead of retrying the write. A second, independent bug compounded it: the
# honest failure message itself ("...no booking was created") tripped the
# false-write-success guard on the bare word "created" with no negation
# awareness, so the customer never even saw that specific message -- only the
# fully generic "Sorry, I couldn't prepare that response properly" apology.
# ===========================================================================
def test_failed_booking_draft_write_lets_the_very_next_confirmation_reply_retry(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _answer_booking_questions(runtime)
    assert session.stage == "booking_confirmation_required"

    call_count = {"n": 0}

    def flaky_once(*, action: str, payload: dict, session_context: dict) -> dict:
        assert action == "create_booking_draft"
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated transient write failure")
        return BOOKING_DRAFT_WRITE

    runtime._write_executor.execute.side_effect = flaky_once

    session = _send(runtime, "yes", session)
    failure_reply = session.messages[-1]["text"]
    assert "no booking was created" in failure_reply.lower()
    assert "couldn't prepare" not in failure_reply.lower()
    assert not session.booking_completed
    # The retry must be routed back through the confirmation handler, not
    # dropped into a state that produces a full re-ask of the original question.
    assert session.stage == "booking_confirmation_required"
    assert session.booking_confirmation_requested is True

    session = _send(runtime, "yes", session)
    reply = session.messages[-1]["text"]
    assert "BK00001" in reply
    assert session.booking_completed is True
    assert call_count["n"] == 2


# ===========================================================================
# Gap found during a production-readiness QA pass: _AFFIRMATIVE_REPLIES
# (tool_calling_runtime.py) defines 16 synonyms accepted as a booking
# confirmation, but only "yes" and "confirm" were ever exercised by a test.
# Parametrize over the remaining 14 so every accepted synonym is proven to
# actually reach _handle_booking_confirmation_reply and complete the
# booking, not just fall through to the model/router untested.
# ===========================================================================
@pytest.mark.parametrize(
    "confirmation_reply",
    [
        "y",
        "ok",
        "okay",
        "sure",
        "book it",
        "go ahead",
        "تمام",
        "ماشي",
        "موافق",
        "ايوه",
        "أيوه",
        "نعم",
        "اوكي",
        "اوكى",
    ],
)
def test_every_affirmative_reply_synonym_confirms_the_booking(
    runtime: ToolCallingSessionRuntime, confirmation_reply: str
) -> None:
    runtime._write_executor.execute.side_effect = _write_results_by_action(
        create_booking_draft=BOOKING_DRAFT_WRITE
    )
    session = _answer_booking_questions(runtime)
    assert session.stage == "booking_confirmation_required"

    session = _send(runtime, confirmation_reply, session)

    reply = session.messages[-1]["text"]
    assert "BK00001" in reply
    assert session.booking_completed is True
    call = runtime._write_executor.execute.call_args_list[-1]
    assert call.kwargs["action"] == "create_booking_draft"


# ===========================================================================
# Bug: found live while re-verifying the fix above. A real booking (BK000002)
# was genuinely created against real production Postgres, but the customer
# was told "I could not complete the request right now" anyway.
# PostgresAgentBridgeService.create_booking_draft reports a fresh write as
# write_result_contract status="created" -- the SQLite path always used
# "success" instead, and _SUCCESS_STATUSES only recognized "success" (plus
# "reused"/"duplicate"), so write_result_allows_success treated a genuinely
# successful Postgres write as a failure.
# ===========================================================================
def test_booking_draft_created_status_from_the_postgres_bridge_is_reported_as_success(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _answer_booking_questions(runtime)
    assert session.stage == "booking_confirmation_required"

    runtime._write_executor.execute.side_effect = _write_results_by_action(
        create_booking_draft={
            "executed": True,
            "result_id": "BK000002",
            "booking_result": {"booking_id": "BK000002", "booking_status": "Draft"},
            "write_result_contract": {
                "status": "created",
                "record_id": "BK000002",
                "record_type": "booking",
                "executed": True,
            },
            "session_update": {
                "booking_result": {"booking_id": "BK000002", "booking_status": "Draft"},
                "booking_status": "Draft",
            },
        }
    )

    session = _send(runtime, "yes", session)
    reply = session.messages[-1]["text"]
    assert "BK000002" in reply
    assert "could not" not in reply.lower()
    assert session.booking_completed is True


def test_international_trip_requires_a_passport_attachment_before_the_draft(
    runtime: ToolCallingSessionRuntime,
) -> None:
    runtime._write_executor.execute.side_effect = _write_results_by_action(
        create_booking_draft=BOOKING_DRAFT_WRITE
    )
    _verified_runtime(runtime)
    session = runtime.create_session()
    for text in ("01554158741", "international", "1", "boys", "single", "2"):
        session = _send(runtime, text, session)

    # International trips add two steps a local trip does not have: the flight
    # question and the passport attachment.
    assert session.stage == "flight_option_required"
    session = _send(runtime, "2", session)
    assert session.flight_option == "Without Flight"
    assert session.stage == "awaiting_passport_upload"
    assert "passport" in session.messages[-1]["text"].lower()

    # Typing instead of attaching must not skip the requirement.
    session = _send(runtime, "I will send it later", session)
    assert session.stage == "awaiting_passport_upload"

    runtime.handle_passport_attachment(session, "passport-scan.pdf")
    session = _send(runtime, "done", session)

    # The passport attachment alone is not the full record -- structured
    # number/expiry/issuing-country fields are asked next, one at a time.
    assert session.stage == "passport_number_required"
    session = _send(runtime, "A1234567", session)
    assert session.stage == "passport_expiry_required"
    session = _send(runtime, "01/06/2032", session)
    assert session.stage == "passport_country_required"
    session = _send(runtime, "Egyptian", session)

    assert session.stage == "booking_confirmation_required"
    session = _send(runtime, "yes", session)
    payload = runtime._write_executor.execute.call_args_list[-1].kwargs["payload"]
    assert payload["trip_id"] == "RT-INT-26-001"
    assert payload["passport_attachment_ref"] == "passport-scan.pdf"
    assert "BK00001" in session.messages[-1]["text"]


def test_booking_draft_write_failure_never_claims_a_booking(
    runtime: ToolCallingSessionRuntime,
) -> None:
    runtime._write_executor.execute.side_effect = _write_results_by_action(
        create_booking_draft={
            "executed": False,
            "result_id": "",
            "assistant_message": "I could not create the booking request yet. Please review the details or try again.",
            "write_result_contract": {
                "status": "failed",
                "record_type": "booking",
                "executed": False,
                "error_code": "write_failed",
            },
        }
    )
    session = _answer_booking_questions(runtime)

    session = _send(runtime, "yes", session)

    reply = session.messages[-1]["text"]
    assert "BK" not in reply
    assert session.booking_completed is False
    assert session.booking_confirmed is False
    assert "could not" in reply.lower()


# Bug 7: the handoff write failed for this customer, and the agent answered
# with only "I couldn't submit the review request" and parked the session on a
# dead "waiting" stage, so the conversation had nothing left to answer.
def test_failed_handoff_keeps_the_pending_workflow_question_alive(
    runtime: ToolCallingSessionRuntime,
) -> None:
    runtime._write_executor.execute.side_effect = _write_results_by_action(
        create_handoff={"executed": False, "result_id": ""}
    )
    _verified_runtime(runtime)
    session = runtime.create_session()
    for text in ("01554158741", "local", "1"):
        session = _send(runtime, text, session)
    assert session.stage == "traveler_gender_required"

    session = _send(runtime, "I want to talk to a human", session)

    reply = session.messages[-1]["text"]
    assert session.handoff_state == "handoff_failed"
    # Honest about the failure...
    assert "couldn't submit" in reply.lower() or "could not" in reply.lower()
    # ...but the workflow still has a live question, not a dead end.
    assert "boys" in reply.lower()
    assert session.stage == "traveler_gender_required"


# Bug 8: _execute_update_lead_stage referenced an undefined `created_traveler`,
# so every stage update that ran through this path raised NameError and was
# reported to the customer as a generic write failure.
def test_update_lead_stage_builds_a_result_without_a_name_error() -> None:
    from services.ai_agent.ai_agent_app.agent.write_tool_executor import GeminiWriteToolExecutor

    executor = GeminiWriteToolExecutor.__new__(GeminiWriteToolExecutor)
    executor.settings = SimpleNamespace(default_country_code="20")
    executor.service = SimpleNamespace(
        update_lead_stage=lambda lead_id, **kwargs: {"lead_id": lead_id, "lead_stage": kwargs.get("requested_stage") or ""}
    )
    # The update is now confirmed with an independent read-back before the
    # result reaches the customer -- give it a matching lead to verify against.
    executor.read_only_tools = SimpleNamespace(
        lookup_lead=lambda **kwargs: {"leads": [{"lead_id": "LD00001", "lead_stage": "Qualified"}]}
    )

    result = executor._execute_update_lead_stage(
        {"lead_id": "LD00001", "requested_stage": "Qualified"},
        {},
        SimpleNamespace(decision="APPROVED", traveler_id=""),
    )

    assert result["result_id"] == "LD00001"
    assert result["write_result"]["created_traveler"] is None
    assert result["lead_update"]["lead_stage"] == "Qualified"


# ---------------------------------------------------------------------------
# Bug 9: live transcript -- customer typed "esclate me" (a typo for
# "escalate me") twice in a row while stuck at handle_empty_trip_results (an
# international search with zero results). _is_human_agent_request had no
# entry for escalate/esclate at all, so both messages fell through to the
# generic "no trips of this type" reply instead of ever reaching
# _execute_manual_handoff -- confirmed via the log line "handled unclear
# input without model rewrite step=handle_empty_trip_results".
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("message_text", ["escalate me", "esclate me", "can you escalate this"])
def test_escalate_request_during_empty_trip_results_reaches_manual_handoff(
    runtime: ToolCallingSessionRuntime, message_text: str
) -> None:
    identity = {"traveler_id": "TR00777", "full_name": "Youssef Kamal", "status": "Active"}
    read_tools = RecordingReadTools(identity=identity)
    read_tools.trips = []  # `trips=[]` in the constructor falls back to the TRIPS default (falsy check)
    runtime._read_only_tools = read_tools
    runtime._write_executor.execute.return_value = {
        "executed": True,
        "result_id": "H-0099",
        "handoff_case": {"handoff_id": "H-0099"},
        "write_result_contract": {
            "status": "success",
            "record_id": "H-0099",
            "record_type": "handoff",
            "executed": True,
        },
    }
    session = runtime.create_session()
    session = _send(runtime, "01270482380", session)
    session = _send(runtime, "2", session)  # international
    assert session.stage == "no_trips_available"

    session = _send(runtime, message_text, session)

    assert session.handoff_state == "handed_off"
    reply = session.messages[-1]["text"].lower()
    assert "no active inventory" not in reply
    assert "do not have any available" not in reply


# ===========================================================================
# Defensive hardening (not a confirmed reproduction of a specific incident --
# see ravel_agent_master_discovery_report.md): a session that verified a
# traveler once never re-checked, and _store_identity_result actively
# discarded a later not_found for an already-verified session, so a traveler
# deleted mid-conversation would stay treated as "existing" for the rest of
# that session's lifetime. Added a pre-write re-check
# (_traveler_still_exists_before_write) at the two highest-stakes write
# points -- create_booking_draft and the customer-requested manual handoff --
# instead of on every turn, since a CRM round-trip per message would be
# wasteful for a rare failure mode.
# ===========================================================================
def test_booking_write_is_blocked_when_verified_traveler_is_deleted_mid_session(
    runtime: ToolCallingSessionRuntime,
) -> None:
    runtime._write_executor.execute.side_effect = _write_results_by_action(
        create_booking_draft=BOOKING_DRAFT_WRITE
    )
    session = _answer_booking_questions(runtime)
    assert session.stage == "booking_confirmation_required"

    # Simulate the traveler being deleted from the CRM in a different
    # session/tab while this conversation is still open -- the mocked
    # read-tools double now reports not_found for the same phone number.
    runtime._read_only_tools.identity = None

    session = _send(runtime, "yes", session)

    reply = session.messages[-1]["text"].lower()
    assert session.booking_completed is False
    assert "bk00001" not in reply
    assert not any(
        call.kwargs.get("action") == "create_booking_draft"
        for call in runtime._write_executor.execute.call_args_list
    )
    # Identity collection must restart rather than silently retrying against
    # a traveler_id that no longer resolves to anything real.
    assert session.stage == "identity_required"
    assert session.raw_phone == ""


def test_manual_handoff_falls_back_to_phone_only_when_verified_traveler_is_deleted_mid_session(
    runtime: ToolCallingSessionRuntime,
) -> None:
    identity = {"traveler_id": "TR00777", "full_name": "Youssef Kamal", "status": "Active"}
    runtime._read_only_tools = RecordingReadTools(identity=identity)
    runtime._write_executor.execute.return_value = {
        "executed": True,
        "result_id": "H-0099",
        "handoff_case": {"handoff_id": "H-0099"},
        "write_result_contract": {
            "status": "success",
            "record_id": "H-0099",
            "record_type": "handoff",
            "executed": True,
        },
    }
    session = runtime.create_session()
    session = _send(runtime, "01270482380", session)
    assert session.stage != "identity_required"  # verified against TR00777

    # Same simulated mid-conversation deletion as the booking test above.
    runtime._read_only_tools.identity = None

    session = _send(runtime, "escalate me", session)

    assert session.handoff_state == "handed_off"
    call = runtime._write_executor.execute.call_args_list[-1]
    assert call.kwargs["action"] == "create_handoff"
    payload = call.kwargs["payload"]
    # The handoff must still go through for the customer -- just without a
    # traveler_id that no longer resolves to anything, and with an internal
    # note flagging the discrepancy for whoever picks it up.
    assert payload["traveler_id"] == ""
    assert "no longer resolves" in payload["notes"]


# ---------------------------------------------------------------------------
# The customer-facing handoff messages used to say only "a team member will
# follow up" -- no persona at all. The codebase already has a configured
# named persona for exactly this (settings.post_trip_handoff_responsible_employee,
# previously wired only into the older session_flow.py runtime's post-trip
# handoff message), so ToolCallingSessionRuntime's own handoff messages now
# use it too instead of a generic "team member".
# ---------------------------------------------------------------------------
def test_manual_handoff_success_message_names_the_configured_persona(
    runtime: ToolCallingSessionRuntime,
) -> None:
    runtime.settings = replace(runtime.settings, post_trip_handoff_responsible_employee="Sara")
    runtime._write_executor.execute.return_value = {
        "executed": True,
        "result_id": "H-0100",
        "handoff_case": {"handoff_id": "H-0100"},
        "write_result_contract": {
            "status": "success",
            "record_id": "H-0100",
            "record_type": "handoff",
            "executed": True,
        },
    }
    session = runtime.create_session()
    session.customer_name = "Youssef Kamal"
    session.raw_phone = "01270482380"

    session = _send(runtime, "I want to talk to a human please", session)

    reply = session.messages[-1]["text"]
    assert "Sara" in reply
    assert session.handoff_state == "handed_off"
