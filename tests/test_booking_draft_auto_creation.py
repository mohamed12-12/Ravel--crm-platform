from __future__ import annotations

import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path

SYSTEM_ROOT = Path(__file__).resolve().parent.parent / "apps" / "api"
if str(SYSTEM_ROOT) not in sys.path:
    sys.path.insert(0, str(SYSTEM_ROOT))


def _create_temp_app():
    for module_name in list(sys.modules):
        if module_name == "app" or module_name.startswith("app."):
            sys.modules.pop(module_name, None)
    from app import create_app
    return create_app("development")


def _load_app_objects():
    from app.extensions import db
    from app.models.booking import TripBooking
    from app.models.booking_event import BookingEventTrail
    from app.models.lead import Lead
    from app.models.traveler import Traveler
    from app.models.trip import Trip
    from app.models.user import User
    from services.crm.system_services import UnifiedCRMService

    return db, TripBooking, BookingEventTrail, Lead, Traveler, Trip, User, UnifiedCRMService


class BookingDraftAutoCreationTests(unittest.TestCase):
    """A Lead reaching the Booking Draft stage -- from the manual CRM UI, the
    AI agent's Postgres write path, or the AI agent's SQLite write path --
    must auto-create a Draft Booking exactly once, regardless of who or what
    changed the stage. See apps/api/app/services/booking_automation.py and
    services/crm/system_services/unified_service.py's
    auto_create_booking_from_lead_if_ready.
    """

    def setUp(self) -> None:
        self.tmpdir = Path(".tmp-test-workdirs") / f"booking-draft-auto-{uuid.uuid4().hex}"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tmpdir / "app.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{self.db_path.resolve().as_posix()}"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(self.db_path)
        os.environ["CRM_AUTH_ENABLED"] = "false"
        self.app = _create_temp_app()
        (
            self.db, self.TripBooking, self.BookingEventTrail, self.Lead,
            self.Traveler, self.Trip, self.User, self.UnifiedCRMService,
        ) = _load_app_objects()
        self.app.config["TESTING"] = True
        with self.app.app_context():
            self.db.drop_all()
            self.db.create_all()
            self.db.session.add(self.Traveler(
                traveler_id="TR900",
                full_name="Existing Traveler",
                integrated_whatsapp="20:1000000000",
                normalized_whatsapp="+201000000000",
                phone_lookup_key="20:1000000000",
            ))
            self.db.session.add(self.Trip(
                trip_id="TR-LEAD-1",
                trip_name="Lead Trip",
                type="Local",
                sales_status="Open",
                single_total=2, double_total=2, triple_total=2,
                single_remaining=2, double_remaining=2, triple_remaining=2,
                draft_holds_single=0, draft_holds_double=0, draft_holds_triple=0,
            ))
            self.db.session.add(self.Trip(
                trip_id="TR-LEAD-2",
                trip_name="Second Trip",
                type="Local",
                sales_status="Open",
                single_total=2, double_total=2, triple_total=2,
                single_remaining=2, double_remaining=2, triple_remaining=2,
                draft_holds_single=0, draft_holds_double=0, draft_holds_triple=0,
            ))
            sales_user = self.User(username="sales.rr", full_name="Round Robin Sales", role="sales", is_active=True)
            sales_user.set_password("x")
            assigned_user = self.User(username="assigned.emp", full_name="Assigned Employee", role="agent", is_active=True)
            assigned_user.set_password("x")
            self.db.session.add(sales_user)
            self.db.session.add(assigned_user)
            self.db.session.commit()
            self.sales_user_id = sales_user.id
            self.assigned_user_id = assigned_user.id

            self.db.session.add(self.Lead(
                lead_id="L-100",
                customer_name="Sales Lead",
                lead_stage="Qualified",
                traveler_id="TR900",
                interested_trip_ids="TR-LEAD-1",
                assigned_to_user_id=self.assigned_user_id,
                channel="Instagram",
                language="ar",
            ))
            self.db.session.add(self.Lead(
                lead_id="L-200",
                customer_name="Unassigned Lead",
                lead_stage="Qualified",
                traveler_id="TR900",
                interested_trip_ids="TR-LEAD-1",
            ))
            self.db.session.add(self.Lead(
                lead_id="L-300",
                customer_name="No Traveler Lead",
                lead_stage="Qualified",
                traveler_id=None,
            ))
            self.db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("RAHMA_SYSTEM_DB_PATH", None)
        os.environ.pop("CRM_AUTH_ENABLED", None)

    def test_manual_ui_stage_change_to_booking_draft_auto_creates_booking(self) -> None:
        response = self.client.post("/leads/L-100", data={"lead_stage": "Booking Draft"})
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            lead = self.db.session.get(self.Lead, "L-100")
            self.assertTrue(lead.booking_id)
            booking = self.db.session.get(self.TripBooking, lead.booking_id)
            self.assertIsNotNone(booking)
            self.assertEqual(booking.booking_status, "Draft")
            self.assertEqual(booking.trip_id, "TR-LEAD-1")
            self.assertIsNone(booking.room_type)
            self.assertTrue(booking.missing_info)
            self.assertEqual(booking.assigned_to_user_id, self.assigned_user_id)
            self.assertTrue(booking.booking_source.startswith("Auto ("))
            event = self.BookingEventTrail.query.filter_by(
                booking_id=booking.booking_id, event_type="booking_auto_created"
            ).first()
            self.assertIsNotNone(event)
            self.assertEqual(event.lead_id, "L-100")

    def test_stage_bounce_does_not_duplicate_booking(self) -> None:
        first = self.client.post("/leads/L-100", data={"lead_stage": "Booking Draft"})
        self.assertEqual(first.status_code, 302)
        second = self.client.post("/leads/L-100", data={"lead_stage": "Handoff Needed"})
        self.assertEqual(second.status_code, 302)
        third = self.client.post("/leads/L-100", data={"lead_stage": "Booking Draft"})
        self.assertEqual(third.status_code, 302)

        with self.app.app_context():
            bookings = self.TripBooking.query.filter_by(lead_id="L-100").all()
            self.assertEqual(len(bookings), 1)

    def test_no_assigned_employee_falls_back_to_round_robin(self) -> None:
        response = self.client.post("/leads/L-200", data={"lead_stage": "Booking Draft"})
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            lead = self.db.session.get(self.Lead, "L-200")
            booking = self.db.session.get(self.TripBooking, lead.booking_id)
            self.assertEqual(booking.assigned_to_user_id, self.sales_user_id)

    def test_lead_without_traveler_id_does_not_create_booking(self) -> None:
        response = self.client.post("/leads/L-300", data={"lead_stage": "Booking Draft"})
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            lead = self.db.session.get(self.Lead, "L-300")
            self.assertFalse(lead.booking_id)
            self.assertEqual(self.TripBooking.query.filter_by(lead_id="L-300").count(), 0)

    def test_manual_create_booking_button_still_works_as_fallback(self) -> None:
        """The auto-trigger must not replace the existing manual "Create
        Booking" path -- an employee can still intentionally create a second
        booking for the same lead (e.g. a different trip) through the classic
        form. A second booking for the SAME trip+traveler is intentionally
        deduplicated by UnifiedCRMService.create_booking itself, unrelated to
        this feature, so this uses a different trip to exercise the
        genuinely-a-second-booking case."""
        self.client.post("/leads/L-100", data={"lead_stage": "Booking Draft"})
        response = self.client.post(
            "/bookings/",
            data={
                "trip_id": "TR-LEAD-2",
                "traveler_id": "TR900",
                "room_type": "Double",
                "lead_id": "L-100",
            },
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            bookings = self.TripBooking.query.filter_by(traveler_id="TR900").all()
            self.assertEqual(len(bookings), 2)

    def test_agent_write_path_via_postgres_bridge_auto_creates_booking(self) -> None:
        self.app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://staging/redacted"
        response = self.client.post(
            "/api/crm/agent/write",
            json={
                "action": "update_lead_stage",
                "payload": {"lead_id": "L-100", "requested_stage": "Booking Draft"},
                "session_context": {"session_id": "agent-session-1"},
            },
        )
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            lead = self.db.session.get(self.Lead, "L-100")
            self.assertTrue(lead.booking_id)
            booking = self.db.session.get(self.TripBooking, lead.booking_id)
            self.assertIsNotNone(booking)
            self.assertTrue(booking.booking_source.startswith("Auto (agent"))
            self.assertEqual(booking.assigned_to_user_id, self.assigned_user_id)

    def test_sqlite_backend_auto_create_is_idempotent(self) -> None:
        with self.app.app_context():
            service = self.UnifiedCRMService()
            service.update_lead_stage("L-200", requested_stage="Booking Draft")
            service.update_lead_stage("L-200", requested_stage="Handoff Needed")
            service.update_lead_stage("L-200", requested_stage="Booking Draft")

            self.db.session.expire_all()
            bookings = self.TripBooking.query.filter_by(lead_id="L-200").all()
            self.assertEqual(len(bookings), 1)
            self.assertTrue(bookings[0].missing_info)


if __name__ == "__main__":
    unittest.main()
