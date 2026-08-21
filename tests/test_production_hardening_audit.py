"""Regression tests from the 2026-08-21 production-hardening audit.

Every case here was found by driving the real runtime through adversarial
conversations rather than by reading tests, and each one reproduced a defect
that shipped green. Grouped by the failure, not by the function.
"""
from __future__ import annotations

import pytest

from services.ai_agent.ai_agent_app.agent.privacy_policy import AgentPrivacyPolicy
from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
from tests.test_phase12_booking_state import _send, runtime as runtime  # noqa: F401


NEW_PHONE = "01270482380"

_RECORD_IDS = {
    "create_lead": ("LD00001", "lead"),
    "update_lead_stage": ("LD00001", "lead"),
    "create_traveler": ("TR00001", "traveler"),
    "create_private_trip_request": ("PRT-000001", "private_trip_request"),
    "create_handoff": ("H-00000001", "handoff"),
    "create_booking_draft": ("BK000001", "booking"),
}


def _write_result(*, action, payload, session_context):
    """An honest write executor: a real contract with a real id for every
    action, so a success claim is authorized and the walk actually completes.
    The bare Mock the shared fixture installs returns a MagicMock with no
    traveler id, which strands identity intake at traveler_not_found."""

    result_id, record_type = _RECORD_IDS.get(action, (f"{action.upper()}-1", "write"))
    result = {
        "result_id": result_id,
        "executed": True,
        "write_result": {record_type: {f"{record_type}_id": result_id}},
        "write_result_contract": {
            "status": "created",
            "executed": True,
            "reused": False,
            "record_type": record_type,
            "record_id": result_id,
        },
    }
    if action == "create_traveler":
        result["traveler"] = {
            "traveler_id": result_id,
            "full_name": payload.get("full_name") or payload.get("customer_name") or "",
            "status": "Active",
        }
    if action == "create_lead":
        result["write_result"] = {"lead_update": {"lead_id": result_id}}
        result["session_update"] = {"lead_status": "New Lead"}
    if action == "create_private_trip_request":
        result["session_update"] = {"private_trip_request_id": result_id}
    if action == "create_handoff":
        result["session_update"] = {"handoff_state": "handed_off"}
    return result


def _at_name(rt):
    rt._write_executor.execute.side_effect = _write_result
    return _send(rt, NEW_PHONE, rt.create_session())


def _at_nationality(rt):
    return _send(rt, "Mohamed Ashraf Safwat", _at_name(rt))


def _at_birthday(rt):
    return _send(rt, "مصري", _at_nationality(rt))


def _after_identity(rt):
    return _send(rt, "1998-01-01", _at_birthday(rt))


def _private_intake(rt, *, upto: str):
    """Walk the private intake as far as `upto`."""
    session = _send(rt, "محلية", _send(rt, "عايز رحلة خاصة", _after_identity(rt)))
    if upto == "service_type":
        return session
    session = _send(rt, "3", session)
    if upto == "destination":
        return session
    session = _send(rt, "الغردقة", session)
    if upto == "dates":
        return session
    session = _send(rt, "2026-09-28", session)
    if upto == "party_size":
        return session
    session = _send(rt, "5", session)
    return session


# ---------------------------------------------------------------------------
# 1. A date is not another traveler's phone number.
#
# _phone_candidates' separator class accepts "-" and "." because real numbers
# are written "010-1234-5678", so "2026-09-28" matched -- and any date
# differing from the session's number earned the cross-traveler privacy
# refusal. Including the exact ISO format the agent itself asks for.
# ---------------------------------------------------------------------------

_BOUND_CONTEXT = {
    "customer_name": "Mohamed Ashraf Safwat",
    "raw_phone": "01270482380",
    "pending_raw_phone": "01270482380",
    "traveler_id": "TR00001",
    "workflow": {"identity_verified": True, "verified_traveler": {"traveler_id": "TR00001"}},
}


@pytest.mark.parametrize(
    "text",
    [
        "2026-09-28",
        "28-09-2026",
        "28.09.2026",
        "عايز اسافر 2026-10-05",
        "لا قصدي 2026-10-05",
        "من 2026-09-28 لـ 2026-10-05",
        "1998-01-01",
    ],
)
def test_a_date_is_never_treated_as_another_travelers_phone(text: str) -> None:
    assert AgentPrivacyPolicy.evaluate_user_message(text, _BOUND_CONTEXT) is None


def test_a_real_other_phone_is_still_blocked() -> None:
    assert AgentPrivacyPolicy.evaluate_user_message("01554158741", _BOUND_CONTEXT) is not None


def test_a_date_mid_conversation_gets_a_real_reply(runtime: ToolCallingSessionRuntime) -> None:
    session = _send(runtime, "محلية", _after_identity(runtime))
    session = _send(runtime, "عايز اسافر 2026-10-05", session)
    assert "مشاركة بيانات" not in session.messages[-1]["text"]


# ---------------------------------------------------------------------------
# 2. A side question is not the private trip's destination.
#
# private_destination is the only free-text field in the private intake and it
# accepted ANY message the narrow non-value allowlist did not recognise, so
# every question asked at that step became the destination and the intake
# advanced. Operations received requests for "طب الأسعار كام؟".
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "question",
    [
        "طب الأسعار كام؟",
        "في تقسيط؟",
        "مين هيتواصل معايا؟",
        "مقرّكم فين؟",
        "طلبي هيروح لمين؟",
        "How much does it cost?",
        "Who will contact me?",
        "el price kam?",
        "meen hyklmny?",
        "عايز اعرف الاسعار الاول",
    ],
)
def test_a_question_is_never_captured_as_the_private_destination(
    runtime: ToolCallingSessionRuntime, question: str
) -> None:
    session = _private_intake(runtime, upto="destination")
    assert session.stage == "private_destination_required"

    session = _send(runtime, question, session)

    assert session.private_destination == ""
    assert session.stage == "private_destination_required"


def test_a_real_destination_is_still_captured(runtime: ToolCallingSessionRuntime) -> None:
    session = _private_intake(runtime, upto="destination")
    session = _send(runtime, "شرم الشيخ", session)
    assert session.private_destination == "شرم الشيخ"
    assert session.stage == "private_dates_required"


# ---------------------------------------------------------------------------
# 3. A capability question is not a trip-type decision.
#
# "بتعملوا رحلات بره مصر؟" asks WHETHER Ravel runs trips abroad.
# TRIP_TYPE_TERMS matched "بره مصر" inside it, so the question committed the
# trip type -- and mid private intake that went through
# _apply_trip_type_change -> _clear_private_trip_downstream_state and wiped the
# destination, dates, party size and budget already collected.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("question", ["بتعملوا رحلات بره مصر؟", "Do you offer international trips?"])
def test_a_capability_question_does_not_commit_a_trip_type(
    runtime: ToolCallingSessionRuntime, question: str
) -> None:
    session = _after_identity(runtime)
    assert session.trip_type == ""

    session = _send(runtime, question, session)

    assert session.trip_type == ""


def test_a_capability_question_never_wipes_a_private_intake(runtime: ToolCallingSessionRuntime) -> None:
    session = _private_intake(runtime, upto="budget")
    assert session.stage == "private_budget_required"

    session = _send(runtime, "بتعملوا رحلات بره مصر؟", session)

    assert session.trip_type == "local"
    assert session.private_service_type == "full_package"
    assert session.private_destination == "الغردقة"
    assert session.private_start_date_pref == "2026-09-28"
    assert session.private_party_size == 5
    assert session.stage == "private_budget_required"


def test_an_explicit_scope_correction_still_applies(runtime: ToolCallingSessionRuntime) -> None:
    """The guard must not trap a customer who really does want to switch."""

    session = _private_intake(runtime, upto="destination")
    session = _send(runtime, "لا خليها دولية", session)
    assert session.trip_type == "international"


# ---------------------------------------------------------------------------
# 4. A Franco-Arabic capability question is a question, not private intent.
# ---------------------------------------------------------------------------

def test_a_franco_capability_question_does_not_switch_to_private_mode(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _at_name(runtime)
    session = _send(runtime, "bt3mlo private trips?", session)
    assert session.private_trip_active is False
    assert "Yes, we organize" in session.messages[-1]["text"]


def test_real_franco_private_intent_still_works(runtime: ToolCallingSessionRuntime) -> None:
    session = _send(runtime, "عايز private trip", _after_identity(runtime))
    assert session.private_trip_active is True


# ---------------------------------------------------------------------------
# 5. "لا، اسمي ..." -- a correction written with an Arabic comma.
#
# _normalize_trip_reference keeps punctuation in the Arabic block as literal
# characters, so the "لا اسمي" prefix never matched "لا، اسمي ..." and the
# correction was silently dropped.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("لا، اسمي محمد أحمد سيد", "محمد أحمد سيد"),
        ("لا، انا اسمي محمد أحمد سيد", "محمد أحمد سيد"),
        ("اسمي: محمد أحمد سيد", "محمد أحمد سيد"),
        ("my name is, Khaled Gad Maged", "Khaled Gad Maged"),
    ],
)
def test_a_punctuated_name_declaration_is_still_read(text: str, expected: str) -> None:
    assert ToolCallingSessionRuntime._declared_full_name(text) == expected


def test_a_name_correction_applies_at_a_later_step(runtime: ToolCallingSessionRuntime) -> None:
    session = _at_nationality(runtime)
    assert session.customer_name == "Mohamed Ashraf Safwat"

    session = _send(runtime, "لا، اسمي محمد أحمد سيد", session)

    assert session.customer_name == "محمد أحمد سيد"
    assert session.stage == "nationality_required"


# ---------------------------------------------------------------------------
# 6. Every phrasing of "where is my request" names the real request.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "question",
    ["طلبي فين", "فين طلبي", "رقم طلبي ايه؟", "هل الطلب اتبعت؟", "الطلب وصل؟", "my request number?"],
)
def test_request_status_questions_name_the_real_request(
    runtime: ToolCallingSessionRuntime, question: str
) -> None:
    session = _send(runtime, "5000 جنيه", _private_intake(runtime, upto="budget"))
    request_id = session.private_trip_request_id
    assert request_id

    session = _send(runtime, question, session)

    assert request_id in session.messages[-1]["text"]


# ---------------------------------------------------------------------------
# 7. Questions answerable from configuration get an answer, not the pending
#    workflow question repeated back verbatim.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "question",
    ["مين هيتواصل معايا؟", "طلبي هيروح لمين؟", "Who will contact me?", "طب الأسعار كام؟", "في تقسيط؟", "How much does it cost?"],
)
def test_general_policy_questions_are_answered_and_the_step_is_re_asked(
    runtime: ToolCallingSessionRuntime, question: str
) -> None:
    session = _at_birthday(runtime)
    prompt = session.messages[-1]["text"]

    session = _send(runtime, question, session)
    reply = session.messages[-1]["text"]

    assert reply.strip() != prompt.strip()
    assert session.stage == "birthday_required"


def test_a_pricing_question_never_quotes_a_number(runtime: ToolCallingSessionRuntime) -> None:
    """The honest answer is how pricing is decided, never an invented figure."""

    session = _send(runtime, "طب الأسعار كام؟", _at_birthday(runtime))
    reply = session.messages[-1]["text"]
    assert "Operations Team" in reply or "فريق Ravel" in reply
    assert "جنيه" not in reply.replace("بالجنيه", "")


# ---------------------------------------------------------------------------
# 8. A provider outage must not take the turn down.
#
# The model turn is the only remote call in handle_message and it was
# unprotected: the error branch handled a provider that RETURNS an error, but a
# quota rejection or transport exception propagated out of handle_message, so a
# Gemini outage produced no reply at all and a webhook retry crashed again.
# ---------------------------------------------------------------------------

class _RaisingAgent:
    def rewrite_message(self, *args, **kwargs):
        raise RuntimeError("429 quota exceeded")

    def respond(self, *args, **kwargs):
        raise RuntimeError("429 quota exceeded")

    def classify_off_script_turn(self, *args, **kwargs):
        raise RuntimeError("429 quota exceeded")


@pytest.mark.parametrize(
    "text",
    ["هو انتوا بتنظموا رحلات خاصة؟", "طب الأسعار كام؟", "مين هيتواصل معايا؟", "عايز رحلة", "hello there"],
)
def test_a_provider_outage_still_answers_deterministically(
    runtime: ToolCallingSessionRuntime, text: str
) -> None:
    runtime._conversation_ai = _RaisingAgent()
    session = runtime.create_session()

    session = _send(runtime, text, session)

    reply = session.messages[-1]["text"]
    assert reply.strip()
    # Never a success claim, and never an internal error surfaced verbatim.
    for leak in ("429", "quota", "Traceback", "RuntimeError"):
        assert leak not in reply


def test_a_provider_outage_answers_a_capability_question_with_real_facts(
    runtime: ToolCallingSessionRuntime,
) -> None:
    runtime._conversation_ai = _RaisingAgent()
    session = _send(runtime, "هو انتوا بتنظموا رحلات خاصة؟", runtime.create_session())
    assert "بننظم" in session.messages[-1]["text"]


def test_a_provider_outage_does_not_lose_captured_values(runtime: ToolCallingSessionRuntime) -> None:
    session = _at_birthday(runtime)
    runtime._conversation_ai = _RaisingAgent()

    session = _send(runtime, "طب الأسعار كام؟", session)

    assert session.customer_name == "Mohamed Ashraf Safwat"
    assert session.nationality == "Egyptian"
    assert session.stage == "birthday_required"


# ---------------------------------------------------------------------------
# 9. A message that names no trip is not a trip reference.
#
# "عايز رحلة" left "عايز" as the only token surviving the filler words, so the
# whole sentence was searched as a trip NAME and the most natural opening
# message there is got "لم أجد رحلة مؤكدة بهذا الاسم في CRM. من فضلك أرسل اسم
# الرحلة أو رقمها كما هو مكتوب."
#
# Note the deliberate boundary: a message that DOES carry a candidate token
# ("عايزة رحلة حلوة") still gets the clarify-the-name reply, which
# test_golden_transcript_regressions pins as intended for an unmatched
# destination before identity.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["عايز رحلة", "محتاج رحلة", "I want a trip"])
def test_a_message_naming_no_trip_is_not_a_failed_trip_lookup(
    runtime: ToolCallingSessionRuntime, text: str
) -> None:
    session = _send(runtime, text, runtime.create_session())
    reply = session.messages[-1]["text"]
    assert "لم أجد رحلة" not in reply
    assert "could not find" not in reply.lower()


def test_a_verified_traveler_can_still_name_a_trip(runtime: ToolCallingSessionRuntime) -> None:
    session = _send(runtime, "محلية", _after_identity(runtime))
    session = _send(runtime, "Phase 12 Mixed Inventory Demo", session)
    assert session.selected_trip_id == "RT-LOC-MIXED-P12"


# ---------------------------------------------------------------------------
# 10. A date is not a head count.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["2026-09-28", "28/9/2026", "لا قصدي 2026-10-05", "28.09.2026"])
def test_a_date_is_never_captured_as_the_party_size(
    runtime: ToolCallingSessionRuntime, text: str
) -> None:
    session = _private_intake(runtime, upto="party_size")
    assert session.stage == "private_party_size_required"

    session = _send(runtime, text, session)

    assert session.private_party_size == 0
    assert session.stage == "private_party_size_required"


@pytest.mark.parametrize(("text", "expected"), [("5", 5), ("5 افراد", 5), ("احنا 12", 12), ("2 people on 28/9", 2)])
def test_a_real_party_size_is_still_captured(
    runtime: ToolCallingSessionRuntime, text: str, expected: int
) -> None:
    session = _private_intake(runtime, upto="party_size")
    session = _send(runtime, text, session)
    assert session.private_party_size == expected


# ---------------------------------------------------------------------------
# 11. Duplicate webhook delivery of a decisive message writes once.
# ---------------------------------------------------------------------------

def test_a_duplicated_birthday_does_not_save_a_second_lead(runtime: ToolCallingSessionRuntime) -> None:
    session = _at_birthday(runtime)
    session = _send(runtime, "1998-01-01", session)
    first = [call.kwargs["action"] for call in runtime._write_executor.execute.call_args_list]

    session = _send(runtime, "1998-01-01", session)
    second = [call.kwargs["action"] for call in runtime._write_executor.execute.call_args_list]

    assert second == first


def test_a_duplicated_budget_does_not_create_a_second_request(runtime: ToolCallingSessionRuntime) -> None:
    session = _send(runtime, "5000 جنيه", _private_intake(runtime, upto="budget"))
    request_id = session.private_trip_request_id
    first = [call.kwargs["action"] for call in runtime._write_executor.execute.call_args_list]

    session = _send(runtime, "5000 جنيه", session)

    second = [call.kwargs["action"] for call in runtime._write_executor.execute.call_args_list]
    assert second == first
    assert session.private_trip_request_id == request_id
