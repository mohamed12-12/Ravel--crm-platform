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

from .payment_rules import REFUND_PAYMENT_STATUSES
from .trip_pricing import price_for_room_and_currency

REVENUE_BOOKING_STATUSES = {"confirmed", "paid", "completed"}
# Payment statuses meaning "paid in full, nothing returned". Left exactly as it
# was: it still answers "has this booking been paid for", which is what the
# Needs Attention check and several callers actually ask.
REVENUE_PAYMENT_STATUSES = {"fully paid", "paid"}
# Payment statuses meaning "was paid, then some or all of it was returned".
# These earn gross revenue too -- the money did arrive -- and then have the
# refund taken back off. Derived from payment_rules rather than restated, so
# adding a refund spelling there can never leave revenue recognition behind.
REFUNDED_PAYMENT_STATUSES = {status.lower() for status in REFUND_PAYMENT_STATUSES}
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

    `fees` is the part of gross that came from employee-added additional fees
    rather than the trip's own room price. Carried separately -- gross already
    includes it -- so a surface can show why a booking is worth more than its
    trip's list price without having to recompute the fee total itself.
    """

    currency: str
    gross: float
    refunds: float
    refunds_recorded: float
    fees: float = 0.0

    @property
    def trip_value(self) -> float:
        """Gross excluding additional fees: the trip price x party size."""
        return self.gross - self.fees

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


def active_fee_total(fees, currency: str) -> float:
    """Sum the live additional fees denominated in `currency`.

    Voided fees are excluded (a fee taken off a booking must stop counting as
    revenue), and so is anything in another currency: two currencies cannot be
    added without inventing an exchange rate, and this system deliberately
    never does. The write path rejects a mismatched fee currency, so a
    non-matching row here means legacy or hand-edited data -- skipped rather
    than guessed at.
    """
    code = str(currency or "").strip().upper()
    if not code:
        return 0.0
    total = 0.0
    for fee in fees or ():
        if getattr(fee, "voided_at", None):
            continue
        if str(getattr(fee, "currency", "") or "").strip().upper() != code:
            continue
        amount = parse_money(getattr(fee, "amount", 0.0))
        if math.isfinite(amount) and amount > 0:
            total += amount
    return total


def booking_revenue_breakdown(booking, trip, fees=None) -> RevenueBreakdown | None:
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
    # Additional fees (visa, insurance, transfers, upgrades) are part of what
    # the customer owes for this booking, so they are part of what it earns --
    # in the booking's own currency, never converted.
    fees = active_fee_total(fees, currency)
    gross = parse_money(price) * int(booking.group_size or 1) + fees
    recorded = refund_total(booking)
    # Cap what is actually deducted at the booking's value. Legacy rows can
    # carry a refund larger than the booking was ever worth (the Phase 2
    # ceiling only governs new saves), and letting one of those through would
    # push a traveler's Lifetime Revenue negative and drag down every company
    # total it rolls into. The excess stays visible via refunds_recorded.
    applied = min(recorded, gross) if gross > 0 else 0.0
    return RevenueBreakdown(
        currency=currency, gross=gross, refunds=applied, refunds_recorded=recorded, fees=fees
    )


def booking_revenue(booking, trip, fees=None) -> tuple[str, float] | None:
    """(currency, NET revenue) for a recognized booking, else None.

    Net -- gross less refunds -- because that is what every existing caller
    means by "revenue": what the business actually kept. Callers needing the
    components use booking_revenue_breakdown() or booking_gross_revenue().
    """
    breakdown = booking_revenue_breakdown(booking, trip, fees)
    if breakdown is None:
        return None
    return breakdown.currency, breakdown.net


def booking_gross_revenue(booking, trip, fees=None) -> tuple[str, float] | None:
    """(currency, GROSS revenue) -- before refunds.

    Use this, not net, to judge whether a booking is priced at all: a fully
    refunded booking nets zero while being perfectly well configured.
    """
    breakdown = booking_revenue_breakdown(booking, trip, fees)
    if breakdown is None:
        return None
    return breakdown.currency, breakdown.gross


@dataclass(frozen=True)
class PrivateTripMoney:
    """What one private trip request is worth, and what has actually been paid.

    A private trip is priced by hand (there is no Trip row, no room price
    table) and settled in instalments, so its money is a *ledger*, not a
    status. That difference drives the one rule that matters here:

        **Revenue is money recorded as received, never the agreed price.**

    A 45,000 EGP private trip with a 15,000 deposit is 15,000 of revenue and
    30,000 of outstanding balance. Counting the price would report money the
    company does not have, which is precisely what "do not make it revenue
    until it is paid" rules out. The agreed price is still carried here -- it
    is what outstanding balance and the derived payment status are measured
    against -- but it is never itself revenue.

    `currency` is the one currency this request's money lives in. EGP and USD
    are never added together and no exchange rate is ever applied, so a
    request is denominated once and its payments must match; the write path
    enforces that, and `currency_conflict` reports any legacy row where it
    does not hold.
    """

    currency: str
    price: float | None
    fees: float
    paid: float
    refunded: float
    entry_count: int
    currency_conflict: bool = False

    @property
    def contract_value(self) -> float | None:
        """Agreed price plus additional fees -- the full amount owed."""
        if self.price is None:
            return None
        return self.price + self.fees

    @property
    def gross_revenue(self) -> float:
        """Money received. Recognized on receipt, per the rule above."""
        return self.paid

    @property
    def net_revenue(self) -> float:
        """Money received and kept. Clamped at zero: a refund can never
        exceed the payments it is refunding (the write path enforces it), so a
        negative here would be corrupt legacy data, and letting it through
        would drag down every company total it rolls into."""
        return max(self.paid - self.refunded, 0.0)

    @property
    def outstanding(self) -> float | None:
        """What is still owed, or None when no price has been agreed.

        None is not zero: "we have not priced this yet" and "this is settled"
        are different facts, and reporting the first as the second would show
        a fully collected trip that nobody has quoted.
        """
        value = self.contract_value
        if value is None:
            return None
        return max(value - self.paid, 0.0)

    @property
    def is_fully_collected(self) -> bool:
        value = self.contract_value
        return value is not None and value > 0 and self.paid + 0.005 >= value

    @property
    def payment_status(self) -> str:
        from .payment_rules import derive_payment_status

        return derive_payment_status(self.contract_value, self.paid, self.refunded)

    @property
    def has_money(self) -> bool:
        return self.entry_count > 0


def private_trip_money(
    *,
    price: float | int | str | None,
    price_currency: str | None,
    fees=None,
    total_paid: float = 0.0,
    total_refunded: float = 0.0,
    entry_count: int = 0,
    ledger_currency: str | None = None,
    fallback_currency: str | None = None,
) -> PrivateTripMoney:
    """Assemble one private request's money picture from its parts.

    The currency is resolved from the strongest available evidence, in order:
    the currency money actually arrived in, then the currency it was priced
    in, then a fallback (the customer's stated budget currency) which is used
    for display only -- with no price and no payments there is nothing to
    report either way.
    """
    resolved_ledger = str(ledger_currency or "").strip().upper()
    resolved_price = str(price_currency or "").strip().upper()
    currency = resolved_ledger or resolved_price or str(fallback_currency or "").strip().upper()
    if currency not in REVENUE_CURRENCIES:
        currency = ""

    amount = parse_money(price) if price is not None else 0.0
    priced = amount if (price is not None and math.isfinite(amount) and amount > 0) else None
    return PrivateTripMoney(
        currency=currency,
        price=priced,
        fees=active_fee_total(fees, currency),
        paid=max(float(total_paid or 0.0), 0.0),
        refunded=max(float(total_refunded or 0.0), 0.0),
        entry_count=int(entry_count or 0),
        # Money in one currency against a price in another cannot be netted
        # without an exchange rate. Flagged for a human rather than guessed.
        currency_conflict=bool(resolved_ledger and resolved_price and resolved_ledger != resolved_price),
    )


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
