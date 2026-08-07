from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from services.ai_agent.ai_agent_app.agent.crm_api_client import CRMApiError
from services.ai_agent.ai_agent_app.agent.read_only_tools import ReadOnlyCRMTools
from services.ai_agent.ai_agent_app.config import Settings
from services.ai_agent.ai_agent_app.logger import agent_logger
from services.ai_agent.validation import APPROVED, NEED_MORE_INFORMATION, REJECTED, ActionValidator
from services.ai_agent.validation.validation_rules import normalize_flight_option
from services.ai_agent.ai_agent_app.agent.write_response_gating import customer_message_from_write_result, gate_customer_write_reply


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
        access_mode = str(getattr(settings, "crm_access_mode", "shared_service") or "shared_service").strip().lower()
        if access_mode == "api" and self.service is None and getattr(self.read_only_tools, "api_client", None) is None:
            # In api mode self.service is deliberately None (reads/writes go
            # over HTTP), so a missing api_client here means every handler
            # that touches self.service directly -- or any future one added
            # the same way -- fails with an AttributeError on every single
            # call instead of a loud, obvious startup error. Only gated on
            # api mode: plenty of read-only test doubles legitimately have no
            # write path at all and are not exercising this configuration.
            raise RuntimeError(
                "Write executor is configured for CRM_ACCESS_MODE=api but has neither "
                "self.service nor an api_client -- every write action would silently fail."
            )

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

        if action == "create_booking_draft" and "booking_confirmed" in session_context and not self._as_bool(
            session_context.get("booking_confirmed"), default=False
        ):
            reply = "Before I create the booking draft, please confirm the final details."
            contract = self._write_result_contract(
                status="blocked",
                executed=False,
                reused=False,
                record_type="booking",
                customer_confirmation_allowed=False,
                error_code="confirmation_required",
                safe_customer_message_key="booking.confirmation_required",
                audit={"session_id": str(session_context.get("session_id") or "")},
            )
            audit["reason"] = "confirmation_required"
            self._log_audit(audit)
            return {
                "action": action,
                "decision": REJECTED,
                "executed": False,
                "validation": validation_payload,
                "assistant_message": reply,
                "reply": reply,
                "result": None,
                "result_id": "",
                "audit": audit,
                "write_result": {"write_result_contract": contract},
                "write_result_contract": contract,
            }

        if validation.decision != APPROVED:
            duplicate_result = self._duplicate_write_result_if_available(action, validation_payload, session_context, audit)
            if duplicate_result is not None:
                return duplicate_result
            audit["reason"] = "; ".join(validation.reasons or validation.missing_information or ["write blocked"])
            reply = self._human_message_for_blocked_action(action, validation_payload, session_context)
            contract = self._write_result_contract(
                status="blocked",
                executed=False,
                reused=False,
                record_type=action.replace("create_", "").replace("_draft", ""),
                customer_confirmation_allowed=False,
                error_code=str(validation.decision or "blocked"),
                safe_customer_message_key="write.blocked",
                audit={"session_id": str(session_context.get("session_id") or "")},
            )
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
                "write_result": {"write_result_contract": contract},
                "write_result_contract": contract,
            }

        if self.read_only_tools.api_client is not None:
            try:
                result = self.read_only_tools.api_client.write(action, payload, session_context)
            except CRMApiError as exc:
                agent_logger.error(
                    "CRM API write failed action=%s session=%s error=%s",
                    action, session_context.get("session_id", ""), exc,
                )
                audit["reason"] = self._safe_error_code_for_exception(exc)
                self._log_audit(audit)
                return self._failed_write_result(
                    action=action,
                    exc=exc,
                    session_context=session_context,
                    validation_payload=validation_payload,
                    audit=audit,
                )
            audit["executed"] = bool(result.get("executed", True))
            audit["result_id"] = str(result.get("result_id") or "")
            audit["reason"] = str(result.get("assistant_message") or "CRM API write executed")
            result.setdefault("audit", audit)
            result.setdefault("validation", validation_payload)
            self._log_audit(audit)
            return result

        handler = getattr(self, f"_execute_{action}", None)
        if handler is None:
            audit["reason"] = f"Unsupported controlled write action: {action}"
            self._log_audit(audit)
            raise RuntimeError(audit["reason"])

        try:
            raw_result = handler(payload, session_context, validation)
            result = self._normalize_result(action, raw_result, payload, session_context, validation)
            audit["executed"] = bool(result.get("executed", True))
            audit["result_id"] = str(result.get("result_id") or "")
            audit["reason"] = str(result.get("assistant_message") or "").strip() or "write executed"
            self._log_audit(audit)
            result["audit"] = audit
            result["validation"] = validation_payload
            return result
        except Exception as exc:
            audit["reason"] = self._safe_error_code_for_exception(exc)
            self._log_audit(audit)
            return self._failed_write_result(
                action=action,
                exc=exc,
                session_context=session_context,
                validation_payload=validation_payload,
                audit=audit,
            )

    def _execute_create_traveler(
        self,
        payload: dict[str, Any],
        session_context: dict[str, Any],
        validation,
    ) -> dict[str, Any]:
        return self._create_with_verified_retry(
            perform=lambda: self._perform_create_traveler(payload, session_context, validation),
            verify=lambda record_id, result: self._verify_traveler_record(record_id),
            record_type="traveler",
            session_context=session_context,
        )

    def _perform_create_traveler(
        self,
        payload: dict[str, Any],
        session_context: dict[str, Any],
        validation,
    ) -> dict[str, Any]:
        """Create the Traveler record for a phone number with no CRM match.

        `create_lead` already creates a traveler as a side effect in both service
        implementations, but the conversation needs a guaranteed traveler_id even
        when that side effect did not happen (for example when the lead write was
        de-duplicated against an older lead that has no traveler linked). The
        validator rejects this action whenever a traveler already matches the
        number, so it can never fork an identity.
        """
        customer_name = self._value(payload, session_context, "customer_name", "full_name")
        raw_phone = self._value(payload, session_context, "raw_phone", "pending_raw_phone")
        country_code = self._value(payload, session_context, "country_code") or self.settings.default_country_code
        if not customer_name or not raw_phone:
            raise RuntimeError("Traveler creation requires both a full name and a WhatsApp number.")
        traveler = self.service.create_traveler(
            full_name=customer_name,
            raw_phone=raw_phone,
            birthday=self._value(payload, session_context, "birthday") or "",
            gender=self._value(payload, session_context, "gender") or "",
            nationality=self._value(payload, session_context, "nationality") or "",
            preferred_currency=self._value(payload, session_context, "preferred_currency", "currency") or "",
            lead_source=self._value(payload, session_context, "lead_source") or "Gemini Agent",
            agent_notes=self._value(payload, session_context, "notes") or "Created through the controlled traveler write path.",
            country_code=country_code,
        )
        traveler = dict(traveler or {})
        traveler_id = str(traveler.get("traveler_id") or "").strip()
        if not traveler_id:
            raise RuntimeError("Traveler creation did not return a traveler id.")
        traveler.setdefault("status", "Active")
        contract = self._write_result_contract(
            status="success",
            executed=True,
            reused=False,
            record_type="traveler",
            record_id=traveler_id,
            customer_confirmation_allowed=False,
            audit={"session_id": str(session_context.get("session_id") or ""), "action": "create_traveler"},
        )
        return {
            "result_id": traveler_id,
            "assistant_message": "",
            "traveler": traveler,
            "write_result": {"created_traveler": traveler, "write_result_contract": contract},
            "write_result_contract": contract,
            "session_update": {
                "final_result": {
                    "traveler": traveler,
                    "write_result": {"created_traveler": traveler},
                }
            },
        }

    def _execute_create_lead(
        self,
        payload: dict[str, Any],
        session_context: dict[str, Any],
        validation,
    ) -> dict[str, Any]:
        return self._create_with_verified_retry(
            perform=lambda: self._perform_create_lead(payload, session_context, validation),
            verify=lambda record_id, result: self._verify_lead_record(record_id),
            record_type="lead",
            session_context=session_context,
        )

    def _perform_create_lead(
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
        notes = self._value(payload, session_context, "notes") or "Created through controlled Gemini write tool."
        flow_key = self._value(payload, session_context, "flow_key") or "gemini_write"
        current_step = self._value(payload, session_context, "current_step") or "create_lead"
        language = self._value(payload, session_context, "language") or str(session_context.get("language") or "")
        preferred_trip_id = self._value(payload, session_context, "preferred_trip_id", "selected_trip_id", "trip_id")

        record_agent_outcome = getattr(self.service, "record_agent_outcome", None)
        if callable(record_agent_outcome):
            result = record_agent_outcome(
                full_name=customer_name or "",
                raw_phone=raw_phone or "",
                country_code=country_code,
                trip_type=trip_type or None,
                channel=channel,
                source=lead_source,
                agent_notes=notes,
                birthday=self._value(payload, session_context, "birthday") or "",
                gender=self._value(payload, session_context, "gender") or "",
                nationality=self._value(payload, session_context, "nationality") or "",
                preferred_currency=self._value(payload, session_context, "preferred_currency", "currency") or "",
                preferred_trip_id=preferred_trip_id or "",
                language=language,
                force_create_new_lead=True,
                group_size=group_size,
                session_id=str(session_context.get("session_id") or ""),
            )
            write_result = result.get("write_result") if isinstance(result.get("write_result"), dict) else {}
            lead_update = write_result.get("lead_update") if isinstance(write_result.get("lead_update"), dict) else {}
            created_traveler = write_result.get("created_traveler") if isinstance(write_result.get("created_traveler"), dict) else None
            final_result = {
                "traveler": result.get("traveler") if isinstance(result.get("traveler"), dict) else traveler,
                "lead_id": lead_update.get("lead_id", ""),
                "handoff_id": "",
                "handoff_required": bool(result.get("handoff_required")),
                "handoff_reason": str(result.get("handoff_reason") or ""),
                "write_result": {
                    "created_traveler": created_traveler,
                    "lead_update": lead_update,
                },
            }
            lead_id = str(lead_update.get("lead_id") or "")
            contract = self._write_result_contract(
                status="success" if lead_id else "failed",
                executed=bool(lead_id),
                reused=False,
                record_type="lead",
                record_id=lead_id,
                customer_confirmation_allowed=False,
                error_code="" if lead_id else "write_failed",
                safe_customer_message_key="lead.created" if lead_id else "lead.write_failed",
                audit={"session_id": str(session_context.get("session_id") or "")},
            )
            return {
                "result_id": lead_id,
                "assistant_message": self._lead_message(lead_update, customer_name, language),
                "lead_update": lead_update,
                "write_result": {
                    "created_traveler": created_traveler,
                    "lead_update": lead_update,
                    "write_result_contract": contract,
                },
                "write_result_contract": contract,
                "traveler": result.get("traveler") if isinstance(result.get("traveler"), dict) else traveler,
                "session_update": {
                    "lead_status": lead_update.get("lead_stage", ""),
                    "final_result": final_result,
                },
            }

        created_traveler: dict[str, Any] | None = None
        if not traveler_id and customer_name and raw_phone:
            # No Traveler record exists for this phone number. The
            # record_agent_outcome path above already creates one for
            # shared_service/dev mode; this is the equivalent for the
            # Postgres-native service, which has no combined
            # "qualify + create traveler + create lead" helper. Without this,
            # every new-traveler Lead was saved with traveler_id=None, and
            # find_traveler_by_phone would never match this customer again in
            # a later session -- they would be re-classified as brand new
            # every time.
            created_traveler = self.service.create_traveler(
                full_name=customer_name,
                raw_phone=raw_phone,
                birthday=self._value(payload, session_context, "birthday") or "",
                gender=self._value(payload, session_context, "gender") or "",
                nationality=self._value(payload, session_context, "nationality") or "",
                preferred_currency=self._value(payload, session_context, "preferred_currency", "currency") or "",
                lead_source=lead_source,
                agent_notes=notes,
                country_code=country_code,
            )
            traveler_id = str(created_traveler.get("traveler_id") or "").strip()
            traveler = created_traveler

        traveler_status = str((traveler or {}).get("status") or "").strip()
        customer_tier = "VIP" if traveler_status.upper() == "VIP" else ("Repeat" if traveler_status.upper() == "REPEAT" else "")
        match_status = "single_match" if traveler_id else "not_found"
        interested_trip_ids = self._value(payload, session_context, "interested_trip_ids", "selected_trip_id")
        suggested_trip_ids = self._value(payload, session_context, "suggested_trip_ids", "selected_trip_id")
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
            priority=self._value(payload, session_context, "priority") or "Medium",
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
            language=language,
            country_code=country_code,
            group_size=group_size,
            force_create_new=False,
            session_id=str(session_context.get("session_id") or ""),
        )

        lead_id = str(result.get("lead_id") or "")
        contract = self._write_result_contract(
            status="success" if lead_id else "failed",
            executed=bool(lead_id),
            reused=False,
            record_type="lead",
            record_id=lead_id,
            customer_confirmation_allowed=False,
            error_code="" if lead_id else "write_failed",
            safe_customer_message_key="lead.created" if lead_id else "lead.write_failed",
            audit={"session_id": str(session_context.get("session_id") or "")},
        )
        return {
            "result_id": lead_id,
            "assistant_message": self._lead_message(result, customer_name, language),
            "lead_update": result,
            "write_result": {
                "created_traveler": created_traveler,
                "lead_update": result,
                "write_result_contract": contract,
            },
            "write_result_contract": contract,
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
                        "created_traveler": created_traveler,
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
        requested_stage = self._value(payload, session_context, "requested_stage", "lead_stage")
        return self._create_with_verified_retry(
            perform=lambda: self._perform_update_lead_stage(payload, session_context, validation),
            verify=lambda record_id, result: self._verify_lead_stage_record(record_id, requested_stage),
            record_type="lead",
            session_context=session_context,
        )

    def _perform_update_lead_stage(
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
        lead_id = str(result.get("lead_id") or "").strip()
        # UnifiedCRMService.update_lead_stage never produced a
        # write_result_contract at all (PostgresAgentBridgeService.
        # update_lead_stage does), so this outcome was never comparable
        # across backends -- build it explicitly here, backend-agnostic,
        # using the same shared write_result_contract() both backends alias.
        contract = result.get("write_result_contract") if isinstance(result.get("write_result_contract"), dict) else None
        if not contract:
            contract = self._write_result_contract(
                status="success" if lead_id else "failed",
                executed=bool(lead_id),
                reused=False,
                record_type="lead",
                record_id=lead_id,
                customer_confirmation_allowed=False,
                audit={"session_id": str(session_context.get("session_id") or ""), "action": "update_lead_stage"},
            )
        return {
            "result_id": lead_id,
            "assistant_message": self._stage_message(result),
            "lead_update": result,
            "write_result": {
                # A stage update never creates a traveler; this key exists so the
                # write_result shape stays identical across lead writes.
                "created_traveler": None,
                "lead_update": result,
                "write_result_contract": contract,
            },
            "write_result_contract": contract,
            "traveler": traveler,
            "session_update": {
                "lead_status": result.get("lead_stage", ""),
                "final_result": {
                    "traveler": traveler,
                    "lead_id": lead_id,
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
        return self._create_with_verified_retry(
            perform=lambda: self._perform_create_booking_draft(payload, session_context, validation),
            verify=lambda record_id, result: self._verify_booking_record(record_id),
            record_type="booking",
            session_context=session_context,
        )

    def _perform_create_booking_draft(
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
        room_group = self._value(payload, session_context, "room_group")
        room_requirements = payload.get("room_requirements")
        if room_requirements is None:
            room_requirements = session_context.get("room_requirements")
        boys_rooms_requested = self._value(payload, session_context, "boys_rooms_requested")
        girls_rooms_requested = self._value(payload, session_context, "girls_rooms_requested")
        flight_option = normalize_flight_option(self._value(payload, session_context, "flight_option")) or self._value(
            payload, session_context, "flight_option"
        )
        passport_attachment_ref = self._value(payload, session_context, "passport_attachment_ref")
        passport_required = self._passport_required_for_trip(
            trip if isinstance(trip, dict) else {},
            flight_option=flight_option,
        )
        passport_status = "uploaded" if passport_required and passport_attachment_ref else ("pending" if passport_required else "")
        lead_id = self._ensure_booking_lead(
            payload=payload,
            session_context=session_context,
            validation=validation,
            traveler=traveler,
            trip=trip if isinstance(trip, dict) else {},
            trip_id=trip_id,
        )
        result = self.service.create_booking_draft(
            trip_id=trip_id,
            traveler_id=str((traveler or {}).get("traveler_id") or validation.traveler_id or ""),
            traveler_name=traveler_name or str((traveler or {}).get("full_name") or "").strip() or self._value(payload, session_context, "customer_name", "full_name") or "Traveler",
            room_type=room_type,
            room_group=room_group,
            boys_rooms_requested=boys_rooms_requested,
            girls_rooms_requested=girls_rooms_requested,
            room_requirements=room_requirements,
            channel=self._value(payload, session_context, "channel") or "web",
            lead_id=lead_id,
            flight_option=flight_option,
            date_option=self._value(payload, session_context, "date_option") or "",
            currency=self._value(payload, session_context, "currency") or "",
            source=self._value(payload, session_context, "source") or "Gemini Agent",
            agent_notes=self._value(payload, session_context, "booking_notes", "agent_notes") or "Created through controlled Gemini write tool.",
            passport_required=passport_required,
            passport_status=passport_status,
            group_size=self._as_int(self._value(payload, session_context, "group_size"), default=1) or 1,
            session_id=str(session_context.get("session_id") or ""),
            require_explicit_confirmation="booking_confirmed" in session_context,
            customer_confirmed=session_context.get("booking_confirmed", True),
        )
        contract = result.get("write_result_contract") if isinstance(result.get("write_result_contract"), dict) else {}
        executed = bool(contract.get("executed", True))
        return {
            "result_id": result.get("booking_id", ""),
            "assistant_message": self._booking_message(result, str(session_context.get("language") or "en")),
            "booking_result": result,
            "executed": executed,
            "write_result": result.get("write_result") or {},
            "write_result_contract": contract,
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

    def _ensure_booking_lead(
        self,
        *,
        payload: dict[str, Any],
        session_context: dict[str, Any],
        validation,
        traveler: dict[str, Any] | None,
        trip: dict[str, Any],
        trip_id: str,
    ) -> str:
        traveler_id = str((traveler or {}).get("traveler_id") or validation.traveler_id or "").strip()
        requested_lead_id = str(self._value(payload, session_context, "lead_id") or "").strip()
        if requested_lead_id:
            lead_result = self.read_only_tools.lookup_lead(lead_id=requested_lead_id)
            leads = lead_result.get("leads") if isinstance(lead_result, dict) else []
            for lead in leads or []:
                if str(lead.get("lead_id") or "").strip() != requested_lead_id:
                    continue
                linked_traveler_id = str(lead.get("traveler_id") or "").strip()
                if not traveler_id or not linked_traveler_id or linked_traveler_id == traveler_id:
                    return requested_lead_id

        lead_payload = dict(payload)
        lead_payload.pop("lead_id", None)
        lead_payload.update(
            {
                "customer_name": str((traveler or {}).get("full_name") or self._value(payload, session_context, "customer_name", "full_name") or "Traveler").strip(),
                "raw_phone": str(
                    (traveler or {}).get("raw_phone")
                    or (traveler or {}).get("whatsapp_raw")
                    or self._value(payload, session_context, "raw_phone", "pending_raw_phone")
                    or ""
                ).strip(),
                "preferred_trip_type": str(trip.get("trip_type") or trip.get("type") or self._value(payload, session_context, "trip_type") or "").strip(),
                "preferred_trip_id": trip_id,
                "lead_source": self._value(payload, session_context, "source") or "Gemini Agent",
                "channel": self._value(payload, session_context, "channel") or "web",
                "current_step": "booking_draft",
                "notes": self._value(payload, session_context, "booking_notes", "agent_notes") or "Lead created for an AI-assisted booking draft.",
            }
        )
        lead_result = self._execute_create_lead(lead_payload, session_context, validation)
        lead_update = lead_result.get("lead_update") if isinstance(lead_result.get("lead_update"), dict) else {}
        lead_id = str(lead_update.get("lead_id") or lead_result.get("result_id") or "").strip()
        if not lead_id:
            raise RuntimeError("Booking draft was not created because a CRM lead could not be linked.")
        return lead_id

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
            deduplicate_open=self._as_bool(self._value(payload, session_context, "deduplicate_open"), default=True),
            session_id=str(session_context.get("session_id") or ""),
        )
        # create_handoff_case sets a "reused" contract (executed=False) when
        # deduplicate_open finds an existing open handoff instead of creating
        # a new one. Without surfacing that here, _normalize_result's blanket
        # executed=True default (see below) hides the distinction, and any
        # caller checking the top-level executed flag can never tell a
        # deduplicated handoff apart from one that actually failed to write.
        contract = result.get("write_result_contract") if isinstance(result.get("write_result_contract"), dict) else {}
        executed = bool(contract.get("executed", True))
        return {
            "result_id": result.get("handoff_id", ""),
            "assistant_message": self._handoff_message(result, str(session_context.get("language") or "en")),
            "handoff_case": result,
            "executed": executed,
            "write_result": {"handoff_case": result},
            "write_result_contract": contract,
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

    def _failed_write_result(
        self,
        *,
        action: str,
        exc: Exception,
        session_context: dict[str, Any],
        validation_payload: dict[str, Any],
        audit: dict[str, Any],
    ) -> dict[str, Any]:
        record_type = self._record_type_for_action(action)
        language = str(session_context.get("language") or "en")
        error_code = self._safe_error_code_for_exception(exc)
        message_key = f"{record_type}.{error_code}" if record_type != "write" else "write.failed"
        contract = self._write_result_contract(
            status="failed",
            executed=False,
            reused=False,
            record_type=record_type,
            record_id="",
            customer_confirmation_allowed=False,
            error_code=error_code,
            safe_customer_message_key=message_key,
            audit={"session_id": str(session_context.get("session_id") or ""), "action": action},
        )
        reply = self._safe_failed_write_message(record_type, error_code, language)
        return {
            "action": action,
            "decision": validation_payload.get("decision") or APPROVED,
            "executed": False,
            "validation": validation_payload,
            "assistant_message": reply,
            "reply": reply,
            "result": None,
            "result_id": "",
            "audit": audit,
            "write_result": {"write_result_contract": contract},
            "write_result_contract": contract,
        }

    @staticmethod
    def _record_type_for_action(action: str) -> str:
        normalized = str(action or "").strip().lower()
        if "booking" in normalized:
            return "booking"
        if "handoff" in normalized:
            return "handoff"
        if "lead" in normalized:
            return "lead"
        if "traveler" in normalized:
            return "traveler"
        if "passport" in normalized or "document" in normalized:
            return "document"
        return "write"

    @staticmethod
    def _safe_error_code_for_exception(exc: Exception) -> str:
        message = str(exc or "").strip().lower()
        if "capacity" in message or "remaining draftable" in message or "no remaining" in message:
            return "capacity_unavailable"
        if "not found" in message:
            return "not_found"
        if "could not be linked" in message or "lead" in message and "could not" in message:
            return "lead_link_failed"
        return "write_failed"

    @staticmethod
    def _safe_failed_write_message(record_type: str, error_code: str, language: str) -> str:
        arabic = str(language or "").strip().lower().startswith("ar")
        if error_code == "not_found":
            # This is a distinct failure from a generic write error: the id
            # we tried to update (e.g. update_lead_stage given a lead_id that
            # doesn't exist for this session) simply doesn't exist, so "try
            # again" is misleading -- retrying with the same id fails the
            # same way every time.
            if record_type == "lead":
                return "لم أجد طلبك السابق لتحديثه. من فضلك أرسل رقم واتسابك مرة أخرى حتى أتحقق من بياناتك." if arabic else "I couldn't find your previous request to update. Please resend your WhatsApp number so I can check your details again."
            if record_type == "handoff":
                return "لم أجد طلب التواصل مع موظف لتحديثه. من فضلك أخبرني إذا كنت لا تزال بحاجة لمساعدة موظف." if arabic else "I couldn't find that human-support request to update. Let me know if you still need to reach a team member."
            if record_type == "booking":
                return "لم أجد طلب الحجز لتحديثه. من فضلك أرسل رقم الحجز أو ابدأ طلبًا جديدًا." if arabic else "I couldn't find that booking to update. Please share the booking reference, or we can start a new request."
        if record_type == "booking" and error_code == "capacity_unavailable":
            if arabic:
                return "لم أستطع إنشاء طلب الحجز لهذا الخيار لأن التوافر تغير. من فضلك اختر خيار غرفة آخر، أو يمكنني توصيلك بموظف بشري."
            return "I could not create the booking request for that option because availability changed. Please choose another room option, or I can connect you with a human agent."
        if record_type == "booking":
            if arabic:
                return "لا أقدر أسجل طلب الحجز الآن. من فضلك راجع التفاصيل أو أكدها مرة أخرى."
            return "I could not create the booking request yet. Please review the details or try again."
        if record_type == "handoff":
            if arabic:
                return "لم أتمكن من تسجيل طلب التواصل مع موظف الآن. من فضلك حاول مرة أخرى أو تواصل معنا مباشرة."
            return "I could not submit the human handoff request right now. Please try again or contact us directly."
        if record_type == "lead":
            if arabic:
                return "لم أتمكن من تسجيل طلبك الآن. من فضلك حاول مرة أخرى."
            return "I could not save your request right now. Please try again."
        if arabic:
            return "لم أتمكن من إكمال الطلب الآن. من فضلك حاول مرة أخرى."
        return "I could not complete the request right now. Please try again."

    def _write_result_contract(
        self,
        *,
        status: str,
        executed: bool,
        reused: bool,
        record_type: str,
        record_id: str = "",
        idempotency_key: str = "",
        customer_confirmation_allowed: bool = False,
        error_code: str = "",
        safe_customer_message_key: str = "",
        audit: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        factory = getattr(self.service, "write_result_contract", None)
        if callable(factory):
            return factory(
                status=status,
                executed=executed,
                reused=reused,
                record_type=record_type,
                record_id=record_id,
                idempotency_key=idempotency_key,
                customer_confirmation_allowed=customer_confirmation_allowed,
                error_code=error_code,
                safe_customer_message_key=safe_customer_message_key,
                audit=audit,
            )
        return {
            "status": status,
            "executed": bool(executed),
            "reused": bool(reused),
            "record_type": record_type,
            "record_id": str(record_id or ""),
            "idempotency_key": str(idempotency_key or ""),
            "customer_confirmation_allowed": bool(customer_confirmation_allowed),
            "error_code": str(error_code or ""),
            "safe_customer_message_key": str(safe_customer_message_key or ""),
            "audit": audit or {},
        }

    def _duplicate_booking_result_if_available(
        self,
        action: str,
        validation_payload: dict[str, Any],
        session_context: dict[str, Any],
        audit: dict[str, Any],
    ) -> dict[str, Any] | None:
        if action != "create_booking_draft":
            return None
        reasons = " ".join(str(item or "") for item in validation_payload.get("reasons") or []).lower()
        warnings = [str(item or "") for item in validation_payload.get("warnings") or []]
        if "active booking already exists" not in reasons:
            return None
        booking_id = ""
        for warning in warnings:
            if "Existing booking ID:" in warning:
                booking_id = warning.split("Existing booking ID:", 1)[1].strip()
                break
        if not booking_id:
            return None
        contract = self._write_result_contract(
            status="duplicate",
            executed=False,
            reused=True,
            record_type="booking",
            record_id=booking_id,
            customer_confirmation_allowed=True,
            safe_customer_message_key="booking.duplicate_active",
            audit={"session_id": str(session_context.get("session_id") or "")},
        )
        reply = f"Booking request {booking_id} is already recorded."
        audit["executed"] = False
        audit["result_id"] = booking_id
        audit["reason"] = "duplicate_active_booking_reused"
        self._log_audit(audit)
        return {
            "action": action,
            "decision": validation_payload.get("decision") or REJECTED,
            "executed": False,
            "validation": validation_payload,
            "assistant_message": reply,
            "reply": reply,
            "result": None,
            "result_id": booking_id,
            "audit": audit,
            "write_result": {"booking_draft": {"booking_id": booking_id}, "write_result_contract": contract},
            "write_result_contract": contract,
        }

    def _duplicate_lead_result_if_available(
        self,
        action: str,
        validation_payload: dict[str, Any],
        session_context: dict[str, Any],
        audit: dict[str, Any],
    ) -> dict[str, Any] | None:
        if action != "create_lead":
            return None
        reasons = " ".join(str(item or "") for item in validation_payload.get("reasons") or []).lower()
        warnings = [str(item or "") for item in validation_payload.get("warnings") or []]
        if "open lead already exists" not in reasons:
            return None
        lead_id = self._extract_existing_id_from_warnings(warnings, "Existing lead IDs:")
        if not lead_id:
            return None
        lead_update: dict[str, Any] = {"lead_id": lead_id}
        try:
            lead_lookup = self.read_only_tools.lookup_lead(lead_id=lead_id)
            leads = lead_lookup.get("leads") if isinstance(lead_lookup, dict) else []
            for lead in leads or []:
                if str(lead.get("lead_id") or "").strip() == lead_id:
                    lead_update = dict(lead)
                    break
        except Exception:
            lead_update = {"lead_id": lead_id}
        idempotency_key = str(lead_update.get("idempotency_key") or "").strip()
        contract = self._write_result_contract(
            status="duplicate",
            executed=False,
            reused=True,
            record_type="lead",
            record_id=lead_id,
            idempotency_key=idempotency_key,
            customer_confirmation_allowed=True,
            safe_customer_message_key="lead.duplicate_open",
            audit={"session_id": str(session_context.get("session_id") or "")},
        )
        lead_update["write_result_contract"] = contract
        reply = customer_message_from_write_result(
            {"lead_update": lead_update, "write_result_contract": contract},
            "lead",
            str(session_context.get("language") or "en"),
        )
        audit["executed"] = False
        audit["result_id"] = lead_id
        audit["reason"] = "duplicate_open_lead_reused"
        self._log_audit(audit)
        return {
            "action": action,
            "decision": validation_payload.get("decision") or REJECTED,
            "executed": False,
            "validation": validation_payload,
            "assistant_message": reply,
            "reply": reply,
            "result": None,
            "result_id": lead_id,
            "lead_update": lead_update,
            "audit": audit,
            "write_result": {"lead_update": lead_update, "write_result_contract": contract},
            "write_result_contract": contract,
            "session_update": {
                "lead_status": lead_update.get("lead_stage", ""),
                "final_result": {
                    "lead_id": lead_id,
                    "write_result": {"lead_update": lead_update, "write_result_contract": contract},
                },
            },
        }

    def _duplicate_write_result_if_available(
        self,
        action: str,
        validation_payload: dict[str, Any],
        session_context: dict[str, Any],
        audit: dict[str, Any],
    ) -> dict[str, Any] | None:
        return self._duplicate_booking_result_if_available(
            action,
            validation_payload,
            session_context,
            audit,
        ) or self._duplicate_lead_result_if_available(
            action,
            validation_payload,
            session_context,
            audit,
        )

    @staticmethod
    def _extract_existing_id_from_warnings(warnings: list[str], prefix: str) -> str:
        for warning in warnings:
            if prefix not in warning:
                continue
            raw_ids = warning.split(prefix, 1)[1].strip()
            for candidate in raw_ids.split(","):
                candidate = candidate.strip()
                if candidate:
                    return candidate
        return ""

    def _create_with_verified_retry(
        self,
        *,
        perform: Callable[[], dict[str, Any]],
        verify: Callable[[str, dict[str, Any]], bool],
        record_type: str,
        session_context: dict[str, Any],
        max_attempts: int = 2,
    ) -> dict[str, Any]:
        """Run a create call, confirm it with a DB read-back, retry once on failure.

        A create call's own return value is not proof the row exists -- the
        traveler-page dead-end this fixes was exactly a create call reporting an
        id that never actually landed in CRM. Only a fresh read by that id counts
        as verified, and only a verified result is allowed to reach the customer
        as a success message.
        """
        session_id = str(session_context.get("session_id") or "")
        last_error: Exception | None = None
        last_record_id = ""
        for attempt in range(1, max_attempts + 1):
            try:
                result = perform()
            except Exception as exc:
                last_error = exc
                agent_logger.warning(
                    "%s create attempt %s raised session=%s error=%s",
                    record_type, attempt, session_id, exc,
                )
                continue
            record_id = str((result or {}).get("result_id") or "").strip()
            last_record_id = record_id
            if record_id and verify(record_id, result):
                return result
            last_error = None
            agent_logger.warning(
                "%s create attempt %s did not verify session=%s record_id=%s",
                record_type, attempt, session_id, record_id,
            )
        if last_error is not None:
            raise last_error
        raise RuntimeError(
            f"{record_type} could not be verified after {max_attempts} attempts (last_result_id={last_record_id!r})"
        )

    def _verify_traveler_record(self, traveler_id: str) -> bool:
        if not traveler_id:
            return False
        try:
            profile = self.read_only_tools.get_traveler_profile(traveler_id=traveler_id)
        except Exception:
            return False
        traveler = profile.get("traveler") if isinstance(profile, dict) else None
        return isinstance(traveler, dict) and str(traveler.get("traveler_id") or "").strip() == traveler_id

    def _verify_lead_record(self, lead_id: str) -> bool:
        if not lead_id:
            return False
        try:
            lookup = self.read_only_tools.lookup_lead(lead_id=lead_id)
        except Exception:
            return False
        leads = lookup.get("leads") if isinstance(lookup, dict) else []
        return any(
            isinstance(lead, dict) and str(lead.get("lead_id") or "").strip() == lead_id
            for lead in leads or []
        )

    def _verify_booking_record(self, booking_id: str) -> bool:
        if not booking_id:
            return False
        try:
            status = self.read_only_tools.get_booking_status(booking_id=booking_id)
        except Exception:
            return False
        bookings = status.get("bookings") if isinstance(status, dict) else []
        return any(
            isinstance(booking, dict) and str(booking.get("booking_id") or "").strip() == booking_id
            for booking in bookings or []
        )

    def _verify_lead_stage_record(self, lead_id: str, expected_stage: str) -> bool:
        if not lead_id:
            return False
        try:
            lookup = self.read_only_tools.lookup_lead(lead_id=lead_id)
        except Exception:
            return False
        leads = lookup.get("leads") if isinstance(lookup, dict) else []
        expected = str(expected_stage or "").strip().casefold()
        for lead in leads or []:
            if not isinstance(lead, dict) or str(lead.get("lead_id") or "").strip() != lead_id:
                continue
            if not expected:
                return True
            return str(lead.get("lead_stage") or "").strip().casefold() == expected
        return False

    def _verify_guardian_consent_record(self, traveler_id: str, *, guardian_name: str, guardian_phone: str) -> bool:
        if not traveler_id:
            return False
        try:
            profile = self.read_only_tools.get_traveler_profile(traveler_id=traveler_id)
        except Exception:
            return False
        traveler = profile.get("traveler") if isinstance(profile, dict) else None
        if not isinstance(traveler, dict):
            return False
        return (
            str(traveler.get("guardian_name") or "").strip() == str(guardian_name or "").strip()
            and str(traveler.get("guardian_phone") or "").strip() == str(guardian_phone or "").strip()
        )

    def _verify_lead_guardian_flag_record(self, lead_id: str, *, requires_guardian_approval: bool) -> bool:
        if not lead_id:
            return False
        try:
            lookup = self.read_only_tools.lookup_lead(lead_id=lead_id)
        except Exception:
            return False
        leads = lookup.get("leads") if isinstance(lookup, dict) else []
        for lead in leads or []:
            if not isinstance(lead, dict) or str(lead.get("lead_id") or "").strip() != lead_id:
                continue
            return bool(lead.get("requires_guardian_approval")) == bool(requires_guardian_approval)
        return False

    def _execute_set_guardian_consent(
        self,
        payload: dict[str, Any],
        session_context: dict[str, Any],
        validation,
    ) -> dict[str, Any]:
        traveler_id = self._value(payload, session_context, "traveler_id")
        if not traveler_id:
            raise RuntimeError("Guardian consent requires a traveler_id.")
        result = self.service.set_guardian_consent(
            traveler_id,
            is_minor=self._as_bool(self._value(payload, session_context, "is_minor"), default=True),
            guardian_name=self._value(payload, session_context, "guardian_name") or "",
            guardian_phone=self._value(payload, session_context, "guardian_phone") or "",
        )
        # UnifiedCRMService.set_guardian_consent never produced a
        # write_result_contract at all (PostgresAgentBridgeService.
        # set_guardian_consent does) -- build it explicitly here so the
        # outcome is comparable across backends regardless.
        result = result if isinstance(result, dict) else {}
        contract = result.get("write_result_contract") if isinstance(result.get("write_result_contract"), dict) else None
        if not contract:
            contract = self._write_result_contract(
                status="success" if result else "failed",
                executed=bool(result),
                reused=False,
                record_type="traveler",
                record_id=traveler_id,
                customer_confirmation_allowed=False,
                audit={"session_id": str(session_context.get("session_id") or ""), "action": "set_guardian_consent"},
            )
        return {
            "result_id": traveler_id,
            "assistant_message": "",
            "traveler": result,
            "write_result": {"traveler_update": result, "write_result_contract": contract},
            "write_result_contract": contract,
        }

    def _execute_flag_lead_guardian_approval(
        self,
        payload: dict[str, Any],
        session_context: dict[str, Any],
        validation,
    ) -> dict[str, Any]:
        lead_id = self._value(payload, session_context, "lead_id")
        if not lead_id:
            raise RuntimeError("The guardian-approval flag requires a lead_id.")
        result = self.service.flag_lead_guardian_approval(
            lead_id,
            requires_guardian_approval=self._as_bool(
                self._value(payload, session_context, "requires_guardian_approval"), default=True
            ),
        )
        # UnifiedCRMService.flag_lead_guardian_approval never produced a
        # write_result_contract at all (PostgresAgentBridgeService.
        # flag_lead_guardian_approval does) -- build it explicitly here so
        # the outcome is comparable across backends regardless.
        result = result if isinstance(result, dict) else {}
        contract = result.get("write_result_contract") if isinstance(result.get("write_result_contract"), dict) else None
        if not contract:
            contract = self._write_result_contract(
                status="success" if result else "failed",
                executed=bool(result),
                reused=False,
                record_type="lead",
                record_id=lead_id,
                customer_confirmation_allowed=False,
                audit={"session_id": str(session_context.get("session_id") or ""), "action": "flag_lead_guardian_approval"},
            )
        return {
            "result_id": lead_id,
            "assistant_message": "",
            "lead_update": result,
            "write_result": {"lead_update": result, "write_result_contract": contract},
            "write_result_contract": contract,
        }

    def record_guardian_consent(
        self,
        *,
        traveler_id: str,
        is_minor: bool,
        guardian_name: str = "",
        guardian_phone: str = "",
    ) -> dict[str, Any]:
        """Write guardian consent through the controlled dispatch, then confirm
        with an independent read-back.

        This used to call `self.service.set_guardian_consent(...)` directly.
        `self.service` is deliberately `None` when `CRM_ACCESS_MODE=api` (reads
        and writes go over HTTP in that mode), so every call raised
        AttributeError, was swallowed by the bare except below, and the caller
        went on to mark consent "saved" anyway. Routing through `self.execute()`
        makes this respect the same api_client/service branch every other write
        action already uses correctly. `set_guardian_consent` itself also runs
        an UPDATE ... WHERE traveler_id = ? that silently succeeds (0 rows
        affected) for a traveler_id that does not exist, so even a write that
        reaches the real service is not proof the fields persisted -- only a
        fresh read counts. Callers must check the "verified" key before
        treating a minor's booking as guardian-approved.
        """
        if not traveler_id:
            return {}
        payload = {
            "traveler_id": traveler_id,
            "is_minor": is_minor,
            "guardian_name": guardian_name,
            "guardian_phone": guardian_phone,
        }
        try:
            result = self._create_with_verified_retry(
                perform=lambda: self.execute(
                    action="set_guardian_consent", payload=payload, session_context={}
                ),
                verify=lambda record_id, res: self._verify_guardian_consent_record(
                    record_id, guardian_name=guardian_name, guardian_phone=guardian_phone
                ),
                record_type="traveler",
                session_context={},
            )
        except Exception as exc:
            agent_logger.warning("Guardian consent write failed traveler=%s error=%s", traveler_id, exc)
            return {}
        self._log_audit(
            {
                "timestamp": _utc_now_iso(),
                "session_id": "",
                "action": "set_guardian_consent",
                "validator_decision": "",
                "executed": True,
                "result_id": traveler_id,
                "reason": "guardian_consent_persisted",
                "warnings": [],
            }
        )
        return {**result, "verified": True}

    def record_lead_guardian_flag(self, *, lead_id: str, requires_guardian_approval: bool) -> dict[str, Any]:
        if not lead_id:
            return {}
        payload = {"lead_id": lead_id, "requires_guardian_approval": requires_guardian_approval}
        try:
            result = self._create_with_verified_retry(
                perform=lambda: self.execute(
                    action="flag_lead_guardian_approval", payload=payload, session_context={}
                ),
                verify=lambda record_id, res: self._verify_lead_guardian_flag_record(
                    record_id, requires_guardian_approval=requires_guardian_approval
                ),
                record_type="lead",
                session_context={},
            )
        except Exception as exc:
            agent_logger.warning("Guardian lead flag write failed lead=%s error=%s", lead_id, exc)
            return {}
        return {**result, "verified": True}

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

    def _passport_required_for_trip(self, trip: dict[str, Any], *, flight_option: str = "") -> bool:
        for key in ("passport_required", "requires_passport"):
            if key in trip:
                return self._as_bool(trip.get(key))
        if "passport_required_with_flight" in trip:
            return str(flight_option or "").strip() == "With Flight" and self._as_bool(trip.get("passport_required_with_flight"))
        return str(trip.get("type") or trip.get("trip_type") or "").strip().lower() == "international"

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
        raw = str(value or "").strip()
        labels = {
            "traveler_id_or_raw_phone": "WhatsApp number or traveler ID",
            "raw_phone": "WhatsApp number",
            "pending_raw_phone": "WhatsApp number",
            "trip_id": "trip",
            "room_type": "room type",
            "flight_option": "flight preference",
            "selected_trip_id": "selected trip",
        }
        if raw in labels:
            return labels[raw]
        return " ".join(raw.replace("_", " ").split())

    @staticmethod
    def _human_message_for_blocked_action(action: str, validation_payload: dict[str, Any], session_context: dict[str, Any]) -> str:
        decision = str(validation_payload.get("decision") or "").strip()
        reasons = validation_payload.get("reasons") or []
        missing = validation_payload.get("missing_information") or []
        language = str(session_context.get("language") or "").strip().lower()
        if decision == NEED_MORE_INFORMATION and missing:
            joined = ", ".join(GeminiWriteToolExecutor._humanize_token(item) for item in missing if item)
            if language.startswith("ar"):
                return f"\u0642\u0628\u0644 \u0623\u0646 \u0623\u062a\u0627\u0628\u0639\u060c \u0623\u062d\u062a\u0627\u062c \u0625\u0644\u0649: {joined}."
            return f"Before I can continue, I still need: {joined}."
        if decision == REJECTED and reasons:
            joined = " ".join(GeminiWriteToolExecutor._humanize_token(item) for item in reasons if item)
            if language.startswith("ar"):
                return f"لا يمكنني تنفيذ هذا الطلب الآن. {joined}"
            return f"I can't execute this request right now. {joined}"
        if language.startswith("ar"):
            return "لا يمكنني تنفيذ هذا الطلب الآن."
        return "I can't execute this request right now."

    @staticmethod
    def _lead_message(result: dict[str, Any], customer_name: str, language: str = "en") -> str:
        lead_id = str(result.get("lead_id") or "").strip()
        stage = str(result.get("lead_stage") or "").strip()
        name = customer_name.strip() or "the customer"
        proposed = f"Lead {lead_id} created for {name}. Current stage: {stage}."
        return gate_customer_write_reply(
            proposed_reply=proposed,
            write_result=result,
            record_type="lead",
            language=language,
            display_name=name,
        )

    @staticmethod
    def _stage_message(result: dict[str, Any]) -> str:
        lead_id = str(result.get("lead_id") or "").strip()
        stage = str(result.get("lead_stage") or "").strip()
        proposed = f"Lead {lead_id} moved to {stage}."
        return gate_customer_write_reply(
            proposed_reply=proposed,
            write_result=result,
            record_type="lead",
        )

    @staticmethod
    def _booking_message(result: dict[str, Any], language: str = "en") -> str:
        booking_id = str(result.get("booking_id") or "").strip()
        trip_name = str(result.get("trip_name") or "").strip()
        booking_status = str(result.get("booking_status") or "").strip() or "Draft"
        proposed = f"Booking draft {booking_id} created for {trip_name}. Status: {booking_status}."
        return gate_customer_write_reply(
            proposed_reply=proposed,
            write_result=result,
            record_type="booking",
            language=language,
        )

    @staticmethod
    def _handoff_message(result: dict[str, Any], language: str = "en") -> str:
        handoff_id = str(result.get("handoff_id") or "").strip()
        reason = str(result.get("reason_text") or result.get("reason_code") or "").strip()
        proposed = f"Handoff case {handoff_id} created. Automation is now stopped for this session. Reason: {reason}."
        return gate_customer_write_reply(
            proposed_reply=proposed,
            write_result=result,
            record_type="handoff",
            language=language,
        )

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
