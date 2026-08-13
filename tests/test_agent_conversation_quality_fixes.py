"""Conversation-quality fixes found by auditing the live agent runtime.

Each test here corresponds to a case where the agent rejected, mis-stored, or
escalated a perfectly ordinary customer message. Grouped by the bug they lock
down so a regression names itself.
"""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
from services.ai_agent.ai_agent_app.agent.workflow_policy import ConversationWorkflowPolicy
from services.ai_agent.validation.validation_rules import normalize_trip_type
from tests.test_agent_conversation_reliability import (
    TRIPS,
    PassiveAgent,
    RecordingReadTools,
    _selected_trip_session,
    _verified_trip_search_session,
)
from tests.test_phase11_demo_features import _make_app_with_db


@pytest.fixture()
def runtime(tmp_path: Path):
    original_env = dict(os.environ)
    os.environ["AI_AGENT_MODE"] = "tool_calling"
    _client, app = _make_app_with_db(tmp_path / uuid.uuid4().hex)
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


# ---------------------------------------------------------------------------
# Asking the agent for help must not escalate to a human
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "can you help me choose?",
        "help me pick a trip",
        "can u help me decide",
        "Could you help me compare the two trips?",
        "عايز مساعدة في الاختيار",
        "تساعدني أختار رحلة",
        "ساعدني اختار",
    ],
)
def test_asking_the_agent_for_help_is_not_a_human_handoff_request(text: str) -> None:
    """The most natural opener in a sales conversation used to escalate straight
    out of the bot, because bare "help"/"مساعده" are handoff markers matched as
    substrings."""
    assert ToolCallingSessionRuntime._is_human_agent_request(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "I need support with my booking",
        "can someone help me please",
        "محتاج مساعدة",
        "عايز موظف",
        "talk to a human",
        "transfer me to a real agent",
        "عايز الدعم الفني",
    ],
)
def test_genuine_human_requests_still_reach_handoff(text: str) -> None:
    """The self-service override must not swallow real "get me a person" asks."""
    assert ToolCallingSessionRuntime._is_human_agent_request(text) is True


def test_a_customer_service_number_question_is_a_side_question_not_a_handoff() -> None:
    assert ToolCallingSessionRuntime._is_human_agent_request(
        "Is there a customer service number I can call?"
    ) is False


# ---------------------------------------------------------------------------
# Arabic number words 4-10 (only 1-3 existed)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("اربعة", 4),
        ("أربعة", 4),
        ("اربع", 4),
        ("خمسة", 5),
        ("ستة", 6),
        ("سبعة", 7),
        ("تمانية", 8),
        ("ثمانية", 8),
        ("تسعة", 9),
        ("عشرة", 10),
        ("اتنين", 2),
        ("تلاتة", 3),
    ],
)
def test_arabic_number_words_resolve(text: str, expected: int) -> None:
    assert ToolCallingSessionRuntime._number_from_text(text) == expected


# ---------------------------------------------------------------------------
# Mixed group written with a spaced connector waw
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    ["شباب و بنات", "شباب وبنات", "ولاد و بنات", "بنات و ولاد", "ذكور و اناث"],
)
def test_spaced_and_glued_mixed_group_are_both_detected(text: str) -> None:
    """Only the glued spelling was listed, so the spaced form fell through to the
    unconditional "بنات" check and the whole group was stored as girls-only."""
    assert ToolCallingSessionRuntime._has_mixed_group_hint(text) is True


@pytest.mark.parametrize("text", ["بنات بس", "كلهم شباب", "girls only"])
def test_single_gender_answers_are_not_flagged_as_mixed(text: str) -> None:
    assert ToolCallingSessionRuntime._has_mixed_group_hint(text) is False


def test_spaced_mixed_group_is_not_silently_stored_as_girls_only(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _selected_trip_session(runtime)
    assert session.stage == "traveler_gender_required"

    runtime._apply_required_step_capture(session, "شباب و بنات")

    assert session.room_group != "girls"


# ---------------------------------------------------------------------------
# Egyptian phrasing for trip type ("outside/inside Egypt")
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("برة مصر", "international"),
        ("بره", "international"),
        ("برا", "international"),
        ("خارج مصر", "international"),
        ("outside egypt", "international"),
        ("داخل مصر", "local"),
        ("جوه مصر", "local"),
        ("inside egypt", "local"),
        ("محلي", "local"),
        ("دولي", "international"),
    ],
)
def test_colloquial_trip_type_phrasing_resolves(text: str, expected: str) -> None:
    assert normalize_trip_type(text) == expected


# ---------------------------------------------------------------------------
# Currency step accepts the Arabic words it prints
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("بالدولار", "USD"),
        ("دولار", "USD"),
        ("الدولار", "USD"),
        ("دولارات", "USD"),
        ("بالجنيه", "EGP"),
        ("جنيه", "EGP"),
        ("جنيه مصري", "EGP"),
        ("USD", "USD"),
        ("EGP", "EGP"),
        ("1", "EGP"),
        ("2", "USD"),
    ],
)
def test_currency_step_accepts_arabic_and_prefixed_forms(
    runtime: ToolCallingSessionRuntime, text: str, expected: str
) -> None:
    """This step prints "جنيه مصري / دولار أمريكي" then used to reject the
    customer for echoing it: there was no Arabic USD term at all, and \\b never
    matches before an Arabic prefixed clitic ("بالجنيه")."""
    session = runtime.create_session()
    session.stage = "currency_required"

    captured = runtime._apply_required_step_capture(session, text)

    assert captured is True
    assert session.currency == expected


def test_currency_step_still_rejects_an_unrelated_answer(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = runtime.create_session()
    session.stage = "currency_required"

    assert runtime._apply_required_step_capture(session, "whatever is cheaper") is False
    assert session.currency == ""


# ---------------------------------------------------------------------------
# Room type accepts the Arabic words the prompt renders
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("دبل", "Double"),
        ("دابل", "Double"),
        ("ثنائية", "Double"),
        ("الثنائية", "Double"),
        ("مزدوجة", "Double"),
        ("double", "Double"),
    ],
)
def test_room_type_accepts_arabic_room_words(
    runtime: ToolCallingSessionRuntime, text: str, expected: str
) -> None:
    """The prompt renders options as "Double (ثنائية)" but the alias map was
    English-only, so echoing the printed Arabic was rejected."""
    trip = dict(TRIPS[1])
    trip["available_double"] = 4
    trip["available_single"] = 2
    trip["available_triple"] = 2
    session = _selected_trip_session(runtime, trip)
    session.stage = "room_type_required"
    session.room_type = ""

    captured = runtime._apply_required_step_capture(session, text)

    assert captured is True
    assert session.room_type == expected


# ---------------------------------------------------------------------------
# Gender-split capacity must not report 0 for a trip that isn't gender-split
# ---------------------------------------------------------------------------

def _policy() -> ConversationWorkflowPolicy:
    return ConversationWorkflowPolicy()


def test_ungendered_trip_falls_back_to_the_shared_room_pool() -> None:
    """UnifiedCRMService always emits boys_double/girls_double (default 0), so a
    trip that simply doesn't split doubles by gender looked sold out. The agent
    offered "Double", the customer picked it, and the next turn escalated to a
    human claiming 0 available while 6 were free."""
    trip = {"available_double": 6, "boys_double": 0, "girls_double": 0}

    assert _policy()._gender_split_tracked(trip, "double") is False
    assert _policy()._gendered_room_capacity(trip, "double", "boys") == 6
    assert _policy()._room_capacity(trip, "double", "boys") == 6


def test_a_genuinely_gender_split_trip_still_uses_its_split_numbers() -> None:
    """When the trip really does track a split, a sold-out side must stay blocked
    rather than falling back to the shared pool."""
    trip = {"available_double": 6, "boys_double": 0, "girls_double": 4}

    assert _policy()._gender_split_tracked(trip, "double") is True
    assert _policy()._gendered_room_capacity(trip, "double", "boys") == 0
    assert _policy()._gendered_room_capacity(trip, "double", "girls") == 4


def test_ungendered_trip_reports_no_mixed_capacity_issue() -> None:
    trip = {"available_double": 6, "boys_double": 0, "girls_double": 0}
    requirements = [
        {"room_type": "double", "room_group": "boys", "rooms": 1},
        {"room_type": "double", "room_group": "girls", "rooms": 1},
    ]

    assert _policy()._mixed_room_capacity_issue(trip, requirements) == ""


def test_split_trip_still_reports_a_real_shortfall() -> None:
    trip = {"available_double": 6, "boys_double": 0, "girls_double": 4}
    requirements = [{"room_type": "double", "room_group": "boys", "rooms": 2}]

    assert _policy()._mixed_room_capacity_issue(trip, requirements) != ""


def test_unknown_capacity_stays_unknown_and_does_not_block() -> None:
    assert _policy()._room_capacity({}, "double", "boys") is None


# ---------------------------------------------------------------------------
# Repeated unparsed answers must vary, then escalate -- not repeat forever
# ---------------------------------------------------------------------------

def _unclear_replies(rt: ToolCallingSessionRuntime, count: int) -> tuple[list[str], object]:
    session = _selected_trip_session(rt)
    replies = []
    for _ in range(count):
        session = rt.handle_message(session, "hmmmm not sure", gateway=None)
        replies.append(session.messages[-1]["text"])
    return replies, session


def test_second_unparsed_answer_is_reworded_not_byte_identical(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """_natural_interruption_fallback is a pure function of (step, reason), so the
    agent used to re-send the exact same sentence every turn with no signal that
    anything had changed."""
    replies, _session = _unclear_replies(runtime, 2)

    assert replies[0] != replies[1]


def test_third_unparsed_answer_escalates_to_a_human_handoff(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """The strict per-step capture only accepts a narrow set of answers, so a
    customer whose phrasing it does not know had no exit at all."""
    replies, session = _unclear_replies(runtime, 3)

    assert len({*replies}) == 3
    actions = [call.kwargs.get("action") for call in runtime._write_executor.execute.call_args_list]
    assert "create_handoff" in actions
    assert session.unclear_step_strikes == 0


def test_a_successful_answer_clears_the_unclear_streak(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _selected_trip_session(runtime)
    session = runtime.handle_message(session, "hmmmm not sure", gateway=None)
    assert session.unclear_step_strikes == 1

    session = runtime.handle_message(session, "boys", gateway=None)

    assert session.unclear_step_strikes == 0
    assert session.room_group == "boys"


def test_a_single_unparsed_answer_does_not_escalate(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """One fumbled answer is normal conversation -- it must not create a handoff."""
    _replies, _session = _unclear_replies(runtime, 1)

    actions = [call.kwargs.get("action") for call in runtime._write_executor.execute.call_args_list]
    assert "create_handoff" not in actions


# ---------------------------------------------------------------------------
# Real transcript: "محتاج الأاول اعرف الرحلات" / "قولي الرحلات المتاحه" must
# not be misread as a failed trip-name lookup, and must never hallucinate a
# destination before the traveler is verified
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "محتاج الأاول اعرف الرحلات",
        "عايز اعرف الرحلات",
        "طيب ايه الرحلات",
    ],
)
def test_general_trip_discovery_questions_are_recognized(text: str) -> None:
    """"I need to first know the trips" is a general discovery question, not a
    trip name -- it must not fall through to the "trip not found" reply."""
    assert ToolCallingSessionRuntime._is_trip_discovery_request(text) is True


def test_unverified_trip_discovery_question_gets_a_deterministic_phone_ask(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Before the traveler is verified, the agent has done zero real trip
    lookups -- asking about trips at this point must get a fixed, safe reply,
    not a free-form LLM turn that could ad-lib destinations. Reproduces the
    real bug: the LLM once echoed back "الغردقة"/"مطروح" (destinations the
    CUSTOMER had typed earlier) as if they were confirmed CRM inventory."""
    session = runtime.create_session()
    session = runtime.handle_message(session, "رحله للغردقه", session)
    session = runtime.handle_message(session, "رحله مطروح", session)

    session = runtime.handle_message(session, "محتاج الأاول اعرف الرحلات", session)

    reply = session.messages[-1]["text"]
    assert "لم أجد رحلة مؤكدة" not in reply
    assert "واتساب" in reply or "WhatsApp" in reply
    assert "الغردقة" not in reply
    assert "مطروح" not in reply


# ---------------------------------------------------------------------------
# "مفيش غير ديه؟" (is that the only one?) and exhausted trip search must
# create a visible handoff, not loop or promise a fake outbound notification
# ---------------------------------------------------------------------------

def test_empty_trip_search_creates_a_handoff_with_an_honest_message(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _verified_trip_search_session(runtime, trip_type="international")
    session.preview["trip_result"] = {"open_trips": [], "date_tbd_trips": []}

    session = runtime.handle_message(session, "طيب الدولي؟", session)

    reply = session.messages[-1]["text"]
    # No fabricated promise of a proactive WhatsApp notification -- no such
    # outbound pipeline exists anywhere in this codebase.
    assert "هبلغك" not in reply
    assert "notify you" not in reply.lower()
    actions = [call.kwargs.get("action") for call in runtime._write_executor.execute.call_args_list]
    assert "create_handoff" in actions


def test_asking_if_the_single_trip_is_the_only_one_escalates_instead_of_looping(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Reproduces "مفيش غير ديه؟" after the agent lists exactly one trip: this
    must be recognized as "the one option doesn't work for me" and create a
    handoff, not increment the unclear-answer strike counter and re-list the
    same trip with softer wording."""
    session = _verified_trip_search_session(runtime, trip_type="local")
    session.preview["trip_result"] = {"open_trips": [dict(TRIPS[1])], "date_tbd_trips": []}
    session.stage = "trip_selection_required"

    session = runtime.handle_message(session, "مفيش غير ديه؟", session)

    assert session.unclear_step_strikes == 0
    actions = [call.kwargs.get("action") for call in runtime._write_executor.execute.call_args_list]
    assert "create_handoff" in actions


def test_asking_if_that_is_all_with_multiple_trips_shown_gets_an_honest_answer_not_escalation(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """When there genuinely ARE other options, "is that all?" has a real
    answer and must not create a handoff or burn a strike."""
    session = _verified_trip_search_session(runtime, trip_type="local")
    session.preview["trip_result"] = {"open_trips": [dict(TRIPS[1]), dict(TRIPS[2])], "date_tbd_trips": []}
    session.stage = "trip_selection_required"

    session = runtime.handle_message(session, "مفيش غير ديه؟", session)

    assert session.unclear_step_strikes == 0
    actions = [call.kwargs.get("action") for call in runtime._write_executor.execute.call_args_list]
    assert "create_handoff" not in actions
    reply = session.messages[-1]["text"]
    assert "المتاحة حاليًا" in reply or "currently available" in reply
