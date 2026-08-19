from __future__ import annotations

from datetime import date
from unittest.mock import Mock

from services.ai_agent.ai_agent_app.agent.session_flow import SessionState
from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
from tests.test_booking_ledger import BookingLedgerTests as _BookingLedgerTests
from tests.test_phase12_booking_state import _send, runtime as runtime

_BookingLedgerTests.__test__ = False


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
