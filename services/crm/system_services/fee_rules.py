# services/crm/system_services/fee_rules.py
"""Shared vocabulary and validation for employee-added additional fees.

A fee is money the customer owes on top of the trip's list price -- a visa, an
insurance policy, an airport transfer, a room upgrade. Both trip bookings and
private trip requests carry them, and both feed the same revenue surfaces, so
the rules live here (backend-agnostic, no Flask import) rather than in either
route.

Two rules are worth stating because they are what keeps the money honest:

* **A fee is denominated in its parent's currency, or it is rejected.** The
  CRM never converts between EGP and USD -- there is no exchange rate anywhere
  in this system, by design -- so a fee in a currency the booking does not use
  could only ever be added to its total by inventing one.

* **Fees are voided, never deleted.** A fee that has been quoted to a customer
  and then removed is a fact about the booking's history, and revenue for a
  past month must not change shape because somebody tidied up a row.
"""
from __future__ import annotations

import math

# Presentation labels for the categories an employee can pick. `other` exists
# so an unusual fee is still categorized rather than left blank; the free-text
# label is what actually names it on the booking.
ADDITIONAL_FEE_CATEGORIES = {
    "visa": "Visa",
    "insurance": "Insurance",
    "transfer": "Transfer",
    "upgrade": "Upgrade",
    "excursion": "Excursion",
    "flight": "Flight",
    "service": "Service fee",
    "other": "Other",
}

DEFAULT_FEE_CATEGORY = "other"
MAX_FEE_LABEL_LENGTH = 120


def normalize_fee_category(value: str | None) -> str:
    raw = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    return raw if raw in ADDITIONAL_FEE_CATEGORIES else DEFAULT_FEE_CATEGORY


def fee_category_label(value: str | None) -> str:
    return ADDITIONAL_FEE_CATEGORIES.get(normalize_fee_category(value), "Other")


def parse_fee_amount(value) -> float:
    """A fee amount, or ValueError explaining why it is not one.

    Rejects NaN and infinity explicitly. float() accepts both, and NaN is the
    dangerous one: every comparison against it is false, so it slips past a
    positivity check and lands in the database as a number no report can total.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError("Enter the fee amount.")
    if isinstance(value, bool):
        raise ValueError("Fee amount must be a number.")
    try:
        amount = float(str(value).replace(",", "").strip()) if not isinstance(value, (int, float)) else float(value)
    except (TypeError, ValueError):
        raise ValueError("Fee amount must be a number.")
    if not math.isfinite(amount):
        raise ValueError("Fee amount must be a number.")
    if amount <= 0:
        raise ValueError("Fee amount must be greater than zero.")
    return round(amount, 2)


def validate_fee_currency(fee_currency: str | None, parent_currency: str | None, *, parent_label: str) -> str:
    """The currency a fee may be stored in, or ValueError.

    Requiring the parent's currency (rather than defaulting to it silently) is
    deliberate: an employee who picked the wrong currency has typed an amount
    for a different one, and quietly relabelling it would change what the
    customer owes.
    """
    parent = str(parent_currency or "").strip().upper()
    fee = str(fee_currency or "").strip().upper() or parent
    if not parent:
        raise ValueError(
            f"Set a currency on this {parent_label} before adding fees, so the fee can be added to its total."
        )
    if fee != parent:
        raise ValueError(
            f"This {parent_label} is in {parent}. Add the fee in {parent} -- "
            "the CRM never converts between currencies."
        )
    return parent


def validate_fee_label(value: str | None) -> str:
    label = " ".join(str(value or "").split())
    if not label:
        raise ValueError("Describe what the fee is for.")
    return label[:MAX_FEE_LABEL_LENGTH]
