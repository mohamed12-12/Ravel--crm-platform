# app/services/booking_audit.py
"""Structured audit trail for booking activity -- the Activity Timeline.

Every booking event the CRM shows comes from app.models.booking_event's
BookingEventTrail. Before this module existed exactly one writer populated
it (booking_automation.auto_create_booking_from_lead), so a booking's
timeline stopped at "auto-created" and never recorded a single status
change, payment, refund, or field edit afterwards.

The fix is deliberately NOT "call a helper from every route that touches a
booking" -- that is the arrangement that decayed in the first place, and any
new write path silently opts out of it. Instead the diff recorder is a
SQLAlchemy ``before_flush`` listener on the session (see
register_booking_audit_listeners). Every path that persists a TripBooking
through the ORM -- the CRM routes, app/services/booking_automation.py, the
agent bridge in app/services/agent_crm_bridge.py, a future route nobody has
written yet -- goes through a flush, so all of them are covered without
knowing this module exists.

Two things the listener genuinely cannot see, and which therefore call
record_booking_event() explicitly:

* bookings.create() inserts its row through UnifiedCRMService's raw sqlite3
  connection, not the ORM, so no flush ever carries the new booking.
* An employee's typed note. booking_notes is append-only free text that the
  route also appends system audit lines to; only the route knows which part
  the human wrote.

"Do not duplicate events when an update does not actually change the value"
is satisfied structurally rather than by convention: SQLAlchemy's attribute
history reports an attribute as *unchanged* when it is re-assigned a value
equal to the one already loaded, which is exactly what the booking update
route does to fields absent from the submitted form.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, NamedTuple

from sqlalchemy import event, inspect as sa_inspect, select

from app.extensions import db
from app.models.booking import TripBooking
from app.models.booking_event import BookingEventTrail

logger = logging.getLogger(__name__)

_EVENT_ID_PREFIX = "EVT"
_EVENT_ID_WIDTH = 8
# booking_event_trail.actor / .event_label / .channel column widths.
_ACTOR_MAX_LENGTH = 50
_LABEL_MAX_LENGTH = 150
_CHANNEL_MAX_LENGTH = 50

# Event types that mean "this booking came into existence". Used to stop the
# listener adding a second creation event for a booking whose creator already
# recorded its own richer one (auto_create_booking_from_lead does, and its
# metadata -- trigger_source, missing_fields -- is worth keeping).
CREATION_EVENT_TYPES = frozenset({"booking_created", "booking_auto_created"})

# Set by booking_audit_actor() so non-request write paths (the agent bridge,
# scheduled jobs) can attribute their events. Falls back to the logged-in
# employee, then to "system".
_actor_override: ContextVar[str | None] = ContextVar("booking_audit_actor", default=None)
_suspended: ContextVar[bool] = ContextVar("booking_audit_suspended", default=False)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Value formatting
# --------------------------------------------------------------------------
# Formatters turn a stored column value into the string an employee reads in
# the timeline. They receive the session so they can resolve foreign keys
# (a trip id is meaningless to a human; the trip's name is not).


def _format_plain(value: Any, booking: TripBooking, session: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _format_money(value: Any, booking: TripBooking, session: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return str(value).strip()
    currency = str(booking.currency or "").strip().upper()
    return f"{amount:,.2f} {currency}".strip()


def _format_datetime(value: Any, booking: TripBooking, session: Any) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat(sep=" ")
    return str(value).strip()


def _format_completeness(value: Any, booking: TripBooking, session: Any) -> str:
    return "Incomplete" if value else "Complete"


def _format_trip(value: Any, booking: TripBooking, session: Any) -> str:
    trip_id = str(value or "").strip()
    if not trip_id:
        return ""
    try:
        from app.models.trip import Trip

        trip = session.get(Trip, trip_id)
    except Exception:
        trip = None
    if trip is not None and (trip.trip_name or "").strip():
        return f"{trip.trip_name} ({trip_id})"
    return trip_id


def _format_employee(value: Any, booking: TripBooking, session: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        from app.models.user import User

        user = session.get(User, int(value))
    except Exception:
        user = None
    if user is not None:
        return getattr(user, "display_name", None) or user.username or str(value)
    return str(value)


def _completeness_label(previous: Any, current: Any) -> str:
    return "Booking information incomplete" if current else "Booking information completed"


# --------------------------------------------------------------------------
# Tracked-field registry
# --------------------------------------------------------------------------


class _TrackedField(NamedTuple):
    """One booking column worth an audit event when it changes.

    ``value_label`` is used when there was no previous value ("Amount: 120.00
    EGP" reads better than "Refund: 120.00 EGP"). ``event_label`` may be a
    callable when the heading depends on the direction of the change.
    """

    attribute: str
    label: str
    event_type: str
    event_label: str | Callable[[Any, Any], str]
    formatter: Callable[[Any, TripBooking, Any], str] = _format_plain
    value_label: str | None = None


TRACKED_FIELDS: tuple[_TrackedField, ...] = (
    _TrackedField("booking_status", "Booking Status", "booking_status_changed", "Booking status changed"),
    _TrackedField("payment_status", "Payment Status", "payment_status_changed", "Payment status changed"),
    _TrackedField(
        "refund_amount", "Refund", "refund_updated", "Refund recorded",
        formatter=_format_money, value_label="Amount",
    ),
    _TrackedField("trip_id", "Trip", "booking_details_updated", "Trip updated", formatter=_format_trip),
    _TrackedField("room_type", "Room Type", "booking_details_updated", "Room type updated"),
    _TrackedField("currency", "Currency", "booking_details_updated", "Currency updated"),
    _TrackedField("group_size", "Group Size", "booking_details_updated", "Group size updated"),
    _TrackedField("flight_option", "Flight Option", "booking_details_updated", "Flight option updated"),
    _TrackedField("priority", "Priority", "booking_details_updated", "Priority updated"),
    _TrackedField("passport_status", "Passport Status", "booking_details_updated", "Passport status updated"),
    _TrackedField(
        "assigned_to_user_id", "Assigned Employee", "booking_assignment_changed", "Assignment changed",
        formatter=_format_employee,
    ),
    _TrackedField(
        "next_follow_up_at", "Next Follow-up", "booking_follow_up_updated", "Follow-up scheduled",
        formatter=_format_datetime,
    ),
    _TrackedField("next_action", "Next Action", "booking_follow_up_updated", "Next action updated"),
    _TrackedField(
        "customer_response_status", "Customer Response", "booking_follow_up_updated",
        "Customer response updated",
    ),
    _TrackedField(
        "last_contact_at", "Last Contact", "booking_contacted", "Customer contacted",
        formatter=_format_datetime,
    ),
    _TrackedField(
        "missing_info", "Booking Information", "booking_info_changed", _completeness_label,
        formatter=_format_completeness,
    ),
)

# Snapshot written into the creation event's metadata so a booking's opening
# state is legible without cross-referencing the row itself.
_CREATION_SNAPSHOT_FIELDS = (
    "trip_id", "trip_name", "room_type", "currency", "group_size",
    "booking_status", "payment_status", "booking_source", "lead_id",
)


# --------------------------------------------------------------------------
# Actor resolution and suspension
# --------------------------------------------------------------------------


def _resolve_actor(explicit: str | None = None) -> str:
    for candidate in (explicit, _actor_override.get()):
        cleaned = str(candidate or "").strip()
        if cleaned:
            return cleaned[:_ACTOR_MAX_LENGTH]
    try:
        from flask import has_request_context

        if has_request_context():
            from app.security import current_actor

            resolved = str(current_actor() or "").strip()
            if resolved:
                return resolved[:_ACTOR_MAX_LENGTH]
    except Exception:  # pragma: no cover - actor lookup must never break a write
        logger.debug("Booking audit could not resolve an actor", exc_info=True)
    return "system"


@contextmanager
def booking_audit_actor(actor: str) -> Iterator[None]:
    """Attribute audit events written inside this block to ``actor``.

    For write paths with no Flask request to read a logged-in employee from
    (the AI agent bridge, background jobs, management commands).
    """
    token = _actor_override.set(str(actor or "").strip() or None)
    try:
        yield
    finally:
        _actor_override.reset(token)


@contextmanager
def suspend_booking_audit() -> Iterator[None]:
    """Stop the listener recording events inside this block.

    For bulk loads (spreadsheet imports, fixture seeding) where one event per
    row is noise rather than history. Explicit record_booking_event() calls
    are suspended too, so a caller cannot half-opt-out.
    """
    token = _suspended.set(True)
    try:
        yield
    finally:
        _suspended.reset(token)


# --------------------------------------------------------------------------
# Event id allocation
# --------------------------------------------------------------------------


def _pending_event_ids(session: Any) -> set[str]:
    return {
        str(obj.event_id)
        for obj in session.new
        if isinstance(obj, BookingEventTrail) and obj.event_id
    }


def _highest_event_number(session: Any, pending: set[str]) -> int:
    """Largest EVT######## sequence already taken.

    Only the fixed-width numeric spelling is considered. UnifiedCRMService's
    sqlite path mints ``EVT-YYYYMMDD-NNNN`` ids into the same table; those are
    a different length so the LIKE pattern below cannot match them, and the
    two sequences coexist without colliding.
    """
    pattern = _EVENT_ID_PREFIX + "_" * _EVENT_ID_WIDTH
    highest = 0
    try:
        rows = session.execute(
            select(BookingEventTrail.event_id).where(BookingEventTrail.event_id.like(pattern))
        ).scalars().all()
    except Exception:  # pragma: no cover - fall back to pending-only numbering
        logger.debug("Booking audit could not read existing event ids", exc_info=True)
        rows = []
    for value in list(rows) + list(pending):
        tail = str(value or "")[len(_EVENT_ID_PREFIX):]
        if len(tail) == _EVENT_ID_WIDTH and tail.isdigit():
            highest = max(highest, int(tail))
    return highest


class _EventIdAllocator:
    """Hands out sequential ids from a single query per flush.

    Querying once per event inside before_flush would re-enter the session for
    every field an employee changed in one save.
    """

    def __init__(self, session: Any) -> None:
        self._session = session
        self._next: int | None = None

    def allocate(self) -> str:
        if self._next is None:
            self._next = _highest_event_number(self._session, _pending_event_ids(self._session)) + 1
        event_id = f"{_EVENT_ID_PREFIX}{self._next:0{_EVENT_ID_WIDTH}d}"
        self._next += 1
        return event_id


# --------------------------------------------------------------------------
# Writing events
# --------------------------------------------------------------------------


def _build_event(
    booking: TripBooking,
    *,
    event_id: str,
    event_type: str,
    event_label: str,
    notes: str = "",
    metadata: dict[str, Any] | None = None,
    actor: str | None = None,
    occurred_at: datetime | None = None,
    channel: str | None = None,
) -> BookingEventTrail:
    payload = dict(metadata or {})
    payload.setdefault("booking_id", booking.booking_id)
    resolved_channel = str(channel or booking.booking_source or "crm-ui")[:_CHANNEL_MAX_LENGTH]
    return BookingEventTrail(
        event_id=event_id,
        occurred_at=occurred_at or _utc_now(),
        event_type=event_type,
        event_label=str(event_label or event_type)[:_LABEL_MAX_LENGTH],
        traveler_id=booking.traveler_id or None,
        lead_id=booking.lead_id or None,
        booking_id=booking.booking_id,
        trip_id=booking.trip_id or None,
        interaction_id=booking.interaction_id or None,
        channel=resolved_channel or None,
        actor=_resolve_actor(actor),
        notes=(notes or "").strip() or None,
        metadata_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
    )


def record_booking_event(
    booking: TripBooking,
    *,
    event_type: str,
    event_label: str,
    notes: str = "",
    metadata: dict[str, Any] | None = None,
    actor: str | None = None,
    channel: str | None = None,
) -> BookingEventTrail | None:
    """Add one timeline event for ``booking`` to the current session.

    Does not commit -- the event lands in the same transaction as whatever
    change prompted it, so a rolled-back save never leaves a phantom entry in
    the history. Returns the pending event, or None when auditing is
    suspended or the booking has no id to attach to.

    Safe to call before ``db.session.add(booking)`` for a brand-new booking:
    the event is added first, and SQLAlchemy still inserts trip_bookings
    before booking_event_trail because of the foreign key between them. A
    caller creating a booking should do exactly that, so the creation event
    is already pending when the listener inspects the flush and the listener
    does not add a second one of its own.
    """
    if _suspended.get() or booking is None or not booking.booking_id:
        return None
    session = db.session
    # Allocating an id reads the table. Callers reach this mid-mutation, and
    # letting that read autoflush would push half-finished booking changes to
    # the database earlier than the caller's own commit intends.
    with session.no_autoflush:
        event_id = _EventIdAllocator(session).allocate()
    entry = _build_event(
        booking,
        event_id=event_id,
        event_type=event_type,
        event_label=event_label,
        notes=notes,
        metadata=metadata,
        actor=actor,
        channel=channel,
    )
    session.add(entry)
    return entry


def record_booking_created(
    booking: TripBooking,
    *,
    source: str = "",
    notes: str = "",
    actor: str | None = None,
) -> BookingEventTrail | None:
    """Record the creation of a booking the ORM never saw inserted.

    bookings.create() writes its row through UnifiedCRMService's own sqlite3
    connection, so no flush carries the new TripBooking and the listener has
    nothing to react to.
    """
    return record_booking_event(
        booking,
        event_type="booking_created",
        event_label="Booking created",
        notes=notes or "Booking created in the CRM.",
        metadata={
            "source": source or booking.booking_source or "",
            "snapshot": _creation_snapshot(booking),
        },
        actor=actor,
    )


def record_booking_note(
    booking: TripBooking,
    note: str,
    *,
    actor: str | None = None,
) -> BookingEventTrail | None:
    """Record a note an employee typed.

    Kept out of the tracked-field registry on purpose: booking_notes is an
    append-only blob that the update route also writes system audit lines
    into, and only the route can tell the two apart.
    """
    cleaned = (note or "").strip()
    if not cleaned:
        return None
    return record_booking_event(
        booking,
        event_type="booking_note_added",
        event_label="Note added",
        notes=cleaned,
        metadata={"note": cleaned},
        actor=actor,
    )


def _creation_snapshot(booking: TripBooking) -> dict[str, Any]:
    return {name: getattr(booking, name, None) for name in _CREATION_SNAPSHOT_FIELDS}


# --------------------------------------------------------------------------
# Change detection
# --------------------------------------------------------------------------


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _values_equal(left: Any, right: Any) -> bool:
    """Whether a change is worth a timeline entry.

    "Empty" has several spellings in this schema -- NULL from the auto-create
    path, '' from the update route's ``(data.get(...) or '').strip() or None``
    idiom -- and shuffling between them is a storage detail, not booking
    activity. Treating them as equal is what keeps a plain status change from
    also reporting "Passport Status cleared".
    """
    if _is_blank(left) and _is_blank(right):
        return True
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) == bool(right)
    if left is None or right is None:
        return False
    if isinstance(left, str) and isinstance(right, str):
        return left.strip() == right.strip()
    try:
        return left == right
    except Exception:  # pragma: no cover - exotic column types
        return str(left) == str(right)


def _change_notes(field: _TrackedField, previous_display: str, current_display: str) -> str:
    if not previous_display and current_display:
        return f"{field.value_label or field.label}: {current_display}"
    if previous_display and not current_display:
        return f"{field.label} cleared (previously: {previous_display})"
    return f"Previously: {previous_display}\nNow: {current_display}"


def booking_field_changes(booking: TripBooking) -> list[tuple[_TrackedField, Any, Any]]:
    """Tracked fields SQLAlchemy reports as genuinely changed on ``booking``.

    Re-assigning a column the value it already holds leaves ``has_changes()``
    False, which is what keeps a form save that re-posts unchanged fields from
    filling the timeline with noise.
    """
    state = sa_inspect(booking)
    changes: list[tuple[_TrackedField, Any, Any]] = []
    for field in TRACKED_FIELDS:
        try:
            history = state.attrs[field.attribute].history
        except Exception:  # pragma: no cover - attribute missing on a partial model
            continue
        if not history.has_changes():
            continue
        previous = history.deleted[0] if history.deleted else None
        current = history.added[0] if history.added else None
        if _values_equal(previous, current):
            continue
        changes.append((field, previous, current))
    return changes


def _change_event(
    booking: TripBooking,
    session: Any,
    allocator: _EventIdAllocator,
    field: _TrackedField,
    previous: Any,
    current: Any,
) -> BookingEventTrail:
    previous_display = field.formatter(previous, booking, session)
    current_display = field.formatter(current, booking, session)
    label = field.event_label(previous, current) if callable(field.event_label) else field.event_label
    return _build_event(
        booking,
        event_id=allocator.allocate(),
        event_type=field.event_type,
        event_label=label,
        notes=_change_notes(field, previous_display, current_display),
        metadata={
            "field": field.attribute,
            "field_label": field.label,
            "previous": previous_display,
            "current": current_display,
        },
    )


# --------------------------------------------------------------------------
# The session listener
# --------------------------------------------------------------------------


def _booking_already_has_creation_event(session: Any, booking_id: str) -> bool:
    return any(
        isinstance(obj, BookingEventTrail)
        and obj.booking_id == booking_id
        and obj.event_type in CREATION_EVENT_TYPES
        for obj in session.new
    )


def _collect_flush_events(session: Any) -> list[BookingEventTrail]:
    allocator = _EventIdAllocator(session)
    events: list[BookingEventTrail] = []

    for obj in list(session.new):
        if not isinstance(obj, TripBooking) or not obj.booking_id:
            continue
        if _booking_already_has_creation_event(session, obj.booking_id):
            # auto_create_booking_from_lead writes its own richer creation
            # event in this same flush; a second one would just be a duplicate.
            continue
        events.append(
            _build_event(
                obj,
                event_id=allocator.allocate(),
                event_type="booking_created",
                event_label="Booking created",
                notes="Booking created.",
                metadata={"source": obj.booking_source or "", "snapshot": _creation_snapshot(obj)},
            )
        )

    for obj in list(session.dirty):
        if not isinstance(obj, TripBooking) or not obj.booking_id:
            continue
        for field, previous, current in booking_field_changes(obj):
            events.append(_change_event(obj, session, allocator, field, previous, current))

    return events


def _before_flush_audit(session: Any, flush_context: Any, instances: Any) -> None:
    if _suspended.get():
        return
    try:
        # Reading trips/users to render a change, and querying the highest
        # event id, must not trigger a nested autoflush of the very objects
        # this handler is inspecting.
        with session.no_autoflush:
            events = _collect_flush_events(session)
    except Exception:  # pragma: no cover - auditing must never break a booking write
        logger.error("Booking audit trail could not be recorded", exc_info=True)
        return
    for entry in events:
        session.add(entry)


_listeners_registered = False


def register_booking_audit_listeners() -> None:
    """Attach the audit listener to the Flask-SQLAlchemy session.

    Called from create_app(). Idempotent: create_app() runs repeatedly across
    the test suite, and registering twice would double every event.
    """
    global _listeners_registered
    if _listeners_registered:
        return
    event.listen(db.session, "before_flush", _before_flush_audit)
    _listeners_registered = True
