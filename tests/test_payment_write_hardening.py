"""Two live gaps found while auditing for the payments ledger.

1. Money had no conflict detection. The booking form's
   `expected_history_count` guard counts booking_status_history rows, and
   those are only written when booking_status changes -- so two employees
   editing a refund at the same time both passed it and the later save
   silently overwrote the earlier one.

2. UnifiedCRMService.update_booking_status applied payment statuses with no
   validation whatsoever, so it could move a booking backwards from Fully
   Paid to Deposit Paid -- something the CRM route has always refused.

Both are independent of the ledger design; they are wrong today.
"""
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
    from app.models.traveler import Traveler
    from app.models.trip import Trip
    from app.services import booking_audit
    from services.crm.system_services import UnifiedCRMService, payment_rules

    return db, TripBooking, Traveler, Trip, booking_audit, UnifiedCRMService, payment_rules


class PaymentWriteHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(".tmp-test-workdirs") / f"pay-harden-{uuid.uuid4().hex}"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tmpdir / "app.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{self.db_path.resolve().as_posix()}"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(self.db_path)
        os.environ["CRM_AUTH_ENABLED"] = "false"
        self.app = _create_temp_app()
        (
            self.db, self.TripBooking, self.Traveler, self.Trip,
            self.booking_audit, UnifiedCRMService, self.payment_rules,
        ) = _load_app_objects()
        self.app.config["TESTING"] = True
        with self.app.app_context():
            self.db.drop_all()
            self.db.create_all()
            self.db.session.add(
                self.Traveler(
                    traveler_id="TR100",
                    full_name="Hardening Traveler",
                    integrated_whatsapp="20:1000000000",
                    normalized_whatsapp="+201000000000",
                    phone_lookup_key="20:1000000000",
                )
            )
            self.db.session.add(
                self.Trip(
                    trip_id="TRIP-100",
                    trip_name="Hardening Trip",
                    type="Local",
                    sales_status="Open",
                    single_total=5, double_total=5, triple_total=5,
                    single_remaining=5, double_remaining=5, triple_remaining=5,
                    draft_holds_single=0, draft_holds_double=0, draft_holds_triple=0,
                    public_price="1000",
                )
            )
            self.db.session.commit()
        self.client = self.app.test_client()
        self.service = UnifiedCRMService()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("RAHMA_SYSTEM_DB_PATH", None)

    # -- helpers ---------------------------------------------------------

    def _seed(self, booking_id="B-HD-1", **overrides):
        fields = dict(
            booking_id=booking_id,
            trip_id="TRIP-100",
            trip_name="Hardening Trip",
            traveler_id="TR100",
            traveler_name="Hardening Traveler",
            room_type="Double",
            currency="USD",
            group_size=1,
            booking_status="Confirmed",
            booking_source="Admin",
            payment_status="Fully Paid",
            priority="Medium",
            missing_info=False,
        )
        fields.update(overrides)
        with self.app.app_context():
            with self.booking_audit.suspend_booking_audit():
                self.db.session.add(self.TripBooking(**fields))
                self.db.session.commit()
        return booking_id

    def _state(self, booking_id="B-HD-1"):
        with self.app.app_context():
            booking = self.db.session.get(self.TripBooking, booking_id)
            return booking.payment_status, booking.refund_amount

    def _token(self, booking_id="B-HD-1"):
        status, refund = self._state(booking_id)
        return self.payment_rules.payment_state_token(status, refund)

    # === Gap 1: concurrent money edits ==================================

    def test_two_employees_refunding_at_once_no_longer_overwrite_each_other(self) -> None:
        """The reported scenario. Both employees load the page, so both hold
        the same token. The first save wins; the second is told to reload
        rather than silently replacing the first employee's figure."""
        self._seed()
        shared_token = self._token()

        first = self.client.post(
            "/bookings/B-HD-1/status",
            data={
                "payment_status": "Partial Refund",
                "refund_amount": "400",
                "expected_payment_state": shared_token,
            },
        )
        self.assertEqual(first.status_code, 302)
        self.assertEqual(self._state(), ("Partial Refund", 400.0))

        second = self.client.post(
            "/bookings/B-HD-1/status",
            data={
                "payment_status": "Partial Refund",
                "refund_amount": "250",
                "expected_payment_state": shared_token,
            },
            follow_redirects=True,
        )
        self.assertEqual(second.status_code, 200)
        self.assertIn("changed while you were editing", second.get_data(as_text=True))
        # The first employee's 400 survives.
        self.assertEqual(self._state(), ("Partial Refund", 400.0))

    def test_the_old_history_guard_could_not_catch_this(self) -> None:
        """Documents precisely why a second token was needed: a refund-only
        save writes no booking_status_history row, so the count is identical
        before and after."""
        from app.models.booking_status_history import BookingStatusHistory

        self._seed()
        with self.app.app_context():
            before = BookingStatusHistory.query.filter_by(booking_id="B-HD-1").count()

        self.client.post(
            "/bookings/B-HD-1/status",
            data={"payment_status": "Partial Refund", "refund_amount": "400"},
        )

        with self.app.app_context():
            after = BookingStatusHistory.query.filter_by(booking_id="B-HD-1").count()
        self.assertEqual(before, after)
        self.assertEqual(self._state(), ("Partial Refund", 400.0))

    def test_a_fresh_token_saves_normally(self) -> None:
        self._seed()
        response = self.client.post(
            "/bookings/B-HD-1/status",
            data={
                "payment_status": "Partial Refund",
                "refund_amount": "120",
                "expected_payment_state": self._token(),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._state(), ("Partial Refund", 120.0))

    def test_a_stale_token_is_rejected_before_anything_is_written(self) -> None:
        self._seed()
        response = self.client.post(
            "/bookings/B-HD-1/status",
            data={
                "payment_status": "Partial Refund",
                "refund_amount": "300",
                "booking_notes": "should not be saved either",
                "expected_payment_state": "Fully Paid|999.00",
            },
            follow_redirects=True,
        )
        self.assertIn("changed while you were editing", response.get_data(as_text=True))
        with self.app.app_context():
            booking = self.db.session.get(self.TripBooking, "B-HD-1")
            self.assertEqual(booking.payment_status, "Fully Paid")
            self.assertIsNone(booking.refund_amount)
            self.assertNotIn("should not be saved", booking.booking_notes or "")

    def test_a_json_client_omitting_the_token_still_works(self) -> None:
        """Backwards compatible: the guard only fires when a token is sent,
        exactly like expected_history_count.

        Also covers a crash this test found on the way in -- a JSON client
        sends refund_amount as a number, and the parser assumed a string, so
        a perfectly valid API call returned 500.
        """
        self._seed()
        response = self.client.post(
            "/bookings/B-HD-1/status",
            json={"payment_status": "Partial Refund", "refund_amount": 120},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._state(), ("Partial Refund", 120.0))

    def test_a_json_client_can_send_a_fractional_refund_amount(self) -> None:
        self._seed()
        response = self.client.post(
            "/bookings/B-HD-1/status",
            json={"payment_status": "Partial Refund", "refund_amount": 120.55},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._state(), ("Partial Refund", 120.55))

    def test_a_json_boolean_refund_amount_is_rejected_not_coerced(self) -> None:
        """`True` is an int in Python, so it would otherwise silently become a
        refund of 1.00."""
        self._seed()
        response = self.client.post(
            "/bookings/B-HD-1/status",
            json={"payment_status": "Partial Refund", "refund_amount": True},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._state(), ("Fully Paid", None))

    def test_a_stale_token_returns_409_for_a_json_client(self) -> None:
        self._seed()
        response = self.client.post(
            "/bookings/B-HD-1/status",
            json={
                "payment_status": "Partial Refund",
                "refund_amount": 120,
                "expected_payment_state": "Pending|none",
            },
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("changed while you were editing", response.get_json()["error"])

    def test_the_booking_page_issues_a_token_matching_current_state(self) -> None:
        self._seed(payment_status="Partial Refund", refund_amount=120.0)
        body = self.client.get("/bookings/B-HD-1").get_data(as_text=True)
        self.assertIn('name="expected_payment_state"', body)
        self.assertIn("Partial Refund|120.00", body)

    def test_a_malformed_history_count_is_not_reported_as_a_concurrent_edit(self) -> None:
        """It is a broken client, and saying "another employee changed this"
        sent people to reload a page that was never stale."""
        self._seed()
        response = self.client.post(
            "/bookings/B-HD-1/status",
            data={"booking_status": "Completed", "expected_history_count": "not-a-number"},
            follow_redirects=True,
        )
        body = response.get_data(as_text=True)
        self.assertIn("Could not verify this booking was up to date", body)
        self.assertNotIn("updated by another employee", body)

    def test_a_genuine_history_conflict_still_reports_a_concurrent_edit(self) -> None:
        self._seed()
        response = self.client.post(
            "/bookings/B-HD-1/status",
            data={"booking_status": "Completed", "expected_history_count": "99"},
            follow_redirects=True,
        )
        self.assertIn("updated by another employee", response.get_data(as_text=True))

    # === Gap 2: the unguarded service write path ========================

    def test_the_service_path_now_refuses_a_backwards_payment_change(self) -> None:
        """Fully Paid -> Deposit Paid is not in PAYMENT_TRANSITIONS. The CRM
        route has always refused it; this path used to allow it silently."""
        self._seed(booking_status="Draft", payment_status="Fully Paid")
        with self.assertRaises(ValueError) as ctx:
            self.service.update_booking_status(
                "B-HD-1", new_status="Cancelled", new_payment_status="Deposit Paid"
            )
        self.assertIn("Invalid payment status transition", str(ctx.exception))

    def test_a_refused_service_change_writes_nothing_at_all(self) -> None:
        self._seed(booking_status="Draft", payment_status="Fully Paid")
        with self.assertRaises(ValueError):
            self.service.update_booking_status(
                "B-HD-1", new_status="Cancelled", new_payment_status="Deposit Paid"
            )
        # The booking status change in the same call must not have leaked
        # through -- validation happens before any write.
        with self.app.app_context():
            booking = self.db.session.get(self.TripBooking, "B-HD-1")
            self.assertEqual(booking.booking_status, "Draft")
            self.assertEqual(booking.payment_status, "Fully Paid")

    def test_the_service_path_still_allows_a_legal_payment_change(self) -> None:
        self._seed(booking_status="Draft", payment_status="Pending")
        result = self.service.update_booking_status(
            "B-HD-1", new_status="Cancelled", new_payment_status="Fully Paid"
        )
        self.assertEqual(result["payment_status"], "Fully Paid")

    def test_the_service_path_honours_the_written_reason_override(self) -> None:
        """Same escape hatch the route offers, so this is not a dead end for
        a genuine correction."""
        self._seed(booking_status="Draft", payment_status="Fully Paid")
        result = self.service.update_booking_status(
            "B-HD-1",
            new_payment_status="Deposit Paid",
            notes="Bank reversed the second instalment; correcting the record.",
            allow_employee_correction=True,
        )
        self.assertEqual(result["payment_status"], "Deposit Paid")

    def test_the_override_still_demands_a_reason(self) -> None:
        self._seed(booking_status="Draft", payment_status="Fully Paid")
        with self.assertRaises(ValueError) as ctx:
            self.service.update_booking_status(
                "B-HD-1", new_payment_status="Deposit Paid", allow_employee_correction=True
            )
        self.assertIn("Add a reason", str(ctx.exception))

    # === One definition of the vocabulary ===============================

    def test_the_route_and_the_service_share_one_definition(self) -> None:
        from app.routes import bookings as bookings_route

        self.assertIs(bookings_route.PAYMENT_STATUSES, self.payment_rules.PAYMENT_STATUSES)
        self.assertIs(bookings_route.PAYMENT_TRANSITIONS, self.payment_rules.PAYMENT_TRANSITIONS)
        self.assertIs(bookings_route.REFUND_PAYMENT_STATUSES, self.payment_rules.REFUND_PAYMENT_STATUSES)

    def test_revenue_recognition_derives_its_refund_set_from_the_same_place(self) -> None:
        """These were three hand-maintained copies of one list. Deriving means
        a new refund spelling cannot be added to the route while revenue
        recognition quietly keeps ignoring it."""
        from app.services import revenue

        self.assertEqual(
            revenue.REFUNDED_PAYMENT_STATUSES,
            {status.lower() for status in self.payment_rules.REFUND_PAYMENT_STATUSES},
        )

    def test_the_template_takes_its_refund_list_from_the_server(self) -> None:
        self._seed()
        body = self.client.get("/bookings/B-HD-1").get_data(as_text=True)
        for status in self.payment_rules.REFUND_PAYMENT_STATUSES:
            self.assertIn(status, body)
        self.assertIn("var REFUND_STATUSES = [", body)

    def test_payment_state_token_is_stable_and_distinguishing(self) -> None:
        token = self.payment_rules.payment_state_token
        self.assertEqual(token("Fully Paid", None), token("Fully Paid", None))
        self.assertNotEqual(token("Fully Paid", None), token("Fully Paid", 0.0))
        self.assertNotEqual(token("Partial Refund", 120.0), token("Partial Refund", 150.0))
        self.assertEqual(token("Partial Refund", 120), token("Partial Refund", 120.00))
        self.assertEqual(token(None, None), "Pending|none")
        self.assertEqual(token("  Fully Paid  ", None), "Fully Paid|none")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
