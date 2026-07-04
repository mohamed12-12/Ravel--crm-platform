from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from services.ai_agent.ai_agent_app.agent.read_only_tools import ReadOnlyCRMTools
from services.ai_agent.ai_agent_app.config import Settings
from services.ai_agent.ai_agent_app.logger import agent_logger
from services.ai_agent.validation import APPROVED, NEED_MORE_INFORMATION, REJECTED, ActionValidator
from services.ai_agent.validation.validation_rules import normalize_flight_option


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class GeminiWriteToolExecutor:
    def __init__(
        self,
        *,
        settings: Settings,
        read_only_tools: ReadOnlyCRMTools | None = None,
        action_validator: ActionValidator | None = None,
    ) -> None:
        self.settings = settings
        self.read_only_tools = read_only_tools or ReadOnlyCRMTools(settings)
        self.action_validator = action_validator or ActionValidator(settings, read_only_tools=self.read_only_tools)
        self.service = self.read_only_tools.service

    def execute(
        self,
        *,
        action: str,
        payload: dict[str, Any],
        session_context: dict[str, Any],
    ) -> dict[str, Any]:
        validation = self.action_validator.validate_action(
            action=action,
            payload=payload,
            session_context=session_context,
        )
        validation_payload = validation.to_dict()
        audit: dict[str, Any] = {
            "timestamp": _utc_now_iso(),
            "session_id": str(session_context.get("session_id") or ""),
            "action": action,
            "validator_decision": validation.decision,
            "executed": False,
            "result_id": "",
            "reason": "",
            "warnings": list(validation.warnings),
        }

        if validation.decision != APPROVED:
            audit["reason"] = "; ".join(validation.reasons or validation.missing_information or ["write blocked"])
            reply = self._human_message_for_blocked_action(action, validation_payload, session_context)
            self._log_audit(audit)
            return {
                "action": action,
                "decision": validation.decision,
                "executed": False,
                "validation": validation_payload,
                "assistant_message": reply,
                "reply": reply,
                "result": None,
                "result_id": "",
                "audit": audit,
                "write_result": None,
            }

        handler = getattr(self, f"_execute_{action}", None)
        if handler is None:
            audit["reason"] = f"Unsupported controlled write action: {action}"
            self._log_audit(audit)
            raise RuntimeError(audit["reason"])

        try:
            raw_result = handler(payload, session_context, validation)
            result = self._normalize_result(action, raw_result, payload, session_context, validation)
            audit["executed"] = True
            audit["result_id"] = str(result.get("result_id") or "")
            audit["reason"] = str(result.get("assistant_message") or "").strip() or "write executed"
            self._log_audit(audit)
            result["audit"] = audit
            result["validation"] = validation_payload
            return result
        except Exception as exc:
            audit["reason"] = str(exc)
            self._log_audit(audit)
            raise

    def _execute_create_lead(
        self,
        payload: dict[str, Any],
        session_context: dict[str, Any],
        validation,
    ) -> dict[str, Any]:
        traveler = self._resolve_traveler(payload, session_context)
        customer_name = self._value(payload, session_context, "customer_name", "full_name")
        if not customer_name and traveler:
            customer_name = str(traveler.get("full_name") or "").strip()
        raw_phone = self._value(payload, session_context, "raw_phone", "pending_raw_phone")
        country_code = self._value(payload, session_context, "country_code") or self.settings.default_country_code
        trip_type = self._value(payload, session_context, "preferred_trip_type", "trip_type")
        lead_source = self._value(payload, session_context, "lead_source") or "Gemini Agent"
        channel = self._value(payload, session_context, "channel") or "web"
        priority = self._value(payload, session_context, "priority") or "Medium"
        group_size = self._as_int(self._value(payload, session_context, "group_size"), default=1) or 1
        traveler_id = str((traveler or {}).get("traveler_id") or validation.traveler_id or "").strip()
        if not raw_phone and traveler:
            raw_phone = str(
                traveler.get("raw_phone")
                or traveler.get("whatsapp_raw")
                or traveler.get("integrated_whatsapp")
                or traveler.get("normalized_whatsapp")
                or ""
            ).strip()
        traveler_status = str((traveler or {}).get("status") or "").strip()
        customer_tier = "VIP" if traveler_status.upper() == "VIP" else ("Repeat" if traveler_status.upper() == "REPEAT" else "")
        match_status = "single_match" if traveler_id else "not_found"
        interested_trip_ids = self._value(payload, session_context, "interested_trip_ids", "selected_trip_id")
        suggested_trip_ids = self._value(payload, session_context, "suggested_trip_ids", "selected_trip_id")
        notes = self._value(payload, session_context, "notes") or "Created through controlled Gemini write tool."
        flow_key = self._value(payload, session_context, "flow_key") or "gemini_write"
        current_step = self._value(payload, session_context, "current_step") or "create_lead"
        handoff_required = self._as_bool(self._value(payload, session_context, "handoff_required"))
        handoff_reason = self._value(payload, session_context, "handoff_reason")
        lead_stage = self._value(payload, session_context, "lead_stage")
        if not lead_stage:
            if traveler_status.upper() == "VIP":
                lead_stage = "VIP Priority"
            elif traveler_status.upper() == "REPEAT":
                lead_stage = "Repeat Priority"
            else:
                lead_stage = "New Lead"

        result = self.service.upsert_lead(
            customer_name=customer_name,
            raw_phone=raw_phone,
            traveler_id=traveler_id or None,
            lead_stage=lead_stage,
            lead_source=lead_source,
            channel=channel,
            preferred_trip_type=trip_type or "",
            interested_trip_ids=interested_trip_ids or "",
            suggested_trip_ids=suggested_trip_ids or "",
            priority=priority,
            follow_up_status=self._value(payload, session_context, "follow_up_status") or "",
            follow_up_due_date=self._value(payload, session_context, "follow_up_due_date") or "",
            notes=notes,
            booking_id=self._value(payload, session_context, "booking_id") or "",
            traveler_status=traveler_status,
            customer_tier=customer_tier,
            match_status=match_status,
            flow_key=flow_key,
            current_step=current_step,
            handoff_required=handoff_required,
            handoff_reason=handoff_reason,
            language=self._value(payload, session_context, "language") or str(session_context.get("language") or ""),
            country_code=country_code,
            group_size=group_size,
            force_create_new=True,
        )

        return {
            "result_id": result.get("lead_id", ""),
            "assistant_message": self._lead_message(result, customer_name),
            "lead_update": result,
            "write_result": {
                "created_traveler": None,
                "lead_update": result,
            },
            "traveler": traveler,
            "session_update": {
                "lead_status": result.get("lead_stage", ""),
                "final_result": {
                    "traveler": traveler,
                    "lead_id": result.get("lead_id", ""),
                    "handoff_id": "",
                    "handoff_required": bool(handoff_required),
                    "handoff_reason": handoff_reason or "",
                    "write_result": {
                        "created_traveler": None,
                        "lead_update": result,
                    },
                },
            },
        }

    def _execute_update_lead_stage(
        self,
        payload: dict[str, Any],
        session_context: dict[str, Any],
        validation,
    ) -> dict[str, Any]:
        lead_id = self._value(payload, session_context, "lead_id")
        requested_stage = self._value(payload, session_context, "requested_stage", "lead_stage")
        result = self.service.update_lead_stage(
            lead_id,
            requested_stage=requested_stage,
            priority=self._value(payload, session_context, "priority") or "",
            follow_up_status=self._value(payload, session_context, "follow_up_status") or "",
            follow_up_due_date=self._value(payload, session_context, "follow_up_due_date") or "",
            notes=self._value(payload, session_context, "notes") or "",
            channel=self._value(payload, session_context, "channel") or "",
            flow_key=self._value(payload, session_context, "flow_key") or "",
            current_step=self._value(payload, session_context, "current_step") or "",
        )
        traveler = self._resolve_traveler(payload, session_context)
        return {
            "result_id": result.get("lead_id", ""),
            "assistant_message": self._stage_message(result),
            "lead_update": result,
            "write_result": {
                "created_traveler": None,
                "lead_update": result,
            },
            "traveler": traveler,
            "session_update": {
                "lead_status": result.get("lead_stage", ""),
                "final_result": {
                    "traveler": traveler,
                    "lead_id": result.get("lead_id", ""),
                    "handoff_id": "",
                    "write_result": {
                        "created_traveler": None,
                        "lead_update": result,
                    },
                },
            },
        }

    def _execute_create_booking_draft(
        self,
        payload: dict[str, Any],
        session_context: dict[str, Any],
        validation,
    ) -> dict[str, Any]:
        traveler = self._resolve_traveler(payload, session_context)
        trip_id = self._value(payload, session_context, "trip_id", "selected_trip_id")
        trip = self.read_only_tools.get_trip_details(trip_id=trip_id).get("trip") if trip_id else None
        traveler_name = self._value(payload, session_context, "traveler_name")
        if not traveler_name and traveler:
            traveler_name = str(traveler.get("full_name") or "").strip()
        room_type = self._value(payload, session_context, "room_type")
        flight_option = normalize_flight_option(self._value(payload, session_context, "flight_option")) or self._value(
            payload, session_context, "flight_option"
        )
        passport_attachment_ref = self._value(payload, session_context, "passport_attachment_ref")
        passport_required = bool(trip and str(trip.get("type") or trip.get("trip_type") or "").strip().lower() == "international")
        passport_status = "provided" if passport_required and passport_attachment_ref else ("pending" if passport_required else "")
        result = self.service.create_booking_draft(
            trip_id=trip_id,
            traveler_id=str((traveler or {}).get("traveler_id") or validation.traveler_id or ""),
            traveler_name=traveler_name or str((traveler or {}).get("full_name") or "").strip() or self._value(payload, session_context, "customer_name", "full_name") or "Traveler",
            room_type=room_type,
            channel=self._value(payload, session_context, "channel") or "web",
            lead_id=self._value(payload, session_context, "lead_id") or "",
            flight_option=flight_option,
            date_option=self._value(payload, session_context, "date_option") or "",
            currency=self._value(payload, session_context, "currency") or "",
            source=self._value(payload, session_context, "source") or "Gemini Agent",
            agent_notes=self._value(payload, session_context, "booking_notes", "agent_notes") or "Created through controlled Gemini write tool.",
            passport_required=passport_required,
            passport_status=passport_status,
            group_size=self._as_int(self._value(payload, session_context, "group_size"), default=1) or 1,
        )
        return {
            "result_id": result.get("booking_id", ""),
            "assistant_message": self._booking_message(result),
            "booking_result": result,
            "write_result": result.get("write_result") or {},
            "traveler": traveler,
            "session_update": {
                "booking_result": result,
                "booking_status": result.get("booking_status", ""),
                "lead_status": (result.get("lead_update") or {}).get("lead_stage", ""),
                "handoff_state": "completed",
                "stage": "completed",
                "final_result": {
                    "traveler": traveler,
                    "booking_id": result.get("booking_id", ""),
                    "lead_id": (result.get("lead_update") or {}).get("lead_id", ""),
                    "booking_result": result,
                    "write_result": result.get("write_result") or {},
                },
            },
        }

    def _execute_create_handoff(
        self,
        payload: dict[str, Any],
        session_context: dict[str, Any],
        validation,
    ) -> dict[str, Any]:
        traveler = self._resolve_traveler(payload, session_context)
        lead_id = self._value(payload, session_context, "lead_id")
        trip_id = self._value(payload, session_context, "trip_id", "selected_trip_id")
        reason_code = self._value(payload, session_context, "reason_code") or self._derive_handoff_reason_code(validation)
        reason_text = self._value(payload, session_context, "reason_text") or self._derive_handoff_reason_text(validation)
        priority = self._value(payload, session_context, "priority") or self._default_priority_from_validation(validation)
        customer_name = self._value(payload, session_context, "customer_name", "full_name")
        if not customer_name and traveler:
            customer_name = str(traveler.get("full_name") or "").strip()
        result = self.service.create_handoff_case(
            lead_id=lead_id,
            traveler_id=str((traveler or {}).get("traveler_id") or validation.traveler_id or ""),
            trip_id=trip_id,
            flow_key=self._value(payload, session_context, "flow_key") or "gemini_write",
            reason_code=reason_code,
            reason_text=reason_text,
            priority=priority,
            channel=self._value(payload, session_context, "channel") or "web",
            customer_name=customer_name or "",
            agent_summary=self._value(payload, session_context, "agent_summary") or "Created through controlled Gemini write tool.",
            customer_summary=self._value(payload, session_context, "customer_summary") or "",
            notes=self._value(payload, session_context, "notes") or "",
            metadata={"validation": validation.to_dict(), "session_id": session_context.get("session_id", "")},
            update_lead=self._as_bool(self._value(payload, session_context, "update_lead", "handoff_required"), default=True),
        )
        return {
            "result_id": result.get("handoff_id", ""),
            "assistant_message": self._handoff_message(result),
            "handoff_case": result,
            "write_result": {"handoff_case": result},
            "traveler": traveler,
            "session_update": {
                "handoff_state": "handed_off",
                "stage": "handed_off",
                "final_result": {
                    "traveler": traveler,
                    "lead_id": lead_id or "",
                    "handoff_id": result.get("handoff_id", ""),
                    "handoff_required": True,
                    "handoff_reason": reason_code or reason_text,
                    "write_result": {"handoff_case": result},
                },
            },
        }

    def _resolve_traveler(self, payload: dict[str, Any], session_context: dict[str, Any]) -> dict[str, Any] | None:
        traveler_id = self._value(payload, session_context, "traveler_id")
        raw_phone = self._value(payload, session_context, "raw_phone", "pending_raw_phone")
        country_code = self._value(payload, session_context, "country_code") or self.settings.default_country_code
        if traveler_id:
            profile = self.read_only_tools.get_traveler_profile(traveler_id=traveler_id)
            traveler = profile.get("traveler")
            return traveler if isinstance(traveler, dict) and traveler else None
        if raw_phone:
            search = self.read_only_tools.search_traveler(raw_phone=raw_phone, country_code=country_code)
            traveler = search.get("traveler")
            return traveler if isinstance(traveler, dict) and traveler else None
        return None

    @staticmethod
    def _value(payload: dict[str, Any], session_context: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in payload and payload.get(key) not in {None, ""}:
                return payload.get(key)
            if key in session_context and session_context.get(key) not in {None, ""}:
                return session_context.get(key)
        return ""

    @staticmethod
    def _as_bool(value: Any, *, default: bool = False) -> bool:
        if value in {"", None}:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "y"}

    @staticmethod
    def _as_int(value: Any, *, default: int = 0) -> int:
        if value in {"", None}:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _result_id(result: dict[str, Any]) -> str:
        return str(result.get("result_id") or result.get("lead_id") or result.get("booking_id") or result.get("handoff_id") or "").strip()

    @staticmethod
    def _humanize_token(value: Any) -> str:
        return " ".join(str(value or "").strip().replace("_", " ").split())

    @staticmethod
    def _human_message_for_blocked_action(action: str, validation_payload: dict[str, Any], session_context: dict[str, Any]) -> str:
        decision = str(validation_payload.get("decision") or "").strip()
        reasons = validation_payload.get("reasons") or []
        missing = validation_payload.get("missing_information") or []
        language = str(session_context.get("language") or "").strip().lower()
        if decision == NEED_MORE_INFORMATION and missing:
            joined = ", ".join(GeminiWriteToolExecutor._humanize_token(item) for item in missing if item)
            if language.startswith("ar"):
                return f"قبل أن أتمكن من تنفيذ {action}، أحتاج إلى: {joined}."
            return f"Before I can execute {action}, I still need: {joined}."
        if decision == REJECTED and reasons:
            joined = " ".join(GeminiWriteToolExecutor._humanize_token(item) for item in reasons if item)
            if language.startswith("ar"):
                return f"لا يمكنني تنفيذ هذا الطلب الآن. {joined}"
            return f"I can't execute this request right now. {joined}"
        if language.startswith("ar"):
            return "لا يمكنني تنفيذ هذا الطلب الآن."
        return "I can't execute this request right now."

    @staticmethod
    def _lead_message(result: dict[str, Any], customer_name: str) -> str:
        lead_id = str(result.get("lead_id") or "").strip()
        stage = str(result.get("lead_stage") or "").strip()
        name = customer_name.strip() or "the customer"
        return f"Lead {lead_id} created for {name}. Current stage: {stage}."

    @staticmethod
    def _stage_message(result: dict[str, Any]) -> str:
        lead_id = str(result.get("lead_id") or "").strip()
        stage = str(result.get("lead_stage") or "").strip()
        return f"Lead {lead_id} moved to {stage}."

    @staticmethod
    def _booking_message(result: dict[str, Any]) -> str:
        booking_id = str(result.get("booking_id") or "").strip()
        trip_name = str(result.get("trip_name") or "").strip()
        booking_status = str(result.get("booking_status") or "").strip() or "Draft"
        return f"Booking draft {booking_id} created for {trip_name}. Status: {booking_status}."

    @staticmethod
    def _handoff_message(result: dict[str, Any]) -> str:
        handoff_id = str(result.get("handoff_id") or "").strip()
        reason = str(result.get("reason_text") or result.get("reason_code") or "").strip()
        return f"Handoff case {handoff_id} created. Automation is now stopped for this session. Reason: {reason}."

    @staticmethod
    def _default_priority_from_validation(validation) -> str:
        reasons = " ".join(getattr(validation, "reasons", []) or []).lower()
        if "blocked" in reasons or "blacklisted" in reasons:
            return "Critical"
        return "High"

    @staticmethod
    def _derive_handoff_reason_code(validation) -> str:
        reasons = [str(reason or "").lower() for reason in getattr(validation, "reasons", []) or []]
        combined = " ".join(reasons)
        if "blacklisted" in combined:
            return "blacklisted_customer"
        if "blocked" in combined:
            return "blocked_traveler"
        if "confidence" in combined:
            return "low_confidence"
        if "validation" in combined:
            return "validation_failure"
        return "manual_handoff"

    @staticmethod
    def _derive_handoff_reason_text(validation) -> str:
        reasons = [str(reason or "").strip() for reason in getattr(validation, "reasons", []) or [] if str(reason or "").strip()]
        if reasons:
            return " ".join(reasons)
        return "Manual handoff requested."

    @staticmethod
    def _normalize_result(
        action: str,
        raw_result: dict[str, Any],
        payload: dict[str, Any],
        session_context: dict[str, Any],
        validation,
    ) -> dict[str, Any]:
        result = dict(raw_result or {})
        result.setdefault("action", action)
        result.setdefault("decision", validation.decision)
        result.setdefault("executed", True)
        result.setdefault("result_id", GeminiWriteToolExecutor._result_id(result))
        if "assistant_message" not in result:
            result["assistant_message"] = ""
        if "reply" not in result:
            result["reply"] = result["assistant_message"]
        result.setdefault("validation", validation.to_dict())
        return result

    @staticmethod
    def _log_audit(audit: dict[str, Any]) -> None:
        agent_logger.info("Write audit %s", json.dumps(audit, ensure_ascii=False, sort_keys=True))
