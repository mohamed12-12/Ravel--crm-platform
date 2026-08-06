"""Read-back verification + retry-once for create_lead/create_traveler/create_booking_draft.

Pins the gap-analysis fix: a create call's own return value used to be trusted
as proof a Traveler/Lead/Booking row existed. In production this let a Lead get
created while no Traveler record and no draft Booking were ever created, and
the conversation still ended looking successful. Now every create is followed
by a DB read-back by id; an unverified create is retried exactly once, and if
it still does not verify, the write is reported as failed rather than success.
"""

from __future__ import annotations

import sqlite3
import unittest
from contextlib import contextmanager
from types import SimpleNamespace

from services.ai_agent.ai_agent_app.agent.read_only_tools import ReadOnlyCRMTools
from services.ai_agent.ai_agent_app.agent.write_tool_executor import GeminiWriteToolExecutor
from services.ai_agent.validation.validation_result import APPROVED, ValidationResult


class _AlwaysApproveValidator:
    """Bypasses business-rule validation so tests isolate the verify/retry mechanism."""

    def validate_action(self, *, action: str, payload: dict, session_context: dict) -> ValidationResult:
        return ValidationResult(action=action, decision=APPROVED, traveler_id=str(payload.get("traveler_id") or ""))


class _FakeService:
    """A controllable CRM service: each create call can raise, or return an id that
    is (or is not) actually written to the row, so tests can force every branch of
    the verify/retry mechanism."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.connection.row_factory = sqlite3.Row
        self.create_traveler_outcomes: list = []
        self.create_traveler_calls = 0
        self.upsert_lead_outcomes: list = []
        self.upsert_lead_calls = 0
        self.create_booking_draft_outcomes: list = []
        self.create_booking_draft_calls = 0

    @contextmanager
    def connect(self):
        yield self.connection

    def create_traveler(self, **kwargs):
        outcome = self.create_traveler_outcomes[self.create_traveler_calls]
        self.create_traveler_calls += 1
        if outcome == "raise":
            raise RuntimeError("transient CRM error")
        traveler_id, insert_row = outcome
        if insert_row:
            self.connection.execute(
                "INSERT INTO travelers (traveler_id, full_name, status) VALUES (?, ?, ?)",
                (traveler_id, kwargs.get("full_name", ""), "Active"),
            )
            self.connection.commit()
        return {"traveler_id": traveler_id, "full_name": kwargs.get("full_name", ""), "status": "Active"}

    def upsert_lead(self, **kwargs):
        outcome = self.upsert_lead_outcomes[self.upsert_lead_calls]
        self.upsert_lead_calls += 1
        if outcome == "raise":
            raise RuntimeError("transient lead write error")
        lead_id, insert_row = outcome
        if insert_row:
            self.connection.execute(
                "INSERT INTO leads (lead_id, customer_name, traveler_id, raw_phone) VALUES (?, ?, ?, ?)",
                (lead_id, kwargs.get("customer_name", ""), kwargs.get("traveler_id") or None, kwargs.get("raw_phone", "")),
            )
            self.connection.commit()
        return {"lead_id": lead_id, "lead_stage": kwargs.get("lead_stage", "New Lead")}

    def create_booking_draft(self, **kwargs):
        outcome = self.create_booking_draft_outcomes[self.create_booking_draft_calls]
        self.create_booking_draft_calls += 1
        if outcome == "raise":
            raise RuntimeError("transient booking write error")
        booking_id, insert_row = outcome
        if insert_row:
            self.connection.execute(
                "INSERT INTO trip_bookings (booking_id, trip_id, traveler_id, lead_id, booking_status) VALUES (?, ?, ?, ?, ?)",
                (booking_id, kwargs.get("trip_id", ""), kwargs.get("traveler_id") or None, kwargs.get("lead_id") or None, "Draft"),
            )
            self.connection.commit()
        return {"booking_id": booking_id, "booking_status": "Draft", "write_result_contract": {"executed": True}}


def _settings() -> SimpleNamespace:
    return SimpleNamespace(default_country_code="20", crm_access_mode="shared_service", crm_api_base_url="", crm_api_token="")


class _VerificationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(
            """
            CREATE TABLE travelers (traveler_id TEXT PRIMARY KEY, full_name TEXT, status TEXT);
            CREATE TABLE leads (
                lead_id TEXT PRIMARY KEY, customer_name TEXT, traveler_id TEXT,
                raw_phone TEXT, integrated_whatsapp TEXT, phone_lookup_key TEXT,
                created_at TEXT, lead_stage TEXT
            );
            CREATE TABLE trips (trip_id TEXT PRIMARY KEY, trip_name TEXT, type TEXT);
            CREATE TABLE trip_bookings (
                booking_id TEXT PRIMARY KEY, trip_id TEXT, traveler_id TEXT,
                lead_id TEXT, draft_created_at TEXT, booking_status TEXT
            );
            """
        )
        self.conn.commit()
        self.service = _FakeService(self.conn)
        read_only = ReadOnlyCRMTools(_settings(), service=self.service)
        self.executor = GeminiWriteToolExecutor(
            settings=_settings(),
            read_only_tools=read_only,
            action_validator=_AlwaysApproveValidator(),
        )

    def tearDown(self) -> None:
        self.conn.close()


class TestCreateTravelerVerification(_VerificationTestCase):
    def test_first_attempt_verifies_successfully(self) -> None:
        self.service.create_traveler_outcomes = [("TR00001", True)]
        result = self.executor.execute(
            action="create_traveler",
            payload={"customer_name": "Mona Ali", "raw_phone": "01112223333"},
            session_context={"session_id": "s1"},
        )
        self.assertEqual(result["result_id"], "TR00001")
        self.assertEqual(self.service.create_traveler_calls, 1)

    def test_unverified_first_attempt_retries_and_then_succeeds(self) -> None:
        # First call reports an id but the row never lands in CRM; the retry
        # actually writes it. The customer must see a normal success, not an error.
        self.service.create_traveler_outcomes = [("TR00002", False), ("TR00002", True)]
        result = self.executor.execute(
            action="create_traveler",
            payload={"customer_name": "Sara Adel", "raw_phone": "01112223344"},
            session_context={"session_id": "s2"},
        )
        self.assertEqual(result["result_id"], "TR00002")
        self.assertEqual(self.service.create_traveler_calls, 2)
        self.assertTrue(result.get("executed", True))

    def test_unverified_twice_is_reported_as_failure_not_success(self) -> None:
        self.service.create_traveler_outcomes = [("TR00003", False), ("TR00003", False)]
        result = self.executor.execute(
            action="create_traveler",
            payload={"customer_name": "Omar Adel", "raw_phone": "01112223355"},
            session_context={"session_id": "s3"},
        )
        self.assertEqual(self.service.create_traveler_calls, 2)
        self.assertFalse(result.get("executed", True))
        # The unverified id must never leak into a "saved" message.
        self.assertNotIn("TR00003", str(result.get("assistant_message") or ""))

    def test_transient_exception_then_success_is_invisible_to_customer(self) -> None:
        self.service.create_traveler_outcomes = ["raise", ("TR00004", True)]
        result = self.executor.execute(
            action="create_traveler",
            payload={"customer_name": "Laila Adel", "raw_phone": "01112223366"},
            session_context={"session_id": "s4"},
        )
        self.assertEqual(result["result_id"], "TR00004")
        self.assertEqual(self.service.create_traveler_calls, 2)
        self.assertTrue(result.get("executed", True))

    def test_exception_on_both_attempts_is_reported_as_failure(self) -> None:
        self.service.create_traveler_outcomes = ["raise", "raise"]
        result = self.executor.execute(
            action="create_traveler",
            payload={"customer_name": "Hana Adel", "raw_phone": "01112223377"},
            session_context={"session_id": "s5"},
        )
        self.assertEqual(self.service.create_traveler_calls, 2)
        self.assertFalse(result.get("executed", True))


class TestCreateLeadVerification(_VerificationTestCase):
    def _seed_traveler(self, traveler_id: str = "TR00900") -> None:
        self.conn.execute(
            "INSERT INTO travelers (traveler_id, full_name, status) VALUES (?, ?, ?)",
            (traveler_id, "Mona Ali", "Active"),
        )
        self.conn.commit()

    def test_lead_verifies_successfully(self) -> None:
        self._seed_traveler()
        self.service.upsert_lead_outcomes = [("LD00001", True)]
        result = self.executor.execute(
            action="create_lead",
            payload={"customer_name": "Mona Ali", "raw_phone": "01112223333", "traveler_id": "TR00900"},
            session_context={"session_id": "s1"},
        )
        self.assertEqual(result["result_id"], "LD00001")
        self.assertEqual(self.service.upsert_lead_calls, 1)

    def test_unverified_lead_twice_is_reported_as_failure_not_success(self) -> None:
        self._seed_traveler()
        self.service.upsert_lead_outcomes = [("LD00002", False), ("LD00002", False)]
        result = self.executor.execute(
            action="create_lead",
            payload={"customer_name": "Mona Ali", "raw_phone": "01112223333", "traveler_id": "TR00900"},
            session_context={"session_id": "s2"},
        )
        self.assertEqual(self.service.upsert_lead_calls, 2)
        self.assertFalse(result.get("executed", True))
        self.assertNotIn("LD00002", str(result.get("assistant_message") or ""))


class TestCreateBookingDraftVerification(_VerificationTestCase):
    def _seed_trip_and_lead(self) -> None:
        self.conn.execute("INSERT INTO trips (trip_id, trip_name, type) VALUES (?, ?, ?)", ("RT-LOC-1", "Siwa Escape", "local"))
        self.conn.execute(
            "INSERT INTO leads (lead_id, customer_name, traveler_id, raw_phone) VALUES (?, ?, ?, ?)",
            ("LD00900", "Mona Ali", "TR00900", "01112223333"),
        )
        self.conn.execute("INSERT INTO travelers (traveler_id, full_name, status) VALUES (?, ?, ?)", ("TR00900", "Mona Ali", "Active"))
        self.conn.commit()

    def test_booking_draft_verifies_successfully(self) -> None:
        self._seed_trip_and_lead()
        self.service.create_booking_draft_outcomes = [("BK00001", True)]
        result = self.executor.execute(
            action="create_booking_draft",
            payload={
                "trip_id": "RT-LOC-1",
                "traveler_id": "TR00900",
                "lead_id": "LD00900",
                "room_type": "Double",
                "currency": "EGP",
            },
            session_context={"session_id": "s1", "booking_confirmed": True},
        )
        self.assertEqual(result["result_id"], "BK00001")
        self.assertEqual(self.service.create_booking_draft_calls, 1)

    def test_unverified_booking_twice_is_reported_as_failure_not_success(self) -> None:
        self._seed_trip_and_lead()
        self.service.create_booking_draft_outcomes = [("BK00002", False), ("BK00002", False)]
        result = self.executor.execute(
            action="create_booking_draft",
            payload={
                "trip_id": "RT-LOC-1",
                "traveler_id": "TR00900",
                "lead_id": "LD00900",
                "room_type": "Double",
                "currency": "EGP",
            },
            session_context={"session_id": "s2", "booking_confirmed": True},
        )
        self.assertEqual(self.service.create_booking_draft_calls, 2)
        self.assertFalse(result.get("executed", True))
        self.assertNotIn("BK00002", str(result.get("assistant_message") or ""))


if __name__ == "__main__":
    unittest.main()
