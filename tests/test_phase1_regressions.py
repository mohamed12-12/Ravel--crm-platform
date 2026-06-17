from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import create_app
from app.extensions import db
from app.models.booking import TripBooking
from services.crm.system_services.phone_normalization import normalize_phone_input
from test_phase11_demo_features import _make_app_with_db


class Phase1PhoneContractTests(unittest.TestCase):
    def test_phone_identity_contract_exposes_backward_compatible_keys(self) -> None:
        result = normalize_phone_input("+201012345678", "")
        data = result.to_dict()
        self.assertEqual(data["phone"], "+201012345678")
        self.assertEqual(data["e164"], "+201012345678")
        self.assertEqual(data["phone_lookup_key"], "20:1012345678")
        self.assertIn("legacy_lookup_keys", data)


class Phase1BookingRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="phase1-booking-"))
        self.db_path = self.tmpdir / "app.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{self.db_path}"
        self.app = create_app("development")
        self.app.config["TESTING"] = True

        with self.app.app_context():
            db.drop_all()
            db.create_all()
            booking = TripBooking(
                booking_id="B-001",
                trip_id="RT-LOC-26-001",
                trip_name="Sinai Trek",
                traveler_id="TR001",
                traveler_name="Test Traveler",
                room_type="Double",
                flight_option="With Flight",
                currency="EGP",
                booking_status="Confirmed",
                booking_source="Admin",
                payment_status="Pending",
                booking_notes="Initial note",
            )
            db.session.add(booking)
            db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("DATABASE_URL", None)

    def test_booking_status_update_accepts_form_payload(self) -> None:
        response = self.client.post(
            "/bookings/B-001/status",
            data={
                "booking_status": "Payment Pending",
                "payment_status": "Fully Paid",
                "booking_notes": "Paid in full",
            },
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            booking = TripBooking.query.get("B-001")
            self.assertEqual(booking.booking_status, "Payment Pending")
            self.assertEqual(booking.payment_status, "Fully Paid")


class Phase1AgentSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="phase1-agent-"))
        self.original_env = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.original_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_booking_flow_keeps_session_distinct_from_completed(self) -> None:
        client, _ = _make_app_with_db(self.tmp)
        session = client.post("/api/session", json={}).get_json()["session"]
        session_id = session["id"]
        client.post(
            f"/api/session/{session_id}/intake",
            json={
                "fullName": "Test Traveler",
                "birthday": "1990-01-01",
                "gender": "Male",
                "nationality": "Egypt",
                "countryCode": "20",
                "rawPhone": "01012345678",
            },
        )
        client.post(f"/api/session/{session_id}/message", json={"text": "local"})
        client.post(f"/api/session/{session_id}/message", json={"text": "1"})
        client.post(f"/api/session/{session_id}/message", json={"text": "single"})
        client.post(f"/api/session/{session_id}/message", json={"text": "no"})
        session = client.post(f"/api/session/{session_id}/message", json={"text": "EGP"}).get_json()["session"]
        self.assertEqual(session["stage"], "booking_created")
        self.assertNotEqual(session["stage"], "completed")


if __name__ == "__main__":
    unittest.main()
