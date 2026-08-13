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
        self.upsert_lead_received_kwargs: list = []
        self.create_booking_draft_outcomes: list = []
        self.create_booking_draft_calls = 0
        self.update_lead_stage_outcomes: list = []
        self.update_lead_stage_calls = 0

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
        self.upsert_lead_received_kwargs.append(kwargs)
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

    def resolve_identity(self, full_name, raw_phone, country_code="20"):
        from services.crm.system_services.unified_service import IdentityResolution

        row = self.connection.execute(
            "SELECT traveler_id, full_name, status FROM travelers WHERE raw_phone = ?",
            (raw_phone,),
        ).fetchone()
        if row is None:
            return IdentityResolution(
                match_status="not_found",
                handoff_required=False,
                handoff_reason="",
                traveler=None,
                lookup_phone={"raw_phone": raw_phone, "country_code": country_code},
                name_match_status="",
                actions=["collect_new_traveler_data"],
            )
        traveler = {"traveler_id": row["traveler_id"], "full_name": row["full_name"], "status": row["status"]}
        return IdentityResolution(
            match_status="single_match",
            handoff_required=False,
            handoff_reason="",
            traveler=traveler,
            lookup_phone={"raw_phone": raw_phone, "country_code": country_code},
            name_match_status="",
            actions=[],
        )

    def update_lead_stage(self, lead_id, *, requested_stage, **kwargs):
        outcome = self.update_lead_stage_outcomes[self.update_lead_stage_calls]
        self.update_lead_stage_calls += 1
        if outcome == "raise":
            raise RuntimeError("transient lead stage update error")
        stage, apply_update = outcome
        if apply_update:
            self.connection.execute("UPDATE leads SET lead_stage = ? WHERE lead_id = ?", (stage, lead_id))
            self.connection.commit()
        # The service reports the requested stage regardless of whether the row
        # actually changed -- this is what makes an unverified update dangerous.
        return {"lead_id": lead_id, "lead_stage": requested_stage}


def _settings() -> SimpleNamespace:
    return SimpleNamespace(default_country_code="20", crm_access_mode="shared_service", crm_api_base_url="", crm_api_token="")


class _VerificationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(
            """
            CREATE TABLE travelers (traveler_id TEXT PRIMARY KEY, full_name TEXT, status TEXT, raw_phone TEXT);
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


class TestCreateLeadMatchStatus(_VerificationTestCase):
    """A Lead's match_status drives the "Existing Traveler" vs "New Profile"
    badge in the Leads pipeline UI (leads/index.html). On the shared_service
    path (no record_agent_outcome), a brand-new customer with no prior
    Traveler record used to be stamped single_match/"Existing Traveler" just
    because a Traveler row got created for them in the same call -- these pin
    that a genuinely new customer reads not_found, and a real pre-existing
    match still reads single_match.
    """

    def test_brand_new_customer_with_no_prior_traveler_is_not_found(self) -> None:
        self.service.create_traveler_outcomes = [("TR01000", True)]
        self.service.upsert_lead_outcomes = [("LD01000", True)]
        result = self.executor.execute(
            action="create_lead",
            payload={"customer_name": "Maged Mousa", "raw_phone": "01199998888"},
            session_context={"session_id": "s-new"},
        )
        self.assertEqual(result["result_id"], "LD01000")
        self.assertEqual(self.service.upsert_lead_received_kwargs[-1]["match_status"], "not_found")
        # The traveler record still gets created and linked -- only the
        # match_status badge was wrong, not the traveler creation itself.
        lead_row = self.conn.execute(
            "SELECT traveler_id FROM leads WHERE lead_id = ?", ("LD01000",)
        ).fetchone()
        self.assertEqual(lead_row["traveler_id"], "TR01000")

    def test_genuinely_pre_existing_traveler_is_single_match(self) -> None:
        self.conn.execute(
            "INSERT INTO travelers (traveler_id, full_name, status, raw_phone) VALUES (?, ?, ?, ?)",
            ("TR00900", "Mona Ali", "Active", "01112223333"),
        )
        self.conn.commit()
        self.service.upsert_lead_outcomes = [("LD01001", True)]
        result = self.executor.execute(
            action="create_lead",
            payload={"customer_name": "Mona Ali", "raw_phone": "01112223333"},
            session_context={"session_id": "s-existing"},
        )
        self.assertEqual(result["result_id"], "LD01001")
        self.assertEqual(self.service.create_traveler_calls, 0)
        self.assertEqual(self.service.upsert_lead_received_kwargs[-1]["match_status"], "single_match")


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


class TestUpdateLeadStageVerification(_VerificationTestCase):
    """`update_lead_stage` narrated success straight from the service's own
    return value with no independent read-back at all -- unlike the create
    actions above, it wasn't even wrapped in the verify/retry mechanism.
    """

    def _seed_lead(self, lead_id: str = "LD00950", initial_stage: str = "New Lead") -> None:
        self.conn.execute(
            "INSERT INTO leads (lead_id, customer_name, traveler_id, raw_phone, lead_stage) VALUES (?, ?, ?, ?, ?)",
            (lead_id, "Mona Ali", "TR00900", "01112223333", initial_stage),
        )
        self.conn.commit()

    def test_stage_update_verifies_successfully(self) -> None:
        self._seed_lead()
        self.service.update_lead_stage_outcomes = [("Qualified", True)]
        result = self.executor.execute(
            action="update_lead_stage",
            payload={"lead_id": "LD00950", "requested_stage": "Qualified"},
            session_context={"session_id": "s1"},
        )
        self.assertEqual(result["result_id"], "LD00950")
        self.assertEqual(self.service.update_lead_stage_calls, 1)
        self.assertIn("Qualified", str(result.get("assistant_message") or ""))

    def test_unverified_update_retries_and_then_succeeds(self) -> None:
        self._seed_lead()
        self.service.update_lead_stage_outcomes = [("Qualified", False), ("Qualified", True)]
        result = self.executor.execute(
            action="update_lead_stage",
            payload={"lead_id": "LD00950", "requested_stage": "Qualified"},
            session_context={"session_id": "s2"},
        )
        self.assertEqual(result["result_id"], "LD00950")
        self.assertEqual(self.service.update_lead_stage_calls, 2)
        self.assertTrue(result.get("executed", True))

    def test_unverified_update_twice_is_reported_as_failure_not_success(self) -> None:
        self._seed_lead()
        self.service.update_lead_stage_outcomes = [("Qualified", False), ("Qualified", False)]
        result = self.executor.execute(
            action="update_lead_stage",
            payload={"lead_id": "LD00950", "requested_stage": "Qualified"},
            session_context={"session_id": "s3"},
        )
        self.assertEqual(self.service.update_lead_stage_calls, 2)
        self.assertFalse(result.get("executed", True))
        # The stage claim must never reach the customer if it was never verified.
        self.assertNotIn("moved to Qualified", str(result.get("assistant_message") or ""))


class _ApprovingValidator:
    @staticmethod
    def validate_action(*, action: str, payload: dict, session_context: dict) -> ValidationResult:
        return ValidationResult(action=action, decision=APPROVED, session_id=str(session_context.get("session_id") or ""))


class TestGuardianConsentDispatchByMode(unittest.TestCase):
    """`record_guardian_consent`/`record_lead_guardian_flag` used to call
    `self.service.set_guardian_consent(...)` directly. `self.service` is
    deliberately None under CRM_ACCESS_MODE=api (reads/writes go over HTTP in
    that mode), so every call raised AttributeError in production, was
    swallowed, and the caller marked consent "saved" anyway. These pin that
    the fix actually dispatches through the same api_client/service branch
    every other write action uses, in both modes, with a real read-back.
    """

    def test_api_mode_dispatches_through_api_client_and_verifies(self) -> None:
        class FakeApiClient:
            def __init__(self) -> None:
                self.write_calls: list[str] = []

            def read(self, action, payload):
                if action == "get_traveler_profile":
                    return {"traveler": {"traveler_id": "TR777", "guardian_name": "Parent X", "guardian_phone": "0100"}}
                if action == "lookup_lead":
                    return {"leads": [{"lead_id": "LD777", "requires_guardian_approval": True}]}
                return {}

            def write(self, action, payload, session_context):
                self.write_calls.append(action)
                return {"result_id": payload.get("traveler_id") or payload.get("lead_id")}

        settings = SimpleNamespace(default_country_code="20", crm_access_mode="api", crm_api_base_url="http://crm.test", crm_api_token="token")
        api_client = FakeApiClient()
        tools = ReadOnlyCRMTools(settings, api_client=api_client)
        executor = GeminiWriteToolExecutor(settings=settings, read_only_tools=tools, action_validator=_ApprovingValidator())
        self.assertIsNone(executor.service)  # confirms this test exercises the exact broken state

        result = executor.record_guardian_consent(
            traveler_id="TR777", is_minor=True, guardian_name="Parent X", guardian_phone="0100"
        )
        self.assertTrue(result.get("verified"))

        flag_result = executor.record_lead_guardian_flag(lead_id="LD777", requires_guardian_approval=True)
        self.assertTrue(flag_result.get("verified"))
        self.assertEqual(api_client.write_calls, ["set_guardian_consent", "flag_lead_guardian_approval"])

    def test_api_mode_verification_failure_retries_once_then_reports_unverified(self) -> None:
        class FakeApiClient:
            def __init__(self) -> None:
                self.write_calls = 0

            def read(self, action, payload):
                # The fields never actually land, no matter what the write claims.
                return {"traveler": {"traveler_id": "TR778", "guardian_name": "", "guardian_phone": ""}}

            def write(self, action, payload, session_context):
                self.write_calls += 1
                return {"result_id": payload.get("traveler_id")}

        settings = SimpleNamespace(default_country_code="20", crm_access_mode="api", crm_api_base_url="http://crm.test", crm_api_token="token")
        api_client = FakeApiClient()
        tools = ReadOnlyCRMTools(settings, api_client=api_client)
        executor = GeminiWriteToolExecutor(settings=settings, read_only_tools=tools, action_validator=_ApprovingValidator())

        result = executor.record_guardian_consent(
            traveler_id="TR778", is_minor=True, guardian_name="Parent Y", guardian_phone="0200"
        )
        self.assertFalse(result.get("verified"))
        self.assertEqual(api_client.write_calls, 2)  # performed, then retried exactly once

    def test_construction_raises_when_neither_service_nor_api_client_available(self) -> None:
        """Regression test for the exact original bug state: a write executor
        configured for CRM_ACCESS_MODE=api with no way to actually reach the
        CRM must refuse to start rather than let every handler that touches
        self.service fail one call at a time."""
        broken_tools = SimpleNamespace(service=None, api_client=None)
        with self.assertRaises(RuntimeError):
            GeminiWriteToolExecutor(
                settings=SimpleNamespace(default_country_code="20", crm_access_mode="api"),
                read_only_tools=broken_tools,
            )

    def test_construction_does_not_raise_for_a_read_only_double_outside_api_mode(self) -> None:
        """A test double with no write path at all is a normal, legitimate
        pattern for read-only/enforcement tests -- only CRM_ACCESS_MODE=api
        with no working write path is the broken state worth crashing on."""
        read_only_double = SimpleNamespace(service=None, api_client=None)
        executor = GeminiWriteToolExecutor(
            settings=SimpleNamespace(default_country_code="20"),
            read_only_tools=read_only_double,
            action_validator=_ApprovingValidator(),
        )
        self.assertIsNone(executor.service)


if __name__ == "__main__":
    unittest.main()
