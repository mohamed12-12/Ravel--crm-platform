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
    "lost",
)

# "converted" used to be the terminal success stage: the request was turned
# into a synthetic private Trip + TripBooking so its money could travel
# through the ordinary booking revenue engine. A private request now carries
# its own price, fees and payment ledger, so converting it would make the
# same money countable twice -- once on the request and once on the booking it
# spawned. The stage and the conversion action are therefore retired.
#
# The value is still recognized on read: rows created before the change exist
# in production and must keep loading, rendering and reporting exactly as they
# are. Nothing can move INTO it (it has no entry in the transition table), and
# no new row can reach it, because the only writer is gone.
LEGACY_PRIVATE_REQUEST_STAGES = ("converted",)
ALL_PRIVATE_REQUEST_STAGES = PRIVATE_REQUEST_STAGES + LEGACY_PRIVATE_REQUEST_STAGES

PRIVATE_REQUEST_TRANSITIONS = {
    "registered": {"consultation_scheduled", "lost"},
    "consultation_scheduled": {"consultation_done", "lost"},
    "consultation_done": {"deposit_pending", "lost"},
    "deposit_pending": {"deposit_paid", "lost"},
    "deposit_paid": {"designing", "lost"},
    "designing": {"design_delivered", "lost"},
    "design_delivered": {"payment_pending", "lost"},
    "payment_pending": {"paid", "lost"},
    # Terminal. "paid" means the customer has settled the trip in full; there
    # is nothing left to move to and nothing left to lose.
    "paid": set(),
    "lost": set(),
    "converted": set(),
}

# Stages after which the request is finished either way -- no SLA clock runs.
PRIVATE_REQUEST_CLOSED_STAGES = frozenset({"paid", "lost", "converted"})

# Stages at which the customer has committed to the trip: a deposit has been
# taken and Ravel is building or delivering it. The single definition of "this
# traveler has a private trip", used for the count on their profile, so the
# profile card and any report can never disagree about what they are counting.
# A request the customer walked away from is excluded no matter how far it got.
PRIVATE_REQUEST_COMMITTED_STAGES = frozenset({
    "deposit_paid",
    "designing",
    "design_delivered",
    "payment_pending",
    "paid",
    "converted",
})


def is_committed_private_trip(stage: str | None, *, total_paid: float = 0.0) -> bool:
    """Whether a request counts as a private trip this traveler is taking.

    Money received counts even at an earlier stage: if the customer has paid,
    the trip is real regardless of which milestone an employee has ticked.
    A lost request never counts -- a kept non-refundable deposit is revenue,
    but it is not a trip anybody went on.
    """
    normalized = normalize_private_stage(stage)
    if normalized == "lost":
        return False
    if normalized in PRIVATE_REQUEST_COMMITTED_STAGES:
        return True
    try:
        return float(total_paid or 0.0) > 0
    except (TypeError, ValueError):
        return False


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
    """Validate a stage name, legacy values included.

    Deliberately accepts LEGACY_PRIVATE_REQUEST_STAGES: this function is what
    the model's __init__ runs on load, so rejecting "converted" would silently
    rewrite every historical row's stage to "registered" -- reopening closed
    requests and putting them back on the SLA clock. Movement is governed by
    PRIVATE_REQUEST_TRANSITIONS, which is where the retirement is enforced.
    """
    raw = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    return raw if raw in ALL_PRIVATE_REQUEST_STAGES else ""


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
