from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
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
    from app.models.handoff import HandoffQueue
    from app.models.lead import Lead
    from app.models.trip import Trip
    from app.models.traveler import Traveler

    return db, Lead, Trip, Traveler, TripBooking, HandoffQueue, BookingEventTrail


class Phase4LeadRedesignTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(".tmp-test-workdirs") / f"phase4-leads-{uuid.uuid4().hex}"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tmpdir / "app.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{self.db_path.resolve().as_posix()}"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(self.db_path)
        self.app = _create_temp_app()
        self.db, self.Lead, self.Trip, self.Traveler, self.TripBooking, self.HandoffQueue, self.BookingEventTrail = _load_app_objects()
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
                single_total=2,
                double_total=2,
                triple_total=2,
                single_remaining=2,
                double_remaining=2,
                triple_remaining=2,
                draft_holds_single=0,
                draft_holds_double=0,
                draft_holds_triple=0,
            ))
            self.db.session.add(self.Lead(
                lead_id="L-100",
                customer_name="Sales Lead",
                lead_stage="New Lead",
                priority="Medium",
                lead_source="Manual",
                current_step="Call back",
                follow_up_status="Open",
                booking_id="",
            ))
            self.db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("RAHMA_SYSTEM_DB_PATH", None)

    def test_lead_status_transition_validation(self) -> None:
        response = self.client.post(
            "/leads/L-100",
            data={"lead_stage": "Contacted", "current_step": "Initial follow up"},
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            lead = self.db.session.get(self.Lead, "L-100")
            self.assertEqual(lead.lead_stage, "Contacted")
            self.assertEqual(lead.current_step, "Initial follow up")

    def test_invalid_transition_is_rejected(self) -> None:
        response = self.client.post("/leads/L-100", data={"lead_stage": "Won"})
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            lead = self.db.session.get(self.Lead, "L-100")
            self.assertEqual(lead.lead_stage, "New Lead")

    def test_existing_traveler_new_interest_preserves_traveler_id(self) -> None:
        response = self.client.post(
            "/leads/",
            data={
                "customer_name": "Existing Traveler",
                "raw_phone": "201000000000",
                "lead_stage": "Contacted",
                "priority": "High",
                "lead_source": "WhatsApp",
                "interested_trip_ids": "TR-LEAD-1",
            },
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(self.Traveler.query.count(), 1)
            lead = self.Lead.query.order_by(self.Lead.created_at.desc()).first()
            self.assertEqual(lead.traveler_id, "TR900")
            self.assertEqual(lead.lead_stage, "Contacted")

    def test_manual_lead_form_shows_trip_name_selector_for_open_trips(self) -> None:
        with self.app.app_context():
            self.db.session.add(
                self.Trip(
                    trip_id="TR-INT-1",
                    trip_name="International Lead Trip",
                    type="International",
                    sales_status="Open",
                )
            )
            self.db.session.commit()

        response = self.client.get("/leads/")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Interested Trip Name", body)
        self.assertNotIn("Interested Trip IDs</label>", body)
        self.assertIn('name="interested_trip_ids"', body)
        self.assertIn("Lead Trip", body)
        self.assertIn("International Lead Trip", body)
        self.assertIn('data-trip-type="Local"', body)
        self.assertIn('data-trip-type="International"', body)

    def test_leads_index_orders_by_latest_activity(self) -> None:
        with self.app.app_context():
            older_active = self.db.session.get(self.Lead, "L-100")
            older_active.created_at = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
            older_active.updated_at = datetime(2026, 7, 15, 10, 33, tzinfo=timezone.utc)
            self.db.session.add(self.Lead(
                lead_id="L-200",
                customer_name="Newer Created Lead",
                lead_stage="New Lead",
                priority="Medium",
                lead_source="Manual",
                created_at=datetime(2026, 6, 28, 9, 0, tzinfo=timezone.utc),
                updated_at=datetime(2026, 6, 28, 9, 0, tzinfo=timezone.utc),
            ))
            self.db.session.commit()

        response = self.client.get("/leads/")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertLess(body.find("L-100"), body.find("L-200"))
        self.assertIn("Last Activity", body)

    def test_leads_index_humanizes_legacy_source_and_workflow_step(self) -> None:
        with self.app.app_context():
            lead = self.db.session.get(self.Lead, "L-100")
            lead.lead_source = b"\xf0\x9f\x92\xac- Rahma Sales AI".decode("latin-1")
            lead.current_step = "booking_ready"
            self.db.session.commit()

        response = self.client.get("/leads/")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Rahma Sales AI", body)
        self.assertIn("Ready to create booking draft", body)
        self.assertNotIn("booking_ready", body)
        self.assertNotIn("\u00c2\u00b7", body)

    def test_lost_lead_shows_reason_not_stale_next_action(self) -> None:
        with self.app.app_context():
            lead = self.db.session.get(self.Lead, "L-100")
            lead.lead_stage = "Lost"
            lead.current_step = "booking_ready"
            lead.notes = "Customer chose not to continue."
            self.db.session.commit()

        response = self.client.get("/leads/?stage=Lost")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Reason: Customer chose not to continue.", body)
        self.assertNotIn("Next: Ready to create booking draft", body)

    def test_handoff_needed_lead_visible_and_actionable(self) -> None:
        with self.app.app_context():
            lead = self.db.session.get(self.Lead, "L-100")
            lead.lead_stage = "Handoff Needed"
            lead.handoff_required = True
            lead.handoff_reason = "phone_name_conflict"
            self.db.session.commit()

        response = self.client.get("/leads/?stage=Handoff Needed")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Handoff Needed", response.data)

    def test_booking_draft_linkage_created_from_booking_flow(self) -> None:
        with self.app.app_context():
            lead = self.db.session.get(self.Lead, "L-100")
            lead.lead_stage = "Qualified"
            lead.traveler_id = "TR900"
            self.db.session.commit()

        from services.crm.system_services import UnifiedCRMService
        service = UnifiedCRMService()
        booking = service.create_booking_draft(
            traveler_id="TR900",
            traveler_name="Existing Traveler",
            trip_id="TR-LEAD-1",
            room_type="Double",
            lead_id="L-100",
            channel="web",
        )
        self.assertTrue(booking["booking_id"])
        with self.app.app_context():
            lead = self.db.session.get(self.Lead, "L-100")
            self.assertEqual(lead.booking_id, booking["booking_id"])
            self.assertIn(lead.lead_stage, {"Booking Draft", "Booking Draft Created"})
            booking_row = self.db.session.get(self.TripBooking, booking["booking_id"])
            self.assertEqual(booking_row.traveler_id, "TR900")

        bookings_response = self.client.get("/bookings/")
        self.assertEqual(bookings_response.status_code, 200)
        self.assertIn(booking["booking_id"], bookings_response.get_data(as_text=True))

    def test_manual_booking_created_from_lead_context_is_visible_on_bookings_page(self) -> None:
        with self.app.app_context():
            lead = self.db.session.get(self.Lead, "L-100")
            lead.lead_stage = "Qualified"
            lead.traveler_id = "TR900"
            self.db.session.commit()

        response = self.client.post(
            "/bookings/",
            data={
                "trip_id": "TR-LEAD-1",
                "traveler_id": "TR900",
                "traveler_name": "Existing Traveler",
                "room_type": "Double",
                "currency": "USD",
                "booking_source": "Admin",
                "lead_id": "L-100",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            booking = self.TripBooking.query.order_by(self.TripBooking.draft_created_at.desc()).first()
            self.assertIsNotNone(booking)
            self.assertEqual(booking.lead_id, "L-100")
            self.assertEqual(booking.traveler_id, "TR900")
            lead = self.db.session.get(self.Lead, "L-100")
            self.assertEqual(lead.booking_id, booking.booking_id)
            self.assertIn(lead.lead_stage, {"Booking Draft", "Booking Draft Created"})
            booking_id = booking.booking_id

        bookings_response = self.client.get("/bookings/")
        self.assertEqual(bookings_response.status_code, 200)
        body = bookings_response.get_data(as_text=True)
        self.assertIn("TR900", body)
        self.assertIn(booking_id, body)

    def test_delete_lead_hard_deletes_record_and_reuses_highest_id(self) -> None:
        with self.app.app_context():
            traveler = self.db.session.get(self.Traveler, "TR900")
            traveler.last_lead_id = "LD00030"
            booking = self.TripBooking(
                booking_id="B-L-30",
                trip_id="TR-LEAD-1",
                trip_name="Lead Trip",
                traveler_id="TR900",
                traveler_name="Existing Traveler",
                booking_status="Draft",
                lead_id="LD00030",
            )
            handoff = self.HandoffQueue(
                handoff_id="H-L-30",
                traveler_id="TR900",
                lead_id="LD00030",
                reason="delete-test",
            )
            event = self.BookingEventTrail(
                event_id="E-L-30",
                traveler_id="TR900",
                lead_id="LD00030",
                booking_id="B-L-30",
                event_type="lead_created",
                event_label="Lead Created",
            )
            self.db.session.add_all(
                [
                    self.Lead(
                        lead_id="LD00029",
                        customer_name="Lower Lead",
                        lead_stage="Qualified",
                        traveler_id="TR900",
                    ),
                    self.Lead(
                        lead_id="LD00030",
                        customer_name="Delete Me",
                        lead_stage="Booking Draft",
                        traveler_id="TR900",
                        booking_id="B-L-30",
                    ),
                    booking,
                    handoff,
                    event,
                ]
            )
            self.db.session.commit()

        with self.assertLogs("app.routes.leads", level="INFO") as logs:
            response = self.client.post(
                "/leads/LD00030",
                data={"_method": "DELETE"},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 302)
        # Regression: the TripBooking.lead_id/Traveler.last_lead_id cleanup
        # in delete() are bulk .update() calls whose rowcount was never
        # captured or logged -- nothing recorded how many rows a cascade
        # delete actually touched. Confirm the counts are now logged and
        # match the 1 booking / 1 traveler set up above.
        self.assertTrue(any("unlinked 1 booking(s), 1 traveler" in message for message in logs.output))

        with self.app.app_context():
            self.assertIsNone(self.db.session.get(self.Lead, "LD00030"))
            self.assertIsNone(self.db.session.get(self.HandoffQueue, "H-L-30"))
            self.assertIsNone(self.db.session.get(self.BookingEventTrail, "E-L-30"))
            booking = self.db.session.get(self.TripBooking, "B-L-30")
            self.assertIsNotNone(booking)
            self.assertIsNone(booking.lead_id)
            traveler = self.db.session.get(self.Traveler, "TR900")
            self.assertIsNone(traveler.last_lead_id)

        create_response = self.client.post(
            "/leads/",
            data={
                "customer_name": "Replacement Lead",
                "raw_phone": "201022233344",
                "lead_stage": "Contacted",
                "priority": "High",
                "lead_source": "WhatsApp",
                "interested_trip_ids": "TR-LEAD-1",
            },
            follow_redirects=False,
        )
        self.assertEqual(create_response.status_code, 302)

        with self.app.app_context():
            replacement = self.db.session.get(self.Lead, "LD00030")
            self.assertIsNotNone(replacement)
            self.assertEqual(replacement.customer_name, "Replacement Lead")


if __name__ == "__main__":
    unittest.main()
