# app/services/additional_fees.py
"""Adding, voiding and totalling additional fees.

One module owns every fee write so the validation cannot be applied on one
page and forgotten on the other: a fee is checked identically whether it is
added to a trip booking or to a private trip request. The rules themselves
(what a valid amount is, what currency is allowed, what the categories are)
live in services/crm/system_services/fee_rules.py, which is backend-agnostic
and also readable by the revenue engine.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db
from app.models.additional_fee import AdditionalFee
from services.crm.system_services.fee_rules import (
    normalize_fee_category,
    parse_fee_amount,
    validate_fee_currency,
    validate_fee_label,
)
from services.crm.system_services.revenue_rules import active_fee_total


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)


def fees_for_booking(booking_id: str) -> list[AdditionalFee]:
    """Every fee on a booking, live and voided, newest first.

    Voided rows are included deliberately: the fee history is part of the
    booking's money story, and an employee looking for a fee that was removed
    needs to see that it was, by whom and why.
    """
    if not booking_id:
        return []
    return (
        AdditionalFee.query.filter(AdditionalFee.booking_id == booking_id)
        .order_by(AdditionalFee.created_at.desc(), AdditionalFee.fee_id.desc())
        .all()
    )


def fees_for_private_request(request_id: str) -> list[AdditionalFee]:
    if not request_id:
        return []
    return (
        AdditionalFee.query.filter(AdditionalFee.private_request_id == request_id)
        .order_by(AdditionalFee.created_at.desc(), AdditionalFee.fee_id.desc())
        .all()
    )


def _grouped(rows, key_attr: str, keys) -> dict[str, list[AdditionalFee]]:
    grouped: dict[str, list[AdditionalFee]] = {key: [] for key in keys}
    for row in rows:
        grouped.setdefault(getattr(row, key_attr), []).append(row)
    return grouped


def fees_for_bookings(booking_ids) -> dict[str, list[AdditionalFee]]:
    """Fees for many bookings in one query -- for the revenue aggregation.

    Revenue Analytics reads every booking in the system; one query per booking
    here would make the page's cost scale with the whole booking table.
    """
    ids = [value for value in (booking_ids or []) if value]
    if not ids:
        return {}
    rows = AdditionalFee.query.filter(AdditionalFee.booking_id.in_(ids)).all()
    return _grouped(rows, "booking_id", ids)


def fees_for_private_requests(request_ids) -> dict[str, list[AdditionalFee]]:
    ids = [value for value in (request_ids or []) if value]
    if not ids:
        return {}
    rows = AdditionalFee.query.filter(AdditionalFee.private_request_id.in_(ids)).all()
    return _grouped(rows, "private_request_id", ids)


def active_fees(fees) -> list[AdditionalFee]:
    return [fee for fee in (fees or []) if fee.is_active]


def fee_total(fees, currency: str) -> float:
    """Live fees in `currency`. Same helper the revenue rules use."""
    return active_fee_total(fees, currency)


def add_fee(
    *,
    booking=None,
    private_request=None,
    label: str,
    amount,
    currency: str | None,
    category: str | None = None,
    notes: str | None = None,
    actor_user_id: int | None = None,
    actor_name: str | None = None,
) -> AdditionalFee:
    """Record one additional fee. Raises ValueError with a message for the UI.

    Exactly one parent must be supplied. The parent's currency is what the fee
    is denominated in -- a booking with no currency set cannot take fees at
    all, because there would be no total to add the fee to.
    """
    if (booking is None) == (private_request is None):
        raise ValueError("A fee belongs to either a booking or a private trip request.")

    if booking is not None:
        parent_currency = booking.currency
        parent_label = "booking"
        traveler_id = booking.traveler_id
    else:
        # A private request is denominated by its agreed price. Money already
        # recorded in the ledger counts too, so a request that has taken a
        # deposit before being formally priced can still take fees.
        parent_currency = private_request.agreed_price_currency
        if not parent_currency:
            from app.services.private_trip_ledger import ledger_totals

            parent_currency = ledger_totals(private_request.request_id).currency
        parent_label = "private request"
        traveler_id = private_request.traveler_id

    resolved_currency = validate_fee_currency(currency, parent_currency, parent_label=parent_label)
    fee = AdditionalFee(
        booking_id=booking.booking_id if booking is not None else None,
        private_request_id=private_request.request_id if private_request is not None else None,
        traveler_id=traveler_id,
        label=validate_fee_label(label),
        category=normalize_fee_category(category),
        amount=parse_fee_amount(amount),
        currency=resolved_currency,
        notes=str(notes or "").strip() or None,
        created_at=_utc_now(),
        created_by_user_id=actor_user_id,
        created_by=str(actor_name or "").strip() or None,
    )
    db.session.add(fee)
    return fee


def void_fee(
    fee: AdditionalFee,
    *,
    reason: str,
    actor_user_id: int | None = None,
) -> AdditionalFee:
    """Take a fee off its parent without erasing that it was ever there.

    A reason is required: removing a fee changes what the customer owes and
    what a past month earned, so "why" is not optional.
    """
    if fee is None:
        raise ValueError("Fee not found.")
    if not fee.is_active:
        raise ValueError(f"{fee.public_ref} is already voided.")
    cleaned = " ".join(str(reason or "").split())
    if not cleaned:
        raise ValueError("Add a reason for removing this fee.")
    fee.voided_at = _utc_now()
    fee.voided_by_user_id = actor_user_id
    fee.void_reason = cleaned
    return fee
