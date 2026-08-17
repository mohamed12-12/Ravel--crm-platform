"""Phase 1: the Booking Activity Timeline is a real audit trail.

BookingEventTrail used to be written in exactly one place -- the auto-create
path -- so a booking's timeline recorded its birth and nothing else. These
tests pin the behaviour of app/services/booking_audit.py: every ORM write
path records structured before/after events, no event is written when a
value did not actually change, and a rolled-back save leaves no history
behind.
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
    from app.models.lead import Lead
    from app.models.traveler import Traveler
    from app.models.trip import Trip
    from app.services import booking_audit

    return db, TripBooking, BookingEventTrail, Lead, Traveler, Trip, booking_audit


class BookingActivityTimelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(".tmp-test-workdirs") / f"booking-timeline-{uuid.uuid4().hex}"
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
            self.Lead,
            self.Traveler,
            self.Trip,
            self.booking_audit,
        ) = _load_app_objects()
        self.app.config["TESTING"] = True
        with self.app.app_context():
            self.db.drop_all()
            self.db.create_all()
            self.db.session.add(
                self.Traveler(
                    traveler_id="TR100",
                    full_name="Timeline Traveler",
                    integrated_whatsapp="20:1000000000",
                    normalized_whatsapp="+201000000000",
                    phone_lookup_key="20:1000000000",
                )
            )
            self.db.session.add(
                self.Trip(
                    trip_id="TRIP-100",
                    trip_name="Timeline Trip",
                    type="Local",
                    sales_status="Open",
                    single_total=5,
                    double_total=5,
                    triple_total=5,
                    single_remaining=5,
                    double_remaining=5,
                    triple_remaining=5,
                    draft_holds_single=0,
                    draft_holds_double=0,
                    draft_holds_triple=0,
                    public_price="$1,000",
                )
            )
            self.db.session.add(
                self.Trip(
                    trip_id="TRIP-200",
                    trip_name="Alternate Trip",
                    type="Local",
                    sales_status="Open",
                    single_total=5,
                    double_total=5,
                    triple_total=5,
                    single_remaining=5,
                    double_remaining=5,
                    triple_remaining=5,
                    draft_holds_single=0,
                    draft_holds_double=0,
                    draft_holds_triple=0,
                    public_price="$2,000",
                )
            )
            self.db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("RAHMA_SYSTEM_DB_PATH", None)

    # -- helpers ---------------------------------------------------------

    def _seed_booking(self, booking_id: str = "B-TL-1", **overrides):
        """Create a complete, unremarkable booking with auditing suspended.

        Seeding is not booking activity, so the events these tests inspect
        are only the ones the exercised code path produced.
        """
        fields = dict(
            booking_id=booking_id,
            trip_id="TRIP-100",
            trip_name="Timeline Trip",
            traveler_id="TR100",
            traveler_name="Timeline Traveler",
            room_type="Double",
            currency="USD",
            group_size=2,
            booking_status="Draft",
            booking_source="Admin",
            payment_status="Pending",
            priority="Medium",
            missing_info=False,
        )
        fields.update(overrides)
        with self.app.app_context():
            with self.booking_audit.suspend_booking_audit():
                self.db.session.add(self.TripBooking(**fields))
                self.db.session.commit()
        return booking_id

    def _events(self, booking_id: str = "B-TL-1", event_type: str | None = None):
        with self.app.app_context():
            query = self.BookingEventTrail.query.filter_by(booking_id=booking_id)
            if event_type:
                query = query.filter_by(event_type=event_type)
            return query.order_by(self.BookingEventTrail.event_id.asc()).all()

    def _event_payloads(self, booking_id: str = "B-TL-1", event_type: str | None = None):
        with self.app.app_context():
            query = self.BookingEventTrail.query.filter_by(booking_id=booking_id)
            if event_type:
                query = query.filter_by(event_type=event_type)
            rows = query.order_by(self.BookingEventTrail.event_id.asc()).all()
            return [row.to_dict() for row in rows]

    # -- creation --------------------------------------------------------

    def test_booking_created_through_the_crm_records_a_creation_event(self) -> None:
        response = self.client.post(
            "/bookings/",
            data={
                "trip_id": "TRIP-100",
                "traveler_id": "TR100",
                "room_type": "Double",
                "currency": "USD",
                "group_size": "2",
                "booking_source": "Admin",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            booking = self.TripBooking.query.filter_by(traveler_id="TR100").one()
            events = self.BookingEventTrail.query.filter_by(
                booking_id=booking.booking_id, event_type="booking_created"
            ).all()
            self.assertEqual(len(events), 1)
            event = events[0]
            self.assertEqual(event.event_label, "Booking created")
            self.assertEqual(event.traveler_id, "TR100")
            self.assertIsNotNone(event.occurred_at)
            snapshot = event.metadata_dict()["snapshot"]
            self.assertEqual(snapshot["trip_id"], "TRIP-100")
            self.assertEqual(snapshot["room_type"], "Double")

    def test_booking_added_through_the_orm_records_a_creation_event(self) -> None:
        """The agent bridge inserts bookings straight through SQLAlchemy.

        It never calls the audit module, which is the point: the session
        listener covers it anyway.
        """
        with self.app.app_context():
            self.db.session.add(
                self.TripBooking(
                    booking_id="B-ORM-1",
                    trip_id="TRIP-100",
                    trip_name="Timeline Trip",
                    traveler_id="TR100",
                    traveler_name="Timeline Traveler",
                    room_type="Single",
                    booking_status="Draft",
                    booking_source="Agent",
                    payment_status="Pending",
                )
            )
            self.db.session.commit()

        events = self._events("B-ORM-1", "booking_created")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].channel, "Agent")

    def test_auto_created_booking_records_exactly_one_creation_event(self) -> None:
        """auto_create_booking_from_lead writes its own richer creation event.

        The listener must recognise it and not add a second one for the same
        INSERT.
        """
        with self.app.app_context():
            self.db.session.add(
                self.Lead(
                    lead_id="L-TL-1",
                    customer_name="Timeline Traveler",
                    lead_stage="Qualified",
                    traveler_id="TR100",
                    interested_trip_ids="TRIP-100",
                    group_size=2,
                )
            )
            self.db.session.commit()

        response = self.client.post("/leads/L-TL-1", data={"lead_stage": "Booking Draft"})
        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            booking = self.TripBooking.query.filter_by(lead_id="L-TL-1").one()
            creation_events = self.BookingEventTrail.query.filter(
                self.BookingEventTrail.booking_id == booking.booking_id,
                self.BookingEventTrail.event_type.in_(sorted(self.booking_audit.CREATION_EVENT_TYPES)),
            ).all()
            self.assertEqual(len(creation_events), 1)
            self.assertEqual(creation_events[0].event_type, "booking_auto_created")

    # -- status and payment ----------------------------------------------

    def test_status_change_records_one_event_with_previous_and_new_values(self) -> None:
        self._seed_booking()
        response = self.client.post(
            "/bookings/B-TL-1/status",
            data={"booking_status": "Cancelled"},
        )
        self.assertEqual(response.status_code, 302)

        events = self._event_payloads("B-TL-1", "booking_status_changed")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_label"], "Booking status changed")
        self.assertEqual(events[0]["metadata"]["field_label"], "Booking Status")
        self.assertEqual(events[0]["metadata"]["previous"], "Draft")
        self.assertEqual(events[0]["metadata"]["current"], "Cancelled")
        self.assertIn("Previously: Draft", events[0]["notes"])
        self.assertIn("Now: Cancelled", events[0]["notes"])

    def test_payment_status_change_records_one_event(self) -> None:
        self._seed_booking(payment_status="Deposit Paid")
        response = self.client.post(
            "/bookings/B-TL-1/status",
            data={"payment_status": "Fully Paid"},
        )
        self.assertEqual(response.status_code, 302)

        events = self._event_payloads("B-TL-1", "payment_status_changed")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["metadata"]["previous"], "Deposit Paid")
        self.assertEqual(events[0]["metadata"]["current"], "Fully Paid")

    def test_refund_records_one_event_carrying_the_amount(self) -> None:
        self._seed_booking(payment_status="Fully Paid")
        response = self.client.post(
            "/bookings/B-TL-1/status",
            data={"payment_status": "Partial Refund", "refund_amount": "120"},
        )
        self.assertEqual(response.status_code, 302)

        events = self._event_payloads("B-TL-1", "refund_updated")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_label"], "Refund recorded")
        # No previous amount, so the entry reads as a statement of fact
        # rather than a before/after pair.
        self.assertEqual(events[0]["notes"], "Amount: 120.00 USD")
        self.assertEqual(events[0]["metadata"]["current"], "120.00 USD")

    def test_refund_amount_correction_records_the_previous_amount(self) -> None:
        self._seed_booking(payment_status="Partial Refund", refund_amount=120.0)
        response = self.client.post(
            "/bookings/B-TL-1/status",
            data={"payment_status": "Partial Refund", "refund_amount": "150"},
        )
        self.assertEqual(response.status_code, 302)

        events = self._event_payloads("B-TL-1", "refund_updated")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["metadata"]["previous"], "120.00 USD")
        self.assertEqual(events[0]["metadata"]["current"], "150.00 USD")

    # -- other important fields ------------------------------------------

    def test_booking_detail_field_changes_each_record_their_own_event(self) -> None:
        self._seed_booking()
        response = self.client.post(
            "/bookings/B-TL-1/status",
            data={
                "trip_id": "TRIP-200",
                "room_type": "Triple",
                "currency": "EGP",
                "group_size": "5",
            },
        )
        self.assertEqual(response.status_code, 302)

        events = self._event_payloads("B-TL-1", "booking_details_updated")
        by_field = {event["metadata"]["field"]: event for event in events}
        self.assertEqual(
            set(by_field), {"trip_id", "room_type", "currency", "group_size"}
        )
        self.assertEqual(by_field["room_type"]["metadata"]["previous"], "Double")
        self.assertEqual(by_field["room_type"]["metadata"]["current"], "Triple")
        self.assertEqual(by_field["group_size"]["metadata"]["previous"], "2")
        self.assertEqual(by_field["group_size"]["metadata"]["current"], "5")
        # A trip is shown by name -- an id alone means nothing to an employee.
        self.assertIn("Alternate Trip", by_field["trip_id"]["metadata"]["current"])

    def test_group_size_synced_from_a_lead_edit_records_an_event(self) -> None:
        self._seed_booking(booking_id="B-TL-SYNC", group_size=1)
        with self.app.app_context():
            self.db.session.add(
                self.Lead(
                    lead_id="L-TL-SYNC",
                    customer_name="Timeline Traveler",
                    lead_stage="Won",
                    traveler_id="TR100",
                    booking_id="B-TL-SYNC",
                    group_size=1,
                )
            )
            self.db.session.commit()

        response = self.client.post("/leads/L-TL-SYNC", data={"group_size": "4"})
        self.assertEqual(response.status_code, 302)

        events = self._event_payloads("B-TL-SYNC", "booking_details_updated")
        group_events = [e for e in events if e["metadata"]["field"] == "group_size"]
        self.assertEqual(len(group_events), 1)
        self.assertEqual(group_events[0]["metadata"]["previous"], "1")
        self.assertEqual(group_events[0]["metadata"]["current"], "4")

    def test_completing_a_booking_records_an_information_event(self) -> None:
        self._seed_booking(room_type=None, currency=None, missing_info=True)
        response = self.client.post(
            "/bookings/B-TL-1/status",
            data={"trip_id": "TRIP-100", "room_type": "Double", "currency": "USD"},
        )
        self.assertEqual(response.status_code, 302)

        events = self._event_payloads("B-TL-1", "booking_info_changed")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_label"], "Booking information completed")
        self.assertEqual(events[0]["metadata"]["previous"], "Incomplete")
        self.assertEqual(events[0]["metadata"]["current"], "Complete")

    def test_employee_note_records_a_note_event(self) -> None:
        self._seed_booking()
        response = self.client.post(
            "/bookings/B-TL-1/status",
            data={"booking_notes": "Customer asked to be called after 6pm."},
        )
        self.assertEqual(response.status_code, 302)

        events = self._event_payloads("B-TL-1", "booking_note_added")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["notes"], "Customer asked to be called after 6pm.")

    def test_system_audit_note_does_not_become_a_second_note_event(self) -> None:
        """A payment change appends a system line to booking_notes.

        That line already has a structured event of its own; recording it as
        a note too would say the same thing twice in one timeline.
        """
        self._seed_booking(payment_status="Deposit Paid")
        self.client.post("/bookings/B-TL-1/status", data={"payment_status": "Fully Paid"})

        self.assertEqual(len(self._events("B-TL-1", "booking_note_added")), 0)
        self.assertEqual(len(self._events("B-TL-1", "payment_status_changed")), 1)

    # -- no duplicates ---------------------------------------------------

    def test_saving_unchanged_values_records_no_events(self) -> None:
        self._seed_booking()
        response = self.client.post(
            "/bookings/B-TL-1/status",
            data={
                "booking_status": "Draft",
                "payment_status": "Pending",
                "trip_id": "TRIP-100",
                "room_type": "Double",
                "currency": "USD",
                "group_size": "2",
                "priority": "Medium",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._events("B-TL-1"), [])

    def test_repeating_the_same_change_records_it_only_once(self) -> None:
        self._seed_booking()
        payload = {"booking_status": "Cancelled", "room_type": "Triple"}
        self.client.post("/bookings/B-TL-1/status", data=payload)
        self.client.post("/bookings/B-TL-1/status", data=payload)

        self.assertEqual(len(self._events("B-TL-1", "booking_status_changed")), 1)
        room_events = [
            event
            for event in self._event_payloads("B-TL-1", "booking_details_updated")
            if event["metadata"]["field"] == "room_type"
        ]
        self.assertEqual(len(room_events), 1)

    def test_a_rejected_save_records_no_events(self) -> None:
        self._seed_booking(payment_status="Full Refund")
        response = self.client.post(
            "/bookings/B-TL-1/status",
            data={"payment_status": "Fully Paid"},
        )
        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            booking = self.db.session.get(self.TripBooking, "B-TL-1")
            self.assertEqual(booking.payment_status, "Full Refund")
        self.assertEqual(self._events("B-TL-1"), [])

    # -- several changes at once -----------------------------------------

    def test_multiple_changes_in_one_save_produce_separate_events(self) -> None:
        self._seed_booking(payment_status="Deposit Paid")
        response = self.client.post(
            "/bookings/B-TL-1/status",
            data={
                "booking_status": "Completed",
                "payment_status": "Fully Paid",
                "room_type": "Single",
                "booking_notes": "Trip finished, paid in full.",
                "employee_correction": "1",
            },
        )
        self.assertEqual(response.status_code, 302)

        payloads = self._event_payloads("B-TL-1")
        by_type: dict[str, int] = {}
        for payload in payloads:
            by_type[payload["event_type"]] = by_type.get(payload["event_type"], 0) + 1
        self.assertEqual(by_type.get("booking_status_changed"), 1)
        self.assertEqual(by_type.get("payment_status_changed"), 1)
        self.assertEqual(by_type.get("booking_details_updated"), 1)
        self.assertEqual(by_type.get("booking_note_added"), 1)
        # Every entry is independently identifiable and attributable.
        self.assertEqual(len({payload["event_id"] for payload in payloads}), len(payloads))
        for payload in payloads:
            self.assertTrue(payload["actor"])
            self.assertTrue(payload["occurred_at"])
            self.assertEqual(payload["booking_id"], "B-TL-1")

    # -- rendering and backward compatibility ----------------------------

    def test_timeline_renders_previous_and_new_values(self) -> None:
        self._seed_booking(payment_status="Deposit Paid")
        self.client.post("/bookings/B-TL-1/status", data={"payment_status": "Fully Paid"})

        page = self.client.get("/bookings/B-TL-1")
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn("Payment status changed", body)
        self.assertIn("Previously: Deposit Paid", body)
        self.assertIn("Now: Fully Paid", body)

    def test_booking_with_no_events_still_renders(self) -> None:
        """Bookings that predate the audit trail must keep working."""
        self._seed_booking(booking_id="B-TL-LEGACY")
        page = self.client.get("/bookings/B-TL-LEGACY")
        self.assertEqual(page.status_code, 200)
        self.assertIn("No booking events recorded yet.", page.get_data(as_text=True))

    def test_legacy_events_without_metadata_still_render(self) -> None:
        self._seed_booking(booking_id="B-TL-OLD")
        with self.app.app_context():
            self.db.session.add(
                self.BookingEventTrail(
                    event_id="EVT-20240101-0001",
                    booking_id="B-TL-OLD",
                    traveler_id="TR100",
                    event_type="booking_draft_created",
                    event_label="Booking draft created",
                    notes="Legacy entry with no structured metadata.",
                )
            )
            self.db.session.commit()

        page = self.client.get("/bookings/B-TL-OLD")
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn("Booking draft created", body)
        self.assertIn("Legacy entry with no structured metadata.", body)

    def test_event_ids_do_not_collide_with_the_legacy_dated_sequence(self) -> None:
        """UnifiedCRMService mints EVT-YYYYMMDD-NNNN into the same table."""
        self._seed_booking()
        with self.app.app_context():
            self.db.session.add(
                self.BookingEventTrail(
                    event_id="EVT-20240101-0001",
                    booking_id="B-TL-1",
                    event_type="legacy",
                    event_label="Legacy",
                )
            )
            self.db.session.commit()

        self.client.post("/bookings/B-TL-1/status", data={"booking_status": "Cancelled"})
        events = self._events("B-TL-1", "booking_status_changed")
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0].event_id.startswith("EVT"))
        self.assertNotEqual(events[0].event_id, "EVT-20240101-0001")

    def _enforce_sqlite_foreign_keys(self) -> None:
        """SQLite ignores foreign keys unless asked; Postgres never does.

        Registered on connect rather than executed once, so every connection
        the pool hands out afterwards is covered.
        """
        from sqlalchemy import event as sa_event

        sa_event.listen(
            self.db.engine,
            "connect",
            lambda dbapi_conn, _: dbapi_conn.execute("PRAGMA foreign_keys=ON"),
        )
        self.db.session.remove()
        self.db.engine.dispose()

    def test_auto_created_booking_event_survives_real_foreign_keys(self) -> None:
        """The auto-create path registers its event *before* adding the
        booking to the session, so this is the ordering most likely to insert
        an event whose booking does not exist yet.
        """
        with self.app.app_context():
            self._enforce_sqlite_foreign_keys()
            self.db.session.add(
                self.Lead(
                    lead_id="L-TL-FK",
                    customer_name="Timeline Traveler",
                    lead_stage="Qualified",
                    traveler_id="TR100",
                    interested_trip_ids="TRIP-100",
                    group_size=2,
                )
            )
            self.db.session.commit()

        response = self.client.post("/leads/L-TL-FK", data={"lead_stage": "Booking Draft"})
        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            booking = self.TripBooking.query.filter_by(lead_id="L-TL-FK").one()
            events = self.BookingEventTrail.query.filter_by(
                booking_id=booking.booking_id, event_type="booking_auto_created"
            ).all()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].lead_id, "L-TL-FK")

    def test_creation_event_is_inserted_after_its_booking_under_real_foreign_keys(self) -> None:
        """booking_event_trail's foreign key columns alone do not tell the
        unit of work that an event INSERT depends on its booking's -- only
        relationships do. Without them SQLAlchemy is free to insert the event
        first, which SQLite silently tolerates (foreign keys off by default)
        and Postgres rejects outright. This creates a traveler and a booking
        in the same flush as the event they trigger, with enforcement on.
        """
        with self.app.app_context():
            self._enforce_sqlite_foreign_keys()
            self.db.session.add(
                self.Traveler(traveler_id="TR-FK", full_name="Foreign Key Traveler")
            )
            self.db.session.add(
                self.TripBooking(
                    booking_id="B-TL-FK",
                    trip_id="TRIP-100",
                    trip_name="Timeline Trip",
                    traveler_id="TR-FK",
                    traveler_name="Foreign Key Traveler",
                    room_type="Double",
                    booking_status="Draft",
                    payment_status="Pending",
                )
            )
            self.db.session.commit()

        events = self._events("B-TL-FK", "booking_created")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].traveler_id, "TR-FK")

    # -- the audit layer itself ------------------------------------------

    def test_suspending_the_audit_records_nothing(self) -> None:
        self._seed_booking()
        with self.app.app_context():
            with self.booking_audit.suspend_booking_audit():
                booking = self.db.session.get(self.TripBooking, "B-TL-1")
                booking.booking_status = "Cancelled"
                self.db.session.commit()
        self.assertEqual(self._events("B-TL-1"), [])

    def test_explicit_actor_is_used_outside_a_request(self) -> None:
        self._seed_booking()
        with self.app.app_context():
            with self.booking_audit.booking_audit_actor("nightly-job"):
                booking = self.db.session.get(self.TripBooking, "B-TL-1")
                booking.booking_status = "Cancelled"
                self.db.session.commit()

        events = self._events("B-TL-1", "booking_status_changed")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].actor, "nightly-job")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
