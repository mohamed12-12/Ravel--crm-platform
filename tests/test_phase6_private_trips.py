from __future__ import annotations

import os
import shutil
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import Mock

from services.ai_agent.ai_agent_app.agent.session_flow import SessionState
from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
from tests.test_booking_ledger import BookingLedgerTests as _BookingLedgerTests
from tests.test_golden_transcript_regressions import NEW_TRAVELER_WRITE, _write_results_by_action
from tests.test_payment_write_hardening import _create_temp_app
from tests.test_phase12_booking_state import _send, runtime as runtime

_BookingLedgerTests.__test__ = False


def test_create_private_trip_request_does_not_raise_on_a_real_schema() -> None:
    """Pins a live-transcript bug: every private-trip save failed after the
    budget question with a silent write failure (later surfaced as an
    IntegrityError once logging was added). Root cause: the raw sqlite3
    INSERT in UnifiedCRMService.create_private_trip_request omitted
    `deposit_is_refundable`, which the SQLAlchemy model
    (private_trip_request.py) declares NOT NULL with only a Python-side ORM
    default, not a server_default -- so any DB created from the real app
    schema (as production/demo is) rejected the insert outright.
    """
    tmpdir = Path(".tmp-test-workdirs") / f"prt-{uuid.uuid4().hex}"
    tmpdir.mkdir(parents=True, exist_ok=True)
    db_path = tmpdir / "app.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.resolve().as_posix()}"
    os.environ["RAHMA_SYSTEM_DB_PATH"] = str(db_path)
    os.environ["CRM_AUTH_ENABLED"] = "false"
    try:
        app = _create_temp_app()
        from app.extensions import db
        from app.models.traveler import Traveler
        from services.crm.system_services import UnifiedCRMService

        with app.app_context():
            db.drop_all()
            db.create_all()
            db.session.add(Traveler(
                traveler_id="TR00587",
                full_name="Mazen Test Traveler",
                integrated_whatsapp="20:1225456855",
                normalized_whatsapp="+201225456855",
                phone_lookup_key="20:1225456855",
            ))
            db.session.commit()

            service = UnifiedCRMService()
            result = service.create_private_trip_request(
                traveler_id="TR00587",
                lead_id="LD00003",
                service_type="full_package",
                trip_scope="Local",
                destination="Sharm",
                dates_flexible=True,
                party_size=5,
                budget_amount=500.0,
                budget_currency="USD",
                session_id="test-session-1",
            )
            assert str(result.get("request_id") or "").startswith("PRT-")

            row = db.session.execute(
                db.text("SELECT deposit_is_refundable FROM private_trip_requests WHERE request_id = :rid"),
                {"rid": result["request_id"]},
            ).fetchone()
            assert row is not None
            assert row[0] in (0, False)
    finally:
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("RAHMA_SYSTEM_DB_PATH", None)
        os.environ.pop("CRM_AUTH_ENABLED", None)
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_non_refundable_private_deposit_is_excluded_from_refund_allowance() -> None:
    case = _BookingLedgerTests(methodName="runTest")
    case.setUp()
    try:
        with case.app.app_context():
            booking = case.TripBooking(
                booking_id="B-PRV-1",
                trip_id="TRIP-1000",
                trip_name="Ledger Trip",
                traveler_id="TR100",
                traveler_name="Ledger Traveler",
                room_type="Single",
                currency="EGP",
                group_size=1,
                booking_status="Payment Pending",
                payment_status="Deposit Paid",
            )
            case.db.session.add(booking)
            case.db.session.add(case.BookingTransaction(
                booking_id="B-PRV-1",
                traveler_id="TR100",
                entry_type="payment",
                amount=300.0,
                currency="EGP",
                occurred_on=date.today(),
                source="private_trip_deposit",
                is_non_refundable=True,
            ))
            case.db.session.commit()

            allowance = case.refund_limits.refund_allowance_for_booking(
                booking,
                case.db.session.get(case.Trip, "TRIP-1000"),
            )

            assert allowance.amount_paid == 0.0
            assert allowance.maximum_refund == 0.0
    finally:
        case.tearDown()


def _private_verified_session(rt: ToolCallingSessionRuntime) -> SessionState:
    session = rt.create_session()
    session.raw_phone = "01112223333"
    session.country_code = "20"
    session.customer_name = "Mona Ali"
    session.preview = {
        "traveler": {"traveler_id": "TR100", "full_name": "Mona Ali", "status": "Active"},
        "workflow": {
            "identity_verified": True,
            "verified_traveler": {"traveler_id": "TR100", "full_name": "Mona Ali", "status": "Active"},
            "verified_status": "Active",
        },
        "trip_result": {"open_trips": [], "date_tbd_trips": []},
        "collection_state": {},
    }
    rt._read_only_tools.identity = {"traveler_id": "TR100", "full_name": "Mona Ali", "status": "Active"}
    session.stage = "traveler_verified"
    return session


def test_agent_private_trip_intake_saves_request_then_handoff(runtime: ToolCallingSessionRuntime) -> None:
    def execute_side_effect(*, action, payload, session_context):
        if action == "create_private_trip_request":
            return {
                "result_id": "PRT-000001",
                "executed": True,
                "write_result": {"private_trip_request": {"request_id": "PRT-000001"}},
                "write_result_contract": {
                    "status": "created",
                    "executed": True,
                    "reused": False,
                    "record_type": "private_trip_request",
                    "record_id": "PRT-000001",
                },
                "session_update": {"private_trip_request_id": "PRT-000001"},
            }
        if action == "create_handoff":
            return {
                "result_id": "H-00000001",
                "executed": True,
                "write_result": {"handoff_case": {"handoff_id": "H-00000001"}},
                "write_result_contract": {
                    "status": "created",
                    "executed": True,
                    "reused": False,
                    "record_type": "handoff",
                    "record_id": "H-00000001",
                },
                "session_update": {"handoff_state": "handed_off"},
            }
        raise AssertionError(action)

    runtime._write_executor = Mock()
    runtime._write_executor.execute.side_effect = execute_side_effect
    session = _private_verified_session(runtime)

    session = _send(runtime, "I want a private trip just for us", session)
    assert session.private_trip_active is True
    assert session.stage == "trip_type_required"

    for answer, expected_stage in (
        ("Local", "private_service_type_required"),
        ("3", "private_destination_required"),
        ("Siwa", "private_dates_required"),
        ("flexible dates", "private_party_size_required"),
        ("4 travelers", "private_budget_required"),
    ):
        session = _send(runtime, answer, session)
        assert session.stage == expected_stage

    session = _send(runtime, "around 50000 EGP", session)

    assert session.private_trip_request_id == "PRT-000001"
    # Escalated rather than the generic post_booking_support ("Done"): the
    # request plus its consultation handoff are with a human from here on.
    assert session.stage == "private_request_escalated"
    assert session.messages[-1]["role"] == "assistant"
    actions = [call.kwargs["action"] for call in runtime._write_executor.execute.call_args_list]
    assert actions == ["create_private_trip_request", "create_handoff"]

    # The closing line must confirm THIS request, not report it as something
    # that already existed -- see the escalation-message tests below.
    closing = session.messages[-1]["text"]
    assert "PRT-000001" in closing
    assert "already with" not in closing.casefold()


# ---------------------------------------------------------------------------
# Live transcript bug (2026-08-20): an all-Arabic private-trip intake ended
# with "طلبك موجود بالفعل مع Operations Team ... للمراجعة" -- the
# ALREADY-under-review line -- for a request that had been created seconds
# earlier. _execute_private_trip_request saves the request and then raises its
# own consultation handoff, so by the time the created-confirmation was
# composed _has_active_handoff() was already True, and
# _safe_response_fallback's blanket "any non-handoff message becomes the
# already-under-review line" clause replaced the confirmation wholesale.
#
# The customer asked to be told the request was SENT to the team and that they
# will be contacted, then for the conversation to close as an escalation.
# ---------------------------------------------------------------------------
def _run_private_intake_to_completion(rt: ToolCallingSessionRuntime, *, arabic: bool) -> SessionState:
    def execute_side_effect(*, action, payload, session_context):
        if action == "create_private_trip_request":
            return {
                "result_id": "PRT-000002",
                "executed": True,
                "write_result": {"private_trip_request": {"request_id": "PRT-000002"}},
                "write_result_contract": {
                    "status": "created",
                    "executed": True,
                    "reused": False,
                    "record_type": "private_trip_request",
                    "record_id": "PRT-000002",
                },
                "session_update": {"private_trip_request_id": "PRT-000002"},
            }
        if action == "create_handoff":
            return {
                "result_id": "H-00000002",
                "executed": True,
                "write_result": {"handoff_case": {"handoff_id": "H-00000002"}},
                "write_result_contract": {
                    "status": "created",
                    "executed": True,
                    "reused": False,
                    "record_type": "handoff",
                    "record_id": "H-00000002",
                },
                "session_update": {"handoff_state": "handed_off"},
            }
        raise AssertionError(action)

    rt._write_executor = Mock()
    rt._write_executor.execute.side_effect = execute_side_effect
    session = _private_verified_session(rt)
    if arabic:
        session.language = "ar"
        answers = ["عايز رحلة خاصة لينا", "محلية", "3", "الغردقة", "2026-09-20", "5 أفراد", "5000 جنيه مصري"]
    else:
        answers = ["I want a private trip just for us", "Local", "3", "Hurghada", "2026-09-20", "5 travelers", "5000 EGP"]
    for answer in answers:
        session = _send(rt, answer, session)
    return session


def test_completed_private_request_is_confirmed_as_sent_not_as_already_existing(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _run_private_intake_to_completion(runtime, arabic=False)

    assert session.private_trip_request_id == "PRT-000002"
    closing = session.messages[-1]["text"]
    assert "PRT-000002" in closing
    assert "has been sent to the Ravel team" in closing
    # The exact wording the customer reported, from
    # _handoff_already_under_review_message.
    assert "already with" not in closing.casefold()
    assert "for review" not in closing.casefold()


def test_completed_arabic_private_request_says_the_request_was_sent_to_the_team(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _run_private_intake_to_completion(runtime, arabic=True)

    closing = session.messages[-1]["text"]
    assert "PRT-000002" in closing
    assert "تم إرسال" in closing
    assert "هيتواصلوا معاك" in closing
    assert "موجود بالفعل" not in closing


def test_completed_private_request_closes_the_session_as_an_escalation(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _run_private_intake_to_completion(runtime, arabic=True)

    assert session.stage == "private_request_escalated"
    assert runtime._safe_status(session.stage) == "Sent to the Ravel team"

    # Escalated is terminal for the workflow, but the customer can still ask
    # where the request stands -- and asking must not silently downgrade the
    # session's status back to "Done".
    session = _send(runtime, "طلبي فين", session)
    assert session.stage == "private_request_escalated"
    assert "PRT-000002" in session.messages[-1]["text"]


# ---------------------------------------------------------------------------
# Live transcript bug: a private trip request kept failing to save for
# returning customers with "for the following reasons: unable to save right
# now" even though every collected field looked valid. Traced to
# _linked_ids() sourcing traveler_id/lead_id ONLY from session.preview/
# final_result/booking_result -- volatile nested dicts that a later write
# action's session_update can wholesale replace (write_tool_executor.py's
# various actions each build a differently-shaped final_result, some with
# no "traveler" key, some with no "lead_id" key) -- never from
# session.traveler_id/session.lead_id, the flat fields _build_context()
# latches once a real identity is resolved specifically so this kind of
# lookup never goes blank. Confirmed against a real production session dump
# where those flat fields were correct while _linked_ids() would still have
# returned "".
# ---------------------------------------------------------------------------
def test_linked_ids_falls_back_to_the_latched_session_fields_when_preview_is_empty() -> None:
    session = SessionState(id="s1", agent_mode="tool_calling")
    session.preview = {}
    session.final_result = {}
    session.booking_result = {}
    session.resumed_lead_id = ""
    session.traveler_id = "TR100"
    session.lead_id = "LD00002"

    linked = ToolCallingSessionRuntime._linked_ids(session)

    assert linked["traveler_id"] == "TR100"
    # Deliberately NOT "LD00002": session.lead_id gets latched onto the
    # customer's OLD open lead the moment identity lookup finds one,
    # regardless of whether they later chose to start a brand new private
    # request -- falling back to it here would silently re-attach a fresh
    # request onto the old lead the customer explicitly declined to reuse.
    assert linked["lead_id"] == ""


def test_linked_ids_still_prefers_the_fresher_nested_sources_over_the_latched_fields() -> None:
    session = SessionState(id="s2", agent_mode="tool_calling")
    session.preview = {"traveler": {"traveler_id": "TR200"}}
    session.final_result = {"lead_id": "LD00050"}
    session.traveler_id = "TR100"
    session.lead_id = "LD00002"

    linked = ToolCallingSessionRuntime._linked_ids(session)

    assert linked["traveler_id"] == "TR200"
    assert linked["lead_id"] == "LD00050"


# ---------------------------------------------------------------------------
# Live transcript bug: an Arabic-speaking customer got every greeting, side
# question, and clarification wrapper correctly in Arabic, but all six
# private-trip intake questions (trip scope, service type, destination,
# dates, party size, budget) always rendered in English -- both the
# first-ask copy (workflow_policy.py's assistant_message) and the
# unclear-answer retry copy (tool_calling_runtime.py's
# _natural_interruption_fallback) had no `arabic` branch at all, unlike
# every other required-step prompt in either file.
# ---------------------------------------------------------------------------
def test_private_trip_intake_questions_render_in_arabic_for_an_arabic_session(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _private_verified_session(runtime)
    session.language = "ar"

    session = _send(runtime, "عايز رحلة خاصة", session)
    assert session.stage == "trip_type_required"
    assert "local inside Egypt" not in session.messages[-1]["text"]
    assert "محلية" in session.messages[-1]["text"]

    for answer, expected_stage, must_contain in (
        ("محلي", "private_service_type_required", "الخدمة"),
        ("3", "private_destination_required", "وجهة"),
        ("سيوة", "private_dates_required", "المواعيد"),
        ("مرنة", "private_party_size_required", "شخص"),
        ("4 مسافرين", "private_budget_required", "الميزانية"),
    ):
        session = _send(runtime, answer, session)
        assert session.stage == expected_stage, session.messages[-1]["text"]
        reply = session.messages[-1]["text"]
        assert must_contain in reply, reply
        assert not any(word in reply for word in ("What ", "Reply ", "How many"))


def test_private_trip_unclear_answer_retry_copy_renders_in_arabic(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """The bug above has a second, separate rendering path: a customer
    answer that fails to parse (not the first ask of a step) goes through
    _natural_interruption_fallback, not workflow_policy's assistant_message
    -- confirmed live by the "معلش، يمكن سؤالي ما كان واضح" wrapper still
    embedding English private-service-type copy even though the wrapper
    itself was in Arabic.
    """
    session = _private_verified_session(runtime)
    session.language = "ar"
    session = _send(runtime, "عايز رحلة خاصة", session)
    session = _send(runtime, "محلي", session)
    assert session.stage == "private_service_type_required"

    session = _send(runtime, "اتكلم عربي بقولك رقم 3", session)

    assert session.stage == "private_service_type_required"
    reply = session.messages[-1]["text"]
    assert "الخدمة" in reply, reply
    assert "What private service" not in reply


# ---------------------------------------------------------------------------
# Live-transcript bug: a brand-new traveler ("جاد ماجد العوينه") who opened
# with "رحله خاصه" (private trip) still ended up with a Lead created (LD00004)
# as a side effect of identity intake (name/nationality/birthday ->
# save_new_traveler_lead), even though private trip requests are their own
# CRM record (private_trip_requests, saved separately once the private-trip
# fields are collected) and must never also leave an unworked duplicate in
# the regular Leads pipeline.
# ---------------------------------------------------------------------------
def test_new_traveler_private_trip_intake_saves_traveler_only_no_lead(
    runtime: ToolCallingSessionRuntime,
) -> None:
    runtime._write_executor.execute.side_effect = _write_results_by_action(
        create_traveler=NEW_TRAVELER_WRITE,
        create_private_trip_request={
            "result_id": "PRT-000001",
            "executed": True,
            "write_result": {"private_trip_request": {"request_id": "PRT-000001"}},
            "write_result_contract": {
                "status": "created",
                "executed": True,
                "reused": False,
                "record_type": "private_trip_request",
                "record_id": "PRT-000001",
            },
            "session_update": {"private_trip_request_id": "PRT-000001"},
        },
        create_handoff={
            "result_id": "H-00000002",
            "executed": True,
            "write_result": {"handoff_case": {"handoff_id": "H-00000002"}},
            "write_result_contract": {
                "status": "created",
                "executed": True,
                "reused": False,
                "record_type": "handoff",
                "record_id": "H-00000002",
            },
            "session_update": {"handoff_state": "handed_off"},
        },
    )
    session = runtime.create_session()
    session = _send(runtime, "عايز رحلة خاصة", session)
    assert session.private_trip_active is True

    for text in ("01270482380", "Maged Samir Adly", "Egyptian", "28/4/2006"):
        session = _send(runtime, text, session)

    reply = session.messages[-1]["text"]
    assert session.new_traveler_lead_saved is True
    assert session.stage == "trip_type_required"
    assert "تم حفظ بياناتك" in reply
    assert "LD0" not in reply
    assert "طلبك برقم" not in reply

    actions_so_far = [call.kwargs["action"] for call in runtime._write_executor.execute.call_args_list]
    assert actions_so_far == ["create_traveler"]
    assert ToolCallingSessionRuntime._linked_ids(session)["lead_id"] == ""
    final_result = session.final_result if isinstance(session.final_result, dict) else {}
    assert not final_result.get("lead_id")

    for answer, expected_stage in (
        ("Local", "private_service_type_required"),
        ("3", "private_destination_required"),
        ("Siwa", "private_dates_required"),
        ("flexible dates", "private_party_size_required"),
        ("4 travelers", "private_budget_required"),
    ):
        session = _send(runtime, answer, session)
        assert session.stage == expected_stage

    session = _send(runtime, "around 50000 EGP", session)

    assert session.private_trip_request_id == "PRT-000001"
    actions = [call.kwargs["action"] for call in runtime._write_executor.execute.call_args_list]
    assert actions == ["create_traveler", "create_private_trip_request", "create_handoff"]
    assert "create_lead" not in actions


def test_private_trip_destination_clarification_question_is_not_captured_as_the_destination(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = _private_verified_session(runtime)
    session = _send(runtime, "I want a private trip", session)
    session = _send(runtime, "Local", session)
    session = _send(runtime, "3", session)
    assert session.stage == "private_destination_required"

    session = _send(runtime, "يعني ايه", session)

    assert session.stage == "private_destination_required"
    assert session.private_destination == ""

    session = _send(runtime, "Siwa", session)
    assert session.private_destination == "Siwa"
    assert session.stage == "private_dates_required"


# ---------------------------------------------------------------------------
# Live 2026-08-20 transcript: the agent ignored the customer's questions.
# ---------------------------------------------------------------------------


def test_plural_arabic_private_trip_phrasing_is_recognized() -> None:
    """_private_trip_intent's Arabic list was singular-only, so the very common
    plural ("رحلات خاصه") matched nothing and the customer never
    entered the private-trip flow at all."""

    assert ToolCallingSessionRuntime._private_trip_intent("عايز رحلات خاصه") is True
    assert ToolCallingSessionRuntime._private_trip_intent("عاوز رحلات خاصة") is True
    assert ToolCallingSessionRuntime._private_trip_intent("بتعملوا برامج خاصة؟") is True
    # Still not a private-trip request.
    assert ToolCallingSessionRuntime._private_trip_intent("عايز رحلة للغردقة") is False


def test_service_capability_question_is_answered_and_still_asks_for_the_name(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """The reported bug: mid name-collection the customer asked
    "هو انتم بتنظموا رحلات خاصه؟" and the ONLY thing they got back was
    the name question repeated word for word. The answer must come first and
    the pending required step must still be asked in the same message.
    """

    session = runtime.create_session()
    session = _send(runtime, "01270482380", session)
    assert session.stage == "traveler_not_found"

    session = _send(runtime, "هو انتم بتنظموا رحلات خاصه؟", session)

    reply = session.messages[-1]["text"]
    # It answered the actual question ...
    assert "أيوه" in reply
    assert "رحلات خاصة" in reply
    # ... and still asked for the pending field, in the same turn.
    assert "اسم" in reply
    # ... without advancing or capturing anything.
    assert session.stage == "traveler_not_found"
    assert session.customer_name == ""

    session = _send(runtime, "Maged Samir Adly", session)
    assert session.customer_name == "Maged Samir Adly"


def test_service_capability_question_does_not_flip_a_regular_session_into_private_mode(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Asking whether private trips exist is a question, not a request for one.
    Answering it must not silently abandon the customer's current flow."""

    session = runtime.create_session()
    session = _send(runtime, "01270482380", session)
    session = _send(runtime, "هو انتم بتنظموا رحلات خاصه؟", session)

    assert session.private_trip_active is False

    # An actual request still switches it on.
    session = _send(runtime, "طب عايز رحلة خاصة", session)
    assert session.private_trip_active is True


def test_request_status_question_after_a_private_request_names_the_request(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """"طلبي فين" matched none of _post_booking_status_intent's
    booking-shaped terms, so it fell through to a generic "I need one more
    detail" non-answer -- and even when it did match, the reply was built from
    booking fields a private-trip session never has.
    """

    def execute_side_effect(*, action, payload, session_context):
        if action == "create_private_trip_request":
            return {
                "result_id": "PRT-000001",
                "executed": True,
                "write_result": {"private_trip_request": {"request_id": "PRT-000001"}},
                "write_result_contract": {
                    "status": "created",
                    "executed": True,
                    "reused": False,
                    "record_type": "private_trip_request",
                    "record_id": "PRT-000001",
                },
                "session_update": {"private_trip_request_id": "PRT-000001"},
            }
        if action == "create_handoff":
            return {
                "result_id": "H-00000001",
                "executed": True,
                "write_result": {"handoff_case": {"handoff_id": "H-00000001"}},
                "write_result_contract": {
                    "status": "created",
                    "executed": True,
                    "reused": False,
                    "record_type": "handoff",
                    "record_id": "H-00000001",
                },
                "session_update": {"handoff_state": "handed_off"},
            }
        raise AssertionError(action)

    runtime._write_executor = Mock()
    runtime._write_executor.execute.side_effect = execute_side_effect
    session = _private_verified_session(runtime)
    session = _send(runtime, "I want a private trip just for us", session)
    for answer in ("Local", "3", "Siwa", "flexible dates", "4 travelers", "around 50000 EGP"):
        session = _send(runtime, answer, session)
    assert session.private_trip_request_id == "PRT-000001"

    session = _send(runtime, "where is my request", session)

    reply = session.messages[-1]["text"]
    assert "PRT-000001" in reply
    assert "your saved booking" not in reply
    assert "I need one more detail" not in reply


def test_a_safe_llm_answer_that_omits_the_pending_field_keeps_the_answer_and_appends_the_question(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """_run_conversational_llm_turn used to DISCARD an otherwise-safe reply
    outright whenever _candidate_is_relevant_to_required_step could not find a
    literal per-step token in it, falling back to the bare scripted re-ask --
    which is how a genuine answer to "do you organise private trips?" was
    replaced by nothing but the name question. The answer must survive, with
    the required question appended so the workflow is still carried forward.
    """

    from services.ai_agent.ai_agent_app.agent.gemini_agent import GeminiAgent
    from services.ai_agent.ai_agent_app.agent.tool_registry import build_tool_calling_registry
    from tests.test_phase2_gemini_tool_loop import LoopProviderStub, text_response

    answer = "أيوه، إحنا بننظم رحلات خاصة بالكامل حسب ما تحب."
    runtime._conversation_ai = GeminiAgent(
        settings=runtime.settings,
        provider=LoopProviderStub([text_response(answer)]),
        tool_registry=build_tool_calling_registry(),
    )

    session = runtime.create_session()
    session = _send(runtime, "01270482380", session)
    assert session.stage == "traveler_not_found"

    session = _send(runtime, "هو انتم بتنظموا رحلات خاصه؟", session)

    reply = session.messages[-1]["text"]
    assert answer in reply, reply
    assert "اسم" in reply, reply
    assert session.stage == "traveler_not_found"
    assert session.customer_name == ""
