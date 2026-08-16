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

from app.extensions import db
from app.models.booking import CEBooking, TripBooking
from app.models.traveler import Traveler
from app.models.trip import Trip
from app.services.revenue import booking_revenue


def _recalculate_postgres(traveler_id: str) -> None:
    traveler = db.session.get(Traveler, traveler_id)
    if not traveler:
        return

    bookings = TripBooking.query.filter(TripBooking.traveler_id == traveler_id).all()
    trip_ids = {b.trip_id for b in bookings if b.trip_id}
    trips = {t.trip_id: t for t in Trip.query.filter(Trip.trip_id.in_(trip_ids)).all()} if trip_ids else {}

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
        result = booking_revenue(booking, trip)
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


def recalculate_traveler_stats(traveler_id: str) -> None:
    """Refresh one traveler's trip/event/revenue counters from live bookings,
    against whichever backend SQLALCHEMY_DATABASE_URI actually points at."""
    if not traveler_id:
        return
    uri = str(current_app.config.get("SQLALCHEMY_DATABASE_URI") or "")
    if uri.startswith("sqlite:///"):
        _recalculate_sqlite(traveler_id, uri)
        return
    _recalculate_postgres(traveler_id)
