# app/services/revenue.py
"""Shared revenue-recognition rules.

Canonical source for "does this booking count as revenue, and for how
much" -- used by both the per-traveler Lifetime Revenue figure
(app/routes/travelers.py) and the company-wide Revenue Analytics dashboard
(app/routes/admin.py). Kept in one place so the two views can never
silently disagree on what counts as revenue.
"""
from __future__ import annotations

from datetime import datetime

from services.crm.system_services.trip_pricing import price_for_room_and_currency

REVENUE_BOOKING_STATUSES = {"confirmed", "paid", "completed"}
REVENUE_PAYMENT_STATUSES = {"fully paid", "paid"}
REVENUE_CURRENCIES = {"USD", "EGP"}


def parse_money(value: str | int | float | None) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return 0.0
    cleaned = "".join(ch for ch in text if ch.isdigit() or ch in ".-")
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0


def booking_revenue(booking, trip) -> tuple[str, float] | None:
    """Return (currency, amount) if this booking counts as recognized
    revenue, else None. `trip` may be None (booking with no linked trip)."""
    booking_status = str(booking.booking_status or "").strip().lower()
    payment_status = str(booking.payment_status or "").strip().lower()
    if booking_status not in REVENUE_BOOKING_STATUSES or payment_status not in REVENUE_PAYMENT_STATUSES:
        return None
    currency = str(booking.currency or "").strip().upper()
    if currency not in REVENUE_CURRENCIES:
        return None
    price = price_for_room_and_currency(
        trip.to_dict() if trip else {},
        room_type=booking.room_type or "",
        currency=currency,
    )
    amount = parse_money(price) * int(booking.group_size or 1)
    return currency, amount


def booking_recognized_at(booking, status_history_by_booking: dict[str, list]) -> datetime:
    """The date this booking's revenue should be attributed to: the earliest
    time its status entered a revenue-counting state, falling back to when
    the booking was first drafted for records with no status history (e.g.
    legacy imports created directly in a paid state)."""
    history = status_history_by_booking.get(booking.booking_id) or []
    qualifying = [
        entry.changed_at
        for entry in history
        if entry.changed_at and str(entry.new_status or "").strip().lower() in REVENUE_BOOKING_STATUSES
    ]
    if qualifying:
        return min(qualifying)
    return booking.draft_created_at
