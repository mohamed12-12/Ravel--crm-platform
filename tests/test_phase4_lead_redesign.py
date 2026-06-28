from __future__ import annotations

import os
import shutil
import sys
import tempfile
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
    from app.models.lead import Lead
    from app.models.trip import Trip
    from app.models.traveler import Traveler

    return db, Lead, Trip, Traveler, TripBooking


class Phase4LeadRedesignTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(".tmp-test-workdirs") / f"phase4-leads-{uuid.uuid4().hex}"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tmpdir / "app.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{self.db_path.resolve().as_posix()}"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(self.db_path)
        self.app = _create_temp_app()
        self.db, self.Lead, self.Trip, self.Traveler, self.TripBooking = _load_app_objects()
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


if __name__ == "__main__":
    unittest.main()
