# services/crm/system_services/payment_rules.py
"""Shared payment-status vocabulary and transition rules.

Canonical home for "what payment statuses exist, which ones mean a refund,
and which changes between them are legal". Lives here rather than under
apps/api/app so both booking write paths can enforce the same rules: the
Flask route (app/routes/bookings.py) and the backend-agnostic
UnifiedCRMService.update_booking_status, which is part of this same
`services` package and cannot import back into a Flask-specific one.

That split is the reason this module exists. The transition rules used to
live only in the route, so UnifiedCRMService could move a booking straight
from Fully Paid to Deposit Paid -- backwards, no validation, no permission
check, no audit event -- walking past every guard the route enforces. Same
arrangement, and same fix, as revenue_rules.py: one definition, imported by
everyone who needs it.
"""
from __future__ import annotations

DEFAULT_PAYMENT_STATUS = "Pending"

# Payment statuses that carry a refund amount. 'Refunded' is a legacy
# spelling that is still a valid PAYMENT_STATUSES member and still selectable
# in the UI -- leaving it out of this set once meant choosing it silently
# nulled an already-recorded refund amount.
REFUND_PAYMENT_STATUSES = {"Full Refund", "Partial Refund", "Refunded"}

PAYMENT_STATUSES = [
    "Pending",
    "Deposit Paid",
    "Fully Paid",
    "Partial Refund",
    "Full Refund",
    "Refunded",
]

# Legal forward moves. Refund statuses are terminal apart from Partial ->
# Full, because money only travels one way once it has been returned.
PAYMENT_TRANSITIONS: dict[str, set[str]] = {
    "Pending": {"Deposit Paid", "Fully Paid", "Partial Refund", "Full Refund", "Refunded"},
    "Deposit Paid": {"Fully Paid", "Partial Refund", "Full Refund", "Refunded"},
    "Fully Paid": {"Partial Refund", "Full Refund", "Refunded"},
    "Partial Refund": {"Full Refund"},
    "Full Refund": set(),
    "Refunded": set(),
}


def normalize_payment_status(value: str | None) -> str:
    return str(value or "").strip() or DEFAULT_PAYMENT_STATUS


def is_refund_status(value: str | None) -> bool:
    return str(value or "").strip() in REFUND_PAYMENT_STATUSES


def validate_payment_transition(
    current_status: str | None,
    new_status: str | None,
    *,
    allow_employee_correction: bool = False,
    correction_note: str = "",
) -> None:
    """Raise ValueError unless this payment status change is allowed.

    An employee may still force a non-standard change, but only with a
    written reason -- the same override convention the booking-status
    transition check uses, so both behave identically from the employee's
    side.
    """
    current = normalize_payment_status(current_status)
    target = str(new_status or "").strip()
    if not target or target == current:
        return
    if target in PAYMENT_TRANSITIONS.get(current, set()):
        return
    if allow_employee_correction and target in PAYMENT_STATUSES:
        if not str(correction_note or "").strip():
            raise ValueError("Add a reason before making a non-standard payment status change.")
        return
    raise ValueError(f"Invalid payment status transition: {current} -> {target}")


def derive_payment_status(
    contract_value: float | None,
    total_paid: float,
    total_refunded: float,
) -> str:
    """The payment status implied by recorded money, from the same vocabulary.

    Used where the money is a ledger rather than a hand-picked label (private
    trip requests): the status is then a *reading* of the transactions, so it
    can never claim something the ledger does not support. Nobody types "Fully
    Paid" here -- it is true only when the receipts add up to the agreed price.

    `contract_value` is the price plus additional fees, or None when no price
    has been agreed yet. With no price there is no way to tell a deposit from
    a settled balance, so money received reads as "Deposit Paid": understating
    is the only safe direction when the claim is about someone's money.

    Refunds outrank everything, because "we gave it back" is the most
    important fact about a payment. A refund covering everything received is a
    Full Refund; anything less is Partial.
    """
    paid = max(float(total_paid or 0.0), 0.0)
    refunded = max(float(total_refunded or 0.0), 0.0)
    if refunded > 0:
        # >= rather than == so a rounding-tail difference on a genuinely full
        # refund is not reported as partial.
        return "Full Refund" if refunded + 0.005 >= paid else "Partial Refund"
    if paid <= 0:
        return DEFAULT_PAYMENT_STATUS
    if contract_value is None or float(contract_value) <= 0:
        return "Deposit Paid"
    return "Fully Paid" if paid + 0.005 >= float(contract_value) else "Deposit Paid"


def payment_state_token(payment_status: str | None, refund_amount: float | None) -> str:
    """A fingerprint of a booking's money fields, for conflict detection.

    The booking form's existing `expected_history_count` guard counts
    booking_status_history rows, and those are written only when
    booking_status changes -- so two employees editing a refund at the same
    time both passed the check and the later save silently overwrote the
    earlier one. This token covers the fields that guard misses.

    Deliberately human-readable rather than hashed: it shows up in form
    payloads and logs, and there is nothing secret in it.
    """
    status = normalize_payment_status(payment_status)
    if refund_amount is None or str(refund_amount).strip() == "":
        refund = "none"
    else:
        try:
            refund = f"{float(refund_amount):.2f}"
        except (TypeError, ValueError):
            refund = "invalid"
    return f"{status}|{refund}"
