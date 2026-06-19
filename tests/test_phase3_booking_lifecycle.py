from __future__ import annotations

import os
import shutil
import tempfile
import uuid
import unittest
import sys
from pathlib import Path

SYSTEM_ROOT = Path(__file__).resolve().parent.parent / "apps" / "api"
if str(SYSTEM_ROOT) not in sys.path:
    sys.path.insert(0, str(SYSTEM_ROOT))

from app.extensions import db
from app.models.booking import TripBooking
from app.models.booking_status_history import BookingStatusHistory
from app.models.traveler import Traveler
from app.models.trip import Trip
from services.crm.system_services import UnifiedCRMService




def _create_temp_app():
    from app import create_app
    return create_app("development")
class Phase3BookingLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(".tmp-test-workdirs") / f"phase3-booking-life-{uuid.uuid4().hex}"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tmpdir / "app.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{self.db_path.resolve().as_posix()}"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(self.db_path)
        self.app = _create_temp_app()
        self.app.config["TESTING"] = True
        with self.app.app_context():
            db.drop_all()
            db.create_all()
            db.session.add(
                Traveler(
                    traveler_id="TR100",
                    full_name="Returning Traveler",
                    integrated_whatsapp="20:1000000000",
                    normalized_whatsapp="+201000000000",
                    phone_lookup_key="20:1000000000",
                )
            )
            db.session.add(
                Trip(
                    trip_id="TRIP-100",
                    trip_name="Lifecycle Trip",
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
                )
            )
            db.session.commit()
        self.client = self.app.test_client()
        self.service = UnifiedCRMService()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("RAHMA_SYSTEM_DB_PATH", None)

    def test_lifecycle_transitions_and_history_are_recorded(self) -> None:
        with self.app.app_context():
            booking = TripBooking(
                booking_id="B-100",
                trip_id="TRIP-100",
                trip_name="Lifecycle Trip",
                traveler_id="TR100",
                traveler_name="Returning Traveler",
                room_type="Double",
                booking_status="Draft",
                booking_source="Admin",
                payment_status="Pending",
            )
            db.session.add(booking)
            db.session.commit()

        first = self.service.update_booking_status("B-100", new_status="Waiting Customer", notes="Waiting on reply")
        second = self.service.update_booking_status("B-100", new_status="Pending Confirmation")
        third = self.service.update_booking_status("B-100", new_status="Confirmed")
        fourth = self.service.update_booking_status("B-100", new_status="Payment Pending")
        fifth = self.service.update_booking_status("B-100", new_status="Paid", new_payment_status="Fully Paid")
        sixth = self.service.update_booking_status("B-100", new_status="Completed")

        self.assertEqual(first["booking_status"], "Waiting Customer")
        self.assertEqual(second["booking_status"], "Pending Confirmation")
        self.assertEqual(third["booking_status"], "Confirmed")
        self.assertEqual(fourth["booking_status"], "Payment Pending")
        self.assertEqual(fifth["booking_status"], "Paid")
        self.assertEqual(fifth["payment_status"], "Fully Paid")
        self.assertEqual(sixth["booking_status"], "Completed")

        with self.app.app_context():
            history = BookingStatusHistory.query.filter_by(booking_id="B-100").order_by(BookingStatusHistory.history_id.asc()).all()
            self.assertEqual([(row.old_status, row.new_status) for row in history], [
                ("Draft", "Waiting Customer"),
                ("Waiting Customer", "Pending Confirmation"),
                ("Pending Confirmation", "Confirmed"),
                ("Confirmed", "Payment Pending"),
                ("Payment Pending", "Paid"),
                ("Paid", "Completed"),
            ])

    def test_invalid_transition_is_rejected(self) -> None:
        with self.app.app_context():
            booking = TripBooking(
                booking_id="B-101",
                trip_id="TRIP-100",
                trip_name="Lifecycle Trip",
                traveler_id="TR100",
                traveler_name="Returning Traveler",
                room_type="Double",
                booking_status="Draft",
                booking_source="Admin",
                payment_status="Pending",
            )
            db.session.add(booking)
            db.session.commit()

        with self.assertRaises(ValueError):
            self.service.update_booking_status("B-101", new_status="Confirmed")

    def test_returning_traveler_booking_preserves_traveler_id(self) -> None:
        booking = self.service.create_booking_draft(
            traveler_id="TR100",
            traveler_name="Returning Traveler",
            trip_id="TRIP-100",
            room_type="Double",
            channel="web",
            source="Web CRM",
        )
        self.assertEqual(booking["traveler_id"], "TR100")
        with self.app.app_context():
            self.assertEqual(Traveler.query.count(), 1)
            self.assertEqual(TripBooking.query.filter_by(traveler_id="TR100").count(), 1)

    def test_admin_booking_ui_updates_lifecycle_and_history(self) -> None:
        with self.app.app_context():
            booking = TripBooking(
                booking_id="B-102",
                trip_id="TRIP-100",
                trip_name="Lifecycle Trip",
                traveler_id="TR100",
                traveler_name="Returning Traveler",
                room_type="Double",
                booking_status="Confirmed",
                booking_source="Admin",
                payment_status="Pending",
            )
            db.session.add(booking)
            db.session.commit()

        response = self.client.post(
            "/bookings/B-102/status",
            data={
                "booking_status": "Payment Pending",
                "payment_status": "Deposit Paid",
                "booking_notes": "Deposit requested",
            },
        )
        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            booking = TripBooking.query.get("B-102")
            self.assertEqual(booking.booking_status, "Payment Pending")
            self.assertEqual(booking.payment_status, "Deposit Paid")
            history = BookingStatusHistory.query.filter_by(booking_id="B-102").all()
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0].old_status, "Confirmed")
            self.assertEqual(history[0].new_status, "Payment Pending")

    def test_trip_detail_shows_remaining_after_active_bookings(self) -> None:
        with self.app.app_context():
            db.session.add_all(
                [
                    TripBooking(
                        booking_id="B-200",
                        trip_id="TRIP-100",
                        trip_name="Lifecycle Trip",
                        traveler_id="TR100",
                        traveler_name="Returning Traveler",
                        room_type="Double",
                        booking_status="Confirmed",
                        booking_source="Admin",
                        payment_status="Pending",
                    ),
                    TripBooking(
                        booking_id="B-201",
                        trip_id="TRIP-100",
                        trip_name="Lifecycle Trip",
                        traveler_id="TR100",
                        traveler_name="Returning Traveler",
                        room_type="Triple",
                        booking_status="Cancelled",
                        booking_source="Admin",
                        payment_status="Pending",
                    ),
                ]
            )
            db.session.commit()

        response = self.client.get("/trips/TRIP-100")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Double", body)
        self.assertIn("Booked: 1", body)
        self.assertIn("of 2 remaining", body)


if __name__ == "__main__":
    unittest.main()
