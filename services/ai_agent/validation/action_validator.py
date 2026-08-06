from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from services.ai_agent.ai_agent_app.agent.read_only_tools import ReadOnlyCRMTools
from services.ai_agent.ai_agent_app.logger import agent_logger
from services.ai_agent.ai_agent_app.config import Settings
from services.ai_agent.validation.validation_result import (
    APPROVED,
    NEED_MORE_INFORMATION,
    REJECTED,
    ValidationResult,
)
from services.ai_agent.validation.validation_rules import (
    ACTIVE_BOOKING_STATUSES,
    ALLOWED_BOOKING_UPDATE_FIELDS,
    ALLOWED_TRAVELER_UPDATE_FIELDS,
    CLOSED_LEAD_STAGES,
    HANDOFF_CONFIDENCE_THRESHOLD,
    HANDOFF_VALIDATION_FAILURE_THRESHOLD,
    REJECTED_TRAVELER_STATUSES,
    SUPPORTED_VALIDATION_ACTIONS,
    VALID_BOOKING_STATUSES,
    normalize_flight_option,
    VALID_ROOM_TYPES,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class ActionValidator:
    def __init__(self, settings: Settings, read_only_tools: ReadOnlyCRMTools | None = None) -> None:
        self.settings = settings
        self.read_only_tools = read_only_tools or ReadOnlyCRMTools(settings)
        self.service = self.read_only_tools.service

    def validate_action(
        self,
        *,
        action: str,
        payload: dict[str, Any] | None = None,
        session_context: dict[str, Any] | None = None,
    ) -> ValidationResult:
        normalized_action = str(action or "").strip()
        clean_payload = dict(payload or {})
        clean_context = dict(session_context or {})

        if normalized_action not in SUPPORTED_VALIDATION_ACTIONS:
            result = ValidationResult(
                action=normalized_action,
                decision=REJECTED,
                reasons=[f"Unsupported validation action: {normalized_action or 'empty_action'}."],
                session_id=str(clean_context.get("session_id") or ""),
            )
            self._log_decision(result, clean_payload)
            return result

        handler = getattr(self, f"_validate_{normalized_action}", None)
        if handler is None:
            result = ValidationResult(
                action=normalized_action,
                decision=REJECTED,
                reasons=[f"No validation rule exists for {normalized_action}."],
                session_id=str(clean_context.get("session_id") or ""),
            )
            self._log_decision(result, clean_payload)
            return result

        result = handler(clean_payload, clean_context)
        self._log_decision(result, clean_payload)
        return result

    def _validate_create_traveler(self, payload: dict[str, Any], session_context: dict[str, Any]) -> ValidationResult:
        raw_phone = self._value(payload, session_context, "raw_phone", "pending_raw_phone")
        country_code = self._value(payload, session_context, "country_code") or self.settings.default_country_code
        session_id = str(session_context.get("session_id") or "")
        if not raw_phone:
            return ValidationResult(
                action="create_traveler",
                decision=NEED_MORE_INFORMATION,
                missing_information=["raw_phone"],
                reasons=["A WhatsApp number is required before a traveler profile can be created."],
                session_id=session_id,
            )

        resolution = self.read_only_tools.search_traveler(raw_phone=raw_phone, country_code=country_code)
        if resolution.get("match_status") == "not_found":
            return ValidationResult(
                action="create_traveler",
                decision=APPROVED,
                warnings=["Validation approved; execution must still pass through the controlled write tool."],
                session_id=session_id,
            )

        traveler_id = str((resolution.get("traveler") or {}).get("traveler_id") or "")
        reasons = ["An existing traveler already matches this WhatsApp identity."]
        if resolution.get("match_status") == "multiple_matches":
            reasons = ["This WhatsApp number matches multiple traveler records and needs human review."]
        if resolution.get("handoff_required"):
            reasons.append(str(resolution.get("handoff_reason") or "human_review_required"))
        return ValidationResult(
            action="create_traveler",
            decision=REJECTED,
            reasons=reasons,
            warnings=["Create traveler is blocked until the identity conflict is resolved."],
            traveler_id=traveler_id,
            session_id=session_id,
        )

    def _validate_update_traveler(self, payload: dict[str, Any], session_context: dict[str, Any]) -> ValidationResult:
        traveler, resolution = self._resolve_traveler(payload, session_context)
        session_id = str(session_context.get("session_id") or "")
        if traveler is None:
            missing = ["traveler_id_or_raw_phone"] if not self._has_identity_hint(payload, session_context) else []
            decision = NEED_MORE_INFORMATION if missing else REJECTED
            reasons = ["I need an existing traveler profile before I can validate an update."]
            if not missing:
                reasons = ["No existing traveler was found for this update request."]
            return ValidationResult(
                action="update_traveler",
                decision=decision,
                reasons=reasons,
                missing_information=missing,
                session_id=session_id,
            )

        status = str(traveler.get("status") or "").strip().lower()
        if status in REJECTED_TRAVELER_STATUSES:
            return ValidationResult(
                action="update_traveler",
                decision=REJECTED,
                reasons=[f"Traveler status {traveler.get('status') or 'unknown'} does not allow profile updates."],
                traveler_id=str(traveler.get("traveler_id") or ""),
                session_id=session_id,
            )

        requested_fields = [field for field in ALLOWED_TRAVELER_UPDATE_FIELDS if self._has_value(payload, session_context, field)]
        if not requested_fields:
            return ValidationResult(
                action="update_traveler",
                decision=NEED_MORE_INFORMATION,
                reasons=["No traveler fields were provided for validation."],
                missing_information=["traveler_update_fields"],
                traveler_id=str(traveler.get("traveler_id") or ""),
                session_id=session_id,
            )

        warnings: list[str] = []
        if resolution and resolution.get("handoff_required"):
            warnings.append(f"Identity review flag: {resolution.get('handoff_reason') or 'manual_review'}")
        warnings.append("Validation approved; execution must still pass through the controlled write tool.")
        return ValidationResult(
            action="update_traveler",
            decision=APPROVED,
            warnings=warnings,
            traveler_id=str(traveler.get("traveler_id") or ""),
            session_id=session_id,
            metadata={"requested_fields": requested_fields},
        )

    def _validate_create_lead(self, payload: dict[str, Any], session_context: dict[str, Any]) -> ValidationResult:
        traveler, _resolution = self._resolve_traveler(payload, session_context)
        raw_phone = self._value(payload, session_context, "raw_phone", "pending_raw_phone")
        country_code = self._value(payload, session_context, "country_code") or self.settings.default_country_code
        customer_name = self._value(payload, session_context, "customer_name", "full_name")
        if not customer_name and traveler is not None:
            customer_name = str(traveler.get("full_name") or "").strip()
        session_id = str(session_context.get("session_id") or "")
        if traveler is None and not raw_phone:
            return ValidationResult(
                action="create_lead",
                decision=NEED_MORE_INFORMATION,
                reasons=["I need a traveler identity or WhatsApp number before I can validate a lead."],
                missing_information=["traveler_id_or_raw_phone"],
                session_id=session_id,
            )
        if not customer_name:
            return ValidationResult(
                action="create_lead",
                decision=NEED_MORE_INFORMATION,
                reasons=["I need the customer's name before I can validate lead creation."],
                missing_information=["customer_name"],
                session_id=session_id,
            )

        traveler_id = str((traveler or {}).get("traveler_id") or "")
        traveler_status = str((traveler or {}).get("status") or "").strip().lower()
        if traveler_status in REJECTED_TRAVELER_STATUSES:
            return ValidationResult(
                action="create_lead",
                decision=REJECTED,
                reasons=[f"Traveler status {traveler.get('status') or 'unknown'} does not allow a new lead."],
                traveler_id=traveler_id,
                session_id=session_id,
            )

        duplicate_override = str(session_context.get("duplicate_lead_override") or "").strip()
        open_leads = self._find_open_leads(traveler_id=traveler_id, raw_phone=raw_phone, country_code=country_code)
        if open_leads and duplicate_override != "new":
            return ValidationResult(
                action="create_lead",
                decision=REJECTED,
                reasons=["An open lead already exists for this traveler or WhatsApp identity."],
                warnings=[f"Existing lead IDs: {', '.join(lead['lead_id'] for lead in open_leads if lead.get('lead_id'))}"],
                traveler_id=traveler_id,
                session_id=session_id,
            )

        warnings = ["Validation approved; execution must still pass through the controlled write tool."]
        if not self._value(payload, session_context, "trip_id", "selected_trip_id", "trip_type"):
            warnings.append("Trip interest is still missing and should be collected before execution.")
        return ValidationResult(
            action="create_lead",
            decision=APPROVED,
            warnings=warnings,
            traveler_id=traveler_id,
            session_id=session_id,
        )

    def _validate_update_lead_stage(self, payload: dict[str, Any], session_context: dict[str, Any]) -> ValidationResult:
        lead_id = self._value(payload, session_context, "lead_id")
        target_stage = self._value(payload, session_context, "requested_stage", "lead_stage")
        session_id = str(session_context.get("session_id") or "")
        if not lead_id:
            return ValidationResult(
                action="update_lead_stage",
                decision=NEED_MORE_INFORMATION,
                reasons=["Lead ID is required before I can validate a stage update."],
                missing_information=["lead_id"],
                session_id=session_id,
            )
        if not target_stage:
            return ValidationResult(
                action="update_lead_stage",
                decision=NEED_MORE_INFORMATION,
                reasons=["I need the target lead stage before I can validate the update."],
                missing_information=["requested_stage"],
                session_id=session_id,
            )
        lead = self._get_lead_by_id(lead_id)
        if lead is None:
            return ValidationResult(
                action="update_lead_stage",
                decision=REJECTED,
                reasons=[f"Lead {lead_id} was not found."],
                session_id=session_id,
            )

        warnings = ["Validation approved; execution must still pass through the controlled write tool."]
        current_stage = str(lead.get("lead_stage") or "").strip()
        if current_stage == target_stage:
            warnings.append("Lead is already in the requested stage.")
        return ValidationResult(
            action="update_lead_stage",
            decision=APPROVED,
            warnings=warnings,
            traveler_id=str(lead.get("traveler_id") or ""),
            session_id=session_id,
            metadata={"lead_id": lead_id, "current_stage": current_stage, "requested_stage": target_stage},
        )

    def _validate_set_guardian_consent(self, payload: dict[str, Any], session_context: dict[str, Any]) -> ValidationResult:
        traveler_id = self._value(payload, session_context, "traveler_id")
        session_id = str(session_context.get("session_id") or "")
        if not traveler_id:
            return ValidationResult(
                action="set_guardian_consent",
                decision=NEED_MORE_INFORMATION,
                reasons=["A traveler_id is required before I can validate guardian consent."],
                missing_information=["traveler_id"],
                session_id=session_id,
            )
        guardian_name = self._value(payload, session_context, "guardian_name")
        guardian_phone = self._value(payload, session_context, "guardian_phone")
        missing = [name for name, value in (("guardian_name", guardian_name), ("guardian_phone", guardian_phone)) if not value]
        if missing:
            return ValidationResult(
                action="set_guardian_consent",
                decision=NEED_MORE_INFORMATION,
                reasons=["Guardian name and phone are both required before I can validate consent."],
                missing_information=missing,
                traveler_id=str(traveler_id),
                session_id=session_id,
            )
        return ValidationResult(
            action="set_guardian_consent",
            decision=APPROVED,
            warnings=["Validation approved; execution must still pass through the controlled write tool."],
            traveler_id=str(traveler_id),
            session_id=session_id,
        )

    def _validate_flag_lead_guardian_approval(self, payload: dict[str, Any], session_context: dict[str, Any]) -> ValidationResult:
        lead_id = self._value(payload, session_context, "lead_id")
        session_id = str(session_context.get("session_id") or "")
        if not lead_id:
            return ValidationResult(
                action="flag_lead_guardian_approval",
                decision=NEED_MORE_INFORMATION,
                reasons=["A lead_id is required before I can validate the guardian-approval flag."],
                missing_information=["lead_id"],
                session_id=session_id,
            )
        return ValidationResult(
            action="flag_lead_guardian_approval",
            decision=APPROVED,
            warnings=["Validation approved; execution must still pass through the controlled write tool."],
            session_id=session_id,
        )

    def _validate_create_booking_draft(self, payload: dict[str, Any], session_context: dict[str, Any]) -> ValidationResult:
        traveler, _resolution = self._resolve_traveler(payload, session_context)
        session_id = str(session_context.get("session_id") or "")
        if traveler is None:
            missing = ["traveler_id_or_raw_phone"] if not self._has_identity_hint(payload, session_context) else []
            return ValidationResult(
                action="create_booking_draft",
                decision=NEED_MORE_INFORMATION if missing else REJECTED,
                reasons=["An existing traveler is required before a booking draft can be validated."],
                missing_information=missing,
                session_id=session_id,
            )

        traveler_id = str(traveler.get("traveler_id") or "")
        traveler_status = str(traveler.get("status") or "").strip().lower()
        if traveler_status in REJECTED_TRAVELER_STATUSES:
            return ValidationResult(
                action="create_booking_draft",
                decision=REJECTED,
                reasons=[f"Traveler status {traveler.get('status') or 'unknown'} blocks booking draft creation."],
                warnings=["A human handoff should be created for this traveler."],
                traveler_id=traveler_id,
                session_id=session_id,
            )

        trip_id = self._value(payload, session_context, "trip_id", "selected_trip_id")
        if not trip_id:
            return ValidationResult(
                action="create_booking_draft",
                decision=REJECTED,
                reasons=["A trip must be selected before a booking draft can be validated."],
                traveler_id=traveler_id,
                session_id=session_id,
            )

        trip = self.read_only_tools.get_trip_details(trip_id=trip_id).get("trip")
        if not trip:
            return ValidationResult(
                action="create_booking_draft",
                decision=REJECTED,
                reasons=[f"Trip {trip_id} was not found."],
                traveler_id=traveler_id,
                session_id=session_id,
            )

        sales_status = str(trip.get("sales_status") or "").strip().lower()
        if sales_status and sales_status != "open":
            return ValidationResult(
                action="create_booking_draft",
                decision=REJECTED,
                reasons=[f"Trip {trip_id} is not open for booking."],
                traveler_id=traveler_id,
                session_id=session_id,
            )

        duplicate_booking = self._find_duplicate_active_booking(traveler_id=traveler_id, trip_id=trip_id)
        if duplicate_booking is not None:
            return ValidationResult(
                action="create_booking_draft",
                decision=REJECTED,
                reasons=["An active booking already exists for this traveler on the selected trip."],
                warnings=[f"Existing booking ID: {duplicate_booking.get('booking_id') or ''}".strip()],
                traveler_id=traveler_id,
                session_id=session_id,
            )

        room_type = self._value(payload, session_context, "room_type")
        if not room_type:
            return ValidationResult(
                action="create_booking_draft",
                decision=NEED_MORE_INFORMATION,
                reasons=["Room type is required before a booking draft can be validated."],
                missing_information=["room_type"],
                traveler_id=traveler_id,
                session_id=session_id,
            )
        if room_type not in VALID_ROOM_TYPES:
            return ValidationResult(
                action="create_booking_draft",
                decision=REJECTED,
                reasons=[f"Unsupported room type: {room_type}."],
                traveler_id=traveler_id,
                session_id=session_id,
            )

        flight_option = self._value(payload, session_context, "flight_option")
        normalized_flight_option = normalize_flight_option(flight_option)
        if flight_option and not normalized_flight_option:
            return ValidationResult(
                action="create_booking_draft",
                decision=REJECTED,
                reasons=[f"Unsupported flight option: {flight_option}."],
                traveler_id=traveler_id,
                session_id=session_id,
            )

        if not self._trip_has_departure_dates(trip):
            return ValidationResult(
                action="create_booking_draft",
                decision=NEED_MORE_INFORMATION,
                reasons=["The traveler still needs a confirmed departure option for this trip."],
                missing_information=["departure_date"],
                traveler_id=traveler_id,
                session_id=session_id,
            )

        if self._passport_required_for_trip(trip, normalized_flight_option or flight_option):
            passport_status = self.read_only_tools.get_passport_status(traveler_id=traveler_id)
            has_attachment = bool(
                self._value(payload, session_context, "passport_attachment_ref")
                or (passport_status.get("traveler") or {}).get("passport_attachment_ref")
            )
            if passport_status.get("passport_missing", False) and not has_attachment:
                return ValidationResult(
                    action="create_booking_draft",
                    decision=NEED_MORE_INFORMATION,
                    reasons=["Passport attachment is required before this booking draft can be validated."],
                    missing_information=["passport_attachment_ref"],
                    traveler_id=traveler_id,
                    session_id=session_id,
                )

        return ValidationResult(
            action="create_booking_draft",
            decision=APPROVED,
            warnings=["Validation approved; execution must still pass through the controlled write tool."],
            traveler_id=traveler_id,
            session_id=session_id,
            metadata={"trip_id": trip_id, "room_type": room_type, "flight_option": normalized_flight_option or "Without Flight"},
        )

    def _validate_update_booking(self, payload: dict[str, Any], session_context: dict[str, Any]) -> ValidationResult:
        booking_id = self._value(payload, session_context, "booking_id")
        session_id = str(session_context.get("session_id") or "")
        if not booking_id:
            return ValidationResult(
                action="update_booking",
                decision=NEED_MORE_INFORMATION,
                reasons=["Booking ID is required before I can validate a booking update."],
                missing_information=["booking_id"],
                session_id=session_id,
            )

        booking = self._get_booking_by_id(booking_id)
        if booking is None:
            return ValidationResult(
                action="update_booking",
                decision=REJECTED,
                reasons=[f"Booking {booking_id} was not found."],
                session_id=session_id,
            )

        requested_fields = [field for field in ALLOWED_BOOKING_UPDATE_FIELDS if self._has_value(payload, session_context, field)]
        if not requested_fields:
            return ValidationResult(
                action="update_booking",
                decision=NEED_MORE_INFORMATION,
                reasons=["No booking update fields were provided for validation."],
                missing_information=["booking_update_fields"],
                traveler_id=str(booking.get("traveler_id") or ""),
                session_id=session_id,
            )

        requested_stage = self._value(payload, session_context, "requested_stage")
        if requested_stage and requested_stage not in VALID_BOOKING_STATUSES:
            return ValidationResult(
                action="update_booking",
                decision=REJECTED,
                reasons=[f"Unsupported booking status: {requested_stage}."],
                traveler_id=str(booking.get("traveler_id") or ""),
                session_id=session_id,
            )

        return ValidationResult(
            action="update_booking",
            decision=APPROVED,
            warnings=["Validation approved; execution must still pass through the controlled write tool."],
            traveler_id=str(booking.get("traveler_id") or ""),
            session_id=session_id,
            metadata={"booking_id": booking_id, "requested_fields": requested_fields},
        )

    def _validate_upload_passport(self, payload: dict[str, Any], session_context: dict[str, Any]) -> ValidationResult:
        traveler, _resolution = self._resolve_traveler(payload, session_context)
        session_id = str(session_context.get("session_id") or "")
        if traveler is None:
            missing = ["traveler_id_or_raw_phone"] if not self._has_identity_hint(payload, session_context) else []
            return ValidationResult(
                action="upload_passport",
                decision=NEED_MORE_INFORMATION if missing else REJECTED,
                reasons=["Passport upload validation requires an existing traveler profile."],
                missing_information=missing,
                session_id=session_id,
            )

        attachment_ref = self._value(payload, session_context, "passport_attachment_ref")
        if not attachment_ref:
            return ValidationResult(
                action="upload_passport",
                decision=NEED_MORE_INFORMATION,
                reasons=["Passport attachment reference is required before upload can be validated."],
                missing_information=["passport_attachment_ref"],
                traveler_id=str(traveler.get("traveler_id") or ""),
                session_id=session_id,
            )
        if not str(attachment_ref).strip().lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".pdf")):
            return ValidationResult(
                action="upload_passport",
                decision=REJECTED,
                reasons=["Passport attachment must be an allowed image or PDF file."],
                traveler_id=str(traveler.get("traveler_id") or ""),
                session_id=session_id,
            )
        workflow = session_context.get("workflow_policy") if isinstance(session_context.get("workflow_policy"), dict) else {}
        stage = str(session_context.get("stage") or workflow.get("state") or "").strip()
        required_step = str(workflow.get("required_step") or "").strip()
        if stage and stage != "awaiting_passport_upload" and required_step != "collect_passport_attachment":
            return ValidationResult(
                action="upload_passport",
                decision=REJECTED,
                reasons=["Passport attachment was not expected at the current conversation step."],
                traveler_id=str(traveler.get("traveler_id") or ""),
                session_id=session_id,
            )

        return ValidationResult(
            action="upload_passport",
            decision=APPROVED,
            warnings=["Validation approved; execution must still pass through the controlled write tool."],
            traveler_id=str(traveler.get("traveler_id") or ""),
            session_id=session_id,
        )

    def _validate_create_handoff(self, payload: dict[str, Any], session_context: dict[str, Any]) -> ValidationResult:
        traveler, resolution = self._resolve_traveler(payload, session_context)
        session_id = str(session_context.get("session_id") or "")
        reasons: list[str] = []
        warnings: list[str] = ["Validation approved; execution must still pass through the controlled write tool."]

        if self._as_bool(self._value(payload, session_context, "user_requested_human")):
            reasons.append("The traveler explicitly requested a human agent.")
        ai_confidence = self._as_float(self._value(payload, session_context, "ai_confidence"))
        if ai_confidence is not None and ai_confidence < HANDOFF_CONFIDENCE_THRESHOLD:
            reasons.append("AI confidence is below the safe threshold for automated handling.")
        validation_failures = self._as_int(self._value(payload, session_context, "validation_failures", "repeated_validation_failures"))
        if validation_failures is not None and validation_failures >= HANDOFF_VALIDATION_FAILURE_THRESHOLD:
            reasons.append("Repeated validation failures require human review.")
        if resolution and resolution.get("handoff_required"):
            reasons.append(f"Traveler resolution requires handoff: {resolution.get('handoff_reason') or 'manual_review'}.")
        controlled_reason = str(self._value(payload, session_context, "reason_code", "handoff_reason_code") or "").strip().lower()
        controlled_step = str(self._value(payload, session_context, "required_step") or "").strip().lower()
        controlled_state = str(self._value(payload, session_context, "state") or "").strip().lower()
        controlled_review_reasons = {
            "room_capacity",
            "capacity_review",
            "duplicate_phone_match",
            "unsupported_request",
            "policy_review",
        }
        if (
            controlled_reason in controlled_review_reasons
            or controlled_step == "create_capacity_handoff"
            or controlled_state in {"capacity_handoff_required", "duplicate_traveler_detected"}
        ):
            reasons.append("Backend workflow policy requires a controlled human review handoff.")
        traveler_status = str((traveler or {}).get("status") or "").strip().lower()
        if traveler_status in REJECTED_TRAVELER_STATUSES:
            reasons.append(f"Traveler status {traveler.get('status') or 'unknown'} requires handoff.")

        if reasons:
            return ValidationResult(
                action="create_handoff",
                decision=APPROVED,
                reasons=reasons,
                warnings=warnings,
                traveler_id=str((traveler or {}).get("traveler_id") or ""),
                session_id=session_id,
            )

        return ValidationResult(
            action="create_handoff",
            decision=REJECTED,
            reasons=["No handoff trigger was identified for this request."],
            session_id=session_id,
        )

    def _resolve_traveler(
        self,
        payload: dict[str, Any],
        session_context: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        traveler_id = self._value(payload, session_context, "traveler_id")
        raw_phone = self._value(payload, session_context, "raw_phone", "pending_raw_phone")
        country_code = self._value(payload, session_context, "country_code") or self.settings.default_country_code
        if traveler_id:
            result = self.read_only_tools.get_traveler_profile(traveler_id=traveler_id)
            traveler = result.get("traveler")
            return (traveler if isinstance(traveler, dict) and traveler else None), None
        if raw_phone:
            resolution = self.read_only_tools.search_traveler(raw_phone=raw_phone, country_code=country_code)
            traveler = resolution.get("traveler")
            return (traveler if isinstance(traveler, dict) and traveler else None), resolution
        return None, None

    def _has_identity_hint(self, payload: dict[str, Any], session_context: dict[str, Any]) -> bool:
        return bool(
            self._value(payload, session_context, "traveler_id")
            or self._value(payload, session_context, "raw_phone", "pending_raw_phone")
        )

    def _find_open_leads(self, *, traveler_id: str = "", raw_phone: str = "", country_code: str = "") -> list[dict[str, Any]]:
        lead_lookup = self.read_only_tools.lookup_lead(
            traveler_id=traveler_id,
            raw_phone=raw_phone,
            country_code=country_code,
        )
        open_leads = []
        for lead in lead_lookup.get("leads", []):
            stage = str(lead.get("lead_stage") or "").strip()
            if stage not in CLOSED_LEAD_STAGES:
                open_leads.append(lead)
        return open_leads

    def _find_duplicate_active_booking(self, *, traveler_id: str, trip_id: str) -> dict[str, Any] | None:
        result = self.read_only_tools.get_booking_status(traveler_id=traveler_id)
        for booking in result.get("bookings", []):
            if str(booking.get("trip_id") or "").strip() != trip_id:
                continue
            if str(booking.get("booking_status") or "").strip() in ACTIVE_BOOKING_STATUSES:
                return dict(booking)
        return None

    def _get_lead_by_id(self, lead_id: str) -> dict[str, Any] | None:
        result = self.read_only_tools.lookup_lead(lead_id=lead_id)
        leads = list(result.get("leads") or [])
        return dict(leads[0]) if leads else None

    def _get_booking_by_id(self, booking_id: str) -> dict[str, Any] | None:
        result = self.read_only_tools.get_booking_status(booking_id=booking_id)
        bookings = list(result.get("bookings") or [])
        return dict(bookings[0]) if bookings else None

    @staticmethod
    def _trip_has_departure_dates(trip: dict[str, Any]) -> bool:
        return bool(str(trip.get("start_date") or "").strip() and str(trip.get("end_date") or "").strip())

    @staticmethod
    def _is_international_trip(trip: dict[str, Any]) -> bool:
        value = str(trip.get("type") or trip.get("trip_type") or "").strip().lower()
        return value == "international"

    @classmethod
    def _passport_required_for_trip(cls, trip: dict[str, Any], flight_option: str = "") -> bool:
        for key in ("passport_required", "requires_passport"):
            if key in trip:
                return cls._as_bool(trip.get(key))
        if "passport_required_with_flight" in trip:
            return str(flight_option or "").strip() == "With Flight" and cls._as_bool(trip.get("passport_required_with_flight"))
        return cls._is_international_trip(trip)

    @staticmethod
    def _value(payload: dict[str, Any], session_context: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in payload and payload.get(key) not in {None, ""}:
                return payload.get(key)
            if key in session_context and session_context.get(key) not in {None, ""}:
                return session_context.get(key)
        return ""

    @staticmethod
    def _has_value(payload: dict[str, Any], session_context: dict[str, Any], key: str) -> bool:
        return ActionValidator._value(payload, session_context, key) not in {"", None}

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "y"}

    @staticmethod
    def _as_float(value: Any) -> float | None:
        if value in {"", None}:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_int(value: Any) -> int | None:
        if value in {"", None}:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _log_decision(self, result: ValidationResult, payload: dict[str, Any]) -> None:
        log_payload = {
            "timestamp": _utc_now_iso(),
            "requested_action": result.action,
            "validator_result": result.to_dict(),
            "traveler_id": result.traveler_id,
            "session_id": result.session_id,
            "warnings": list(result.warnings),
            "payload_keys": sorted(payload.keys()),
        }
        agent_logger.info("Validation decision %s", json.dumps(log_payload, ensure_ascii=False, sort_keys=True))
