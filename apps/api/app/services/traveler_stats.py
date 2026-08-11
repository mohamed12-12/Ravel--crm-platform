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

import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from flask import current_app

from app.extensions import db
from app.models.booking import CEBooking, TripBooking
from app.models.traveler import Traveler
from app.models.trip import Trip

_REVENUE_BOOKING_STATUSES = {"confirmed", "paid", "completed"}
_REVENUE_PAYMENT_STATUSES = {"fully paid", "paid"}


def _parse_money(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    cleaned = re.sub(r"[^0-9.]", "", text.replace(",", ""))
    if not cleaned:
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _recalculate_postgres(traveler_id: str) -> None:
    traveler = db.session.get(Traveler, traveler_id)
    if not traveler:
        return

    rows = (
        db.session.query(TripBooking, Trip.type, Trip.public_price)
        .join(Trip, Trip.trip_id == TripBooking.trip_id)
        .filter(TripBooking.traveler_id == traveler_id)
        .all()
    )
    local = 0
    intl = 0
    revenue = 0.0
    for booking, trip_type, public_price in rows:
        status = (booking.booking_status or "").strip().lower()
        if status == "cancelled":
            continue
        normalized_type = (trip_type or "").strip().lower()
        if normalized_type == "local":
            local += 1
        elif normalized_type == "international":
            intl += 1
        payment_status = (booking.payment_status or "").strip().lower()
        if status in _REVENUE_BOOKING_STATUSES and payment_status in _REVENUE_PAYMENT_STATUSES:
            revenue += _parse_money(public_price) * max(booking.group_size or 1, 1)

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
