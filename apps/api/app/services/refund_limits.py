# app/services/refund_limits.py
"""How much of a booking may be refunded, and why.

The rule this enforces is "never give back more than was taken". Two facts
about the existing financial model shape how it can be enforced, and both are
worth stating plainly because they are not obvious from the schema:

**There is no record of how much a traveler actually paid.** The whole system
carries exactly one monetary column for a booking -- ``refund_amount`` -- plus
a categorical ``payment_status``. There is no payments table, no deposit
amount, no instalments, no invoices. ``scripts/migrate_excel_to_crm.py``'s
map_payment_status() shows the source spreadsheets *did* have real amounts
("Amount", "Amount #2") and the migration read them only to derive the
"Deposit Paid" / "Fully Paid" label before discarding the numbers. So
"Deposit Paid" records that *a* deposit was taken and nothing about its size.

That leaves exactly one status the system can price: "Fully Paid" means the
full booking value was received. For every other status the amount paid is
genuinely unknown, and the tightest defensible ceiling is the booking's total
contract value -- nobody can pay more than the booking costs. That bound is
sound (it never rejects a legitimate refund) without being tight (it will not
catch over-refunding a part-paid booking). resolve_amount_paid() below is the
single function that has to change the day a payments ledger exists.

**``refund_amount`` is a running total, not an instalment.** It is one scalar
that each save overwrites; there is no refund ledger, and the audit trail
records the change as "120.00 -> 150.00", a replacement. So a second refund is
entered as the new cumulative figure, and the invariant to enforce is
``total refunded <= amount paid`` -- which is the same "never give back more
than was taken" guarantee that per-instalment accounting would give, just
expressed against the field the CRM actually has.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.crm.system_services.revenue_rules import REVENUE_CURRENCIES, parse_money
from services.crm.system_services.trip_pricing import price_for_room_and_currency

# The only payment status that pins the amount received to a number. Kept as a
# mapping rather than an `if` so a future "Deposit Paid = 25%" business rule
# has an obvious home -- but note that no such rule exists anywhere in the
# codebase today, which is exactly why the others are absent.
_PAID_FRACTION_BY_STATUS: dict[str, float] = {
    "fully paid": 1.0,
}

# Why a particular ceiling was chosen. Surfaced in the error message so an
# employee is never told a number without being told what it means.
BASIS_RECORDED_PAID = "recorded_paid"
BASIS_BOOKING_VALUE = "booking_value"
BASIS_UNKNOWN = "unknown"


def format_money(amount: float | None, currency: str = "") -> str:
    """Render an amount the way the booking templates do: currency first.

    app/services/booking_audit.py's _format_money puts the currency last. That
    inconsistency predates this module and is left alone; validation messages
    sit next to the booking page's own figures, so they match those.
    """
    if amount is None:
        return ""
    code = str(currency or "").strip().upper()
    return f"{code} {amount:,.2f}".strip()


@dataclass(frozen=True)
class RefundAllowance:
    """What may be refunded on one booking, and the reasoning behind it."""

    currency: str
    booking_value: float | None
    amount_paid: float | None
    amount_paid_is_recorded: bool
    already_refunded: float
    maximum_refund: float | None
    basis: str

    @property
    def remaining_refundable(self) -> float | None:
        """Headroom left on top of what is already recorded as refunded.

        Not the cap on the input field -- that is ``maximum_refund``, because
        the field holds the cumulative total. This is the figure an employee
        thinks in ("how much more can I give back").
        """
        if self.maximum_refund is None:
            return None
        return max(0.0, self.maximum_refund - self.already_refunded)

    @property
    def is_enforceable(self) -> bool:
        return self.maximum_refund is not None


def booking_contract_value(
    trip: Any,
    *,
    room_type: str = "",
    currency: str = "",
    group_size: int | None = 1,
) -> float | None:
    """Total list value of a booking: room price for the currency x party size.

    Deliberately *not* revenue_rules.booking_revenue(): that function returns
    None unless the booking is already in a revenue-recognising status, and a
    refunded booking never is -- so it cannot price the very bookings that get
    refunded. This reuses the same pricing helpers rather than restating them,
    and does not touch revenue recognition in any way.

    Returns None when the value cannot be established (no trip, no room type,
    an unrecognised currency, or an unparseable price), so callers can tell
    "worth nothing" apart from "cannot tell".
    """
    normalized_currency = str(currency or "").strip().upper()
    if normalized_currency not in REVENUE_CURRENCIES:
        return None
    if trip is None:
        return None
    price = price_for_room_and_currency(
        trip.to_dict() if hasattr(trip, "to_dict") else trip,
        room_type=str(room_type or ""),
        currency=normalized_currency,
    )
    unit_price = parse_money(price)
    if unit_price <= 0:
        return None
    try:
        party = max(int(group_size or 1), 1)
    except (TypeError, ValueError):
        party = 1
    return unit_price * party


def resolve_amount_paid(
    payment_status: str | None,
    booking_value: float | None,
) -> tuple[float | None, bool]:
    """(amount paid, whether that figure is actually recorded).

    The single point to replace once real payment records exist. Everything
    else in this module consumes its output.
    """
    status = str(payment_status or "").strip().lower()
    fraction = _PAID_FRACTION_BY_STATUS.get(status)
    if fraction is not None and booking_value is not None:
        return booking_value * fraction, True
    return None, False


def refund_allowance(
    *,
    trip: Any = None,
    room_type: str = "",
    currency: str = "",
    group_size: int | None = 1,
    payment_status: str | None = None,
    already_refunded: float | None = None,
) -> RefundAllowance:
    """Work out the refund ceiling for one booking's effective field values.

    Takes loose values rather than a booking object so the update route can ask
    "what would the ceiling be if this save went through", using the trip, room
    type, currency and party size the employee is submitting rather than the
    ones currently stored.
    """
    normalized_currency = str(currency or "").strip().upper()
    value = booking_contract_value(
        trip, room_type=room_type, currency=normalized_currency, group_size=group_size
    )
    paid, paid_is_recorded = resolve_amount_paid(payment_status, value)

    if paid is not None:
        maximum: float | None = paid
        basis = BASIS_RECORDED_PAID
    elif value is not None:
        # Nobody can pay more than the booking costs, so its value is a sound
        # -- if loose -- upper bound on what could ever be given back.
        maximum = value
        basis = BASIS_BOOKING_VALUE
    else:
        maximum = None
        basis = BASIS_UNKNOWN

    return RefundAllowance(
        currency=normalized_currency,
        booking_value=value,
        amount_paid=paid,
        amount_paid_is_recorded=paid_is_recorded,
        already_refunded=float(already_refunded or 0.0),
        maximum_refund=maximum,
        basis=basis,
    )


def refund_allowance_for_booking(booking: Any, trip: Any = None) -> RefundAllowance:
    """The allowance for a booking exactly as it is stored right now."""
    return refund_allowance(
        trip=trip,
        room_type=getattr(booking, "room_type", "") or "",
        currency=getattr(booking, "currency", "") or "",
        group_size=getattr(booking, "group_size", 1),
        payment_status=getattr(booking, "payment_status", None),
        already_refunded=getattr(booking, "refund_amount", None),
    )


def refund_ceiling_error(amount: float, allowance: RefundAllowance) -> str:
    """The message shown when ``amount`` breaks the ceiling.

    Says which number was exceeded *and* where that number came from -- an
    employee told only "too high" cannot tell whether to correct the figure or
    fix the booking's price.
    """
    ceiling = format_money(allowance.maximum_refund, allowance.currency)
    if allowance.amount_paid_is_recorded:
        source = "the amount recorded as paid for this booking"
    else:
        source = "the total value of this booking"

    if allowance.already_refunded > 0:
        remaining = format_money(allowance.remaining_refundable, allowance.currency)
        already = format_money(allowance.already_refunded, allowance.currency)
        return (
            f"Refund amount is the total refunded for this booking and cannot exceed "
            f"{ceiling}, {source}. {already} is already recorded as refunded, "
            f"leaving {remaining} refundable."
        )
    return f"Refund amount cannot exceed {ceiling}, {source}."


def validate_refund_total(amount: float | None, allowance: RefundAllowance) -> None:
    """Raise ValueError if ``amount`` may not be stored as the total refunded.

    The amount is never quietly clamped to the maximum: an employee who typed
    the wrong figure needs to see that they did, not have the CRM pick a
    different number on their behalf.
    """
    if amount is None or allowance.maximum_refund is None:
        return
    # Tolerate float representation noise so an exactly-equal refund entered as
    # the displayed maximum is never rejected by a trailing 0.000000001.
    if amount > allowance.maximum_refund + 0.005:
        raise ValueError(refund_ceiling_error(amount, allowance))


def exceeds_allowance(booking: Any, trip: Any = None) -> bool:
    """Whether a booking's *stored* refund already breaks the rule.

    For reporting on legacy records. Deliberately read-only: historical refunds
    are left exactly as they are.
    """
    stored = getattr(booking, "refund_amount", None)
    if stored is None:
        return False
    allowance = refund_allowance_for_booking(booking, trip)
    if allowance.maximum_refund is None:
        return False
    return float(stored) > allowance.maximum_refund + 0.005
