# app/services/private_trip_money.py
"""The money picture for a private trip request, and the writes that change it.

Sits between the ledger (app/services/private_trip_ledger.py -- what was
recorded) and the pages that show or change money (the request page, the
traveler profile, Revenue Analytics). Every rule that decides whether a
payment or refund may be recorded is here, so the request page, a future API
and anything else cannot enforce different ones.

The rules, and why each exists:

* **A request is denominated in exactly one currency.** Whichever is
  established first -- the agreed price or the first payment -- fixes it, and
  everything after must match. EGP and USD are never added and no exchange
  rate exists anywhere in this system, so a request holding both would have no
  answer to "what is it worth".

* **A refund can never exceed the refundable money received.** Non-refundable
  deposits are excluded from the ceiling, which is the whole reason the
  private-trip deposit is marked non-refundable when it is taken.

* **A payment is never dated in the future.** Revenue is attributed to the day
  the money arrived; a future date would report income the company does not
  have yet, and it is almost always a typo in the year.

* **Nothing is ever edited.** A mistake is corrected with a reversal, which
  leaves both rows visible. Enforced by the ledger's session guard, not by
  convention here.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone

from app.extensions import db
from app.models.private_trip_transaction import (
    ENTRY_PAYMENT,
    ENTRY_REFUND,
    PRECISION_EXACT,
    SOURCE_CORRECTION,
    SOURCE_CRM_UI,
    PrivateTripTransaction,
)
from app.services.additional_fees import fees_for_private_request, fees_for_private_requests
from app.services.private_trip_ledger import (
    PrivateLedgerTotals,
    ledger_totals,
    summarize,
    totals_for,
)
from services.crm.system_services.private_trips import PRIVATE_BUDGET_CURRENCIES
from services.crm.system_services.revenue_rules import (
    PrivateTripMoney,
    REVENUE_CURRENCIES,
    private_trip_money,
)

PAYMENT_METHODS = ("bank_transfer", "instapay", "cash", "card", "wallet", "other")
PAYMENT_METHOD_LABELS = {
    "bank_transfer": "Bank transfer",
    "instapay": "InstaPay",
    "cash": "Cash",
    "card": "Card",
    "wallet": "Mobile wallet",
    "other": "Other",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)


def normalize_method(value: str | None) -> str:
    raw = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    return raw if raw in PAYMENT_METHODS else "other"


def method_label(value: str | None) -> str:
    return PAYMENT_METHOD_LABELS.get(normalize_method(value), "Other")


def parse_amount(value, *, field: str = "Amount") -> float:
    """A positive, finite money amount, or ValueError.

    NaN and infinity are rejected explicitly: float() accepts both, and every
    comparison against NaN is false, so it would slip past the positivity
    check and the refund ceiling alike.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"Enter the {field.lower()}.")
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number.")
    try:
        amount = float(value) if isinstance(value, (int, float)) else float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a number.")
    if not math.isfinite(amount):
        raise ValueError(f"{field} must be a number.")
    if amount <= 0:
        raise ValueError(f"{field} must be greater than zero.")
    return round(amount, 2)


def parse_occurred_on(value, *, today: date | None = None) -> date:
    """The date money moved. Defaults to today; never in the future."""
    reference = today or datetime.now(timezone.utc).date()
    raw = str(value or "").strip()
    if not raw:
        return reference
    try:
        parsed = date.fromisoformat(raw[:10])
    except ValueError:
        raise ValueError("Enter the date as YYYY-MM-DD.")
    if parsed > reference:
        raise ValueError("A payment cannot be dated in the future.")
    return parsed


def request_currency(request, totals: PrivateLedgerTotals | None = None) -> str:
    """The one currency this request's money lives in, or "" if not yet fixed.

    Money already recorded outranks the priced currency: it is a fact about
    what arrived, while a price is an intention that can still be corrected.
    """
    resolved = totals if totals is not None else ledger_totals(request.request_id)
    return (
        str(resolved.currency or "").strip().upper()
        or str(request.agreed_price_currency or "").strip().upper()
    )


def money_for(request, *, totals: PrivateLedgerTotals | None = None, fees=None) -> PrivateTripMoney:
    """The complete money picture for one request."""
    resolved_totals = totals if totals is not None else ledger_totals(request.request_id)
    resolved_fees = fees if fees is not None else fees_for_private_request(request.request_id)
    return private_trip_money(
        price=request.agreed_price_amount,
        price_currency=request.agreed_price_currency,
        fees=resolved_fees,
        total_paid=resolved_totals.total_paid,
        total_refunded=resolved_totals.total_refunded,
        entry_count=resolved_totals.entry_count,
        ledger_currency=resolved_totals.currency,
        fallback_currency=request.budget_currency,
    )


def money_for_many(requests) -> dict[str, PrivateTripMoney]:
    """One money picture per request, in two queries rather than 2N."""
    items = list(requests or [])
    ids = [item.request_id for item in items if item.request_id]
    if not ids:
        return {}
    totals = totals_for(ids)
    fees = fees_for_private_requests(ids)
    empty = summarize([])
    return {
        item.request_id: money_for(
            item,
            totals=totals.get(item.request_id, empty),
            fees=fees.get(item.request_id, []),
        )
        for item in items
    }


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------


def set_agreed_price(request, *, amount, currency: str | None) -> None:
    """Record what the customer agreed to pay.

    Refuses to re-denominate a request that already holds money: changing the
    currency under recorded payments would silently reinterpret every one of
    them, turning 15,000 EGP received into 15,000 USD.
    """
    resolved_currency = str(currency or "").strip().upper()
    if resolved_currency not in PRIVATE_BUDGET_CURRENCIES:
        raise ValueError("Choose EGP or USD for the agreed price.")
    totals = ledger_totals(request.request_id)
    ledger_currency = str(totals.currency or "").strip().upper()
    if ledger_currency and ledger_currency != resolved_currency:
        raise ValueError(
            f"This request already holds payments in {ledger_currency}. "
            f"Reverse them before repricing it in {resolved_currency}."
        )
    request.agreed_price_amount = parse_amount(amount, field="Agreed price")
    request.agreed_price_currency = resolved_currency


def record_payment(
    request,
    *,
    amount,
    currency: str | None = None,
    occurred_on=None,
    method: str | None = None,
    reference: str | None = None,
    notes: str | None = None,
    is_non_refundable: bool = False,
    source: str = SOURCE_CRM_UI,
    idempotency_key: str | None = None,
    actor_user_id: int | None = None,
    actor_name: str | None = None,
    today: date | None = None,
) -> PrivateTripTransaction:
    """Record money received against a private request.

    An `idempotency_key` makes the write repeatable: the same key returns the
    entry already recorded instead of adding a second one, so a resubmitted
    form or a retried request can never double someone's money.
    """
    key = str(idempotency_key or "").strip()
    if key:
        existing = PrivateTripTransaction.query.filter_by(idempotency_key=key).first()
        if existing is not None:
            return existing

    totals = ledger_totals(request.request_id)
    established = request_currency(request, totals)
    resolved_currency = str(currency or "").strip().upper() or established
    if resolved_currency not in REVENUE_CURRENCIES:
        raise ValueError("Choose EGP or USD for this payment.")
    if established and resolved_currency != established:
        raise ValueError(
            f"This request is in {established}. Record the payment in {established} -- "
            "the CRM never converts between currencies."
        )

    entry = PrivateTripTransaction(
        request_id=request.request_id,
        traveler_id=request.traveler_id,
        entry_type=ENTRY_PAYMENT,
        amount=parse_amount(amount),
        currency=resolved_currency,
        occurred_on=parse_occurred_on(occurred_on, today=today),
        date_precision=PRECISION_EXACT,
        method=normalize_method(method),
        reference=str(reference or "").strip() or None,
        notes=str(notes or "").strip() or None,
        source=source,
        is_non_refundable=bool(is_non_refundable),
        idempotency_key=key or None,
        created_by_user_id=actor_user_id,
        created_by=str(actor_name or "").strip() or None,
        created_at=_utc_now(),
    )
    db.session.add(entry)
    return entry


def refund_ceiling(request, *, totals: PrivateLedgerTotals | None = None) -> float:
    """The most that may still be refunded on this request.

    Refundable money received, less what has already been given back.
    Non-refundable deposits are excluded, which is what makes the private-trip
    deposit policy enforceable rather than merely written down.
    """
    resolved = totals if totals is not None else ledger_totals(request.request_id)
    return resolved.remaining_refundable


def record_refund(
    request,
    *,
    amount,
    reason: str,
    currency: str | None = None,
    occurred_on=None,
    method: str | None = None,
    reference: str | None = None,
    notes: str | None = None,
    actor_user_id: int | None = None,
    actor_name: str | None = None,
    today: date | None = None,
) -> PrivateTripTransaction:
    """Record money given back. Never more than was taken."""
    cleaned_reason = " ".join(str(reason or "").split())
    if not cleaned_reason:
        raise ValueError("Add a reason for this refund.")

    totals = ledger_totals(request.request_id)
    established = request_currency(request, totals)
    if not established:
        raise ValueError("Nothing has been paid on this request, so there is nothing to refund.")
    resolved_currency = str(currency or "").strip().upper() or established
    if resolved_currency != established:
        raise ValueError(f"This request is in {established}. Record the refund in {established}.")

    value = parse_amount(amount, field="Refund amount")
    ceiling = totals.remaining_refundable
    if value > ceiling + 0.005:
        if totals.total_paid <= 0:
            detail = "nothing has been recorded as paid"
        elif totals.non_refundable_paid > 0:
            detail = (
                f"{totals.total_paid:,.2f} {established} received, of which "
                f"{totals.non_refundable_paid:,.2f} is non-refundable"
            )
        else:
            detail = f"{totals.total_paid:,.2f} {established} received"
        if totals.total_refunded > 0:
            detail += f", {totals.total_refunded:,.2f} already refunded"
        raise ValueError(
            f"A refund cannot exceed {ceiling:,.2f} {established} on this request "
            f"({detail})."
        )

    entry = PrivateTripTransaction(
        request_id=request.request_id,
        traveler_id=request.traveler_id,
        entry_type=ENTRY_REFUND,
        amount=value,
        currency=resolved_currency,
        occurred_on=parse_occurred_on(occurred_on, today=today),
        date_precision=PRECISION_EXACT,
        method=normalize_method(method),
        reference=str(reference or "").strip() or None,
        reason=cleaned_reason,
        notes=str(notes or "").strip() or None,
        source=SOURCE_CRM_UI,
        created_by_user_id=actor_user_id,
        created_by=str(actor_name or "").strip() or None,
        created_at=_utc_now(),
    )
    db.session.add(entry)
    return entry


def reverse_entry(
    entry: PrivateTripTransaction,
    *,
    reason: str,
    actor_user_id: int | None = None,
    actor_name: str | None = None,
) -> PrivateTripTransaction:
    """Cancel a recorded entry by inserting its mirror image.

    The ledger is append-only, so a mistyped amount is corrected this way
    rather than edited: both rows survive, the totals stop counting either, and
    the record of what was believed at the time is intact.
    """
    if entry is None:
        raise ValueError("Transaction not found.")
    cleaned_reason = " ".join(str(reason or "").split())
    if not cleaned_reason:
        raise ValueError("Add a reason for reversing this entry.")
    if entry.reverses_id is not None:
        raise ValueError(f"{entry.public_ref} is itself a correction and cannot be reversed.")
    existing = PrivateTripTransaction.query.filter_by(reverses_id=entry.transaction_id).first()
    if existing is not None:
        raise ValueError(f"{entry.public_ref} was already reversed by {existing.public_ref}.")

    mirror = PrivateTripTransaction(
        request_id=entry.request_id,
        traveler_id=entry.traveler_id,
        entry_type=entry.entry_type,
        amount=entry.amount,
        currency=entry.currency,
        occurred_on=entry.occurred_on,
        date_precision=entry.date_precision,
        method=entry.method,
        reference=entry.reference,
        reason=cleaned_reason,
        notes=f"Reverses {entry.public_ref}.",
        source=SOURCE_CORRECTION,
        is_non_refundable=bool(entry.is_non_refundable),
        reverses_id=entry.transaction_id,
        created_by_user_id=actor_user_id,
        created_by=str(actor_name or "").strip() or None,
        created_at=_utc_now(),
    )
    db.session.add(mirror)
    return mirror
