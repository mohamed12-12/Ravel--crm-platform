from __future__ import annotations

import re
import uuid
from dataclasses import replace
from typing import Any

from services.ai_agent.ai_agent_app.agent.gemini_agent import GeminiAgent
from services.ai_agent.ai_agent_app.agent.agent_state import AgentState
from services.ai_agent.ai_agent_app.agent.context_builder import ContextBuilder
from services.ai_agent.ai_agent_app.agent.identity_policy import AgentIdentityPolicy
from services.ai_agent.ai_agent_app.agent.memory import AgentMemory
from services.ai_agent.ai_agent_app.agent.persona import AgentPersona
from services.ai_agent.ai_agent_app.agent.planner import AgentPlanner
from services.ai_agent.ai_agent_app.agent.privacy_policy import AgentPrivacyPolicy
from services.ai_agent.ai_agent_app.agent.response_format import format_agent_reply
from services.ai_agent.ai_agent_app.agent.production_agent import ProductionAgentCoordinator
from services.ai_agent.ai_agent_app.agent.safety import AgentSafetyLayer
from services.ai_agent.ai_agent_app.agent.read_only_tools import ReadOnlyCRMTools
from services.ai_agent.ai_agent_app.agent.session_flow import SessionState, detect_language
from services.ai_agent.ai_agent_app.agent.tool_manager import ToolManager
from services.ai_agent.ai_agent_app.agent.tool_registry import build_agent_tool_registry
from services.ai_agent.ai_agent_app.agent.write_tool_executor import GeminiWriteToolExecutor
from services.ai_agent.ai_agent_app.agent.workflow_policy import ConversationWorkflowPolicy
from services.ai_agent.ai_agent_app.config import Settings
from services.ai_agent.ai_agent_app.logger import agent_logger
from services.ai_agent.validation.validation_rules import normalize_flight_option, normalize_trip_type
from services.ai_agent.llm import build_llm_provider
from services.crm.system_services.phone_normalization import normalize_phone_input


SAFE_STATUS_MAP = {
    "starting": "Ready",
    "identity_required": "Waiting for WhatsApp number",
    "identity_lookup_pending": "Checking CRM",
    "traveler_found": "Traveler found",
    "traveler_not_found": "New traveler details required",
    "traveler_verified": "Traveler verified",
    "trip_type_required": "Ready",
    "duplicate_traveler_detected": "Human review required",
    "human_handoff_required": "Human review required",
    "trip_discovery": "Trip preferences being collected",
    "trip_search_ready": "Searching trips",
    "trip_selection_required": "Waiting for customer response",
    "trip_results_available": "Searching trips",
    "room_type_required": "Waiting for customer response",
    "traveler_gender_required": "Waiting for customer response",
    "group_size_required": "Waiting for customer response",
    "capacity_handoff_required": "Human review required",
    "flight_option_required": "Waiting for customer response",
    "nationality_required": "Waiting for customer response",
    "birthday_required": "Waiting for customer response",
    "currency_required": "Waiting for customer response",
    "awaiting_passport_upload": "Waiting for customer response",
    "booking_ready": "Ready",
    "booking_confirmation_required": "Waiting for customer response",
    "trip_media_shared": "Trip media shared",
    "no_trip_match": "Trip preferences being collected",
    "collecting_context": "Understanding request",
    "checking_crm": "Checking CRM",
    "searching_trips": "Searching trips",
    "waiting": "Waiting for customer response",
    "done": "Done",
    "error": "Unable to complete request",
}

_DIGIT_TRANSLATION = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_PHONE_CANDIDATE_RE = re.compile(r"(?:\+|00)?[\d٠-٩۰-۹][\d٠-٩۰-۹\s().-]{7,}[\d٠-٩۰-۹]")

_BIRTHDAY_RE = re.compile(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b|\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b")
_TRIP_MEDIA_URL_RE = re.compile(r"(?:https?://[^\s<>()]+)?/trips/media/[A-Za-z0-9._~:-]+")


_AFFIRMATIVE_REPLIES = {
    "yes",
    "y",
    "ok",
    "okay",
    "sure",
    "confirm",
    "book it",
    "go ahead",
    "تمام",
    "ماشي",
    "موافق",
    "ايوه",
    "أيوه",
    "نعم",
    "اوكي",
    "اوكى",
}

_TRIP_REFERENCE_STOP_WORDS = {
    "a", "an", "about", "all", "any", "can", "details", "for", "get", "i", "info", "is",
    "me", "need", "of", "please", "show", "the", "to", "travel", "trip", "trips", "want", "we",
    "with", "عايز", "عايزه", "عاوز", "عاوزه", "اريد", "رحله", "رحلة", "رحلات", "سفر", "تفاصيل",
    "عن", "في", "من", "ممكن", "لو", "عايزين", "عاوزين", "انا", "إنا", "ايه", "اي", "ال",
}
_TRIP_REFERENCE_FILLER_WORDS = _TRIP_REFERENCE_STOP_WORDS | {
    "book", "booking", "reserve", "reservation", "option", "options", "named", "called",
    "رحله", "رحلة", "رحلات", "احجز", "حجز", "اسمها",
}
_TRIP_TYPE_ONLY_REFERENCES = {
    "1", "2", "local", "loc", "loca", "domestic", "international", "int", "intl", "inter",
    "abroad", "overseas", "محلي", "محليه", "داخلي", "داخليه", "دولي", "دوليه", "خارجي", "خارجيه",
}

_ARABIC_TRIP_REFERENCE_TRANSLATION = str.maketrans(
    {"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"}
)

_BACKEND_OWNED_COLLECTION_STEPS = {
    "collect_traveler_gender",
    "collect_room_type",
    "collect_group_size",
    "collect_flight_preference",
    "collect_passport_attachment",
}

_ABUSIVE_OR_HOSTILE_RE = re.compile(r"\b(?:fuck|f\W*u\W*c\W*k|shit|stupid|idiot|dumb|bad bot)\b", re.IGNORECASE)


class ToolCallingSessionRuntime:
    def __init__(self, *, settings: Settings, conversation_ai: GeminiAgent | None = None) -> None:
        self.settings = settings
        self.agent_persona_name = settings.agent_persona_name or "Ravel Agent"
        self._sessions: dict[str, SessionState] = {}
        self._conversation_ai = conversation_ai
        self._read_only_tools = ReadOnlyCRMTools(settings)
        self._write_executor = GeminiWriteToolExecutor(settings=settings, read_only_tools=self._read_only_tools)
        self.max_tool_rounds = max(1, int(settings.ai_max_tool_rounds or 4))
        self._persona = AgentPersona.from_settings(settings)
        self._memory = AgentMemory()
        self._state_by_session: dict[str, AgentState] = {}
        self._identity_policy = AgentIdentityPolicy()
        self._privacy_policy = AgentPrivacyPolicy()
        self._workflow_policy = ConversationWorkflowPolicy()
        self._tool_registry = build_agent_tool_registry(include_write_tools=True, include_validation_tool=False)
        self._coordinator = ProductionAgentCoordinator(
            persona=self._persona,
            memory=self._memory,
            planner=AgentPlanner(),
            context_builder=ContextBuilder(),
            tool_manager=ToolManager(self._tool_registry),
            safety=AgentSafetyLayer(),
        )

        if self._conversation_ai is None:
            provider = build_llm_provider(settings)
            if provider is not None:
                self._conversation_ai = GeminiAgent(
                    settings=settings,
                    provider=provider,
                    read_only_tools=self._read_only_tools,
                    tool_registry=self._tool_registry,
                    write_tools_enabled=True,
                    max_tool_calls=self.max_tool_rounds,
                    safety_layer=self._coordinator.safety,
                )

    def clear(self) -> None:
        self._sessions.clear()

    def get(self, session_id: str) -> SessionState | None:
        return self._sessions.get(session_id)

    def create_session(self, gateway=None) -> SessionState:
        session = SessionState(id=uuid.uuid4().hex)
        session.agent_mode = "tool_calling"
        session.stage = "identity_required"
        self._state_by_session[session.id] = AgentState(goal="help the traveler plan a trip")
        session.messages.append(
            {
                "role": "assistant",
                "text": self._opening_message(),
            }
        )
        self._sessions[session.id] = session
        return session

    def _opening_message(self) -> str:
        return (
            f"Hello, I am {self.agent_persona_name}, Rahma Traveler's AI travel sales assistant. "
            "Please share your WhatsApp number first so I can check your CRM profile safely, then I will continue with your trip request."
        )

    def _safe_status(self, raw: str) -> str:
        return SAFE_STATUS_MAP.get(raw, raw or SAFE_STATUS_MAP["starting"])

    @staticmethod
    def _linked_ids(session: SessionState) -> dict[str, str]:
        final_result = session.final_result if isinstance(session.final_result, dict) else {}
        booking_result = session.booking_result if isinstance(session.booking_result, dict) else {}
        preview = session.preview if isinstance(session.preview, dict) else {}
        write_result = final_result.get("write_result") if isinstance(final_result.get("write_result"), dict) else {}
        lead_update = write_result.get("lead_update") if isinstance(write_result.get("lead_update"), dict) else {}
        created_traveler = write_result.get("created_traveler") if isinstance(write_result.get("created_traveler"), dict) else {}
        preview_traveler = preview.get("traveler") if isinstance(preview.get("traveler"), dict) else {}
        traveler = final_result.get("traveler") if isinstance(final_result.get("traveler"), dict) else {}
        return {
            "traveler_id": str(
                created_traveler.get("traveler_id")
                or traveler.get("traveler_id")
                or preview_traveler.get("traveler_id")
                or ""
            ).strip(),
            "lead_id": str(lead_update.get("lead_id") or final_result.get("lead_id") or "").strip(),
            "booking_id": str(booking_result.get("booking_id") or final_result.get("booking_id") or "").strip(),
        }

    def _passport_context(self, session: SessionState, known_traveler: dict[str, Any]) -> dict[str, Any]:
        traveler_id = str((known_traveler or {}).get("traveler_id") or "").strip()
        raw_phone = str(session.raw_phone or session.pending_raw_phone or "").strip()
        if not traveler_id and not raw_phone:
            return {}
        try:
            if traveler_id:
                return self._read_only_tools.get_passport_status(traveler_id=traveler_id)
            return self._read_only_tools.get_passport_status(
                raw_phone=raw_phone,
                country_code=session.country_code or self.settings.default_country_code,
            )
        except Exception:
            return {}

    def _build_context(self, session: SessionState, user_text: str) -> dict[str, Any]:
        preview = session.preview if isinstance(session.preview, dict) else {}
        traveler = preview.get("traveler") if isinstance(preview.get("traveler"), dict) else {}
        workflow = preview.get("workflow") if isinstance(preview.get("workflow"), dict) else {}
        trip_result = preview.get("trip_result") if isinstance(preview.get("trip_result"), dict) else {}
        selected_trip = self._selected_trip(session)
        linked_ids = self._linked_ids(session)
        final_result = session.final_result if isinstance(session.final_result, dict) else {}
        final_traveler = final_result.get("traveler") if isinstance(final_result.get("traveler"), dict) else {}
        write_result = final_result.get("write_result") if isinstance(final_result.get("write_result"), dict) else {}
        created_traveler = write_result.get("created_traveler") if isinstance(write_result.get("created_traveler"), dict) else {}
        known_traveler = traveler or final_traveler or created_traveler or {}
        if linked_ids["traveler_id"] and not known_traveler:
            known_traveler = {
                "traveler_id": linked_ids["traveler_id"],
                "full_name": session.customer_name,
                "status": "Active",
            }
        if known_traveler.get("traveler_id") and not known_traveler.get("status"):
            known_traveler = {**known_traveler, "status": "Active"}
        if known_traveler.get("traveler_id") and known_traveler.get("status") and workflow.get("lookup_status") == "not_found":
            workflow = {
                **dict(workflow),
                "lookup_status": "found",
                "identity_verified": True,
                "verified_status": str(known_traveler.get("status") or "Active"),
                "verified_traveler": dict(known_traveler),
            }
        passport_status = self._passport_context(session, known_traveler)
        passport_traveler = passport_status.get("traveler") if isinstance(passport_status.get("traveler"), dict) else {}
        passport_on_file = bool(
            session.passport_attachment_ref
            or passport_traveler.get("passport_attachment_ref")
            or list(passport_status.get("documents") or [])
        )
        return {
            "session_id": session.id,
            "language": session.language,
            "customer_name": session.customer_name,
            "birthday": session.birthday,
            "nationality": session.nationality,
            "raw_phone": session.raw_phone,
            "pending_raw_phone": session.pending_raw_phone,
            "country_code": session.country_code,
            "phone_normalization": session.phone_normalization,
            "traveler_id": linked_ids["traveler_id"] or str((known_traveler or {}).get("traveler_id") or ""),
            "lead_id": linked_ids["lead_id"],
            "booking_id": linked_ids["booking_id"],
            "trip_type": self._effective_trip_type(session),
            "selected_trip_id": session.selected_trip_id,
            "selected_trip_name": session.selected_trip_name,
            "group_size": session.group_size,
            "preferred_date": session.preferred_date,
            "flight_option": session.flight_option,
            "room_type": session.room_type,
            "room_group": session.room_group,
            "currency": session.currency,
            "passport_attachment_ref": session.passport_attachment_ref,
            "passport_status": passport_status,
            "passport_on_file": passport_on_file,
            "booking_confirmation_requested": bool(session.booking_confirmation_requested),
            "booking_confirmed": bool(session.booking_confirmed),
            "known_traveler": known_traveler,
            "workflow": workflow,
            "trip_result": trip_result,
            "selected_trip": selected_trip,
            "collection_state": self._collection_state(session),
            "customer_preferences": {
                "trip_type": self._effective_trip_type(session),
                "destination": session.selected_trip_name,
                "birthday": session.birthday,
                "nationality": session.nationality,
                "group_size": session.group_size,
                "preferred_date": session.preferred_date,
                "flight_option": session.flight_option,
                "room_type": session.room_type,
                "room_group": session.room_group,
                "currency": session.currency,
            },
            "conversation_history": list(session.messages[-12:]),
            "last_user_message": user_text,
            "stage": session.stage,
        }

    def _effective_trip_type(self, session: SessionState) -> str:
        selected_trip = self._selected_trip(session)
        selected_type = str(selected_trip.get("type") or selected_trip.get("trip_type") or "").strip().lower()
        if selected_type in {"local", "international"}:
            return selected_type
        trip_type = str(session.trip_type or "").strip().lower()
        if trip_type in {"local", "international"}:
            return trip_type
        return trip_type

    @staticmethod
    def _normalize_reply(reply: str, language: str) -> str:
        cleaned = format_agent_reply(reply)
        if not cleaned:
            return "I need one more detail so I can help you correctly."
        if language.startswith("ar"):
            return cleaned
        return cleaned

    @staticmethod
    def _collection_state(session: SessionState) -> dict[str, bool]:
        preview = session.preview if isinstance(session.preview, dict) else {}
        stored = preview.get("collection_state") if isinstance(preview.get("collection_state"), dict) else {}
        return {
            "trip_type": bool(session.trip_type),
            "selected_trip": bool(session.selected_trip_id),
            "destination": bool(stored.get("destination") or session.selected_trip_name),
            "preferred_date": bool(stored.get("preferred_date") or session.preferred_date),
            "birthday": bool(stored.get("birthday") or session.birthday),
            "nationality": bool(stored.get("nationality") or session.nationality),
            "room_type": bool(stored.get("room_type") or session.room_type),
            "room_group": bool(stored.get("room_group") or session.room_group),
            "group_size": bool(stored.get("group_size")),
            "flight_option": bool(stored.get("flight_option") or session.flight_option),
            "currency": bool(stored.get("currency") or session.currency),
        }

    @staticmethod
    def _update_collection_state(session: SessionState, **updates: bool) -> None:
        preview = dict(session.preview or {})
        current = preview.get("collection_state") if isinstance(preview.get("collection_state"), dict) else {}
        preview["collection_state"] = {
            **dict(current),
            **{key: bool(value) for key, value in updates.items()},
        }
        session.preview = preview

    def _run_identity_lookup_if_ready(self, session: SessionState) -> dict[str, Any] | None:
        if self._preview_has_verified_traveler(session):
            return None
        raw_phone = str(session.raw_phone or session.pending_raw_phone or "").strip()
        if not raw_phone:
            return None
        phone_info = normalize_phone_input(
            raw_phone,
            session.country_code or self.settings.default_country_code,
            default_country_is_explicit=bool(session.country_code or self.settings.default_country_code),
        ).to_dict()
        session.phone_normalization = phone_info
        if phone_info.get("requires_country_confirmation") or not phone_info.get("normalized_e164"):
            workflow = {
                "lookup_status": "invalid_phone",
                "identity_verified": False,
                "required_step": "collect_valid_whatsapp_number",
            }
            session.preview = {**dict(session.preview or {}), "workflow": workflow}
            return {
                "name": "find_traveler_by_phone",
                "input": {"raw_phone": raw_phone, "country_code": session.country_code or self.settings.default_country_code},
                "result": {"status": "invalid_phone", "warnings": ["Phone number requires confirmation."], "traveler": None},
                "executed": False,
            }
        result = self._read_only_tools.find_traveler_by_phone(
            raw_phone=raw_phone,
            country_code=str(phone_info.get("country_code") or session.country_code or self.settings.default_country_code or ""),
        )
        self._store_identity_result(session, result)
        return {
            "name": "find_traveler_by_phone",
            "input": {"raw_phone": raw_phone, "country_code": str(phone_info.get("country_code") or "")},
            "result": result,
            "executed": True,
        }

    @staticmethod
    def _preview_has_verified_traveler(session: SessionState) -> bool:
        preview = session.preview if isinstance(session.preview, dict) else {}
        traveler = preview.get("traveler") if isinstance(preview.get("traveler"), dict) else {}
        workflow = preview.get("workflow") if isinstance(preview.get("workflow"), dict) else {}
        return bool(workflow.get("identity_verified") and traveler.get("traveler_id") and traveler.get("status"))

    @staticmethod
    def _store_identity_result(session: SessionState, result: dict[str, Any]) -> None:
        result = result if isinstance(result, dict) else {}
        status = str(result.get("status") or "").strip()
        traveler = result.get("traveler") if isinstance(result.get("traveler"), dict) else {}
        workflow = {
            "lookup_status": status,
            "identity_verified": status == "found" and bool(traveler.get("traveler_id") and traveler.get("status")),
            "verified_status": str(traveler.get("status") or "").strip(),
            "verified_traveler": dict(traveler) if traveler else {},
        }
        if status == "duplicate":
            workflow["handoff_required"] = True
        preview = dict(session.preview or {})
        if traveler:
            preview["traveler"] = traveler
            session.customer_name = str(traveler.get("full_name") or session.customer_name or "")
        preview["workflow"] = workflow
        session.preview = preview

    @staticmethod
    def _trip_candidates(session: SessionState) -> list[dict[str, Any]]:
        preview = session.preview if isinstance(session.preview, dict) else {}
        trip_result = preview.get("trip_result") if isinstance(preview.get("trip_result"), dict) else {}
        return [*list(trip_result.get("open_trips") or []), *list(trip_result.get("date_tbd_trips") or [])]

    @staticmethod
    def _selected_trip(session: SessionState) -> dict[str, Any]:
        trip_id = str(session.selected_trip_id or "").strip()
        if not trip_id:
            return {}
        for trip in ToolCallingSessionRuntime._trip_candidates(session):
            if str(trip.get("trip_id") or "").strip() == trip_id:
                return dict(trip)
        return {}

    @staticmethod
    def _current_trip_for_followup(session: SessionState) -> dict[str, Any]:
        selected = ToolCallingSessionRuntime._selected_trip(session)
        if selected:
            return selected
        preview = session.preview if isinstance(session.preview, dict) else {}
        referenced = preview.get("trip_reference") if isinstance(preview.get("trip_reference"), dict) else {}
        if referenced and str(referenced.get("trip_id") or "").strip():
            return dict(referenced)
        candidates = ToolCallingSessionRuntime._trip_candidates(session)
        if len(candidates) == 1 and isinstance(candidates[0], dict):
            return dict(candidates[0])
        return {}

    @staticmethod
    def _explicit_english_request(text: str) -> bool:
        normalized = ToolCallingSessionRuntime._normalize_trip_reference(text)
        return any(
            phrase in normalized
            for phrase in (
                "speak english",
                "english please",
                "continue in english",
                "reply in english",
                "بالانجليزي",
                "بالانجليزى",
            )
        )

    @staticmethod
    def _looks_affirmative(text: str) -> bool:
        normalized = ToolCallingSessionRuntime._normalize_public_trip_reference(text)
        return normalized in _AFFIRMATIVE_REPLIES

    @staticmethod
    def _looks_negative_confirmation(text: str) -> bool:
        normalized = ToolCallingSessionRuntime._normalize_trip_reference(text)
        return normalized in {"no", "n", "cancel", "stop", "not now", "لا", "لاء", "الغاء", "إلغاء"}

    def _apply_trip_selection_from_text(self, session: SessionState, text: str) -> None:
        if session.selected_trip_id:
            return
        candidates = self._trip_candidates(session)
        if not candidates:
            return
        normalized = self._normalize_trip_reference(text)
        chosen: dict[str, Any] | None = None
        option_match = re.fullmatch(r"\s*([1-9])[\s.)_\-]*", str(text or "").translate(_DIGIT_TRANSLATION))
        if option_match and int(option_match.group(1)) <= len(candidates):
            chosen = candidates[int(option_match.group(1)) - 1]
        elif len(candidates) == 1 and self._looks_affirmative(text):
            chosen = candidates[0]
        else:
            matches = self._rank_trip_reference_matches(normalized, candidates)
            if len(matches) == 1 and matches[0]["score"] >= 55:
                chosen = matches[0]["trip"]
            elif len(matches) > 1 and matches[0]["score"] >= 75 and matches[0]["score"] >= matches[1]["score"] + 15:
                chosen = matches[0]["trip"]
        if not chosen:
            return
        self._select_trip(session, chosen)

    def _select_trip(self, session: SessionState, trip: dict[str, Any]) -> None:
        session.selected_trip_id = str(trip.get("trip_id") or "").strip()
        session.selected_trip_name = str(trip.get("trip_name") or session.selected_trip_name or "").strip()
        chosen_type = str(trip.get("type") or trip.get("trip_type") or "").strip().lower()
        if chosen_type in {"local", "international"}:
            session.trip_type = chosen_type
            self._update_collection_state(session, trip_type=True)
        self._update_collection_state(session, selected_trip=True, destination=bool(session.selected_trip_name))

    @staticmethod
    def _has_trip_reference_words(text: str) -> bool:
        normalized = ToolCallingSessionRuntime._normalize_trip_reference(text)
        return any(
            token in normalized
            for token in (
                "trip", "trips", "travel", "رحله", "رحلة", "رحلات", "سفر",
                "book", "booking", "reserve", "حجز", "احجز",
            )
        )

    def _run_trip_reference_lookup_if_ready(self, session: SessionState, text: str) -> None:
        if session.selected_trip_id or not self._preview_has_verified_traveler(session):
            return
        normalized = " ".join(str(text or "").strip().lower().split())
        if not normalized or normalized in _AFFIRMATIVE_REPLIES:
            return
        query = re.sub(
            r"\b(?:i|we|want|need|show|me|the|a|an|trip|trips|travel|please|عايز|عايزة|اريد|أريد|رحلة|رحلات)\b",
            " ",
            normalized,
            flags=re.IGNORECASE,
        )
        query = " ".join(query.split())
        if len(query) < 2 or len(query) > 80:
            return
        try:
            result = self._read_only_tools.search_trips(trip_type=session.trip_type, query=query)
        except Exception:
            return
        candidates = [*list(result.get("open_trips") or []), *list(result.get("date_tbd_trips") or [])]
        if not candidates:
            return
        preview = dict(session.preview or {})
        preview["trip_result"] = {
            "open_trips": list(result.get("open_trips") or []),
            "date_tbd_trips": list(result.get("date_tbd_trips") or []),
        }
        session.preview = preview
        if len(candidates) == 1:
            self._apply_trip_selection_from_text(session, query)

    @staticmethod
    def _normalize_public_trip_reference(value: str) -> str:
        return ToolCallingSessionRuntime._normalize_trip_reference(value)

    @staticmethod
    def _normalize_trip_reference(value: str) -> str:
        normalized = str(value or "").casefold().translate(_ARABIC_TRIP_REFERENCE_TRANSLATION)
        normalized = re.sub(r"[\u064b-\u065f\u0670]", "", normalized)
        normalized = re.sub(r"\bintl?\.?(?=\s|$)", "international", normalized)
        normalized = re.sub(r"\bloc\.?(?=\s|$)", "local", normalized)
        normalized = re.sub(r"[^\w\u0600-\u06ff-]+", " ", normalized)
        return " ".join(normalized.split())

    @classmethod
    def _trip_reference_tokens(cls, value: str) -> list[str]:
        normalized = cls._normalize_trip_reference(value)
        return [
            token
            for token in re.findall(r"[\w\u0600-\u06ff-]+", normalized)
            if token and token not in _TRIP_REFERENCE_FILLER_WORDS
        ]

    @classmethod
    def _compact_trip_reference(cls, value: str) -> str:
        return re.sub(r"[\W_]+", "", cls._normalize_trip_reference(value), flags=re.UNICODE)

    @classmethod
    def _trip_reference_is_only_type(cls, value: str) -> bool:
        tokens = cls._trip_reference_tokens(value)
        return bool(tokens) and all(token in _TRIP_TYPE_ONLY_REFERENCES for token in tokens)

    @classmethod
    def _trip_reference_score(cls, query: str, trip: dict[str, Any]) -> int:
        normalized_query = cls._normalize_trip_reference(query)
        if not normalized_query or cls._trip_reference_is_only_type(normalized_query):
            return 0
        query_tokens = cls._trip_reference_tokens(normalized_query)
        if not query_tokens:
            return 0

        trip_id = str(trip.get("trip_id") or "")
        trip_name = str(trip.get("trip_name") or "")
        normalized_id = cls._normalize_trip_reference(trip_id)
        normalized_name = cls._normalize_trip_reference(trip_name)
        compact_query = cls._compact_trip_reference(normalized_query)
        compact_id = cls._compact_trip_reference(trip_id)
        compact_name = cls._compact_trip_reference(trip_name)

        if normalized_query == normalized_id or compact_query == compact_id:
            return 100
        if normalized_query == normalized_name or compact_query == compact_name:
            return 96
        if compact_id and compact_id in compact_query:
            return 94
        if compact_name and compact_name in compact_query:
            return 95

        name_tokens = set(cls._trip_reference_tokens(trip_name))
        id_tokens = set(cls._trip_reference_tokens(trip_id))
        searchable_tokens = name_tokens | id_tokens
        if not searchable_tokens:
            return 0
        matched = [token for token in query_tokens if token in searchable_tokens]
        if not matched:
            return 0
        coverage = len(set(matched)) / max(1, len(set(query_tokens)))
        name_coverage = len(set(matched) & name_tokens) / max(1, len(name_tokens))
        score = int(45 + (coverage * 30) + (name_coverage * 20))
        if len(set(matched)) >= 2:
            score += 10
        if len(set(query_tokens)) == 1 and len(next(iter(set(query_tokens)))) < 4:
            score -= 20
        return max(0, min(score, 89))

    @classmethod
    def _rank_trip_reference_matches(cls, query: str, trips: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ranked: list[dict[str, Any]] = []
        seen: set[str] = set()
        for trip in trips:
            if not isinstance(trip, dict):
                continue
            trip_id = str(trip.get("trip_id") or "").strip()
            if not trip_id or trip_id in seen:
                continue
            score = cls._trip_reference_score(query, trip)
            if score >= 45:
                ranked.append({"trip": dict(trip), "score": score})
                seen.add(trip_id)
        ranked.sort(key=lambda item: (-int(item["score"]), str(item["trip"].get("start_date") or ""), str(item["trip"].get("trip_name") or "")))
        return ranked

    @classmethod
    def _public_trip_reference_queries(cls, text: str) -> list[str]:
        normalized = cls._normalize_trip_reference(text)
        if not normalized or normalized in _AFFIRMATIVE_REPLIES:
            return []
        tokens = [
            token
            for token in re.findall(r"[\w\u0600-\u06ff-]+", normalized)
            if token not in _TRIP_REFERENCE_FILLER_WORDS and len(token) >= 2
        ]
        if tokens and all(token in _TRIP_TYPE_ONLY_REFERENCES for token in tokens):
            return []
        queries: list[str] = []
        if len(normalized) >= 3:
            queries.append(normalized)
        for width in range(len(tokens), 0, -1):
            for start in range(len(tokens) - width + 1):
                candidate = " ".join(tokens[start : start + width])
                if len(candidate) >= 3 and candidate not in queries:
                    queries.append(candidate)
        return queries[:8]

    def _search_trip_reference_candidates(self, text: str) -> dict[str, Any]:
        queries = self._public_trip_reference_queries(text)
        if not queries:
            return {"query": "", "matches": [], "trips": []}
        all_trips: list[dict[str, Any]] = []
        seen: set[str] = set()
        for query in [*queries, ""]:
            try:
                result = self._read_only_tools.search_trips(query="" if query == "" else query)
            except Exception as exc:
                agent_logger.warning("Trip reference search failed query=%s error=%s", query, exc, exc_info=True)
                continue
            for trip in [*list(result.get("open_trips") or []), *list(result.get("date_tbd_trips") or [])]:
                if not isinstance(trip, dict):
                    continue
                trip_id = str(trip.get("trip_id") or "").strip()
                if trip_id and trip_id not in seen:
                    seen.add(trip_id)
                    all_trips.append(dict(trip))
        best_query = queries[0]
        matches = self._rank_trip_reference_matches(best_query, all_trips)
        return {"query": best_query, "matches": matches, "trips": all_trips}

    def _resolve_public_trip_reference(self, session: SessionState, text: str) -> dict[str, Any] | None:
        if session.selected_trip_id:
            return None
        search = self._search_trip_reference_candidates(text)
        matches = list(search.get("matches") or [])
        if not matches:
            return None
        if len(matches) > 1 and matches[0]["score"] < matches[1]["score"] + 5 and matches[1]["score"] >= 65:
            return None
        if matches[0]["score"] < 65:
            return None
        trip = dict(matches[0]["trip"])
        preview = dict(session.preview or {})
        preview["trip_result"] = {
            "open_trips": [trip] if str(trip.get("start_date") or "").strip() else [],
            "date_tbd_trips": [] if str(trip.get("start_date") or "").strip() else [trip],
        }
        preview["trip_reference"] = trip
        session.preview = preview
        self._select_trip(session, trip)
        return trip
        return None

    @staticmethod
    def _trip_reference_choices_reply(matches: list[dict[str, Any]], language: str) -> str:
        trips = [dict(item["trip"]) for item in matches[:5] if isinstance(item, dict) and isinstance(item.get("trip"), dict)]
        if language.startswith("ar"):
            lines = ["وجدت أكثر من رحلة مطابقة. من فضلك اختر رقم الرحلة:"]
            for index, trip in enumerate(trips, start=1):
                trip_name = str(trip.get("trip_name") or trip.get("trip_id") or "").strip()
                trip_id = str(trip.get("trip_id") or "").strip()
                start_date = str(trip.get("start_date") or "").strip()
                date_part = f" - {start_date}" if start_date else ""
                lines.append(f"{index}. {trip_name} ({trip_id}){date_part}")
            return "\n".join(lines)
        lines = ["I found more than one matching trip. Please choose the trip number:"]
        for index, trip in enumerate(trips, start=1):
            trip_name = str(trip.get("trip_name") or trip.get("trip_id") or "").strip()
            trip_id = str(trip.get("trip_id") or "").strip()
            start_date = str(trip.get("start_date") or "").strip()
            date_part = f" - {start_date}" if start_date else ""
            lines.append(f"{index}. {trip_name} ({trip_id}){date_part}")
        return "\n".join(lines)

    @staticmethod
    def _trip_reference_unknown_reply(query: str, language: str) -> str:
        if language.startswith("ar"):
            return "لم أجد رحلة مؤكدة بهذا الاسم في CRM. من فضلك أرسل اسم الرحلة أو رقمها كما هو مكتوب."
        return "I could not find a verified CRM trip with that name. Please send the exact trip name or trip ID."

    @classmethod
    def _is_trip_details_request(cls, text: str) -> bool:
        normalized = cls._normalize_trip_reference(text)
        if not normalized:
            return False
        return any(
            phrase in normalized
            for phrase in (
                "more details",
                "more detail",
                "tell me more",
                "details",
                "detail",
                "info",
                "information",
                "program",
                "\u062a\u0641\u0627\u0635\u064a\u0644",
                "\u0645\u0639\u0644\u0648\u0645\u0627\u062a",
                "\u0628\u0631\u0646\u0627\u0645\u062c",
            )
        )

    @classmethod
    def _is_identity_question(cls, text: str) -> bool:
        normalized = cls._normalize_trip_reference(text)
        if not normalized:
            return False
        return normalized in {"who are you", "who are u", "who r u", "what are you"} or any(
            phrase in normalized
            for phrase in (
                "are you a bot",
                "are you human",
                "your name",
                "\u0627\u0646\u062a \u0645\u064a\u0646",
                "\u0627\u0646\u062a\u064a \u0645\u064a\u0646",
                "\u0645\u064a\u0646 \u0627\u0646\u062a",
                "\u0645\u064a\u0646 \u0627\u0646\u062a\u064a",
            )
        )

    @classmethod
    def _is_human_agent_request(cls, text: str) -> bool:
        normalized = cls._normalize_trip_reference(text)
        return any(
            phrase in normalized
            for phrase in (
                "human",
                "real agent",
                "person",
                "employee",
                "call me",
                "\u0645\u0648\u0638\u0641",
                "\u0627\u0646\u0633\u0627\u0646",
                "\u0643\u0644\u0645\u0646\u064a",
            )
        )

    @staticmethod
    def _is_hostile_message(text: str) -> bool:
        return bool(_ABUSIVE_OR_HOSTILE_RE.search(str(text or "")))

    @classmethod
    def _is_explanation_request(cls, text: str) -> bool:
        normalized = cls._normalize_trip_reference(text)
        if not normalized:
            return False
        return (
            normalized in {"why", "how come", "what for", "ليه", "لماذا", "ليش"}
            or any(
                phrase in normalized
                for phrase in (
                    "why do",
                    "why are",
                    "why need",
                    "what do you need",
                    "ليه",
                    "لماذا",
                    "ليش",
                    "عشان ايه",
                    "ليه محتاج",
                )
            )
        )

    @classmethod
    def _is_conversational_interruption(cls, text: str) -> bool:
        normalized = cls._normalize_trip_reference(text)
        if not normalized:
            return False
        if cls._is_explanation_request(text) or cls._is_identity_question(text) or cls._is_human_agent_request(text):
            return True
        if cls._is_hostile_message(text):
            return True
        return normalized in {
            "hello",
            "hi",
            "hey",
            "thanks",
            "thank you",
            "تمام",
            "شكرا",
            "مش فاهم",
            "مش فاهمة",
            "وضح",
            "وضحلي",
        }

    def _natural_interruption_fallback(self, session: SessionState, decision, clean_text: str) -> str:
        language = session.language
        trip = self._current_trip_for_followup(session)
        trip_name = session.selected_trip_name or str(trip.get("trip_name") or "الرحلة" if language.startswith("ar") else "the trip")
        step = str(decision.required_step or "")
        if self._is_hostile_message(clean_text):
            if language.startswith("ar"):
                return "فاهم إنك متضايق. هخليها بسيطة ونكمل خطوة بخطوة. المسافرون شباب ولا بنات؟ اكتب 1 للشباب أو 2 للبنات."
            return "I hear you. I’ll keep this simple and help you step by step. Are the travelers boys/male or girls/female? Reply with 1 for boys or 2 for girls."
        if self._is_identity_question(clean_text):
            if language.startswith("ar"):
                return "أنا مساعد المبيعات بالذكاء الاصطناعي من Rahma Traveler. أراجع الرحلات المؤكدة في CRM وأساعدك في تجهيز طلب الحجز. نكمل: المسافرون شباب ولا بنات؟"
            return "I’m Rahma Traveler’s AI sales assistant. I check verified CRM trips and help prepare your booking request. To continue, are the travelers boys/male or girls/female?"
        if self._is_human_agent_request(clean_text):
            if language.startswith("ar"):
                return "أقدر أحولك لموظف من الفريق. ولو تحب نكمل هنا، محتاج أعرف فقط: المسافرون شباب ولا بنات؟"
            return "I can connect you with a human agent. If you would like to continue here, I only need to know whether the travelers are boys or girls."
        if step == "collect_traveler_gender":
            if self._is_explanation_request(clean_text):
                if language.startswith("ar"):
                    return (
                        f"فاهم سؤالك. بسأل عن نوع المجموعة في {trip_name} لأن توافر الغرف قد يختلف بين الشباب والبنات، "
                        "وعايز أعرض لك المتاح الحقيقي من CRM فقط.\n\n"
                        "اكتب 1 للشباب أو 2 للبنات."
                    )
                return (
                    f"Good question. I need the traveler group for {trip_name} because room availability can differ for boys and girls, "
                    "and I want to show you only the verified CRM options.\n\n"
                    "Reply with 1 for boys or 2 for girls."
                )
            if language.startswith("ar"):
                return (
                    f"ولا يهمك، أنا معاك في حجز {trip_name}. خلينا نكمل بخطوة واحدة: "
                    "المسافرون شباب ولا بنات؟ اكتب 1 للشباب أو 2 للبنات."
                )
            return (
                f"No problem, I’m with you on {trip_name}. Let’s take it one step at a time: "
                "are the travelers boys or girls? Reply with 1 for boys or 2 for girls."
            )
        if step == "collect_room_type":
            if language.startswith("ar"):
                return "تمام، عشان أكمل الحجز محتاج اختيار الغرفة فقط. اكتب اسم الغرفة أو رقمها من الاختيارات الظاهرة."
            return "I’m with you. To continue, I only need the room choice. Reply with the room name or its number."
        if step == "collect_group_size":
            return "كم عدد المسافرين في الطلب؟" if language.startswith("ar") else "How many travelers should I include in the request?"
        if step == "collect_flight_preference":
            return "تحب الرحلة بالطيران أم بدون طيران؟" if language.startswith("ar") else "Would you like the trip with flights or without flights?"
        if step == "collect_passport_attachment":
            return "لنكمل الرحلة الدولية، أرسل صورة أو ملف جواز السفر من فضلك." if language.startswith("ar") else "To continue with this international trip, please attach the passport image or PDF."
        return self._normalize_reply(decision.assistant_message, language)

    @staticmethod
    def _candidate_is_relevant_to_required_step(candidate: str, decision, language: str) -> bool:
        normalized = ToolCallingSessionRuntime._normalize_trip_reference(candidate)
        if not normalized:
            return False
        required_step = str(decision.required_step or "")
        if required_step == "collect_traveler_gender":
            return any(token in normalized for token in ("boys", "male", "girls", "female", "شباب", "بنات"))
        if required_step == "collect_room_type":
            return any(token in normalized for token in ("room", "single", "double", "triple", "غرفة"))
        if required_step == "collect_group_size":
            return any(token in normalized for token in ("traveler", "people", "مسافر", "عدد"))
        if required_step == "collect_flight_preference":
            return any(token in normalized for token in ("flight", "without", "طيران"))
        if required_step == "collect_passport_attachment":
            return any(token in normalized for token in ("passport", "جواز"))
        return bool(language.startswith("ar") == ("".join(ch for ch in candidate if "\u0600" <= ch <= "\u06ff") != ""))

    def _handle_conversational_interruption(self, session: SessionState, decision, clean_text: str) -> bool:
        if not self._is_conversational_interruption(clean_text):
            return False

        session_context = self._build_context(session, clean_text)
        session_context["workflow_policy"] = decision.to_context()
        session_context["conversation_turn_guidance"] = (
            "Answer the customer's actual interruption first. Do not repeat the previous question verbatim. "
            "Briefly explain or acknowledge it, then ask only for the one required field."
        )
        candidate = ""
        if isinstance(self._conversation_ai, GeminiAgent):
            try:
                result = self._conversation_ai.respond(
                    user_message=clean_text,
                    session_context=session_context,
                    conversation_history=session.messages[-12:],
                )
                candidate = self._normalize_reply(str(result.get("reply") or ""), session.language)
                if not self._candidate_is_relevant_to_required_step(candidate, decision, session.language):
                    candidate = ""
            except Exception:
                agent_logger.exception("Conversational interruption recovery failed for session=%s", session.id)

        reply = candidate or self._natural_interruption_fallback(session, decision, clean_text)
        session.messages.append({"role": "assistant", "text": reply})
        session.tools_used = []
        session.fallback_used = not bool(candidate)
        return True

    @staticmethod
    def _last_assistant_text(session: SessionState) -> str:
        for message in reversed(session.messages):
            if isinstance(message, dict) and message.get("role") == "assistant":
                return str(message.get("text") or "")
        return ""

    @staticmethod
    def _trip_detail_summary(trip: dict[str, Any], language: str) -> str:
        trip_name = str(trip.get("trip_name") or "this trip").strip()
        trip_type = str(trip.get("trip_type") or trip.get("type") or "").strip().lower()
        start_date = str(trip.get("start_date") or "").strip()
        end_date = str(trip.get("end_date") or "").strip()
        price = str(trip.get("public_price") or "").strip()
        description = str(trip.get("public_description") or "").strip()
        if language.startswith("ar"):
            type_label = "\u0645\u062d\u0644\u064a\u0629" if trip_type == "local" else "\u062f\u0648\u0644\u064a\u0629" if trip_type == "international" else trip_type
            lines = [f"\u0623\u0643\u064a\u062f. \u062f\u064a \u062a\u0641\u0627\u0635\u064a\u0644 \u0631\u062d\u0644\u0629 {trip_name} \u0627\u0644\u0645\u0624\u0643\u062f\u0629 \u0641\u064a CRM:"]
            if type_label:
                lines.append(f"\u0627\u0644\u0646\u0648\u0639: {type_label}")
            if start_date and end_date:
                lines.append(f"\u0627\u0644\u062a\u0627\u0631\u064a\u062e: \u0645\u0646 {start_date} \u0625\u0644\u0649 {end_date}")
            elif start_date:
                lines.append(f"\u0627\u0644\u062a\u0627\u0631\u064a\u062e: {start_date}")
            if price:
                lines.append(f"\u0627\u0644\u0633\u0639\u0631: {price}")
            if description:
                lines.append(f"\u0627\u0644\u0648\u0635\u0641: {description}")
            return "\n".join(lines)
        type_label = trip_type.title() if trip_type else ""
        lines = [f"Sure. Here are the verified CRM details for {trip_name}:"]
        if type_label:
            lines.append(f"Type: {type_label}")
        if start_date and end_date:
            lines.append(f"Dates: {start_date} to {end_date}")
        elif start_date:
            lines.append(f"Date: {start_date}")
        if price:
            lines.append(f"Price: {price}")
        if description:
            lines.append(f"Description: {description}")
        return "\n".join(lines)

    def _backend_required_step_reply(self, session: SessionState, decision, clean_text: str = "") -> str:
        language = session.language
        current_trip = self._current_trip_for_followup(session)
        trip_name = session.selected_trip_name or str(current_trip.get("trip_name") or "").strip()
        step = str(decision.required_step or "")
        prefix = ""
        if self._is_hostile_message(clean_text):
            prefix = (
                "\u0641\u0627\u0647\u0645\u0643. \u0647\u062e\u0644\u064a \u0627\u0644\u0645\u0648\u0636\u0648\u0639 \u0628\u0633\u064a\u0637 \u0648\u0647\u0633\u0627\u0639\u062f\u0643 \u062e\u0637\u0648\u0629 \u0628\u062e\u0637\u0648\u0629."
                if language.startswith("ar")
                else "I hear you. I will keep this simple and help you step by step."
            )
        elif self._is_identity_question(clean_text):
            prefix = (
                "\u0623\u0646\u0627 \u0645\u0633\u0627\u0639\u062f \u0631\u062d\u0645\u0629 \u062a\u0631\u0627\u0641\u0644 \u0644\u0644\u0645\u0628\u064a\u0639\u0627\u062a. \u0628\u0623\u0631\u0627\u062c\u0639 \u0631\u062d\u0644\u0627\u062a CRM \u0627\u0644\u0645\u0624\u0643\u062f\u0629 \u0648\u0628\u062c\u0647\u0632 \u0637\u0644\u0628 \u0627\u0644\u062d\u062c\u0632 \u0628\u0639\u062f \u062a\u0623\u0643\u064a\u062f\u0643."
                if language.startswith("ar")
                else "I am Rahma Traveler's AI sales assistant. I check verified CRM trips and prepare booking requests after your confirmation."
            )
        elif self._is_human_agent_request(clean_text):
            prefix = (
                "\u0623\u0642\u062f\u0631 \u0623\u062d\u0648\u0644\u0643 \u0644\u0645\u0648\u0638\u0641. \u0648\u0644\u0648 \u062a\u062d\u0628 \u0646\u0643\u0645\u0644 \u0647\u0646\u0627\u060c \u062f\u064a \u0627\u0644\u062e\u0637\u0648\u0629 \u0627\u0644\u0646\u0627\u0642\u0635\u0629:"
                if language.startswith("ar")
                else "I can connect you with a human agent. If you want to continue here, this is the next missing detail:"
            )

        if step == "collect_traveler_gender":
            if language.startswith("ar"):
                trip_part = f" \u0644\u0631\u062d\u0644\u0629 {trip_name}" if trip_name else ""
                prompt = (
                    f"\u0639\u0634\u0627\u0646 \u0623\u0631\u0627\u062c\u0639 \u0627\u0644\u063a\u0631\u0641 \u0627\u0644\u0645\u062a\u0627\u062d\u0629{trip_part}\u060c \u0647\u0644 \u0627\u0644\u0645\u0633\u0627\u0641\u0631\u0648\u0646 \u0634\u0628\u0627\u0628 \u0623\u0645 \u0628\u0646\u0627\u062a\u061f\n"
                    "1. \u0634\u0628\u0627\u0628\n2. \u0628\u0646\u0627\u062a"
                )
            else:
                trip_part = f" for {trip_name}" if trip_name else ""
                prompt = (
                    f"To check the right room availability{trip_part}, are the travelers boys/male or girls/female?\n"
                    "1. Boys / Male\n2. Girls / Female"
                )
        elif step == "collect_group_size":
            prompt = "\u0643\u0645 \u0639\u062f\u062f \u0627\u0644\u0645\u0633\u0627\u0641\u0631\u064a\u0646 \u0641\u064a \u0637\u0644\u0628 \u0627\u0644\u062d\u062c\u0632\u061f" if language.startswith("ar") else "How many travelers should I put on this booking request?"
        elif step == "collect_flight_preference":
            prompt = "\u062a\u062d\u0628 \u0627\u0644\u0631\u062d\u0644\u0629 \u0645\u0639 \u0637\u064a\u0631\u0627\u0646 \u0623\u0645 \u0628\u062f\u0648\u0646 \u0637\u064a\u0631\u0627\u0646\u061f" if language.startswith("ar") else "Do you want this trip with flights or without flights?"
        else:
            prompt = self._normalize_reply(decision.assistant_message, language)

        if prefix:
            return f"{prefix}\n\n{prompt}"
        last_reply = self._last_assistant_text(session)
        fixed_prompt = self._normalize_reply(decision.assistant_message, language)
        if last_reply.strip() == prompt.strip() or last_reply.strip() == fixed_prompt.strip():
            if language.startswith("ar"):
                return f"\u0627\u0644\u062e\u0637\u0648\u0629 \u0627\u0644\u0645\u0647\u0645\u0629 \u0627\u0644\u0622\u0646:\n{prompt}"
            return f"The only detail I still need now is this:\n{prompt}"
        return prompt

    def _handle_trip_details_request_if_ready(self, session: SessionState, clean_text: str) -> bool:
        if not self._is_trip_details_request(clean_text):
            return False
        trip = self._current_trip_for_followup(session)
        if not trip:
            return False
        if str(trip.get("trip_id") or "").strip() and not session.selected_trip_id:
            self._select_trip(session, trip)
        session.messages.append({"role": "user", "text": clean_text})
        session_context = self._build_context(session, clean_text)
        decision = self._workflow_policy.evaluate(session_context)
        details = self._trip_detail_summary(trip, session.language)
        if decision.required_step in _BACKEND_OWNED_COLLECTION_STEPS:
            reply = f"{details}\n\n{self._backend_required_step_reply(session, decision, clean_text)}"
            session.stage = decision.state
        else:
            reply = self._public_trip_reply(trip, session.language)
            session.stage = "public_trip_details"
        session.messages.append({"role": "assistant", "text": self._normalize_reply(reply, session.language)})
        session.tools_used = []
        session.fallback_used = False
        return True

    def _booking_confirmation_summary(self, session: SessionState) -> str:
        trip_name = session.selected_trip_name or session.selected_trip_id or "the selected trip"
        room = " ".join(part for part in (session.room_type, session.room_group) if part).strip() or "not specified"
        flight = session.flight_option or "not specified"
        if session.language.startswith("ar"):
            return (
                "قبل إنشاء طلب الحجز، هذه هي التفاصيل المؤكدة:\n"
                f"الرحلة: {trip_name}\n"
                f"عدد المسافرين: {session.group_size}\n"
                f"الغرفة: {room}\n"
                f"الطيران: {flight}\n"
                "هل تؤكد إنشاء طلب الحجز؟"
            )
        return (
            "Before I create the booking draft, please confirm these details:\n"
            f"Trip: {trip_name}\n"
            f"Travelers: {session.group_size}\n"
            f"Room: {room}\n"
            f"Flight option: {flight}\n"
            "Do you confirm creating the booking draft?"
        )

    def _handle_booking_confirmation_reply(self, session: SessionState, clean_text: str) -> bool:
        if session.stage != "booking_confirmation_required":
            return False
        if self._looks_affirmative(clean_text):
            session.booking_confirmed = True
            session.booking_confirmation_requested = False
            session.stage = "booking_ready"
            return False
        session.messages.append({"role": "user", "text": clean_text})
        if self._looks_negative_confirmation(clean_text):
            session.booking_confirmation_requested = False
            session.booking_confirmed = False
            session.stage = "waiting"
            reply = (
                "تمام، لن أنشئ طلب حجز الآن. أستطيع تعديل التفاصيل أو تحويلك إلى موظف."
                if session.language.startswith("ar")
                else "No problem. I will not create a booking draft now. I can adjust the details or connect you with a human agent."
            )
        else:
            reply = (
                "من فضلك أكد بنعم لإنشاء طلب الحجز، أو لا لإيقافه."
                if session.language.startswith("ar")
                else "Please confirm with yes to create the booking draft, or no to stop."
            )
        session.messages.append({"role": "assistant", "text": reply})
        session.tools_used = []
        session.fallback_used = False
        return True

    def _handle_public_trip_reference_if_present(self, session: SessionState, clean_text: str) -> bool:
        if session.selected_trip_id:
            return False
        search = self._search_trip_reference_candidates(clean_text)
        query = str(search.get("query") or "").strip()
        matches = list(search.get("matches") or [])
        if not query:
            return False
        confident = [item for item in matches if int(item.get("score") or 0) >= 65]
        session.messages.append({"role": "user", "text": clean_text})
        if len(confident) == 1 or (len(confident) > 1 and confident[0]["score"] >= confident[1]["score"] + 5):
            trip = dict(confident[0]["trip"])
            preview = dict(session.preview or {})
            preview["trip_result"] = {
                "open_trips": [trip] if str(trip.get("start_date") or "").strip() else [],
                "date_tbd_trips": [] if str(trip.get("start_date") or "").strip() else [trip],
            }
            preview["trip_reference"] = trip
            session.preview = preview
            self._select_trip(session, trip)
            session.messages.append({"role": "assistant", "text": self._public_trip_reply(trip, session.language)})
            session.tools_used = ["search_trips"]
            session.fallback_used = False
            session.stage = "public_trip_details"
            return True
        if len(confident) > 1:
            matched_trips = [dict(item["trip"]) for item in confident[:5]]
            preview = dict(session.preview or {})
            preview["trip_result"] = {
                "open_trips": [trip for trip in matched_trips if str(trip.get("start_date") or "").strip()],
                "date_tbd_trips": [trip for trip in matched_trips if not str(trip.get("start_date") or "").strip()],
            }
            session.preview = preview
            session.messages.append({"role": "assistant", "text": self._trip_reference_choices_reply(confident, session.language)})
            session.tools_used = ["search_trips"]
            session.fallback_used = False
            session.stage = "trip_selection_required"
            return True
        if (
            self._has_trip_reference_words(clean_text)
            and not self._extract_phone_candidate(clean_text)
            and len(self._trip_reference_tokens(query)) <= 4
        ):
            session.messages.append({"role": "assistant", "text": self._trip_reference_unknown_reply(query, session.language)})
            session.tools_used = ["search_trips"]
            session.fallback_used = False
            session.stage = "trip_selection_required"
            return True
        session.messages.pop()
        return False

    @staticmethod
    def _public_trip_reply(trip: dict[str, Any], language: str) -> str:
        trip_name = str(trip.get("trip_name") or "this trip").strip()
        trip_type = str(trip.get("trip_type") or trip.get("type") or "").strip().lower()
        start_date = str(trip.get("start_date") or "").strip()
        end_date = str(trip.get("end_date") or "").strip()
        price = str(trip.get("public_price") or "").strip()
        description = str(trip.get("public_description") or "").strip()
        if language.startswith("ar"):
            type_label = "محلية" if trip_type == "local" else "دولية" if trip_type == "international" else trip_type
            lines = ["وجدت رحلة مطابقة متاحة حالياً:", f"1) اسم الرحلة: {trip_name}"]
            if type_label:
                lines.append(f"2) النوع: {type_label}")
            if start_date and end_date:
                lines.append(f"{len(lines)}) التاريخ: من {start_date} إلى {end_date}")
            elif start_date:
                lines.append(f"{len(lines)}) التاريخ: {start_date}")
            if price:
                lines.append(f"{len(lines)}) السعر: {price}")
            if description:
                lines.append(f"{len(lines)}) الوصف: {description}")
            lines.append("هل ترغب في متابعة طلب الحجز لهذه الرحلة؟")
            return "\n".join(lines)
        type_label = trip_type.title() if trip_type else ""
        lines = ["I found a matching trip that is currently available:", f"1) Trip: {trip_name}"]
        if type_label:
            lines.append(f"2) Type: {type_label}")
        if start_date and end_date:
            lines.append(f"{len(lines)}) Dates: {start_date} to {end_date}")
        elif start_date:
            lines.append(f"{len(lines)}) Date: {start_date}")
        if price:
            lines.append(f"{len(lines)}) Price: {price}")
        if description:
            lines.append(f"{len(lines)}) Description: {description}")
        lines.append("Would you like to continue with a booking request for this trip?")
        return "\n".join(lines)

    @staticmethod
    def _is_trip_media_request(text: str) -> bool:
        normalized = ToolCallingSessionRuntime._normalize_public_trip_reference(text)
        lowered = str(text or "").casefold()
        passport_terms = ("passport", "جواز", "باسبور", "بطاقة", "id")
        if any(term in lowered for term in passport_terms):
            return False
        media_terms = (
            "photo",
            "picture",
            "pic",
            "image",
            "photos",
            "pictures",
            "pics",
            "gallery",
            "hotel",
            "room",
            "see",
            "show",
            "صورة",
            "صور",
            "فندق",
            "اوتيل",
            "أوتيل",
            "غرفة",
            "الغرفة",
            "ابعت",
            "ارسل",
            "اشوف",
            "أشوف",
        )
        trip_terms = ("trip", "hotel", "room", "رحلة", "الرحلة", "فندق", "غرفة", "الغرفة")
        return any(term in normalized or term in lowered for term in media_terms) and any(
            term in normalized or term in lowered for term in trip_terms
        )

    def _selected_or_referenced_trip_for_media(self, session: SessionState, text: str) -> dict[str, Any]:
        selected = self._selected_trip(session)
        if selected:
            return selected
        if session.selected_trip_id:
            try:
                detail = self._read_only_tools.get_trip_details(trip_id=session.selected_trip_id)
            except Exception:
                detail = {}
            trip = detail.get("trip") if isinstance(detail, dict) and isinstance(detail.get("trip"), dict) else {}
            if trip:
                return dict(trip)
        public_trip = self._resolve_public_trip_reference(session, text)
        if public_trip:
            session.selected_trip_id = str(public_trip.get("trip_id") or "").strip()
            session.selected_trip_name = str(public_trip.get("trip_name") or session.selected_trip_name or "").strip()
            self._update_collection_state(session, selected_trip=True, destination=bool(session.selected_trip_name))
            return dict(public_trip)
        return {}

    @staticmethod
    def _trip_media_reply(media_result: dict[str, Any], trip: dict[str, Any], language: str) -> str:
        trip_name = str(trip.get("trip_name") or trip.get("trip_id") or "this trip").strip()
        media = list(media_result.get("media") or []) if isinstance(media_result, dict) else []
        if not media:
            if language.startswith("ar"):
                return f"لا توجد صور رسمية مؤكدة في CRM لرحلة {trip_name} حاليا."
            return f"There are no verified official CRM images for {trip_name} yet."
        if language.startswith("ar"):
            lines = [f"هذه الصور الرسمية المؤكدة من CRM لرحلة {trip_name}:"]
            for index, item in enumerate(media[:5], start=1):
                label = "الصورة الرئيسية" if item.get("image_type") == "cover" else "صورة إضافية"
                lines.append(f"{index}) {label}: {item.get('alt_text') or 'Official trip image'}")
            return "\n".join(lines)
        lines = [f"Here are the verified official CRM images for {trip_name}:"]
        for index, item in enumerate(media[:5], start=1):
            label = "Cover image" if item.get("image_type") == "cover" else "Gallery image"
            lines.append(f"{index}) {label}: {item.get('alt_text') or 'Official trip image'}")
        return "\n".join(lines)

    @staticmethod
    def _media_from_agent_result(result: dict[str, Any]) -> list[dict[str, Any]]:
        media: list[dict[str, Any]] = []
        for event in result.get("tool_requests", []) if isinstance(result, dict) else []:
            if not isinstance(event, dict) or event.get("name") != "get_trip_media":
                continue
            tool_result = event.get("result") if isinstance(event.get("result"), dict) else {}
            for item in tool_result.get("media", []) if isinstance(tool_result.get("media"), list) else []:
                if isinstance(item, dict) and (item.get("public_url") or item.get("url")):
                    media.append(dict(item))
        return media

    @staticmethod
    def _strip_media_urls(text: str) -> str:
        cleaned = _TRIP_MEDIA_URL_RE.sub("", str(text or ""))
        cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    @classmethod
    def _assistant_message(cls, *, text: str, language: str, media: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "text": text}
        safe_media = [dict(item) for item in (media or []) if isinstance(item, dict) and (item.get("public_url") or item.get("url"))]
        if safe_media:
            message["text"] = cls._strip_media_urls(text) or (
                "هذه الصورة الرسمية المؤكدة من CRM."
                if str(language or "").startswith("ar")
                else "Here is the verified official CRM trip image."
            )
            message["media"] = safe_media
        return message

    def _handle_trip_media_request_if_ready(self, session: SessionState, clean_text: str) -> bool:
        if not self._preview_has_verified_traveler(session):
            return False
        trip = self._selected_or_referenced_trip_for_media(session, clean_text)
        session.messages.append({"role": "user", "text": clean_text})
        if not trip:
            reply = (
                "حدد اسم الرحلة أولا، وسأعرض الصور الرسمية الموجودة في CRM."
                if session.language.startswith("ar")
                else "Please tell me which trip you mean first, and I will show the official CRM images."
            )
            session.messages.append({"role": "assistant", "text": reply})
            session.tools_used = []
            session.fallback_used = False
            session.stage = "trip_media_shared"
            return True
        trip_id = str(trip.get("trip_id") or session.selected_trip_id or "").strip()
        media_result = self._read_only_tools.get_trip_media(trip_id=trip_id)
        reply = self._trip_media_reply(media_result, trip, session.language)
        message = {"role": "assistant", "text": reply}
        media = list(media_result.get("media") or []) if isinstance(media_result, dict) else []
        if media:
            message["media"] = media
        session.messages.append(message)
        session.tools_used = ["get_trip_media"]
        session.fallback_used = False
        session.stage = "trip_media_shared"
        return True

    def handle_message(self, session: SessionState, text: str, gateway) -> SessionState:
        if session.agent_mode != "tool_calling":
            raise RuntimeError("Session mode mismatch for tool-calling runtime.")

        clean_text = str(text or "").strip()
        if not clean_text:
            return session

        detected = detect_language(clean_text)
        if detected == "ar":
            session.language = "ar"
        elif session.language == "ar" and self._explicit_english_request(clean_text):
            session.language = "en"
        elif session.language != "ar":
            session.language = "en"

        if self._handle_booking_confirmation_reply(session, clean_text):
            return session

        identity_response = self._identity_policy.evaluate(clean_text)
        if identity_response is not None:
            session.language = identity_response.language
            session.messages.append({"role": "user", "text": clean_text})
            session.messages.append({"role": "assistant", "text": identity_response.text})
            session.tools_used = []
            session.fallback_used = False
            agent_logger.info(
                "Tool-calling session %s answered %s from backend identity policy",
                session.id,
                identity_response.intent,
            )
            return session

        if self._conversation_ai is None:
            raise RuntimeError("Tool-calling agent runtime is not configured.")

        capture_stage = session.stage
        step_value_captured = self._apply_required_step_capture(session, clean_text)
        if not step_value_captured:
            privacy_context = self._build_context(session, clean_text)
            privacy_response = self._privacy_policy.evaluate_user_message(clean_text, privacy_context)
            if privacy_response is not None:
                session.language = privacy_response.language
                session.messages.append({"role": "user", "text": clean_text})
                session.messages.append({"role": "assistant", "text": privacy_response.text})
                session.tools_used = []
                session.fallback_used = False
                agent_logger.warning(
                    "Tool-calling session %s blocked %s before model",
                    session.id,
                    privacy_response.intent,
                )
                return session

        hints = self._extract_hints(clean_text, stage=capture_stage)
        self._merge_hints(session, hints)
        self._apply_trip_selection_from_text(session, clean_text)
        media_intent = self._is_trip_media_request(clean_text)
        preloaded_tool_event = self._run_identity_lookup_if_ready(session) if media_intent else None
        if media_intent and self._handle_trip_media_request_if_ready(session, clean_text):
            agent_logger.info("Tool-calling session %s returned verified trip media from CRM", session.id)
            return session
        if not media_intent and self._handle_trip_details_request_if_ready(session, clean_text):
            agent_logger.info("Tool-calling session %s answered trip follow-up details from session CRM context", session.id)
            return session
        if not media_intent and self._handle_public_trip_reference_if_present(session, clean_text):
            agent_logger.info("Tool-calling session %s resolved public trip reference from CRM", session.id)
            return session
        if preloaded_tool_event is None:
            preloaded_tool_event = self._run_identity_lookup_if_ready(session)
        self._run_trip_reference_lookup_if_ready(session, clean_text)
        agent_state = self._state_by_session.setdefault(session.id, AgentState(goal="help the traveler plan a trip"))
        agent_state.customer_intent = clean_text
        self._memory.update_short_term(
            language=session.language,
            raw_phone=session.raw_phone,
            trip_type=session.trip_type,
            selected_trip_id=session.selected_trip_id,
            room_type=session.room_type,
            flight_option=session.flight_option,
            currency=session.currency,
            customer_intent=clean_text,
        )

        session.messages.append({"role": "user", "text": clean_text})
        session.stage = "collecting_context"

        session_context = self._build_context(session, clean_text)
        session_context.update(hints)
        preview = self._preview_from_context(session_context)
        if preview:
            session.preview = {**dict(session.preview or {}), **preview}
        workflow_decision = self._workflow_policy.evaluate(session_context)
        session_context["workflow_policy"] = workflow_decision.to_context()
        session.stage = workflow_decision.state

        if workflow_decision.handoff_required:
            if self._execute_policy_handoff(session, session_context, workflow_decision):
                return session
            session.messages.append(
                {
                    "role": "assistant",
                    "text": self._normalize_reply(workflow_decision.assistant_message, session.language),
                }
            )
            session.tools_used = [str(preloaded_tool_event["name"])] if preloaded_tool_event else []
            session.fallback_used = False
            agent_logger.error(
                "Required handoff could not be persisted session=%s state=%s",
                session.id,
                workflow_decision.state,
            )
            return session

        if workflow_decision.required_step in _BACKEND_OWNED_COLLECTION_STEPS:
            if not step_value_captured and self._handle_conversational_interruption(
                session,
                workflow_decision,
                clean_text,
            ):
                session.stage = workflow_decision.state
                agent_logger.info(
                    "Tool-calling session %s handled conversational interruption step=%s",
                    session.id,
                    workflow_decision.required_step,
                )
                return session
            session.messages.append(
                {
                    "role": "assistant",
                    "text": self._normalize_reply(
                        self._backend_required_step_reply(session, workflow_decision, clean_text),
                        session.language,
                    ),
                }
            )
            session.tools_used = [str(preloaded_tool_event["name"])] if preloaded_tool_event else []
            session.fallback_used = False
            agent_logger.info(
                "Tool-calling session %s used backend workflow prompt step=%s selected_trip=%s",
                session.id,
                workflow_decision.required_step,
                session.selected_trip_id,
            )
            return session

        if workflow_decision.required_step == "create_booking_draft" and not session.booking_confirmed:
            session.booking_confirmation_requested = True
            session.stage = "booking_confirmation_required"
            session.messages.append({"role": "assistant", "text": self._booking_confirmation_summary(session)})
            session.tools_used = [str(preloaded_tool_event["name"])] if preloaded_tool_event else []
            session.fallback_used = False
            agent_logger.info("Tool-calling session %s requested booking confirmation before write", session.id)
            return session

        preloaded_tool_results = [preloaded_tool_event] if preloaded_tool_event else []
        turn = self._coordinator.think(agent_state, crm_facts=session_context, conversation=session.messages[-12:], tool_results=preloaded_tool_results)
        agent_state.subgoal = str(turn.decision.get("reason") or "")
        session_context["persona"] = turn.context.get("persona")
        session_context["memory"] = turn.context.get("memory")
        session_context["agent_state"] = agent_state.to_dict()
        session_context["system_constraints"] = turn.context.get("system_constraints")
        session_context["available_tools"] = [tool.name for tool in self._coordinator.tool_manager.describe()]
        agent_logger.info("Coordinator entered session=%s", session.id)
        agent_logger.info("Persona loaded session=%s", session.id)
        agent_logger.info("Memory loaded session=%s", session.id)
        agent_logger.info("State updated session=%s", session.id)
        agent_logger.info("Context built session=%s", session.id)
        agent_logger.info("Planner decision session=%s action=%s tool=%s", session.id, turn.decision.get("action"), turn.decision.get("tool_name"))

        result = self._conversation_ai.respond(
            user_message=clean_text,
            session_context=session_context,
            conversation_history=session.messages[-12:],
        )

        reply = self._normalize_reply(str(result.get("reply") or ""), session.language)
        error = str(result.get("error") or "").strip()
        if error:
            agent_logger.warning("Tool-calling session %s failed: %s", session.id, error)
            session.stage = "error"
            agent_state.add_pending_task("recover from model error")
            session.messages.append(
                {
                    "role": "assistant",
                    "text": "I’m having trouble completing that request right now. Please try again in a moment.",
                }
            )
            session.fallback_used = True
            return session

        media = self._media_from_agent_result(result)
        session.messages.append(self._assistant_message(text=reply, language=session.language, media=media))
        session.tools_used = [
            str(event.get("name") or "").strip()
            for event in result.get("tool_requests", [])
            if isinstance(event, dict) and str(event.get("name") or "").strip()
        ]
        if preloaded_tool_event and preloaded_tool_event.get("name"):
            session.tools_used = [str(preloaded_tool_event["name"]), *[tool for tool in session.tools_used if tool != preloaded_tool_event["name"]]]
        session.fallback_used = False

        self._apply_result(session, result)
        for event in [*preloaded_tool_results, *list(result.get("tool_requests", []))]:
            if not isinstance(event, dict):
                continue
            tool_name = str(event.get("name") or "").strip()
            tool_result = event.get("result") if isinstance(event.get("result"), dict) else {}
            if tool_name and tool_result:
                safety_ok, safety_error = self._coordinator.safety.validate_tool_args(
                    tool_name,
                    event.get("input") if isinstance(event.get("input"), dict) else {},
                    allowed_tools=set(self._coordinator.tool_manager.registry.keys()),
                )
                agent_logger.info("Safety result session=%s tool=%s ok=%s", session.id, tool_name, safety_ok)
                if not safety_ok:
                    agent_logger.warning("Tool-calling session %s safety rejected tool=%s reason=%s", session.id, tool_name, safety_error)
                    continue
                observation = self._coordinator.observe(agent_state, tool_name, tool_result)
                agent_state.last_observation = str(observation.get("label") or "")
                agent_state.mark_task_completed(tool_name)
        final_context = self._build_context(session, clean_text)
        session.stage = self._workflow_policy.evaluate(final_context).state
        self._memory.update_short_term(
            last_goal=agent_state.goal,
            last_subgoal=agent_state.subgoal,
            last_tool=agent_state.last_tool,
            last_observation=agent_state.last_observation,
        )
        if session.customer_name:
            preview = session.preview if isinstance(session.preview, dict) else {}
            traveler = preview.get("traveler") if isinstance(preview.get("traveler"), dict) else {}
            self._memory.update_long_term(customer_name=session.customer_name, traveler_id=str(traveler.get("traveler_id") or ""))
        agent_logger.info("Final response completed session=%s", session.id)
        return session

    @staticmethod
    def _policy_handoff_details(session_context: dict[str, Any], decision) -> tuple[str, str, str]:
        traveler = session_context.get("known_traveler") if isinstance(session_context.get("known_traveler"), dict) else {}
        status = str(traveler.get("status") or decision.verified_status or "").strip().casefold()
        if status in {"blacklisted", "blacklist", "blocked"}:
            return (
                "blacklisted_customer",
                "Critical",
                "Restricted traveler identity matched by the backend CRM workflow policy.",
            )
        if status in {"inactive", "archived"}:
            return (
                "archived_traveler",
                "Medium",
                "Archived or inactive traveler identity requires employee review.",
            )
        if decision.state == "duplicate_traveler_detected":
            return (
                "duplicate_phone_match",
                "High",
                "The WhatsApp number matches multiple CRM traveler profiles.",
            )
        if decision.required_step == "create_capacity_handoff":
            return (
                "room_capacity",
                "High",
                "Room inventory is insufficient for the requested traveler group.",
            )
        reason = str(decision.reason or "manual_review").strip().split(":", 1)[0]
        return reason or "manual_review", "High", "Backend workflow policy requires employee review."

    def _execute_policy_handoff(
        self,
        session: SessionState,
        session_context: dict[str, Any],
        decision,
    ) -> bool:
        reason_code, priority, agent_summary = self._policy_handoff_details(session_context, decision)
        payload = {
            "traveler_id": session_context.get("traveler_id") or "",
            "raw_phone": session.raw_phone or session.pending_raw_phone,
            "country_code": session.country_code or self.settings.default_country_code,
            "lead_id": session_context.get("lead_id") or "",
            "trip_id": session.selected_trip_id,
            "flow_key": f"tool_calling:{session.id}",
            "reason_code": reason_code,
            "reason_text": decision.assistant_message,
            "priority": priority,
            "channel": "web",
            "customer_name": session.customer_name,
            "agent_summary": agent_summary,
            "customer_summary": decision.assistant_message,
            "notes": f"Backend workflow policy: {decision.reason or decision.state}",
            "update_lead": True,
            "deduplicate_open": True,
        }
        try:
            result = self._write_executor.execute(
                action="create_handoff",
                payload=payload,
                session_context=session_context,
            )
        except Exception as exc:
            agent_logger.warning("Policy handoff could not be created session=%s error=%s", session.id, exc)
            return False
        if not isinstance(result, dict) or not result.get("executed"):
            return False
        reply = self._normalize_reply(str(decision.assistant_message or result.get("assistant_message") or ""), session.language)
        session.messages.append({"role": "assistant", "text": reply})
        session.tools_used = ["create_handoff"]
        session.fallback_used = False
        self._apply_result(session, {"write_results": [result], "tool_requests": []})
        session.handoff_state = "handed_off"
        if not isinstance(session.final_result, dict) or not session.final_result.get("handoff_id"):
            traveler = session_context.get("known_traveler")
            session.final_result = {
                "traveler": traveler if isinstance(traveler, dict) else None,
                "lead_id": str(session_context.get("lead_id") or ""),
                "handoff_id": str(result.get("result_id") or ""),
                "handoff_required": True,
                "handoff_reason": reason_code,
                "write_result": {
                    "handoff_case": result.get("handoff_case")
                    if isinstance(result.get("handoff_case"), dict)
                    else {}
                },
            }
        session.stage = decision.state
        agent_logger.info(
            "Backend policy handoff persisted session=%s handoff=%s reason=%s deduplicated=%s",
            session.id,
            result.get("result_id", ""),
            reason_code,
            bool((result.get("handoff_case") or {}).get("deduplicated")),
        )
        return True

    def _execute_capacity_handoff(
        self,
        session: SessionState,
        session_context: dict[str, Any],
        decision,
    ) -> bool:
        return self._execute_policy_handoff(session, session_context, decision)

    def handle_passport_attachment(self, session: SessionState, attachment_ref: str) -> None:
        session.passport_attachment_ref = str(attachment_ref or "").strip()
        if session.passport_attachment_ref:
            session.stage = "awaiting_passport_upload"
        agent_logger.info("Tool-calling session %s: passport attachment recorded -> %s", session.id, session.passport_attachment_ref)

    def _apply_result(self, session: SessionState, result: dict[str, Any]) -> None:
        write_results = result.get("write_results") if isinstance(result, dict) else []
        if isinstance(write_results, list):
            for event in write_results:
                if not isinstance(event, dict) or not event.get("executed"):
                    continue
                session_update = event.get("session_update")
                if not isinstance(session_update, dict):
                    continue
                if "lead_status" in session_update:
                    session.lead_status = str(session_update.get("lead_status") or session.lead_status or "")
                if "booking_status" in session_update:
                    session.booking_status = str(session_update.get("booking_status") or session.booking_status or "")
                if "handoff_state" in session_update:
                    session.handoff_state = str(session_update.get("handoff_state") or session.handoff_state or "")
                if "stage" in session_update:
                    session.stage = str(session_update.get("stage") or session.stage or "")
                if "booking_result" in session_update and isinstance(session_update.get("booking_result"), dict):
                    session.booking_result = dict(session_update["booking_result"])
                if "final_result" in session_update and isinstance(session_update.get("final_result"), dict):
                    session.final_result = dict(session_update["final_result"])
        if isinstance(result.get("tool_requests"), list):
            for event in result["tool_requests"]:
                if not isinstance(event, dict):
                    continue
                tool_name = str(event.get("name") or "").strip()
                tool_result = event.get("result") if isinstance(event.get("result"), dict) else {}
                if tool_name == "find_traveler_by_phone":
                    self._store_identity_result(session, tool_result)
                if tool_name == "get_traveler_profile":
                    traveler = tool_result.get("traveler") if isinstance(tool_result.get("traveler"), dict) else {}
                    if traveler:
                        preview = dict(session.preview or {})
                        preview["traveler"] = traveler
                        preview["workflow"] = {
                            **dict(preview.get("workflow") or {}),
                            "verified_traveler": traveler,
                            "verified_status": str(traveler.get("status") or "").strip(),
                            "identity_verified": bool(traveler.get("traveler_id") and traveler.get("status")),
                        }
                        session.preview = preview
                if tool_name == "get_traveler_trip_history":
                    session.final_result = {**dict(session.final_result or {}), "trip_history": tool_result}
                if (
                    tool_name == "search_available_trips"
                    and not session.selected_trip_id
                    and tool_result.get("status") != "workflow_blocked"
                    and event.get("executed", True) is not False
                ):
                    preview = dict(session.preview or {})
                    preview["trip_result"] = {
                        "open_trips": list(tool_result.get("open_trips") or []),
                        "date_tbd_trips": list(tool_result.get("date_tbd_trips") or []),
                    }
                    session.preview = preview

    @staticmethod
    def _preview_from_context(session_context: dict[str, Any]) -> dict[str, Any]:
        traveler = session_context.get("known_traveler") if isinstance(session_context.get("known_traveler"), dict) else {}
        preview: dict[str, Any] = {}
        if traveler:
            preview["traveler"] = traveler
        return preview

    @staticmethod
    def _extract_phone_candidate(text: str) -> str:
        normalized = str(text or "").translate(_DIGIT_TRANSLATION)
        match = _PHONE_CANDIDATE_RE.search(normalized)
        return match.group(0).strip() if match else ""

    @staticmethod
    def _extract_hints(text: str, *, stage: str = "") -> dict[str, Any]:
        lowered = text.strip().lower()
        numeric_option = bool(re.fullmatch(r"\s*[12][\s.)_\-]*", text))
        trip_type = normalize_trip_type(text) if stage == "trip_type_required" or not numeric_option else ""
        raw_phone = ToolCallingSessionRuntime._extract_phone_candidate(text)
        group_size = 0
        group_context = stage == "group_size_required" or any(
            token in lowered
            for token in ("traveler", "travelers", "person", "people", "group", "we are", "passenger", "passengers")
        )
        if group_context:
            for token, value in {
                "one": 1,
                "two": 2,
                "three": 3,
                "four": 4,
                "five": 5,
                "six": 6,
                "seven": 7,
                "eight": 8,
                "nine": 9,
            }.items():
                if token in lowered:
                    group_size = value
                    break
            if not group_size:
                match = re.search(r"\b([1-9][0-9]?)\b", lowered)
                if match:
                    group_size = int(match.group(1))
        room_type = ""
        if "single" in lowered or "فرد" in lowered:
            room_type = "Single"
        elif "double" in lowered or "دابل" in lowered or "مزدوج" in lowered:
            room_type = "Double"
        elif "triple" in lowered or "تريبل" in lowered or "ثلاث" in lowered:
            room_type = "Triple"
        room_group = ""
        if "girls" in lowered or "بنات" in lowered:
            room_group = "girls"
        elif "boys" in lowered or "اولاد" in lowered or "رجال" in lowered:
            room_group = "boys"
        flight_context = stage == "flight_option_required" or any(
            token in lowered for token in ("flight", "flights", "طيران")
        )
        flight_option = normalize_flight_option(text) if flight_context else ""
        if flight_context and ("without flight" in lowered or "without flights" in lowered or "بدون طيران" in lowered):
            flight_option = "Without Flight"
        elif flight_context and ("with flight" in lowered or "with flights" in lowered or "مع طيران" in lowered):
            flight_option = "With Flight"
        return {
            "candidate_trip_type": trip_type or ("local"
            if "local" in lowered or "داخلي" in lowered
            else (
                "international"
                if "international" in lowered or "دولي" in lowered or "عمرة" in lowered or "turkey" in lowered or "تركيا" in lowered
                else ""
            )),
            "candidate_group_size": group_size,
            "candidate_raw_phone": raw_phone,
            "candidate_destination": "Turkey" if "turkey" in lowered or "تركيا" in lowered else "",
            "candidate_preferred_date": "August" if "august" in lowered or "أغسطس" in lowered else "",
            "candidate_birthday": ToolCallingSessionRuntime._extract_birthday(text),
            "candidate_nationality": ToolCallingSessionRuntime._extract_nationality_hint(text),
            "candidate_flight_option": flight_option,
            "candidate_currency": (
                "USD"
                if any(token in lowered for token in ("usd", "dollar", "dollars", "$"))
                else (
                    "EGP"
                    if any(token in lowered for token in ("egp", "egyptian pound", "egyptian pounds", "pound", "pounds", "\u062c\u0646\u064a\u0647", "\u0645\u0635\u0631\u064a"))
                    else ""
                )
            ),
            "candidate_room_type": room_type,
            "candidate_room_group": room_group,
            "candidate_trip_search": any(token in lowered for token in ("trip", "trips", "رحلة", "رحلات", "travel", "available")),
            "candidate_human_request": any(token in lowered for token in ("human", "agent", "person", "موظف", "إنسان", "انسان")),
            "candidate_requires_whatsapp_for_crm": any(token in lowered for token in ("profile", "traveler", "booking", "lead", "crm", "مليف", "بروفايل", "ملفي")),
        }

    def _merge_hints(self, session: SessionState, hints: dict[str, Any]) -> None:
        trip_type = str(hints.get("candidate_trip_type") or "").strip()
        if session.selected_trip_id and trip_type and trip_type != self._effective_trip_type(session):
            agent_logger.warning(
                "Ignored conflicting trip-type hint session=%s selected_trip=%s current_type=%s candidate_type=%s",
                session.id,
                session.selected_trip_id,
                self._effective_trip_type(session),
                trip_type,
            )
            trip_type = ""
        if trip_type and session.trip_type != trip_type:
            session.trip_type = trip_type
            session.selected_trip_id = ""
            preview = dict(session.preview or {})
            preview.pop("trip_result", None)
            session.preview = preview
            self._update_collection_state(session, selected_trip=False)
        if trip_type:
            self._update_collection_state(session, trip_type=True)

        group_size = int(hints.get("candidate_group_size") or 0)
        if group_size:
            session.group_size = group_size
            self._update_collection_state(session, group_size=True)

        destination = str(hints.get("candidate_destination") or "").strip()
        if destination and not session.selected_trip_id:
            session.selected_trip_name = destination
            self._update_collection_state(session, destination=True)

        preferred_date = str(hints.get("candidate_preferred_date") or "").strip()
        if preferred_date:
            session.preferred_date = preferred_date
            self._update_collection_state(session, preferred_date=True)

        birthday = str(hints.get("candidate_birthday") or "").strip()
        if birthday:
            session.birthday = birthday
            self._update_collection_state(session, birthday=True)

        nationality = str(hints.get("candidate_nationality") or "").strip()
        if nationality:
            session.nationality = nationality
            self._update_collection_state(session, nationality=True)

        flight_option = str(hints.get("candidate_flight_option") or "").strip()
        if flight_option:
            session.flight_option = flight_option
            self._update_collection_state(session, flight_option=True)

        currency = str(hints.get("candidate_currency") or "").strip()
        if currency:
            session.currency = currency
            self._update_collection_state(session, currency=True)

        room_type = str(hints.get("candidate_room_type") or "").strip()
        if room_type:
            session.room_type = room_type
            self._update_collection_state(session, room_type=True)
        room_group = str(hints.get("candidate_room_group") or "").strip()
        if room_group:
            session.room_group = room_group
            self._update_collection_state(session, room_group=True)

        raw_phone = str(hints.get("candidate_raw_phone") or "").strip()
        if raw_phone:
            session.raw_phone = raw_phone
            session.pending_raw_phone = raw_phone
            if not session.country_code:
                session.country_code = "20"

    @staticmethod
    def _extract_birthday(text: str) -> str:
        match = _BIRTHDAY_RE.search(str(text or "").strip())
        if not match:
            return ""
        if match.group(1):
            year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        else:
            day, month, year = int(match.group(4)), int(match.group(5)), int(match.group(6))
        if year < 1900 or year > 2100:
            return ""
        if month < 1 or month > 12 or day < 1 or day > 31:
            return ""
        return f"{year:04d}-{month:02d}-{day:02d}"

    @staticmethod
    def _extract_nationality_hint(text: str) -> str:
        lowered = " ".join(str(text or "").strip().lower().split())
        aliases = {
            "egyptian": "Egyptian",
            "egypt": "Egyptian",
            "saudi": "Saudi",
            "saudi arabian": "Saudi",
            "american": "American",
            "usa": "American",
            "british": "British",
        }
        if lowered in aliases:
            return aliases[lowered]
        match = re.search(r"\b(?:my nationality is|nationality is|i am)\s+([a-z][a-z\s-]{2,40})\b", lowered)
        if match:
            return " ".join(part.capitalize() for part in match.group(1).split())
        return ""

    def _available_room_types(self, session: SessionState) -> list[str]:
        trip = self._selected_trip(session)
        if not trip:
            return []

        def available(key: str) -> int:
            try:
                return max(0, int(trip.get(key) or 0))
            except (TypeError, ValueError):
                return 0

        options: list[str] = []
        if available("available_single"):
            options.append("Single")

        group = str(session.room_group or "").strip().lower()
        double_key = f"{group}_double" if group in {"boys", "girls"} else "available_double"
        triple_key = f"{group}_triple" if group in {"boys", "girls"} else "available_triple"
        if available(double_key) or (double_key not in trip and available("available_double")):
            options.append("Double")
        if available(triple_key) or (triple_key not in trip and available("available_triple")):
            options.append("Triple")
        return options

    def _apply_required_step_capture(self, session: SessionState, text: str) -> bool:
        normalized_text = str(text or "").translate(_DIGIT_TRANSLATION).strip()
        option_match = re.fullmatch(r"([1-9])[\s.)_\-]*", normalized_text)
        option_number = int(option_match.group(1)) if option_match else 0
        lowered = " ".join(normalized_text.casefold().split())

        if session.stage == "trip_type_required":
            trip_type = {1: "local", 2: "international"}.get(option_number) or normalize_trip_type(normalized_text)
            if trip_type:
                session.trip_type = trip_type
                self._update_collection_state(session, trip_type=True)
                return True
            return False

        if session.stage == "traveler_not_found" and not session.customer_name:
            name = str(text or "").strip()
            if name and len(name) <= 80 and not _PHONE_CANDIDATE_RE.search(name):
                session.customer_name = name
                self._update_collection_state(session, customer_name=True)
                return True
            return False
        if session.stage == "traveler_gender_required" and not session.room_group:
            if option_number == 1 or lowered in {"boys", "boy", "male", "men", "man", "شباب", "اولاد", "أولاد", "رجال"} or "boys" in lowered or "male" in lowered:
                session.room_group = "boys"
                self._update_collection_state(session, room_group=True)
                return True
            if option_number == 2 or lowered in {"girls", "girl", "female", "women", "woman", "بنات", "نساء"} or "girls" in lowered or "female" in lowered:
                session.room_group = "girls"
                self._update_collection_state(session, room_group=True)
                return True
            return False
        if session.stage == "room_type_required" and not session.room_type:
            available_types = self._available_room_types(session)
            room_type = ""
            if option_number and option_number <= len(available_types):
                room_type = available_types[option_number - 1]
            else:
                aliases = {"single": "Single", "double": "Double", "triple": "Triple"}
                room_type = next((value for token, value in aliases.items() if token in lowered), "")
                if room_type not in available_types:
                    room_type = ""
            if room_type:
                session.room_type = room_type
                self._update_collection_state(session, room_type=True)
                return True
            return False
        if session.stage == "group_size_required":
            group_size = option_number
            if not group_size:
                group_size = int(self._extract_hints(normalized_text, stage="group_size_required").get("candidate_group_size") or 0)
            if group_size:
                session.group_size = group_size
                self._update_collection_state(session, group_size=True)
                return True
            return False
        if session.stage == "flight_option_required" and not session.flight_option:
            flight_option = normalize_flight_option(normalized_text)
            if flight_option:
                session.flight_option = flight_option
                self._update_collection_state(session, flight_option=True)
                return True
            return False
        if session.stage == "birthday_required" and not session.birthday:
            birthday = self._extract_birthday(text)
            if birthday:
                session.birthday = birthday
                self._update_collection_state(session, birthday=True)
                return True
            return False
        if session.stage == "nationality_required" and not session.nationality:
            nationality = self._extract_nationality_hint(text) or str(text or "").strip()
            if nationality and not _PHONE_CANDIDATE_RE.search(nationality):
                session.nationality = nationality[:60]
                self._update_collection_state(session, nationality=True)
                return True
            return False
        if session.stage == "currency_required" and not session.currency:
            lowered = " ".join(str(text or "").strip().lower().split())
            if any(token in lowered for token in ("usd", "dollar", "dollars", "$", "2")):
                session.currency = "USD"
                self._update_collection_state(session, currency=True)
                return True
            elif any(token in lowered for token in ("egp", "egyptian pound", "egyptian pounds", "pound", "pounds", "\u062c\u0646\u064a\u0647", "\u0645\u0635\u0631\u064a", "1")):
                session.currency = "EGP"
                self._update_collection_state(session, currency=True)
                return True
            return False
        return False
