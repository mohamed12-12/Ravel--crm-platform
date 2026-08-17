"""Phase 3: a refund reduces revenue, it does not erase the booking.

The bug: revenue recognition gated on REVENUE_PAYMENT_STATUSES
({"fully paid", "paid"}), so the moment a booking's payment status became any
refund it stopped matching and its entire value vanished. A 1,000 booking with
a 120 refund reported 0 revenue, and a partial refund was indistinguishable
from a full one.

The model now is Gross - Refunds = Net, computed in exactly one place
(services/crm/system_services/revenue_rules.py) and consumed by every revenue
surface, so Lifetime Revenue, the traveler profile and Revenue Analytics
cannot disagree.
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
    from app.services import booking_audit, revenue
    from services.crm.system_services import UnifiedCRMService

    return db, TripBooking, BookingEventTrail, Traveler, Trip, booking_audit, revenue, UnifiedCRMService


class RevenueNetOfRefundsTests(unittest.TestCase):
    """TRIP-1000 is priced at 1,000 per person in both currencies, so a
    single-traveller booking is worth exactly 1,000 -- the figure used
    throughout the brief."""

    def setUp(self) -> None:
        self.tmpdir = Path(".tmp-test-workdirs") / f"revenue-net-{uuid.uuid4().hex}"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tmpdir / "app.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{self.db_path.resolve().as_posix()}"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(self.db_path)
        os.environ["CRM_AUTH_ENABLED"] = "false"
        self.app = _create_temp_app()
        (
            self.db, self.TripBooking, self.BookingEventTrail, self.Traveler,
            self.Trip, self.booking_audit, self.revenue, UnifiedCRMService,
        ) = _load_app_objects()
        self.app.config["TESTING"] = True
        with self.app.app_context():
            self.db.drop_all()
            self.db.create_all()
            self.db.session.add(
                self.Traveler(
                    traveler_id="TR100",
                    full_name="Net Revenue Traveler",
                    integrated_whatsapp="20:1000000000",
                    normalized_whatsapp="+201000000000",
                    phone_lookup_key="20:1000000000",
                )
            )
            self.db.session.add(
                self.Trip(
                    trip_id="TRIP-1000",
                    trip_name="Thousand Trip",
                    type="Local",
                    sales_status="Open",
                    single_total=9, double_total=9, triple_total=9,
                    single_remaining=9, double_remaining=9, triple_remaining=9,
                    draft_holds_single=0, draft_holds_double=0, draft_holds_triple=0,
                    public_price="1000",
                    room_prices_json='{"Single": {"USD": "1000", "EGP": "1000"},'
                                     ' "Double": {"USD": "1000", "EGP": "1000"},'
                                     ' "Triple": {"USD": "1000", "EGP": "1000"}}',
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

    def _seed_booking(self, booking_id="B-NR-1", **overrides):
        fields = dict(
            booking_id=booking_id,
            trip_id="TRIP-1000",
            trip_name="Thousand Trip",
            traveler_id="TR100",
            traveler_name="Net Revenue Traveler",
            room_type="Double",
            currency="USD",
            group_size=1,
            booking_status="Completed",
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

    def _breakdown(self, booking_id="B-NR-1"):
        with self.app.app_context():
            booking = self.db.session.get(self.TripBooking, booking_id)
            trip = self.db.session.get(self.Trip, booking.trip_id) if booking.trip_id else None
            return self.revenue.booking_revenue_breakdown(booking, trip)

    def _lifetime_revenue(self, traveler_id="TR100"):
        with self.app.app_context():
            from app.services.traveler_stats import recalculate_traveler_stats

            recalculate_traveler_stats(traveler_id)
            return self.db.session.get(self.Traveler, traveler_id).lifetime_revenue

    # === THE REPORTED BUG ==============================================

    def test_thousand_booking_with_a_one_twenty_refund_is_eight_eighty_not_zero(self) -> None:
        """The headline case from the brief, asserted end to end.

            Booking = 1,000
            Refund  =   120
            Net     =   880   (was 0)
        """
        self._seed_booking(payment_status="Partial Refund", refund_amount=120.0)

        breakdown = self._breakdown()
        self.assertIsNotNone(breakdown, "a partially refunded booking must still be revenue")
        self.assertEqual(breakdown.gross, 1000.0)
        self.assertEqual(breakdown.refunds, 120.0)
        self.assertEqual(breakdown.net, 880.0)
        self.assertNotEqual(breakdown.net, 0.0)

        # ...and the same 880 reaches every surface that reports revenue.
        self.assertEqual(self._lifetime_revenue(), 880.0)

        profile = self.client.get("/travelers/TR100")
        self.assertEqual(profile.status_code, 200)
        self.assertIn("$880.00", profile.get_data(as_text=True))

        analytics = self.client.get("/admin/revenue-analytics")
        self.assertEqual(analytics.status_code, 200)
        self.assertIn("880.00", analytics.get_data(as_text=True))

    def test_thousand_booking_fully_refunded_nets_zero(self) -> None:
        self._seed_booking(payment_status="Full Refund", refund_amount=1000.0)
        breakdown = self._breakdown()
        self.assertEqual(breakdown.gross, 1000.0)
        self.assertEqual(breakdown.refunds, 1000.0)
        self.assertEqual(breakdown.net, 0.0)
        self.assertEqual(self._lifetime_revenue(), 0.0)

    def test_partial_and_full_refunds_are_no_longer_the_same_number(self) -> None:
        """Before this change both produced 0, which is what made the bug so
        easy to miss: the dashboard looked plausible either way."""
        self._seed_booking(booking_id="B-NR-PART", payment_status="Partial Refund", refund_amount=250.0)
        self._seed_booking(booking_id="B-NR-FULL", payment_status="Full Refund", refund_amount=1000.0)
        self.assertEqual(self._breakdown("B-NR-PART").net, 750.0)
        self.assertEqual(self._breakdown("B-NR-FULL").net, 0.0)

    # === THE ARITHMETIC =================================================

    def test_no_refund_nets_the_full_booking_value(self) -> None:
        self._seed_booking()
        breakdown = self._breakdown()
        self.assertEqual((breakdown.gross, breakdown.refunds, breakdown.net), (1000.0, 0.0, 1000.0))

    def test_zero_refund_behaves_as_no_refund(self) -> None:
        self._seed_booking(refund_amount=0.0)
        self.assertEqual(self._breakdown().net, 1000.0)

    def test_a_range_of_partial_refunds(self) -> None:
        for refund, expected_net in ((120.0, 880.0), (500.0, 500.0), (1000.0, 0.0), (0.0, 1000.0)):
            with self.subTest(refund=refund):
                booking_id = f"B-NR-{refund}"
                self._seed_booking(
                    booking_id=booking_id, payment_status="Partial Refund", refund_amount=refund
                )
                breakdown = self._breakdown(booking_id)
                self.assertEqual(breakdown.net, expected_net)
                self.assertEqual(breakdown.gross - breakdown.refunds, breakdown.net)

    def test_refund_is_subtracted_exactly_once(self) -> None:
        """Two travellers' worth of value, one refund. Net must be 2,000 - 300,
        not 2,000 - 600."""
        self._seed_booking(group_size=2, payment_status="Partial Refund", refund_amount=300.0)
        breakdown = self._breakdown()
        self.assertEqual(breakdown.gross, 2000.0)
        self.assertEqual(breakdown.net, 1700.0)
        self.assertEqual(self._lifetime_revenue(), 1700.0)

    def test_editing_the_refund_replaces_it_rather_than_adding_to_it(self) -> None:
        """refund_amount is a running total: 100 then 150 means 150 refunded
        in all, so net is 850 -- not 750, which is what treating each save as
        a fresh transaction would give."""
        self._seed_booking()
        self.client.post(
            "/bookings/B-NR-1/status",
            data={"payment_status": "Partial Refund", "refund_amount": "100"},
        )
        self.assertEqual(self._breakdown().net, 900.0)

        self.client.post(
            "/bookings/B-NR-1/status",
            data={"payment_status": "Partial Refund", "refund_amount": "150"},
        )
        breakdown = self._breakdown()
        self.assertEqual(breakdown.refunds, 150.0)
        self.assertEqual(breakdown.net, 850.0)
        self.assertEqual(self._lifetime_revenue(), 850.0)

    # === RECOGNITION RULES ==============================================

    def test_a_refund_status_alone_does_not_make_revenue_zero(self) -> None:
        for status in ("Partial Refund", "Full Refund", "Refunded"):
            with self.subTest(status=status):
                booking_id = f"B-NR-ST-{status.replace(' ', '')}"
                self._seed_booking(booking_id=booking_id, payment_status=status, refund_amount=1.0)
                self.assertEqual(self._breakdown(booking_id).net, 999.0)

    def test_a_cancelled_booking_is_still_not_revenue(self) -> None:
        """Booking status remains a gate. A cancelled booking never earned
        revenue, so its refund has nothing to come off -- deducting it would
        invent negative revenue."""
        self._seed_booking(booking_status="Cancelled", payment_status="Full Refund", refund_amount=1000.0)
        self.assertIsNone(self._breakdown())
        self.assertEqual(self._lifetime_revenue(), 0.0)

    def test_a_draft_booking_is_still_not_revenue(self) -> None:
        self._seed_booking(booking_status="Draft", payment_status="Pending")
        self.assertIsNone(self._breakdown())

    def test_an_unpaid_booking_is_still_not_revenue(self) -> None:
        self._seed_booking(payment_status="Deposit Paid")
        self.assertIsNone(self._breakdown())

    def test_a_booking_without_a_recognized_currency_is_still_not_revenue(self) -> None:
        self._seed_booking(currency=None)
        self.assertIsNone(self._breakdown())

    def test_existing_non_refunded_revenue_is_unchanged(self) -> None:
        """The regression guard for everyone who never touched a refund."""
        self._seed_booking(booking_id="B-NR-A", booking_status="Completed", payment_status="Fully Paid")
        self._seed_booking(booking_id="B-NR-B", booking_status="Confirmed", payment_status="Paid")
        self.assertEqual(self._breakdown("B-NR-A").net, 1000.0)
        self.assertEqual(self._breakdown("B-NR-B").net, 1000.0)
        self.assertEqual(self._lifetime_revenue(), 2000.0)

    # === LEGACY DATA =====================================================

    def test_a_refund_larger_than_the_booking_cannot_drive_revenue_negative(self) -> None:
        """Possible in data predating the Phase 2 ceiling. Net floors at zero
        rather than dragging down every total it rolls into."""
        self._seed_booking(payment_status="Partial Refund", refund_amount=99999.0)
        breakdown = self._breakdown()
        self.assertEqual(breakdown.gross, 1000.0)
        self.assertEqual(breakdown.refunds, 1000.0)
        self.assertEqual(breakdown.net, 0.0)
        self.assertEqual(breakdown.refunds_recorded, 99999.0)
        self.assertTrue(breakdown.refund_exceeds_gross)
        self.assertGreaterEqual(self._lifetime_revenue(), 0.0)

    def test_historical_refund_values_are_never_rewritten(self) -> None:
        self._seed_booking(payment_status="Partial Refund", refund_amount=99999.0)
        self._lifetime_revenue()
        self.client.get("/travelers/TR100")
        self.client.get("/admin/revenue-analytics")
        with self.app.app_context():
            self.assertEqual(self.db.session.get(self.TripBooking, "B-NR-1").refund_amount, 99999.0)

    def test_an_over_refunded_booking_is_flagged_for_attention(self) -> None:
        self._seed_booking(payment_status="Partial Refund", refund_amount=99999.0)
        body = self.client.get("/admin/revenue-analytics").get_data(as_text=True)
        self.assertIn("larger than", body)

    def test_a_fully_refunded_booking_is_not_accused_of_having_no_price(self) -> None:
        """Needs Attention judges on gross. A completed refund nets zero while
        being perfectly well configured, and must not be reported as broken."""
        self._seed_booking(payment_status="Full Refund", refund_amount=1000.0)
        body = self.client.get("/admin/revenue-analytics").get_data(as_text=True)
        self.assertNotIn("No price configured", body)

    # === CURRENCY ========================================================

    def test_currencies_stay_separated(self) -> None:
        self._seed_booking(booking_id="B-NR-USD", currency="USD",
                           payment_status="Partial Refund", refund_amount=120.0)
        self._seed_booking(booking_id="B-NR-EGP", currency="EGP",
                           payment_status="Partial Refund", refund_amount=200.0)
        usd = self._breakdown("B-NR-USD")
        egp = self._breakdown("B-NR-EGP")
        self.assertEqual((usd.currency, usd.net), ("USD", 880.0))
        self.assertEqual((egp.currency, egp.net), ("EGP", 800.0))

        body = self.client.get("/admin/revenue-analytics").get_data(as_text=True)
        self.assertIn("880.00", body)
        self.assertIn("800.00", body)

    def test_refunds_are_reported_per_currency_in_analytics(self) -> None:
        self._seed_booking(currency="USD", payment_status="Partial Refund", refund_amount=120.0)
        body = self.client.get("/admin/revenue-analytics").get_data(as_text=True)
        self.assertIn("Total Net Revenue (USD)", body)
        self.assertIn("1000.00 gross", body)
        self.assertIn("120.00 refunded", body)

    # === ONE SOURCE OF TRUTH =============================================

    def test_every_revenue_surface_reports_the_same_number(self) -> None:
        """Lifetime Revenue, the profile hero and Revenue Analytics all read
        the same breakdown, so they cannot drift apart the way the duplicated
        calculations used to."""
        self._seed_booking(payment_status="Partial Refund", refund_amount=120.0)
        expected = 880.0

        self.assertEqual(self._breakdown().net, expected)
        self.assertEqual(self._lifetime_revenue(), expected)

        # Legacy SQLite recalculation path -- a separate implementation that
        # must agree with the ORM one.
        self.service.recalculate_traveler_stats("TR100")
        with self.app.app_context():
            self.assertEqual(self.db.session.get(self.Traveler, "TR100").lifetime_revenue, expected)

        self.assertIn("$880.00", self.client.get("/travelers/TR100").get_data(as_text=True))
        self.assertIn("880.00", self.client.get("/admin/revenue-analytics").get_data(as_text=True))

    def test_no_double_counting_across_multiple_bookings(self) -> None:
        self._seed_booking(booking_id="B-NR-1", payment_status="Partial Refund", refund_amount=120.0)
        self._seed_booking(booking_id="B-NR-2", payment_status="Fully Paid")
        self._seed_booking(booking_id="B-NR-3", payment_status="Full Refund", refund_amount=1000.0)
        # 880 + 1000 + 0
        self.assertEqual(self._lifetime_revenue(), 1880.0)

    def test_gross_helper_reports_before_refunds(self) -> None:
        self._seed_booking(payment_status="Partial Refund", refund_amount=120.0)
        with self.app.app_context():
            booking = self.db.session.get(self.TripBooking, "B-NR-1")
            trip = self.db.session.get(self.Trip, "TRIP-1000")
            self.assertEqual(self.revenue.booking_gross_revenue(booking, trip), ("USD", 1000.0))
            self.assertEqual(self.revenue.booking_revenue(booking, trip), ("USD", 880.0))

    def test_refund_total_ignores_unusable_values(self) -> None:
        from types import SimpleNamespace

        for raw in (None, "", -5, float("nan"), float("inf"), "abc"):
            with self.subTest(raw=raw):
                self.assertEqual(self.revenue.refund_total(SimpleNamespace(refund_amount=raw)), 0.0)
        self.assertEqual(self.revenue.refund_total(SimpleNamespace(refund_amount="150.5")), 150.5)

    def test_revenue_payment_statuses_is_left_intact(self) -> None:
        """Still means "paid in full, nothing returned". The refund statuses
        were added as a separate set rather than by widening this one."""
        self.assertEqual(self.revenue.REVENUE_PAYMENT_STATUSES, {"fully paid", "paid"})
        self.assertEqual(
            self.revenue.RECOGNIZED_PAYMENT_STATUSES,
            {"fully paid", "paid", "partial refund", "full refund", "refunded"},
        )

    # === INTERACTION WITH PHASES 1 AND 2 =================================

    def test_the_phase_two_refund_ceiling_still_applies(self) -> None:
        self._seed_booking()
        response = self.client.post(
            "/bookings/B-NR-1/status",
            data={"payment_status": "Partial Refund", "refund_amount": "1500"},
            follow_redirects=True,
        )
        self.assertIn("cannot exceed", response.get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNone(self.db.session.get(self.TripBooking, "B-NR-1").refund_amount)
        self.assertEqual(self._breakdown().net, 1000.0)

    def test_the_phase_one_timeline_still_records_refunds(self) -> None:
        self._seed_booking()
        self.client.post(
            "/bookings/B-NR-1/status",
            data={"payment_status": "Partial Refund", "refund_amount": "120"},
        )
        with self.app.app_context():
            events = self.BookingEventTrail.query.filter_by(
                booking_id="B-NR-1", event_type="refund_updated"
            ).all()
        self.assertEqual(len(events), 1)
        self.assertEqual(self._breakdown().net, 880.0)

    def test_recording_a_refund_updates_revenue_immediately(self) -> None:
        """The whole point, from the employee's side: save a refund and the
        traveler's revenue moves by exactly that amount."""
        self._seed_booking()
        self.assertEqual(self._lifetime_revenue(), 1000.0)

        self.client.post(
            "/bookings/B-NR-1/status",
            data={"payment_status": "Partial Refund", "refund_amount": "120"},
        )
        with self.app.app_context():
            self.assertEqual(self.db.session.get(self.Traveler, "TR100").lifetime_revenue, 880.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
