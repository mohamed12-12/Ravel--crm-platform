from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import or_, text

from app.extensions import db
from app.models import BookingStatusHistory, HandoffQueue, Lead, Traveler, TravelerDocument, Trip, TripBooking, TripMedia
from services.crm.system_services.unified_service import UnifiedCRMService


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _trim(value: Any) -> str:
    return str(value or "").strip()


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


@dataclass
class IdentityResolution:
    lookup_phone: dict[str, str]
    match_status: str
    handoff_required: bool
    handoff_reason: str
    name_match_status: str
    actions: list[str]
    traveler: dict[str, Any] | None


class PostgresAgentBridgeService:
    """Backend-selected CRM bridge using the same SQLAlchemy source of truth as the dashboard."""

    normalize_phone = staticmethod(UnifiedCRMService.normalize_phone)
    lookup_key_variants = staticmethod(UnifiedCRMService.lookup_key_variants)
    write_result_contract = staticmethod(UnifiedCRMService.write_result_contract)
    booking_idempotency_key = UnifiedCRMService.booking_idempotency_key
    lead_idempotency_key = staticmethod(UnifiedCRMService.lead_idempotency_key)
    handoff_idempotency_key = staticmethod(UnifiedCRMService.handoff_idempotency_key)

    ACTIVE_HANDOFF_STATUSES = {"pending", "in progress", "open"}
    ACTIVE_BOOKING_STATUSES = {"draft", "confirmed", "pending"}

    def resolve_identity(self, full_name: str, raw_phone: str, country_code: str = "20") -> IdentityResolution:
        phone = self.normalize_phone(raw_phone, country_code)
        lookup_keys = self.lookup_key_variants(phone)
        normalized = _trim(phone.get("normalized_whatsapp"))
        raw_input = _trim(phone.get("raw_phone"))
        query = Traveler.query
        clauses = []
        if normalized:
            normalized_digits = "".join(ch for ch in normalized if ch.isdigit())
            clauses.extend(
                [
                    Traveler.integrated_whatsapp == normalized,
                    Traveler.normalized_whatsapp == normalized,
                ]
            )
            if normalized_digits:
                clauses.extend(
                    [
                        Traveler.integrated_whatsapp == normalized_digits,
                        Traveler.normalized_whatsapp == normalized_digits,
                    ]
                )
        if raw_input:
            clauses.append(Traveler.whatsapp_raw == raw_input)
        if lookup_keys:
            clauses.append(Traveler.phone_lookup_key.in_(lookup_keys))
        matches = query.filter(or_(*clauses)).all() if clauses else []
        records = [traveler.to_dict() for traveler in matches]
        if not records:
            return IdentityResolution(phone, "not_found", False, "", "unknown", [], None)
        if len(records) > 1:
            return IdentityResolution(
                phone,
                "multiple_matches",
                True,
                "multiple traveler records share this phone number",
                "unknown",
                ["needs_human_review"],
                None,
            )
        return IdentityResolution(phone, "single_match", False, "", "unknown", [], records[0])

    def build_trip_result(self, trip_type: str | None, *, today: date | None = None) -> dict[str, list[dict[str, Any]]]:
        today = today or date.today()
        query = Trip.query
        normalized_trip_type = _trim(trip_type)
        if normalized_trip_type:
            query = query.filter(Trip.type == normalized_trip_type)
        trips = []
        for trip in query.order_by(Trip.start_date.asc().nullslast(), Trip.trip_name.asc()).all():
            if _trim(trip.sales_status).lower() in {"closed", "cancelled"}:
                continue
            trip_dict = self._trip_to_result(trip)
            if trip.start_date and trip.start_date < today:
                continue
            trips.append(trip_dict)
        open_trips = [trip for trip in trips if trip.get("start_date")]
        date_tbd_trips = [trip for trip in trips if not trip.get("start_date")]
        return {"open_trips": open_trips, "date_tbd_trips": date_tbd_trips}

    def get_traveler(self, traveler_id: str) -> dict[str, Any] | None:
        traveler = db.session.get(Traveler, traveler_id)
        return traveler.to_dict() if traveler else None

    def get_trip(self, trip_id: str) -> dict[str, Any] | None:
        trip = db.session.get(Trip, trip_id)
        return self._trip_to_result(trip) if trip else None

    def get_trip_media(self, trip_id: str) -> list[dict[str, Any]]:
        return [
            media.to_public_dict()
            for media in TripMedia.query.filter_by(
                trip_id=trip_id,
                is_active=True,
                verification_status="verified",
            ).order_by(TripMedia.display_order.asc(), TripMedia.media_id.asc())
        ]

    def lookup_leads(
        self,
        *,
        lead_id: str = "",
        traveler_id: str = "",
        raw_phone: str = "",
        country_code: str = "",
    ) -> list[dict[str, Any]]:
        query = Lead.query
        if lead_id:
            query = query.filter(Lead.lead_id == lead_id)
        if traveler_id:
            query = query.filter(Lead.traveler_id == traveler_id)
        if raw_phone:
            phone = self.normalize_phone(raw_phone, country_code or "20")
            lookup_keys = self.lookup_key_variants(phone)
            phone_clauses = []
            normalized = _trim(phone.get("normalized_whatsapp"))
            local_number = _trim(phone.get("local_number")) or _trim(raw_phone)
            lookup_key = _trim(phone.get("lookup_key"))
            if normalized:
                phone_clauses.append(Lead.integrated_whatsapp == normalized)
            if local_number:
                phone_clauses.append(Lead.raw_phone == local_number)
            if lookup_key:
                phone_clauses.append(Lead.phone_lookup_key == lookup_key)
            if lookup_keys:
                phone_clauses.append(Lead.phone_lookup_key.in_(lookup_keys))
            query = query.filter(or_(*phone_clauses))
        return [lead.to_dict() for lead in query.order_by(Lead.created_at.desc(), Lead.lead_id.desc()).all()]

    def get_bookings(self, *, booking_id: str = "", traveler_id: str = "", lead_id: str = "") -> list[dict[str, Any]]:
        query = TripBooking.query
        if booking_id:
            query = query.filter(TripBooking.booking_id == booking_id)
        if traveler_id:
            query = query.filter(TripBooking.traveler_id == traveler_id)
        if lead_id:
            query = query.filter(TripBooking.lead_id == lead_id)
        rows = query.order_by(TripBooking.draft_created_at.desc(), TripBooking.booking_id.desc()).all()
        return [self._booking_to_dict(row) for row in rows]

    def get_traveler_trip_history(self, traveler_id: str) -> list[dict[str, Any]]:
        rows = (
            db.session.query(TripBooking, Trip)
            .outerjoin(Trip, Trip.trip_id == TripBooking.trip_id)
            .filter(TripBooking.traveler_id == traveler_id)
            .order_by(TripBooking.draft_created_at.desc(), TripBooking.booking_id.desc())
            .all()
        )
        history = []
        for booking, trip in rows:
            history.append(
                {
                    "booking_id": booking.booking_id,
                    "trip_id": booking.trip_id or "",
                    "trip_name": (trip.trip_name if trip else booking.trip_name) or "",
                    "trip_type": (trip.type if trip else "") or "",
                    "booking_status": booking.booking_status or "",
                    "start_date": _iso(trip.start_date if trip else None),
                    "end_date": _iso(trip.end_date if trip else None),
                }
            )
        return history

    def get_passport_documents(self, traveler_id: str) -> list[dict[str, Any]]:
        rows = TravelerDocument.query.filter_by(traveler_id=traveler_id).order_by(TravelerDocument.document_id.desc()).all()
        return [row.to_dict() for row in rows]

    def save_traveler_passport(self, traveler_id: str, **payload: Any) -> dict[str, Any]:
        traveler = db.session.get(Traveler, traveler_id)
        if traveler is None:
            raise ValueError(f"Traveler not found: {traveler_id}")
        for field in ("passport_name", "passport_number", "passport_nationality", "passport_attachment_ref"):
            if field in payload:
                setattr(traveler, field, payload.get(field) or None)
        if "passport_expiry" in payload:
            value = payload.get("passport_expiry")
            traveler.passport_expiry = date.fromisoformat(value[:10]) if value else None
        db.session.commit()
        return {
            "traveler_id": traveler.traveler_id,
            "passport_status": "provided" if traveler.passport_number or traveler.passport_attachment_ref else "missing",
        }

    def upsert_lead(
        self,
        *,
        customer_name: str,
        raw_phone: str,
        traveler_id: str | None,
        lead_stage: str,
        lead_source: str,
        channel: str,
        preferred_trip_type: str = "",
        interested_trip_ids: str = "",
        suggested_trip_ids: str = "",
        priority: str = "Medium",
        follow_up_status: str = "",
        follow_up_due_date: str = "",
        notes: str = "",
        booking_id: str = "",
        traveler_status: str = "",
        customer_tier: str = "",
        match_status: str = "",
        last_interaction_id: str = "",
        flow_key: str = "",
        current_step: str = "",
        handoff_required: bool = False,
        handoff_reason: str = "",
        language: str = "",
        country_code: str = "20",
        group_size: int | str = 1,
        force_create_new: bool = False,
        session_id: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        phone = self.normalize_phone(raw_phone, country_code)
        interest_key = interested_trip_ids or preferred_trip_type or "general"
        resolved_idempotency_key = _trim(idempotency_key) or self.lead_idempotency_key(
            normalized_phone=phone.get("lookup_key") or phone.get("normalized_whatsapp") or raw_phone,
            trip_interest_or_general=interest_key,
            session_id=session_id or flow_key,
        )
        existing = None
        if not force_create_new:
            existing = self._lead_by_idempotency(resolved_idempotency_key)
            if existing is None and traveler_id:
                existing = (
                    Lead.query.filter_by(traveler_id=traveler_id)
                    .order_by(Lead.created_at.desc(), Lead.lead_id.desc())
                    .first()
                )
        if existing:
            existing.updated_at = _utc_now()
            existing.interaction_count = int(existing.interaction_count or 0) + 1
            existing.idempotency_key = getattr(existing, "idempotency_key", None) or resolved_idempotency_key
            db.session.commit()
            payload = existing.to_dict()
            payload["idempotency_key"] = resolved_idempotency_key
            payload["write_result_contract"] = self.write_result_contract(
                status="reused",
                executed=False,
                reused=True,
                record_type="lead",
                record_id=existing.lead_id,
                idempotency_key=resolved_idempotency_key,
                customer_confirmation_allowed=True,
                safe_customer_message_key="lead.reused",
                audit={"session_id": session_id},
            )
            return payload

        lead_id = self._next_prefixed_id("leads", "lead_id", "LD", 5)
        lead = Lead(
            lead_id=lead_id,
            customer_name=customer_name or None,
            raw_phone=_trim(phone.get("local_number")) or raw_phone or None,
            integrated_whatsapp=_trim(phone.get("normalized_whatsapp")) or None,
            phone_lookup_key=_trim(phone.get("lookup_key")) or None,
            traveler_id=traveler_id or None,
            traveler_status=traveler_status or None,
            customer_tier=customer_tier or None,
            match_status=match_status or None,
            lead_stage=lead_stage or None,
            lead_source=lead_source or None,
            channel=channel or None,
            preferred_trip_type=preferred_trip_type or None,
            interested_trip_ids=interested_trip_ids or None,
            suggested_trip_ids=suggested_trip_ids or None,
            priority=priority or None,
            follow_up_status=follow_up_status or None,
            follow_up_due_date=date.fromisoformat(follow_up_due_date[:10]) if follow_up_due_date else None,
            last_interaction_id=last_interaction_id or None,
            interaction_count=1,
            handoff_required=bool(handoff_required),
            handoff_reason=handoff_reason or None,
            notes=notes or None,
            flow_key=flow_key or None,
            current_step=current_step or None,
            language=language or None,
            booking_id=booking_id or None,
            group_size=int(group_size or 1),
        )
        setattr(lead, "idempotency_key", resolved_idempotency_key)
        db.session.add(lead)
        traveler = db.session.get(Traveler, traveler_id) if traveler_id else None
        if traveler is not None:
            traveler.last_lead_id = lead_id
        db.session.commit()
        payload = lead.to_dict()
        payload["idempotency_key"] = resolved_idempotency_key
        payload["write_result_contract"] = self.write_result_contract(
            status="created",
            executed=True,
            reused=False,
            record_type="lead",
            record_id=lead_id,
            idempotency_key=resolved_idempotency_key,
            customer_confirmation_allowed=True,
            safe_customer_message_key="lead.created",
            audit={"session_id": session_id},
        )
        return payload

    def update_lead_stage(
        self,
        lead_id: str,
        *,
        requested_stage: str,
        priority: str = "",
        follow_up_status: str = "",
        follow_up_due_date: str = "",
        notes: str = "",
        channel: str = "",
        flow_key: str = "",
        current_step: str = "",
    ) -> dict[str, Any]:
        lead = db.session.get(Lead, lead_id)
        if lead is None:
            raise ValueError(f"Lead not found: {lead_id}")
        lead.lead_stage = requested_stage or lead.lead_stage
        lead.priority = priority or lead.priority
        lead.follow_up_status = follow_up_status or lead.follow_up_status
        lead.follow_up_due_date = date.fromisoformat(follow_up_due_date[:10]) if follow_up_due_date else lead.follow_up_due_date
        lead.channel = channel or lead.channel
        lead.flow_key = flow_key or lead.flow_key
        lead.current_step = current_step or lead.current_step
        if notes:
            lead.notes = notes
        lead.updated_at = _utc_now()
        db.session.commit()
        payload = lead.to_dict()
        payload["write_result_contract"] = self.write_result_contract(
            status="updated",
            executed=True,
            reused=False,
            record_type="lead",
            record_id=lead.lead_id,
            customer_confirmation_allowed=True,
            safe_customer_message_key="lead.updated",
            audit={"lead_id": lead_id},
        )
        return payload

    def create_handoff_case(
        self,
        *,
        lead_id: str = "",
        traveler_id: str = "",
        trip_id: str = "",
        flow_key: str = "",
        reason_code: str = "",
        reason_text: str = "",
        priority: str = "",
        channel: str = "",
        status: str = "Pending",
        customer_name: str = "",
        agent_summary: str = "",
        customer_summary: str = "",
        notes: str = "",
        metadata: dict[str, Any] | None = None,
        lead_stage_override: str = "",
        update_lead: bool = True,
        deduplicate_open: bool = False,
        session_id: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        reason_code = _trim(reason_code) or "manual_handoff"
        reason_text = _trim(reason_text) or reason_code
        scope_key = traveler_id or lead_id or flow_key or customer_name
        resolved_idempotency_key = _trim(idempotency_key) or self.handoff_idempotency_key(
            traveler_id_or_phone=scope_key,
            reason_code=reason_code,
            session_id_or_open_lead_id=session_id or lead_id,
        )
        existing = self._handoff_by_idempotency(resolved_idempotency_key)
        if existing is None and deduplicate_open:
            open_query = HandoffQueue.query.filter(
                HandoffQueue.status.in_(["Pending", "In Progress"]),
                HandoffQueue.traveler_id == (traveler_id or None),
                HandoffQueue.reason == reason_text,
            )
            existing = open_query.order_by(HandoffQueue.created_at.desc(), HandoffQueue.handoff_id.desc()).first()
        if existing:
            payload = existing.to_dict()
            payload["reason_text"] = existing.reason or reason_text
            payload["deduplicated"] = True
            payload["idempotency_key"] = resolved_idempotency_key
            payload["write_result_contract"] = self.write_result_contract(
                status="reused",
                executed=False,
                reused=True,
                record_type="handoff",
                record_id=existing.handoff_id,
                idempotency_key=resolved_idempotency_key,
                customer_confirmation_allowed=True,
                safe_customer_message_key="handoff.reused",
                audit={"session_id": session_id},
            )
            return payload

        handoff_id = self._next_prefixed_id("handoff_queue", "handoff_id", "H-", 8)
        handoff = HandoffQueue(
            handoff_id=handoff_id,
            lead_id=lead_id or None,
            traveler_id=traveler_id or None,
            trip_id=trip_id or None,
            flow_key=flow_key or None,
            reason=reason_text,
            priority=priority or "Medium",
            channel=channel or None,
            status=status or "Pending",
            notes=self._handoff_notes(notes, reason_code, customer_name, customer_summary, agent_summary, metadata),
        )
        setattr(handoff, "idempotency_key", resolved_idempotency_key)
        db.session.add(handoff)
        if update_lead and lead_id:
            lead = db.session.get(Lead, lead_id)
            if lead is not None:
                lead.handoff_required = True
                lead.handoff_reason = reason_text
                lead.handoff_id = handoff_id
                if lead_stage_override:
                    lead.lead_stage = lead_stage_override
        db.session.commit()
        payload = handoff.to_dict()
        payload["reason_text"] = reason_text
        payload["deduplicated"] = False
        payload["idempotency_key"] = resolved_idempotency_key
        payload["write_result_contract"] = self.write_result_contract(
            status="created",
            executed=True,
            reused=False,
            record_type="handoff",
            record_id=handoff_id,
            idempotency_key=resolved_idempotency_key,
            customer_confirmation_allowed=True,
            safe_customer_message_key="handoff.created",
            audit={"session_id": session_id},
        )
        return payload

    def create_booking_draft(
        self,
        *,
        trip_id: str,
        traveler_id: str,
        traveler_name: str,
        room_type: str,
        room_group: str = "",
        boys_rooms_requested: int | str = 0,
        girls_rooms_requested: int | str = 0,
        room_requirements: list[dict[str, Any]] | dict[str, Any] | str | None = None,
        channel: str = "",
        lead_id: str = "",
        flight_option: str = "",
        date_option: str = "",
        currency: str = "",
        source: str = "",
        agent_notes: str = "",
        passport_required: bool = False,
        passport_status: str = "",
        group_size: int | str = 1,
        session_id: str = "",
        idempotency_key: str = "",
        require_explicit_confirmation: bool = False,
        customer_confirmed: bool | str = True,
    ) -> dict[str, Any]:
        if require_explicit_confirmation and not self._as_bool(customer_confirmed):
            return {
                "booking_id": "",
                "booking_status": "blocked",
                "write_result_contract": self.write_result_contract(
                    status="blocked",
                    executed=False,
                    reused=False,
                    record_type="booking",
                    idempotency_key=_trim(idempotency_key),
                    customer_confirmation_allowed=False,
                    error_code="confirmation_required",
                    safe_customer_message_key="booking.confirmation_required",
                    audit={"source": source, "session_id": session_id},
                ),
            }
        traveler = db.session.get(Traveler, traveler_id)
        if traveler is None:
            raise ValueError(f"Traveler not found: {traveler_id}")
        trip = db.session.get(Trip, trip_id)
        if trip is None:
            raise ValueError(f"Trip not found: {trip_id}")
        if _trim(trip.sales_status).lower() == "cancelled":
            raise ValueError("Trip capacity unavailable.")
        normalized_requirements = self._normalize_room_requirements(room_requirements, room_type, room_group)
        resolved_idempotency_key = _trim(idempotency_key) or self.booking_idempotency_key(
            traveler_id=traveler_id,
            trip_id=trip_id,
            room_requirements=normalized_requirements,
            session_id=session_id,
        )
        existing = self._booking_by_idempotency(resolved_idempotency_key)
        if existing:
            return self._booking_result(existing, "reused", False, True, resolved_idempotency_key)
        duplicate = (
            TripBooking.query.filter(
                TripBooking.traveler_id == traveler_id,
                TripBooking.trip_id == trip_id,
                TripBooking.booking_status.in_(["Draft", "Confirmed", "Pending"]),
            )
            .order_by(TripBooking.draft_created_at.desc(), TripBooking.booking_id.desc())
            .first()
        )
        if duplicate:
            return self._booking_result(duplicate, "duplicate", False, True, resolved_idempotency_key)
        self._assert_room_capacity(trip, room_type)
        booking_id = self._next_prefixed_id("trip_bookings", "booking_id", "BK", 6)
        booking = TripBooking(
            booking_id=booking_id,
            trip_id=trip.trip_id,
            trip_name=trip.trip_name,
            traveler_id=traveler.traveler_id,
            traveler_name=traveler_name or traveler.full_name,
            room_type=room_type,
            room_group=room_group or None,
            boys_rooms_requested=int(boys_rooms_requested or 0),
            girls_rooms_requested=int(girls_rooms_requested or 0),
            room_requirements_json=json.dumps(normalized_requirements, ensure_ascii=False),
            flight_option=flight_option or None,
            date_option=date_option or None,
            currency=currency or None,
            group_size=int(group_size or 1),
            booking_status="Draft",
            booking_source=source or channel or "Agent",
            lead_id=lead_id or None,
            payment_status="Pending",
            passport_required=bool(passport_required),
            passport_status=passport_status or None,
            booking_notes=agent_notes or None,
        )
        setattr(booking, "idempotency_key", resolved_idempotency_key)
        db.session.add(booking)
        self._apply_capacity_hold(trip, room_type)
        traveler.last_booking_id = booking_id
        if lead_id:
            lead = db.session.get(Lead, lead_id)
            if lead is not None:
                lead.booking_id = booking_id
        history = BookingStatusHistory(
            booking_id=booking_id,
            old_status=None,
            new_status="Draft",
            changed_by="agent_bridge",
            change_source="agent_write",
            notes="Created through PostgreSQL-backed agent bridge.",
        )
        db.session.add(history)
        db.session.commit()
        return self._booking_result(booking, "created", True, False, resolved_idempotency_key)

    def _trip_to_result(self, trip: Trip | None) -> dict[str, Any]:
        if trip is None:
            return {}
        data = trip.to_dict()
        data["trip_type"] = data.get("type") or ""
        data["available_single"] = int(trip.single_remaining or 0) - int(trip.draft_holds_single or 0)
        data["available_double"] = int(trip.double_remaining or 0) - int(trip.draft_holds_double or 0)
        data["available_triple"] = int(trip.triple_remaining or 0) - int(trip.draft_holds_triple or 0)
        data["passport_required"] = _trim(trip.type).lower() == "international"
        return data

    def _booking_to_dict(self, booking: TripBooking) -> dict[str, Any]:
        data = booking.to_dict()
        data["idempotency_key"] = getattr(booking, "idempotency_key", None)
        return data

    def _booking_result(
        self,
        booking: TripBooking,
        status: str,
        executed: bool,
        reused: bool,
        idempotency_key: str,
    ) -> dict[str, Any]:
        result = self._booking_to_dict(booking)
        result["idempotency_key"] = idempotency_key
        result["write_result"] = {"booking_draft": {"booking_id": booking.booking_id}}
        result["write_result_contract"] = self.write_result_contract(
            status=status,
            executed=executed,
            reused=reused,
            record_type="booking",
            record_id=booking.booking_id,
            idempotency_key=idempotency_key,
            customer_confirmation_allowed=True,
            safe_customer_message_key=f"booking.{status}",
            audit={"booking_id": booking.booking_id},
        )
        return result

    def _lead_by_idempotency(self, key: str) -> Lead | None:
        if not key:
            return None
        return Lead.query.filter(text("idempotency_key = :key")).params(key=key).first()

    def _handoff_by_idempotency(self, key: str) -> HandoffQueue | None:
        if not key:
            return None
        return HandoffQueue.query.filter(text("idempotency_key = :key")).params(key=key).first()

    def _booking_by_idempotency(self, key: str) -> TripBooking | None:
        if not key:
            return None
        return TripBooking.query.filter(text("idempotency_key = :key")).params(key=key).first()

    def _next_prefixed_id(self, table_name: str, column_name: str, prefix: str, width: int) -> str:
        query = text(f"SELECT {column_name} FROM {table_name} WHERE {column_name} LIKE :prefix ORDER BY {column_name} DESC LIMIT 1")
        last_value = db.session.execute(query, {"prefix": f"{prefix}%"}).scalar()
        digits = "".join(ch for ch in _trim(last_value) if ch.isdigit())
        next_number = int(digits or "0") + 1
        return f"{prefix}{next_number:0{width}d}"

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return _trim(value).lower() in {"1", "true", "yes", "y"}

    @staticmethod
    def _normalize_room_requirements(
        room_requirements: list[dict[str, Any]] | dict[str, Any] | str | None,
        room_type: str,
        room_group: str,
    ) -> list[dict[str, Any]]:
        if isinstance(room_requirements, list):
            return room_requirements
        if isinstance(room_requirements, dict):
            return [room_requirements]
        if isinstance(room_requirements, str) and room_requirements.strip():
            try:
                parsed = json.loads(room_requirements)
                if isinstance(parsed, list):
                    return parsed
                if isinstance(parsed, dict):
                    return [parsed]
            except json.JSONDecodeError:
                pass
        return [{"room_type": room_type, "room_group": room_group or ""}]

    @staticmethod
    def _assert_room_capacity(trip: Trip, room_type: str) -> None:
        mapping = {
            "single": int(trip.single_remaining or 0) - int(trip.draft_holds_single or 0),
            "double": int(trip.double_remaining or 0) - int(trip.draft_holds_double or 0),
            "triple": int(trip.triple_remaining or 0) - int(trip.draft_holds_triple or 0),
        }
        available = mapping.get(_trim(room_type).lower())
        if available is not None and available <= 0:
            raise ValueError("Trip capacity unavailable.")

    @staticmethod
    def _apply_capacity_hold(trip: Trip, room_type: str) -> None:
        key = _trim(room_type).lower()
        if key == "single":
            trip.draft_holds_single = int(trip.draft_holds_single or 0) + 1
        elif key == "double":
            trip.draft_holds_double = int(trip.draft_holds_double or 0) + 1
        elif key == "triple":
            trip.draft_holds_triple = int(trip.draft_holds_triple or 0) + 1

    @staticmethod
    def _handoff_notes(
        notes: str,
        reason_code: str,
        customer_name: str,
        customer_summary: str,
        agent_summary: str,
        metadata: dict[str, Any] | None,
    ) -> str:
        payload = {
            "notes": notes or "",
            "reason_code": reason_code or "",
            "customer_name": customer_name or "",
            "customer_summary": customer_summary or "",
            "agent_summary": agent_summary or "",
            "metadata": metadata or {},
        }
        return json.dumps(payload, ensure_ascii=False)


class PostgresAgentCRMTools:
    def __init__(self, service: PostgresAgentBridgeService | None = None) -> None:
        self.service = service or PostgresAgentBridgeService()
        self.api_client = None

    def _safe_traveler_fields(self, traveler: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(traveler, dict):
            return {}
        return {
            "traveler_id": traveler.get("traveler_id"),
            "full_name": traveler.get("full_name"),
            "status": traveler.get("status"),
            "local_trips_count": traveler.get("local_trips_count"),
            "international_trips_count": traveler.get("international_trips_count"),
            "total_trips": traveler.get("total_trips"),
            "passport_name": traveler.get("passport_name"),
            "passport_number": traveler.get("passport_number"),
            "passport_expiry": traveler.get("passport_expiry"),
            "passport_nationality": traveler.get("passport_nationality"),
            "passport_attachment_ref": traveler.get("passport_attachment_ref"),
            "whatsapp_raw": traveler.get("whatsapp_raw"),
            "integrated_whatsapp": traveler.get("integrated_whatsapp"),
            "normalized_whatsapp": traveler.get("normalized_whatsapp"),
            "phone_lookup_key": traveler.get("phone_lookup_key"),
            "preferred_currency": traveler.get("preferred_currency"),
        }

    @staticmethod
    def _field_state(value: Any) -> str:
        return "present" if value not in (None, "") else "missing"

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join(str(value or "").strip().split())

    def _trip_matches_query(self, trip: dict[str, Any], query: str) -> bool:
        haystack = " ".join(
            [
                str(trip.get("trip_id") or ""),
                str(trip.get("trip_name") or ""),
                str(trip.get("type") or ""),
                str(trip.get("trip_leader") or ""),
                str(trip.get("public_description") or ""),
            ]
        ).casefold()
        return query.casefold() in haystack

    def search_traveler(self, *, raw_phone: str, country_code: str = "") -> dict[str, Any]:
        resolution = self.service.resolve_identity("", raw_phone, country_code or "20")
        return {
            "lookup_phone": resolution.lookup_phone,
            "match_status": resolution.match_status,
            "handoff_required": resolution.handoff_required,
            "handoff_reason": resolution.handoff_reason,
            "name_match_status": resolution.name_match_status,
            "actions": list(resolution.actions),
            "traveler": resolution.traveler,
        }

    def find_traveler_by_phone(self, *, raw_phone: str, country_code: str = "") -> dict[str, Any]:
        result = self.search_traveler(raw_phone=raw_phone, country_code=country_code)
        traveler = result.get("traveler") if isinstance(result.get("traveler"), dict) else None
        if result.get("match_status") == "not_found":
            return {"status": "not_found", "traveler": None, "warnings": []}
        if result.get("match_status") == "multiple_matches":
            return {"status": "duplicate", "traveler": None, "warnings": [result.get("handoff_reason") or "duplicate_phone_match"]}
        safe = self._safe_traveler_fields(traveler)
        required_missing = [key for key in ("traveler_id", "full_name", "status") if not safe.get(key)]
        if required_missing:
            return {"status": "incomplete", "traveler": safe, "warnings": [f"Missing fields: {', '.join(required_missing)}"]}
        return {"status": "found", "traveler": safe, "warnings": list(result.get("actions") or [])}

    def get_traveler_profile(self, *, traveler_id: str = "", raw_phone: str = "", country_code: str = "") -> dict[str, Any]:
        if traveler_id:
            return {"traveler": self.service.get_traveler(traveler_id), "lookup_mode": "traveler_id"}
        if raw_phone:
            search = self.search_traveler(raw_phone=raw_phone, country_code=country_code)
            return {"traveler": search.get("traveler"), "lookup_mode": "phone", **search}
        return {"traveler": None, "lookup_mode": "empty"}

    def get_traveler_profile_safe(self, *, traveler_id: str = "", raw_phone: str = "", country_code: str = "") -> dict[str, Any]:
        profile = self.get_traveler_profile(traveler_id=traveler_id, raw_phone=raw_phone, country_code=country_code)
        traveler = profile.get("traveler") if isinstance(profile.get("traveler"), dict) else {}
        profile["field_states"] = {
            "traveler_id": self._field_state(traveler.get("traveler_id")),
            "full_name": self._field_state(traveler.get("full_name")),
            "status": self._field_state(traveler.get("status")),
            "local_trips_count": self._field_state(traveler.get("local_trips_count")),
            "international_trips_count": self._field_state(traveler.get("international_trips_count")),
            "total_trips": self._field_state(traveler.get("total_trips")),
            "passport_name": self._field_state(traveler.get("passport_name")),
            "passport_number": self._field_state(traveler.get("passport_number")),
            "passport_expiry": self._field_state(traveler.get("passport_expiry")),
            "passport_nationality": self._field_state(traveler.get("passport_nationality")),
            "passport_attachment_ref": self._field_state(traveler.get("passport_attachment_ref")),
        }
        profile["traveler"] = self._safe_traveler_fields(traveler)
        return profile

    def get_traveler_trip_history(self, *, traveler_id: str = "", raw_phone: str = "", country_code: str = "") -> dict[str, Any]:
        profile = self.get_traveler_profile_safe(traveler_id=traveler_id, raw_phone=raw_phone, country_code=country_code)
        traveler = profile.get("traveler") if isinstance(profile.get("traveler"), dict) else {}
        traveler_id = _trim(traveler.get("traveler_id") or traveler_id)
        if not traveler_id:
            return {"status": "not_found", "summary": {}, "history": []}
        history = self.service.get_traveler_trip_history(traveler_id)
        local = sum(1 for item in history if _trim(item.get("trip_type")).lower() == "local" and _trim(item.get("booking_status")).lower() != "cancelled")
        intl = sum(1 for item in history if _trim(item.get("trip_type")).lower() == "international" and _trim(item.get("booking_status")).lower() != "cancelled")
        return {
            "status": "found",
            "summary": {"local_trips": local, "international_trips": intl, "total_trips": local + intl, "source": "computed"},
            "history": history,
            "traveler": traveler,
        }

    def search_trips(self, *, trip_type: str = "", query: str = "") -> dict[str, Any]:
        trip_result = self.service.build_trip_result(trip_type or None, today=date.today())
        normalized_query = self._normalize_text(query).casefold()
        if normalized_query:
            for bucket in ("open_trips", "date_tbd_trips"):
                trip_result[bucket] = [trip for trip in trip_result[bucket] if self._trip_matches_query(trip, normalized_query)]
        return {"trip_type": trip_type or "", "query": query or "", **trip_result, "trips": [*trip_result["open_trips"], *trip_result["date_tbd_trips"]]}

    def search_available_trips(
        self,
        *,
        trip_type: str = "",
        destination: str = "",
        query: str = "",
        preferred_date: str = "",
        travelers: str = "",
        flight_option: str = "",
        room_type: str = "",
    ) -> dict[str, Any]:
        result = self.search_trips(trip_type=trip_type, query=query or destination)
        trips = list(result.get("trips") or [])
        normalized_destination = _trim(destination).casefold()
        normalized_query = _trim(query).casefold()
        normalized_date = _trim(preferred_date)
        normalized_room = _trim(room_type).casefold()
        if normalized_destination:
            trips = [trip for trip in trips if self._trip_matches_query(trip, normalized_destination)]
        if normalized_query and normalized_query != normalized_destination:
            trips = [trip for trip in trips if self._trip_matches_query(trip, normalized_query)]
        if normalized_date:
            trips = [trip for trip in trips if str(trip.get("start_date") or "").startswith(normalized_date[:10]) or str(trip.get("end_date") or "").startswith(normalized_date[:10])]
        if normalized_room:
            key = f"available_{normalized_room}".replace(" ", "_")
            trips = [trip for trip in trips if trip.get(key) is None or int(trip.get(key) or 0) > 0]
        return {
            "status": "found" if trips else "not_found",
            "trip_type": trip_type or "",
            "destination": destination or "",
            "preferred_date": preferred_date or "",
            "travelers": travelers or "",
            "flight_option": flight_option or "",
            "room_type": room_type or "",
            "trips": trips,
            "open_trips": [trip for trip in trips if trip in result.get("open_trips", [])],
            "date_tbd_trips": [trip for trip in trips if trip in result.get("date_tbd_trips", [])],
        }

    def get_trip_details(self, *, trip_id: str) -> dict[str, Any]:
        if not trip_id:
            return {"trip": None}
        trip = self.service.get_trip(trip_id)
        if not trip:
            return {"trip": None, "status": "not_found"}
        return {"trip": trip, "status": "found"}

    def get_trip_media(self, *, trip_id: str) -> dict[str, Any]:
        trip_id = self._normalize_text(trip_id)
        if not trip_id:
            return {"status": "missing_trip", "trip_id": "", "media": [], "message": "A trip must be selected before sharing official trip images."}
        media = self.service.get_trip_media(trip_id)
        if not media:
            return {"status": "not_found", "trip_id": trip_id, "cover": None, "gallery": [], "media": [], "message": "No verified CRM trip image is available for this trip."}
        cover = next((item for item in media if item.get("image_type") == "cover"), media[0])
        return {"status": "found", "trip_id": trip_id, "cover": cover, "gallery": media, "media": media, "message": "Verified CRM trip media retrieved."}

    def get_booking_status(self, *, booking_id: str = "", traveler_id: str = "", lead_id: str = "") -> dict[str, Any]:
        return {"bookings": self.service.get_bookings(booking_id=booking_id, traveler_id=traveler_id, lead_id=lead_id)}

    def lookup_lead(self, *, lead_id: str = "", traveler_id: str = "", raw_phone: str = "", country_code: str = "") -> dict[str, Any]:
        return {"leads": self.service.lookup_leads(lead_id=lead_id, traveler_id=traveler_id, raw_phone=raw_phone, country_code=country_code)}

    def get_passport_status(self, *, traveler_id: str = "", raw_phone: str = "", country_code: str = "") -> dict[str, Any]:
        traveler = None
        if traveler_id:
            traveler = self.service.get_traveler(traveler_id)
        elif raw_phone:
            resolved = self.search_traveler(raw_phone=raw_phone, country_code=country_code)
            traveler = resolved.get("traveler")
        traveler = traveler if isinstance(traveler, dict) else {}
        if not traveler.get("traveler_id"):
            return {"traveler": None, "documents": [], "passport_required": False}
        documents = self.service.get_passport_documents(str(traveler.get("traveler_id")))
        has_passport_fields = any(traveler.get(field) for field in ("passport_name", "passport_number", "passport_expiry", "passport_nationality", "passport_attachment_ref"))
        return {
            "traveler": traveler,
            "documents": documents,
            "passport_required": False,
            "passport_missing": not has_passport_fields,
            "has_passport_data": has_passport_fields,
        }
