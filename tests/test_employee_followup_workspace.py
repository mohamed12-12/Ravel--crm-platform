from __future__ import annotations

import os
import shutil
import sys
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parent.parent / "apps" / "api"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def _fresh_app():
    from app import create_app

    return create_app("development")


class EmployeeFollowupWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_env = dict(os.environ)
        self.tmpdir = Path(".tmp-test-workdirs") / f"employee-followup-{uuid.uuid4().hex}"
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
        from app.models.booking_status_history import BookingStatusHistory
        from app.models.lead import Lead
        from app.models.traveler import Traveler
        from app.models.trip import Trip
        from app.models.user import User

        self.db = db
        self.TripBooking = TripBooking
        self.BookingStatusHistory = BookingStatusHistory
        self.BookingEventTrail = BookingEventTrail
        self.Lead = Lead
        self.Traveler = Traveler
        self.Trip = Trip
        self.User = User
        with self.app.app_context():
            self.db.drop_all()
            self.db.create_all()
            # This suite points DATABASE_URL and RAHMA_SYSTEM_DB_PATH at the
            # same file, so it cannot expose split-brain persistence bugs
            # unless a test deliberately diverges them.
            self.db.session.add(
                self.Traveler(
                    traveler_id="TR-FU-1",
                    full_name="Follow Up Traveler",
                    normalized_whatsapp="+201000000000",
                    phone_lookup_key="20:1000000000",
                )
            )
            self.db.session.add(
                self.Trip(
                    trip_id="TRIP-FU-1",
                    trip_name="Follow Up Trip",
                    type="International",
                    sales_status="Open",
                    single_total=4,
                    single_remaining=4,
                )
            )
            self.db.session.add(
                self.TripBooking(
                    booking_id="B-FU-1",
                    trip_id="TRIP-FU-1",
                    trip_name="Follow Up Trip",
                    traveler_id="TR-FU-1",
                    traveler_name="Follow Up Traveler",
                    room_type="Single",
                    booking_status="Confirmed",
                    payment_status="Pending",
                    booking_source="Admin",
                    passport_required=True,
                    passport_status="pending",
                )
            )
            self.db.session.add(
                self.Lead(
                    lead_id="L-FU-1",
                    customer_name="Lead Follow Up",
                    raw_phone="01000000000",
                    traveler_id="TR-FU-1",
                    lead_stage="New Lead",
                    priority="Medium",
                    follow_up_due_date=date.today() - timedelta(days=1),
                    current_step="Call customer",
                    channel="WhatsApp",
                )
            )
            self.db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.original_env)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _login(self, role: str = "agent", username: str = "tester") -> str:
        token = f"csrf-{uuid.uuid4().hex}"
        with self.app.app_context():
            user = self.User.query.filter_by(username=username).one_or_none()
            if user is None:
                user = self.User(
                    username=username,
                    full_name=username.title(),
                    password_hash="test-hash",
                    role=role,
                    is_active=True,
                )
                self.db.session.add(user)
            else:
                user.role = role
                user.is_active = True
            self.db.session.commit()
            user_id = user.id
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["username"] = username
            sess["user_id"] = user_id
            sess["csrf_token"] = token
        return token

    def _user_id(self, username: str) -> int:
        with self.app.app_context():
            return self.User.query.filter_by(username=username).one().id

    def test_unauthenticated_browser_status_update_redirects_to_login_not_raw_json(self) -> None:
        response = self.client.post(
            "/bookings/B-FU-1/status",
            data={"booking_status": "Payment Pending", "payment_status": "Pending"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])
        self.assertNotIn("authentication_required", response.get_data(as_text=True))

    def test_api_client_still_receives_structured_json_auth_error(self) -> None:
        response = self.client.post(
            "/bookings/B-FU-1/status",
            json={"booking_status": "Payment Pending"},
            headers={"Accept": "application/json"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"], "authentication_required")

    def test_csrf_failure_is_rejected_for_browser_session(self) -> None:
        self._login()
        response = self.client.post(
            "/bookings/B-FU-1/status",
            data={"booking_status": "Payment Pending", "payment_status": "Pending"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("authentication_required", response.get_data(as_text=True))

    def test_authorized_employee_updates_booking_status_payment_note_and_followup(self) -> None:
        token = self._login(role="manager", username="mona")
        response = self.client.post(
            "/bookings/B-FU-1/status",
            data={
                "csrf_token": token,
                "expected_history_count": "0",
                "booking_status": "Payment Pending",
                "payment_status": "Deposit Paid",
                "booking_notes": "Deposit requested by phone.",
                "assigned_to_user_id": str(self._user_id("mona")),
                "priority": "High",
                "next_action": "Verify deposit receipt",
                "next_follow_up_at": "2026-07-17T09:30",
                "customer_response_status": "Contacted",
                "mark_contacted": "1",
            },
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            booking = self.db.session.get(self.TripBooking, "B-FU-1")
            self.assertEqual(booking.booking_status, "Payment Pending")
            self.assertEqual(booking.payment_status, "Deposit Paid")
            self.assertEqual(booking.assigned_to, "Mona")
            self.assertEqual(booking.assigned_to_user_id, self._user_id("mona"))
            self.assertEqual(booking.priority, "High")
            self.assertEqual(booking.next_action, "Verify deposit receipt")
            self.assertIn("Deposit requested by phone.", booking.booking_notes)
            history = self.BookingStatusHistory.query.filter_by(booking_id="B-FU-1").one()
            self.assertEqual(history.changed_by, "mona")
            self.assertEqual(history.old_status, "Confirmed")
            self.assertEqual(history.new_status, "Payment Pending")
            self.assertEqual(history.change_source, "crm-ui")
            # BookingEventTrail writes were tied to the removed SQLite path and
            # remain a tracked follow-up, not a regression for this route.

    def test_status_update_ignores_broken_system_db_path_and_writes_postgres_models(self) -> None:
        token = self._login(role="manager", username="mona")
        broken_path = self.tmpdir / "missing" / "rahma-system.db"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(broken_path)
        response = self.client.post(
            "/bookings/B-FU-1/status",
            data={
                "csrf_token": token,
                "expected_history_count": "0",
                "booking_status": "Payment Pending",
                "payment_status": "Deposit Paid",
                "booking_notes": "Deposit requested by phone.",
            },
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            booking = self.db.session.get(self.TripBooking, "B-FU-1")
            self.assertEqual(booking.booking_status, "Payment Pending")
            self.assertEqual(booking.payment_status, "Deposit Paid")
            history = self.BookingStatusHistory.query.filter_by(booking_id="B-FU-1").order_by(
                self.BookingStatusHistory.history_id.desc()
            ).first()
            self.assertIsNotNone(history)
            self.assertEqual(history.change_source, "crm-ui")
            self.assertEqual(history.new_status, "Payment Pending")

    def test_status_update_logs_and_surfaces_unexpected_save_errors(self) -> None:
        token = self._login(role="manager", username="mona")
        from unittest.mock import patch

        with patch("app.routes.bookings.apply_assignment", side_effect=RuntimeError("assignment failed")):
            with self.assertLogs("app.routes.bookings", level="ERROR") as logs:
                response = self.client.post(
                    "/bookings/B-FU-1/status",
                    data={
                        "csrf_token": token,
                        "expected_history_count": "0",
                        "booking_status": "Payment Pending",
                        "payment_status": "Deposit Paid",
                        "assigned_to_user_id": str(self._user_id("mona")),
                    },
                    follow_redirects=True,
                )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any("Booking status update failed booking_id=B-FU-1" in entry for entry in logs.output))
        self.assertIn("Something went wrong saving this update. Please try again.", response.get_data(as_text=True))

    def test_booking_detail_offers_full_employee_status_menu(self) -> None:
        self._login()
        response = self.client.get("/bookings/B-FU-1")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        # The manual status menu is intentionally simplified to the current
        # value plus Draft/Completed/Cancelled (see _allowed_status_options) -
        # intermediate lifecycle statuses like Payment Pending/Paid are driven
        # elsewhere, not hand-picked from this dropdown.
        self.assertIn('<option value="Confirmed" selected>', body)
        self.assertIn('<option value="Draft" >', body)
        self.assertIn('<option value="Cancelled" >', body)
        self.assertIn('<option value="Completed" >', body)
        self.assertNotIn('<option value="Payment Pending"', body)
        self.assertNotIn('<option value="Paid"', body)
        self.assertIn('More follow-up details', body)

    def test_employee_can_confirm_draft_directly_with_reason(self) -> None:
        token = self._login(role="agent", username="sara")
        with self.app.app_context():
            self.db.session.add(
                self.TripBooking(
                    booking_id="B-FU-DRAFT",
                    trip_id="TRIP-FU-1",
                    trip_name="Follow Up Trip",
                    traveler_id="TR-FU-1",
                    traveler_name="Follow Up Traveler",
                    room_type="Single",
                    booking_status="Draft",
                    payment_status="Pending",
                    booking_source="Admin",
                )
            )
            self.db.session.commit()

        response = self.client.post(
            "/bookings/B-FU-DRAFT/status",
            data={
                "csrf_token": token,
                "expected_history_count": "0",
                "employee_correction": "1",
                "booking_status": "Confirmed",
                "payment_status": "Pending",
                "booking_notes": "Customer confirmation received by phone.",
            },
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            booking = self.db.session.get(self.TripBooking, "B-FU-DRAFT")
            self.assertEqual(booking.booking_status, "Confirmed")
            history = self.BookingStatusHistory.query.filter_by(booking_id="B-FU-DRAFT").one()
            self.assertEqual(history.old_status, "Draft")
            self.assertEqual(history.new_status, "Confirmed")
            self.assertIn("Customer confirmation", history.notes)

    def test_employee_can_reopen_lost_lead_with_reason(self) -> None:
        token = self._login(role="agent", username="sara")
        with self.app.app_context():
            self.db.session.add(
                self.Lead(
                    lead_id="L-FU-LOST",
                    customer_name="Reopen Me",
                    raw_phone="01000000001",
                    traveler_id="TR-FU-1",
                    lead_stage="Lost",
                    priority="Medium",
                    channel="WhatsApp",
                )
            )
            self.db.session.commit()

        response = self.client.post(
            "/leads/L-FU-LOST",
            data={
                "csrf_token": token,
                "employee_correction": "1",
                "lead_stage": "Won",
                "stage_change_reason": "Customer confirmed after the previous lost outcome.",
            },
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            lead = self.db.session.get(self.Lead, "L-FU-LOST")
            self.assertEqual(lead.lead_stage, "Won")
            event = self.BookingEventTrail.query.filter_by(lead_id="L-FU-LOST").one()
            self.assertIn("Lost -> Won", event.notes)
            self.assertIn("Customer confirmed", event.notes)

    def test_unauthorized_assignment_change_is_rejected(self) -> None:
        token = self._login(role="agent", username="agent")
        response = self.client.post(
            "/bookings/B-FU-1/status",
            data={
                "csrf_token": token,
                "expected_history_count": "0",
                "booking_status": "Confirmed",
                "payment_status": "Pending",
                "assigned_to": "Manager",
            },
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            booking = self.db.session.get(self.TripBooking, "B-FU-1")
            self.assertIsNone(booking.assigned_to)

    def test_invalid_booking_and_payment_transitions_are_rejected(self) -> None:
        token = self._login()
        invalid_booking = self.client.post(
            "/bookings/B-FU-1/status",
            data={
                "csrf_token": token,
                "expected_history_count": "0",
                "booking_status": "Completed",
                "payment_status": "Pending",
            },
        )
        self.assertEqual(invalid_booking.status_code, 302)
        with self.app.app_context():
            booking = self.db.session.get(self.TripBooking, "B-FU-1")
            booking.payment_status = "Fully Paid"
            self.db.session.commit()
        invalid_payment = self.client.post(
            "/bookings/B-FU-1/status",
            data={
                "csrf_token": token,
                "expected_history_count": "0",
                "booking_status": "Confirmed",
                "payment_status": "Deposit Paid",
            },
        )
        self.assertEqual(invalid_payment.status_code, 302)
        with self.app.app_context():
            booking = self.db.session.get(self.TripBooking, "B-FU-1")
            self.assertEqual(booking.booking_status, "Confirmed")
            self.assertEqual(booking.payment_status, "Fully Paid")

    def test_concurrent_booking_update_conflict_is_detected(self) -> None:
        token = self._login()
        with self.app.app_context():
            self.db.session.add(
                self.BookingStatusHistory(
                    booking_id="B-FU-1",
                    old_status="Confirmed",
                    new_status="Payment Pending",
                    changed_at=datetime.now(timezone.utc),
                )
            )
            self.db.session.commit()
        response = self.client.post(
            "/bookings/B-FU-1/status",
            data={
                "csrf_token": token,
                "expected_history_count": "0",
                "booking_status": "Payment Pending",
                "payment_status": "Pending",
            },
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            booking = self.db.session.get(self.TripBooking, "B-FU-1")
            self.assertEqual(booking.booking_status, "Confirmed")

    def test_lead_detail_displays_followup_summary_and_overdue(self) -> None:
        self._login()
        response = self.client.get("/leads/L-FU-1")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Employee Follow-up", body)
        self.assertIn("Assigned Employee", body)
        self.assertIn("Overdue", body)

    def test_quick_action_uses_backend_verified_transition(self) -> None:
        token = self._login()
        response = self.client.post(
            "/leads/L-FU-1/quick-action",
            json={"action": "mark_contacted"},
            headers={"Accept": "application/json", "X-CSRF-Token": token},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")
        with self.app.app_context():
            lead = self.db.session.get(self.Lead, "L-FU-1")
            self.assertEqual(lead.lead_stage, "Contacted")
            self.assertEqual(lead.follow_up_status, "Contacted")
            event = self.BookingEventTrail.query.filter_by(lead_id="L-FU-1", event_type="lead_quick_action").first()
            self.assertIsNotNone(event)


if __name__ == "__main__":
    unittest.main()
