from __future__ import annotations

import re
import uuid
from dataclasses import replace
from typing import Any

from services.ai_agent.ai_agent_app.agent.gemini_agent import GeminiAgent
from services.ai_agent.ai_agent_app.agent.agent_state import AgentState
from services.ai_agent.ai_agent_app.agent.context_builder import ContextBuilder
from services.ai_agent.ai_agent_app.agent.date_parsing import normalize_birthdate_input
from services.ai_agent.ai_agent_app.agent.identity_policy import AgentIdentityPolicy
from services.ai_agent.ai_agent_app.agent.memory import AgentMemory
from services.ai_agent.ai_agent_app.agent.persona import AgentPersona
from services.ai_agent.ai_agent_app.agent.planner import AgentPlanner
from services.ai_agent.ai_agent_app.agent.privacy_policy import AgentPrivacyPolicy
from services.ai_agent.ai_agent_app.agent.response_format import format_agent_reply, response_completeness_issue
from services.ai_agent.ai_agent_app.agent.response_guard import response_guard_issue
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
    "identity_lookup_pending": "Checking records",
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
    "checking_crm": "Checking records",
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
    "country", "destination", "from", "go", "in", "interested", "visit", "visiting",
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
    "collect_valid_whatsapp_number",
    "collect_new_traveler_name",
    "collect_nationality",
    "collect_birthday",
    "collect_payment_currency",
    "select_trip",
    "collect_traveler_gender",
    "collect_room_type",
    "collect_group_size",
    "collect_flight_preference",
    "collect_passport_attachment",
}

_ABUSIVE_OR_HOSTILE_RE = re.compile(
    r"\b(?:fuck|f\W*u\W*c\W*k|shit|stupid|idiot|dumb|bad bot|"
    r"غبي|غبية|أحمق|احمق|حمار|كلب|زفت|خرا|خره|قرف|مقرف|تافه|وسخ|حقير)\b",
    re.IGNORECASE,
)
_NAME_TOKEN_RE = re.compile(r"^[A-Za-z\u0600-\u06FF]+(?:[-'][A-Za-z\u0600-\u06FF]+)*$")


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
                "text": self._agent_reply(
                    session,
                    message_key="session.opening",
                    base_text=self._opening_message(),
                    required_action="Ask for the traveler's WhatsApp number before continuing.",
                ),
                "state": "completed",
            }
        )
        self._sessions[session.id] = session
        return session

    def _opening_message(self) -> str:
        return (
            f"Hi, I'm {self.agent_persona_name} from Ravel Traveler! I'd love to help you plan your trip. "
            "Could you share your WhatsApp number first so I can pull up your profile safely, then we'll get started?"
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

    @staticmethod
    def _conversation_memory(session: SessionState, user_text: str = "") -> dict[str, Any]:
        """Keep session facts available without sending an unbounded transcript to the model."""

        user_messages = [
            str(message.get("text") or "").strip()[:240]
            for message in session.messages
            if isinstance(message, dict) and message.get("role") == "user"
        ]
        current_message = str(user_text or "").strip()[:240]
        if current_message and (not user_messages or user_messages[-1] != current_message):
            user_messages.append(current_message)
        return {
            "turn_count": len(user_messages),
            "pending_stage": session.stage,
            "pending_question": session.stage,
            "confirmed_facts": {
                "customer_name": session.customer_name,
                "raw_phone": session.raw_phone,
                "nationality": session.nationality,
                "birthday": session.birthday,
                "currency": session.currency,
                "trip_type": session.trip_type,
                "selected_trip_id": session.selected_trip_id,
                "selected_trip_name": session.selected_trip_name,
                "room_group": session.room_group,
                "room_type": session.room_type,
                "room_requirements": dict(session.room_requirements or {}),
                "group_size": session.group_size,
                "flight_option": session.flight_option,
            },
            "user_messages": user_messages[-20:],
        }
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
            "trip_query": session.trip_query,
            "selected_trip_id": session.selected_trip_id,
            "selected_trip_name": session.selected_trip_name,
            "group_size": session.group_size,
            "preferred_date": session.preferred_date,
            "flight_option": session.flight_option,
            "room_type": session.room_type,
            "room_group": session.room_group,
            "room_requirements": dict(session.room_requirements or {}),
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
                "destination": session.trip_query or session.selected_trip_name,
                "birthday": session.birthday,
                "nationality": session.nationality,
                "group_size": session.group_size,
                "preferred_date": session.preferred_date,
                "flight_option": session.flight_option,
                "room_type": session.room_type,
                "room_group": session.room_group,
                "room_requirements": dict(session.room_requirements or {}),
                "currency": session.currency,
            },
            "conversation_history": list(session.messages[-12:]),
            "conversation_memory": self._conversation_memory(session, user_text),
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
            if str(language or "").strip().lower().startswith("ar"):
                return "\u0645\u062d\u062a\u0627\u062c \u0623\u0639\u0631\u0641 \u062a\u0641\u0635\u064a\u0644\u0629 \u0625\u0636\u0627\u0641\u064a\u0629 \u0639\u0644\u0634\u0627\u0646 \u0623\u0642\u062f\u0631 \u0623\u0633\u0627\u0639\u062f\u0643 \u0628\u0634\u0643\u0644 \u0635\u062d\u064a\u062d."
            return "I need one more detail so I can help you correctly."
        if language.startswith("ar"):
            return cleaned
        return cleaned

    @staticmethod
    def _customer_reply_has_structural_leak(reply: str) -> bool:
        normalized = str(reply or "").casefold()
        blocked = (
            "assistant_message",
            "workflow_policy",
            "required_step",
            "customer_message_key",
            "allowed_tools",
            "tool_result",
            "selected_trip_id",
            "raw json",
            "traceback",
            "runtimeerror",
            "valueerror",
        )
        return any(term in normalized for term in blocked) or bool(re.search(r"^\s*[\[{].*[\]}]\s*$", str(reply or ""), re.S))

    @staticmethod
    def _customer_reply_validation_issue(
        reply: str,
        *,
        write_result: dict[str, Any] | None = None,
        record_type: str = "",
    ) -> str:
        if ToolCallingSessionRuntime._customer_reply_has_structural_leak(reply):
            return "structural_leak"
        issue, _terms = response_guard_issue(reply, write_result=write_result, record_type=record_type)
        if issue:
            return issue
        return response_completeness_issue(reply)

    @staticmethod
    def _response_write_result_for_message(session: SessionState, message_key: str) -> tuple[dict[str, Any] | None, str]:
        key = str(message_key or "").strip().lower()
        final_result = session.final_result if isinstance(session.final_result, dict) else {}
        booking_result = session.booking_result if isinstance(session.booking_result, dict) else {}
        record_type = ""
        record_id = ""
        if "booking" in key:
            record_type = "booking"
            record_id = str(booking_result.get("booking_id") or final_result.get("booking_id") or "").strip()
        elif "handoff" in key or "human" in key:
            record_type = "handoff"
            record_id = str(final_result.get("handoff_id") or "").strip()
        elif "lead" in key:
            record_type = "lead"
            record_id = str(final_result.get("lead_id") or "").strip()
        if not record_type:
            return None, ""
        if not record_id:
            return None, record_type
        return (
            {
                "status": "success",
                "executed": True,
                "reused": False,
                "record_type": record_type,
                "record_id": record_id,
            },
            record_type,
        )

    def _safe_response_fallback(self, session: SessionState, *, message_key: str, base_text: str = "") -> str:
        if "handoff_failed" in message_key:
            if session.language.startswith("ar"):
                return "لم أتمكن من إرسال طلب المراجعة تلقائيا. من فضلك تواصل مع فريق Ravel مباشرة أو حاول مرة أخرى بعد قليل."
            return "I couldn't submit the review request automatically. Please contact the Ravel team directly, or try again in a moment."
        if message_key.endswith("already_under_review") or (self._has_active_handoff(session) and "workflow.handoff." not in message_key):
            return self._handoff_already_under_review_message(session)
        fallback = self._normalize_reply(base_text, session.language)
        write_result, record_type = self._response_write_result_for_message(session, message_key)
        if self._customer_reply_validation_issue(fallback, write_result=write_result, record_type=record_type):
            if session.language.startswith("ar"):
                return "\u0645\u0639\u0644\u0634\u060c \u0645\u0642\u062f\u0631\u062a\u0634 \u0623\u062c\u0647\u0632 \u0627\u0644\u0631\u062f \u0628\u0634\u0643\u0644 \u0635\u062d\u064a\u062d. \u0645\u0645\u0643\u0646 \u062a\u0628\u0639\u062a \u0637\u0644\u0628\u0643 \u0645\u0631\u0629 \u062a\u0627\u0646\u064a\u0629\u061f"
            return "Sorry, I couldn\u2019t prepare that response properly. Could you try that again?"
        return fallback

    def _agent_reply(
        self,
        session: SessionState,
        *,
        message_key: str,
        base_text: str,
        user_text: str = "",
        required_action: str = "",
        session_context: dict[str, Any] | None = None,
    ) -> str:
        """Let the AI agent author customer copy from verified state.

        The base text is an authoritative state/action contract, not the final
        voice. If the configured AI cannot rewrite safely, the sanitized base is
        kept as an emergency fallback so the flow never sends raw errors.
        """

        fallback = self._safe_response_fallback(session, message_key=message_key, base_text=base_text)
        if message_key == "session.opening" or message_key.startswith(("identity_policy.", "privacy_policy.")):
            return fallback
        rewriter = getattr(self._conversation_ai, "rewrite_message", None)
        if not callable(rewriter):
            return fallback
        if isinstance(self._conversation_ai, GeminiAgent):
            try:
                from services.ai_agent.llm.gemini_provider import GeminiProvider
            except Exception:
                GeminiProvider = ()  # type: ignore[assignment]
            if not isinstance(getattr(self._conversation_ai, "provider", None), GeminiProvider):
                return fallback
        try:
            context = dict(session_context or self._build_context(session, user_text))
            workflow = context.get("workflow") if isinstance(context.get("workflow"), dict) else {}
            verified = bool(workflow.get("identity_verified") or workflow.get("verified_traveler"))
            if not verified and message_key.startswith(("trip.reference.", "trip.media.", "workflow.required_step.")):
                return fallback
            context = self._traveler_safe_context(context)
            context["response_contract"] = {
                "source": "authoritative_state_plus_ai_generation",
                "message_key": message_key,
                "must_preserve_booking_state": True,
                "must_not_invent_ravel_facts": True,
                "must_not_expose_internal_terms": True,
                "must_not_show_exact_room_quantities": True,
                "fallback_text_is_not_customer_voice": True,
            }
            rewritten = rewriter(
                message_key=message_key,
                base_text=fallback,
                language=session.language,
                user_text=user_text,
                required_action=required_action or "Generate a concise traveler-facing response without changing the workflow action.",
                session_context=context,
            )
        except Exception as exc:
            agent_logger.warning("AI reply generation failed session=%s key=%s error=%s", session.id, message_key, exc)
            return fallback
        reply = self._normalize_reply(str(rewritten or ""), session.language)
        write_result, record_type = self._response_write_result_for_message(session, message_key)
        validation_issue = self._customer_reply_validation_issue(reply, write_result=write_result, record_type=record_type)
        if validation_issue:
            agent_logger.warning(
                "AI reply rejected session=%s key=%s reason=%s text=%r",
                session.id,
                message_key,
                validation_issue,
                reply[:160],
            )
            return fallback
        return reply

    def _append_agent_reply(
        self,
        session: SessionState,
        *,
        message_key: str,
        base_text: str,
        user_text: str = "",
        required_action: str = "",
        session_context: dict[str, Any] | None = None,
        media: list[dict[str, Any]] | None = None,
    ) -> None:
        reply = self._agent_reply(
            session,
            message_key=message_key,
            base_text=base_text,
            user_text=user_text,
            required_action=required_action,
            session_context=session_context,
        )
        session.messages.append(self._assistant_message(text=reply, language=session.language, media=media))

    def _append_authoritative_reply(
        self,
        session: SessionState,
        *,
        message_key: str,
        base_text: str,
        media: list[dict[str, Any]] | None = None,
    ) -> None:
        """Append backend-owned copy without sending it through the LLM."""

        reply = self._safe_response_fallback(session, message_key=message_key, base_text=base_text)
        session.messages.append(self._assistant_message(text=reply, language=session.language, media=media))

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
            "room_type": bool(stored.get("room_type") or session.room_type or session.room_requirements),
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
        invalid_reason = str(phone_info.get("invalid_reason") or "").strip() or "invalid_format"
        warning = "Phone number requires confirmation." if phone_info.get("requires_country_confirmation") else f"Invalid WhatsApp number: {invalid_reason}."
        if not phone_info.get("is_valid", True) or phone_info.get("requires_country_confirmation") or not phone_info.get("normalized_e164"):
            workflow = {
                "lookup_status": "invalid_phone",
                "identity_verified": False,
                "required_step": "collect_valid_whatsapp_number",
            }
            session.preview = {**dict(session.preview or {}), "workflow": workflow}
            return {
                "name": "find_traveler_by_phone",
                "input": {"raw_phone": raw_phone, "country_code": session.country_code or self.settings.default_country_code},
                "result": {"status": "invalid_phone", "warnings": [warning], "traveler": None},
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

    @classmethod
    def _is_need_clarification_request(cls, text: str) -> bool:
        normalized = cls._normalize_trip_reference(text)
        if not normalized:
            return False
        if normalized in {"what", "why", "help", "explain", "what next", "what now"}:
            return True
        return any(
            phrase in normalized
            for phrase in (
                "what do u need",
                "what do you need",
                "what u need",
                "what you need",
                "what do u want",
                "what do you want",
                "what should i",
                "what should i send",
                "what information",
                "what info",
                "what is required",
                "\u0645\u0634 \u0641\u0627\u0647\u0645",
                "\u0645\u0634 \u0641\u0627\u0647\u0645\u0629",
                "\u0648\u0636\u062d",
                "\u0648\u0636\u062d\u0644\u064a",
                "\u0645\u062d\u062a\u0627\u062c \u0627\u064a\u0647",
                "\u0645\u062d\u062a\u0627\u062c \u0627\u064a",
                "\u0639\u0627\u064a\u0632 \u0627\u064a\u0647",
                "\u0627\u064a\u0647 \u0627\u0644\u0645\u0637\u0644\u0648\u0628",
            )
        )

    def _handle_trip_selection_contextual_reply(self, session: SessionState, clean_text: str, decision) -> bool:
        if str(decision.required_step or "") != "select_trip":
            return False
        if session.selected_trip_id:
            return False

        reply = ""
        message_key = "workflow.trip_selection.contextual"
        required_action = "Respond naturally to the traveler while preserving the pending trip selection step."
        if self._looks_negative_confirmation(clean_text):
            reply = (
                "\u062a\u0645\u0627\u0645\u060c \u0645\u0634 \u0647\u0643\u0645\u0644 \u0639\u0644\u0649 \u0627\u0644\u0631\u062d\u0644\u0629 \u062f\u064a. \u0644\u0648 \u062d\u0627\u0628\u0628 \u062e\u064a\u0627\u0631 \u062a\u0627\u0646\u064a\u060c \u0627\u0643\u062a\u0628 \u0627\u0644\u0648\u062c\u0647\u0629 \u0623\u0648 \u0627\u0644\u062a\u0627\u0631\u064a\u062e \u0623\u0648 \u0627\u0633\u0645 \u0627\u0644\u0631\u062d\u0644\u0629."
                if session.language.startswith("ar")
                else "No problem. I will not continue with that trip. If you want another option, tell me the destination, date, or trip name you are interested in."
            )
            message_key = "workflow.trip_selection.rejected"
        elif self._is_need_clarification_request(clean_text):
            reply = (
                "\u0645\u062d\u062a\u0627\u062c \u0641\u0642\u0637 \u062a\u062e\u062a\u0627\u0631 \u0631\u062d\u0644\u0629 \u0645\u0646 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0628\u0627\u0644\u0631\u0642\u0645 \u0623\u0648 \u0627\u0644\u0627\u0633\u0645. \u0648\u0644\u0648 \u0645\u0641\u064a\u0634 \u0631\u062d\u0644\u0629 \u0645\u0646\u0627\u0633\u0628\u0627\u0643\u060c \u0627\u0643\u062a\u0628 \u0627\u0644\u0648\u062c\u0647\u0629 \u0623\u0648 \u0627\u0644\u062a\u0627\u0631\u064a\u062e \u0627\u0644\u0644\u064a \u062a\u0641\u0636\u0644\u0647."
                if session.language.startswith("ar")
                else "I only need you to choose one of the listed trips by number or name. If none of those trips works for you, tell me the destination or date you prefer."
            )
            message_key = "workflow.trip_selection.explain_needed"
        else:
            return False

        session.stage = decision.state
        self._append_agent_reply(
            session,
            message_key=message_key,
            base_text=reply,
            user_text=clean_text,
            required_action=required_action,
            session_context={**self._build_context(session, clean_text), "workflow_policy": decision.to_context()},
        )
        session.tools_used = []
        session.fallback_used = False
        return True

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
        previous_trip_id = str(session.selected_trip_id or "").strip()
        new_trip_id = str(trip.get("trip_id") or "").strip()
        if previous_trip_id and new_trip_id and previous_trip_id != new_trip_id:
            self._clear_booking_dependent_state(session)
        session.selected_trip_id = str(trip.get("trip_id") or "").strip()
        session.selected_trip_name = str(trip.get("trip_name") or session.selected_trip_name or "").strip()
        chosen_type = str(trip.get("type") or trip.get("trip_type") or "").strip().lower()
        if chosen_type in {"local", "international"}:
            session.trip_type = chosen_type
            self._update_collection_state(session, trip_type=True)
        self._update_collection_state(session, selected_trip=True, destination=bool(session.selected_trip_name))
        if not self._trip_supports_flights(trip):
            session.flight_option = "Not Applicable"
            self._update_collection_state(session, flight_option=True)

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
        result = self._filter_trip_result_by_query(result, session.trip_query or query)
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
        return "I could not find an available trip with that name. Please send the exact trip name."

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
                "\u0645\u0648\u0627\u0635\u0641\u0627\u062a",
                "\u0648\u0635\u0641",
            )
        )

    @classmethod
    def _is_trip_quality_question(cls, text: str) -> bool:
        normalized = cls._normalize_trip_reference(text)
        if not normalized:
            return False
        return any(
            phrase in normalized
            for phrase in (
                "is it good",
                "is this trip good",
                "worth it",
                "worth going",
                "recommend",
                "\u0643\u0648\u064a\u0633",
                "\u0643\u0648\u064a\u0633\u0629",
                "\u064a\u0633\u062a\u0627\u0647\u0644",
                "\u062a\u0646\u0635\u062d",
                "\u0631\u0623\u064a\u0643",
                "\u0631\u0627\u064a\u0643",
                "\u0639\u0627\u062c\u0628\u062a\u0643",
                "\u062d\u0644\u0648\u0629",
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
        if normalized in {
            "what do i need", "what is needed",
            "\u064a\u0639\u0646\u064a \u0627\u064a\u0647", "\u064a\u0639\u0646\u064a \u0625\u064a\u0647",
            "\u0648\u0636\u062d", "\u0648\u0636\u062d\u0644\u064a",
            "\u0627\u064a\u0647 \u0627\u0644\u0645\u0637\u0644\u0648\u0628",
        }:
            return True
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
    def _is_name_step_clarification(cls, text: str) -> bool:
        normalized = cls._normalize_trip_reference(text)
        if not normalized:
            return False
        return any(
            phrase in normalized
            for phrase in (
                "full name",
                "three part",
                "three-part",
                "first name enough",
                "why do you need my full name",
                "\u0644\u0627\u0632\u0645 \u0643\u0627\u0645\u0644",
                "\u064a\u0639\u0646\u064a \u0627\u0644\u0627\u0633\u0645 \u0627\u0644\u062b\u0644\u0627\u062b\u064a",
                "\u0627\u0644\u0627\u0633\u0645 \u0627\u0644\u062b\u0644\u0627\u062b\u064a",
                "\u0643\u0627\u0645\u0644",
            )
        )

    @classmethod
    def _is_asking_for_known_name(cls, text: str) -> bool:
        normalized = cls._normalize_trip_reference(text)
        if not normalized:
            return False
        return any(
            phrase in normalized
            for phrase in (
                "what is my name",
                "do you know my name",
                "\u0627\u0633\u0645\u064a \u0627\u064a\u0647",
                "\u0627\u0633\u0645\u064a \u0625\u064a\u0647",
                "\u0627\u0646\u0627 \u0627\u0633\u0645\u064a \u0627\u064a\u0647",
                "\u0623\u0646\u0627 \u0627\u0633\u0645\u064a \u0625\u064a\u0647",
            )
        )

    @classmethod
    def _is_conversational_interruption(cls, text: str) -> bool:
        normalized = cls._normalize_trip_reference(text)
        if not normalized:
            return False
        if (
            cls._is_explanation_request(text)
            or cls._is_identity_question(text)
            or cls._is_human_agent_request(text)
            or cls._is_name_step_clarification(text)
            or cls._is_asking_for_known_name(text)
            or cls._is_trip_quality_question(text)
        ):
            return True
        if cls._is_hostile_message(text):
            return True
        if any(phrase in normalized for phrase in ("i want to book", "i want a booking", "\u0627\u0646\u0627 \u0639\u0627\u064a\u0632 \u0627\u062d\u062c\u0632")):
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
        if step == "collect_group_size":
            if self._is_explanation_request(clean_text):
                if language.startswith("ar"):
                    return "\u0623\u0642\u0635\u062f \u0639\u062f\u062f \u0627\u0644\u0645\u0633\u0627\u0641\u0631\u064a\u0646 \u0641\u064a \u0637\u0644\u0628 \u0627\u0644\u062d\u062c\u0632\u060c \u0645\u0634 \u0639\u062f\u062f \u0627\u0644\u063a\u0631\u0641. \u0627\u0643\u062a\u0628 \u0627\u0644\u0631\u0642\u0645 \u0645\u062b\u0644: 2."
                return "I mean the number of travelers in the booking request, not the number of rooms. Reply with a number, for example 2."
            if language.startswith("ar"):
                return "\u0644\u0627 \u0645\u0634\u0643\u0644\u0629. \u0623\u0646\u0627 \u0645\u0633\u0627\u0639\u062f\u0643 \u0641\u064a \u0627\u0644\u062d\u062c\u0632\u060c \u0648\u0645\u062d\u062a\u0627\u062c \u0641\u0642\u0637 \u0639\u062f\u062f \u0627\u0644\u0645\u0633\u0627\u0641\u0631\u064a\u0646. \u0627\u0643\u062a\u0628 \u0627\u0644\u0631\u0642\u0645 \u0645\u062b\u0644: 2."
            return "No problem. I can help with the booking; I only need the number of travelers. Reply with a number, for example 2."
        if self._is_hostile_message(clean_text):
            if language.startswith("ar"):
                return "فاهم إنك متضايق. هخليها بسيطة ونكمل خطوة بخطوة. المسافرون شباب ولا بنات؟ اكتب 1 للشباب أو 2 للبنات."
            return "I hear you. I’ll keep this simple and help you step by step. Are the travelers boys/male or girls/female? Reply with 1 for boys or 2 for girls."
        if self._is_identity_question(clean_text):
            if language.startswith("ar"):
                return "أنا مساعد المبيعات بالذكاء الاصطناعي من Rahma Traveler. أراجع الرحلات المؤكدة في CRM وأساعدك في تجهيز طلب الحجز. نكمل: المسافرون شباب ولا بنات؟"
            return "I’m Ravel Traveler’s AI sales assistant. I check available trips and help prepare your booking request. To continue, are the travelers boys/male or girls/female?"
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
                    "and I want to show you only the available options.\n\n"
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
        if step == "collect_new_traveler_name":
            if self._is_asking_for_known_name(clean_text):
                if language.startswith("ar"):
                    return "\u0623\u0646\u062a \u0644\u0645 \u062a\u0631\u0633\u0644 \u0627\u0633\u0645\u0643 \u0628\u0639\u062f\u060c \u0644\u0630\u0644\u0643 \u0644\u0627 \u064a\u0645\u0643\u0646\u0646\u064a \u0645\u0639\u0631\u0641\u062a\u0647 \u0623\u0648 \u062a\u062e\u0645\u064a\u0646\u0647. \u0645\u0646 \u0641\u0636\u0644\u0643 \u0627\u0643\u062a\u0628 \u0627\u0633\u0645\u0643 \u0627\u0644\u062b\u0644\u0627\u062b\u064a\u060c \u0645\u062b\u0644: \u0645\u062d\u0645\u062f \u0623\u0634\u0631\u0641 \u0635\u0641\u0648\u062a."
                return "You have not sent your name yet, so I cannot know or guess it. Please enter your full three-part name, for example: Mohamed Ashraf Safwat."
            if self._is_name_step_clarification(clean_text) or self._is_explanation_request(clean_text):
                if language.startswith("ar"):
                    return "\u0646\u0639\u0645\u060c \u0646\u062d\u062a\u0627\u062c \u0627\u0633\u0645\u0643 \u0627\u0644\u062b\u0644\u0627\u062b\u064a \u0644\u0625\u0646\u0634\u0627\u0621 \u0645\u0644\u0641\u0643 \u0628\u0634\u0643\u0644 \u0635\u062d\u064a\u062d. \u0645\u0646 \u0641\u0636\u0644\u0643 \u0627\u0643\u062a\u0628\u0647 \u0645\u062b\u0644: \u0645\u062d\u0645\u062f \u0623\u0634\u0631\u0641 \u0635\u0641\u0648\u062a."
                return "Yes, we need your three-part name to create your profile correctly. Please enter it like: Mohamed Ashraf Safwat."
            if language.startswith("ar"):
                return "\u0645\u0646 \u0641\u0636\u0644\u0643 \u0627\u0643\u062a\u0628 \u0627\u0633\u0645\u0643 \u0627\u0644\u062b\u0644\u0627\u062b\u064a\u060c \u0645\u062b\u0644: \u0645\u062d\u0645\u062f \u0623\u0634\u0631\u0641 \u0635\u0641\u0648\u062a."
            return "Please enter your full three-part name, for example: Mohamed Ashraf Safwat."
        if step == "collect_group_size":
            return "كم عدد المسافرين في الطلب؟" if language.startswith("ar") else "How many travelers should I include in the request?"
        if step == "collect_flight_preference":
            return (
                "\u062a\u062d\u0628 \u0627\u0644\u0631\u062d\u0644\u0629 \u0645\u0639 \u0637\u064a\u0631\u0627\u0646 \u0623\u0645 \u0628\u062f\u0648\u0646 \u0637\u064a\u0631\u0627\u0646\u061f\n\n1. \u0645\u0639 \u0637\u064a\u0631\u0627\u0646\n2. \u0628\u062f\u0648\u0646 \u0637\u064a\u0631\u0627\u0646"
                if language.startswith("ar")
                else "Would you like the trip with flights or without flights?\n\n1. With flights\n2. Without flights"
            )
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

        if candidate:
            reply = candidate
        else:
            # Clarification is an input-understanding case, not an output failure.
            reply = self._safe_response_fallback(
                session,
                message_key=f"workflow.interruption.{decision.required_step or 'unknown'}",
                base_text=self._natural_interruption_fallback(session, decision, clean_text),
            )
        session.messages.append({"role": "assistant", "text": reply, "state": "completed"})
        session.tools_used = []
        session.fallback_used = False
        return True

    @staticmethod
    def _last_assistant_text(session: SessionState) -> str:
        for message in reversed(session.messages):
            if isinstance(message, dict) and message.get("role") == "assistant":
                return str(message.get("text") or "")
        return ""

    @classmethod
    def _trip_matches_query(cls, trip: dict[str, Any], query: str) -> bool:
        normalized_query = cls._normalize_trip_reference(query)
        if not normalized_query:
            return True
        haystack = cls._normalize_trip_reference(
            " ".join(
                str(trip.get(key) or "")
                for key in (
                    "trip_id",
                    "trip_name",
                    "destination",
                    "country",
                    "city",
                    "location",
                    "public_description",
                    "description",
                    "program",
                    "notes",
                )
            )
        )
        query_tokens = [token for token in normalized_query.split() if token not in _TRIP_REFERENCE_STOP_WORDS]
        if not query_tokens:
            return True
        return all(token in haystack for token in query_tokens)

    @classmethod
    def _filter_trip_result_by_query(cls, trip_result: dict[str, Any], query: str) -> dict[str, Any]:
        if not str(query or "").strip():
            return trip_result
        filtered = dict(trip_result or {})
        for bucket in ("open_trips", "date_tbd_trips"):
            filtered[bucket] = [
                dict(trip)
                for trip in list((trip_result or {}).get(bucket) or [])
                if isinstance(trip, dict) and cls._trip_matches_query(trip, query)
            ]
        filtered["trips"] = [*filtered.get("open_trips", []), *filtered.get("date_tbd_trips", [])]
        filtered["query"] = query
        return filtered

    @staticmethod
    def _no_matching_trip_reply(query: str, language: str) -> str:
        clean_query = str(query or "").strip()
        if language.startswith("ar"):
            if clean_query:
                return f"لا يوجد لدي رحلة مؤكدة مطابقة لـ {clean_query} حاليا. إذا كنت تقصد رحلة من بوست أو ستوري، أرسل اسم الرحلة أو صورة الإعلان وسأطابقها مع رحلات Ravel المتاحة."
            return "لا يوجد لدي رحلة مؤكدة مطابقة لهذا الطلب حاليا. أرسل اسم الرحلة أو صورة الإعلان وسأطابقها مع رحلات Ravel المتاحة."
        if clean_query:
            return f"I don't have a confirmed Ravel trip matching {clean_query} right now. If you mean a trip from an Instagram post or story, send the trip name or the ad image and I'll match it against Ravel's available trips."
        return "I don't have a confirmed Ravel trip matching that request right now. If you mean a trip from an Instagram post or story, send the trip name or the ad image and I'll match it against Ravel's available trips."

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
        lines = [f"Sure. Here are the trip details for {trip_name}:"]
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

    @staticmethod
    def _canonical_trip_search_reply(session: SessionState) -> str:
        """Render trip results deterministically so numbering cannot drift."""
        preview = session.preview if isinstance(session.preview, dict) else {}
        trip_result = preview.get("trip_result") if isinstance(preview.get("trip_result"), dict) else {}
        trips = [
            trip
            for trip in [*list(trip_result.get("open_trips") or []), *list(trip_result.get("date_tbd_trips") or [])]
            if isinstance(trip, dict)
        ]
        if not trips:
            trip_type = str(session.trip_type or "").strip().lower()
            if trip_type in {"local", "international"}:
                if session.language.startswith("ar"):
                    return "\u0644\u0627 \u062a\u0648\u062c\u062f \u0631\u062d\u0644\u0627\u062a \u0645\u062a\u0627\u062d\u0629 \u0644\u0647\u0630\u0627 \u0627\u0644\u0646\u0648\u0639 \u062d\u0627\u0644\u064a\u0627 \u0641\u064a CRM. \u0644\u0646 \u0623\u0639\u0631\u0636 \u0646\u0648\u0639 \u0631\u062d\u0644\u0629 \u0622\u062e\u0631 \u0644\u0623\u0646 \u0637\u0644\u0628\u0643 \u0645\u062d\u062f\u062f. \u0633\u0628\u0628 \u0627\u0644\u062a\u062d\u0648\u064a\u0644: \u0644\u0627 \u062a\u0648\u062c\u062f \u0631\u062d\u0644\u0627\u062a \u0646\u0634\u0637\u0629 \u0645\u062a\u0627\u062d\u0629 \u0644\u0647\u0630\u0627 \u0627\u0644\u0646\u0648\u0639 \u0627\u0644\u0622\u0646."
                return f"I do not have any available {trip_type} trips in CRM right now. I will not show another trip type because you asked for this one. Reason for escalation: no active inventory is available for the requested trip type."
            return ToolCallingSessionRuntime._no_matching_trip_reply(session.trip_query or session.selected_trip_name, session.language)

        trip_type = str(session.trip_type or "").strip().lower()
        type_label = f" {trip_type}" if trip_type in {"local", "international"} else ""
        if trip_type in {"local", "international"}:
            heading = f"Here are the {trip_type} trips currently available:"
        else:
            heading = "Here are the trips currently available:"
        lines = [heading, ""]
        for index, trip in enumerate(trips, start=1):
            name = str(trip.get("trip_name") or trip.get("trip_id") or "Unnamed trip").strip()
            lines.append(f"{index}) {name}")
            start_date = str(trip.get("start_date") or "").strip()
            end_date = str(trip.get("end_date") or "").strip()
            if start_date and end_date:
                lines.append(f"Dates: {start_date} to {end_date}")
            elif start_date:
                lines.append(f"Date: {start_date}")
            else:
                lines.append("Dates: To be confirmed")
            price = str(trip.get("public_price") or "").strip()
            if price:
                lines.append(f"Price: {price}")
            lines.append("")
        lines.append("Please reply with the trip number or exact trip name.")
        return "\n".join(lines)

    def _load_verified_trip_results(self, session: SessionState) -> None:
        """Load the trip list before the model speaks about search results."""
        query = str(session.trip_query or "").strip()
        try:
            result = self._read_only_tools.search_trips(
                trip_type=session.trip_type,
                query=query,
            )
        except Exception as exc:
            agent_logger.warning("Trip search failed before response session=%s error=%s", session.id, exc)
            return
        result = self._filter_trip_result_by_query(result, query)
        preview = dict(session.preview or {})
        preview["trip_result"] = {
            "open_trips": list(result.get("open_trips") or []),
            "date_tbd_trips": list(result.get("date_tbd_trips") or []),
        }
        preview["trip_query"] = query
        session.preview = preview

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
                else "I am Ravel Traveler's AI sales assistant. I check available trips and prepare booking requests after your confirmation."
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
            prompt = (
                "\u062a\u062d\u0628 \u0627\u0644\u0631\u062d\u0644\u0629 \u0645\u0639 \u0637\u064a\u0631\u0627\u0646 \u0623\u0645 \u0628\u062f\u0648\u0646 \u0637\u064a\u0631\u0627\u0646\u061f\n\n1. \u0645\u0639 \u0637\u064a\u0631\u0627\u0646\n2. \u0628\u062f\u0648\u0646 \u0637\u064a\u0631\u0627\u0646"
                if language.startswith("ar")
                else "Do you want this trip with flights or without flights?\n\n1. With flights\n2. Without flights"
            )
        elif step == "collect_room_type":
            prompt = self._normalize_reply(decision.assistant_message, language)
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
        self._append_agent_reply(
            session,
            message_key="trip.details.followup",
            base_text=reply,
            user_text=clean_text,
            required_action="Answer with only the verified trip details in the base text, then continue the current workflow step.",
            session_context={**session_context, "workflow_policy": decision.to_context()},
        )
        session.tools_used = []
        session.fallback_used = False
        return True

    def _booking_confirmation_summary(self, session: SessionState) -> str:
        trip_name = session.selected_trip_name or session.selected_trip_id or "the selected trip"
        room = " ".join(part for part in (session.room_type, session.room_group) if part).strip() or "not specified"
        room_request = self._room_requirements_summary(session)
        if room_request:
            room = room_request
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

    @staticmethod
    def _room_occupancy(room_type: str) -> int:
        return {"Single": 1, "Double": 2, "Triple": 3}.get(str(room_type or "").strip().title(), 1)

    @classmethod
    def _ensure_room_requirements_for_group(cls, session: SessionState) -> None:
        if isinstance(session.room_requirements, dict) and session.room_requirements.get("requirements"):
            return
        room_type = str(session.room_type or "").strip().title()
        room_group = str(session.room_group or "").strip().lower()
        if room_type not in {"Single", "Double", "Triple"} or room_group not in {"boys", "girls"}:
            return
        group_size = max(1, int(session.group_size or 1))
        occupancy = cls._room_occupancy(room_type)
        rooms = (group_size + occupancy - 1) // occupancy
        session.room_requirements = {
            "requirements": [{"room_type": room_type, "room_group": room_group, "rooms": rooms}],
            "boys_rooms_requested": rooms if room_group == "boys" else 0,
            "girls_rooms_requested": rooms if room_group == "girls" else 0,
        }

    @staticmethod
    def _room_requirements_summary(session: SessionState) -> str:
        data = session.room_requirements if isinstance(session.room_requirements, dict) else {}
        requirements = data.get("requirements") if isinstance(data.get("requirements"), list) else []
        if not requirements:
            return ""
        parts = []
        for item in requirements:
            if not isinstance(item, dict):
                continue
            rooms = int(item.get("rooms") or 0)
            if rooms <= 0:
                continue
            group = str(item.get("room_group") or "").strip()
            room_type = str(item.get("room_type") or "room").strip().lower()
            label = f"{rooms} {group} {room_type} room"
            if rooms != 1:
                label += "s"
            parts.append(label)
        if not parts:
            return ""
        return "Requested: " + " and ".join(parts)

    def _handle_booking_confirmation_reply(self, session: SessionState, clean_text: str) -> bool:
        last_assistant = next(
            (
                str(message.get("text") or "")
                for message in reversed(session.messages or [])
                if isinstance(message, dict) and message.get("role") == "assistant"
            ),
            "",
        ).casefold()
        confirmation_visible = "booking draft" in last_assistant and "confirm" in last_assistant
        if (
            session.stage != "booking_confirmation_required"
            and not session.booking_confirmation_requested
            and not confirmation_visible
        ):
            return False
        if session.booking_confirmation_requested or confirmation_visible:
            session.stage = "booking_confirmation_required"
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
            if session.language.startswith("ar"):
                reply = "تمام، لن أرسل طلب الحجز الآن. أقدر أعدل التفاصيل أو أحولك لموظف."
        else:
            reply = (
                "من فضلك أكد بنعم لإنشاء طلب الحجز، أو لا لإيقافه."
                if session.language.startswith("ar")
                else "Please reply yes to continue with this booking draft, or no to stop."
            )
            if session.language.startswith("ar"):
                reply = "من فضلك رد بنعم للمتابعة في طلب الحجز، أو لا لإيقافه."
        self._append_authoritative_reply(session, message_key="booking.confirmation_reply", base_text=reply)
        session.tools_used = []
        session.fallback_used = False
        return True

    @staticmethod
    def _has_completed_booking_context(session: SessionState) -> bool:
        return bool(
            getattr(session, "booking_completed", False)
            or isinstance(session.booking_result, dict)
            or (
                isinstance(session.final_result, dict)
                and (session.final_result.get("booking_id") or session.final_result.get("handoff_id"))
            )
            or session.stage in {"completed", "booking_created", "handoff_created", "post_booking_support"}
        )

    @staticmethod
    def _post_booking_booking_intent(text: str) -> bool:
        lowered = " ".join(str(text or "").strip().casefold().split())
        arabic = any(term in lowered for term in (
            "\u0639\u0627\u064a\u0632 \u0627\u062d\u062c\u0632",
            "\u0639\u0627\u064a\u0632 \u0623\u062d\u062c\u0632",
            "\u062d\u062c\u0632 \u062c\u062f\u064a\u062f",
            "\u062d\u062c\u0632 \u062a\u0627\u0646\u064a",
            "\u0631\u062d\u0644\u0629 \u062a\u0627\u0646\u064a\u0629",
            "\u0646\u0628\u062f\u0623 \u062d\u062c\u0632",
        ))
        english = any(term in lowered for term in (
            "i want to book",
            "book again",
            "another booking",
            "start a new booking",
            "another trip",
            "book for someone else",
        ))
        return arabic or english

    @staticmethod
    def _post_booking_explicit_new_booking(text: str) -> bool:
        lowered = " ".join(str(text or "").strip().casefold().split())
        return any(term in lowered for term in (
            "\u062a\u0627\u0646\u064a",
            "\u062a\u0627\u0646\u064a\u0629",
            "\u062c\u062f\u064a\u062f",
            "\u0634\u062e\u0635 \u062a\u0627\u0646\u064a",
            "again",
            "another",
            "new booking",
            "someone else",
        ))

    @staticmethod
    def _post_booking_status_intent(text: str) -> bool:
        lowered = " ".join(str(text or "").strip().casefold().split())
        return any(term in lowered for term in (
            "\u062d\u0627\u0644\u0629 \u0627\u0644\u062d\u062c\u0632",
            "\u062d\u062c\u0632\u064a",
            "\u0627\u062a\u0633\u062c\u0644",
            "\u0647\u064a\u062a\u0648\u0627\u0635\u0644",
            "\u0645\u064a\u0646 \u0647\u064a\u062a\u0648\u0627\u0635\u0644",
            "booking status",
            "was my booking created",
            "who will contact",
        ))

    @staticmethod
    def _post_booking_ack(text: str) -> bool:
        normalized = " ".join(str(text or "").strip().casefold().split())
        return normalized in {
            "\u062a\u0645\u0627\u0645",
            "\u0645\u0627\u0634\u064a",
            "\u0627\u0648\u0643\u064a",
            "\u0623\u0648\u0643\u064a",
            "\u0634\u0643\u0631\u0627",
            "\u0634\u0643\u0631\u0627\u064b",
            "ok",
            "okay",
            "thanks",
            "thank you",
            "understood",
        }

    @staticmethod
    def _post_booking_negative_or_unclear(text: str) -> bool:
        normalized = " ".join(str(text or "").strip().casefold().split())
        return normalized in {"\u0644\u0627", "\u0645\u0641\u064a\u0634", "\u0645\u0641\u064a\u0634.", "no", "nothing"}

    @staticmethod
    def _post_booking_record(session: SessionState) -> dict[str, Any]:
        booking = session.booking_result if isinstance(session.booking_result, dict) else {}
        final_result = session.final_result if isinstance(session.final_result, dict) else {}
        return {
            "booking_id": str(booking.get("booking_id") or final_result.get("booking_id") or "").strip(),
            "trip_name": str(booking.get("trip_name") or session.selected_trip_name or "").strip(),
            "trip_id": str(booking.get("trip_id") or session.selected_trip_id or "").strip(),
            "booking_status": str(booking.get("booking_status") or session.booking_status or "Draft").strip(),
            "handoff_id": str(final_result.get("handoff_id") or booking.get("handoff_id") or "").strip(),
            "handoff_state": str(session.handoff_state or final_result.get("handoff_state") or "").strip(),
        }

    def _start_new_booking_after_completion(self, session: SessionState) -> None:
        if isinstance(session.booking_result, dict) and session.booking_result:
            session.previous_booking_result = dict(session.booking_result)
        session.trip_type = ""
        session.trip_query = ""
        session.selected_trip_id = ""
        session.selected_trip_name = ""
        session.room_type = ""
        session.room_group = ""
        session.room_requirements = {}
        session.group_size = 1
        session.preferred_date = ""
        session.flight_option = ""
        session.currency = ""
        session.booking_confirmation_requested = False
        session.booking_confirmed = False
        session.stage = "new_booking_intent"
        self._update_collection_state(session, trip_type=False, selected_trip=False, room_type=False, room_group=False, group_size=False, flight_option=False, currency=False)

    def _post_booking_reply(self, session: SessionState, clean_text: str) -> str:
        record = self._post_booking_record(session)
        if self._post_booking_status_intent(clean_text):
            if session.language.startswith("ar"):
                booking_id = record["booking_id"] or "\u0627\u0644\u062d\u062c\u0632 \u0627\u0644\u0645\u062d\u0641\u0648\u0638"
                status = record["booking_status"] or "\u0645\u062d\u0641\u0648\u0638"
                return f"\u0623\u064a\u0648\u0647\u060c {booking_id} \u0645\u062a\u0633\u062c\u0644 \u0648\u062d\u0627\u0644\u062a\u0647 {status}. \u0641\u0631\u064a\u0642 Ravel \u0647\u064a\u062a\u0648\u0627\u0635\u0644 \u0645\u0639\u0627\u0643 \u0644\u0645\u062a\u0627\u0628\u0639\u0629 \u0627\u0644\u062a\u0641\u0627\u0635\u064a\u0644."
            booking_id = record["booking_id"] or "your saved booking"
            status = record["booking_status"] or "saved"
            return f"Yes, {booking_id} is created and its status is {status}. The Ravel team will follow up with you about the details."
        if self._post_booking_ack(clean_text):
            return "\u062a\u0645\u0627\u0645\u060c \u0623\u064a \u0648\u0642\u062a \u062a\u062d\u062a\u0627\u062c \u0645\u0633\u0627\u0639\u062f\u0629 \u0623\u0646\u0627 \u0645\u0639\u0627\u0643." if session.language.startswith("ar") else "All good. I’m here if you need anything else."
        if self._post_booking_negative_or_unclear(clean_text):
            return "\u0647\u0644 \u062a\u0642\u0635\u062f \u0623\u0646\u0643 \u0644\u0627 \u062a\u0631\u064a\u062f \u0628\u062f\u0621 \u062d\u062c\u0632 \u062c\u062f\u064a\u062f\u061f" if session.language.startswith("ar") else "Do you mean you don’t want to start a new booking?"
        if self._post_booking_booking_intent(clean_text):
            if self._post_booking_explicit_new_booking(clean_text):
                self._start_new_booking_after_completion(session)
                return "\u0628\u0627\u0644\u062a\u0623\u0643\u064a\u062f\u060c \u064a\u0645\u0643\u0646\u0646\u0627 \u0628\u062f\u0621 \u062d\u062c\u0632 \u062c\u062f\u064a\u062f. \u0647\u0644 \u062a\u0631\u064a\u062f \u0627\u0644\u062d\u062c\u0632 \u0641\u064a \u0646\u0641\u0633 \u0627\u0644\u0631\u062d\u0644\u0629 \u0623\u0645 \u062a\u0628\u062d\u062b \u0639\u0646 \u0631\u062d\u0644\u0629 \u0623\u062e\u0631\u0649\u061f" if session.language.startswith("ar") else "Of course. We can start a new booking. Would you like the same trip, or are you looking for another trip?"
            session.stage = "post_booking_support"
            return "\u0647\u0644 \u062a\u0631\u064a\u062f \u0628\u062f\u0621 \u062d\u062c\u0632 \u062c\u062f\u064a\u062f\u060c \u0623\u0645 \u062a\u0631\u064a\u062f \u0645\u062a\u0627\u0628\u0639\u0629 \u0627\u0644\u062d\u062c\u0632 \u0627\u0644\u062d\u0627\u0644\u064a\u061f" if session.language.startswith("ar") else "Do you want to start a new booking, or follow up on the current booking?"
        return "\u0645\u062d\u062a\u0627\u062c \u0623\u0639\u0631\u0641 \u062a\u0641\u0635\u064a\u0644\u0629 \u0625\u0636\u0627\u0641\u064a\u0629 \u0639\u0644\u0634\u0627\u0646 \u0623\u0642\u062f\u0631 \u0623\u0633\u0627\u0639\u062f\u0643 \u0628\u0634\u0643\u0644 \u0635\u062d\u064a\u062d." if session.language.startswith("ar") else "I need one more detail so I can help you correctly."

    def _handle_post_booking_message(self, session: SessionState, clean_text: str) -> bool:
        if not self._has_completed_booking_context(session):
            return False
        if not (
            self._post_booking_booking_intent(clean_text)
            or self._post_booking_status_intent(clean_text)
            or self._post_booking_ack(clean_text)
            or self._post_booking_negative_or_unclear(clean_text)
            or session.stage in {"completed", "booking_created", "handoff_created", "post_booking_support", "new_booking_intent"}
        ):
            return False
        session.messages.append({"role": "user", "text": clean_text})
        reply = self._post_booking_reply(session, clean_text)
        if session.stage not in {"new_booking_intent"}:
            session.stage = "post_booking_support"
        session.booking_completed = True
        session.messages.append({"role": "assistant", "text": reply, "state": "completed"})
        session.tools_used = []
        session.fallback_used = False
        return True

    def _handle_public_trip_reference_if_present(self, session: SessionState, clean_text: str) -> bool:
        if session.selected_trip_id:
            return False
        if self._extract_phone_candidate(clean_text):
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
            self._append_agent_reply(
                session,
                message_key="trip.reference.single_match",
                base_text=self._public_trip_reply(trip, session.language),
                user_text=clean_text,
                required_action="Present this single verified trip match and ask whether the traveler wants to continue.",
            )
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
            self._append_agent_reply(
                session,
                message_key="trip.reference.ambiguous_matches",
                base_text=self._trip_reference_choices_reply(confident, session.language),
                user_text=clean_text,
                required_action="Ask the traveler to choose one of these verified matching trips. Preserve every listed trip name.",
            )
            session.tools_used = ["search_trips"]
            session.fallback_used = False
            session.stage = "trip_selection_required"
            return True
        if (
            self._has_trip_reference_words(clean_text)
            and not self._extract_phone_candidate(clean_text)
            and len(self._trip_reference_tokens(query)) <= 4
        ):
            self._append_agent_reply(
                session,
                message_key="trip.reference.no_match",
                base_text=self._trip_reference_unknown_reply(query, session.language),
                user_text=clean_text,
                required_action="Ask one focused clarification for the intended trip name.",
            )
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
        if any(term in lowered for term in ("\u063a\u0631\u0641\u0629", "\u0623\u0648\u0636\u0629")) and not any(term in lowered for term in ("\u0635\u0648\u0631", "\u0627\u0634\u0648\u0641", "\u0623\u0634\u0648\u0641", "\u0627\u0631\u0633\u0644")):
            return False
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
            return f"There are no official images for {trip_name} yet."
        if language.startswith("ar"):
            lines = [f"هذه الصور الرسمية المؤكدة من CRM لرحلة {trip_name}:"]
            for index, item in enumerate(media[:5], start=1):
                label = "الصورة الرئيسية" if item.get("image_type") == "cover" else "صورة إضافية"
                lines.append(f"{index}) {label}: {item.get('alt_text') or 'Official trip image'}")
            return "\n".join(lines)
        lines = [f"Here are the official images for {trip_name}:"]
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
        message: dict[str, Any] = {"role": "assistant", "text": text, "state": "completed"}
        safe_media = [dict(item) for item in (media or []) if isinstance(item, dict) and (item.get("public_url") or item.get("url"))]
        if safe_media:
            message["text"] = cls._strip_media_urls(text) or (
                "هذه الصورة الرسمية المؤكدة من CRM."
                if str(language or "").startswith("ar")
                else "Here is the official trip image."
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
                else "Please tell me which trip you mean first, and I will show the official images."
            )
            self._append_agent_reply(
                session,
                message_key="trip.media.trip_required",
                base_text=reply,
                user_text=clean_text,
                required_action="Ask which trip the traveler means before showing official images.",
            )
            session.tools_used = []
            session.fallback_used = False
            session.stage = "trip_media_shared"
            return True
        trip_id = str(trip.get("trip_id") or session.selected_trip_id or "").strip()
        media_result = self._read_only_tools.get_trip_media(trip_id=trip_id)
        reply = self._trip_media_reply(media_result, trip, session.language)
        media = list(media_result.get("media") or []) if isinstance(media_result, dict) else []
        self._append_agent_reply(
            session,
            message_key="trip.media.results",
            base_text=reply,
            user_text=clean_text,
            required_action="Describe only the official images returned in the base text.",
            media=media,
        )
        session.tools_used = ["get_trip_media"]
        session.fallback_used = False
        session.stage = "trip_media_shared"
        return True

    def _handle_navigation_intent(self, session: SessionState, clean_text: str) -> bool:
        if self._is_start_over_intent(clean_text):
            self._reset_booking_state(session)
            session.stage = "trip_type_required" if self._preview_has_verified_traveler(session) else "identity_required"
            reply = (
                "Sure. We can start again. Please share your WhatsApp number first."
                if session.stage == "identity_required"
                else "Sure. We can start again. Are you looking for a local trip or an international trip?"
            )
            session.messages.append({"role": "user", "text": clean_text})
            self._append_agent_reply(
                session,
                message_key="navigation.start_over",
                base_text=reply,
                user_text=clean_text,
                required_action="Acknowledge the reset and ask for the next required detail.",
            )
            session.tools_used = []
            session.fallback_used = False
            return True
        if self._is_cancel_intent(clean_text):
            session.booking_confirmation_requested = False
            session.booking_confirmed = False
            session.handoff_state = "cancelled"
            session.stage = "waiting"
            session.messages.append({"role": "user", "text": clean_text})
            self._append_agent_reply(
                session,
                message_key="navigation.cancel",
                base_text="No problem. I will stop this booking flow for now.",
                user_text=clean_text,
                required_action="Acknowledge cancellation without creating any booking.",
            )
            session.tools_used = []
            session.fallback_used = False
            return True

        changed_flight = self._flight_option_from_text(clean_text)
        if changed_flight and session.stage in {"flight_option_required", "awaiting_passport_upload", "booking_confirmation_required", "booking_ready", "waiting"}:
            session.flight_option = changed_flight
            session.booking_confirmation_requested = False
            session.booking_confirmed = False
            self._clear_passport_state(session)
            self._update_collection_state(session, flight_option=True)
            context = self._build_context(session, clean_text)
            decision = self._workflow_policy.evaluate(context)
            session.stage = decision.state
            session.messages.append({"role": "user", "text": clean_text})
            if decision.required_step in _BACKEND_OWNED_COLLECTION_STEPS:
                reply = self._backend_required_step_reply(session, decision, clean_text)
            elif decision.required_step == "create_booking_draft":
                session.booking_confirmation_requested = True
                session.stage = "booking_confirmation_required"
                reply = self._booking_confirmation_summary(session)
            else:
                reply = decision.assistant_message
            self._append_agent_reply(
                session,
                message_key="navigation.flight_changed",
                base_text=reply,
                user_text=clean_text,
                required_action="Acknowledge the flight-choice change and continue from the recalculated next step.",
                session_context={**context, "workflow_policy": decision.to_context()},
            )
            session.tools_used = []
            session.fallback_used = False
            return True

        if not self._is_back_intent(clean_text):
            return False
        session.messages.append({"role": "user", "text": clean_text})
        session.booking_confirmation_requested = False
        session.booking_confirmed = False
        if session.stage in {"awaiting_passport_upload", "booking_confirmation_required", "booking_ready"}:
            self._clear_passport_state(session)
            if self._trip_supports_flights(self._selected_trip(session)):
                session.flight_option = ""
                self._update_collection_state(session, flight_option=False)
                session.stage = "flight_option_required"
                reply = "Sure. Do you want this trip with flights or without flights?"
            else:
                session.stage = "room_type_required"
                reply = "Sure. Please choose the room option again."
        elif session.stage == "flight_option_required":
            session.flight_option = ""
            self._update_collection_state(session, flight_option=False)
            session.stage = "group_size_required"
            reply = "Sure. How many travelers should I put on this booking request?"
        elif session.stage == "group_size_required":
            session.group_size = 1
            self._update_collection_state(session, group_size=False)
            session.stage = "room_type_required"
            reply = "Sure. Please choose your preferred room option again."
        elif session.stage == "room_type_required":
            session.room_type = ""
            session.room_requirements = {}
            self._update_collection_state(session, room_type=False)
            session.stage = "traveler_gender_required"
            reply = "Sure. Are the travelers boys/male or girls/female?"
        else:
            session.messages.pop()
            return False
        self._append_agent_reply(
            session,
            message_key="navigation.back",
            base_text=reply,
            user_text=clean_text,
            required_action="Acknowledge going back and ask only for the previous required booking detail.",
        )
        session.tools_used = []
        session.fallback_used = False
        return True

    @classmethod
    def _is_back_intent(cls, text: str) -> bool:
        return cls._compact_intent(text) in {
            "back",
            "goback",
            "previous",
            "changemychoice",
            "changechoice",
            "changeanswer",
            "رجوع",
            "ارجع",
            "السابق",
            "غيراختياري",
        }

    @classmethod
    def _is_cancel_intent(cls, text: str) -> bool:
        return cls._compact_intent(text) in {"cancel", "stop", "end", "nevermind", "الغاء", "إلغاء", "وقف"}

    @classmethod
    def _is_start_over_intent(cls, text: str) -> bool:
        return cls._compact_intent(text) in {"startover", "restart", "startagain", "newrequest", "ابدأمنجديد", "منالأول"}

    @classmethod
    def _flight_option_from_text(cls, text: str) -> str:
        normalized = cls._normalize_trip_reference(text)
        compact = cls._compact_intent(text)
        if compact in {
            "withoutflight",
            "withoutflights",
            "without",
            "witout",
            "withot",
            "whitout",
            "wihout",
            "wthout",
            "noflight",
            "noflights",
            "idontwantaflight",
            "idontwantflight",
            "donotwantaflight",
            "donotwantflight",
            "بدونطيران",
            "منغيرطيران",
            "مشعايزطيران",
            "مشعايزةطيران",
        } or "without flight" in normalized or "with out" in normalized or "w/out" in normalized or "no flight" in normalized or "do not want a flight" in normalized or "do not want flight" in normalized:
            return "Without Flight"
        if compact in {
            "withflight",
            "withflights",
            "with",
            "iwantaflight",
            "iwantflight",
            "flightinstead",
            "withflightinstead",
            "معطيران",
            "عايزطيران",
            "عايزةطيران",
        } or "with flight" in normalized:
            return "With Flight"
        return ""

    @classmethod
    def _compact_intent(cls, text: str) -> str:
        return re.sub(r"[^0-9a-z\u0600-\u06ff]+", "", cls._normalize_trip_reference(text))

    @classmethod
    def _trip_supports_flights(cls, trip: dict[str, Any] | None) -> bool:
        trip = dict(trip or {})
        trip_type = str(trip.get("trip_type") or trip.get("type") or "").strip().casefold()
        if trip_type in {"local", "domestic"}:
            return False
        for key in ("supports_flights", "flights_supported", "flight_supported", "flight_available", "customer_can_request_flights"):
            if key in trip:
                return cls._as_bool(trip.get(key))
        policy = trip.get("flight_policy") if isinstance(trip.get("flight_policy"), dict) else {}
        mode = str(policy.get("mode") or "").strip().lower()
        if mode in {"not_supported", "no_flights", "without_flights_only"}:
            return False
        if "customer_can_request_flights" in policy:
            return cls._as_bool(policy.get("customer_can_request_flights"))
        return True

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value or "").strip().casefold() in {"1", "true", "yes", "y", "supported", "available"}

    @staticmethod
    def _clear_passport_state(session: SessionState) -> None:
        session.passport_name = ""
        session.passport_number = ""
        session.passport_expiry = ""
        session.passport_nationality = ""
        session.passport_attachment_ref = ""
        session._passport_step = ""

    def _passport_required_for_session(self, session: SessionState) -> bool:
        trip = self._selected_trip(session)
        for key in ("passport_required", "requires_passport"):
            if key in trip:
                return self._as_bool(trip.get(key))
        if "passport_required_with_flight" in trip:
            return session.flight_option == "With Flight" and self._as_bool(trip.get("passport_required_with_flight"))
        trip_type = str(trip.get("type") or trip.get("trip_type") or self._effective_trip_type(session) or "").strip().lower()
        return trip_type == "international"

    @staticmethod
    def _allowed_passport_attachment_ref(attachment_ref: str) -> bool:
        return str(attachment_ref or "").strip().lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".pdf"))

    def _clear_booking_dependent_state(self, session: SessionState) -> None:
        session.room_type = ""
        session.room_group = ""
        session.room_requirements = {}
        session.group_size = 1
        session.flight_option = ""
        session.currency = ""
        session.booking_confirmation_requested = False
        session.booking_confirmed = False
        self._clear_passport_state(session)
        self._update_collection_state(session, room_type=False, room_group=False, group_size=False, flight_option=False, currency=False)

    def _reset_booking_state(self, session: SessionState) -> None:
        session.trip_type = ""
        session.trip_query = ""
        session.selected_trip_id = ""
        session.selected_trip_name = ""
        session.booking_result = None
        self._clear_booking_dependent_state(session)
        preview = dict(session.preview or {})
        preview.pop("trip_result", None)
        preview.pop("trip_reference", None)
        session.preview = preview

    def _apply_trip_configuration_defaults(self, session: SessionState) -> None:
        trip = self._selected_trip(session)
        if not trip:
            return
        if not self._trip_supports_flights(trip):
            if session.flight_option != "Not Applicable":
                session.flight_option = "Not Applicable"
                self._update_collection_state(session, flight_option=True)
            self._clear_passport_state(session)

    @staticmethod
    def _handoff_id(session: SessionState) -> str:
        final_result = session.final_result if isinstance(session.final_result, dict) else {}
        return str(final_result.get("handoff_id") or "").strip()

    @classmethod
    def _has_active_handoff(cls, session: SessionState) -> bool:
        return str(session.handoff_state or "").strip().lower() in {"handed_off", "handoff_created", "handoff_pending", "assigned"} or bool(cls._handoff_id(session))

    @classmethod
    def _is_handoff_acknowledgement(cls, text: str) -> bool:
        normalized = cls._normalize_trip_reference(text)
        compact = cls._compact_intent(text)
        if cls._is_human_agent_request(text):
            return True
        if compact in {
            "ok",
            "okay",
            "thanks",
            "thankyou",
            "doit",
            "please",
            "yes",
            "confirm",
            "contactme",
            "callme",
            "sendit",
            "goahead",
            "تمام",
            "ماشي",
            "شكرا",
            "اعملها",
            "نفذ",
            "كلمني",
        }:
            return True
        return any(
            phrase in normalized
            for phrase in (
                "after review",
                "after reviewing",
                "contact me",
                "call me",
                "do it",
                "send it",
                "go ahead",
                "خليهم يكلموني",
                "بعد المراجعة",
            )
        )

    def _handoff_success_message(self, session: SessionState, reason_code: str = "") -> str:
        if session.language.startswith("ar"):
            if reason_code == "duplicate_phone_match":
                return "رقم واتساب ده مرتبط بأكتر من ملف مسافر، لذلك أرسلت الطلب لفريق Ravel للمراجعة، وسيتواصل معك أحد أعضاء الفريق."
            if reason_code == "room_capacity":
                return "خيار الغرفة المطلوب غير متاح حاليا للمجموعة كلها. أرسلت الطلب لفريق Ravel لمراجعة البدائل المتاحة، وسيتواصل معك أحد أعضاء الفريق بعد المراجعة."
            return "تم إرسال طلبك لفريق Ravel للمراجعة، وسيتواصل معك أحد أعضاء الفريق بعد التحقق من التفاصيل."
        if session.language.startswith("ar"):
            if reason_code == "duplicate_phone_match":
                return "رقم واتساب هذا مرتبط بأكثر من ملف مسافر، لذلك أرسلت الطلب إلى فريق Ravel للمراجعة وسيتواصل معك أحد أعضاء الفريق."
            if reason_code == "room_capacity":
                return "خيار الغرفة المطلوب غير متاح حاليا للمجموعة كلها. أرسلت الطلب إلى فريق Ravel لمراجعة البدائل المتاحة، وسيتواصل معك أحد أعضاء الفريق بعد المراجعة."
            return "تم إرسال طلبك إلى فريق Ravel للمراجعة، وسيتواصل معك أحد أعضاء الفريق بعد التحقق من التفاصيل."
        if reason_code == "duplicate_phone_match":
            return "This WhatsApp number matches more than one traveler profile, so I've sent the request to the Ravel team for review. A team member will follow up with you."
        if reason_code == "room_capacity":
            return "The requested room option is not currently available for the full group. I've sent the request to the Ravel team to check the available alternatives, and a team member will follow up with you."
        return "I've sent your request to the Ravel team for review, and a team member will follow up with you once they have an update."

    def _handoff_already_under_review_message(self, session: SessionState) -> str:
        if session.language.startswith("ar"):
            return "طلبك موجود بالفعل مع فريق Ravel للمراجعة. سيتواصلون معك بعد التحقق من البدائل المتاحة."
        if session.language.startswith("ar"):
            return "طلبك موجود بالفعل مع فريق Ravel للمراجعة. سيتواصلون معك بعد التحقق من البدائل المتاحة."
        return "Your request is already with the Ravel team for review. They'll follow up with you once they've checked the available options."

    def _handle_existing_handoff_message(self, session: SessionState, clean_text: str) -> bool:
        if not self._has_active_handoff(session):
            return False
        if not self._is_handoff_acknowledgement(clean_text):
            return False
        session.messages.append({"role": "user", "text": clean_text})
        self._append_agent_reply(
            session,
            message_key="workflow.handoff.already_under_review",
            base_text=self._handoff_already_under_review_message(session),
            user_text=clean_text,
            required_action="Confirm that an existing Ravel team review is already active. Do not create a duplicate handoff.",
        )
        session.tools_used = []
        session.fallback_used = False
        agent_logger.info("Duplicate handoff prevented session=%s handoff=%s", session.id, self._handoff_id(session))
        return True

    def _execute_manual_handoff(self, session: SessionState, clean_text: str) -> bool:
        session_context = self._build_context(session, clean_text)
        payload = {
            "traveler_id": session_context.get("traveler_id") or "",
            "raw_phone": session.raw_phone or session.pending_raw_phone,
            "country_code": session.country_code or self.settings.default_country_code,
            "lead_id": session_context.get("lead_id") or "",
            "trip_id": session.selected_trip_id,
            "flow_key": f"tool_calling:{session.id}",
            "reason_code": "customer_requested_human",
            "reason_text": "The traveler explicitly requested a human agent.",
            "priority": "High",
            "channel": "web",
            "customer_name": session.customer_name,
            "agent_summary": "Customer asked to speak with a Ravel team member.",
            "customer_summary": clean_text,
            "notes": f"Session {session.id}. Latest customer message: {clean_text}",
            "update_lead": True,
            "deduplicate_open": True,
            "user_requested_human": True,
        }
        agent_logger.info("Handoff requested session=%s reason=customer_requested_human", session.id)
        try:
            result = self._write_executor.execute(
                action="create_handoff",
                payload=payload,
                session_context={**session_context, "user_requested_human": True},
            )
        except Exception as exc:
            agent_logger.warning("Manual handoff could not be created session=%s error=%s", session.id, exc)
            result = {}
        session.messages.append({"role": "user", "text": clean_text})
        if isinstance(result, dict) and result.get("executed") and str(result.get("result_id") or "").strip():
            self._apply_result(session, {"write_results": [result], "tool_requests": []})
            session.handoff_state = "handed_off"
            session.stage = "human_handoff_required"
            if not isinstance(session.final_result, dict) or not session.final_result.get("handoff_id"):
                session.final_result = {
                    "traveler": session_context.get("known_traveler") if isinstance(session_context.get("known_traveler"), dict) else None,
                    "lead_id": str(session_context.get("lead_id") or ""),
                    "handoff_id": str(result.get("result_id") or ""),
                    "handoff_required": True,
                    "handoff_reason": "customer_requested_human",
                    "write_result": {"handoff_case": result.get("handoff_case") if isinstance(result.get("handoff_case"), dict) else {}},
                }
            self._append_authoritative_reply(
                session,
                message_key="workflow.handoff.customer_requested_human",
                base_text=self._handoff_success_message(session, "customer_requested_human"),
            )
            session.tools_used = ["create_handoff"]
            session.fallback_used = False
            agent_logger.info("Handoff created session=%s handoff=%s reason=customer_requested_human", session.id, result.get("result_id"))
            return True

        self._append_agent_reply(
            session,
            message_key="workflow.handoff_failed.customer_requested_human",
            base_text=self._safe_response_fallback(session, message_key="workflow.handoff_failed.customer_requested_human"),
            user_text=clean_text,
            required_action="Tell the traveler the automatic handoff could not be submitted. Do not claim success.",
            session_context=session_context,
        )
        session.stage = "waiting"
        session.handoff_state = "handoff_failed"
        session.tools_used = []
        session.fallback_used = True
        return True

    @staticmethod
    def _is_simple_greeting(text: str) -> bool:
        normalized = " ".join(str(text or "").strip().casefold().split())
        if not normalized:
            return False
        greetings = {
            "hi",
            "hello",
            "hey",
            "hello there",
            "\u0647\u0644\u0627",
            "\u0645\u0631\u062d\u0628\u0627",
            "\u0627\u0647\u0644\u0627",
            "\u0623\u0647\u0644\u0627",
            "\u0627\u0644\u0633\u0644\u0627\u0645 \u0639\u0644\u064a\u0643\u0645",
            "\u0647\u0627\u064a",
        }
        return normalized in greetings or (normalized.startswith("\u0627\u0644\u0633\u0644\u0627\u0645") and len(normalized.split()) <= 4)

    def _handle_identity_required_greeting(self, session: SessionState, clean_text: str) -> bool:
        if session.raw_phone or session.pending_raw_phone or self._extract_phone_candidate(clean_text):
            return False
        if session.stage not in {"identity_required", "awaiting_phone", "gemini_conversation", "collecting_context"}:
            return False
        compact_intent = self._compact_intent(clean_text)
        early_booking_intent = compact_intent in {
            "book",
            "booking",
            "reserve",
            "reservation",
            "createbooking",
            "makebooking",
            "احجز",
            "حجز",
        }
        if not (self._is_simple_greeting(clean_text) or self._looks_affirmative(clean_text) or early_booking_intent):
            return False
        session.messages.append({"role": "user", "text": clean_text})
        session.stage = "identity_required"
        reply = (
            "\u0623\u0647\u0644\u0627 \u0628\u064a\u0643. \u0645\u0646 \u0641\u0636\u0644\u0643 \u0623\u0631\u0633\u0644 \u0631\u0642\u0645 \u0648\u0627\u062a\u0633\u0627\u0628\u0643 \u0623\u0648\u0644\u0627 \u0639\u0644\u0634\u0627\u0646 \u0623\u0631\u0627\u062c\u0639 \u0645\u0644\u0641\u0643 \u0628\u0623\u0645\u0627\u0646\u060c \u0648\u0628\u0639\u062f\u0647\u0627 \u0623\u0643\u0645\u0644 \u0645\u0639\u0643 \u0637\u0644\u0628 \u0627\u0644\u0631\u062d\u0644\u0629."
            if session.language.startswith("ar")
            else "Hello. Please share your WhatsApp number first so I can check your traveler profile safely, then I will continue with your trip request."
        )
        session.messages.append(self._assistant_message(text=reply, language=session.language))
        session.tools_used = []
        session.fallback_used = False
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

        if self._handle_post_booking_message(session, clean_text):
            return session

        if self._handle_booking_confirmation_reply(session, clean_text):
            return session

        if self._handle_navigation_intent(session, clean_text):
            return session

        if self._handle_existing_handoff_message(session, clean_text):
            return session

        if self._is_human_agent_request(clean_text):
            self._execute_manual_handoff(session, clean_text)
            return session

        identity_response = self._identity_policy.evaluate(clean_text)
        if identity_response is not None:
            session.language = identity_response.language
            session.messages.append({"role": "user", "text": clean_text})
            self._append_agent_reply(
                session,
                message_key=f"identity_policy.{identity_response.intent}",
                base_text=identity_response.text,
                user_text=clean_text,
                required_action="Answer the direct identity question honestly without pretending to be human.",
            )
            session.tools_used = []
            session.fallback_used = False
            agent_logger.info(
                "Tool-calling session %s answered %s from backend identity policy",
                session.id,
                identity_response.intent,
            )
            return session

        if self._handle_identity_required_greeting(session, clean_text):
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
                self._append_agent_reply(
                    session,
                    message_key=f"privacy_policy.{privacy_response.intent}",
                    base_text=privacy_response.text,
                    user_text=clean_text,
                    required_action="Apply the privacy guardrail and keep the answer concise.",
                    session_context=privacy_context,
                )
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
        self._apply_trip_configuration_defaults(session)
        self._ensure_room_requirements_for_group(session)
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

        if workflow_decision.required_step == "search_matching_trips":
            self._load_verified_trip_results(session)
            session_context = self._build_context(session, clean_text)
            session_context.update(hints)
            workflow_decision = self._workflow_policy.evaluate(session_context)
            session_context["workflow_policy"] = workflow_decision.to_context()
            session.stage = workflow_decision.state

        if workflow_decision.handoff_required:
            if self._execute_policy_handoff(session, session_context, workflow_decision):
                return session
            self._append_agent_reply(
                session,
                message_key=f"workflow.handoff_failed.{workflow_decision.state}",
                base_text=self._safe_response_fallback(
                    session,
                    message_key=f"workflow.handoff_failed.{workflow_decision.state}",
                    base_text="",
                ),
                user_text=clean_text,
                required_action="Tell the traveler the automatic review request could not be submitted. Do not claim a handoff was created.",
                session_context=session_context,
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
            if self._handle_trip_selection_contextual_reply(session, clean_text, workflow_decision):
                agent_logger.info(
                    "Tool-calling session %s handled contextual trip selection reply",
                    session.id,
                )
                return session
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
            if not step_value_captured:
                # An unclear customer input is not an incomplete assistant output.
                # Keep the pending field unchanged and answer from the deterministic policy.
                self._append_authoritative_reply(
                    session,
                    message_key=f"workflow.unclear_input.{workflow_decision.required_step}",
                    base_text=(
                        self._canonical_trip_search_reply(session)
                        if workflow_decision.required_step == "select_trip"
                        else self._natural_interruption_fallback(session, workflow_decision, clean_text)
                    ),
                )
                session.stage = workflow_decision.state
                session.tools_used = [str(preloaded_tool_event["name"])] if preloaded_tool_event else []
                session.fallback_used = False
                agent_logger.info(
                    "Tool-calling session %s handled unclear input without model rewrite step=%s",
                    session.id,
                    workflow_decision.required_step,
                )
                return session
            base_reply = (
                self._canonical_trip_search_reply(session)
                if workflow_decision.required_step == "select_trip"
                else self._backend_required_step_reply(session, workflow_decision, clean_text)
            )
            if step_value_captured:
                self._append_authoritative_reply(
                    session,
                    message_key=f"workflow.required_step.{workflow_decision.required_step}",
                    base_text=base_reply,
                )
            else:
                self._append_agent_reply(
                    session,
                    message_key=f"workflow.required_step.{workflow_decision.required_step}",
                    base_text=base_reply,
                    user_text=clean_text,
                    required_action=(
                        "Generate the traveler-facing wording for this required workflow step. "
                        "Preserve all listed verified trip names/options and ask only one question."
                    ),
                    session_context=session_context,
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
            summary = self._booking_confirmation_summary(session)
            if step_value_captured:
                self._append_authoritative_reply(session, message_key="booking.confirmation_summary", base_text=summary)
            else:
                self._append_agent_reply(
                    session,
                    message_key="booking.confirmation_summary",
                    base_text=summary,
                    user_text=clean_text,
                    required_action="Ask the traveler to confirm the verified booking draft details before any write action.",
                    session_context=session_context,
                )
            session.tools_used = [str(preloaded_tool_event["name"])] if preloaded_tool_event else []
            session.fallback_used = False
            agent_logger.info("Tool-calling session %s requested booking confirmation before write", session.id)
            return session

        preloaded_tool_results = [preloaded_tool_event] if preloaded_tool_event else []
        model_session_context = self._traveler_safe_context(session_context)
        turn = self._coordinator.think(agent_state, crm_facts=model_session_context, conversation=session.messages[-12:], tool_results=preloaded_tool_results)
        agent_state.subgoal = str(turn.decision.get("reason") or "")
        model_session_context["persona"] = turn.context.get("persona")
        model_session_context["memory"] = turn.context.get("memory")
        model_session_context["agent_state"] = agent_state.to_dict()
        model_session_context["system_constraints"] = turn.context.get("system_constraints")
        model_session_context["available_tools"] = [tool.name for tool in self._coordinator.tool_manager.describe()]
        agent_logger.info("Coordinator entered session=%s", session.id)
        agent_logger.info("Persona loaded session=%s", session.id)
        agent_logger.info("Memory loaded session=%s", session.id)
        agent_logger.info("State updated session=%s", session.id)
        agent_logger.info("Context built session=%s", session.id)
        agent_logger.info("Planner decision session=%s action=%s tool=%s", session.id, turn.decision.get("action"), turn.decision.get("tool_name"))

        result = self._conversation_ai.respond(
            user_message=clean_text,
            session_context=model_session_context,
            conversation_history=session.messages[-12:],
        )

        reply = self._normalize_reply(str(result.get("reply") or ""), session.language)
        error = str(result.get("error") or "").strip()
        if error:
            agent_logger.warning("Tool-calling session %s failed: %s", session.id, error)
            session.stage = "error"
            agent_state.add_pending_task("recover from model error")
            error_reply = (
                "\u0623\u0648\u0627\u062c\u0647 \u0645\u0634\u0643\u0644\u0629 \u0645\u0624\u0642\u062a\u0629 \u0641\u064a \u0625\u0643\u0645\u0627\u0644 \u0627\u0644\u0637\u0644\u0628 \u0627\u0644\u0622\u0646. \u0645\u0646 \u0641\u0636\u0644\u0643 \u062d\u0627\u0648\u0644 \u0645\u0631\u0629 \u0623\u062e\u0631\u0649 \u0628\u0639\u062f \u0642\u0644\u064a\u0644."
                if session.language.startswith("ar")
                else "I'm having trouble completing that request right now. Please try again in a moment."
            )
            session.messages.append({"role": "assistant", "text": error_reply})
            session.fallback_used = True
            return session
            session.messages.append(
                {
                    "role": "assistant",
                    "text": "I’m having trouble completing that request right now. Please try again in a moment.",
                }
            )
            session.fallback_used = True
            return session

        media = self._media_from_agent_result(result)
        response_fallback_used = False
        validation_issue = self._customer_reply_validation_issue(reply)
        if validation_issue:
            agent_logger.warning(
                "Model reply rejected session=%s reason=%s text=%r",
                session.id,
                validation_issue,
                reply[:160],
            )
            reply = self._safe_response_fallback(session, message_key="model.response.invalid", base_text="")
            response_fallback_used = True
        session.messages.append(self._assistant_message(text=reply, language=session.language, media=media))
        session.tools_used = [
            str(event.get("name") or "").strip()
            for event in result.get("tool_requests", [])
            if isinstance(event, dict) and str(event.get("name") or "").strip()
        ]
        if preloaded_tool_event and preloaded_tool_event.get("name"):
            session.tools_used = [str(preloaded_tool_event["name"]), *[tool for tool in session.tools_used if tool != preloaded_tool_event["name"]]]
        session.fallback_used = response_fallback_used

        self._apply_result(session, result)
        if workflow_decision.required_step == "search_matching_trips":
            # The model may describe CRM results, but it must not own their
            # numbering or invent a result while the backend is still searching.
            session.messages[-1] = {
                "role": "assistant",
                "text": self._agent_reply(
                    session,
                    message_key="trip.search.results",
                    base_text=self._canonical_trip_search_reply(session),
                    user_text=clean_text,
                    required_action="Present exactly these verified trip search results without adding trip facts.",
                    session_context=session_context,
                ),
                "state": "completed",
            }
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
        if not isinstance(result, dict) or not result.get("executed") or not str(result.get("result_id") or "").strip():
            return False
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
        self._append_authoritative_reply(
            session,
            message_key=f"workflow.handoff.{decision.state}",
            base_text=self._handoff_success_message(session, reason_code),
        )
        session.tools_used = ["create_handoff"]
        session.fallback_used = False
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
        ref = str(attachment_ref or "").strip()
        if session.stage != "awaiting_passport_upload" or not self._passport_required_for_session(session):
            agent_logger.warning("Tool-calling session %s: ignored passport attachment outside required passport step", session.id)
            return
        if not self._allowed_passport_attachment_ref(ref):
            agent_logger.warning("Tool-calling session %s: ignored unsupported passport attachment -> %s", session.id, ref)
            return
        session.passport_attachment_ref = ref
        agent_logger.info("Tool-calling session %s: passport attachment uploaded pending review -> %s", session.id, session.passport_attachment_ref)

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
                    session.booking_completed = True
                    if session.stage == "completed":
                        session.stage = "post_booking_support"
                if "final_result" in session_update and isinstance(session_update.get("final_result"), dict):
                    session.final_result = dict(session_update["final_result"])
                    if session.final_result.get("handoff_id"):
                        session.booking_completed = True
                        if session.stage in {"completed", "handed_off"}:
                            session.stage = "post_booking_support"
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
                    query = str(
                        tool_result.get("destination")
                        or tool_result.get("query")
                        or (event.get("input") or {}).get("destination")
                        or (event.get("input") or {}).get("query")
                        or session.trip_query
                        or ""
                    ).strip()
                    filtered_result = self._filter_trip_result_by_query(tool_result, query)
                    preview = dict(session.preview or {})
                    preview["trip_result"] = {
                        "open_trips": list(filtered_result.get("open_trips") or []),
                        "date_tbd_trips": list(filtered_result.get("date_tbd_trips") or []),
                    }
                    preview["trip_query"] = query
                    session.preview = preview
                    if query:
                        session.trip_query = query

    @staticmethod
    def _preview_from_context(session_context: dict[str, Any]) -> dict[str, Any]:
        traveler = session_context.get("known_traveler") if isinstance(session_context.get("known_traveler"), dict) else {}
        preview: dict[str, Any] = {}
        if traveler:
            preview["traveler"] = traveler
        return preview

    @classmethod
    def _traveler_safe_context(cls, session_context: dict[str, Any]) -> dict[str, Any]:
        safe = dict(session_context or {})
        if isinstance(safe.get("selected_trip"), dict):
            safe["selected_trip"] = cls._traveler_safe_trip(safe["selected_trip"])
        trip_result = safe.get("trip_result") if isinstance(safe.get("trip_result"), dict) else {}
        if trip_result:
            safe["trip_result"] = {
                "open_trips": [cls._traveler_safe_trip(trip) for trip in list(trip_result.get("open_trips") or []) if isinstance(trip, dict)],
                "date_tbd_trips": [cls._traveler_safe_trip(trip) for trip in list(trip_result.get("date_tbd_trips") or []) if isinstance(trip, dict)],
            }
        workflow_policy = safe.get("workflow_policy") if isinstance(safe.get("workflow_policy"), dict) else {}
        if workflow_policy:
            safe["workflow_policy"] = {
                **workflow_policy,
                "assistant_message": format_agent_reply(str(workflow_policy.get("assistant_message") or "")),
            }
        return safe

    @staticmethod
    def _traveler_safe_trip(trip: dict[str, Any]) -> dict[str, Any]:
        forbidden = {
            "remaining_places",
            "single_total",
            "double_total",
            "triple_total",
            "single_remaining",
            "double_remaining",
            "triple_remaining",
            "draft_holds_single",
            "draft_holds_double",
            "draft_holds_triple",
            "available_single",
            "available_double",
            "available_triple",
            "boys_double",
            "girls_double",
            "boys_triple",
            "girls_triple",
        }
        safe = {key: value for key, value in dict(trip or {}).items() if key not in forbidden}
        for room_type, key in (("single", "available_single"), ("double", "available_double"), ("triple", "available_triple")):
            if key in trip:
                try:
                    safe[f"{room_type}_availability"] = "Available" if int(trip.get(key) or 0) > 0 else "Unavailable"
                except (TypeError, ValueError):
                    safe[f"{room_type}_availability"] = "Unavailable"
        return safe

    @staticmethod
    def _extract_phone_candidate(text: str) -> str:
        normalized = str(text or "").translate(_DIGIT_TRANSLATION)
        if _BIRTHDAY_RE.fullmatch(normalized.strip()):
            return ""
        match = _PHONE_CANDIDATE_RE.search(normalized)
        if not match:
            return ""
        candidate = match.group(0).strip()
        if _BIRTHDAY_RE.fullmatch(candidate):
            return ""
        digits = re.sub(r"\D", "", candidate)
        if len(digits) < 9:
            return ""
        return candidate

    @staticmethod
    def _number_from_text(value: str) -> int:
        normalized = str(value or "").strip().casefold()
        words = {
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "\u0648\u0627\u062d\u062f": 1,
            "\u0648\u0627\u062d\u062f\u0629": 1,
            "\u0627\u062a\u0646\u064a\u0646": 2,
            "\u0627\u062b\u0646\u064a\u0646": 2,
            "\u062a\u0644\u0627\u062a\u0629": 3,
            "\u062b\u0644\u0627\u062b\u0629": 3,
        }
        if normalized in words:
            return words[normalized]
        match = re.search(r"\b([1-9][0-9]?)\b", normalized)
        return int(match.group(1)) if match else 0

    @classmethod
    def _extract_mixed_people_counts(cls, text: str) -> dict[str, int]:
        lowered = str(text or "").strip().casefold()
        counts = {"boys": 0, "girls": 0}
        group_terms = {
            "boys": ("boy", "boys", "male", "males", "\u0648\u0644\u062f", "\u0627\u0648\u0644\u0627\u062f", "\u0623\u0648\u0644\u0627\u062f", "\u0634\u0628\u0627\u0628"),
            "girls": ("girl", "girls", "female", "females", "\u0628\u0646\u062a", "\u0628\u0646\u0627\u062a"),
        }
        number_pattern = r"[1-9][0-9]?|one|two|three|four|five|six|seven|eight|nine"
        for group, terms in group_terms.items():
            for term in terms:
                for match in re.finditer(
                    rf"(?:\b({number_pattern})\s+{re.escape(term)}s?\b)|(?:\b{re.escape(term)}s?\s+([1-9][0-9]?)\b)",
                    lowered,
                ):
                    counts[group] += cls._number_from_text(match.group(1) or match.group(2) or "1") or 1
        return counts

    @classmethod
    def _has_mixed_group_hint(cls, text: str) -> bool:
        lowered = " ".join(str(text or "").strip().casefold().split())
        if not lowered:
            return False
        english_patterns = (
            r"\bmix(?:ed)?(?:\s+group)?\b",
            r"\bboys?\s+(?:and|&|\+|with)\s+girls?\b",
            r"\bgirls?\s+(?:and|&|\+|with)\s+boys?\b",
            r"\bmales?\s+(?:and|&|\+|with)\s+females?\b",
            r"\bfemales?\s+(?:and|&|\+|with)\s+males?\b",
        )
        if any(re.search(pattern, lowered) for pattern in english_patterns):
            return True
        arabic_terms = (
            "\u0645\u062e\u062a\u0644\u0637",
            "\u0645\u062e\u062a\u0644\u0637\u0629",
            "\u0645\u0643\u0633",
            "\u0648\u0644\u0627\u062f \u0648\u0628\u0646\u0627\u062a",
            "\u0627\u0648\u0644\u0627\u062f \u0648\u0628\u0646\u0627\u062a",
            "\u0623\u0648\u0644\u0627\u062f \u0648\u0628\u0646\u0627\u062a",
            "\u0634\u0628\u0627\u0628 \u0648\u0628\u0646\u0627\u062a",
            "\u0628\u0646\u0627\u062a \u0648\u0648\u0644\u0627\u062f",
            "\u0628\u0646\u0627\u062a \u0648\u0627\u0648\u0644\u0627\u062f",
            "\u0628\u0646\u0627\u062a \u0648\u0623\u0648\u0644\u0627\u062f",
            "\u0630\u0643\u0648\u0631 \u0648\u0627\u0646\u0627\u062b",
            "\u0630\u0643\u0648\u0631 \u0648\u0625\u0646\u0627\u062b",
        )
        return any(term in lowered for term in arabic_terms)

    @classmethod
    def _extract_room_requirements(cls, text: str, default_room_type: str = "") -> dict[str, Any]:
        lowered = str(text or "").strip().casefold()
        room_word_present = any(token in lowered for token in ("room", "rooms", "\u063a\u0631\u0641\u0629", "\u063a\u0631\u0641\u062a\u064a\u0646", "\u0623\u0648\u0636\u0629", "\u0627\u0648\u0636\u0629"))
        if not room_word_present:
            return {}
        room_type = default_room_type
        if "single" in lowered:
            room_type = "Single"
        elif "double" in lowered:
            room_type = "Double"
        elif "triple" in lowered:
            room_type = "Triple"
        requirements: list[dict[str, Any]] = []
        number_pattern = r"[1-9][0-9]?|one|two|three|four|five|six|seven|eight|nine"
        for group, terms in {
            "boys": ("boys", "boy", "male", "men", "\u0627\u0648\u0644\u0627\u062f", "\u0623\u0648\u0644\u0627\u062f", "\u0634\u0628\u0627\u0628"),
            "girls": ("girls", "girl", "female", "women", "\u0628\u0646\u0627\u062a"),
        }.items():
            group_count = 0
            for term in terms:
                patterns = [
                    rf"\b({number_pattern})\s+(?:\w+\s+)?rooms?\s+(?:for\s+)?{re.escape(term)}\b",
                    rf"\b({number_pattern})\s+{re.escape(term)}\s+rooms?\b",
                    rf"\b(?:rooms?\s+)?(?:for\s+)?{re.escape(term)}\s+([1-9][0-9]?)\b",
                ]
                for pattern in patterns:
                    for match in re.finditer(pattern, lowered):
                        group_count += cls._number_from_text(match.group(1)) or 1
            if group_count:
                requirements.append({"room_type": room_type or "Double", "room_group": group, "rooms": group_count})
        has_boys = "boys" in lowered or "\u0634\u0628\u0627\u0628" in lowered or "\u0627\u0648\u0644\u0627\u062f" in lowered or "\u0623\u0648\u0644\u0627\u062f" in lowered
        has_girls = "girls" in lowered or "\u0628\u0646\u0627\u062a" in lowered
        if not requirements and (has_boys or has_girls):
            if has_boys:
                requirements.append({"room_type": room_type or "Double", "room_group": "boys", "rooms": 1})
            if has_girls:
                requirements.append({"room_type": room_type or "Double", "room_group": "girls", "rooms": 1})
        if not requirements:
            return {}
        return {
            "requirements": requirements,
            "boys_rooms_requested": sum(int(item["rooms"]) for item in requirements if item["room_group"] == "boys"),
            "girls_rooms_requested": sum(int(item["rooms"]) for item in requirements if item["room_group"] == "girls"),
        }

    @staticmethod
    def _extract_hints(text: str, *, stage: str = "") -> dict[str, Any]:
        digit_normalized = str(text or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
        lowered = digit_normalized.strip().lower()
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
                "\u0648\u0627\u062d\u062f": 1,
                "\u0648\u0627\u062d\u062f\u0629": 1,
                "\u0627\u062a\u0646\u064a\u0646": 2,
                "\u0627\u062b\u0646\u064a\u0646": 2,
                "\u062a\u0646\u064a\u0646": 2,
                "\u0627\u062b\u0646\u0627\u0646": 2,
                "\u062b\u0644\u0627\u062b\u0629": 3,
                "\u062a\u0644\u0627\u062a\u0629": 3,
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
        people_counts = ToolCallingSessionRuntime._extract_mixed_people_counts(text)
        mixed_group_hint = ToolCallingSessionRuntime._has_mixed_group_hint(text)
        if people_counts["boys"] and people_counts["girls"]:
            room_group = "mixed"
            if not group_size:
                group_size = people_counts["boys"] + people_counts["girls"]
        if "girls" in lowered or "بنات" in lowered:
            room_group = "girls"
        elif "boys" in lowered or "اولاد" in lowered or "رجال" in lowered:
            room_group = "boys"
        if people_counts["boys"] and people_counts["girls"]:
            room_group = "mixed"
        if mixed_group_hint:
            room_group = "mixed"
        room_requirements = ToolCallingSessionRuntime._extract_room_requirements(text, room_type)
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
            "candidate_room_requirements": room_requirements,
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
            session.trip_query = ""
            session.selected_trip_id = ""
            session.selected_trip_name = ""
            self._clear_booking_dependent_state(session)
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
            session.trip_query = destination
            self._update_collection_state(session, destination=True)

        preferred_date = str(hints.get("candidate_preferred_date") or "").strip()
        if preferred_date:
            session.preferred_date = preferred_date
            self._update_collection_state(session, preferred_date=True)

        birthday = str(hints.get("candidate_birthday") or "").strip()
        if birthday and session.customer_name and session.nationality:
            session.birthday = birthday
            self._update_collection_state(session, birthday=True)

        nationality = str(hints.get("candidate_nationality") or "").strip()
        if nationality and session.customer_name:
            session.nationality = nationality
            self._update_collection_state(session, nationality=True)

        flight_option = str(hints.get("candidate_flight_option") or "").strip()
        if flight_option:
            if session.flight_option and session.flight_option != flight_option:
                self._clear_passport_state(session)
                session.booking_confirmation_requested = False
                session.booking_confirmed = False
            session.flight_option = flight_option
            self._update_collection_state(session, flight_option=True)

        currency = str(hints.get("candidate_currency") or "").strip()
        if currency and session.customer_name and session.nationality and session.birthday:
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
        room_requirements = hints.get("candidate_room_requirements")
        if isinstance(room_requirements, dict) and room_requirements.get("requirements"):
            session.room_requirements = dict(room_requirements)
            if not session.room_type:
                first = next((item for item in room_requirements.get("requirements") or [] if isinstance(item, dict)), {})
                session.room_type = str(first.get("room_type") or session.room_type or "").strip()
            if room_requirements.get("boys_rooms_requested") and room_requirements.get("girls_rooms_requested"):
                session.room_group = "mixed"
            self._update_collection_state(session, room_type=True, room_group=True)

        raw_phone = str(hints.get("candidate_raw_phone") or "").strip()
        if raw_phone:
            session.raw_phone = raw_phone
            session.pending_raw_phone = raw_phone
            if not session.country_code:
                session.country_code = "20"

    @staticmethod
    def _extract_birthday(text: str) -> str:
        return normalize_birthdate_input(text)

    @classmethod
    def _extract_valid_full_name(cls, text: str) -> str:
        name = re.sub(r"\s+", " ", str(text or "").strip())
        if not name or len(name) > 80:
            return ""
        if _PHONE_CANDIDATE_RE.search(name):
            return ""
        if "?" in name or "\u061f" in name:
            return ""
        normalized = cls._normalize_trip_reference(name)
        if not normalized:
            return ""
        if cls._is_name_step_clarification(name) or cls._is_asking_for_known_name(name):
            return ""
        if cls._is_explanation_request(name) or cls._is_identity_question(name) or cls._is_human_agent_request(name):
            return ""
        if normalized in {
            "hi",
            "hello",
            "hey",
            "ok",
            "okay",
            "thanks",
            "thank you",
            "yes",
            "no",
            "not sure",
            "i do not know",
            "i don't know",
            "\u062a\u0645\u0627\u0645",
            "\u0645\u0627\u0634\u064a",
            "\u0634\u0643\u0631\u0627",
            "\u0645\u0634 \u0639\u0627\u0631\u0641",
            "\u0644\u0627",
        }:
            return ""
        parts = [part for part in name.split(" ") if part]
        if len(parts) < 3:
            return ""
        if not all(_NAME_TOKEN_RE.fullmatch(part) for part in parts):
            return ""
        return name

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
            name = self._extract_valid_full_name(text)
            if name:
                session.customer_name = name
                self._update_collection_state(session, customer_name=True)
                return True
            return False
        if session.stage == "traveler_gender_required" and not session.room_group:
            people_counts = self._extract_mixed_people_counts(normalized_text)
            mixed_group_hint = self._has_mixed_group_hint(normalized_text)
            if (people_counts["boys"] and people_counts["girls"]) or mixed_group_hint:
                session.room_group = "mixed"
                if people_counts["boys"] and people_counts["girls"] and (not session.group_size or session.group_size == 1):
                    session.group_size = people_counts["boys"] + people_counts["girls"]
                    self._update_collection_state(session, group_size=True)
                self._update_collection_state(session, room_group=True)
                return True
            boys_terms = {
                "boys", "boy", "male", "men", "man",
                "\u0634\u0628\u0627\u0628", "\u0630\u0643\u0648\u0631", "\u0631\u062c\u0627\u0644",
                "\u0631\u062c\u0627\u0644\u0629", "\u0627\u0648\u0644\u0627\u062f", "\u0623\u0648\u0644\u0627\u062f",
            }
            girls_terms = {
                "girls", "girl", "female", "women", "woman",
                "\u0628\u0646\u0627\u062a", "\u0625\u0646\u0627\u062b", "\u0627\u0646\u0627\u062b", "\u0646\u0633\u0627\u0621",
            }
            if option_number == 1 or lowered in boys_terms or any(token in lowered for token in boys_terms):
                session.room_group = "boys"
                self._update_collection_state(session, room_group=True)
                return True
            if option_number == 2 or lowered in girls_terms or any(token in lowered for token in girls_terms):
                session.room_group = "girls"
                self._update_collection_state(session, room_group=True)
                return True
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
            room_requirements = self._extract_room_requirements(normalized_text, session.room_type)
            if room_requirements.get("requirements"):
                session.room_requirements = dict(room_requirements)
                first = next((item for item in room_requirements.get("requirements") or [] if isinstance(item, dict)), {})
                session.room_type = str(first.get("room_type") or "Double").strip()
                if room_requirements.get("boys_rooms_requested") and room_requirements.get("girls_rooms_requested"):
                    session.room_group = "mixed"
                self._update_collection_state(session, room_type=True, room_group=bool(session.room_group))
                return True
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
                self._ensure_room_requirements_for_group(session)
                return True
            return False
        if session.stage == "flight_option_required" and not session.flight_option:
            flight_option = {1: "With Flight", 2: "Without Flight"}.get(option_number) or normalize_flight_option(normalized_text)
            if not flight_option:
                flight_option = self._flight_option_from_text(normalized_text)
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
