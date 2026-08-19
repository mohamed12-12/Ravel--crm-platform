"""Regression tests pinned directly to real live-chat transcripts.

Each test here reproduces an actual bug a human found by testing the deployed
agent (see project memory for the full transcripts / diagnosis). They exist so
a future change cannot silently reintroduce any of these failures.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import uuid
from dataclasses import replace
from datetime import date
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
from services.ai_agent.ai_agent_app.agent.tool_registry import build_agent_tool_registry, build_tool_calling_registry
from services.ai_agent.ai_agent_app.agent.write_response_gating import response_claims_write_success
from services.ai_agent.ai_agent_app.agent.nationality_reference import resolve_nationality
from services.ai_agent.llm.gemini_provider import GeminiProviderError
from services.ai_agent.validation.validation_rules import normalize_trip_type
from tests.test_agent_conversation_reliability import PassiveAgent, RecordingReadTools
from tests.test_phase11_demo_features import _make_app_with_db
from tests.test_phase2_gemini_tool_loop import LoopProviderStub, function_call_response, text_response


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

    reply = session.messages[-1]["text"]
    assert session.stage == "trip_type_required"
    assert session.currency == ""
    assert session.new_traveler_lead_saved is True
    assert "LD00003" in reply
    assert "I need one more detail" not in reply
    # A new traveler must get a Traveler record before the lead is written, so the
    # lead is linked and the same customer is recognized in every later session.
    actions = [call.kwargs["action"] for call in runtime._write_executor.execute.call_args_list]
    assert actions == ["create_traveler", "create_lead"]

    # The loop must not resurface on the following turn either: the runtime
    # should have moved on to the next step (trip type) rather than asking for
    # payment currency during identity onboarding.
    session = _send(runtime, "1", session)
    assert "Which payment currency" not in session.messages[-1]["text"]
    # Guard against double-saving the same lead / traveler.
    assert [call.kwargs["action"] for call in runtime._write_executor.execute.call_args_list] == [
        "create_traveler",
        "create_lead",
    ]


# ---------------------------------------------------------------------------
# Product feedback: a customer's very FIRST message named a destination
# ("عايز شرم الشيخ" -- "I want Sharm El Sheikh") before any identity was
# known. The agent immediately demanded a WhatsApp number before showing so
# much as a trip name, because _handle_public_trip_reference_if_present
# (which CAN show public trip details with no identity at all) requires
# either a confident trip_id/trip_name match or the literal word
# "رحلة"/"trip"/"حجز" -- a bare destination name has neither, so it silently
# fell through, all the way to workflow_policy's identity_required gate.
# The product owner wants: show trip details as normal for a browsing
# question; only require the WhatsApp/identity steps once booking is
# actually being pursued.
# ---------------------------------------------------------------------------
def test_naming_a_destination_before_any_identity_shows_trip_details_without_asking_for_phone(
    runtime: ToolCallingSessionRuntime,
) -> None:
    runtime._read_only_tools = RecordingReadTools(
        trips=[
            {
                "trip_id": "RT-LOC-26-RS1",
                "trip_name": "رحلة شرم الشيخ",
                "type": "Local",
                "trip_type": "local",
                "start_date": "2026-09-10",
                "end_date": "2026-09-14",
                "public_price": "3000 EGP",
                "public_description": "برنامج شرم الشيخ المؤكد بإطلالة على البحر الأحمر.",
                "available_single": 2,
            },
        ]
    )
    session = _send(runtime, "عايز شرم الشيخ")

    reply = session.messages[-1]["text"]
    assert "شرم الشيخ" in reply
    assert session.selected_trip_id == "RT-LOC-26-RS1"
    assert session.stage == "public_trip_details"
    assert session.raw_phone == ""
    assert "واتساب" not in reply
    assert "whatsapp" not in reply.lower()


# ---------------------------------------------------------------------------
# Feature: a live customer wrote "شرم الشيخ" against a real trip whose ONLY
# name on file was the English "Sharm-Elshekh" -- it matched (via the
# earlier hyphen-tokenizer fix), but the reply then showed the English name
# back to an Arabic-speaking customer ("اسم الرحلة: Sharm-Elshekh"), and a
# customer writing in pure Arabic vocabulary the trip's English name doesn't
# contain would not have matched at all. Trips now carry a mandatory
# trip_name_ar field (see apps/api/app/models/trip.py) that both the
# matcher and every customer-facing reply must use.
# ---------------------------------------------------------------------------
def test_arabic_query_matches_via_trip_name_ar_and_replies_show_the_arabic_name(
    runtime: ToolCallingSessionRuntime,
) -> None:
    runtime._read_only_tools = RecordingReadTools(
        trips=[
            {
                "trip_id": "RT-LOC-26-RS2",
                "trip_name": "Red Sea Getaway",
                "trip_name_ar": "رحلة شرم الشيخ",
                "type": "Local",
                "trip_type": "local",
                "start_date": "2026-09-10",
                "end_date": "2026-09-14",
                "public_price": "3000 EGP",
                "public_description": "Confirmed Red Sea program.",
                "available_single": 2,
            },
        ]
    )
    session = _send(runtime, "عايز رحلة شرم الشيخ")

    reply = session.messages[-1]["text"]
    assert session.selected_trip_id == "RT-LOC-26-RS2"
    assert "شرم الشيخ" in reply
    assert "Red Sea Getaway" not in reply


# ---------------------------------------------------------------------------
# Bug: a customer asked "كام السعر؟للغرفه الدبل" (what's the price for the
# double room) about a trip already shown to them, before giving any
# identity. _is_trip_details_request had no price vocabulary at all, so the
# question was never recognized as a browsing question -- it fell through
# to the identity gate, contradicting the already-established rule that
# browsing/asking about a trip must never require WhatsApp/identity first.
# ---------------------------------------------------------------------------
def test_price_question_about_shown_trip_is_answered_without_requiring_phone(
    runtime: ToolCallingSessionRuntime,
) -> None:
    runtime._read_only_tools = RecordingReadTools(
        trips=[
            {
                "trip_id": "RT-LOC-26-RS3",
                "trip_name": "رحلة شرم الشيخ",
                "type": "Local",
                "trip_type": "local",
                "start_date": "2026-08-20",
                "end_date": "2026-08-25",
                "public_price": "3000 EGP",
                "public_description": "Amazing Red Sea program.",
                "available_single": 2,
            },
        ]
    )
    session = _send(runtime, "عايز رحلة شرم الشيخ")
    assert session.selected_trip_id == "RT-LOC-26-RS3"

    session = _send(runtime, "كام السعر؟للغرفه الدبل", session)

    reply = session.messages[-1]["text"]
    assert session.raw_phone == ""
    assert "واتساب" not in reply
    assert "whatsapp" not in reply.lower()
    assert "3000" in reply


def test_naming_an_unmatched_destination_before_identity_asks_to_clarify_not_for_phone(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _send(runtime, "عايز شرم الشيخ")  # default RecordingReadTools catalog has no Sharm trip

    reply = session.messages[-1]["text"]
    assert session.selected_trip_id == ""
    assert session.stage == "trip_selection_required"
    assert session.raw_phone == ""
    assert "واتساب" not in reply
    assert "whatsapp" not in reply.lower()


# ---------------------------------------------------------------------------
# Bug: a customer typed just "sharm" (a partial mention) against a real
# catalog trip literally named "Sharm-Elshekh". It did not match -- only the
# full "sharm elshekh" did -- because _trip_reference_score tokenized the
# hyphenated trip NAME as one glued token ("sharm-elshekh"), so "sharm" alone
# had zero token overlap with it. Trip IDs (RT-LOC-26-900) intentionally
# keep hyphens glued; trip NAMES must not, since a hyphen there is just a
# display-friendly word separator. This generalizes to any hyphenated trip
# name in the catalog, not a fix specific to Sharm El Sheikh.
# ---------------------------------------------------------------------------
def test_partial_mention_of_a_hyphenated_trip_name_still_matches(runtime: ToolCallingSessionRuntime) -> None:
    runtime._read_only_tools = RecordingReadTools(
        trips=[
            {
                "trip_id": "RT-LOC-26-900",
                "trip_name": "Sharm-Elshekh",
                "type": "Local",
                "trip_type": "local",
                "start_date": "2026-08-20",
                "end_date": "2026-08-25",
                "public_price": "2500 EGP",
                "public_description": "amazing tripe",
                "available_single": 2,
            },
        ]
    )
    session = _send(runtime, "sharm")

    reply = session.messages[-1]["text"]
    assert "Sharm-Elshekh" in reply
    assert session.selected_trip_id == "RT-LOC-26-900"
    assert session.stage == "public_trip_details"


# ---------------------------------------------------------------------------
# Bug: a real customer typed only "شرم" (the short, common way Egyptians say
# "Sharm el-Sheikh") against a real catalog trip whose name is bilingual and
# has extra descriptive words, e.g. trip_name="Red Sea Getaway" (English) and
# trip_name_ar="رحلة شرم الشيخ" (Arabic, with a leading filler word). This
# scored only 59/100 -- below the 65 confident-match threshold -- purely
# because _trip_reference_score penalized ANY single-token query shorter
# than 4 characters by 20 points, a cutoff calibrated for English (where a
# short word like "sea"/"spa" is almost always generic/filler). Arabic
# script omits short vowels, so real destination names are routinely 3
# characters ("شرم", "دهب", "دبي", "قطر") -- the penalty was silently
# rejecting every short Arabic place name a customer might type alone, not
# just Sharm. Fixed by scoping the length cutoff by script: 3 for Arabic
# tokens, 4 for everything else. Generic short English words like "sea" must
# still lose the tie-break below (a different, deliberate test), proving the
# fix is script-scoped, not a blanket removal of the anti-false-positive
# guard.
# ---------------------------------------------------------------------------
def test_bare_short_arabic_destination_word_matches_a_bilingual_trip_name(
    runtime: ToolCallingSessionRuntime,
) -> None:
    runtime._read_only_tools = RecordingReadTools(
        trips=[
            {
                "trip_id": "RT-LOC-26-RS4",
                "trip_name": "Red Sea Getaway",
                "trip_name_ar": "رحلة شرم الشيخ",
                "type": "Local",
                "trip_type": "local",
                "start_date": "2026-08-20",
                "end_date": "2026-08-25",
                "public_price": "3000 EGP",
                "public_description": "Amazing Red Sea program.",
                "available_single": 2,
            },
        ]
    )
    session = _send(runtime, "شرم")

    reply = session.messages[-1]["text"]
    assert session.selected_trip_id == "RT-LOC-26-RS4"
    assert "شرم الشيخ" in reply


def test_bare_short_english_word_still_does_not_confidently_match_unrelated_trip(
    runtime: ToolCallingSessionRuntime,
) -> None:
    runtime._read_only_tools = RecordingReadTools(
        trips=[
            {
                "trip_id": "RT-LOC-26-RS5",
                "trip_name": "Red Sea Getaway",
                "type": "Local",
                "trip_type": "local",
                "start_date": "2026-08-20",
                "end_date": "2026-08-25",
                "public_price": "3000 EGP",
                "public_description": "Amazing Red Sea program.",
                "available_single": 2,
            },
        ]
    )
    session = _send(runtime, "sea")

    assert session.selected_trip_id == ""


# ---------------------------------------------------------------------------
# Bug: a real customer answered "collect_trip_type" (local inside Egypt or
# international outside Egypt?) by naming their actual destination -- "شرم"
# then "شرم الشيخ" then "ايواه شرم الشيخ" -- three times in a row. Sharm El
# Sheikh IS inside Egypt (local), but _apply_required_step_capture's
# trip_type_required branch only recognized the abstract local/international
# vocabulary, so every reply looked identical to "no answer at all." After
# two unclear-answer strikes the agent escalated with "مش عارف أفهم إجابتك
# صح" ("I can't understand your answer correctly") -- the wrong reason,
# reached the wrong way, after wasting the customer's patience for nothing.
# ---------------------------------------------------------------------------
def test_naming_a_known_destination_answers_trip_type_instead_of_escalating_as_unclear(
    runtime: ToolCallingSessionRuntime,
) -> None:
    runtime._write_executor.execute.side_effect = _write_results_by_action(
        create_traveler=NEW_TRAVELER_WRITE,
        create_lead={
            "executed": True,
            "result_id": "LD00001",
            "assistant_message": "Lead saved.",
            "lead_update": {"lead_id": "LD00001"},
            "write_result_contract": {
                "status": "success",
                "record_id": "LD00001",
                "record_type": "lead",
                "executed": True,
            },
        },
        create_handoff={"executed": True, "result_id": "H-0001"},
    )
    session = runtime.create_session()
    for text in ("01554158741", "حمد أشرف صفوت", "مصري", "28/4/2003"):
        session = _send(runtime, text, session)
    assert session.stage == "trip_type_required"

    session = _send(runtime, "شرم", session)

    reply = session.messages[-1]["text"]
    assert session.trip_type == "local"
    assert session.trip_query == "Sharm El Sheikh"
    assert session.unclear_step_strikes == 0
    # The default test trip catalog has no Sharm El Sheikh trip, so this
    # correctly falls to the HONEST no-matching-trip escalation (a real,
    # visible handoff, right reason) -- never the "I can't understand your
    # answer" unclear-input escalation the live transcript hit.
    assert "لا يوجد لدي رحلة مؤكدة" not in reply
    assert "مش عارف أفهم إجابتك" not in reply
    assert "قيدت طلبك عشان فريق Ravel يراجعه" in reply
    handoff_calls = [
        call.kwargs["payload"]
        for call in runtime._write_executor.execute.call_args_list
        if call.kwargs.get("action") == "create_handoff"
    ]
    assert len(handoff_calls) == 1
    assert handoff_calls[0]["reason_code"] == "no_matching_trip_available"


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

    reply = session.messages[-1]["text"]
    assert session.new_traveler_lead_saved is True
    assert session.stage == "trip_type_required"
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
    for text in ("01554158741", "Mohamed Ashraf Safwat.", "Egyptian", "28/4/2003"):
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
    for text in ("01554158741", "local", "1", "boys", "single", "2", "same", "1"):
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
    assert session.stage == "group_nationality_type_required"
    session = _send(runtime, "same", session)
    assert session.stage == "currency_required"
    session = _send(runtime, "1", session)
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
    assert session.stage == "group_nationality_type_required"
    session = _send(runtime, "same", session)
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

    assert session.stage == "currency_required"
    session = _send(runtime, "1", session)
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


# ---------------------------------------------------------------------------
# Trip switch after selection: once a trip was picked, the runtime refused to
# ever reconsider it (_apply_trip_selection_from_text/
# _handle_public_trip_reference_if_present both hard-return once
# session.selected_trip_id is set), so a mid-flow "actually show me X
# instead" silently dropped the new trip name and the customer kept getting
# asked the pending question for the trip they no longer wanted.
# _apply_trip_switch_from_text (tool_calling_runtime.py) now resolves an
# explicit correction to a different trip -- but only with BOTH a confident
# match (reusing the same score>=65 / +5-margin convention as the
# pre-selection public-trip-reference path) AND an explicit correction
# signal (_is_explicit_correction_signal), so a trip merely mentioned in a
# comparison or aside is never mistaken for a switch request.
# ---------------------------------------------------------------------------
_SWITCH_TEST_TRIPS = [
    {
        "trip_id": "RT-BALI-01",
        "trip_name": "Bali Beach Escape",
        "type": "International",
        "trip_type": "international",
        "start_date": "2026-11-01",
        "end_date": "2026-11-08",
        "public_price": "2500 USD",
        "public_description": "Verified Bali program.",
        "available_single": 2,
    },
    {
        "trip_id": "RT-THAI-01",
        "trip_name": "Thailand Explorer",
        "type": "International",
        "trip_type": "international",
        "start_date": "2026-11-15",
        "end_date": "2026-11-22",
        "public_price": "2600 USD",
        "public_description": "Verified Thailand program.",
        "available_single": 2,
    },
]


def _verified_runtime_with_switch_trips(runtime: ToolCallingSessionRuntime, **traveler: str) -> ToolCallingSessionRuntime:
    identity = {"traveler_id": "TR00042", "full_name": "Returning Traveler", "status": "Active"}
    identity.update(traveler)
    runtime._read_only_tools = RecordingReadTools(trips=_SWITCH_TEST_TRIPS, identity=identity)
    return runtime


def _bali_selected_mid_flow_session(runtime: ToolCallingSessionRuntime) -> SessionState:
    """Walk a real turn sequence up to "Bali selected, room type and group
    size already answered, flight not yet asked" -- the exact "room/group
    state populated" precondition the switch tests need, produced by the
    same _send() path production traffic uses rather than hand-constructed
    session state.
    """

    _verified_runtime_with_switch_trips(runtime)
    session = runtime.create_session()
    for text in ("01554158741", "international", "1", "boys", "single", "2"):
        session = _send(runtime, text, session)
    assert session.selected_trip_id == "RT-BALI-01"
    assert session.room_type == "Single"
    assert session.group_size == 2
    return session


def test_explicit_trip_switch_reselects_and_resets_dependent_state(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _bali_selected_mid_flow_session(runtime)

    session = _send(runtime, "Actually show me Thailand instead", session)

    assert session.selected_trip_id == "RT-THAI-01"
    assert session.selected_trip_name == "Thailand Explorer"
    # _clear_booking_dependent_state's own field list is the single source of
    # truth for what a trip change resets -- asserted here, not duplicated.
    assert session.room_type == ""
    assert session.group_size == 1
    assert session.flight_option == ""
    assert session.booking_confirmed is False
    assert session.booking_confirmation_requested is False


def test_comparison_question_about_another_trip_does_not_switch(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _bali_selected_mid_flow_session(runtime)

    session = _send(runtime, "Is Thailand cheaper than Bali?", session)

    assert session.selected_trip_id == "RT-BALI-01"
    assert session.room_type == "Single"
    assert session.group_size == 2


def test_ambiguous_mention_of_another_trip_does_not_switch(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _bali_selected_mid_flow_session(runtime)

    session = _send(runtime, "Tell me about Thailand.", session)

    assert session.selected_trip_id == "RT-BALI-01"
    assert session.room_type == "Single"
    assert session.group_size == 2


def test_bare_trip_name_with_no_correction_signal_does_not_switch(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Known, deliberate limitation (see _apply_trip_switch_from_text's
    docstring): a bare, unmarked trip name is indistinguishable from a
    passing mention without fuzzy/speculative matching, which is out of
    scope for this change -- so it leaves state untouched rather than
    guessing, same as an ambiguous mention.
    """
    session = _bali_selected_mid_flow_session(runtime)

    session = _send(runtime, "Thailand", session)

    assert session.selected_trip_id == "RT-BALI-01"
    assert session.room_type == "Single"
    assert session.group_size == 2


def test_trip_type_restart_signal_still_works_after_correction_signal_extraction() -> None:
    """_is_explicit_trip_type_restart_signal now delegates to the shared
    _is_explicit_correction_signal -- pin its own direct behavior so the
    Phase 2 refactor can't silently change what counts as a restart signal.
    """
    assert ToolCallingSessionRuntime._is_explicit_trip_type_restart_signal("actually international please")
    assert ToolCallingSessionRuntime._is_explicit_trip_type_restart_signal("بدل الرحلة لدولية")
    assert not ToolCallingSessionRuntime._is_explicit_trip_type_restart_signal("international sounds good")
    assert not ToolCallingSessionRuntime._is_explicit_trip_type_restart_signal("")


# ---------------------------------------------------------------------------
# Off-script conversation classifier (Phase 3A). When neither the strict
# per-step capture (_apply_required_step_capture) nor the keyword
# _is_conversational_interruption allowlist can interpret a message,
# GeminiAgent.classify_off_script_turn gets exactly one chance to route it --
# to the existing conversational LLM turn (side_question), the existing
# deterministic trip-switch/trip-type-switch mechanisms (correction_*), or
# the existing navigation handler. It is a ROUTER only: it never selects a
# trip, decides a workflow step, or calls a tool itself. Any unclear,
# low-confidence, malformed, or failed classification falls straight through
# to the pre-existing scripted fallback -- these tests pin that the
# classifier is never even reached on the reliable fast paths, and that
# every dispatch still goes through the pre-existing deterministic gates.
# ---------------------------------------------------------------------------
class _RaisingProviderStub:
    """A provider whose generate() always raises -- for the classifier's
    fail-closed behavior on a genuine provider failure (Test H)."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls: list[dict] = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        raise self.error


def _classification_response(category: str, confidence: float, *, target_hint: str = "", candidate_value: str = ""):
    return text_response(
        json.dumps({
            "category": category,
            "target_hint": target_hint,
            "candidate_value": candidate_value,
            "confidence": confidence,
        }),
        response_id=f"resp-classify-{category}",
    )


class _ClassifierConversationReadTools(RecordingReadTools):
    """RecordingReadTools only implements the read methods the deterministic
    handlers need. GeminiAgent.respond()'s _collect_crm_context calls a
    wider set (search_traveler/lookup_lead/get_traveler_profile) -- adding
    them here (rather than widening RecordingReadTools itself, used by the
    whole rest of this file) keeps this addition scoped to the two tests
    that actually exercise a full conversational turn (Test C, D).
    """

    def search_traveler(self, *, raw_phone: str, country_code: str = "") -> dict:
        return {
            "lookup_phone": {},
            "match_status": "single_match" if self.identity else "not_found",
            "handoff_required": False,
            "handoff_reason": "",
            "name_match_status": "matched" if self.identity else "",
            "actions": [],
            "traveler": dict(self.identity) if self.identity else None,
        }

    def lookup_lead(self, **_kwargs) -> dict:
        return {"status": "not_found", "lead": None}

    def get_traveler_profile(self, *, traveler_id: str) -> dict:
        return {"status": "found" if self.identity else "not_found", "traveler": dict(self.identity) if self.identity else None}


def _install_real_gemini_agent(runtime: ToolCallingSessionRuntime, provider):
    """Swap in a real GeminiAgent (wired to the given stub provider) so the
    turn loop's classifier dispatch has something to actually call, mirroring
    test_phase39_workflow_policy.py's runtime._conversation_ai = GeminiAgent(...)
    pattern. classify_off_script_turn is wrapped (not replaced) with a Mock
    so call_count/call args can be asserted while its real behavior --
    including provider calls, JSON parsing, and fail-closed handling -- still
    runs for real; this proves integration with the real method, not a
    stand-in for it.
    """
    agent = GeminiAgent(
        settings=runtime.settings,
        provider=provider,
        read_only_tools=runtime._read_only_tools,
        # RecordingReadTools (the test double already installed on `runtime`
        # by the runtime fixture/_verified_runtime_with_switch_trips) has no
        # `.service` attribute, which ActionValidator's default construction
        # requires -- irrelevant here since none of these tests exercise real
        # write validation (Test J's write-tool attempt is expected to be
        # blocked by workflow_policy's allowed_tools before validation would
        # ever run).
        action_validator=Mock(),
        # Phase 6 finding: this was build_tool_calling_registry() -- a small
        # READ-ONLY subset that does not include create_lead/
        # create_booking_draft/create_handoff/update_lead_stage at all. A
        # write-tool function_call_response was therefore rejected by
        # GeminiAgent._validate_tool_call as "Unsupported tool requested"
        # before ever reaching workflow_policy's allowed_tools/
        # _workflow_block_result -- the exact gate every "write tool
        # blocked" test's docstring (starting with Phase 3A's Test J) says
        # it is proving. Matching the real production registry
        # (ToolCallingSessionRuntime.__init__'s own
        # build_agent_tool_registry(include_write_tools=True,
        # include_validation_tool=False)) means the write tool is now a
        # registered call that reaches allowed_tools for real, so these
        # tests prove the gate they claim to.
        tool_registry=build_agent_tool_registry(include_write_tools=True, include_validation_tool=False),
    )
    agent.classify_off_script_turn = Mock(wraps=agent.classify_off_script_turn)
    runtime._conversation_ai = agent
    return agent


def test_classifier_is_never_invoked_on_the_existing_interruption_fast_path(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test A: an existing _is_conversational_interruption case ("why" during
    collect_group_size, which _natural_interruption_fallback already
    special-cases) must be fully handled by the pre-existing mechanism --
    the classifier must never even be called.
    """
    session = _bali_selected_mid_flow_session(runtime)
    agent = _install_real_gemini_agent(
        runtime,
        LoopProviderStub([text_response("Sure, I mean the number of travelers. How many are traveling?")]),
    )

    session = _send(runtime, "why do you need that", session)

    assert agent.classify_off_script_turn.call_count == 0
    assert session.selected_trip_id == "RT-BALI-01"


def test_classifier_is_never_invoked_when_the_step_capture_succeeds(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test B: a plain valid answer to the pending step (here, the group's
    pricing-nationality mix) is captured by _apply_required_step_capture --
    the classifier must never be called, since the turn never even reaches
    the "not step_value_captured" branch.
    """
    session = _bali_selected_mid_flow_session(runtime)
    assert session.stage == "group_nationality_type_required"
    agent = _install_real_gemini_agent(runtime, LoopProviderStub([text_response("unused")]))

    session = _send(runtime, "same", session)

    assert agent.classify_off_script_turn.call_count == 0
    assert session.group_nationality_type == "single"


def test_off_script_side_question_invokes_classifier_and_answers_naturally(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test C: "is this trip family-friendly?" matches neither the strict
    flight-preference capture nor the keyword interruption allowlist, so it
    reaches the classifier, which routes it to the existing conversational
    LLM turn (_run_conversational_llm_turn) -- the same mechanism
    _handle_conversational_interruption already uses, not a new one.
    """
    session = _bali_selected_mid_flow_session(runtime)
    runtime._read_only_tools = _ClassifierConversationReadTools(
        trips=_SWITCH_TEST_TRIPS,
        identity={"traveler_id": "TR00042", "full_name": "Returning Traveler", "status": "Active"},
    )
    natural_reply = (
        "Bali Beach Escape is great for families of all ages! "
        "Now, is your group all one pricing nationality, or a mixed group?"
    )
    agent = _install_real_gemini_agent(
        runtime,
        LoopProviderStub([_classification_response("side_question", 0.86), text_response(natural_reply)]),
    )

    session = _send(runtime, "is this trip family-friendly?", session)

    assert agent.classify_off_script_turn.call_count == 1
    # The pending question was not silently dropped for a generic re-ask --
    # the customer's actual message was answered.
    assert session.messages[-1]["text"] == natural_reply
    assert "famil" in session.messages[-1]["text"].casefold()
    assert session.selected_trip_id == "RT-BALI-01"


def test_off_script_deposit_question_acknowledges_without_inventing_an_amount(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test D: "what's the deposit?" has no backing CRM field (the Trip
    model has no deposit column) -- the classifier still routes it to the
    conversational path, and the actual question is acknowledged, but the
    reply must not state a specific, unsupported deposit amount.
    """
    session = _bali_selected_mid_flow_session(runtime)
    runtime._read_only_tools = _ClassifierConversationReadTools(
        trips=_SWITCH_TEST_TRIPS,
        identity={"traveler_id": "TR00042", "full_name": "Returning Traveler", "status": "Active"},
    )
    honest_reply = (
        "I don't have a specific deposit amount listed in our system for this trip. "
        "Is your group all one pricing nationality, or a mixed group?"
    )
    agent = _install_real_gemini_agent(
        runtime,
        LoopProviderStub([_classification_response("side_question", 0.81), text_response(honest_reply)]),
    )

    session = _send(runtime, "what's the deposit?", session)

    assert agent.classify_off_script_turn.call_count == 1
    reply = session.messages[-1]["text"]
    assert reply == honest_reply
    assert "$" not in reply
    assert not any(char.isdigit() for char in reply)


def test_off_script_classifier_routes_trip_switch_to_deterministic_resolver(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test E: "no I meant Thailand" contains no RESTART_SIGNAL_TERMS
    keyword, so the deterministic _apply_trip_switch_from_text (called
    earlier in the same turn) already failed to switch -- this is exactly
    the gap the classifier exists to close. The classifier only supplies
    the correction-intent signal; _resolve_trip_switch_candidate (the same
    score/margin rule Phase 2 uses) still decides the actual trip, and
    _select_trip still performs the only state mutation.
    """
    session = _bali_selected_mid_flow_session(runtime)
    assert not ToolCallingSessionRuntime._is_explicit_correction_signal("no I meant Thailand")
    agent = _install_real_gemini_agent(
        runtime,
        LoopProviderStub(
            [
                _classification_response("correction_trip_switch", 0.83, target_hint="Thailand"),
                text_response("Sure, switching to Thailand Explorer."),
            ]
        ),
    )

    session = _send(runtime, "no I meant Thailand", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert session.selected_trip_id == "RT-THAI-01"
    assert session.selected_trip_name == "Thailand Explorer"
    assert session.room_type == ""
    assert session.group_size == 1
    assert session.flight_option == ""


def test_off_script_comparison_question_does_not_switch_trip(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test F: a comparison question mentions a different trip without
    asking to replace the current one -- the classifier prompt explicitly
    instructs this is side_question, not correction_trip_switch, and even
    if it were misclassified as a correction, _resolve_trip_switch_candidate
    would still need to run; this test locks in the actual expected
    classification and the resulting no-op on session state.
    """
    session = _bali_selected_mid_flow_session(runtime)
    agent = _install_real_gemini_agent(
        runtime,
        LoopProviderStub(
            [
                _classification_response("side_question", 0.75),
                text_response("Prices vary by trip -- would you like this trip with flight or without flight?"),
            ]
        ),
    )

    session = _send(runtime, "Is Thailand cheaper than Bali?", session)

    assert session.selected_trip_id == "RT-BALI-01"
    assert session.room_type == "Single"
    assert session.group_size == 2


def test_off_script_classifier_malformed_json_falls_back_to_unclear(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test G: the provider returns non-JSON text -- classify_off_script_turn
    must fail closed to category=unclear/confidence=0.0, and the turn must
    fall through to the pre-existing scripted fallback (no crash, no state
    change, no invented behavior).
    """
    session = _bali_selected_mid_flow_session(runtime)
    agent = _install_real_gemini_agent(
        runtime,
        LoopProviderStub([text_response("this is not json at all")]),
    )

    session = _send(runtime, "is this trip family-friendly?", session)

    # Call it directly too, to assert the exact fail-closed contract in isolation.
    direct_result = agent.classify_off_script_turn(
        user_message="is this trip family-friendly?",
        session_context={},
        conversation_history=[],
    )
    assert direct_result == {"category": "unclear", "target_hint": "", "candidate_value": "", "confidence": 0.0}
    assert session.selected_trip_id == "RT-BALI-01"
    assert session.room_type == "Single"


def test_off_script_classifier_provider_failure_falls_back_safely(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test H: the provider raises -- no exception may escape the
    conversation turn, no state may mutate, and the customer must still get
    the existing scripted fallback reply.
    """
    session = _bali_selected_mid_flow_session(runtime)
    agent = _install_real_gemini_agent(runtime, _RaisingProviderStub(GeminiProviderError("boom")))

    session = _send(runtime, "is this trip family-friendly?", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert session.selected_trip_id == "RT-BALI-01"
    assert session.room_type == "Single"
    assert session.group_size == 2
    # A real reply was still produced -- the failure degraded silently.
    assert session.messages[-1]["role"] == "assistant"
    assert session.messages[-1]["text"]


def test_off_script_low_confidence_classification_behaves_like_unclear(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test I: confidence below CLASSIFIER_CONFIDENCE_THRESHOLD must be
    treated exactly like category=unclear, regardless of the stated
    category -- never routed into a new behavior.
    """
    session = _bali_selected_mid_flow_session(runtime)
    agent = _install_real_gemini_agent(
        runtime,
        LoopProviderStub([_classification_response("side_question", 0.30)]),
    )

    session = _send(runtime, "is this trip family-friendly?", session)

    assert agent.classify_off_script_turn.call_count == 1
    # Low confidence means the side_question route (which would have called
    # respond() a second time) was never taken -- only the classification
    # call itself happened.
    assert len(agent.provider.calls) == 1
    assert session.selected_trip_id == "RT-BALI-01"
    assert session.room_type == "Single"


def test_off_script_side_question_write_tool_attempt_is_still_blocked(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test J: even when the classifier routes to side_question and the
    conversational LLM opportunistically tries to call a write tool
    (create_lead) before the deterministic workflow has reached that step,
    the existing allowed_tools/_workflow_block_result machinery must still
    block it -- this is not modified by this phase, and this test proves
    the classifier-routed path still goes through it.
    """
    session = _bali_selected_mid_flow_session(runtime)
    agent = _install_real_gemini_agent(
        runtime,
        LoopProviderStub(
            [
                _classification_response("side_question", 0.9),
                function_call_response("create_lead", {}),
                text_response("Understood -- would you like this trip with flight or without flight?"),
            ]
        ),
    )

    session = _send(runtime, "is this trip family-friendly?", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert session.new_traveler_lead_saved is False
    assert "create_lead" not in (session.tools_used or [])
    assert session.selected_trip_id == "RT-BALI-01"


# ---------------------------------------------------------------------------
# Phase 3B -- Relevance & Safety Guard. Phase 3A's classifier/router is
# approved and unmodified; these tests harden and re-verify, with fresh
# fixtures/assertions, the guarantee stated in that phase's brief: "the LLM
# may help interpret or respond to conversation, but it must never become
# the authority for deterministic booking state." Reuses every fixture and
# helper above (_bali_selected_mid_flow_session, _install_real_gemini_agent,
# _classification_response, _RaisingProviderStub,
# _ClassifierConversationReadTools) -- no second test framework.
# ---------------------------------------------------------------------------
def _state_snapshot(session: SessionState) -> dict:
    """A focused (not exhaustive) snapshot of everything a conversational
    turn must never silently touch: the pending step, every collected
    booking/passport/currency field, and what tools actually ran.
    """
    return {
        "stage": session.stage,
        "selected_trip_id": session.selected_trip_id,
        "selected_trip_name": session.selected_trip_name,
        "trip_type": session.trip_type,
        "room_type": session.room_type,
        "group_size": session.group_size,
        "flight_option": session.flight_option,
        "passport_number": session.passport_number,
        "passport_expiry": session.passport_expiry,
        "passport_nationality": session.passport_nationality,
        "currency": session.currency,
        "booking_confirmed": session.booking_confirmed,
        "new_traveler_lead_saved": session.new_traveler_lead_saved,
        "tools_used": list(session.tools_used or []),
    }


def _bali_passport_number_required_session(runtime: ToolCallingSessionRuntime) -> SessionState:
    """Walk the real turn sequence to collect_passport_number.

    Phase 3B's brief cites collect_passport_country as its literal example
    of the "conversation relevance" risk. While investigating that exact
    step (see the discovered-finding note in the Phase 3B report), it turned
    out _apply_required_step_capture's passport_country branch accepts ANY
    non-empty, non-phone-shaped text up to 60 chars as the passport
    nationality with no further validation -- so "is this trip
    family-friendly?" is captured verbatim as passport_nationality and the
    workflow silently advances, and the conversational/classifier layer this
    phase hardens is never even reached. That is a pre-existing gap in the
    deterministic capture step itself, out of this phase's scope (which is
    about the LLM/classifier layer, not rewriting field validation), and is
    reported separately rather than silently patched here.
    collect_passport_number's capture (alnum, 6-9 chars) does reject an
    off-topic message and therefore actually exercises the interruption/
    classifier path this phase hardens, so it is used for these tests
    instead.
    """
    pytest.skip("Phase 2 removed conversational passport detail collection; passport details are CRM document-owned.")
    _verified_runtime_with_switch_trips(runtime)
    session = runtime.create_session()
    for text in ("01554158741", "international", "1", "boys", "single", "2", "same", "2"):
        session = _send(runtime, text, session)
    assert session.stage == "awaiting_passport_upload"
    runtime.handle_passport_attachment(session, "passport-scan.pdf")
    session = _send(runtime, "done", session)
    assert session.stage == "passport_number_required"
    return session


def test_side_question_during_passport_number_step_stays_conversational_and_leaves_state_untouched(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test A: a side question while collect_passport_number is pending must
    get an informational reply that still asks for the passport number, and
    must leave every field/stage byte-for-byte unchanged -- it must not
    look, to the next turn, like the passport number was captured or the
    workflow advanced.
    """
    session = _bali_passport_number_required_session(runtime)
    before = _state_snapshot(session)
    runtime._read_only_tools = _ClassifierConversationReadTools(
        trips=_SWITCH_TEST_TRIPS,
        identity={"traveler_id": "TR00042", "full_name": "Returning Traveler", "status": "Active"},
    )
    natural_reply = "Bali Beach Escape is great for families! Could you share your passport number?"
    agent = _install_real_gemini_agent(
        runtime,
        LoopProviderStub([_classification_response("side_question", 0.85), text_response(natural_reply)]),
    )

    session = _send(runtime, "is this trip family-friendly?", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert session.messages[-1]["text"] == natural_reply
    assert _state_snapshot(session) == before


def test_side_question_reply_missing_the_pending_field_is_rejected_to_fallback(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test A (relevance widening): before Phase 3B, collect_passport_number
    fell through _candidate_is_relevant_to_required_step to a bare
    language-match check that almost any reply would pass. A reply that
    answers the side question but never mentions the passport number at all
    must now be rejected in favor of the deterministic re-ask, not sent to
    the customer as-is.
    """
    session = _bali_passport_number_required_session(runtime)
    runtime._read_only_tools = _ClassifierConversationReadTools(
        trips=_SWITCH_TEST_TRIPS,
        identity={"traveler_id": "TR00042", "full_name": "Returning Traveler", "status": "Active"},
    )
    off_topic_reply = "Sure, this trip is great for families of all ages!"
    _install_real_gemini_agent(
        runtime,
        LoopProviderStub([_classification_response("side_question", 0.85), text_response(off_topic_reply)]),
    )

    session = _send(runtime, "is this trip family-friendly?", session)

    assert session.messages[-1]["text"] != off_topic_reply
    assert session.stage == "passport_number_required"
    assert session.passport_number == ""


def test_side_question_reply_missing_trip_type_is_rejected_to_fallback(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """collect_trip_type was one of the steps the plan's Phase 4 flagged as
    still falling through to the bare language-match check -- a reply that
    answers a side question but never mentions local/international must be
    rejected in favor of the deterministic re-ask, exactly like the other
    hardened steps above.
    """
    _verified_runtime_with_switch_trips(runtime)
    session = runtime.create_session()
    session = _send(runtime, "01554158741", session)
    assert session.stage == "trip_type_required"

    off_topic_reply = "Sure, our team is available 9am to 9pm daily!"
    _install_real_gemini_agent(
        runtime,
        LoopProviderStub([_classification_response("side_question", 0.85), text_response(off_topic_reply)]),
    )

    session = _send(runtime, "what are your working hours?", session)

    assert session.messages[-1]["text"] != off_topic_reply
    assert session.stage == "trip_type_required"
    assert session.trip_type == ""


def test_side_question_reply_missing_trip_selection_is_rejected_to_fallback(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """select_trip was another step the plan's Phase 4 flagged -- a reply
    that answers a side question but never references trip selection at
    all must be rejected in favor of the deterministic re-ask.
    """
    _verified_runtime_with_switch_trips(runtime)
    session = runtime.create_session()
    for text in ("01554158741", "international"):
        session = _send(runtime, text, session)
    assert session.stage == "trip_selection_required"
    assert session.selected_trip_id == ""

    off_topic_reply = "Sure, our team is available 9am to 9pm daily!"
    _install_real_gemini_agent(
        runtime,
        LoopProviderStub([_classification_response("side_question", 0.85), text_response(off_topic_reply)]),
    )

    session = _send(runtime, "what are your working hours?", session)

    assert session.messages[-1]["text"] != off_topic_reply
    assert session.stage == "trip_selection_required"
    assert session.selected_trip_id == ""


def test_conversational_reply_claiming_field_capture_is_rejected_and_state_is_untouched(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test K: a reply that falsely implies the pending field was already
    captured and the workflow moved on must not reach the customer as-is --
    it fails the widened relevance check since it never actually names the
    pending field -- and even if it had passed, session state must remain
    byte-for-byte unchanged, proving the conversational LLM has no path to
    mutate workflow state regardless of what it says.
    """
    session = _bali_passport_number_required_session(runtime)
    before = _state_snapshot(session)
    runtime._read_only_tools = _ClassifierConversationReadTools(
        trips=_SWITCH_TEST_TRIPS,
        identity={"traveler_id": "TR00042", "full_name": "Returning Traveler", "status": "Active"},
    )
    false_claim_reply = "Great, A1234567 noted -- moving on to the expiry date now!"
    _install_real_gemini_agent(
        runtime,
        LoopProviderStub([_classification_response("side_question", 0.85), text_response(false_claim_reply)]),
    )

    session = _send(runtime, "is this trip family-friendly?", session)

    assert session.messages[-1]["text"] != false_claim_reply
    assert _state_snapshot(session) == before


def test_side_question_attempted_booking_mutation_is_still_blocked(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test B (booking-related mutation): allowed_tools for
    collect_passport_number is SELECTED_TRIP_TOOLS -- no write tool at all.
    Proves update_booking specifically (Test J only proved create_lead) is
    blocked by the same existing allowed_tools/_workflow_block_result
    machinery, not mocked away.
    """
    session = _bali_passport_number_required_session(runtime)
    before = _state_snapshot(session)
    agent = _install_real_gemini_agent(
        runtime,
        LoopProviderStub(
            [
                _classification_response("side_question", 0.9),
                function_call_response("update_booking", {}),
                text_response("Got it -- could you share your passport number?"),
            ]
        ),
    )

    session = _send(runtime, "can you just confirm my booking currency is USD?", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert "update_booking" not in (session.tools_used or [])
    assert _state_snapshot(session) == before


def test_side_question_attempted_required_field_mutation_is_still_blocked(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test B (required-field mutation): update_traveler is a write tool not
    present in SELECTED_TRIP_TOOLS either -- an opportunistic attempt to
    write a traveler field from a side-question turn must be blocked the
    same way.
    """
    session = _bali_passport_number_required_session(runtime)
    before = _state_snapshot(session)
    agent = _install_real_gemini_agent(
        runtime,
        LoopProviderStub(
            [
                _classification_response("side_question", 0.9),
                function_call_response("update_traveler", {}),
                text_response("Got it -- could you share your passport number?"),
            ]
        ),
    )

    session = _send(runtime, "can you just set my nationality to Egyptian for me?", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert "update_traveler" not in (session.tools_used or [])
    assert _state_snapshot(session) == before


def test_side_question_then_answering_the_pending_field_normally_still_advances(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test C: a side question must not desync the turn loop -- the very
    next message answering the still-pending field normally must be
    captured by the ordinary strict per-step capture (not the classifier
    again) and advance the workflow exactly once.
    """
    session = _bali_passport_number_required_session(runtime)
    runtime._read_only_tools = _ClassifierConversationReadTools(
        trips=_SWITCH_TEST_TRIPS,
        identity={"traveler_id": "TR00042", "full_name": "Returning Traveler", "status": "Active"},
    )
    agent = _install_real_gemini_agent(
        runtime,
        LoopProviderStub(
            [
                _classification_response("side_question", 0.85),
                text_response("Sure! Could you share your passport number?"),
            ]
        ),
    )
    session = _send(runtime, "is this trip family-friendly?", session)
    assert session.stage == "passport_number_required"

    session = _send(runtime, "A1234567", session)

    assert agent.classify_off_script_turn.call_count == 1  # not called again for the plain answer
    assert session.passport_number == "A1234567"
    assert session.stage == "passport_expiry_required"


def test_off_script_correction_trip_switch_with_no_confident_match_makes_no_change(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test E: the classifier can identify *intent* to switch trips, but if
    _resolve_trip_switch_candidate finds no confident match (the named trip
    doesn't exist in this catalog), the route must return False and make no
    mutation -- the classifier must never substitute a guess for the
    deterministic resolver's refusal.
    """
    session = _bali_selected_mid_flow_session(runtime)
    before = _state_snapshot(session)
    agent = _install_real_gemini_agent(
        runtime,
        LoopProviderStub([_classification_response("correction_trip_switch", 0.8, target_hint="Iceland")]),
    )

    session = _send(runtime, "actually can we do Iceland instead", session)

    assert agent.classify_off_script_turn.call_count == 1
    # No confident match means _run_conversational_llm_turn's second call
    # (or any other provider call) never happened either.
    assert len(agent.provider.calls) == 1
    assert _state_snapshot(session) == before


def test_off_script_correction_trip_type_switch_resolves_deterministically(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test F: the classifier identifies intent to switch trip type using
    phrasing with no RESTART_SIGNAL_TERMS keyword (so the deterministic
    keyword-gated path in _merge_hints never fires on its own) -- actual
    resolution still goes entirely through normalize_trip_type +
    _apply_trip_type_change, not the classifier's own judgment.
    """
    session = _bali_selected_mid_flow_session(runtime)
    assert not ToolCallingSessionRuntime._is_explicit_correction_signal("you know what, switch me to a local trip please")
    agent = _install_real_gemini_agent(
        runtime,
        LoopProviderStub([_classification_response("correction_trip_type_switch", 0.82, target_hint="local")]),
    )

    session = _send(runtime, "you know what, switch me to a local trip please", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert session.trip_type == "local"
    assert session.selected_trip_id == ""
    assert session.room_type == ""
    assert session.group_size == 1


def test_off_script_correction_trip_type_switch_with_unresolvable_text_makes_no_change(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test G: the classifier can flag intent to switch trip type, but if
    normalize_trip_type cannot map the raw text to "local"/"international"
    at all, the route must return False and leave state untouched -- it must
    never guess a trip type the deterministic normalizer itself rejected.
    """
    session = _bali_selected_mid_flow_session(runtime)
    before = _state_snapshot(session)
    agent = _install_real_gemini_agent(
        runtime,
        LoopProviderStub([_classification_response("correction_trip_type_switch", 0.8, target_hint="something else")]),
    )

    session = _send(runtime, "actually let's do something totally different", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert _state_snapshot(session) == before


def test_classifier_provider_failure_during_passport_number_step_makes_no_change(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test H, generalized to the brief's own literal example step: a
    provider failure during classification must not escape the turn, must
    not mutate state, and must still produce a real reply to the customer.
    """
    session = _bali_passport_number_required_session(runtime)
    before = _state_snapshot(session)
    agent = _install_real_gemini_agent(runtime, _RaisingProviderStub(GeminiProviderError("boom")))

    session = _send(runtime, "is this trip family-friendly?", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert _state_snapshot(session) == before
    assert session.messages[-1]["role"] == "assistant"
    assert session.messages[-1]["text"]


def test_classifier_malformed_json_during_passport_number_step_makes_no_change(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test I, generalized to the brief's own literal example step: a
    non-JSON classifier response must fail closed to unclear/0.0 confidence
    and must not mutate state."""
    session = _bali_passport_number_required_session(runtime)
    before = _state_snapshot(session)
    agent = _install_real_gemini_agent(
        runtime,
        LoopProviderStub([text_response("this is not json at all")]),
    )

    session = _send(runtime, "is this trip family-friendly?", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert _state_snapshot(session) == before


def test_classifier_low_confidence_during_passport_number_step_makes_no_change(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test J, generalized to the brief's own literal example step:
    confidence below CLASSIFIER_CONFIDENCE_THRESHOLD must behave exactly
    like category=unclear and must not mutate state."""
    session = _bali_passport_number_required_session(runtime)
    before = _state_snapshot(session)
    agent = _install_real_gemini_agent(
        runtime,
        LoopProviderStub([_classification_response("side_question", 0.30)]),
    )

    session = _send(runtime, "is this trip family-friendly?", session)

    assert agent.classify_off_script_turn.call_count == 1
    # Low confidence means the side_question route's second provider call
    # (_run_conversational_llm_turn -> respond()) never happened.
    assert len(agent.provider.calls) == 1
    assert _state_snapshot(session) == before


def test_one_turn_with_explicit_switch_signal_and_resolvable_trip_transitions_exactly_once(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test L: when the deterministic keyword-gated trip switch
    (_apply_trip_switch_from_text, evaluated before workflow_policy) already
    resolves and mutates state this turn, the off-script classifier must
    never also run on the same message -- there is exactly one authoritative
    state transition per turn, not a deterministic mutation followed by a
    redundant (or conflicting) classifier-driven one.
    """
    session = _bali_selected_mid_flow_session(runtime)
    agent = _install_real_gemini_agent(runtime, LoopProviderStub([text_response("unused")]))

    session = _send(runtime, "Actually show me Thailand instead", session)

    assert agent.classify_off_script_turn.call_count == 0
    assert len(agent.provider.calls) == 0
    assert session.selected_trip_id == "RT-THAI-01"
    assert session.room_type == ""
    assert session.group_size == 1
    assert session.flight_option == ""


# ---------------------------------------------------------------------------
# Phase 3C -- Deterministic Capture Validation Audit & Hardening. Phase 3A/3B
# are approved and unmodified. This phase's finding: the deterministic
# required-step capture layer (_apply_required_step_capture) runs BEFORE the
# off-script classifier, and several of its branches accepted much broader
# free text than intended -- so a side question never even reached the
# conversational/classifier layer those phases hardened; it was silently
# captured as bogus structured data instead. Fixed by reusing the existing
# nationality_reference resolver (passport_country, and a latent bug in
# nationality_required's own "i am X" fallback) and by tightening two
# substring/whitespace-stripping checks (payment_currency, passport_number)
# to the same whole-answer discipline the file's "Task 3.1" pass already
# established for trip_type/room_type/flight_option/gender.
# ---------------------------------------------------------------------------
def _bali_passport_country_required_session(runtime: ToolCallingSessionRuntime) -> SessionState:
    session = _bali_passport_number_required_session(runtime)
    session = _send(runtime, "A1234567", session)
    assert session.stage == "passport_expiry_required"
    session = _send(runtime, "01/06/2032", session)
    assert session.stage == "passport_country_required"
    return session


def test_passport_country_accepts_a_valid_country_name(runtime: ToolCallingSessionRuntime) -> None:
    """Test A: a real passport country answer is still captured and the
    workflow still advances -- the fix must not overcorrect into rejecting
    legitimate input."""
    session = _bali_passport_country_required_session(runtime)

    session = _send(runtime, "Egypt", session)

    assert session.passport_nationality == "Egyptian"
    assert session.stage == "currency_required"


def test_passport_country_accepts_a_nationality_style_answer(runtime: ToolCallingSessionRuntime) -> None:
    """Test B: the question asks for a "country" but customers naturally
    answer with the nationality/demonym form -- already supported by the
    shared nationality_reference list, must remain supported here too."""
    session = _bali_passport_country_required_session(runtime)

    session = _send(runtime, "Saudi", session)

    assert session.passport_nationality == "Saudi"
    assert session.stage == "currency_required"


def test_passport_country_accepts_a_common_supported_alias(runtime: ToolCallingSessionRuntime) -> None:
    """Test C: common abbreviations/aliases already in nationality_reference
    (not a new list) must still resolve -- proves the fix reused the
    existing maintained list rather than requiring one canonical spelling."""
    session = _bali_passport_country_required_session(runtime)

    session = _send(runtime, "UAE", session)

    assert session.passport_nationality == "Emirati"
    assert session.stage == "currency_required"


def test_passport_country_accepts_an_arabic_demonym(runtime: ToolCallingSessionRuntime) -> None:
    """Test C (Arabic variant): the existing list's Arabic demonym forms
    must still resolve, matching nationality_required's own coverage."""
    session = _bali_passport_country_required_session(runtime)

    session = _send(runtime, "مصري", session)

    assert session.passport_nationality == "Egyptian"
    assert session.stage == "currency_required"


@pytest.mark.parametrize(
    "text",
    [
        "Is this trip family-friendly?",
        "How much does the trip cost?",
        "Can I change the date?",
        "What's the weather like there?",
        "Tell me more about this trip.",
    ],
)
def test_passport_country_rejects_unrelated_conversational_text(
    runtime: ToolCallingSessionRuntime, text: str
) -> None:
    """Tests D/E/F: the brief's own literal examples of clearly-unrelated
    text must never be captured as passport_nationality, must not advance
    the workflow, and must leave every other field untouched (Test I)."""
    session = _bali_passport_country_required_session(runtime)
    before = _state_snapshot(session)

    session = _send(runtime, text, session)

    assert session.passport_nationality == ""
    assert session.stage == "passport_country_required"
    after = _state_snapshot(session)
    after.pop("tools_used")
    before.pop("tools_used")
    assert after == before


def test_passport_country_rejected_side_question_reaches_the_existing_classifier_path(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test G: a rejected strict capture must still fall through to the
    existing interruption/classifier routing (Phase 3A/3B), not a dead end
    -- proves this phase's fix composes with those phases rather than
    bypassing them.
    """
    session = _bali_passport_country_required_session(runtime)
    runtime._read_only_tools = _ClassifierConversationReadTools(
        trips=_SWITCH_TEST_TRIPS,
        identity={"traveler_id": "TR00042", "full_name": "Returning Traveler", "status": "Active"},
    )
    natural_reply = "Bali Beach Escape is great for families! Which country issued your passport?"
    agent = _install_real_gemini_agent(
        runtime,
        LoopProviderStub([_classification_response("side_question", 0.85), text_response(natural_reply)]),
    )

    session = _send(runtime, "is this trip family-friendly?", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert session.messages[-1]["text"] == natural_reply
    assert session.passport_nationality == ""
    assert session.stage == "passport_country_required"


def test_passport_country_valid_answer_on_the_next_turn_is_captured_normally(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test H: after a rejected side question, the very next turn's real
    answer must still be captured by strict capture -- no lingering
    classifier/rejection state left over from the previous turn."""
    session = _bali_passport_country_required_session(runtime)
    runtime._read_only_tools = _ClassifierConversationReadTools(
        trips=_SWITCH_TEST_TRIPS,
        identity={"traveler_id": "TR00042", "full_name": "Returning Traveler", "status": "Active"},
    )
    agent = _install_real_gemini_agent(
        runtime,
        LoopProviderStub(
            [_classification_response("side_question", 0.85), text_response("Sure! Which country issued your passport?")]
        ),
    )
    session = _send(runtime, "is this trip family-friendly?", session)
    assert session.stage == "passport_country_required"

    session = _send(runtime, "Egyptian", session)

    assert agent.classify_off_script_turn.call_count == 1  # not called again for the plain answer
    assert session.passport_nationality == "Egyptian"
    assert session.stage == "currency_required"


def test_passport_country_strict_capture_takes_precedence_over_classifier(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Tests J/K/L: a valid answer must be handled entirely by strict
    capture -- the classifier must never even be invoked -- and the
    workflow must advance exactly once (straight to currency_required, not
    bounced through an intermediate state)."""
    session = _bali_passport_country_required_session(runtime)
    agent = _install_real_gemini_agent(runtime, LoopProviderStub([text_response("unused")]))

    session = _send(runtime, "Egyptian", session)

    assert agent.classify_off_script_turn.call_count == 0
    assert len(agent.provider.calls) == 0
    assert session.passport_nationality == "Egyptian"
    assert session.stage == "currency_required"


def test_extract_nationality_hint_no_longer_accepts_an_unrecognized_i_am_phrase() -> None:
    """Discovered finding: _extract_nationality_hint's regex fallback used
    to title-case ANY unrecognized phrase following "my nationality is"/
    "i am" and return it as if it were a real nationality (e.g. "i am not
    sure, can I ask something?" -> "Not Sure, Can I Ask Something"). Pinned
    directly against the function so this can't silently regress even
    though no step's strict capture happened to expose it in a test until
    this phase's audit found it via the shared code path.
    """
    assert ToolCallingSessionRuntime._extract_nationality_hint("i am not sure, can I ask something?") == ""
    assert ToolCallingSessionRuntime._extract_nationality_hint("my nationality is confused about this") == ""
    # The legitimate use case -- a recognized nationality named via this
    # phrasing -- must still resolve.
    assert ToolCallingSessionRuntime._extract_nationality_hint("my nationality is egyptian") == "Egyptian"
    assert ToolCallingSessionRuntime._extract_nationality_hint("i am saudi") == "Saudi"


def test_nationality_required_step_no_longer_accepts_an_unrelated_i_am_sentence(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Same finding, exercised through the real nationality_required strict
    capture (not just the unit-level helper) -- a casual "I am ..." reply
    that isn't actually stating a nationality must be rejected, not stored
    as session.nationality."""
    session = runtime.create_session()
    for text in ("01270482380", "Mohamed Ashraf Safwat"):
        session = _send(runtime, text, session)
    assert session.stage == "nationality_required"

    session = _send(runtime, "I am not sure what you mean by that", session)

    assert session.nationality == ""
    assert session.stage == "nationality_required"


def _bali_currency_required_session(runtime: ToolCallingSessionRuntime) -> SessionState:
    session = _bali_passport_country_required_session(runtime)
    session = _send(runtime, "Egyptian", session)
    assert session.stage == "currency_required"
    return session


def test_currency_required_rejects_a_bare_digit_inside_unrelated_text(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Discovered finding: "1"/"2" used to be matched as bare substrings
    against the whole message, so any text containing that digit anywhere
    (a time, a room number, an extension) was captured as a currency
    answer. Only the exact single-digit whole answer (option_number) may
    resolve a currency now."""
    session = _bali_currency_required_session(runtime)

    session = _send(runtime, "I'll call you back at 2pm", session)

    assert session.currency == ""
    assert session.stage == "currency_required"


def test_currency_required_still_accepts_the_bare_option_number(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Regression guard: the legitimate numbered-choice answers ("1"/"2")
    the customer is actually presented with must still work after tightening
    the digit check to a whole-answer match."""
    session = _bali_currency_required_session(runtime)

    session = _send(runtime, "2", session)

    assert session.currency == "USD"


def test_passport_number_rejects_a_punctuation_free_phrase_that_collapses_to_a_valid_length(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Discovered finding: stripping ALL whitespace before the alnum+length
    check meant a short, punctuation-free side question could collapse into
    something that looked like a passport number -- "is it far" ->
    "isitfar" (7 chars, alnum). Requiring at least one digit rules this out
    without rejecting any real passport-number format."""
    session = _bali_passport_number_required_session(runtime)

    session = _send(runtime, "is it far", session)

    assert session.passport_number == ""
    assert session.stage == "passport_number_required"


def test_passport_number_still_accepts_real_formats_with_internal_spaces(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Regression guard: some customers type a passport number with spaces
    ("A 123456") -- whitespace-stripping is still needed for that, only the
    added digit requirement is new."""
    session = _bali_passport_number_required_session(runtime)

    session = _send(runtime, "A 123456", session)

    assert session.passport_number == "A123456"
    assert session.stage == "passport_expiry_required"


# ---------------------------------------------------------------------------
# Phase 3D -- End-to-End Conversation Regression & Hardening. Phase 3A/3B/3C
# are approved and unmodified. This phase adds NO new production behavior:
# per its brief, it proves (with multi-turn, full-state-snapshot tests) that
# deterministic capture + the off-script classifier + the conversational
# relevance guard + deterministic trip/trip-type resolution + workflow_policy
# + tool permissions all compose correctly together, across step types none
# of Phase 3A/3B/3C's own tests happened to exercise (gender, room type,
# flight option, traveler name, guardian name, birthday, passport expiry),
# and across multi-turn sequences long enough to catch state corruption that
# only shows up several turns later.
#
# Pre-change audit (Step 1): re-read the full handle_message turn loop
# (tool_calling_runtime.py ~3607-3760). Confirmed unchanged since Phase 3C
# (which only edited _apply_required_step_capture's branch bodies, not the
# loop around it) and confirmed mutually exclusive by construction, not by
# convention:
#   - step_value_captured (from _apply_required_step_capture) gates whether
#     interruption/classifier get a turn AT ALL for a backend-owned step.
#   - _handle_conversational_interruption and _handle_off_script_classifier
#     are tried in sequence with an early `return session` on the first
#     match -- at most one of them can act on a given turn.
#   - _apply_trip_selection_from_text/_apply_trip_switch_from_text run once,
#     unconditionally, before workflow_policy.evaluate() -- each internally
#     gated (selected_trip_id/signal/confidence) so at most one of them
#     mutates the trip for a given message.
# These properties are exercised, not just re-derived, by the tests below.
# ---------------------------------------------------------------------------


def _full_state_snapshot(session: SessionState) -> dict:
    """Wider than _state_snapshot (Phase 3B/3C) -- also covers the
    new-traveler intake fields (name/nationality/birthday/guardian) Phase 3D
    exercises that earlier phases' snapshots didn't need."""
    snapshot = _state_snapshot(session)
    snapshot.update(
        {
            "customer_name": session.customer_name,
            "nationality": session.nationality,
            "birthday": session.birthday,
            "guardian_name": session.guardian_name,
            "guardian_phone": session.guardian_phone,
            "room_group": session.room_group,
        }
    )
    return snapshot


def _bali_gender_required_session(runtime: ToolCallingSessionRuntime) -> SessionState:
    _verified_runtime_with_switch_trips(runtime)
    session = runtime.create_session()
    for text in ("01554158741", "international", "1"):
        session = _send(runtime, text, session)
    assert session.stage == "traveler_gender_required"
    return session


def _bali_room_type_required_session(runtime: ToolCallingSessionRuntime) -> SessionState:
    session = _bali_gender_required_session(runtime)
    session = _send(runtime, "boys", session)
    assert session.stage == "room_type_required"
    return session


def _bali_flight_option_required_session(runtime: ToolCallingSessionRuntime) -> SessionState:
    session = _bali_selected_mid_flow_session(runtime)
    session = _send(runtime, "same", session)
    assert session.stage == "flight_option_required"
    return session


def _bali_passport_expiry_required_session(runtime: ToolCallingSessionRuntime) -> SessionState:
    session = _bali_passport_number_required_session(runtime)
    session = _send(runtime, "A1234567", session)
    assert session.stage == "passport_expiry_required"
    return session


def _new_traveler_name_required_session(runtime: ToolCallingSessionRuntime) -> SessionState:
    session = runtime.create_session()
    session = _send(runtime, "01270482380", session)
    assert session.stage == "traveler_not_found"
    return session


def _new_traveler_nationality_required_session(runtime: ToolCallingSessionRuntime) -> SessionState:
    session = _new_traveler_name_required_session(runtime)
    session = _send(runtime, "Mohamed Ashraf Safwat", session)
    assert session.stage == "nationality_required"
    return session


def _new_traveler_birthday_required_session(runtime: ToolCallingSessionRuntime) -> SessionState:
    session = _new_traveler_nationality_required_session(runtime)
    session = _send(runtime, "Egyptian", session)
    assert session.stage == "birthday_required"
    return session


def _new_minor_traveler_guardian_name_required_session(runtime: ToolCallingSessionRuntime) -> SessionState:
    """A 16-year-old new traveler -- the guardian branch requires a minor,
    unlike every other fixture in this file's default adult path."""
    session = runtime.create_session()
    sixteen_years_ago = date.today().replace(year=date.today().year - 16)
    birthday_text = sixteen_years_ago.strftime("%d/%m/%Y")
    for text in ("01270482380", "Ahmed Sami Youssef", "Egyptian", birthday_text):
        session = _send(runtime, text, session)
    assert session.stage == "guardian_name_required"
    return session


def _install_classifier_agent_for(runtime: ToolCallingSessionRuntime, responses: list) -> "GeminiAgent":
    """_install_real_gemini_agent needs read tools implementing the wider
    CRM-context surface GeminiAgent.respond() calls -- reused here for the
    new-traveler-path fixtures too (identity=None is a valid "not found"
    traveler, same as RecordingReadTools' own default)."""
    runtime._read_only_tools = _ClassifierConversationReadTools(
        trips=getattr(runtime._read_only_tools, "trips", None) or _SWITCH_TEST_TRIPS,
        identity=getattr(runtime._read_only_tools, "identity", None),
    )
    return _install_real_gemini_agent(runtime, LoopProviderStub(responses))


# ---------------------------------------------------------------------------
# A-E: side question during required data collection, across step types
# Phase 3A/3B/3C's own tests didn't happen to cover (passport_number,
# passport_country, payment_currency, and nationality already have dedicated
# coverage from those phases -- not duplicated here).
# ---------------------------------------------------------------------------
def test_side_question_during_passport_expiry_leaves_state_untouched_then_captures_normally(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _bali_passport_expiry_required_session(runtime)
    before = _full_state_snapshot(session)
    agent = _install_classifier_agent_for(
        runtime,
        [_classification_response("side_question", 0.85), text_response("Sure! What's the passport expiry date?")],
    )

    session = _send(runtime, "Is this trip family-friendly?", session)
    assert agent.classify_off_script_turn.call_count == 1
    assert session.passport_expiry == ""
    assert session.stage == "passport_expiry_required"
    after = _full_state_snapshot(session)
    after.pop("tools_used"); before.pop("tools_used")
    assert after == before

    session = _send(runtime, "01/06/2032", session)
    assert agent.classify_off_script_turn.call_count == 1  # not called again
    assert session.passport_expiry == "2032-06-01"
    assert session.stage == "passport_country_required"


def test_side_question_during_gender_collection_leaves_state_untouched_then_captures_normally(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _bali_gender_required_session(runtime)
    before = _full_state_snapshot(session)
    agent = _install_classifier_agent_for(
        runtime,
        [_classification_response("side_question", 0.85), text_response("Sure! Is your group boys or girls?")],
    )

    session = _send(runtime, "How many days is this trip?", session)
    assert agent.classify_off_script_turn.call_count == 1
    assert session.room_group == ""
    assert session.stage == "traveler_gender_required"
    after = _full_state_snapshot(session)
    after.pop("tools_used"); before.pop("tools_used")
    assert after == before

    session = _send(runtime, "boys", session)
    assert agent.classify_off_script_turn.call_count == 1
    assert session.room_group == "boys"
    assert session.stage == "room_type_required"


def test_side_question_during_room_type_collection_leaves_state_untouched_then_captures_normally(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _bali_room_type_required_session(runtime)
    before = _full_state_snapshot(session)
    agent = _install_classifier_agent_for(
        runtime,
        [_classification_response("side_question", 0.85), text_response("Sure! Single, double, or triple room?")],
    )

    session = _send(runtime, "Is breakfast included?", session)
    assert agent.classify_off_script_turn.call_count == 1
    assert session.room_type == ""
    assert session.stage == "room_type_required"
    after = _full_state_snapshot(session)
    after.pop("tools_used"); before.pop("tools_used")
    assert after == before

    session = _send(runtime, "single", session)
    assert agent.classify_off_script_turn.call_count == 1
    assert session.room_type == "Single"
    assert session.stage == "group_size_required"


def test_side_question_during_flight_option_collection_leaves_state_untouched_then_captures_normally(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _bali_flight_option_required_session(runtime)
    before = _full_state_snapshot(session)
    agent = _install_classifier_agent_for(
        runtime,
        [_classification_response("side_question", 0.85), text_response("Sure! With flight or without flight?")],
    )

    session = _send(runtime, "Can I bring extra luggage?", session)
    assert agent.classify_off_script_turn.call_count == 1
    assert session.flight_option == ""
    assert session.stage == "flight_option_required"
    after = _full_state_snapshot(session)
    after.pop("tools_used"); before.pop("tools_used")
    assert after == before

    session = _send(runtime, "2", session)
    assert agent.classify_off_script_turn.call_count == 1
    assert session.flight_option == "Without Flight"
    assert session.stage == "awaiting_passport_upload"


def test_side_question_during_new_traveler_name_collection_leaves_state_untouched_then_captures_normally(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _new_traveler_name_required_session(runtime)
    before = _full_state_snapshot(session)
    agent = _install_classifier_agent_for(
        runtime,
        [_classification_response("side_question", 0.85), text_response("Sure! What's your full name?")],
    )

    session = _send(runtime, "Is there a customer service number I can call?", session)
    assert agent.classify_off_script_turn.call_count == 1
    assert session.customer_name == ""
    assert session.stage == "traveler_not_found"
    after = _full_state_snapshot(session)
    after.pop("tools_used"); before.pop("tools_used")
    assert after == before

    session = _send(runtime, "Mohamed Ashraf Safwat", session)
    assert agent.classify_off_script_turn.call_count == 1
    assert session.customer_name == "Mohamed Ashraf Safwat"
    assert session.stage == "nationality_required"


# ---------------------------------------------------------------------------
# Live transcript bug: a customer whose phone lookup came back "not found"
# was asked for their full name, and instead replied "عرفني بس الرحلات
# الاول" ("just tell me the trips first"). Because that message contains a
# trip-reference word ("الرحلات"), _handle_public_trip_reference_if_present
# claimed it BEFORE the off-script classifier ever got a turn, fuzzy-matched
# it against trip names, found nothing, and replied "لم أجد رحلة مؤكدة بهذا
# الاسم" ("no confirmed trip found by that name") -- an answer to a question
# nobody asked, while silently abandoning the pending name field. The next
# turn ("شرم") was then read against the wrong (trip-selection) stage instead
# of being asked for a name again. Fixed by _IDENTITY_ONBOARDING_STATES: a
# failed-capture message during name/nationality/birthday collection must
# reach the existing off-script classifier's side_question route instead of
# the generic trip-reference handlers.
# ---------------------------------------------------------------------------
def test_trip_worded_side_question_during_new_traveler_name_collection_does_not_hijack_the_turn(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _new_traveler_name_required_session(runtime)
    before = _full_state_snapshot(session)
    agent = _install_classifier_agent_for(
        runtime,
        [_classification_response("side_question", 0.85), text_response("Sure! What's your full name?")],
    )

    session = _send(runtime, "عرفني بس الرحلات الاول", session)

    # The off-script classifier must be the thing that handled this turn --
    # not the trip-reference/discovery handlers, which never call it at all.
    assert agent.classify_off_script_turn.call_count == 1
    reply = session.messages[-1]["text"]
    assert "لم أجد رحلة" not in reply
    assert session.customer_name == ""
    assert session.stage == "traveler_not_found"
    after = _full_state_snapshot(session)
    after.pop("tools_used"); before.pop("tools_used")
    assert after == before

    session = _send(runtime, "Maged Aweis Alani", session)
    assert session.customer_name == "Maged Aweis Alani"
    assert session.stage == "nationality_required"


def test_side_question_during_nationality_collection_leaves_state_untouched_then_captures_normally(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _new_traveler_nationality_required_session(runtime)
    before = _full_state_snapshot(session)
    agent = _install_classifier_agent_for(
        runtime,
        [_classification_response("side_question", 0.85), text_response("Sure! What's your nationality?")],
    )

    session = _send(runtime, "Is there a customer service number I can call?", session)
    assert agent.classify_off_script_turn.call_count == 1
    assert session.nationality == ""
    assert session.stage == "nationality_required"
    after = _full_state_snapshot(session)
    after.pop("tools_used"); before.pop("tools_used")
    assert after == before

    session = _send(runtime, "Egyptian", session)
    assert agent.classify_off_script_turn.call_count == 1
    assert session.nationality == "Egyptian"
    assert session.stage == "birthday_required"


def test_side_question_during_birthday_collection_leaves_state_untouched_then_captures_normally(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _new_traveler_birthday_required_session(runtime)
    before = _full_state_snapshot(session)
    agent = _install_classifier_agent_for(
        runtime,
        [_classification_response("side_question", 0.85), text_response("Sure! What's your date of birth?")],
    )

    session = _send(runtime, "Is there a group discount?", session)
    assert agent.classify_off_script_turn.call_count == 1
    assert session.birthday == ""
    assert session.stage == "birthday_required"
    after = _full_state_snapshot(session)
    after.pop("tools_used"); before.pop("tools_used")
    assert after == before

    # Completing birthday finishes the new-traveler intake and triggers the
    # (mocked) traveler+lead save -- unlike the earlier fixture stages, this
    # turn needs a configured write executor, matching
    # test_new_traveler_intake_saves_lead_instead_of_looping's convention.
    runtime._write_executor.execute.side_effect = _write_results_by_action(
        create_traveler=NEW_TRAVELER_WRITE,
        create_lead={
            "executed": True,
            "result_id": "LD00042",
            "lead_update": {"lead_id": "LD00042"},
            "write_result_contract": {
                "status": "success",
                "record_id": "LD00042",
                "record_type": "lead",
                "executed": True,
            },
        },
    )
    session = _send(runtime, "28/4/2006", session)
    assert agent.classify_off_script_turn.call_count == 1
    assert session.birthday == "2006-04-28"
    assert session.stage == "trip_type_required"


def test_side_question_during_guardian_name_collection_leaves_state_untouched_then_captures_normally(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _new_minor_traveler_guardian_name_required_session(runtime)
    before = _full_state_snapshot(session)
    agent = _install_classifier_agent_for(
        runtime,
        [_classification_response("side_question", 0.85), text_response("Sure! What's your guardian's full name?")],
    )

    session = _send(runtime, "Do minors need extra paperwork?", session)
    assert agent.classify_off_script_turn.call_count == 1
    assert session.guardian_name == ""
    assert session.stage == "guardian_name_required"
    after = _full_state_snapshot(session)
    after.pop("tools_used"); before.pop("tools_used")
    assert after == before

    session = _send(runtime, "Sami Youssef Ahmed", session)
    assert agent.classify_off_script_turn.call_count == 1
    assert session.guardian_name == "Sami Youssef Ahmed"
    assert session.stage == "guardian_phone_required"


# ---------------------------------------------------------------------------
# H/I: trip correction -- deterministic match, no match (already covered in
# Phase 2/3A/3B), and a genuinely ambiguous match (new: two similarly-scoring
# candidates for the same query, which none of the earlier phases' two-trip
# fixture could exercise).
# ---------------------------------------------------------------------------
_AMBIGUOUS_SWITCH_TEST_TRIPS = [
    {
        "trip_id": "RT-BALI-01",
        "trip_name": "Bali Beach Escape",
        "type": "International",
        "trip_type": "international",
        "start_date": "2026-11-01",
        "end_date": "2026-11-08",
        "public_price": "2500 USD",
        "public_description": "Verified Bali program.",
        "available_single": 2,
    },
    {
        "trip_id": "RT-THAI-BEACH-01",
        "trip_name": "Thailand Beach Explorer",
        "type": "International",
        "trip_type": "international",
        "start_date": "2026-11-15",
        "end_date": "2026-11-22",
        "public_price": "2600 USD",
        "public_description": "Verified Thailand beach program.",
        "available_single": 2,
    },
    {
        "trip_id": "RT-THAI-ISLAND-01",
        "trip_name": "Thailand Island Explorer",
        "type": "International",
        "trip_type": "international",
        "start_date": "2026-12-01",
        "end_date": "2026-12-08",
        "public_price": "2700 USD",
        "public_description": "Verified Thailand island program.",
        "available_single": 2,
    },
]


def test_ambiguous_trip_correction_makes_no_change(runtime: ToolCallingSessionRuntime) -> None:
    """Test I: "Thailand" now scores confidently against TWO similarly-named
    trips with no clear margin between them -- _resolve_trip_switch_candidate
    must refuse to guess (same margin rule Phase 2 established), whether
    reached via the deterministic keyword-gated path or the classifier's
    correction_trip_switch route.
    """
    runtime._read_only_tools = RecordingReadTools(
        trips=_AMBIGUOUS_SWITCH_TEST_TRIPS,
        identity={"traveler_id": "TR00042", "full_name": "Returning Traveler", "status": "Active"},
    )
    session = runtime.create_session()
    for text in ("01554158741", "international", "1", "boys", "single", "2"):
        session = _send(runtime, text, session)
    assert session.selected_trip_id == "RT-BALI-01"
    before = _state_snapshot(session)

    session = _send(runtime, "actually show me Thailand instead", session)

    assert _state_snapshot(session) == before


def test_ambiguous_trip_correction_via_classifier_makes_no_change(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Same ambiguous-match refusal, reached through the classifier's
    correction_trip_switch route instead of the keyword-gated one -- proves
    the classifier substitutes nothing when the deterministic resolver
    itself is unable to pick."""
    runtime._read_only_tools = RecordingReadTools(
        trips=_AMBIGUOUS_SWITCH_TEST_TRIPS,
        identity={"traveler_id": "TR00042", "full_name": "Returning Traveler", "status": "Active"},
    )
    session = runtime.create_session()
    for text in ("01554158741", "international", "1", "boys", "single", "2"):
        session = _send(runtime, text, session)
    before = _state_snapshot(session)
    agent = _install_real_gemini_agent(
        runtime,
        LoopProviderStub([_classification_response("correction_trip_switch", 0.8, target_hint="Thailand")]),
    )

    session = _send(runtime, "no I meant Thailand", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert _state_snapshot(session) == before


# ---------------------------------------------------------------------------
# J/K: trip-type correction -- deterministic resolution is already covered
# (Phase 3B); "unsupported" here means a correction signal for a value
# trip_type does not even model (only local/international exist -- there is
# no "family" trip type), which is the existing, intentional architecture,
# not a gap to expand.
# ---------------------------------------------------------------------------
def test_trip_type_correction_with_a_value_the_workflow_does_not_model_makes_no_change(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test K: trip_type only has two valid values (local/international) --
    "family" is not one of them anywhere in TRIP_TYPE_TERMS. A correction
    signal naming an unsupported value must resolve to no change, the same
    outcome as any other unresolvable correction (Phase 3B Test G), not a
    guessed value."""
    assert normalize_trip_type("family") == ""
    session = _bali_selected_mid_flow_session(runtime)
    before = _state_snapshot(session)
    agent = _install_real_gemini_agent(
        runtime,
        LoopProviderStub([_classification_response("correction_trip_type_switch", 0.8, target_hint="family")]),
    )

    session = _send(runtime, "actually make it a family trip instead", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert _state_snapshot(session) == before


# ---------------------------------------------------------------------------
# L: multi-turn continuity -- several real steps, with a side question
# interleaved, checked at the END of the sequence (not just immediately
# after the side question) to catch corruption that only shows up later.
# ---------------------------------------------------------------------------
def test_multi_turn_conversation_with_interleaved_side_question_reaches_currency_intact(
    runtime: ToolCallingSessionRuntime,
) -> None:
    _verified_runtime_with_switch_trips(runtime)
    session = runtime.create_session()
    for text in ("01554158741", "international", "1", "boys", "single", "2", "same"):
        session = _send(runtime, text, session)
    assert session.stage == "flight_option_required"

    runtime._read_only_tools = _ClassifierConversationReadTools(
        trips=_SWITCH_TEST_TRIPS,
        identity={"traveler_id": "TR00042", "full_name": "Returning Traveler", "status": "Active"},
    )
    _install_classifier_agent_for(
        runtime,
        [_classification_response("side_question", 0.85), text_response("Sure! With flight or without flight?")],
    )
    session = _send(runtime, "Is this a good trip for beginners?", session)
    assert session.stage == "flight_option_required"

    session = _send(runtime, "2", session)
    assert session.stage == "awaiting_passport_upload"
    runtime.handle_passport_attachment(session, "passport-scan.pdf")
    for text in ("done", "A1234567", "01/06/2032", "Egyptian", "1"):
        session = _send(runtime, text, session)

    assert session.stage == "booking_confirmation_required"
    assert session.selected_trip_id == "RT-BALI-01"
    assert session.trip_type == "international"
    assert session.room_group == "boys"
    assert session.room_type == "Single"
    assert session.group_size == 2
    assert session.flight_option == "Without Flight"
    assert session.passport_attachment_ref == "passport-scan.pdf"
    assert session.passport_number == ""
    assert session.passport_expiry == ""
    assert session.passport_nationality == ""
    assert session.currency == "EGP"


# ---------------------------------------------------------------------------
# M: mixed-language / realistic phrasing. Only exercises support that
# already exists (Arabic side questions via the classifier's general
# language handling, Arabic valid answers via the existing lexicon) -- no
# fuzzy matching, no new vocabulary.
# ---------------------------------------------------------------------------
def test_arabic_side_question_during_passport_country_then_arabic_valid_answer(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _bali_passport_country_required_session(runtime)
    agent = _install_classifier_agent_for(
        runtime,
        [
            _classification_response("side_question", 0.85),
            text_response("الرحلة مناسبة للعائلات! ما هي جنسية جواز سفرك؟"),
        ],
    )

    session = _send(runtime, "هل الرحلة مناسبة للعائلات؟", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert session.passport_nationality == ""
    assert session.stage == "passport_country_required"

    session = _send(runtime, "مصري", session)

    assert session.passport_nationality == "Egyptian"
    assert session.stage == "currency_required"


# ---------------------------------------------------------------------------
# N: multiple intents in one message. Documents (does not expand) the
# existing, intentional single-intent-per-turn behavior: strict capture only
# recognizes a clean/isolated answer for the pending field, so a compound
# message with a side question folded in is not partially captured -- it is
# answered conversationally and the field stays pending for a clean answer
# on a later turn.
# ---------------------------------------------------------------------------
def test_compound_message_with_an_embedded_answer_does_not_get_partially_captured(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _bali_passport_country_required_session(runtime)
    # _extract_nationality_hint only recognizes the whole answer or a
    # specific trigger phrase ("my nationality is X"/"i am X") -- this
    # sentence contains neither, so strict capture does not isolate "Saudi
    # Arabia" out of it. Confirmed directly against the resolver so this
    # test pins the actual existing behavior rather than assuming it.
    compound_message = "I want Saudi Arabia as my passport country, and is the trip family friendly?"
    assert ToolCallingSessionRuntime._extract_nationality_hint(compound_message) == ""
    agent = _install_classifier_agent_for(
        runtime,
        [
            _classification_response("side_question", 0.85),
            text_response("This trip is great for families! Which country issued your passport?"),
        ],
    )

    session = _send(runtime, compound_message, session)

    assert agent.classify_off_script_turn.call_count == 1
    # No partial/guessed capture -- the field is untouched, not silently
    # set to "Saudi Arabia" or "Saudi".
    assert session.passport_nationality == ""
    assert session.stage == "passport_country_required"

    # A clean answer on the next turn still works normally. ("Saudi Arabia"
    # itself now resolves as of Phase 4 -- see
    # test_passport_country_accepts_the_plain_country_name_variant -- so
    # this uses a bare alias to keep this test's own focus on the compound
    # message, not on that specific alias.)
    session = _send(runtime, "Saudi", session)
    assert session.passport_nationality == "Saudi"
    assert session.stage == "currency_required"


# ---------------------------------------------------------------------------
# Phase 7 -- Remaining Privacy & Observability Hardening. Phase 3A-3D, Phase
# 4, 5, and 6 are approved and unmodified. This phase's audit (full detail
# in the final report) resolved both items Phase 6 left open, WITHOUT any
# production code change:
#
# 1. session_flow.py's raw-message/raw-name/preview-dump logging is real,
#    but confirmed unreachable in production: Settings.validate() (called
#    inside create_app(), which every real entrypoint --
#    services/ai_agent/wsgi.py AND demo_web/app.py's fallback -- calls
#    directly, unwrapped, at import time) hard-rejects app_env="production"
#    with ai_agent_mode != "tool_calling", and wsgi.py raising at import
#    time means gunicorn's worker fails to boot at all. This is contingent
#    on the deployed environment actually setting APP_ENV=production (an
#    operational fact outside this repository), not a code-level guarantee
#    independent of configuration -- flagged explicitly in the report.
#    Test A below locks in the enforcement itself so it can't silently
#    regress.
#
# 2. gemini_agent.py/gemini_provider.py's provider-level exception logging,
#    marked "theoretical" in Phase 5/6, was traced further: GeminiProviderError
#    is always raised from `last_error` (an HTTPError/TimeoutError/generic
#    Exception object) whose str() is a status line ("HTTP Error 400: Bad
#    Request") or generic description -- never the response body (only
#    .read() exposes that, consumed separately and only structurally by
#    _parse_google_error). Unlike CRMApiError (Phase 6's real, fixed leak),
#    there is no code path here that embeds the raw response body. Test B
#    locks this in.
#
# Repository-wide re-check of the active tool_calling production path
# (tool_calling_runtime.py, gemini_agent.py, write_tool_executor.py) for
# raw user_message/session.preview/session.messages logging found nothing
# beyond what Phase 6 already fixed.
# ---------------------------------------------------------------------------
def test_production_config_rejects_non_tool_calling_agent_mode(runtime: ToolCallingSessionRuntime) -> None:
    """Test A: locks in the exact enforcement session_flow.py's
    non-production classification depends on -- if this check is ever
    weakened or removed, this test fails loudly instead of silently
    widening session_flow.py's raw-logging exposure to production."""
    base_settings = runtime.settings
    prod_wrong_mode = replace(base_settings, app_env="production", ai_agent_mode="deterministic")

    errors = prod_wrong_mode.validate()

    assert any("AI_AGENT_MODE must be 'tool_calling' in production" in error for error in errors)


def test_production_config_accepts_tool_calling_agent_mode_for_this_specific_check(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Regression guard: the same check must NOT fire when the mode is
    correct -- proves Test A is checking the actual condition, not a
    tautology that always includes an error string."""
    base_settings = runtime.settings
    prod_right_mode = replace(base_settings, app_env="production", ai_agent_mode="tool_calling")

    errors = prod_right_mode.validate()

    assert not any("AI_AGENT_MODE must be 'tool_calling' in production" in error for error in errors)


def test_gemini_provider_error_never_embeds_the_raw_http_response_body(monkeypatch) -> None:
    """Test B: investigated whether GeminiProviderError (raised from
    generate()'s HTTP-error path) could embed the raw Gemini API response
    body the way CRMApiError used to (Phase 6's real, fixed leak) -- it does
    not. Simulates a response body shaped like a validation error that
    echoes back submitted data (the exact class of risk Phase 6 found and
    fixed for the CRM API) to prove GeminiProviderError's own message stays
    clean regardless.
    """
    from urllib import error as urllib_error

    from services.ai_agent.llm.gemini_provider import GeminiProvider, GeminiProviderError

    provider = GeminiProvider(api_key="test-key", model="gemini-2.0-flash", retries=0)
    leaking_body = b'{"error": {"message": "raw_phone 01554158741 for Mohamed Ashraf Safwat is invalid"}}'

    def _raise_http_error(*_args, **_kwargs):
        raise urllib_error.HTTPError(
            url="https://example.invalid", code=400, msg="Bad Request", hdrs=None, fp=io.BytesIO(leaking_body)
        )

    monkeypatch.setattr("services.ai_agent.llm.gemini_provider.request.urlopen", _raise_http_error)

    with pytest.raises(GeminiProviderError) as exc_info:
        provider.generate(system_prompt="", messages=[{"role": "user", "parts": [{"text": "hi"}]}])

    assert "01554158741" not in str(exc_info.value)
    assert "Mohamed" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# Phase 5 -- Production Readiness & Observability Audit. Phase 3A/3B/3C/3D
# and Phase 4 are approved and unmodified. No workflow/classifier/validation
# behavior changed in this phase -- every test below asserts what got
# LOGGED, not a behavior change (the behavior itself is already proven by
# every prior phase's tests, re-run unmodified as part of this phase's own
# regression pass).
#
# Audit found two genuinely silent branches (zero log output, indistinguish-
# able from "the classifier was never called"): GeminiAgent.
# classify_off_script_turn's malformed-JSON-shape branches (parsed to a
# non-dict; category not in the valid set), and
# _handle_off_script_classifier's low-confidence/unclear-category downgrade.
# Also added elapsed_ms timing to classify_off_script_turn (mirroring
# respond()/rewrite_message()'s existing convention in this same file, which
# this one call site had never had) and previous_step/new_step to the three
# main turn-outcome log lines in handle_message, so a state transition can
# be read off one log line instead of correlating two turns' worth of logs.
# ---------------------------------------------------------------------------
def test_classifier_low_confidence_outcome_is_now_logged(
    runtime: ToolCallingSessionRuntime, caplog
) -> None:
    """Previously silent: a below-threshold classification returned False
    with zero log output, indistinguishable from the classifier never being
    invoked at all."""
    session = _bali_selected_mid_flow_session(runtime)
    _install_real_gemini_agent(
        runtime,
        LoopProviderStub([_classification_response("side_question", 0.30)]),
    )

    with caplog.at_level("INFO", logger="rahma_agent"):
        _send(runtime, "is this trip family-friendly?", session)

    messages = [r.getMessage() for r in caplog.records if r.name == "rahma_agent"]
    assert any("Off-script classifier not acted on" in m and "reason=low_confidence" in m for m in messages)


def test_classifier_unclear_category_outcome_is_distinguished_from_low_confidence(
    runtime: ToolCallingSessionRuntime, caplog
) -> None:
    """The runtime-level "not acted on" log distinguishes an explicit
    category="unclear" result from a low-confidence downgrade of a
    different category -- both used to be the identical silent no-op."""
    session = _bali_selected_mid_flow_session(runtime)
    _install_real_gemini_agent(
        runtime,
        LoopProviderStub([_classification_response("unclear", 0.9)]),
    )

    with caplog.at_level("INFO", logger="rahma_agent"):
        _send(runtime, "is this trip family-friendly?", session)

    messages = [r.getMessage() for r in caplog.records if r.name == "rahma_agent"]
    assert any("Off-script classifier not acted on" in m and "reason=unclear_category" in m for m in messages)


def test_classify_off_script_turn_logs_non_object_json_outcome(runtime: ToolCallingSessionRuntime, caplog) -> None:
    """Previously silent: the provider returning syntactically valid JSON
    that isn't an object (e.g. a bare list) returned the fail-closed result
    with no log at all."""
    agent = _install_real_gemini_agent(runtime, LoopProviderStub([text_response(json.dumps([1, 2, 3]))]))

    with caplog.at_level("WARNING", logger="rahma_agent"):
        result = agent.classify_off_script_turn(
            user_message="anything", session_context={}, conversation_history=[]
        )

    assert result == {"category": "unclear", "target_hint": "", "candidate_value": "", "confidence": 0.0}
    messages = [r.getMessage() for r in caplog.records if r.name == "rahma_agent"]
    assert any("outcome=non_object_output" in m for m in messages)


def test_classify_off_script_turn_logs_invalid_category_outcome(runtime: ToolCallingSessionRuntime, caplog) -> None:
    """Previously silent: the provider returning a well-formed JSON object
    whose category isn't one of OFF_SCRIPT_CLASSIFIER_CATEGORIES returned
    the fail-closed result with no log at all."""
    agent = _install_real_gemini_agent(
        runtime,
        LoopProviderStub([text_response(json.dumps({"category": "bogus_category", "confidence": 0.9}))]),
    )

    with caplog.at_level("WARNING", logger="rahma_agent"):
        result = agent.classify_off_script_turn(
            user_message="anything", session_context={}, conversation_history=[]
        )

    assert result == {"category": "unclear", "target_hint": "", "candidate_value": "", "confidence": 0.0}
    messages = [r.getMessage() for r in caplog.records if r.name == "rahma_agent"]
    assert any("outcome=invalid_category" in m and "bogus_category" in m for m in messages)


def test_classify_off_script_turn_logs_elapsed_ms_on_success(runtime: ToolCallingSessionRuntime, caplog) -> None:
    """Step 7: the classifier call had no latency instrumentation at all --
    every other provider call site in this file (respond()/rewrite_message())
    already logs elapsed_ms."""
    agent = _install_real_gemini_agent(
        runtime,
        LoopProviderStub([_classification_response("side_question", 0.8)]),
    )

    with caplog.at_level("INFO", logger="rahma_agent"):
        result = agent.classify_off_script_turn(
            user_message="anything", session_context={}, conversation_history=[]
        )

    assert result["category"] == "side_question"
    messages = [r.getMessage() for r in caplog.records if r.name == "rahma_agent"]
    match = next((m for m in messages if "outcome=parsed" in m), None)
    assert match is not None
    elapsed = re.search(r"elapsed_ms=(\d+)", match)
    assert elapsed is not None
    assert int(elapsed.group(1)) >= 0


def test_previous_step_appears_on_the_normal_advancement_log_line(
    runtime: ToolCallingSessionRuntime, caplog
) -> None:
    """Step 5: a state transition should be readable from one log line --
    previously only the NEW step was logged, requiring correlation with the
    preceding turn's log line to know the previous one."""
    session = _bali_selected_mid_flow_session(runtime)
    assert session.stage == "group_nationality_type_required"

    with caplog.at_level("INFO", logger="rahma_agent"):
        _send(runtime, "same", session)

    messages = [r.getMessage() for r in caplog.records if r.name == "rahma_agent"]
    assert any(
        "previous_step=group_nationality_type_required" in m and "step=collect_flight_preference" in m
        for m in messages
    )


def test_previous_step_and_new_step_appear_on_the_classifier_route_log_line(
    runtime: ToolCallingSessionRuntime, caplog
) -> None:
    """Same guarantee for the classifier-routed trip-switch path, where the
    fresh post-switch stage (session.stage) genuinely differs from the
    stale workflow_decision.required_step computed before the switch."""
    session = _bali_selected_mid_flow_session(runtime)
    _install_real_gemini_agent(
        runtime,
        LoopProviderStub([_classification_response("correction_trip_switch", 0.83, target_hint="Thailand")]),
    )

    with caplog.at_level("INFO", logger="rahma_agent"):
        _send(runtime, "no I meant Thailand", session)

    messages = [r.getMessage() for r in caplog.records if r.name == "rahma_agent"]
    match = next((m for m in messages if "handled off-script classifier route" in m), None)
    assert match is not None
    assert "previous_step=group_nationality_type_required" in match
    assert "new_step=traveler_gender_required" in match


def test_new_observability_logs_do_not_leak_the_raw_user_message(
    runtime: ToolCallingSessionRuntime, caplog
) -> None:
    """Privacy check (Step 12): none of the new log lines this phase added
    should ever include the customer's actual message text -- they carry
    only session id, step names, category, confidence, and timing."""
    session = _bali_selected_mid_flow_session(runtime)
    distinctive_message = "zzz-distinctive-marker-not-a-real-answer-zzz"
    _install_real_gemini_agent(
        runtime,
        LoopProviderStub([_classification_response("side_question", 0.30)]),
    )

    with caplog.at_level("INFO", logger="rahma_agent"):
        _send(runtime, distinctive_message, session)

    for record in caplog.records:
        if record.name != "rahma_agent":
            continue
        assert distinctive_message not in record.getMessage()


# ---------------------------------------------------------------------------
# Phase 6 -- Sensitive Data Logging Remediation. Phase 3A/3B/3C/3D, Phase 4,
# and Phase 5 are approved and unmodified. No workflow/classifier/tool-
# permission/booking behavior changed -- every fix in this phase is a
# logging-only change, and every test below asserts what got LOGGED (or
# explicitly did NOT get logged), never a behavior change.
#
# Audit summary (full detail in the final report): of every tool schema in
# tool_registry.py/write_tool_registry.py reachable via
# GeminiAgent._run_tool_loop, the sensitive fields that can actually appear
# as LLM tool-call ARGUMENTS are raw_phone (search_traveler,
# find_traveler_by_phone, get_traveler_profile, get_traveler_trip_history,
# get_passport_status, lookup_lead, create_booking_draft, create_handoff),
# customer_name/full_name/traveler_name (create_lead, create_booking_draft,
# create_handoff), and free-text notes/reason/summary fields on the write
# tools. Passport number/DOB/nationality never appear as LLM tool
# arguments in the current schemas -- they are captured deterministically
# (_apply_required_step_capture) and persisted via a separate code path
# (GeminiWriteToolExecutor.execute(), whose own audit log was independently
# checked and found already safe -- see the "no additional mutation" note
# below). update_booking/update_traveler are not registered as LLM-callable
# tools at all (confirmed via GeminiAgent._validate_tool_call raising for
# an unregistered name) -- they can only be reached via that same
# deterministic path, not through the LLM tool-calling loop this phase's
# fix covers.
# ---------------------------------------------------------------------------
_SENSITIVE_SYNTHETIC_TOOL_INPUT = {
    "raw_phone": "01234567890",
    "customer_name": "Mohamed Ashraf Safwat",
    "full_name": "Mohamed Ashraf Safwat",
    "passport_number": "A1234567",
    "date_of_birth": "1990-01-01",
    "nationality": "Egyptian",
    "notes": "Customer prefers a window seat and mentioned a family emergency.",
    "trip_id": "RT-BALI-01",
    "currency": "USD",
    "group_size": 2,
    "handoff_required": True,
    "unknown_future_field": "some value nobody vetted yet",
}


def test_safe_tool_log_summary_redacts_passport_number(runtime: ToolCallingSessionRuntime) -> None:
    """Test A."""
    summary = GeminiAgent._safe_tool_log_summary(_SENSITIVE_SYNTHETIC_TOOL_INPUT)
    assert "A1234567" not in json.dumps(summary)


def test_safe_tool_log_summary_redacts_phone(runtime: ToolCallingSessionRuntime) -> None:
    """Test B."""
    summary = GeminiAgent._safe_tool_log_summary(_SENSITIVE_SYNTHETIC_TOOL_INPUT)
    assert "01234567890" not in json.dumps(summary)


def test_safe_tool_log_summary_redacts_date_of_birth(runtime: ToolCallingSessionRuntime) -> None:
    """Test C."""
    summary = GeminiAgent._safe_tool_log_summary(_SENSITIVE_SYNTHETIC_TOOL_INPUT)
    assert "1990-01-01" not in json.dumps(summary)


def test_safe_tool_log_summary_redacts_names(runtime: ToolCallingSessionRuntime) -> None:
    """Test D."""
    summary = GeminiAgent._safe_tool_log_summary(_SENSITIVE_SYNTHETIC_TOOL_INPUT)
    assert "Mohamed" not in json.dumps(summary)
    assert "Safwat" not in json.dumps(summary)


def test_safe_tool_log_summary_redacts_nationality(runtime: ToolCallingSessionRuntime) -> None:
    """Test E."""
    summary = GeminiAgent._safe_tool_log_summary(_SENSITIVE_SYNTHETIC_TOOL_INPUT)
    assert "Egyptian" not in json.dumps(summary)


def test_safe_tool_log_summary_redacts_free_text(runtime: ToolCallingSessionRuntime) -> None:
    """Test F: arbitrary free text must never appear, even a fragment of it."""
    summary = GeminiAgent._safe_tool_log_summary(_SENSITIVE_SYNTHETIC_TOOL_INPUT)
    rendered = json.dumps(summary)
    assert "window seat" not in rendered
    assert "family emergency" not in rendered
    assert summary["notes"] == "<redacted len=64>"


def test_safe_tool_log_summary_preserves_approved_safe_metadata(runtime: ToolCallingSessionRuntime) -> None:
    """Test G: explicitly-approved safe fields, and type/presence metadata
    for everything else, must remain visible -- this is not a blanket
    "redact everything" implementation."""
    summary = GeminiAgent._safe_tool_log_summary(_SENSITIVE_SYNTHETIC_TOOL_INPUT)
    assert summary["trip_id"] == "RT-BALI-01"
    assert summary["currency"] == "USD"
    assert summary["group_size"] == 2
    assert summary["handoff_required"] is True


def test_safe_tool_log_summary_does_not_mutate_the_original_input(runtime: ToolCallingSessionRuntime) -> None:
    """Test H."""
    original = dict(_SENSITIVE_SYNTHETIC_TOOL_INPUT)
    GeminiAgent._safe_tool_log_summary(_SENSITIVE_SYNTHETIC_TOOL_INPUT)
    assert _SENSITIVE_SYNTHETIC_TOOL_INPUT == original
    assert _SENSITIVE_SYNTHETIC_TOOL_INPUT["passport_number"] == "A1234567"


def test_safe_tool_log_summary_redacts_an_unknown_field_by_default(runtime: ToolCallingSessionRuntime) -> None:
    """Test K: default-deny -- a field this list has never seen (e.g. a
    new tool argument added later) is redacted, not logged raw, even though
    nothing marks it as sensitive by name."""
    summary = GeminiAgent._safe_tool_log_summary(_SENSITIVE_SYNTHETIC_TOOL_INPUT)
    assert "some value nobody vetted yet" not in json.dumps(summary)
    assert summary["unknown_future_field"].startswith("<redacted")


def test_create_lead_tool_call_does_not_log_raw_name_or_phone(
    runtime: ToolCallingSessionRuntime, caplog
) -> None:
    """Test I (create_lead): end-to-end through the real GeminiAgent tool
    loop, not just the unit-level helper -- proves the actual log line this
    phase changed no longer contains the sensitive values, using a
    realistic write-tool call attempt exactly like Phase 3B's Test J."""
    session = _bali_passport_number_required_session(runtime)
    agent = _install_classifier_agent_for(
        runtime,
        [
            _classification_response("side_question", 0.9),
            function_call_response(
                "create_lead",
                {"customer_name": "Mohamed Ashraf Safwat", "raw_phone": "01554158741", "notes": "wants a window seat"},
            ),
            text_response("Got it -- could you share your passport number?"),
        ],
    )

    with caplog.at_level("INFO", logger="rahma_agent"):
        _send(runtime, "is this trip family-friendly?", session)

    assert agent.classify_off_script_turn.call_count == 1
    tool_request_logs = [
        r.getMessage() for r in caplog.records if r.name == "rahma_agent" and "Gemini tool requested" in r.getMessage()
    ]
    assert any("create_lead" in m for m in tool_request_logs), "the tool call never reached the log line under test"
    for record in caplog.records:
        if record.name != "rahma_agent":
            continue
        text = record.getMessage()
        assert "Mohamed" not in text
        assert "01554158741" not in text
        assert "window seat" not in text


def test_create_booking_draft_tool_call_does_not_log_raw_traveler_name(
    runtime: ToolCallingSessionRuntime, caplog
) -> None:
    """Test I (create_booking_draft)."""
    session = _bali_passport_number_required_session(runtime)
    agent = _install_classifier_agent_for(
        runtime,
        [
            _classification_response("side_question", 0.9),
            function_call_response(
                "create_booking_draft",
                {"traveler_name": "Mohamed Ashraf Safwat", "booking_notes": "allergic to seafood"},
            ),
            text_response("Got it -- could you share your passport number?"),
        ],
    )

    with caplog.at_level("INFO", logger="rahma_agent"):
        _send(runtime, "is this trip family-friendly?", session)

    assert agent.classify_off_script_turn.call_count == 1
    tool_request_logs = [
        r.getMessage() for r in caplog.records if r.name == "rahma_agent" and "Gemini tool requested" in r.getMessage()
    ]
    assert any("create_booking_draft" in m for m in tool_request_logs), "the tool call never reached the log line under test"
    for record in caplog.records:
        if record.name != "rahma_agent":
            continue
        text = record.getMessage()
        assert "Mohamed" not in text
        assert "seafood" not in text


@pytest.mark.parametrize("unregistered_tool_name", ["update_booking", "update_traveler"])
def test_update_booking_and_update_traveler_are_not_llm_callable_tools(
    runtime: ToolCallingSessionRuntime, unregistered_tool_name: str
) -> None:
    """Test I (update_booking/update_traveler): these two are never
    registered as LLM-callable tools in the first place (confirmed via
    build_write_tool_registry()'s actual contents) -- an attempted call is
    rejected by GeminiAgent._validate_tool_call before it could ever reach
    the (now-safe) logging line, so there is nothing for this phase's
    redaction to protect there; they are only reachable via the
    deterministic GeminiWriteToolExecutor.execute() path, whose own
    audit-log dict was independently checked and contains no raw payload
    fields (session_id/action/decision/executed/result_id/reason/warnings
    only)."""
    registry = build_tool_calling_registry()
    from services.ai_agent.ai_agent_app.agent.write_tool_registry import build_write_tool_registry

    assert unregistered_tool_name not in registry
    assert unregistered_tool_name not in build_write_tool_registry()


def test_find_traveler_by_phone_read_tool_call_does_not_log_raw_phone(
    runtime: ToolCallingSessionRuntime, caplog
) -> None:
    """Test J: a representative read-only tool."""
    session = _bali_passport_number_required_session(runtime)
    agent = _install_classifier_agent_for(
        runtime,
        [
            _classification_response("side_question", 0.9),
            function_call_response("find_traveler_by_phone", {"raw_phone": "01554158741"}),
            text_response("Got it -- could you share your passport number?"),
        ],
    )

    with caplog.at_level("INFO", logger="rahma_agent"):
        _send(runtime, "is this trip family-friendly?", session)

    assert agent.classify_off_script_turn.call_count == 1
    tool_request_logs = [
        r.getMessage() for r in caplog.records if r.name == "rahma_agent" and "Gemini tool requested" in r.getMessage()
    ]
    assert any("find_traveler_by_phone" in m for m in tool_request_logs), "the tool call never reached the log line under test"
    for record in caplog.records:
        if record.name != "rahma_agent":
            continue
        assert "01554158741" not in record.getMessage()


def test_crm_api_write_failure_logs_a_safe_error_code_not_the_raw_exception(caplog) -> None:
    """Test L (tool-result/exception leakage): CRMApiError's own message can
    embed up to 500 chars of the raw CRM API HTTP response body (which can
    echo back submitted field values in a validation error) -- the fix logs
    the same safe error code already used for the audit trail instead."""
    from unittest.mock import Mock

    from services.ai_agent.ai_agent_app.agent.crm_api_client import CRMApiError
    from services.ai_agent.ai_agent_app.agent.write_tool_executor import GeminiWriteToolExecutor
    from services.ai_agent.validation.validation_result import APPROVED, ValidationResult

    read_only_tools = Mock()
    read_only_tools.api_client = Mock()
    read_only_tools.api_client.write.side_effect = CRMApiError(
        "CRM API returned HTTP 400: Invalid raw_phone '01554158741' for traveler Mohamed Ashraf Safwat"
    )
    action_validator = Mock()
    action_validator.validate_action.return_value = ValidationResult(action="create_lead", decision=APPROVED)
    executor = GeminiWriteToolExecutor(
        settings=Mock(), read_only_tools=read_only_tools, action_validator=action_validator
    )

    with caplog.at_level("ERROR", logger="rahma_agent"):
        executor.execute(
            action="create_lead",
            payload={"raw_phone": "01554158741", "full_name": "Mohamed Ashraf Safwat"},
            session_context={"session_id": "test-session"},
        )

    for record in caplog.records:
        if record.name != "rahma_agent":
            continue
        text = record.getMessage()
        assert "01554158741" not in text
        assert "Mohamed" not in text


def test_passport_country_mismatch_log_no_longer_includes_the_actual_values(
    runtime: ToolCallingSessionRuntime, caplog
) -> None:
    """Test L (tool-result/exception leakage), continued: the nationality
    cross-check log line found during this phase's repository-wide search
    (Step 11) used to log both the stated and passport nationality values
    directly."""
    session = _bali_passport_country_required_session(runtime)
    session.nationality = "Egyptian"

    with caplog.at_level("INFO", logger="rahma_agent"):
        _send(runtime, "Saudi", session)

    messages = [r.getMessage() for r in caplog.records if r.name == "rahma_agent"]
    match = next((m for m in messages if "Passport nationality differs" in m), None)
    assert match is not None
    assert "mismatch=true" in match
    assert "Egyptian" not in match
    assert "Saudi" not in match


# ---------------------------------------------------------------------------
# O/T: one-transition-per-turn, exercised through the classifier's trip-
# switch route specifically (Phase 3B's Test L proved this for the
# deterministic keyword-gated route; this proves the classifier-routed trip
# switch also does not produce a second, competing transition).
# ---------------------------------------------------------------------------
def test_classifier_routed_trip_switch_produces_exactly_one_transition(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _bali_selected_mid_flow_session(runtime)
    agent = _install_real_gemini_agent(
        runtime,
        LoopProviderStub([_classification_response("correction_trip_switch", 0.83, target_hint="Thailand")]),
    )

    session = _send(runtime, "no I meant Thailand", session)

    assert agent.classify_off_script_turn.call_count == 1
    # Exactly one trip is selected, exactly one reset happened -- not
    # switched, then re-evaluated, then switched again.
    assert session.selected_trip_id == "RT-THAI-01"
    assert session.room_type == ""
    assert session.group_size == 1
    assert session.flight_option == ""
    assert session.stage == "traveler_gender_required"


# ---------------------------------------------------------------------------
# P/Q: classifier failure behavior, exercised across a full turn AND proving
# the conversation continues correctly on the following turn (not just that
# the failing turn itself degrades safely, which Phase 3A/3B already pinned).
# ---------------------------------------------------------------------------
def test_conversation_continues_normally_after_a_classifier_provider_failure(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _bali_passport_number_required_session(runtime)
    agent = _install_real_gemini_agent(runtime, _RaisingProviderStub(GeminiProviderError("boom")))

    session = _send(runtime, "is this trip family-friendly?", session)
    assert session.passport_number == ""
    assert session.stage == "passport_number_required"
    assert session.messages[-1]["role"] == "assistant"
    assert session.messages[-1]["text"]

    session = _send(runtime, "A1234567", session)

    assert session.passport_number == "A1234567"
    assert session.stage == "passport_expiry_required"
    assert agent.classify_off_script_turn.call_count == 1  # not retried on the valid turn


def test_conversation_continues_normally_after_a_malformed_classifier_response(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _bali_passport_number_required_session(runtime)
    agent = _install_real_gemini_agent(runtime, LoopProviderStub([text_response("not json")]))

    session = _send(runtime, "is this trip family-friendly?", session)
    assert session.passport_number == ""
    assert session.stage == "passport_number_required"

    session = _send(runtime, "A1234567", session)

    assert session.passport_number == "A1234567"
    assert session.stage == "passport_expiry_required"
    assert agent.classify_off_script_turn.call_count == 1


# ---------------------------------------------------------------------------
# R: unauthorized conversational write attempts, closing the one named tool
# Phase 3B didn't specifically cover (create_booking_draft; create_lead,
# update_booking, and update_traveler were already proven in Phase 3B).
# ---------------------------------------------------------------------------
def test_side_question_attempted_create_booking_draft_is_still_blocked(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _bali_passport_number_required_session(runtime)
    before = _state_snapshot(session)
    agent = _install_real_gemini_agent(
        runtime,
        LoopProviderStub(
            [
                _classification_response("side_question", 0.9),
                function_call_response("create_booking_draft", {}),
                text_response("Got it -- could you share your passport number?"),
            ]
        ),
    )

    session = _send(runtime, "can you just book it now?", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert "create_booking_draft" not in (session.tools_used or [])
    assert session.booking_confirmed is False
    assert _state_snapshot(session) == before


# ---------------------------------------------------------------------------
# Phase 4 -- Controlled Semantic Input Resolution. Phase 3A/3B/3C/3D are
# approved and unmodified.
#
# Audit finding (Step 1/2): passport_country's own capture already resolves
# through resolve_nationality/_extract_nationality_hint (Phase 3C). The
# brief's own example gap -- "Saudi" and "Saudi Arabian" resolve, but the
# plain country name "Saudi Arabia" did not -- was a single missing entry in
# nationality_reference.py's _NATIONALITIES dict; every other Gulf-country
# entry already carries both the demonym and the plain country name. A
# second gap: _extract_nationality_hint's trigger-phrase regex only covered
# "my nationality is X"/"i am X", not a passport-framed answer like "my
# passport is from X" -- a phrasing at least as natural for the question
# collect_passport_country actually asks ("which country issued your
# passport?").
#
# Both gaps are fully solved by completing the EXISTING deterministic
# alias/trigger-phrase mechanism -- no unbounded universe, no invented
# candidate, no confidence score to calibrate. This triggers this phase's
# own Stop Condition #2 ("existing deterministic aliases already solve the
# identified cases"): no LLM-based semantic resolver was built. There is no
# resolve_structured_input(), no candidate/confidence/reason contract, and
# no new provider call -- there is nothing here to route around Phase 3's
# capture order, so Phase 3's routing (strict capture -> interruption ->
# classifier -> fallback) is completely unchanged.
#
# Because no semantic/LLM layer exists, several of the brief's Step 13 test
# letters have no applicable target and are intentionally not faked:
#   H (invented candidate), I (low confidence), J (ambiguous candidate),
#   K (malformed response), L (provider failure), M (semantic layer cannot
#   mutate state directly), N (semantic layer cannot execute tools).
# These would only be meaningful against a component this phase's audit
# concluded should not be built. What Phase 3C's tests already prove --
# _extract_nationality_hint returns "" (not a guess) for anything it does
# not recognize, and nothing bypasses the existing capture/mutation path --
# remains the operative safety guarantee here too, and is re-exercised
# below against the new alias/trigger-phrase coverage specifically.
# ---------------------------------------------------------------------------
def test_passport_country_accepts_the_plain_country_name_variant(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Tests D/E/F/G: the brief's own literal gap -- "Saudi Arabia" (the
    plain country name) now resolves to the same canonical value "Saudi"
    already/Saudi Arabian" resolve to, reaches the existing deterministic
    mutation path (session.passport_nationality), and the workflow advances
    exactly once (straight to currency_required)."""
    session = _bali_passport_country_required_session(runtime)

    session = _send(runtime, "Saudi Arabia", session)

    assert session.passport_nationality == "Saudi"
    assert session.stage == "currency_required"


def test_passport_country_still_accepts_previously_supported_variants(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Regression guard: the alias-dict addition must not disturb the
    forms that already worked (Phase 3C)."""
    assert resolve_nationality("Saudi") == "Saudi"
    assert resolve_nationality("Saudi Arabian") == "Saudi"
    assert resolve_nationality("KSA") == "Saudi"


def test_passport_country_accepts_a_passport_framed_answer(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test D (adversarial list): "my passport is from Saudi" is a natural
    answer to "which country issued your passport?" -- now resolved via the
    widened trigger-phrase regex, still through resolve_nationality only."""
    session = _bali_passport_country_required_session(runtime)

    session = _send(runtime, "my passport is from Saudi", session)

    assert session.passport_nationality == "Saudi"
    assert session.stage == "currency_required"


@pytest.mark.parametrize(
    "text",
    [
        "Saudi?",  # trailing "?" reads as a question, not a stated answer
        "Saudi trip",  # a trip reference, not a passport-country statement
        "Is Saudi included?",  # a question
        "I am traveling to Saudi",  # a travel-plan statement, not "i am <nationality>"
        "Saudi Arabia please",  # extra trailing word -- no resolver in this
        # file tolerates trailing filler words (same "Task 3.1" whole-answer
        # convention _strict_flight_option_from_answer/_compact_intent use)
    ],
)
def test_extract_nationality_hint_rejects_adversarial_near_miss_phrasing(text: str) -> None:
    """Step 14's adversarial examples: superficially country-word-shaped
    text that is not actually a stated passport-country answer must still
    resolve to "" -- confirms the new trigger phrase did not widen matching
    into guessing."""
    assert ToolCallingSessionRuntime._extract_nationality_hint(text) == ""


@pytest.mark.parametrize(
    "text",
    [
        "How much is the trip?",
        "Can I change my dates?",
        "Is the hotel included?",
        "Tell me about cancellation.",
        "Is this trip family friendly?",
    ],
)
def test_extract_nationality_hint_rejects_unrelated_questions_with_no_country_word(text: str) -> None:
    """Step 14's unrelated-message set -- none of these contain a country
    word at all, so they were already safely rejected before this phase;
    pinned here so the widened trigger-phrase regex is proven not to have
    created a new false-positive path for ordinary side questions."""
    assert ToolCallingSessionRuntime._extract_nationality_hint(text) == ""


def test_side_question_with_a_country_word_is_not_captured_as_passport_country(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Tests O/P/Q: "Is Saudi Arabia hot in November?" contains the exact
    new alias ("Saudi Arabia") but is a question about the destination, not
    a stated passport-country answer -- the whole-text resolver only
    matches when the ENTIRE message is the country name, so this remains
    unresolved and must still reach the existing Phase 3 conversational
    path, not be silently captured."""
    session = _bali_passport_country_required_session(runtime)
    before = _state_snapshot(session)
    agent = _install_classifier_agent_for(
        runtime,
        [
            _classification_response("side_question", 0.85),
            text_response("It's warm in November! Which country issued your passport?"),
        ],
    )

    session = _send(runtime, "Is Saudi Arabia hot in November?", session)

    assert agent.classify_off_script_turn.call_count == 1
    assert session.passport_nationality == ""
    assert session.stage == "passport_country_required"
    after = _state_snapshot(session)
    after.pop("tools_used"); before.pop("tools_used")
    assert after == before


def test_rejected_variant_then_valid_deterministic_answer_still_works(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Test R: after a near-miss that correctly does not resolve ("Saudi
    Arabia please"), the next turn's clean answer must still be captured
    normally -- no lingering rejection state carried across turns."""
    session = _bali_passport_country_required_session(runtime)

    session = _send(runtime, "Saudi Arabia please", session)
    assert session.passport_nationality == ""
    assert session.stage == "passport_country_required"

    session = _send(runtime, "Saudi Arabia", session)
    assert session.passport_nationality == "Saudi"
    assert session.stage == "currency_required"
