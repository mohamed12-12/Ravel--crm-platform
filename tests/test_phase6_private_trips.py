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
    assert session.stage == "post_booking_support"
    assert session.messages[-1]["role"] == "assistant"
    actions = [call.kwargs["action"] for call in runtime._write_executor.execute.call_args_list]
    assert actions == ["create_private_trip_request", "create_handoff"]


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
