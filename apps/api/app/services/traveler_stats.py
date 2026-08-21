# app/services/traveler_stats.py
"""Recomputes a traveler's Travel Summary counters (local/international/total
trip counts, community events, lifetime revenue) from live booking data.

UnifiedCRMService.recalculate_traveler_stats always reads and writes the
legacy SQLite file at services/crm/system_services's configured db_path. In
production SQLALCHEMY_DATABASE_URI points at Postgres, so that SQLite path is
a different, effectively empty database -- the call silently no-ops against
data that was never written there, leaving the traveler's stored counters
stale (e.g. a booking marked Completed/Fully Paid never bumps the Travel
Summary widget). Same class of split-brain bug as the one already guarded
against in app/routes/crm.py's _agent_runtime() and app/routes/admin.py's
db_health(); this mirrors that same backend check.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from flask import current_app
from sqlalchemy import update

from app.extensions import db
from app.models.booking import CEBooking, TripBooking
from app.models.private_trip_request import PrivateTripRequest
from app.models.traveler import Traveler
from app.models.trip import Trip
from app.services.revenue import booking_revenue
from services.crm.system_services.private_trips import is_committed_private_trip


def _recalculate_postgres(traveler_id: str) -> None:
    traveler = db.session.get(Traveler, traveler_id)
    if not traveler:
        return

    bookings = TripBooking.query.filter(TripBooking.traveler_id == traveler_id).all()
    trip_ids = {b.trip_id for b in bookings if b.trip_id}
    trips = {t.trip_id: t for t in Trip.query.filter(Trip.trip_id.in_(trip_ids)).all()} if trip_ids else {}
    # Additional fees are part of what the booking earned, so they belong in
    # this counter too -- otherwise the stored figure disagrees with the live
    # revenue summary on the same profile page about the same booking.
    from app.services.additional_fees import fees_for_bookings

    booking_fees = fees_for_bookings([b.booking_id for b in bookings if b.booking_id])

    local = 0
    intl = 0
    revenue = 0.0
    for booking in bookings:
        status = (booking.booking_status or "").strip().lower()
        if status == "cancelled":
            continue
        trip = trips.get(booking.trip_id)
        normalized_type = (trip.type or "").strip().lower() if trip else ""
        if normalized_type == "local":
            local += 1
        elif normalized_type == "international":
            intl += 1
        # booking_revenue() is the same rule Revenue Analytics and the live
        # per-traveler revenue summary use -- room/currency-specific
        # pricing, not just Trip.public_price, and it requires a currency
        # this booking actually recognizes. Using anything else here is
        # exactly how this figure used to silently drift from what the rest
        # of the CRM shows for the same booking.
        result = booking_revenue(booking, trip, booking_fees.get(booking.booking_id, []))
        if result is not None:
            _currency, amount = result
            revenue += amount

    comm_events = (
        db.session.query(CEBooking)
        .filter(CEBooking.traveler_id == traveler_id)
        .filter(db.func.lower(db.func.coalesce(CEBooking.status, "")) != "cancelled")
        .count()
    )

    traveler.local_trips_count = local
    traveler.international_trips_count = intl
    traveler.total_trips = local + intl
    traveler.community_events_count = comm_events
    traveler.lifetime_revenue = revenue
    db.session.commit()


def _recalculate_sqlite(traveler_id: str, uri: str) -> None:
    from services.crm.system_services.config import load_system_settings
    from services.crm.system_services.unified_service import UnifiedCRMService

    db_path = Path(uri.removeprefix("sqlite:///")).resolve()
    system_settings = replace(load_system_settings(), db_path=db_path)
    UnifiedCRMService(system_settings).recalculate_traveler_stats(traveler_id)


def _recalculate_private_trip_count(traveler_id: str) -> None:
    """Refresh the traveler's private-trip counter from live private requests.

    Runs for both backends, unlike the booking counters above. The legacy
    SQLite path routes through UnifiedCRMService, which knows nothing about
    private trip requests, so a private trip would otherwise never appear on a
    profile in demo/dev at all. Private requests live in the same database as
    the Flask app either way, so one ORM read answers it.

    Written with a targeted UPDATE rather than through the loaded Traveler
    object on purpose: the SQLite path has just rewritten the other counters
    on this same row through a separate raw connection, and flushing a stale
    in-session copy of the row would undo that.
    """
    requests = PrivateTripRequest.query.filter(
        PrivateTripRequest.traveler_id == traveler_id
    ).all()
    if requests:
        from app.services.private_trip_ledger import totals_for

        paid_by_request = totals_for([item.request_id for item in requests])
    else:
        paid_by_request = {}

    count = 0
    for item in requests:
        totals = paid_by_request.get(item.request_id)
        if is_committed_private_trip(item.stage, total_paid=totals.total_paid if totals else 0.0):
            count += 1

    db.session.execute(
        update(Traveler)
        .where(Traveler.traveler_id == traveler_id)
        .values(private_trips_count=count)
    )
    db.session.commit()


def recalculate_traveler_stats(traveler_id: str) -> None:
    """Refresh one traveler's trip/event/revenue counters from live bookings,
    against whichever backend SQLALCHEMY_DATABASE_URI actually points at."""
    if not traveler_id:
        return
    uri = str(current_app.config.get("SQLALCHEMY_DATABASE_URI") or "")
    if uri.startswith("sqlite:///"):
        _recalculate_sqlite(traveler_id, uri)
    else:
        _recalculate_postgres(traveler_id)
    _recalculate_private_trip_count(traveler_id)
