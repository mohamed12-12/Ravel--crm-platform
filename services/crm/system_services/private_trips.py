"""Shared vocabulary and small helpers for private/custom trip requests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


PRIVATE_SERVICE_TYPES = {
    "consultation": "Consultation",
    "bookings_only": "Bookings only",
    "full_package": "Full package",
    "design_only": "Design only",
    "chaperone": "Chaperone",
}

PRIVATE_TRIP_SCOPES = ("Local", "International")
PRIVATE_BUDGET_CURRENCIES = ("EGP", "USD")

PRIVATE_REQUEST_STAGES = (
    "registered",
    "consultation_scheduled",
    "consultation_done",
    "deposit_pending",
    "deposit_paid",
    "designing",
    "design_delivered",
    "payment_pending",
    "paid",
    "converted",
    "lost",
)

PRIVATE_REQUEST_TRANSITIONS = {
    "registered": {"consultation_scheduled", "lost"},
    "consultation_scheduled": {"consultation_done", "lost"},
    "consultation_done": {"deposit_pending", "lost"},
    "deposit_pending": {"deposit_paid", "lost"},
    "deposit_paid": {"designing", "lost"},
    "designing": {"design_delivered", "lost"},
    "design_delivered": {"payment_pending", "lost"},
    "payment_pending": {"paid", "lost"},
    "paid": {"converted", "lost"},
    "converted": set(),
    "lost": set(),
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_private_service_type(value: str | None) -> str:
    raw = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "1": "consultation",
        "consult": "consultation",
        "consultation": "consultation",
        # Field-capture audit: this had no Arabic entries at all, despite
        # the question always being asked bilingually -- a customer who
        # answered with the Arabic service name instead of a digit got
        # "معلش، يمكن سؤالي ما كان واضح" every time.
        "استشارة": "consultation",
        "استشاره": "consultation",
        "2": "bookings_only",
        "booking": "bookings_only",
        "bookings": "bookings_only",
        "bookings_only": "bookings_only",
        "حجوزات": "bookings_only",
        "حجوزات_فقط": "bookings_only",
        "حجز": "bookings_only",
        "حجز_فقط": "bookings_only",
        "3": "full_package",
        "full": "full_package",
        "package": "full_package",
        "full_package": "full_package",
        "برنامج_كامل": "full_package",
        "باقة_كاملة": "full_package",
        "باكدج": "full_package",
        "بكدج": "full_package",
        "4": "design_only",
        "design": "design_only",
        "design_only": "design_only",
        "تصميم": "design_only",
        "تصميم_برنامج": "design_only",
        "تصميم_فقط": "design_only",
        "5": "chaperone",
        "chaperone": "chaperone",
        "مرافق": "chaperone",
        "مرافقة": "chaperone",
        "مرافق_رحلة": "chaperone",
    }
    return aliases.get(raw, "")


def normalize_private_scope(value: str | None) -> str:
    raw = str(value or "").strip().casefold()
    if raw in {"local", "domestic", "inside egypt"}:
        return "Local"
    if raw in {"international", "intl", "abroad", "outside egypt"}:
        return "International"
    return ""


def normalize_private_stage(value: str | None) -> str:
    raw = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    return raw if raw in PRIVATE_REQUEST_STAGES else ""


def can_transition_private_stage(current: str | None, target: str | None) -> bool:
    current_stage = normalize_private_stage(current) or "registered"
    target_stage = normalize_private_stage(target)
    return bool(target_stage and target_stage in PRIVATE_REQUEST_TRANSITIONS.get(current_stage, set()))


def consultation_due_from(created_at: datetime | None = None) -> datetime:
    base = created_at or utc_now()
    return base + timedelta(hours=48)


def add_working_days(start: datetime | None, days: int) -> datetime | None:
    if start is None:
        return None
    current = start
    remaining = max(int(days or 0), 0)
    while remaining:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def design_due_from(deposit_paid_at: datetime | None) -> datetime | None:
    return add_working_days(deposit_paid_at, 10)
