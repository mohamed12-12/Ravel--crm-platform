from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
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


class RevenueAnalyticsTests(unittest.TestCase):
    """apps/api/app/routes/admin.py's revenue_analytics() aggregates
    company-wide money by year/month/trip-type, admin-only. Uses the shared
    app/services/revenue.py rules so it can never disagree with the
    per-traveler Lifetime Revenue figure about what counts as revenue.
    """

    def setUp(self) -> None:
        self.tmpdir = Path(".tmp-test-workdirs") / f"revenue-analytics-{uuid.uuid4().hex}"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tmpdir / "app.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{self.db_path.resolve().as_posix()}"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(self.db_path)
        os.environ["CRM_AUTH_ENABLED"] = "false"
        self.app = _create_temp_app()
        from app.extensions import db
        from app.models.booking import TripBooking
        from app.models.booking_status_history import BookingStatusHistory
        from app.models.traveler import Traveler
        from app.models.trip import Trip

        self.db = db
        self.TripBooking = TripBooking
        self.BookingStatusHistory = BookingStatusHistory
        self.app.config["TESTING"] = True
        with self.app.app_context():
            self.db.drop_all()
            self.db.create_all()
            self.db.session.add(Traveler(traveler_id="TR900", full_name="Revenue Traveler"))
            self.db.session.add(Trip(
                trip_id="TRIP-REV-1",
                trip_name="Revenue Trip",
                type="Local",
                sales_status="Open",
                room_prices_json=json.dumps({"double": {"USD": "1000", "EGP": "50000"}}),
            ))
            self.db.session.add(self.TripBooking(
                booking_id="BK-REV-1",
                trip_id="TRIP-REV-1",
                traveler_id="TR900",
                room_type="Double",
                currency="USD",
                group_size=2,
                booking_status="Confirmed",
                payment_status="Fully Paid",
                draft_created_at=datetime(2025, 1, 5, tzinfo=timezone.utc),
            ))
            # A booking sitting in Draft/Pending must not count as revenue at all.
            self.db.session.add(self.TripBooking(
                booking_id="BK-REV-2",
                trip_id="TRIP-REV-1",
                traveler_id="TR900",
                room_type="Double",
                currency="USD",
                group_size=1,
                booking_status="Draft",
                payment_status="Pending",
                draft_created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            ))
            self.db.session.add(self.BookingStatusHistory(
                booking_id="BK-REV-1",
                old_status="Draft",
                new_status="Confirmed",
                changed_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
            ))
            self.db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("RAHMA_SYSTEM_DB_PATH", None)
        os.environ.pop("CRM_AUTH_ENABLED", None)

    def test_page_renders_and_attributes_revenue_to_the_confirmed_status_date(self) -> None:
        response = self.client.get("/admin/revenue-analytics?year=2026")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        # 2 travelers * $1000 = $2000, recognized in June (the Confirmed
        # transition date), NOT January (draft_created_at).
        self.assertIn("$2000.00", body.replace(",", ""))
        self.assertIn("Jun 2026", body)

    def test_draft_booking_is_excluded_from_revenue(self) -> None:
        response = self.client.get("/admin/revenue-analytics")
        body = response.get_data(as_text=True)
        # Only 1 recognized booking total (BK-REV-1); BK-REV-2 (Draft/Pending)
        # must never be counted.
        self.assertIn("<td class=\"num\">1</td>", body)

    def test_revenue_analytics_matches_shared_revenue_module(self) -> None:
        with self.app.app_context():
            from app.models.trip import Trip
            from app.services.revenue import booking_revenue

            trip = self.db.session.get(Trip, "TRIP-REV-1")
            booking = self.db.session.get(self.TripBooking, "BK-REV-1")
            currency, amount = booking_revenue(booking, trip)
            self.assertEqual(currency, "USD")
            self.assertEqual(amount, 2000.0)


if __name__ == "__main__":
    unittest.main()
