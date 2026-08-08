from __future__ import annotations

import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent / "apps" / "api"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def _fresh_app():
    from app import create_app

    return create_app("development")


class TripDeleteAndSortTests(unittest.TestCase):
    """Trip Inventory previously sorted Open/Closed/Cancelled trips together
    by start_date alone (many are NULL/"TBD"), burying Open trips under
    Closed ones, and had no delete action at all. This covers both: Open
    trips sorting first, and deleting a trip -- admin-only, blocked while
    real bookings still reference it, otherwise cleaning up the records
    that point at it the same way travelers.delete() already does.
    """

    def setUp(self) -> None:
        self.original_env = dict(os.environ)
        self.tmpdir = Path(".tmp-test-workdirs") / f"trip-delete-{uuid.uuid4().hex}"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tmpdir / "crm.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{self.db_path.resolve().as_posix()}"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(self.db_path)
        os.environ["CRM_AUTH_ENABLED"] = "true"
        self.app = _fresh_app()
        self.app.config.update(TESTING=True, CRM_AUTH_ENABLED=True, SECRET_KEY="test-secret")

        from app.extensions import db
        from app.models.booking import TripBooking
        from app.models.booking_event import BookingEventTrail
        from app.models.handoff import HandoffQueue
        from app.models.trip import Trip
        from app.models.user import User

        self.db = db
        self.Trip = Trip
        self.TripBooking = TripBooking
        self.BookingEventTrail = BookingEventTrail
        self.HandoffQueue = HandoffQueue
        self.User = User

        with self.app.app_context():
            self.db.drop_all()
            self.db.create_all()

            admin = self.User(username="admin-y", full_name="Admin Y", password_hash="x", role="admin", is_active=True)
            manager = self.User(username="manager-y", full_name="Manager Y", password_hash="x", role="manager", is_active=True)
            agent = self.User(username="agent-y", full_name="Agent Y", password_hash="x", role="agent", is_active=True)
            self.db.session.add_all([admin, manager, agent])
            self.db.session.commit()
            self.admin_id, self.admin_username = admin.id, admin.username
            self.manager_id, self.manager_username = manager.id, manager.username
            self.agent_id, self.agent_username = agent.id, agent.username

        self.client = self.app.test_client()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.original_env)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _login(self, *, user_id: int, username: str) -> None:
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["username"] = username
            sess["user_id"] = user_id

    def _seed_trip(self, trip_id: str, **overrides) -> None:
        defaults = dict(trip_id=trip_id, trip_name=f"Trip {trip_id}", type="Local")
        defaults.update(overrides)
        with self.app.app_context():
            self.db.session.add(self.Trip(**defaults))
            self.db.session.commit()

    # --- sort order ---

    def test_open_trips_are_listed_before_closed_trips_regardless_of_date(self):
        from datetime import date

        self._seed_trip("RT-CLOSED-RECENT", sales_status="Closed", start_date=date(2026, 1, 1))
        self._seed_trip("RT-OPEN-NO-DATE", sales_status="Open", start_date=None)
        self._seed_trip("RT-OPEN-FUTURE", sales_status="Open", start_date=date(2027, 1, 1))
        self._login(user_id=self.admin_id, username=self.admin_username)

        html = self.client.get("/trips/").get_data(as_text=True)
        open_no_date_pos = html.index("RT-OPEN-NO-DATE")
        open_future_pos = html.index("RT-OPEN-FUTURE")
        closed_pos = html.index("RT-CLOSED-RECENT")
        self.assertLess(open_no_date_pos, closed_pos)
        self.assertLess(open_future_pos, closed_pos)

    # --- delete permission ---

    def test_admin_can_delete_a_trip_with_no_bookings(self):
        self._seed_trip("RT-DEL-1")
        self._login(user_id=self.admin_id, username=self.admin_username)
        with self.client.session_transaction() as sess:
            token = "csrf-y"
            sess["csrf_token"] = token
        response = self.client.delete("/trips/RT-DEL-1", headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertIsNone(self.db.session.get(self.Trip, "RT-DEL-1"))

    def test_manager_cannot_delete_a_trip(self):
        self._seed_trip("RT-DEL-2")
        self._login(user_id=self.manager_id, username=self.manager_username)
        response = self.client.delete("/trips/RT-DEL-2", headers={"X-CSRF-Token": "csrf-y"})
        self.assertEqual(response.status_code, 403)
        with self.app.app_context():
            self.assertIsNotNone(self.db.session.get(self.Trip, "RT-DEL-2"))

    def test_agent_cannot_delete_a_trip(self):
        self._seed_trip("RT-DEL-3")
        self._login(user_id=self.agent_id, username=self.agent_username)
        response = self.client.delete("/trips/RT-DEL-3", headers={"X-CSRF-Token": "csrf-y"})
        self.assertEqual(response.status_code, 403)

    # --- delete safety around related records ---

    def test_admin_cannot_delete_a_trip_with_bookings(self):
        self._seed_trip("RT-DEL-4")
        with self.app.app_context():
            self.db.session.add(self.TripBooking(
                booking_id="BK-DEL-1", trip_id="RT-DEL-4", trip_name="Trip RT-DEL-4",
                traveler_id="TR-DEL-1", traveler_name="Someone", room_type="Single",
                booking_status="Confirmed", payment_status="Pending", booking_source="Admin",
                passport_required=False,
            ))
            self.db.session.commit()
        self._login(user_id=self.admin_id, username=self.admin_username)
        with self.client.session_transaction() as sess:
            token = "csrf-y"
            sess["csrf_token"] = token
        response = self.client.delete("/trips/RT-DEL-4", headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 409)
        with self.app.app_context():
            self.assertIsNotNone(self.db.session.get(self.Trip, "RT-DEL-4"))

    def test_deleting_a_trip_nulls_the_trip_id_on_referencing_handoff_cases(self):
        self._seed_trip("RT-DEL-5")
        with self.app.app_context():
            self.db.session.add(self.HandoffQueue(handoff_id="H-DEL-1", trip_id="RT-DEL-5", reason="Question about trip"))
            self.db.session.commit()
        self._login(user_id=self.admin_id, username=self.admin_username)
        with self.client.session_transaction() as sess:
            token = "csrf-y"
            sess["csrf_token"] = token
        response = self.client.delete("/trips/RT-DEL-5", headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            case = self.db.session.get(self.HandoffQueue, "H-DEL-1")
            self.assertIsNotNone(case)
            self.assertIsNone(case.trip_id)

    def test_deleting_a_trip_removes_its_booking_event_trail_rows(self):
        self._seed_trip("RT-DEL-6")
        with self.app.app_context():
            self.db.session.add(self.BookingEventTrail(event_id="EVT-DEL-1", trip_id="RT-DEL-6", event_type="note", actor="tester"))
            self.db.session.commit()
        self._login(user_id=self.admin_id, username=self.admin_username)
        with self.client.session_transaction() as sess:
            token = "csrf-y"
            sess["csrf_token"] = token
        response = self.client.delete("/trips/RT-DEL-6", headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            remaining = self.BookingEventTrail.query.filter_by(trip_id="RT-DEL-6").count()
            self.assertEqual(remaining, 0)


if __name__ == "__main__":
    unittest.main()
