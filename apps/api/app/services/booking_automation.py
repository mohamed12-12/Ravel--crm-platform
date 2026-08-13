# app/services/booking_automation.py
"""Auto-creates a Draft Booking the first time a Lead reaches Booking Draft.

Called from both the manual CRM UI (app/routes/leads.py) and the AI agent's
Postgres write path (app/services/agent_crm_bridge.py's
PostgresAgentBridgeService.update_lead_stage) so a Booking Draft never
depends on someone remembering to click "Create Booking" as a separate
step, regardless of whether the stage change came from an employee or the
agent. services/crm/system_services/unified_service.py's
auto_create_booking_from_lead_if_ready is the SQLite-backend sibling used
when the AI agent runs against that backend instead -- see
services/crm/README.md for how the two relate.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.extensions import db
from app.models.booking import TripBooking
from app.models.booking_event import BookingEventTrail
from app.models.booking_status_history import BookingStatusHistory
from app.models.lead import Lead
from app.models.traveler import Traveler
from app.models.trip import Trip
from app.models.user import User
from app.services.assignments import apply_assignment, auto_assign_booking

# Every lead_stage spelling (current + legacy aliases, see leads.py's
# PIPELINE_GROUPS['Booking Draft']) that means "this lead reached Booking
# Draft".
BOOKING_DRAFT_STAGE_ALIASES = {
    "Booking Draft",
    "Booked",
    "Booking Draft Created",
    "VIP Booking Draft",
    "Repeat Booking Draft",
}

_ACTIVE_BOOKING_STATUSES = ("Draft", "Confirmed", "Pending")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _next_prefixed_id(table_name: str, column_name: str, prefix: str, width: int) -> str:
    query = text(f"SELECT {column_name} FROM {table_name} WHERE {column_name} LIKE :prefix")
    rows = db.session.execute(query, {"prefix": f"{prefix}%"}).scalars().all()
    pattern = re.compile(rf"{re.escape(prefix)}(\d+)")
    max_number = 0
    for value in rows:
        match = pattern.fullmatch(str(value or "").strip())
        if match:
            max_number = max(max_number, int(match.group(1)))
    return f"{prefix}{max_number + 1:0{width}d}"


def auto_create_booking_from_lead(
    lead: Lead,
    *,
    trigger_source: str,
    actor_label: str = "",
    actor_user: User | None = None,
) -> dict[str, Any] | None:
    """Create (or reuse) the Draft Booking for a Lead that just reached Booking Draft.

    Returns None if the lead's current stage isn't a Booking Draft alias, or
    if there is no traveler to attribute the booking to. Idempotent: a lead
    bouncing Qualified -> Booking Draft -> Waiting Customer Reply -> Booking
    Draft again reuses the booking made the first time instead of creating a
    second one.
    """
    if (lead.lead_stage or "").strip() not in BOOKING_DRAFT_STAGE_ALIASES:
        return None

    existing = (
        TripBooking.query.filter_by(lead_id=lead.lead_id)
        .order_by(TripBooking.draft_created_at.desc(), TripBooking.booking_id.desc())
        .first()
    )
    if existing is None and lead.traveler_id:
        existing = (
            TripBooking.query.filter(
                TripBooking.traveler_id == lead.traveler_id,
                TripBooking.booking_status.in_(_ACTIVE_BOOKING_STATUSES),
            )
            .order_by(TripBooking.draft_created_at.desc(), TripBooking.booking_id.desc())
            .first()
        )
    if existing is not None:
        if not lead.booking_id:
            lead.booking_id = existing.booking_id
            db.session.commit()
        return {"booking_id": existing.booking_id, "created": False}

    if not lead.traveler_id:
        return None
    traveler = db.session.get(Traveler, lead.traveler_id)
    if traveler is None:
        return None

    trip_id = (lead.interested_trip_ids or lead.suggested_trip_ids or "").split(",")[0].strip()
    trip = db.session.get(Trip, trip_id) if trip_id else None

    # Lead carries no room-type field to copy from -- every auto-created
    # booking starts without one, so the assigned employee must fill it in.
    missing_fields = ["room_type"]
    if not trip:
        missing_fields.append("trip")

    booking_id = _next_prefixed_id("trip_bookings", "booking_id", "BK", 6)
    booking_notes = (
        f"Auto-created from Lead {lead.lead_id} upon reaching Booking Draft "
        f"(channel: {lead.channel or 'n/a'}, language: {lead.language or 'n/a'}, "
        f"trip type: {lead.preferred_trip_type or 'n/a'})."
    )
    booking = TripBooking(
        booking_id=booking_id,
        trip_id=trip.trip_id if trip else None,
        trip_name=trip.trip_name if trip else None,
        traveler_id=traveler.traveler_id,
        traveler_name=traveler.full_name,
        room_type=None,
        group_size=lead.group_size or 1,
        booking_status="Draft",
        booking_source=f"Auto ({trigger_source})",
        lead_id=lead.lead_id,
        payment_status="Pending",
        passport_required=bool(trip and str(trip.type or "").strip().lower() == "international"),
        booking_notes=booking_notes,
        missing_info=True,
    )
    db.session.add(booking)
    traveler.last_booking_id = booking_id
    lead.booking_id = booking_id
    db.session.add(
        BookingStatusHistory(
            booking_id=booking_id,
            old_status=None,
            new_status="Draft",
            changed_by=actor_label or trigger_source,
            change_source=f"auto_booking:{trigger_source}",
            notes="Booking auto-created upon reaching Booking Draft stage.",
        )
    )
    if lead.assigned_to_user_id:
        apply_assignment(
            booking,
            resource_type="booking",
            resource_id=booking_id,
            new_user_id=lead.assigned_to_user_id,
            actor=actor_user,
            reason="Copied from Lead's assigned employee on auto-created booking",
        )
    else:
        auto_assign_booking(
            booking,
            actor=actor_user,
            reason="Automatic round-robin sales assignment on auto-created booking",
        )
    try:
        event_id = _next_prefixed_id("booking_event_trail", "event_id", "EVT", 8)
        db.session.add(
            BookingEventTrail(
                event_id=event_id,
                occurred_at=_utc_now(),
                event_type="booking_auto_created",
                event_label="Booking auto-created",
                traveler_id=traveler.traveler_id or None,
                lead_id=lead.lead_id,
                booking_id=booking_id,
                trip_id=trip.trip_id if trip else None,
                channel=lead.channel or None,
                actor=actor_label or trigger_source,
                notes="Booking auto-created upon reaching Booking Draft stage",
                metadata_json=json.dumps(
                    {
                        "booking_id": booking_id,
                        "trigger_source": trigger_source,
                        "missing_info": True,
                        "missing_fields": missing_fields,
                    }
                ),
            )
        )
    except Exception:
        # Audit-trail entry must not fail the booking creation.
        pass

    db.session.commit()
    return {"booking_id": booking_id, "created": True, "missing_info": True, "missing_fields": missing_fields}
