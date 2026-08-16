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
    from app.models.booking_status_history import BookingStatusHistory
    from app.models.lead import Lead
    from app.models.traveler import Traveler
    from app.models.trip import Trip
    from services.crm.system_services import UnifiedCRMService

    return db, TripBooking, BookingEventTrail, BookingStatusHistory, Lead, Traveler, Trip, UnifiedCRMService


class Phase3BookingLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(".tmp-test-workdirs") / f"phase3-booking-life-{uuid.uuid4().hex}"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tmpdir / "app.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{self.db_path.resolve().as_posix()}"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(self.db_path)
        # Pin auth off: app/__init__.py defaults CRM_AUTH_ENABLED to "true" when
        # unset, so inheriting it from the ambient environment made these
        # unauthenticated route tests 302-redirect in full-suite order.
        os.environ["CRM_AUTH_ENABLED"] = "false"
        self.app = _create_temp_app()
        self.db, self.TripBooking, self.BookingEventTrail, self.BookingStatusHistory, self.Lead, self.Traveler, self.Trip, UnifiedCRMService = _load_app_objects()
        self.app.config["TESTING"] = True
        with self.app.app_context():
            self.db.drop_all()
            self.db.create_all()
            self.db.session.add(
                self.Traveler(
                    traveler_id="TR100",
                    full_name="Returning Traveler",
                    integrated_whatsapp="20:1000000000",
                    normalized_whatsapp="+201000000000",
                    phone_lookup_key="20:1000000000",
                )
            )
            self.db.session.add(
                self.Lead(
                    lead_id="L-BOOK-1",
                    customer_name="Returning Traveler",
                    lead_stage="Booking Draft",
                    traveler_id="TR100",
                )
            )
            self.db.session.add(
                self.Trip(
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
                    public_price="$1,000",
                )
            )
            self.db.session.commit()
        self.client = self.app.test_client()
        self.service = UnifiedCRMService()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("RAHMA_SYSTEM_DB_PATH", None)

    def test_lifecycle_transitions_and_history_are_recorded(self) -> None:
        with self.app.app_context():
            booking = self.TripBooking(
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
            self.db.session.add(booking)
            self.db.session.commit()

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
            history = self.BookingStatusHistory.query.filter_by(booking_id="B-100").order_by(self.BookingStatusHistory.history_id.asc()).all()
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
            booking = self.TripBooking(
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
            self.db.session.add(booking)
            self.db.session.commit()

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
            self.assertEqual(self.Traveler.query.count(), 1)
            self.assertEqual(self.TripBooking.query.filter_by(traveler_id="TR100").count(), 1)

    def test_cancelled_booking_releases_room_inventory(self) -> None:
        booking = self.service.create_booking_draft(
            traveler_id="TR100",
            traveler_name="Returning Traveler",
            trip_id="TRIP-100",
            room_type="Double",
            channel="web",
            source="Web CRM",
        )

        before = self.service._fetch_trip_record("TRIP-100")
        self.assertEqual(before["draft_holds_double"], 1)
        self.assertEqual(before["available_double"], 1)

        self.service.update_booking_status(booking["booking_id"], new_status="Cancelled")

        after = self.service._fetch_trip_record("TRIP-100")
        self.assertEqual(after["draft_holds_double"], 0)
        self.assertEqual(after["available_double"], 2)

    def test_mixed_boys_girls_booking_stores_and_holds_categories_separately(self) -> None:
        with self.app.app_context():
            trip = self.Trip.query.filter_by(trip_id="TRIP-100").one()
            trip.double_remaining = 3
            trip.boys_double = 2
            trip.girls_double = 1
            self.db.session.commit()

        booking = self.service.create_booking_draft(
            traveler_id="TR100",
            traveler_name="Returning Traveler",
            trip_id="TRIP-100",
            room_type="Double",
            room_group="mixed",
            room_requirements=[
                {"room_type": "Double", "room_group": "boys", "rooms": 1},
                {"room_type": "Double", "room_group": "girls", "rooms": 1},
            ],
            channel="web",
            source="Test",
        )

        self.assertEqual(booking["room_group"], "mixed")
        self.assertEqual(booking["boys_rooms_requested"], 1)
        self.assertEqual(booking["girls_rooms_requested"], 1)
        after = self.service._fetch_trip_record("TRIP-100")
        self.assertEqual(after["draft_holds_double"], 2)
        self.assertEqual(after["draft_holds_boys_double"], 1)
        self.assertEqual(after["draft_holds_girls_double"], 1)
        self.assertEqual(after["boys_double"], 1)
        self.assertEqual(after["girls_double"], 0)

        with self.app.app_context():
            row = self.TripBooking.query.filter_by(booking_id=booking["booking_id"]).one()
            self.assertEqual(row.room_group, "mixed")
            self.assertEqual(row.boys_rooms_requested, 1)
            self.assertEqual(row.girls_rooms_requested, 1)

    def test_unavailable_boys_category_does_not_use_girls_inventory(self) -> None:
        with self.app.app_context():
            trip = self.Trip.query.filter_by(trip_id="TRIP-100").one()
            trip.double_remaining = 2
            trip.boys_double = 0
            trip.girls_double = 2
            self.db.session.commit()

        before = self.service._fetch_trip_record("TRIP-100")
        with self.assertRaises(ValueError) as ctx:
            self.service.create_booking_draft(
                traveler_id="TR100",
                traveler_name="Returning Traveler",
                trip_id="TRIP-100",
                room_type="Double",
                room_group="mixed",
                room_requirements=[
                    {"room_type": "Double", "room_group": "boys", "rooms": 1},
                    {"room_type": "Double", "room_group": "girls", "rooms": 1},
                ],
                channel="web",
                source="Test",
            )
        self.assertIn("Boys double rooms are unavailable", str(ctx.exception))
        after = self.service._fetch_trip_record("TRIP-100")
        self.assertEqual(after["draft_holds_double"], before["draft_holds_double"])
        self.assertEqual(after["draft_holds_boys_double"], before["draft_holds_boys_double"])
        self.assertEqual(after["draft_holds_girls_double"], before["draft_holds_girls_double"])
        self.assertEqual(after["girls_double"], before["girls_double"])

    def test_unavailable_girls_category_does_not_use_boys_inventory(self) -> None:
        with self.app.app_context():
            trip = self.Trip.query.filter_by(trip_id="TRIP-100").one()
            trip.double_remaining = 2
            trip.boys_double = 2
            trip.girls_double = 0
            self.db.session.commit()

        before = self.service._fetch_trip_record("TRIP-100")
        with self.assertRaises(ValueError) as ctx:
            self.service.create_booking_draft(
                traveler_id="TR100",
                traveler_name="Returning Traveler",
                trip_id="TRIP-100",
                room_type="Double",
                room_group="mixed",
                room_requirements=[
                    {"room_type": "Double", "room_group": "boys", "rooms": 1},
                    {"room_type": "Double", "room_group": "girls", "rooms": 1},
                ],
                channel="web",
                source="Test",
            )
        self.assertIn("Girls double rooms are unavailable", str(ctx.exception))
        after = self.service._fetch_trip_record("TRIP-100")
        self.assertEqual(after["draft_holds_double"], before["draft_holds_double"])
        self.assertEqual(after["draft_holds_boys_double"], before["draft_holds_boys_double"])
        self.assertEqual(after["draft_holds_girls_double"], before["draft_holds_girls_double"])
        self.assertEqual(after["boys_double"], before["boys_double"])

    def test_admin_booking_ui_updates_lifecycle_and_history(self) -> None:
        with self.app.app_context():
            booking = self.TripBooking(
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
            self.db.session.add(booking)
            self.db.session.commit()

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
            booking = self.db.session.get(self.TripBooking, "B-102")
            self.assertEqual(booking.booking_status, "Payment Pending")
            self.assertEqual(booking.payment_status, "Deposit Paid")
            history = self.BookingStatusHistory.query.filter_by(booking_id="B-102").all()
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0].old_status, "Confirmed")
            self.assertEqual(history[0].new_status, "Payment Pending")

    def test_booking_detail_status_selector_is_limited_to_manual_options(self) -> None:
        with self.app.app_context():
            booking = self.TripBooking(
                booking_id="B-STATUS-1",
                trip_id="TRIP-100",
                trip_name="Lifecycle Trip",
                traveler_id="TR100",
                traveler_name="Returning Traveler",
                room_type="Double",
                booking_status="Draft",
                booking_source="Admin",
                payment_status="Pending",
            )
            self.db.session.add(booking)
            self.db.session.commit()

        response = self.client.get("/bookings/B-STATUS-1")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        status_select = body.split('<select class="form-select" name="booking_status">', 1)[1].split('</select>', 1)[0]
        self.assertIn('<option value="Draft" selected>', status_select)
        self.assertIn('<option value="Completed"', status_select)
        self.assertIn('<option value="Cancelled"', status_select)
        self.assertNotIn('<option value="Waiting Customer"', status_select)
        self.assertNotIn('<option value="Pending Confirmation"', status_select)
        self.assertNotIn('<option value="Confirmed"', status_select)
        self.assertNotIn('<option value="Payment Pending"', status_select)
        self.assertNotIn('<option value="Paid"', status_select)

    def test_missing_fields_are_computed_dynamically_not_from_a_stored_list(self) -> None:
        with self.app.app_context():
            booking = self.TripBooking(
                booking_id="B-DYNAMIC-1",
                trip_id="TRIP-100",
                trip_name="Lifecycle Trip",
                traveler_id="TR100",
                traveler_name="Returning Traveler",
                room_type=None,
                currency=None,
                booking_status="Draft",
                booking_source="Auto (lead_stage_change)",
                payment_status="Pending",
                missing_info=True,
            )
            self.db.session.add(booking)
            self.db.session.commit()

            booking = self.db.session.get(self.TripBooking, "B-DYNAMIC-1")
            self.assertEqual(booking.compute_missing_fields(), ["Room Type", "Currency"])

            booking.room_type = "Single"
            booking.currency = "EGP"
            self.assertEqual(booking.compute_missing_fields(), [])
            self.assertFalse(booking.recompute_missing_info())

    def test_incomplete_booking_can_be_completed_from_the_detail_page(self) -> None:
        with self.app.app_context():
            booking = self.TripBooking(
                booking_id="B-COMPLETE-1",
                trip_id=None,
                traveler_id="TR100",
                traveler_name="Returning Traveler",
                room_type=None,
                currency=None,
                booking_status="Completed",
                booking_source="Auto (lead_stage_change)",
                payment_status="Fully Paid",
                missing_info=True,
            )
            self.db.session.add(booking)
            self.db.session.commit()

        detail_response = self.client.get("/bookings/B-COMPLETE-1")
        self.assertEqual(detail_response.status_code, 200)
        body = detail_response.get_data(as_text=True)
        self.assertIn("Missing Information", body)
        self.assertIn("Trip, Room Type, Currency", body)

        response = self.client.post(
            "/bookings/B-COMPLETE-1/status",
            data={
                "trip_id": "TRIP-100",
                "room_type": "Single",
                "currency": "EGP",
                "employee_correction": "1",
                "booking_notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            booking = self.db.session.get(self.TripBooking, "B-COMPLETE-1")
            self.assertEqual(booking.trip_id, "TRIP-100")
            self.assertEqual(booking.room_type, "Single")
            self.assertEqual(booking.currency, "EGP")
            self.assertFalse(booking.missing_info)
            traveler = self.db.session.get(self.Traveler, "TR100")
            self.assertGreater(traveler.lifetime_revenue or 0, 0)

        detail_response = self.client.get("/bookings/B-COMPLETE-1")
        body = detail_response.get_data(as_text=True)
        self.assertIn("Booking information complete", body)
        self.assertNotIn("Needs Info</span>", body)

    def test_completed_status_is_blocked_while_required_info_is_missing(self) -> None:
        with self.app.app_context():
            booking = self.TripBooking(
                booking_id="B-BLOCKED-1",
                trip_id=None,
                traveler_id="TR100",
                traveler_name="Returning Traveler",
                room_type=None,
                currency=None,
                booking_status="Draft",
                booking_source="Auto (lead_stage_change)",
                payment_status="Pending",
                missing_info=True,
            )
            self.db.session.add(booking)
            self.db.session.commit()

        response = self.client.post(
            "/bookings/B-BLOCKED-1/status",
            data={
                "booking_status": "Completed",
                "employee_correction": "1",
                "booking_notes": "",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("missing required information", body)

        with self.app.app_context():
            booking = self.db.session.get(self.TripBooking, "B-BLOCKED-1")
            self.assertEqual(booking.booking_status, "Draft")

    def test_completed_status_can_be_overridden_with_a_written_reason(self) -> None:
        with self.app.app_context():
            booking = self.TripBooking(
                booking_id="B-OVERRIDE-1",
                trip_id=None,
                traveler_id="TR100",
                traveler_name="Returning Traveler",
                room_type=None,
                currency=None,
                booking_status="Draft",
                booking_source="Auto (lead_stage_change)",
                payment_status="Pending",
                missing_info=True,
            )
            self.db.session.add(booking)
            self.db.session.commit()

        response = self.client.post(
            "/bookings/B-OVERRIDE-1/status",
            data={
                "booking_status": "Completed",
                "employee_correction": "1",
                "booking_notes": "Manager-approved historic booking, trip data unavailable.",
            },
        )
        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            booking = self.db.session.get(self.TripBooking, "B-OVERRIDE-1")
            self.assertEqual(booking.booking_status, "Completed")
            # The override lets the status through, but it does not
            # fabricate the missing data -- the booking is still flagged.
            self.assertTrue(booking.missing_info)

    def test_normal_completion_of_an_already_complete_booking_is_unaffected(self) -> None:
        with self.app.app_context():
            booking = self.TripBooking(
                booking_id="B-NORMAL-1",
                trip_id="TRIP-100",
                trip_name="Lifecycle Trip",
                traveler_id="TR100",
                traveler_name="Returning Traveler",
                room_type="Double",
                currency="EGP",
                booking_status="Confirmed",
                booking_source="Admin",
                payment_status="Deposit Paid",
            )
            self.db.session.add(booking)
            self.db.session.commit()

        response = self.client.post(
            "/bookings/B-NORMAL-1/status",
            data={
                "booking_status": "Completed",
                "payment_status": "Fully Paid",
                # Confirmed -> Completed directly is a non-standard jump in
                # this booking's lifecycle (independent of the new
                # completeness gate this change adds), so it needs the same
                # override + reason every such jump already requires.
                "employee_correction": "1",
                "booking_notes": "Trip completed, payment finalized.",
            },
        )
        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            booking = self.db.session.get(self.TripBooking, "B-NORMAL-1")
            self.assertEqual(booking.booking_status, "Completed")
            self.assertEqual(booking.payment_status, "Fully Paid")
            self.assertFalse(booking.missing_info)

    def test_needs_info_filter_returns_only_incomplete_bookings(self) -> None:
        with self.app.app_context():
            self.db.session.add(self.TripBooking(
                booking_id="B-FILTER-COMPLETE",
                trip_id="TRIP-100",
                traveler_id="TR100",
                traveler_name="Returning Traveler",
                room_type="Double",
                currency="EGP",
                booking_status="Confirmed",
                booking_source="Admin",
                payment_status="Deposit Paid",
                missing_info=False,
            ))
            self.db.session.add(self.TripBooking(
                booking_id="B-FILTER-INCOMPLETE",
                trip_id=None,
                traveler_id="TR100",
                traveler_name="Returning Traveler",
                room_type=None,
                currency=None,
                booking_status="Draft",
                booking_source="Auto (lead_stage_change)",
                payment_status="Pending",
                missing_info=True,
            ))
            self.db.session.commit()

        response = self.client.get("/bookings/?queue=needs_info")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("B-FILTER-INCOMPLETE", body)
        self.assertNotIn("B-FILTER-COMPLETE", body)

    def test_new_booking_currency_selector_does_not_offer_eur(self) -> None:
        response = self.client.get("/bookings/")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        currency_select = body.split('id="createBookingCurrencySelector"', 1)[1].split('</select>', 1)[0]
        self.assertNotIn('EUR', currency_select)

    def test_invalid_currency_is_rejected_server_side(self) -> None:
        with self.app.app_context():
            booking = self.TripBooking(
                booking_id="B-BADCUR-1",
                trip_id="TRIP-100",
                traveler_id="TR100",
                traveler_name="Returning Traveler",
                room_type="Double",
                booking_status="Draft",
                booking_source="Admin",
                payment_status="Pending",
            )
            self.db.session.add(booking)
            self.db.session.commit()

        response = self.client.post(
            "/bookings/B-BADCUR-1/status",
            data={"currency": "EUR"},
        )
        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            booking = self.db.session.get(self.TripBooking, "B-BADCUR-1")
            self.assertIsNone(booking.currency)

    def test_delete_booking_removes_links_and_related_history(self) -> None:
        with self.app.app_context():
            booking = self.TripBooking(
                booking_id="B-DELETE-1",
                trip_id="TRIP-100",
                trip_name="Lifecycle Trip",
                traveler_id="TR100",
                traveler_name="Returning Traveler",
                room_type="Double",
                booking_status="Draft",
                booking_source="Admin",
                payment_status="Pending",
                lead_id="L-BOOK-1",
            )
            self.db.session.add(booking)
            self.db.session.commit()
            lead = self.db.session.get(self.Lead, "L-BOOK-1")
            lead.booking_id = "B-DELETE-1"
            traveler = self.db.session.get(self.Traveler, "TR100")
            traveler.last_booking_id = "B-DELETE-1"
            self.db.session.add(
                self.BookingStatusHistory(
                    booking_id="B-DELETE-1",
                    old_status="Draft",
                    new_status="Completed",
                    changed_by="admin",
                )
            )
            self.db.session.add(
                self.BookingEventTrail(
                    event_id="E-DELETE-1",
                    booking_id="B-DELETE-1",
                    traveler_id="TR100",
                    lead_id="L-BOOK-1",
                    event_type="booking_draft_created",
                    event_label="Booking draft created",
                )
            )
            self.db.session.commit()

        response = self.client.post("/bookings/B-DELETE-1/delete", follow_redirects=False)
        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            self.assertIsNone(self.db.session.get(self.TripBooking, "B-DELETE-1"))
            self.assertEqual(self.BookingStatusHistory.query.filter_by(booking_id="B-DELETE-1").count(), 0)
            self.assertEqual(self.BookingEventTrail.query.filter_by(booking_id="B-DELETE-1").count(), 0)
            lead = self.db.session.get(self.Lead, "L-BOOK-1")
            self.assertIsNone(lead.booking_id)
            traveler = self.db.session.get(self.Traveler, "TR100")
            self.assertIsNone(traveler.last_booking_id)

    def test_delete_booking_logs_warning_when_lead_pointer_cleanup_matches_no_rows(self) -> None:
        """Regression: the Lead.booking_id/Traveler.last_booking_id cleanup
        updates in delete() are bulk .update() calls whose rowcount was
        never checked -- a lead_id pointing at an already-deleted Lead row
        would silently no-op instead of surfacing, leaving no trace that a
        dangling reference could exist. Same "write happened, nobody
        checked" shape as guardian consent's original bug, just lower risk.
        Deleting the Lead row out from under a real lead_id reproduces the
        0-rows-matched case without needing to fabricate a fake id.
        """
        with self.app.app_context():
            booking = self.TripBooking(
                booking_id="B-DELETE-2",
                trip_id="TRIP-100",
                trip_name="Lifecycle Trip",
                traveler_id="TR100",
                traveler_name="Returning Traveler",
                room_type="Double",
                booking_status="Draft",
                booking_source="Admin",
                payment_status="Pending",
                lead_id="L-BOOK-1",
            )
            self.db.session.add(booking)
            self.db.session.commit()
            self.Lead.query.filter_by(lead_id="L-BOOK-1").delete(synchronize_session=False)
            self.db.session.commit()

        with self.assertLogs("app.routes.bookings", level="WARNING") as logs:
            response = self.client.post("/bookings/B-DELETE-2/delete", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(any("Lead.booking_id cleanup matched no rows" in message for message in logs.output))

        with self.app.app_context():
            self.assertIsNone(self.db.session.get(self.TripBooking, "B-DELETE-2"))

    def test_booking_status_update_recalculates_traveler_summary(self) -> None:
        with self.app.app_context():
            booking = self.TripBooking(
                booking_id="B-103",
                trip_id="TRIP-100",
                trip_name="Lifecycle Trip",
                traveler_id="TR100",
                traveler_name="Returning Traveler",
                room_type="Double",
                booking_status="Draft",
                booking_source="Admin",
                payment_status="Pending",
            )
            self.db.session.add(booking)
            self.db.session.commit()
            traveler = self.db.session.get(self.Traveler, "TR100")
            self.assertEqual(traveler.total_trips or 0, 0)

        self.service.recalculate_traveler_stats("TR100")

        with self.app.app_context():
            traveler = self.db.session.get(self.Traveler, "TR100")
            self.assertEqual(traveler.local_trips_count, 1)
            self.assertEqual(traveler.international_trips_count, 0)
            self.assertEqual(traveler.total_trips, 1)
            self.assertEqual(traveler.lifetime_revenue or 0, 0)

        self.service.update_booking_status("B-103", new_status="Pending Confirmation")
        self.service.update_booking_status("B-103", new_status="Confirmed", new_payment_status="Fully Paid")

        with self.app.app_context():
            traveler = self.db.session.get(self.Traveler, "TR100")
            self.assertEqual(traveler.lifetime_revenue, 1000)

        self.service.update_booking_status("B-103", new_status="Cancelled", new_payment_status="Deposit Paid")

        with self.app.app_context():
            traveler = self.db.session.get(self.Traveler, "TR100")
            self.assertEqual(traveler.local_trips_count, 0)
            self.assertEqual(traveler.international_trips_count, 0)
            self.assertEqual(traveler.total_trips, 0)
            self.assertEqual(traveler.lifetime_revenue, 0)

    def test_traveler_detail_recalculates_stale_summary_from_bookings(self) -> None:
        with self.app.app_context():
            traveler = self.db.session.get(self.Traveler, "TR100")
            traveler.local_trips_count = 0
            traveler.international_trips_count = 0
            traveler.total_trips = 0
            traveler.lifetime_revenue = 0
            self.db.session.add(
                self.TripBooking(
                    booking_id="B-104",
                    trip_id="TRIP-100",
                    trip_name="Lifecycle Trip",
                    traveler_id="TR100",
                    traveler_name="Returning Traveler",
                    room_type="Single",
                    group_size=3,
                    booking_status="Confirmed",
                    booking_source="Admin",
                    payment_status="Fully Paid",
                )
            )
            self.db.session.commit()

        response = self.client.get("/travelers/TR100")
        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            traveler = self.db.session.get(self.Traveler, "TR100")
            self.assertEqual(traveler.local_trips_count, 1)
            self.assertEqual(traveler.international_trips_count, 0)
            self.assertEqual(traveler.total_trips, 1)
            self.assertEqual(traveler.lifetime_revenue, 3000)

    def test_trip_detail_shows_remaining_after_active_bookings(self) -> None:
        with self.app.app_context():
            self.db.session.add_all(
                [
                    self.TripBooking(
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
                    self.TripBooking(
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
            self.db.session.commit()

        response = self.client.get("/trips/TRIP-100")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Double", body)
        self.assertIn("Booked: 1", body)
        self.assertIn("of 2 remaining", body)


if __name__ == "__main__":
    unittest.main()
