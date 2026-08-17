# services/crm/system_services/revenue_rules.py
"""Shared revenue-recognition rules.

Canonical source for "does this booking count as revenue, and for how
much" -- used by the per-traveler Lifetime Revenue figure (both the
Postgres path in apps/api/app/services/traveler_stats.py and the legacy
SQLite path in this module's own UnifiedCRMService.recalculate_traveler_stats),
the live per-traveler revenue summary and company-wide Revenue Analytics
dashboard (apps/api/app/routes/travelers.py, apps/api/app/routes/admin.py).
Lives here (backend-agnostic, no Flask/app import) rather than under
apps/api/app/services so this module -- part of the shared `services`
package used outside the Flask app too -- never has to import back into a
Flask-specific package to reuse it. apps/api/app/services/revenue.py
re-exports these names so existing call sites are unaffected.

Kept in exactly one place so none of these views can ever silently
disagree on what counts as revenue.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from .trip_pricing import price_for_room_and_currency

REVENUE_BOOKING_STATUSES = {"confirmed", "paid", "completed"}
# Payment statuses meaning "paid in full, nothing returned". Left exactly as it
# was: it still answers "has this booking been paid for", which is what the
# Needs Attention check and several callers actually ask.
REVENUE_PAYMENT_STATUSES = {"fully paid", "paid"}
# Payment statuses meaning "was paid, then some or all of it was returned".
# These earn gross revenue too -- the money did arrive -- and then have the
# refund taken back off. Mirrors app/routes/bookings.py's
# REFUND_PAYMENT_STATUSES, lower-cased for comparison here.
REFUNDED_PAYMENT_STATUSES = {"partial refund", "full refund", "refunded"}
# The set that actually gates recognition. Splitting it this way is the whole
# fix: a booking used to vanish from revenue entirely the moment its payment
# status became a refund, so a 1,000 booking with a 120 refund reported 0
# rather than 880, and a partial refund was indistinguishable from a full one.
RECOGNIZED_PAYMENT_STATUSES = REVENUE_PAYMENT_STATUSES | REFUNDED_PAYMENT_STATUSES
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


@dataclass(frozen=True)
class RevenueBreakdown:
    """Gross, refunds and net for one recognized booking, in one currency.

    Kept as three numbers rather than a single "revenue" figure so no surface
    has to choose between showing the money that came in and the money that
    stayed. net is derived, never stored, so the three can never disagree.
    """

    currency: str
    gross: float
    refunds: float
    refunds_recorded: float

    @property
    def net(self) -> float:
        return self.gross - self.refunds

    @property
    def refund_exceeds_gross(self) -> bool:
        """A stored refund larger than the booking was ever worth.

        Possible in legacy data predating the Phase 2 ceiling. Reported rather
        than trusted -- see refunds vs refunds_recorded below.
        """
        return self.refunds_recorded > self.refunds + 0.005


def refund_total(booking) -> float:
    """The amount refunded on this booking, as a running total.

    `refund_amount` is one overwritten scalar, not a ledger of transactions:
    editing it from 100 to 150 means 150 has been refunded in all, not 250.
    Anything unusable -- unset, negative, NaN from a legacy row -- counts as
    no refund rather than silently corrupting every total it feeds.
    """
    raw = getattr(booking, "refund_amount", None)
    if raw is None:
        return 0.0
    try:
        amount = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(amount) or amount <= 0:
        return 0.0
    return amount


def booking_revenue_breakdown(booking, trip) -> RevenueBreakdown | None:
    """Gross / refunds / net for one booking, or None if it is not revenue.

    The single source of truth for every revenue surface in the CRM. `trip`
    may be None (booking with no linked trip); when not None it must expose a
    `.to_dict()` returning at least `public_price` and
    `room_prices`/`room_prices_json` -- true of the SQLAlchemy Trip model and
    of any lightweight stand-in built for a non-ORM backend.

    A refunded booking is still recognized, and its refund is subtracted --
    that is what stops a partial refund erasing the whole booking. Booking
    status is unchanged as a gate: a Cancelled or Draft booking is not revenue
    at all, so its refund has nothing to come off and must not create negative
    revenue out of nothing.
    """
    booking_status = str(booking.booking_status or "").strip().lower()
    payment_status = str(booking.payment_status or "").strip().lower()
    if booking_status not in REVENUE_BOOKING_STATUSES or payment_status not in RECOGNIZED_PAYMENT_STATUSES:
        return None
    currency = str(booking.currency or "").strip().upper()
    if currency not in REVENUE_CURRENCIES:
        return None
    price = price_for_room_and_currency(
        trip.to_dict() if trip else {},
        room_type=booking.room_type or "",
        currency=currency,
    )
    gross = parse_money(price) * int(booking.group_size or 1)
    recorded = refund_total(booking)
    # Cap what is actually deducted at the booking's value. Legacy rows can
    # carry a refund larger than the booking was ever worth (the Phase 2
    # ceiling only governs new saves), and letting one of those through would
    # push a traveler's Lifetime Revenue negative and drag down every company
    # total it rolls into. The excess stays visible via refunds_recorded.
    applied = min(recorded, gross) if gross > 0 else 0.0
    return RevenueBreakdown(
        currency=currency, gross=gross, refunds=applied, refunds_recorded=recorded
    )


def booking_revenue(booking, trip) -> tuple[str, float] | None:
    """(currency, NET revenue) for a recognized booking, else None.

    Net -- gross less refunds -- because that is what every existing caller
    means by "revenue": what the business actually kept. Callers needing the
    components use booking_revenue_breakdown() or booking_gross_revenue().
    """
    breakdown = booking_revenue_breakdown(booking, trip)
    if breakdown is None:
        return None
    return breakdown.currency, breakdown.net


def booking_gross_revenue(booking, trip) -> tuple[str, float] | None:
    """(currency, GROSS revenue) -- before refunds.

    Use this, not net, to judge whether a booking is priced at all: a fully
    refunded booking nets zero while being perfectly well configured.
    """
    breakdown = booking_revenue_breakdown(booking, trip)
    if breakdown is None:
        return None
    return breakdown.currency, breakdown.gross


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
