from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

SYSTEM_ROOT = Path(__file__).resolve().parent.parent / "apps" / "api"
if str(SYSTEM_ROOT) not in sys.path:
    sys.path.insert(0, str(SYSTEM_ROOT))

from app.extensions import db
from app.models.traveler import Traveler
from app.models.booking import TripBooking
from services.crm.system_services.phone_normalization import normalize_phone_input
from test_phase11_demo_features import _make_app_with_db


def _create_temp_app(db_path):
    from app import create_app
    return create_app("development")


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
        self.tmpdir = Path(".tmp-test-workdirs") / f"phase1-booking-{uuid.uuid4().hex}"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tmpdir / "app.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{self.db_path.resolve().as_posix()}"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(self.db_path)
        self.app = _create_temp_app(self.db_path)
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
        os.environ.pop("RAHMA_SYSTEM_DB_PATH", None)

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
            booking = db.session.get(TripBooking, "B-001")
            self.assertEqual(booking.booking_status, "Payment Pending")
            self.assertEqual(booking.payment_status, "Fully Paid")

    def test_travelers_index_normalizes_legacy_slash_datetime_values(self) -> None:
        with self.app.app_context():
            db.session.add(
                Traveler(
                    traveler_id="TR-LEGACY-1",
                    full_name="Legacy Traveler",
                    status="Active",
                )
            )
            db.session.commit()
            db.session.execute(
                db.text(
                    """
                    UPDATE travelers
                    SET created_at = :created_at,
                        last_contacted_at = :last_contacted_at
                    WHERE traveler_id = :traveler_id
                    """
                ),
                {
                    "traveler_id": "TR-LEGACY-1",
                    "created_at": "06/28/2026",
                    "last_contacted_at": "06/29/2026 14:30:00",
                },
            )
            db.session.commit()

        reloaded_app = _create_temp_app(self.db_path)
        reloaded_app.config["TESTING"] = True
        client = reloaded_app.test_client()
        response = client.get("/travelers/")
        self.assertEqual(response.status_code, 200)

        with reloaded_app.app_context():
            traveler = db.session.get(Traveler, "TR-LEGACY-1")
            self.assertIsNotNone(traveler)
            self.assertEqual(traveler.created_at.isoformat(), "2026-06-28T00:00:00")
            self.assertEqual(traveler.last_contacted_at.isoformat(), "2026-06-29T14:30:00")


class Phase1AgentSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(".tmp-test-workdirs") / f"phase1-agent-{uuid.uuid4().hex}"
        self.tmp.mkdir(parents=True, exist_ok=True)
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
        self.assertEqual(session["stage"], "completed")


if __name__ == "__main__":
    unittest.main()
