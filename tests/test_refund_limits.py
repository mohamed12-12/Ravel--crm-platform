"""Phase 2: a refund can never exceed what the traveler paid.

Two facts about the existing financial model drive what these tests assert,
and both are verified here rather than assumed:

* The CRM records no amount-paid figure -- only a categorical payment_status
  -- so "Fully Paid" is the one status whose paid amount is knowable. For
  every other status the booking's total value is used as a sound upper bound.
* ``refund_amount`` is a single overwritten scalar, i.e. a running total
  rather than an instalment, so the invariant enforced is
  ``total refunded <= amount paid``.

See apps/api/app/services/refund_limits.py for the reasoning in full.
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
    from app.models.booking_event import BookingEventTrail
    from app.models.traveler import Traveler
    from app.models.trip import Trip
    from app.services import booking_audit, refund_limits

    return db, TripBooking, BookingEventTrail, Traveler, Trip, booking_audit, refund_limits


class RefundLimitTests(unittest.TestCase):
    """Booking value is USD 500 per person x 2 people = USD 1,000."""

    def setUp(self) -> None:
        self.tmpdir = Path(".tmp-test-workdirs") / f"refund-limits-{uuid.uuid4().hex}"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tmpdir / "app.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{self.db_path.resolve().as_posix()}"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(self.db_path)
        os.environ["CRM_AUTH_ENABLED"] = "false"
        self.app = _create_temp_app()
        (
            self.db,
            self.TripBooking,
            self.BookingEventTrail,
            self.Traveler,
            self.Trip,
            self.booking_audit,
            self.refund_limits,
        ) = _load_app_objects()
        self.app.config["TESTING"] = True
        with self.app.app_context():
            self.db.drop_all()
            self.db.create_all()
            self.db.session.add(
                self.Traveler(
                    traveler_id="TR100",
                    full_name="Refund Traveler",
                    integrated_whatsapp="20:1000000000",
                    normalized_whatsapp="+201000000000",
                    phone_lookup_key="20:1000000000",
                )
            )
            self.db.session.add(
                self.Trip(
                    trip_id="TRIP-100",
                    trip_name="Refund Trip",
                    type="Local",
                    sales_status="Open",
                    single_total=5, double_total=5, triple_total=5,
                    single_remaining=5, double_remaining=5, triple_remaining=5,
                    draft_holds_single=0, draft_holds_double=0, draft_holds_triple=0,
                    public_price="$500",
                    room_prices_json='{"Single": {"USD": "500", "EGP": "25000"},'
                                     ' "Double": {"USD": "500", "EGP": "25000"},'
                                     ' "Triple": {"USD": "500", "EGP": "25000"}}',
                )
            )
            self.db.session.add(
                self.Trip(
                    trip_id="TRIP-UNPRICED",
                    trip_name="Unpriced Trip",
                    type="Local",
                    sales_status="Open",
                    single_total=5, double_total=5, triple_total=5,
                    single_remaining=5, double_remaining=5, triple_remaining=5,
                    draft_holds_single=0, draft_holds_double=0, draft_holds_triple=0,
                    public_price="",
                )
            )
            self.db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("RAHMA_SYSTEM_DB_PATH", None)

    # -- helpers ---------------------------------------------------------

    def _seed_booking(self, booking_id: str = "B-RF-1", **overrides):
        fields = dict(
            booking_id=booking_id,
            trip_id="TRIP-100",
            trip_name="Refund Trip",
            traveler_id="TR100",
            traveler_name="Refund Traveler",
            room_type="Double",
            currency="USD",
            group_size=2,
            booking_status="Draft",
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

    def _post_refund(self, amount, booking_id="B-RF-1", payment_status="Partial Refund"):
        return self.client.post(
            f"/bookings/{booking_id}/status",
            data={"payment_status": payment_status, "refund_amount": str(amount)},
            follow_redirects=True,
        )

    def _stored_refund(self, booking_id="B-RF-1"):
        with self.app.app_context():
            booking = self.db.session.get(self.TripBooking, booking_id)
            return booking.refund_amount, booking.payment_status

    def _refund_events(self, booking_id="B-RF-1"):
        with self.app.app_context():
            return self.BookingEventTrail.query.filter_by(
                booking_id=booking_id, event_type="refund_updated"
            ).all()

    # -- the ceiling itself ----------------------------------------------

    def test_refund_below_the_maximum_succeeds(self) -> None:
        self._seed_booking()
        response = self._post_refund(400)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._stored_refund(), (400.0, "Partial Refund"))

    def test_refund_exactly_equal_to_the_maximum_succeeds(self) -> None:
        """The boundary is inclusive -- a full refund of everything paid is
        the most ordinary refund there is."""
        self._seed_booking()
        response = self._post_refund(1000, payment_status="Full Refund")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._stored_refund(), (1000.0, "Full Refund"))

    def test_refund_above_the_maximum_is_rejected(self) -> None:
        self._seed_booking()
        response = self._post_refund(1000.01)
        self.assertEqual(response.status_code, 200)
        self.assertIn("cannot exceed", response.get_data(as_text=True))
        self.assertEqual(self._stored_refund(), (None, "Fully Paid"))

    def test_refund_far_above_the_maximum_is_rejected(self) -> None:
        self._seed_booking()
        self._post_refund(999999)
        self.assertEqual(self._stored_refund(), (None, "Fully Paid"))

    def test_the_ceiling_accounts_for_group_size(self) -> None:
        """USD 500 per person: a party of four may be refunded USD 2,000."""
        self._seed_booking(group_size=4)
        self._post_refund(2000, payment_status="Full Refund")
        self.assertEqual(self._stored_refund()[0], 2000.0)

        self._seed_booking(booking_id="B-RF-SOLO", group_size=1)
        self._post_refund(2000, booking_id="B-RF-SOLO")
        self.assertEqual(self._stored_refund("B-RF-SOLO")[0], None)

    # -- cumulative behaviour --------------------------------------------

    def test_successive_refunds_are_capped_by_the_total_not_the_increment(self) -> None:
        """refund_amount is one overwritten scalar, so each save states the
        running total. Recording 200 then 500 means 500 has been refunded in
        all -- and the cap applies to that total."""
        self._seed_booking()
        self._post_refund(200)
        self.assertEqual(self._stored_refund()[0], 200.0)

        self._post_refund(500)
        self.assertEqual(self._stored_refund()[0], 500.0)

        self._post_refund(1000)
        self.assertEqual(self._stored_refund()[0], 1000.0)

        self._post_refund(1001)
        self.assertEqual(self._stored_refund()[0], 1000.0)

    def test_refund_after_a_full_refund_cannot_go_higher(self) -> None:
        self._seed_booking()
        self._post_refund(1000, payment_status="Full Refund")
        response = self._post_refund(1200, payment_status="Full Refund")
        self.assertIn("cannot exceed", response.get_data(as_text=True))
        self.assertEqual(self._stored_refund()[0], 1000.0)

    def test_error_names_the_remaining_amount_once_something_is_refunded(self) -> None:
        self._seed_booking(payment_status="Partial Refund", refund_amount=400.0)
        response = self._post_refund(1500, payment_status="Partial Refund")
        body = response.get_data(as_text=True)
        self.assertIn("USD 1,000.00", body)      # the ceiling
        self.assertIn("USD 400.00", body)        # already refunded
        self.assertIn("USD 600.00", body)        # remaining headroom

    # -- amount paid vs booking value ------------------------------------

    def test_fully_paid_bookings_are_capped_by_the_amount_recorded_as_paid(self) -> None:
        self._seed_booking(payment_status="Fully Paid")
        with self.app.app_context():
            booking = self.db.session.get(self.TripBooking, "B-RF-1")
            trip = self.db.session.get(self.Trip, "TRIP-100")
            allowance = self.refund_limits.refund_allowance_for_booking(booking, trip)
        self.assertTrue(allowance.amount_paid_is_recorded)
        self.assertEqual(allowance.amount_paid, 1000.0)
        self.assertEqual(allowance.maximum_refund, 1000.0)
        self.assertEqual(allowance.basis, self.refund_limits.BASIS_RECORDED_PAID)

    def test_deposit_paid_falls_back_to_the_booking_value_as_an_upper_bound(self) -> None:
        """No deposit amount is stored anywhere in this system, so the paid
        figure is genuinely unknown. The booking's value is still a sound
        bound: nobody can have paid more than the booking costs."""
        self._seed_booking(payment_status="Deposit Paid")
        with self.app.app_context():
            booking = self.db.session.get(self.TripBooking, "B-RF-1")
            trip = self.db.session.get(self.Trip, "TRIP-100")
            allowance = self.refund_limits.refund_allowance_for_booking(booking, trip)
        self.assertFalse(allowance.amount_paid_is_recorded)
        self.assertIsNone(allowance.amount_paid)
        self.assertEqual(allowance.maximum_refund, 1000.0)
        self.assertEqual(allowance.basis, self.refund_limits.BASIS_BOOKING_VALUE)

    def test_error_says_which_figure_was_exceeded(self) -> None:
        self._seed_booking(payment_status="Deposit Paid")
        response = self._post_refund(5000)
        self.assertIn("the total value of this booking", response.get_data(as_text=True))

        self._seed_booking(booking_id="B-RF-PAID", payment_status="Fully Paid")
        response = self._post_refund(5000, booking_id="B-RF-PAID")
        self.assertIn("the amount recorded as paid", response.get_data(as_text=True))

    def test_unpriceable_booking_is_not_blocked_by_a_ceiling_of_zero(self) -> None:
        """A booking with no usable price must stay refundable -- refusing
        every refund because the CRM cannot value the trip would be a worse
        failure than the one this phase is preventing."""
        self._seed_booking(trip_id="TRIP-UNPRICED", trip_name="Unpriced Trip")
        with self.app.app_context():
            booking = self.db.session.get(self.TripBooking, "B-RF-1")
            trip = self.db.session.get(self.Trip, "TRIP-UNPRICED")
            allowance = self.refund_limits.refund_allowance_for_booking(booking, trip)
        self.assertIsNone(allowance.maximum_refund)
        self.assertFalse(allowance.is_enforceable)

        self._post_refund(750)
        self.assertEqual(self._stored_refund()[0], 750.0)

    def test_booking_with_no_trip_has_no_enforceable_ceiling(self) -> None:
        self._seed_booking(trip_id=None, trip_name=None)
        self._post_refund(750)
        self.assertEqual(self._stored_refund()[0], 750.0)

    def test_currency_is_taken_from_the_booking(self) -> None:
        """EGP 25,000 per person x 2 = EGP 50,000."""
        self._seed_booking(currency="EGP")
        response = self._post_refund(60000)
        self.assertIn("EGP 50,000.00", response.get_data(as_text=True))
        self.assertEqual(self._stored_refund()[0], None)

        self._post_refund(50000, payment_status="Full Refund")
        self.assertEqual(self._stored_refund()[0], 50000.0)

    # -- malformed input --------------------------------------------------

    def test_negative_refund_is_rejected(self) -> None:
        self._seed_booking()
        response = self._post_refund(-50)
        self.assertIn("cannot be negative", response.get_data(as_text=True))
        self.assertEqual(self._stored_refund()[0], None)

    def test_zero_refund_is_rejected(self) -> None:
        self._seed_booking()
        response = self._post_refund(0)
        self.assertIn("Enter the refund amount", response.get_data(as_text=True))
        self.assertEqual(self._stored_refund()[0], None)

    def test_non_numeric_refund_is_rejected(self) -> None:
        self._seed_booking()
        response = self._post_refund("abc")
        self.assertIn("must be a valid number", response.get_data(as_text=True))
        self.assertEqual(self._stored_refund()[0], None)

    def test_nan_and_infinity_are_rejected(self) -> None:
        """float() accepts both. NaN is the dangerous one: every comparison
        against it is False, so it would slip past the negative check and the
        ceiling check alike and land in the database."""
        self._seed_booking()
        for literal in ("nan", "inf", "-inf", "Infinity"):
            with self.subTest(literal=literal):
                response = self._post_refund(literal)
                self.assertIn("must be a valid number", response.get_data(as_text=True))
                self.assertEqual(self._stored_refund()[0], None)

    # -- the timeline -----------------------------------------------------

    def test_a_successful_refund_records_exactly_one_timeline_event(self) -> None:
        self._seed_booking()
        self._post_refund(400)
        events = self._refund_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_label, "Refund recorded")

    def test_a_rejected_refund_records_no_refund_event(self) -> None:
        self._seed_booking()
        self._post_refund(999999)
        self.assertEqual(self._refund_events(), [])
        with self.app.app_context():
            self.assertEqual(
                self.BookingEventTrail.query.filter_by(booking_id="B-RF-1").count(), 0
            )

    def test_a_rejected_refund_leaves_the_payment_status_untouched(self) -> None:
        self._seed_booking(payment_status="Fully Paid")
        self._post_refund(999999, payment_status="Full Refund")
        self.assertEqual(self._stored_refund(), (None, "Fully Paid"))

    # -- backward compatibility -------------------------------------------

    def test_a_legacy_over_ceiling_refund_is_left_alone(self) -> None:
        """Historical data is evidence, not a bug to be corrected. An edit
        that does not touch the refund must still go through."""
        self._seed_booking(payment_status="Partial Refund", refund_amount=99999.0)
        response = self.client.post(
            "/bookings/B-RF-1/status",
            data={"booking_notes": "Chasing the customer for documents."},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._stored_refund()[0], 99999.0)

    def test_a_legacy_over_ceiling_refund_is_challenged_when_edited(self) -> None:
        self._seed_booking(payment_status="Partial Refund", refund_amount=99999.0)
        response = self._post_refund(88888)
        self.assertIn("cannot exceed", response.get_data(as_text=True))
        self.assertEqual(self._stored_refund()[0], 99999.0)

    def test_a_legacy_over_ceiling_refund_can_be_corrected_downwards(self) -> None:
        self._seed_booking(payment_status="Partial Refund", refund_amount=99999.0)
        self._post_refund(300)
        self.assertEqual(self._stored_refund()[0], 300.0)

    def test_exceeds_allowance_reports_legacy_breaches_without_changing_them(self) -> None:
        self._seed_booking(booking_id="B-RF-BAD", payment_status="Partial Refund", refund_amount=99999.0)
        self._seed_booking(booking_id="B-RF-OK", payment_status="Partial Refund", refund_amount=250.0)
        with self.app.app_context():
            trip = self.db.session.get(self.Trip, "TRIP-100")
            bad = self.db.session.get(self.TripBooking, "B-RF-BAD")
            ok = self.db.session.get(self.TripBooking, "B-RF-OK")
            self.assertTrue(self.refund_limits.exceeds_allowance(bad, trip))
            self.assertFalse(self.refund_limits.exceeds_allowance(ok, trip))
            self.assertEqual(bad.refund_amount, 99999.0)

    # -- the booking page --------------------------------------------------

    def test_the_booking_page_shows_the_maximum_refundable(self) -> None:
        self._seed_booking()
        page = self.client.get("/bookings/B-RF-1")
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn("Maximum USD 1,000.00", body)
        self.assertIn("the amount recorded as paid", body)

    def test_the_booking_page_shows_remaining_headroom_after_a_refund(self) -> None:
        self._seed_booking(payment_status="Partial Refund", refund_amount=400.0)
        body = self.client.get("/bookings/B-RF-1").get_data(as_text=True)
        self.assertIn("USD 400.00 already recorded", body)
        self.assertIn("leaving USD 600.00", body)

    # -- the pure allowance calculation ------------------------------------

    def test_remaining_refundable_is_paid_minus_already_refunded(self) -> None:
        """The relationship the whole phase rests on, checked directly."""
        allowance = self.refund_limits.refund_allowance(
            trip={"public_price": "700", "room_prices_json": ""},
            room_type="Double",
            currency="USD",
            group_size=1,
            payment_status="Fully Paid",
            already_refunded=100.0,
        )
        self.assertEqual(allowance.amount_paid, 700.0)
        self.assertEqual(allowance.already_refunded, 100.0)
        self.assertEqual(allowance.remaining_refundable, 600.0)
        self.assertEqual(
            allowance.remaining_refundable,
            allowance.amount_paid - allowance.already_refunded,
        )

    def test_remaining_refundable_never_goes_negative(self) -> None:
        allowance = self.refund_limits.refund_allowance(
            trip={"public_price": "300", "room_prices_json": ""},
            room_type="Double", currency="USD", group_size=1,
            payment_status="Fully Paid", already_refunded=300.0,
        )
        self.assertEqual(allowance.remaining_refundable, 0.0)

        over = self.refund_limits.refund_allowance(
            trip={"public_price": "300", "room_prices_json": ""},
            room_type="Double", currency="USD", group_size=1,
            payment_status="Fully Paid", already_refunded=5000.0,
        )
        self.assertEqual(over.remaining_refundable, 0.0)

    def test_unrecognised_currency_yields_no_ceiling(self) -> None:
        allowance = self.refund_limits.refund_allowance(
            trip={"public_price": "300", "room_prices_json": ""},
            room_type="Double", currency="GBP", group_size=1,
            payment_status="Fully Paid",
        )
        self.assertIsNone(allowance.maximum_refund)
        self.assertEqual(allowance.basis, self.refund_limits.BASIS_UNKNOWN)

    def test_validate_refund_total_does_not_clamp(self) -> None:
        """The employee must retype a correct figure; the CRM never quietly
        substitutes the maximum for what they entered."""
        allowance = self.refund_limits.refund_allowance(
            trip={"public_price": "300", "room_prices_json": ""},
            room_type="Double", currency="USD", group_size=1,
            payment_status="Fully Paid",
        )
        with self.assertRaises(ValueError):
            self.refund_limits.validate_refund_total(400.0, allowance)
        self.assertEqual(allowance.maximum_refund, 300.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
