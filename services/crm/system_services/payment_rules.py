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
