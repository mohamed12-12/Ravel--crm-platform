"""`SessionState`: the per-conversation data the whole agent reads and
mutates (identity, selected trip, booking-in-progress fields, collected
customer details, etc.) -- this is the shape of what `_sessions` in
tool_calling_runtime.py stores in memory per customer. `SessionFlowManager`
is the conversation driver for every `ai_agent_mode` except
`tool_calling` (plain deterministic scripted flow, and the simpler
non-tool-calling Gemini conversation mode) -- a separate code path from
`ToolCallingSessionRuntime` (tool_calling_runtime.py), which server.py's
`create_app()` picks between based on that setting.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from services.ai_agent.ai_agent_app.logger import agent_logger
from services.ai_agent.ai_agent_app.agent.date_parsing import normalize_birthdate_input
from services.ai_agent.ai_agent_app.agent.response_format import sanitize_traveler_reply
from scripts.phase1_readonly_agent import normalize_trip_type
from services.ai_agent.ai_agent_app.agent.gemini_agent import GeminiAgent
from services.crm.system_services import UnifiedCRMService
from services.crm.system_services.phone_normalization import normalize_phone_input


# ---------------------------------------------------------------------------
# Language detection helpers
# ---------------------------------------------------------------------------

_ARABIC_PATTERN = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")
_AI_REWRITABLE_MESSAGE_KEYS = {
    "session.ask_phone_first",
    "session.ask_phone_first_repeat",
    "session.ask_phone",
    "session.explain_phone_request",
    "session.explain_phone_request_repeat",
    "session.privacy_phone_request",
    "session.phone_negative",
    "session.phone_offtrack",
    "session.explain_country_code",
    "session.ask_country_code",
    "session.retry_country_code",
    "session.new_traveler_intake_start",
    "session.ask_trip_type",
    "session.ask_trip_type_retry",
    "session.ask_passport_upload",
    "session.passport_upload_pending",
    "session.ask_room_type",
    "session.ask_room_type_retry",
    "session.ask_group_size",
    "session.ask_flight",
    "session.ask_currency",
    "session.clarification_retry",
    "session.choose_trip_retry",
    "session.fallback",
}


def detect_language(text: str) -> str:
    """Return 'ar' if Arabic characters are dominant, else 'en'."""
    if not text:
        return "en"
    arabic_chars = len(_ARABIC_PATTERN.findall(text))
    # If at least 20% of non-space characters are Arabic, treat as Arabic.
    total_chars = len(text.replace(" ", "")) or 1
    return "ar" if (arabic_chars / total_chars) >= 0.20 else "en"


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

@dataclass
class SessionState:
    id: str
    stage: str = "awaiting_phone"
    previous_stage: str = ""
    language: str = "en"
    agent_mode: str = "deterministic"
    tools_used: list[str] = field(default_factory=list)
    fallback_used: bool = False
    customer_name: str = ""
    birthday: str = ""
    gender: str = ""
    nationality: str = ""
    raw_phone: str = ""
    country_code: str = ""
    phone_normalization: dict[str, Any] = field(default_factory=dict)
    pending_raw_phone: str = ""
    trip_type: str = ""
    trip_query: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    preview: dict[str, Any] | None = None
    final_result: dict[str, Any] | None = None
    booking_result: dict[str, Any] | None = None
    selected_trip_id: str = ""
    selected_trip_name: str = ""
    # Phase 11: set when the agent has just offered the trip list in
    # response to a trip-change/browse interruption while a trip was
    # already selected -- the *next* turn is allowed to select a trip by a
    # bare name/number, without requiring the explicit correction-signal
    # wording _apply_trip_switch_from_text normally demands, since that
    # signal was already given on the turn that set this flag. Cleared as
    # soon as it's consumed (matched or not) so it never lingers.
    awaiting_trip_reselection: bool = False
    room_type: str = ""
    room_group: str = ""
    room_requirements: dict[str, Any] = field(default_factory=dict)
    group_size: int = 1
    group_nationality_type: str = ""
    group_nationality_counts: dict[str, int] = field(default_factory=dict)
    preferred_date: str = ""
    flight_option: str = ""
    currency: str = ""
    guardian_name: str = ""
    guardian_phone: str = ""
    guardian_consent_saved: bool = False
    lead_status: str = ""
    booking_status: str = ""
    handoff_state: str = ""
    booking_completed: bool = False
    previous_booking_result: dict[str, Any] = field(default_factory=dict)
    booking_confirmation_requested: bool = False
    booking_confirmed: bool = False
    new_traveler_lead_saved: bool = False
    # Passport fields (populated for international trips)
    passport_name: str = ""
    passport_number: str = ""
    passport_expiry: str = ""
    passport_nationality: str = ""
    passport_attachment_ref: str = ""
    # Internal tracking: which passport field we are currently collecting
    _passport_step: str = field(default="", repr=False)
    # Internal tracking: why the last name/nationality answer was rejected, so the
    # re-ask can give a specific reason instead of repeating the generic prompt.
    _name_rejection_reason: str = field(default="", repr=False)
    _nationality_rejection_reason: str = field(default="", repr=False)
    _passport_field_rejection_reason: str = field(default="", repr=False)
    _passport_expiry_warning: str = field(default="", repr=False)
    # Duplicate open-lead dialogue (Task 3.2): "" (not yet asked/resolved),
    # "continue" (resume the found lead), or "new" (create a second lead deliberately).
    duplicate_lead_choice: str = ""
    resumed_lead_id: str = ""
    _open_lead_id: str = field(default="", repr=False)
    _open_lead_trip_type: str = field(default="", repr=False)
    _last_persona_intent: str = field(default="", repr=False)
    _persona_intent_repeat_count: int = field(default=0, repr=False)
    # Consecutive times an about-to-be-sent reply was blocked for
    # contradicting a confirmed session fact (see
    # ToolCallingSessionRuntime._finalize_assistant_reply). Reset on any
    # reply that passes the check cleanly; 2 in a row auto-escalates to
    # human handoff instead of continuing to loop on a customer.
    contradiction_strikes: int = field(default=0, repr=False)

    # Consecutive turns the customer's answer to the SAME required workflow
    # step could not be parsed (see ToolCallingSessionRuntime.handle_message's
    # unclear-input branch). Reset the moment any step captures, or when the
    # pending step changes. Without this the agent re-sent a byte-identical
    # question forever and the customer's only exit was to leave: strike 2
    # re-words the ask, strike 3 hands off to a human.
    unclear_step_strikes: int = field(default=0, repr=False)
    unclear_step_key: str = field(default="", repr=False)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class SessionFlowManager:
    def __init__(
        self,
        human_handoff_phone: str = "",
        agent_persona_name: str = "",
        website_url: str = "",
        post_trip_handoff_enabled: bool = False,
        handoff_keywords: str = "",
        post_trip_handoff_responsible_employee: str = "Operations Team",
        default_country_code: str = "20",
        conversation_ai: Any | None = None,
    ) -> None:
        self._sessions: dict[str, SessionState] = {}
        self.human_handoff_phone = human_handoff_phone
        self.agent_persona_name = agent_persona_name or "Ravel Agent"
        self.website_url = website_url
        self.post_trip_handoff_enabled = post_trip_handoff_enabled
        self.handoff_keywords = [part.strip().lower() for part in str(handoff_keywords or "").split(",") if part.strip()]
        self.post_trip_handoff_responsible_employee = post_trip_handoff_responsible_employee
        self.default_country_code = default_country_code or ""
        self.conversation_ai = conversation_ai
        self._active_session: SessionState | None = None
        self._active_user_text: str = ""

    def clear(self) -> None:
        self._sessions.clear()

    @staticmethod
    def _looks_like_egypt_local_number(phone: str) -> bool:
        digits = re.sub(r"\D", "", phone or "")
        return bool(re.fullmatch(r"0?(10|11|12|15)\d{8}", digits))

    @staticmethod
    def _invalid_phone_prompt(*, arabic: bool = False) -> str:
        return (
            "من فضلك أرسل رقم واتساب صحيح.\n"
            "يجب أن يكون رقم موبايل حقيقي وبصيغة مناسبة لبلده، مثل +201012345678 أو +966512345678."
            if arabic
            else "Please send a valid WhatsApp number. It should be a real mobile number in a valid country format, for example +201012345678 or +966512345678."
        )

    def create_session(self, gateway=None) -> SessionState:
        session = SessionState(id=uuid.uuid4().hex)
        self._active_session = session
        self._active_user_text = ""
        session.messages.append(
            {
                "role": "assistant",
                "text": self._copy_text(
                    gateway,
                    "session.ask_phone_first",
                    f"Hi, I'm {self.agent_persona_name} from Ravel Traveler! I'd love to help you plan your trip. "
                    "Could you share your WhatsApp number first so I can pull up your profile safely?",
                    language=session.language,
                    agent_name=self.agent_persona_name,
                ),
            }
        )
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> SessionState | None:
        return self._sessions.get(session_id)

    def _set_stage(self, session: SessionState, stage: str) -> None:
        if session.stage != stage:
            session.previous_stage = session.stage
            session.stage = stage

    @staticmethod
    def _is_post_booking_booking_intent(text: str) -> bool:
        lowered = " ".join(str(text or "").strip().casefold().split())
        return any(term in lowered for term in (
            "\u0639\u0627\u064a\u0632 \u0627\u062d\u062c\u0632",
            "\u0639\u0627\u064a\u0632 \u0623\u062d\u062c\u0632",
            "\u062d\u062c\u0632 \u062c\u062f\u064a\u062f",
            "\u062d\u062c\u0632 \u062a\u0627\u0646\u064a",
            "\u0631\u062d\u0644\u0629 \u062a\u0627\u0646\u064a\u0629",
            "i want to book",
            "book again",
            "another booking",
            "start a new booking",
            "another trip",
        ))

    @staticmethod
    def _is_post_booking_status_intent(text: str) -> bool:
        lowered = " ".join(str(text or "").strip().casefold().split())
        return any(term in lowered for term in (
            "\u062d\u0627\u0644\u0629 \u0627\u0644\u062d\u062c\u0632",
            "\u062d\u062c\u0632\u064a",
            "\u0627\u062a\u0633\u062c\u0644",
            "booking status",
            "was my booking created",
        ))

    @staticmethod
    def _is_post_booking_ack(text: str) -> bool:
        normalized = " ".join(str(text or "").strip().casefold().split())
        return normalized in {"\u062a\u0645\u0627\u0645", "\u0645\u0627\u0634\u064a", "\u0627\u0648\u0643\u064a", "\u0634\u0643\u0631\u0627", "ok", "okay", "thanks", "thank you", "understood"}

    @staticmethod
    def _is_post_booking_unclear_negative(text: str) -> bool:
        normalized = " ".join(str(text or "").strip().casefold().split())
        return normalized in {"\u0644\u0627", "\u0645\u0641\u064a\u0634", "no", "nothing"}

    def _reset_for_new_booking_after_completion(self, session: SessionState) -> None:
        if isinstance(session.booking_result, dict) and session.booking_result:
            session.previous_booking_result = dict(session.booking_result)
        session.trip_type = ""
        session.trip_query = ""
        session.selected_trip_id = ""
        session.selected_trip_name = ""
        self._clear_booking_dependent_state(session)
        self._set_stage(session, "new_booking_intent")

    def _handle_post_booking_message(self, session: SessionState, text: str) -> bool:
        if session.stage not in {"completed", "booking_created", "handoff_created", "post_booking_support", "new_booking_intent"} and not session.booking_completed:
            return False
        if self._is_post_booking_status_intent(text):
            booking = session.booking_result if isinstance(session.booking_result, dict) else {}
            booking_id = str(booking.get("booking_id") or "booking").strip()
            status = str(booking.get("booking_status") or session.booking_status or "Draft").strip()
            reply = (
                f"\u0623\u064a\u0648\u0647\u060c {booking_id} \u0645\u062a\u0633\u062c\u0644 \u0648\u062d\u0627\u0644\u062a\u0647 {status}. \u0641\u0631\u064a\u0642 Ravel \u0647\u064a\u062a\u0648\u0627\u0635\u0644 \u0645\u0639\u0627\u0643."
                if session.language.startswith("ar")
                else f"Yes, {booking_id} is created and its status is {status}. The Ravel team will follow up with you."
            )
        elif self._is_post_booking_ack(text):
            reply = "\u062a\u0645\u0627\u0645\u060c \u0623\u0646\u0627 \u0645\u0639\u0627\u0643 \u0644\u0648 \u0627\u062d\u062a\u062c\u062a \u0623\u064a \u0645\u0633\u0627\u0639\u062f\u0629." if session.language.startswith("ar") else "All good. I’m here if you need anything else."
        elif self._is_post_booking_unclear_negative(text):
            reply = "\u0647\u0644 \u062a\u0642\u0635\u062f \u0623\u0646\u0643 \u0644\u0627 \u062a\u0631\u064a\u062f \u0628\u062f\u0621 \u062d\u062c\u0632 \u062c\u062f\u064a\u062f\u061f" if session.language.startswith("ar") else "Do you mean you don’t want to start a new booking?"
        elif self._is_post_booking_booking_intent(text):
            explicit_new = any(term in " ".join(str(text or "").casefold().split()) for term in ("\u062a\u0627\u0646\u064a", "\u062c\u062f\u064a\u062f", "again", "another", "new"))
            if explicit_new:
                self._reset_for_new_booking_after_completion(session)
                reply = "\u0628\u0627\u0644\u062a\u0623\u0643\u064a\u062f\u060c \u064a\u0645\u0643\u0646\u0646\u0627 \u0628\u062f\u0621 \u062d\u062c\u0632 \u062c\u062f\u064a\u062f. \u0647\u0644 \u062a\u0631\u064a\u062f \u0627\u0644\u062d\u062c\u0632 \u0641\u064a \u0646\u0641\u0633 \u0627\u0644\u0631\u062d\u0644\u0629 \u0623\u0645 \u062a\u0628\u062d\u062b \u0639\u0646 \u0631\u062d\u0644\u0629 \u0623\u062e\u0631\u0649\u061f" if session.language.startswith("ar") else "Of course. We can start a new booking. Would you like the same trip, or are you looking for another trip?"
            else:
                self._set_stage(session, "post_booking_support")
                reply = "\u0647\u0644 \u062a\u0631\u064a\u062f \u0628\u062f\u0621 \u062d\u062c\u0632 \u062c\u062f\u064a\u062f\u060c \u0623\u0645 \u062a\u0631\u064a\u062f \u0645\u062a\u0627\u0628\u0639\u0629 \u0627\u0644\u062d\u062c\u0632 \u0627\u0644\u062d\u0627\u0644\u064a\u061f" if session.language.startswith("ar") else "Do you want to start a new booking, or follow up on the current booking?"
        else:
            return False
        session.booking_completed = True
        if session.stage not in {"new_booking_intent"}:
            self._set_stage(session, "post_booking_support")
        session.messages.append({"role": "assistant", "text": reply})
        return True

    def submit_intake(self, session: SessionState, payload: dict[str, Any], gateway) -> SessionState:
        self._active_session = session
        self._active_user_text = "intake_form_submitted"
        if session.stage == "completed":
            session.messages.append(
                {
                    "role": "assistant",
                    "text": self._copy_text(gateway, "session.completed", "This session is already completed. Start a new session to continue.", language=session.language),
                }
            )
            return session
        if session.stage in {"handed_off", "cancelled"}:
            session.messages.append(
                {
                    "role": "assistant",
                    "text": self._copy_text(
                        gateway,
                        "session.terminal",
                        "This session has ended. Start a new session to continue.",
                        language=session.language,
                    ),
                }
            )
            return session

        full_name = str(payload.get("fullName", "")).strip()
        raw_birthday = str(payload.get("birthday", "")).strip()
        birthday = normalize_birthdate_input(raw_birthday) if raw_birthday else ""
        gender = str(payload.get("gender", "")).strip()
        nationality = str(payload.get("nationality", "")).strip()
        raw_phone = str(payload.get("rawPhone", "")).strip() or session.raw_phone
        country_code = str(payload.get("countryCode", "")).strip()
        language = str(payload.get("language", "en")).strip().lower() or "en"

        missing = [
            label
            for label, value in {
                "full name": full_name,
                "birthday": raw_birthday,
                "gender": gender,
                "nationality": nationality,
                "WhatsApp number": raw_phone,
            }.items()
            if not value
        ]
        if missing:
            session.messages.append({"role": "assistant", "text": f"Please complete: {', '.join(missing)}."})
            return session
        if raw_birthday and not birthday:
            session.messages.append(
                {
                    "role": "assistant",
                    "text": "Please enter a valid date of birth, for example 21/08/1995, 1995-08-21, or 21 Aug 1995.",
                }
            )
            return session

        session.customer_name = full_name
        session.birthday = birthday
        session.gender = gender
        session.nationality = nationality
        session.raw_phone = raw_phone
        explicit_local = bool(country_code) or (
            not country_code and self.default_country_code == "20" and self._looks_like_egypt_local_number(raw_phone)
        )
        parsed = normalize_phone_input(
            raw_phone,
            country_code or self.default_country_code,
            default_country_is_explicit=explicit_local,
        )
        if not parsed.is_valid:
            session.messages.append(
                {
                    "role": "assistant",
                    "text": self._invalid_phone_prompt(arabic=language.startswith("ar")),
                }
            )
            return session
        if not country_code and parsed.requires_country_confirmation:
            session.messages.append({"role": "assistant", "text": "Please confirm the country code for this WhatsApp number before I continue."})
            self._set_stage(session, "awaiting_country_code")
            session.pending_raw_phone = raw_phone
            session.phone_normalization = parsed.to_dict()
            return session

        session.country_code = parsed.country_code or country_code or ""
        if parsed.inferred_nationality and not nationality:
            session.nationality = parsed.inferred_nationality
        session.phone_normalization = parsed.to_dict()
        session.language = "ar" if language.startswith("ar") else "en"
        session.messages.append(
            {
                "role": "user",
                "text": (
                    f"Intake form submitted: {full_name}, {birthday}, {gender}, "
                    f"{nationality}, WhatsApp {raw_phone}"
                ),
            }
        )

        preview = gateway.preview_customer(
            full_name=session.customer_name,
            raw_phone=session.raw_phone,
            trip_type=None,
            country_code=session.country_code,
        )
        session.preview = preview

        should_handoff = preview.get("match_status") == "multiple_matches" or (
            preview.get("match_status") == "single_match" and preview.get("handoff_required")
        )
        if should_handoff:
            result = gateway.run_sales_cycle(
                full_name=session.customer_name,
                raw_phone=session.raw_phone,
                country_code=session.country_code,
                trip_type=None,
                channel="web-demo",
                source="Web Demo Intake",
                agent_notes="Handoff required during intake verification.",
                birthday=session.birthday,
                gender=session.gender,
                nationality=session.nationality,
            )
            session.final_result = result
            session.handoff_state = "handed_off"
            self._set_stage(session, "handed_off")
            session.messages.append({"role": "assistant", "text": self._handoff_message(gateway, session.language, result.get("handoff_reason", ""))})
            return session

        self._set_stage(session, "awaiting_trip_type")
        session.messages.append(
            {
                "role": "assistant",
                "text": self._copy_text(
                    gateway,
                    "session.ask_trip_type",
                    "Great. Is this inquiry for a local trip or an international trip?\n1. Local\n2. International",
                    language=session.language,
                ),
            }
        )
        return session

    def handle_message(self, session: SessionState, text: str, gateway) -> SessionState:
        agent_logger.info(f"Session {session.id} [{session.stage}] - INCOMING: {text}")
        clean_text = text.strip()
        if not clean_text:
            return session
        self._active_session = session
        self._active_user_text = clean_text

        # Auto-detect language from customer's message and update session if needed
        detected = detect_language(clean_text)
        if detected == "ar" and session.language != "ar":
            session.language = "ar"
        elif detected == "en" and session.language == "ar" and len(clean_text) > 10:
            # Only switch to English if message is substantial (not just a number reply)
            session.language = "en"

        session.messages.append({"role": "user", "text": clean_text})

        if self._handle_post_booking_message(session, clean_text):
            return session
        if session.stage == "completed":
            self._set_stage(session, "post_booking_support")
            session.messages.append(
                {
                    "role": "assistant",
                    "text": (
                        "\u0627\u0644\u062d\u062c\u0632 \u0627\u0644\u0633\u0627\u0628\u0642 \u0645\u062d\u0641\u0648\u0638. \u0647\u0644 \u062a\u0631\u064a\u062f \u0628\u062f\u0621 \u062d\u062c\u0632 \u062c\u062f\u064a\u062f \u0623\u0645 \u0645\u062a\u0627\u0628\u0639\u0629 \u0627\u0644\u062d\u062c\u0632 \u0627\u0644\u062d\u0627\u0644\u064a\u061f"
                        if session.language.startswith("ar")
                        else "The previous booking is saved. Do you want to start a new booking, or follow up on the current booking?"
                    ),
                }
            )
            return session
        if session.stage in {"handed_off", "cancelled"}:
            session.messages.append(
                {
                    "role": "assistant",
                    "text": self._copy_text(
                        gateway,
                        "session.terminal",
                        "This session has ended. Start a new session to continue.",
                        language=session.language,
                    ),
                }
            )
            return session

        if self._handle_navigation_intent(session, clean_text, gateway):
            return session

        # --- Website intent detection (works in any stage) ---
        if self._is_website_intent(clean_text):
            session.messages.append({"role": "assistant", "text": self._website_response(gateway, session.language)})
            return session
        if self._is_human_handoff_intent(clean_text):
            self._handoff_on_customer_request(session, gateway, clean_text)
            return session
        visa_response = self._maybe_handle_visa_intent(session, gateway, clean_text)
        if visa_response:
            session.messages.append({"role": "assistant", "text": visa_response})
            return session

        # Legacy typing path kept for manual testing; the demo UI uses submit_intake.
        if session.stage in {"awaiting_intake", "awaiting_name"}:
            session.customer_name = clean_text
            session.stage = "awaiting_phone"
            session.messages.append(
                {
                    "role": "assistant",
                    "text": self._copy_text(
                        gateway,
                        "session.ask_phone",
                        f"Thanks, {session.customer_name}. Please share the customer's WhatsApp number.",
                        language=session.language,
                        customer_name=session.customer_name,
                    ),
                }
            )
            return session

        if session.stage == "awaiting_phone":
            if not re.search(r"\d", clean_text):
                intent = self._classify_customer_message(clean_text)
                repeat_count = self._register_persona_intent(session, intent)
                session.messages.append(
                    {
                        "role": "assistant",
                        "text": self._copy_text(
                            gateway,
                            self._phone_step_message_key(intent, repeat_count),
                            self._phone_step_persona_fallback(intent, repeat_count),
                            language=session.language,
                            agent_name=self.agent_persona_name,
                        ),
                    }
                )
                return session

            explicit_local = self.default_country_code == "20" and self._looks_like_egypt_local_number(clean_text)
            detected = normalize_phone_input(
                clean_text,
                self.default_country_code,
                default_country_is_explicit=explicit_local,
            )
            session.raw_phone = clean_text
            session.phone_normalization = detected.to_dict()
            if not detected.is_valid:
                session.pending_raw_phone = ""
                session.messages.append(
                    {
                        "role": "assistant",
                        "text": self._invalid_phone_prompt(arabic=session.language.startswith("ar")),
                    }
                )
                return session
            if detected.requires_country_confirmation:
                session.pending_raw_phone = clean_text
                self._set_stage(session, "awaiting_country_code")
                session.messages.append(
                    {
                        "role": "assistant",
                        "text": self._copy_text(
                            gateway,
                            "session.ask_country_code",
                            "I couldn't confirm the country from that number yet. Please send just the country code, such as 20 for Egypt or 966 for Saudi Arabia.",
                            language=session.language,
                        ),
                    }
                )
                return session

            session.country_code = detected.country_code
            if detected.inferred_nationality and not session.nationality:
                session.nationality = detected.inferred_nationality
            preview = gateway.preview_customer(
                full_name=session.customer_name,
                raw_phone=session.raw_phone,
                trip_type=None,
                country_code=session.country_code,
            )
            session.preview = preview

            should_handoff = preview.get("match_status") == "multiple_matches" or (
                preview.get("match_status") == "single_match" and preview.get("handoff_required")
            )
            if should_handoff:
                result = gateway.run_sales_cycle(
                    full_name=session.customer_name,
                    raw_phone=session.raw_phone,
                    country_code=session.country_code,
                    trip_type=None,
                    channel="web-demo",
                    source="Web Demo",
                    agent_notes="Handoff required during phone verification.",
                    birthday=session.birthday,
                    gender=session.gender,
                    nationality=session.nationality,
                )
                session.final_result = result
                session.handoff_state = "handed_off"
                self._set_stage(session, "handed_off")
                session.messages.append({"role": "assistant", "text": self._handoff_message(gateway, session.language, result.get("handoff_reason", ""))})
                return session

            traveler = preview.get("traveler") if isinstance(preview.get("traveler"), dict) else {}
            if preview.get("match_status") == "single_match" and traveler:
                session.customer_name = str(traveler.get("full_name") or "").strip()
                session.country_code = str(traveler.get("code") or session.country_code or "").strip()

            if preview.get("match_status") == "not_found":
                self._set_stage(session, "awaiting_intake")
                session.messages.append(
                    {
                        "role": "assistant",
                        "text": self._copy_text(
                            gateway,
                            "session.new_traveler_intake_start",
                            "I could not find an existing traveler profile for this WhatsApp number. Please complete the intake form so I can create the lead safely.",
                            language=session.language,
                        ),
                    }
                )
                return session

            self._set_stage(session, "awaiting_trip_type")
            session.messages.append(
                {
                    "role": "assistant",
                    "text": self._traveler_profile_message(preview, gateway, session.language),
                }
            )
            session.messages.append(
                {
                    "role": "assistant",
                    "text": self._trip_type_prompt(gateway, session.language, existing_traveler=True),
                }
            )
            return session

        if session.stage == "awaiting_country_code":
            if not re.search(r"\d", clean_text):
                session.messages.append(
                    {
                        "role": "assistant",
                        "text": self._copy_text(
                            gateway,
                            "session.explain_country_code",
                            "I need the country code for the WhatsApp number so I can format it correctly. For example, Egypt is 20 and Saudi Arabia is 966. Please send only the code.",
                            language=session.language,
                        ),
                    }
                )
                return session

            confirmed = re.sub(r"\D", "", clean_text)
            if not confirmed:
                session.messages.append(
                    {
                        "role": "assistant",
                        "text": self._copy_text(
                            gateway,
                            "session.retry_country_code",
                            "Please reply with the numeric country code only, for example 20 or 966.",
                            language=session.language,
                        ),
                    }
                )
                return session

            source_phone = session.pending_raw_phone or session.raw_phone
            detected = normalize_phone_input(source_phone, confirmed, default_country_is_explicit=True)
            if not detected.is_valid:
                session.pending_raw_phone = ""
                session.raw_phone = ""
                self._set_stage(session, "awaiting_phone")
                session.messages.append(
                    {
                        "role": "assistant",
                        "text": self._invalid_phone_prompt(arabic=session.language.startswith("ar")),
                    }
                )
                return session
            if detected.requires_country_confirmation and not detected.normalized_e164:
                session.messages.append(
                    {
                        "role": "assistant",
                        "text": self._copy_text(
                            gateway,
                            "session.retry_country_code",
                            "I still couldn't format that number correctly. Please send the full WhatsApp number with the country code, for example +966512345678.",
                            language=session.language,
                        ),
                    }
                )
                return session

            session.country_code = detected.country_code or confirmed
            session.raw_phone = source_phone
            session.phone_normalization = detected.to_dict()
            session.pending_raw_phone = ""
            if detected.inferred_nationality and not session.nationality:
                session.nationality = detected.inferred_nationality
            preview = gateway.preview_customer(
                full_name=session.customer_name,
                raw_phone=session.raw_phone,
                trip_type=None,
                country_code=session.country_code,
            )
            session.preview = preview
            self._set_stage(session, "awaiting_trip_type")
            session.messages.append(
                {
                    "role": "assistant",
                    "text": self._traveler_profile_message(preview, gateway, session.language),
                }
            )
            session.messages.append(
                {
                    "role": "assistant",
                    "text": self._trip_type_prompt(gateway, session.language, existing_traveler=True),
                }
            )
            return session

        if session.stage == "awaiting_trip_type":
            normalized = self._resolve_trip_type(clean_text)
            if normalized is None:
                self._set_stage(session, "awaiting_clarification")
                session.messages.append(
                    {
                        "role": "assistant",
                        "text": self._copy_text(
                            gateway,
                            "session.ask_trip_type_retry",
                            "Please reply with 1 or 'local', or 2 or 'international', so I can check upcoming trips.",
                            language=session.language,
                        ),
                    }
                )
                return session

            session.trip_type = normalized
            preview = gateway.preview_customer(
                full_name=session.customer_name,
                raw_phone=session.raw_phone,
                country_code=session.country_code,
                trip_type=session.trip_type,
            )
            session.preview = preview
            self._set_stage(session, "awaiting_confirmation")
            session.messages.append({"role": "assistant", "text": self._preview_message(preview, gateway, session.language)})
            return session

        if session.stage == "awaiting_confirmation":
            return self._handle_trip_confirmation(session, clean_text, gateway)

        # -----------------------------------------------------------------------
        # Passport attachment stage (international trips only)
        # -----------------------------------------------------------------------

        if session.stage == "awaiting_passport_upload":
            lowered = clean_text.lower()
            changed_flight = self._flight_option_from_text(clean_text)
            if changed_flight:
                session.flight_option = changed_flight
                self._clear_passport_state(session)
                return self._advance_after_flight_decision(session, gateway)
            if not self._passport_required_for_session(session):
                self._clear_passport_state(session)
                return self._advance_after_flight_decision(session, gateway)
            if session.passport_attachment_ref and lowered in {"done", "continue", "uploaded", "sent", "تمام", "جاهز", "next", "ok", "okay"}:
                # Passport collection complete — proceed to room type
                self._set_stage(session, "awaiting_currency")
                session.messages.append({"role": "assistant", "text": self._currency_prompt(gateway, session.language)})
            else:
                session.messages.append(
                    {
                        "role": "assistant",
                        "text": self._copy_text(
                            gateway,
                            "session.passport_upload_pending",
                            "I still need the passport as an attachment for this trip. Please upload the file using the attachment button, then type 'done' so I can continue.",
                            language=session.language,
                        ),
                    }
                )
            return session

        # -----------------------------------------------------------------------
        # Room, flight, currency stages
        # -----------------------------------------------------------------------

        if session.stage == "awaiting_group_size":
            group_size = self._extract_group_size(clean_text, allow_plain_number=True)
            if group_size is None or group_size < 2:
                session.messages.append(
                    {
                        "role": "assistant",
                        "text": self._copy_text(
                            gateway,
                            "session.ask_group_size",
                            "Please send the group size as a number, for example 4 or 6 travelers.",
                            language=session.language,
                        ),
                    }
                )
                return session
            session.group_size = group_size
            self._set_stage(session, "awaiting_room_type")
            session.messages.append(
                {
                    "role": "assistant",
                    "text": (
                        f"I noted this as a group reservation for {group_size} travelers.\n\n"
                        f"{self._room_type_prompt(gateway, session)}"
                    ),
                }
            )
            return session

        if session.stage == "awaiting_room_type":
            if self._looks_like_group_booking_intent(clean_text):
                group_size = self._extract_group_size(clean_text, allow_plain_number=False)
                if group_size is None:
                    self._set_stage(session, "awaiting_group_size")
                    session.messages.append(
                        {
                            "role": "assistant",
                            "text": self._copy_text(
                                gateway,
                                "session.ask_group_size",
                                "How many travelers are in the group booking? Please send the number, for example 4 or 6.",
                                language=session.language,
                            ),
                        }
                    )
                    return session
                session.group_size = max(group_size, 2)
                session.messages.append(
                    {
                        "role": "assistant",
                        "text": (
                            f"I noted this as a group reservation for {session.group_size} travelers.\n\n"
                            f"{self._room_type_prompt(gateway, session)}"
                        ),
                    }
                )
                return session
            choice = self._resolve_room_choice(clean_text, session)
            if choice is None:
                self._set_stage(session, "awaiting_clarification")
                session.messages.append(
                    {
                        "role": "assistant",
                        "text": self._copy_text(
                            gateway,
                            "session.ask_room_type_retry",
                            self._room_type_prompt(gateway, session),
                            language=session.language,
                        ),
                    }
                )
                return session

            room = str(choice["room_type"])
            room_group = str(choice.get("room_group") or "")

            if not self._room_has_capacity(session, room):
                available = self._available_room_types(session)
                if available:
                    opts = " / ".join(f"{i+1}. {r}" for i, r in enumerate(available))
                    text = f"{room} is not available for this trip. Available options: {opts}."
                else:
                    text = "Accommodation is currently unavailable for this trip. I will keep this as a follow-up for the sales team."
                session.room_type = ""
                session.room_group = ""
                session.room_requirements = {}
                session.messages.append({"role": "assistant", "text": text})
                return session

            session.room_type = room
            session.room_group = room_group
            if not self._trip_supports_flights(self._selected_trip(session)):
                session.flight_option = "Not Applicable"
                return self._advance_after_flight_decision(session, gateway)
            self._set_stage(session, "awaiting_flight")
            session.messages.append(
                {
                    "role": "assistant",
                    "text": self._copy_text(
                        gateway,
                        "session.ask_flight",
                        "Most of our trips are offered without flights by default. Would you like me to note a flight request for this booking?\n1. Yes\n2. No",
                        language=session.language,
                    ),
                }
            )
            return session

        if session.stage == "awaiting_flight":
            if self._is_positive_confirmation(clean_text) or clean_text.strip() == "1":
                session.flight_option = "With Flight"
            else:
                session.flight_option = "Without Flight"

            return self._advance_after_flight_decision(session, gateway)

        if session.stage == "awaiting_currency":
            curr = clean_text.upper()
            if "USD" in curr or "دولار" in curr or clean_text.strip() == "2":
                session.currency = "USD"
            else:
                session.currency = "EGP"

            # Finalize booking draft
            trip_id = session.selected_trip_id
            if not trip_id and session.preview and session.preview.get("trip_result"):
                open_trips = session.preview["trip_result"].get("open_trips", [])
                if open_trips:
                    trip_id = open_trips[0].get("trip_id", "")
                else:
                    date_tbd = session.preview["trip_result"].get("date_tbd_trips", [])
                    if date_tbd:
                        trip_id = date_tbd[0].get("trip_id", "")

            if not trip_id:
                agent_logger.error(f"Cannot create booking: trip_id is empty. Preview: {session.preview}")
                session.messages.append({"role": "assistant", "text": "I encountered an error: could not identify the trip you are booking. Please start a new session."})
                self._set_stage(session, "cancelled")
                session.handoff_state = "cancelled"
                return session

            if not session.final_result:
                agent_logger.error(f"Cannot create booking: final_result is missing.")
                session.messages.append({"role": "assistant", "text": "I encountered an error: lead data is missing. Please start a new session."})
                self._set_stage(session, "cancelled")
                session.handoff_state = "cancelled"
                return session

            if self._passport_required_for_session(session) and not session.passport_attachment_ref:
                self._set_stage(session, "awaiting_passport_upload")
                session.messages.append(
                    {
                        "role": "assistant",
                        "text": self._copy_text(
                            gateway,
                            "session.passport_upload_pending",
                            "I still need the passport as an attachment for this trip. Please upload the file using the attachment button, then type 'done' so I can continue.",
                            language=session.language,
                        ),
                    }
                )
                return session

            room_choice_label = self._room_choice_label(session.room_type, session.room_group)
            agent_logger.info(f"Finalizing booking: traveler={session.customer_name}, trip={trip_id}, room={room_choice_label}, flight={session.flight_option}, currency={session.currency}")

            passport_required = self._passport_required_for_session(session)
            passport_status = 'uploaded' if session.passport_attachment_ref else ('pending' if passport_required else '')
            booking = gateway.create_booking(
                traveler_id=(session.final_result.get("traveler") or {}).get("traveler_id", "TBD"),
                traveler_name=session.customer_name,
                trip_id=trip_id,
                room_type=session.room_type,
                room_group=session.room_group,
                room_requirements=session.room_requirements,
                flight_option=session.flight_option,
                currency=session.currency,
                channel="web-demo",
                lead_id=(session.final_result.get("write_result") or {}).get("lead_update", {}).get("lead_id", "TBD"),
                source="Web Shared Booking Module",
                agent_notes=f"Room: {room_choice_label}, Flight: {session.flight_option}, Currency: {session.currency}",
                passport_required=passport_required,
                passport_status=passport_status,
                group_size=session.group_size,
            )
            booking["room_group"] = session.room_group
            booking["room_choice_label"] = room_choice_label
            booking["group_size"] = session.group_size
            session.booking_result = booking
            session.booking_status = str(booking.get("booking_status") or "Draft")
            session.handoff_state = "completed"
            session.booking_completed = True
            self._set_stage(session, "post_booking_support")

            booking_id = (booking.get("write_result") or {}).get("booking_draft", {}).get("booking_id", "NEW")
            booking_status = booking.get("booking_status") or "Draft"
            payment_status = booking.get("payment_status") or "Pending"
            session.messages.append(
                {
                    "role": "assistant",
                    "text": self._copy_text(
                        gateway,
                        "session.booking_created_complete",
                        f"I created booking draft {booking_id}. Booking status is {booking_status} and payment status is {payment_status}. Our team can continue the payment follow-up directly from this draft.",
                        language=session.language,
                        booking_id=booking_id,
                    ),
                }
            )

            # Post-trip human handoff (configurable, default OFF)
            if self.post_trip_handoff_enabled:
                traveler_id = (session.final_result.get("traveler") or {}).get("traveler_id", "")
                try:
                    if traveler_id and gateway.traveler_has_completed_trips(traveler_id):
                        responsible = self.post_trip_handoff_responsible_employee
                        agent_logger.info(f"Post-trip handoff triggered for traveler {traveler_id}, routing to: {responsible}")
                        session.messages.append(
                            {
                                "role": "assistant",
                                "text": self._copy_text(
                                    gateway,
                                    "session.post_trip_handoff",
                                    f"Since you have traveled with us before, {responsible} will reach out to "
                                    "you personally to ensure a premium experience on your next trip.",
                                    language=session.language,
                                    responsible=responsible,
                                ),
                            }
                        )
                except Exception as exc:
                    agent_logger.warning(f"Post-trip handoff check failed: {exc}")

            return session

        if session.stage == "booking_created":
            session.messages.append(
                {
                    "role": "assistant",
                    "text": self._copy_text(
                        gateway,
                        "session.booking_completed",
                        "The booking draft is already created. Our team can continue the follow-up from the saved draft.",
                        language=session.language,
                    ),
                }
            )
            return session

        if session.stage == "awaiting_clarification":
            resume_stage = session.previous_stage or "awaiting_trip_type"
            if resume_stage == "awaiting_clarification":
                resume_stage = "awaiting_trip_type"
            self._set_stage(session, resume_stage)
            if self._clarification_reply_can_resume(session, clean_text):
                return self._continue_after_clarification(session, clean_text, gateway)
            session.messages.append(
                {
                    "role": "assistant",
                    "text": self._copy_text(
                        gateway,
                        "session.clarification_retry",
                        "Thanks. I’m back on the previous step now. Please answer with one of the available options.",
                        language=session.language,
                    ),
                }
            )
            return session

        session.messages.append(
            {
                "role": "assistant",
                "text": self._copy_text(
                    gateway,
                    "session.fallback",
                    "I could not process that message in the current stage. Please choose one of the available options.",
                ),
            }
        )
        return session

    def _continue_after_clarification(self, session: SessionState, clean_text: str, gateway) -> SessionState:
        """Process a corrected answer immediately after a clarification prompt."""
        if session.stage == "awaiting_trip_type":
            normalized = self._resolve_trip_type(clean_text)
            if normalized is None:
                return session
            session.trip_type = normalized
            preview = gateway.preview_customer(
                full_name=session.customer_name,
                raw_phone=session.raw_phone,
                country_code=session.country_code,
                trip_type=session.trip_type,
            )
            session.preview = preview
            self._set_stage(session, "awaiting_confirmation")
            session.messages.append({"role": "assistant", "text": self._preview_message(preview, gateway, session.language)})
            return session

        if session.stage == "awaiting_confirmation":
            return self._handle_trip_confirmation(session, clean_text, gateway)

        session.messages.append(
            {
                "role": "assistant",
                "text": self._copy_text(
                    gateway,
                    "session.clarification_retry",
                    "Thanks. I am back on the previous step now. Please answer with one of the available options.",
                    language=session.language,
                ),
            }
        )
        return session

    def _clarification_reply_can_resume(self, session: SessionState, clean_text: str) -> bool:
        if session.stage == "awaiting_trip_type":
            return self._resolve_trip_type(clean_text) is not None
        if session.stage == "awaiting_confirmation":
            return self._is_negative_confirmation(clean_text) or self._select_offered_trip(session, clean_text) is not None
        return False

    def _handle_trip_confirmation(self, session: SessionState, clean_text: str, gateway) -> SessionState:
        if self._is_negative_confirmation(clean_text):
            self._set_stage(session, "awaiting_confirmation")
            session.messages.append(
                {
                    "role": "assistant",
                    "text": self._copy_text(
                        gateway,
                        "session.trip_rejected",
                        "No problem. I will not continue with that trip. If you want another option, tell me the destination, date, or trip name you are interested in.",
                        language=session.language,
                    ),
                }
            )
            return session

        if self._is_explanation_or_need_request(clean_text):
            session.messages.append(
                {
                    "role": "assistant",
                    "text": self._copy_text(
                        gateway,
                        "session.explain_trip_selection_needed",
                        "I only need you to choose one of the listed trips by number or name. If none of those trips works for you, tell me what destination, date, or budget you prefer.",
                        language=session.language,
                    ),
                }
            )
            return session

        selected_trip = self._select_offered_trip(session, clean_text)
        if not selected_trip:
            self._set_stage(session, "awaiting_clarification")
            session.messages.append(
                {
                    "role": "assistant",
                    "text": self._copy_text(
                        gateway,
                        "session.choose_trip_retry",
                        "Please choose one of the available trips by typing its number, trip name, or a clear confirmation such as 'yes, I want it'. You can also reply 'no' to stop.",
                        language=session.language,
                    ),
                }
            )
            return session

        session.selected_trip_id = selected_trip["trip_id"]
        session.selected_trip_name = selected_trip["trip_name"]
        self._clear_booking_dependent_state(session)

        result = gateway.run_sales_cycle(
            full_name=session.customer_name,
            raw_phone=session.raw_phone,
            country_code=session.country_code,
            trip_type=session.trip_type,
            preferred_trip_id=session.selected_trip_id,
            channel="web-demo",
            source="Web Demo Confirmed Lead",
            agent_notes=f"Customer confirmed interest in trip: {session.selected_trip_name}",
            birthday=session.birthday,
            gender=session.gender,
            nationality=session.nationality,
        )
        session.final_result = result
        session.lead_status = str(((result.get("write_result") or {}).get("lead_update") or {}).get("lead_stage") or "")

        self._set_stage(session, "awaiting_room_type")
        session.messages.append(
            {
                "role": "assistant",
                "text": self._room_type_prompt(gateway, session),
            }
        )
        return session

    def _select_offered_trip(self, session: SessionState, text: str) -> dict[str, Any] | None:
        open_trips, date_tbd, all_offered = self._offered_trips(session)
        clean_text = str(text or "").strip()
        lowered = clean_text.casefold()

        if self._is_positive_confirmation(clean_text):
            if len(all_offered) == 1:
                return all_offered[0]
            if len(open_trips) == 1:
                return open_trips[0]

        if re.fullmatch(r"[1-9]", clean_text):
            idx = int(clean_text) - 1
            if idx < len(all_offered):
                return all_offered[idx]

        normalized_input = self._normalize_choice_text(clean_text)
        for trip in all_offered:
            trip_name = str(trip.get("trip_name") or "")
            trip_id = str(trip.get("trip_id") or "")
            normalized_name = self._normalize_choice_text(trip_name)
            normalized_id = self._normalize_choice_text(trip_id)
            if not normalized_name and not normalized_id:
                continue
            if normalized_name and (normalized_name in normalized_input or normalized_input in normalized_name):
                return trip
            if normalized_id and normalized_id in normalized_input:
                return trip
            if normalized_name and self._similarity(normalized_input, normalized_name) >= 0.84:
                return trip
        return None

    @staticmethod
    def _is_explanation_or_need_request(text: str) -> bool:
        normalized = SessionFlowManager._normalize_choice_text(text)
        return normalized in {
            "whatdouneed",
            "whatdoyouneed",
            "whatneed",
            "whatdouwant",
            "whatdoyouwant",
            "what",
            "why",
            "help",
            "explain",
            "مش فاهم",
            "مش فاهمة",
            "محتاجايه",
            "محتاجاية",
            "عايزايه",
            "ايهالمطلوب",
        } or any(
            phrase in str(text or "").strip().casefold()
            for phrase in (
                "what do u need",
                "what do you need",
                "what do u want",
                "what do you want",
                "what should i",
                "what next",
            )
        )

    @staticmethod
    def _offered_trips(session: SessionState) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        trip_result = ((session.preview or {}).get("trip_result") or {}) if isinstance(session.preview, dict) else {}
        open_trips = list(trip_result.get("open_trips") or [])
        date_tbd = list(trip_result.get("date_tbd_trips") or [])
        return open_trips, date_tbd, open_trips + date_tbd

    def _handle_navigation_intent(self, session: SessionState, text: str, gateway) -> bool:
        if self._is_start_over_intent(text):
            self._reset_booking_state(session)
            self._set_stage(session, "awaiting_trip_type")
            session.messages.append({"role": "assistant", "text": self._trip_type_prompt(gateway, session.language)})
            return True
        if self._is_cancel_intent(text):
            self._reset_booking_state(session)
            session.handoff_state = "cancelled"
            self._set_stage(session, "cancelled")
            session.messages.append({"role": "assistant", "text": "No problem. I cancelled this booking flow. You can start a new request whenever you are ready."})
            return True

        changed_flight = self._flight_option_from_text(text)
        if changed_flight and session.stage in {"awaiting_passport_upload", "awaiting_currency", "awaiting_flight"}:
            session.flight_option = changed_flight
            self._clear_passport_state(session)
            return self._advance_after_flight_decision(session, gateway)

        if not self._is_back_intent(text):
            return False
        if session.stage == "awaiting_passport_upload":
            self._clear_passport_state(session)
            if self._trip_supports_flights(self._selected_trip(session)):
                session.flight_option = ""
                self._set_stage(session, "awaiting_flight")
                session.messages.append({"role": "assistant", "text": self._flight_prompt(gateway, session.language)})
            else:
                self._set_stage(session, "awaiting_room_type")
                session.messages.append({"role": "assistant", "text": self._room_type_prompt(gateway, session)})
            return True
        if session.stage == "awaiting_currency":
            session.currency = ""
            if self._trip_supports_flights(self._selected_trip(session)):
                session.flight_option = ""
                self._set_stage(session, "awaiting_flight")
                session.messages.append({"role": "assistant", "text": self._flight_prompt(gateway, session.language)})
            else:
                self._set_stage(session, "awaiting_room_type")
                session.messages.append({"role": "assistant", "text": self._room_type_prompt(gateway, session)})
            return True
        if session.stage == "awaiting_flight":
            session.flight_option = ""
            session.room_type = ""
            session.room_group = ""
            session.room_requirements = {}
            self._set_stage(session, "awaiting_room_type")
            session.messages.append({"role": "assistant", "text": self._room_type_prompt(gateway, session)})
            return True
        if session.stage == "awaiting_room_type":
            session.room_type = ""
            session.room_group = ""
            session.room_requirements = {}
            self._set_stage(session, "awaiting_confirmation")
            session.messages.append({"role": "assistant", "text": "Sure. Please choose the trip again by its number or name."})
            return True
        return False

    @staticmethod
    def _is_back_intent(text: str) -> bool:
        normalized = SessionFlowManager._normalize_choice_text(text)
        return normalized in {
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

    @staticmethod
    def _is_cancel_intent(text: str) -> bool:
        normalized = SessionFlowManager._normalize_choice_text(text)
        return normalized in {"cancel", "stop", "end", "nevermind", "لا", "الغاء", "إلغاء", "وقف"}

    @staticmethod
    def _is_start_over_intent(text: str) -> bool:
        normalized = SessionFlowManager._normalize_choice_text(text)
        return normalized in {"startover", "restart", "startagain", "newrequest", "ابدأمنجديد", "منالأول"}

    @staticmethod
    def _flight_option_from_text(text: str) -> str:
        normalized = " ".join(str(text or "").strip().casefold().split())
        compact = SessionFlowManager._normalize_choice_text(text)
        without_terms = {
            "withoutflight",
            "withoutflights",
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
        }
        with_terms = {
            "withflight",
            "withflights",
            "iwantaflight",
            "iwantflight",
            "flightinstead",
            "withflightinstead",
            "معطيران",
            "عايزطيران",
            "عايزةطيران",
        }
        if compact in without_terms or "without flight" in normalized or "no flight" in normalized or "do not want a flight" in normalized or "do not want flight" in normalized:
            return "Without Flight"
        if compact in with_terms or "with flight" in normalized:
            return "With Flight"
        return ""

    def _advance_after_flight_decision(self, session: SessionState, gateway) -> SessionState:
        if self._passport_required_for_session(session) and not session.passport_attachment_ref:
            self._set_stage(session, "awaiting_passport_upload")
            session._passport_step = "attachment"
            session.messages.append(
                {
                    "role": "assistant",
                    "text": self._copy_text(
                        gateway,
                        "session.ask_passport_upload",
                        "Please send a clear passport photo or PDF using the attachment button. Once it is uploaded, type 'done' and I will continue.",
                        language=session.language,
                    ),
                }
            )
            return session
        self._set_stage(session, "awaiting_currency")
        session.messages.append({"role": "assistant", "text": self._currency_prompt(gateway, session.language)})
        return session

    def _currency_prompt(self, gateway, language: str) -> str:
        return self._copy_text(
            gateway,
            "session.ask_currency",
            "What is your preferred currency for payment?\n1. EGP\n2. USD",
            language=language,
        )

    def _flight_prompt(self, gateway, language: str) -> str:
        return self._copy_text(
            gateway,
            "session.ask_flight",
            "Most of our trips are offered without flights by default. Would you like me to note a flight request for this booking?\n1. Yes\n2. No",
            language=language,
        )

    def _passport_required_for_session(self, session: SessionState) -> bool:
        trip = self._selected_trip(session) or {}
        for key in ("passport_required", "requires_passport"):
            if key in trip:
                return self._as_bool(trip.get(key))
        if "passport_required_with_flight" in trip:
            return session.flight_option == "With Flight" and self._as_bool(trip.get("passport_required_with_flight"))
        trip_type = str(trip.get("type") or trip.get("trip_type") or session.trip_type or "").strip().lower()
        return trip_type == "international"

    @classmethod
    def _trip_supports_flights(cls, trip: dict[str, Any] | None) -> bool:
        trip = dict(trip or {})
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

    def _reset_booking_state(self, session: SessionState) -> None:
        session.trip_type = ""
        session.preview = None
        session.final_result = None
        session.booking_result = None
        session.selected_trip_id = ""
        session.selected_trip_name = ""
        self._clear_booking_dependent_state(session)

    # ---------------------------------------------------------------------------
    # Public helpers
    # ---------------------------------------------------------------------------

    def handle_passport_attachment(self, session: SessionState, attachment_ref: str) -> None:
        """Record an uploaded passport attachment reference on the session."""
        ref = str(attachment_ref or "").strip()
        if session.stage != "awaiting_passport_upload" or not self._passport_required_for_session(session):
            agent_logger.warning("Session %s: ignored passport attachment outside required passport step", session.id)
            return
        if not self._allowed_passport_attachment_ref(ref):
            agent_logger.warning("Session %s: ignored unsupported passport attachment -> %s", session.id, ref)
            return
        session.passport_attachment_ref = ref
        agent_logger.info(f"Session {session.id}: passport attachment uploaded pending review -> {ref}")

    @staticmethod
    def _allowed_passport_attachment_ref(attachment_ref: str) -> bool:
        lowered = str(attachment_ref or "").strip().lower()
        return lowered.endswith((".jpg", ".jpeg", ".png", ".webp", ".pdf"))

    # ---------------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------------

    def _persist_passport_profile(self, gateway, session: SessionState) -> None:
        traveler = (session.final_result or {}).get("traveler") or {}
        traveler_id = str(traveler.get("traveler_id") or "").strip()
        if not traveler_id or not hasattr(gateway, "save_traveler_passport"):
            return
        try:
            gateway.save_traveler_passport(
                traveler_id,
                passport_name=session.passport_name,
                passport_number=session.passport_number,
                passport_expiry=session.passport_expiry,
                passport_nationality=session.passport_nationality,
                passport_attachment_ref=session.passport_attachment_ref,
            )
        except Exception as exc:
            agent_logger.warning(
                "Session %s: failed to persist passport profile for %s: %s",
                session.id,
                traveler_id,
                exc,
            )

    def _handoff_message(self, gateway, language: str, reason: str = "") -> str:
        if reason == "phone_name_conflict":
            return self._copy_text(
                gateway,
                "handoff.phone_name_conflict",
                "This WhatsApp number already exists for another traveler name. I paused the automated flow so the sales team can review it safely.",
                language=language,
            )
        if reason == "blacklisted_customer":
            return self._copy_text(
                gateway,
                "handoff.blacklisted_customer",
                "This number is blacklisted.",
                language=language,
            )
        if reason == "duplicate_phone_match":
            return self._copy_text(
                gateway,
                "handoff.duplicate_phone_match",
                "This phone number is linked to multiple traveler profiles. I have paused the automated flow so the team can update the right record.",
                language=language,
            )

        fallback = "I flagged this request for a senior team review. A team member will follow up shortly."
        if self.human_handoff_phone:
            fallback = f"I flagged this request for a senior team review. Please contact our office directly at {self.human_handoff_phone} for support."

        return self._copy_text(
            gateway,
            "handoff.generic",
            fallback,
            language=language,
            phone=self.human_handoff_phone,
        )

    def _website_response(self, gateway, language: str) -> str:
        if self.website_url:
            return self._copy_text(
                gateway,
                "session.website_link",
                f"You can visit our website at: {self.website_url}",
                language=language,
                website_url=self.website_url,
            )
        return self._copy_text(
            gateway,
            "session.website_not_available",
            "Our website is coming soon. In the meantime, please reach out to our team directly.",
            language=language,
        )

    def _is_website_intent(self, text: str) -> bool:
        lowered = text.lower()
        return any(kw in lowered for kw in ("website", "site", "link", "url", "موقع", "رابط"))

    def _is_human_handoff_intent(self, text: str) -> bool:
        lowered = text.lower()
        return any(keyword in lowered for keyword in self.handoff_keywords)

    def _handoff_on_customer_request(self, session: SessionState, gateway, clean_text: str) -> None:
        final_result = session.final_result or {}
        traveler = final_result.get("traveler") if isinstance(final_result.get("traveler"), dict) else {}
        lead = (final_result.get("write_result") or {}).get("lead_update") or {}
        handoff_result = {}
        if hasattr(gateway, "create_handoff_case"):
            try:
                handoff_result = gateway.create_handoff_case(
                    lead_id=str(lead.get("lead_id") or ""),
                    traveler_id=str(traveler.get("traveler_id") or ""),
                    trip_id=session.selected_trip_id,
                    flow_key="agent_chat",
                    reason_code="customer_requested_human_agent",
                    reason_text="Customer requested a human travel agent.",
                    channel="web-demo",
                    customer_name=session.customer_name,
                    customer_summary=clean_text,
                    agent_summary=f"Stage when customer requested handoff: {session.stage}",
                    notes="Requested directly from AI chat.",
                )
            except Exception as exc:
                agent_logger.warning("Customer-requested handoff failed: %s", exc)
        session.handoff_state = "handed_off"
        self._set_stage(session, "handed_off")
        handoff_id = str(handoff_result.get("handoff_id") or "").strip()
        message = "I’ve handed this over to our human team so they can continue with you directly."
        if handoff_id:
            message = f"I’ve handed this over to our human team. Your handoff reference is {handoff_id}."
        session.messages.append({"role": "assistant", "text": message})

    def _maybe_handle_visa_intent(self, session: SessionState, gateway, clean_text: str) -> str:
        lowered = clean_text.lower()
        if "visa" not in lowered and "تأشير" not in lowered and "فيزا" not in lowered:
            return ""
        destination = self._extract_visa_destination(clean_text)
        nationality = (session.nationality or "").strip()
        if not destination:
            return "Please tell me the destination country and the traveler's nationality so I can check the visa requirement safely."
        result = gateway.get_visa_requirement(destination, nationality=nationality)
        summary = str(result.get("summary") or result.get("notes") or "").strip()
        disclaimer = str(result.get("disclaimer") or "").strip()
        if summary:
            return f"{summary}\n\n{disclaimer}".strip()
        if result.get("needs_nationality"):
            return str(result.get("summary") or "Please share the traveler's nationality first so I can check visa rules accurately.")
        return f"I couldn't verify a reliable visa answer for {destination} right now. {disclaimer}".strip()

    @staticmethod
    def _extract_visa_destination(text: str) -> str:
        match = re.search(r"\bvisa\s+(?:for|to)\s+([A-Za-z][A-Za-z\s]{1,60})", text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(" ?!.,")
        return ""

    def _trip_type_prompt(self, gateway, language: str, *, existing_traveler: bool = False) -> str:
        if existing_traveler:
            fallback = (
                "I found your traveler profile. "
                "Is this inquiry for a local trip or an international trip?\n1. Local\n2. International"
            )
        else:
            fallback = "Great. Is this inquiry for a local trip or an international trip?\n1. Local\n2. International"
        return self._copy_text(
            gateway,
            "session.ask_trip_type",
            fallback,
            language=language,
        )

    def _traveler_profile_message(self, result: dict[str, Any], gateway, language: str = "en") -> str:
        traveler = result.get("traveler") if isinstance(result.get("traveler"), dict) else {}
        if not traveler:
            return self._copy_text(
                gateway,
                "session.profile_found_generic",
                "I found your traveler profile.",
                language=language,
            )

        full_name = str(traveler.get("full_name") or "").strip() or "existing traveler"
        status = str(traveler.get("status") or "").strip() or "Active"
        local_trips = int(traveler.get("local_trips_count") or 0)
        international_trips = int(traveler.get("international_trips_count") or 0)
        total_trips = int(traveler.get("total_trips") or 0)
        vip_tail = ""
        if status == "VIP":
            vip_tail = "\nSpecial offer: You are one of our VIP travelers, so your reservation can receive a VIP discount."
        return self._copy_text(
            gateway,
            "session.profile_found_existing",
            (
                "I found your traveler profile:\n"
                "Name: {full_name}\n"
                "Status: {status}\n"
                "History: {local_trips} local, {international_trips} international, {total_trips} total{vip_tail}"
            ),
            language=language,
            full_name=full_name,
            status=status,
            local_trips=local_trips,
            international_trips=international_trips,
            total_trips=total_trips,
            vip_tail=vip_tail,
        )

    def _room_choice_label(self, room_type: str, room_group: str = "") -> str:
        if room_type == "Single":
            return "Single room"
        if room_group == "boys":
            return f"{room_type} boys room"
        if room_group == "girls":
            return f"{room_type} girls room"
        return f"{room_type} room"

    def _available_room_choices(self, session: SessionState) -> list[dict[str, str]]:
        trip = self._selected_trip(session)
        if not trip:
            return [
                {"room_type": "Single", "room_group": "", "reply": "single room", "label": "Single room"},
                {"room_type": "Double", "room_group": "boys", "reply": "double boys room", "label": "Double boys room"},
                {"room_type": "Double", "room_group": "girls", "reply": "double girls room", "label": "Double girls room"},
                {"room_type": "Triple", "room_group": "boys", "reply": "triple boys room", "label": "Triple boys room"},
                {"room_type": "Triple", "room_group": "girls", "reply": "triple girls room", "label": "Triple girls room"},
            ]

        choices: list[dict[str, str]] = []

        def as_count(value: Any) -> int:
            try:
                return max(int(value or 0), 0)
            except (TypeError, ValueError):
                return 0

        if as_count(trip.get("available_single")) > 0:
            choices.append(
                {"room_type": "Single", "room_group": "", "reply": "single room", "label": "Single room"}
            )

        for room_type, available_field, boys_field, girls_field in (
            ("Double", "available_double", "boys_double", "girls_double"),
            ("Triple", "available_triple", "boys_triple", "girls_triple"),
        ):
            if as_count(trip.get(available_field)) <= 0:
                continue
            boys = as_count(trip.get(boys_field))
            girls = as_count(trip.get(girls_field))
            if boys > 0 or girls > 0:
                if boys > 0:
                    choices.append(
                        {
                            "room_type": room_type,
                            "room_group": "boys",
                            "reply": f"{room_type.lower()} boys room",
                            "label": f"{room_type} boys room",
                        }
                    )
                if girls > 0:
                    choices.append(
                        {
                            "room_type": room_type,
                            "room_group": "girls",
                            "reply": f"{room_type.lower()} girls room",
                            "label": f"{room_type} girls room",
                        }
                    )
            else:
                choices.append(
                    {
                        "room_type": room_type,
                        "room_group": "",
                        "reply": f"{room_type.lower()} room",
                        "label": f"{room_type} room",
                    }
                )
        return choices

    def _room_type_prompt(self, gateway, session: SessionState) -> str:
        choices = self._available_room_choices(session)
        if choices:
            opts = "\n".join(f"{i+1}. {choice['label']}" for i, choice in enumerate(choices))
            fallback = f"What type of room do you prefer?\n{opts}"
        else:
            fallback = "What type of room do you prefer?\n1. Single room"
        text = self._copy_text(
            gateway,
            "session.ask_room_type",
            fallback,
            language=session.language,
        )
        if choices and "available" not in text.lower():
            opts_inline = " / ".join(f"{i+1}. {choice['label']}" for i, choice in enumerate(choices))
            text = f"{text}\nAvailable: {opts_inline}"
        commercial_note = self._room_prompt_commercial_note(session, gateway)
        if commercial_note:
            text = f"{text}\n\n{commercial_note}"
        text = f"{text}\nIf this reservation is for a group, reply with the number of travelers, for example '5 travelers'."
        return text

    def _room_prompt_commercial_note(self, session: SessionState, gateway) -> str:
        traveler = (session.final_result or {}).get("traveler") if isinstance((session.final_result or {}).get("traveler"), dict) else None
        commercial_context = UnifiedCRMService.resolve_commercial_context(
            traveler=traveler,
            trip_type=session.trip_type,
            requested_group_size=session.group_size,
            trip_id=session.selected_trip_id,
        )
        notes: list[str] = []
        if commercial_context.get("vip", {}).get("recognized"):
            vip_offer = str(commercial_context.get("vip", {}).get("offer_text") or "").strip()
            if vip_offer:
                notes.append(f"VIP offer: {vip_offer}")
            else:
                notes.append("VIP offer: your booking is eligible for VIP discount handling.")
        if session.group_size > 1:
            group_offer = str(commercial_context.get("group", {}).get("offer_text") or "").strip()
            if group_offer:
                notes.append(f"Group offer: {group_offer}")
            elif commercial_context.get("group", {}).get("eligible"):
                notes.append("Group offer: your booking qualifies for group pricing review.")
        if not notes:
            try:
                discount_note = gateway.get_trip_discount_notes(session.selected_trip_id)
            except Exception:
                discount_note = ""
            if discount_note:
                notes.append(f"Special offer: {discount_note}")
        return "\n".join(notes)

    def _selected_trip(self, session: SessionState) -> dict[str, Any] | None:
        trip_id = session.selected_trip_id
        results = [session.final_result, session.preview]
        for result in results:
            trip_result = (result or {}).get("trip_result") or {}
            for group in ("open_trips", "date_tbd_trips"):
                for trip in trip_result.get(group, []) or []:
                    if trip_id and trip.get("trip_id") == trip_id:
                        return trip
        return None

    def _available_room_types(self, session: SessionState) -> list[str]:
        available: list[str] = []
        for choice in self._available_room_choices(session):
            room_type = str(choice["room_type"])
            if room_type not in available:
                available.append(room_type)
        return available

    def _room_has_capacity(self, session: SessionState, room_type: str) -> bool:
        trip = self._selected_trip(session)
        if not trip:
            return True
        field_name = {
            "Single": "available_single",
            "Double": "available_double",
            "Triple": "available_triple",
        }.get(room_type)
        if not field_name:
            return False
        value = trip.get(field_name)
        if value is None:
            return False
        try:
            return int(value) > 0
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _looks_like_group_booking_intent(text: str) -> bool:
        normalized = str(text or "").strip().lower()
        return any(
            token in normalized
            for token in (
                "group",
                "traveler",
                "travelers",
                "people",
                "persons",
                "friends",
                "family",
                "we are",
                "for us",
            )
        )

    def _extract_group_size(self, text: str, *, allow_plain_number: bool) -> int | None:
        normalized = str(text or "").strip().lower()
        if not normalized:
            return None
        if allow_plain_number and normalized.isdigit():
            try:
                return max(int(normalized), 1)
            except (TypeError, ValueError):
                return None
        if not self._looks_like_group_booking_intent(normalized):
            return None
        match = re.search(r"(\d+)", normalized)
        if not match:
            return None
        try:
            return max(int(match.group(1)), 1)
        except (TypeError, ValueError):
            return None

    def _resolve_trip_type(self, text: str) -> str | None:
        """Resolve trip type from numbered reply (1/2) or typed word."""
        clean = text.strip()
        if clean == "1":
            return "local"
        if clean == "2":
            return "international"
        normalized = normalize_trip_type(clean)
        if normalized is not None:
            return normalized.lower()

        compact = self._normalize_choice_text(clean)
        if compact in {"loca", "locl", "loacl", "lcoal", "localtrip"}:
            return "local"
        if compact.startswith("loc") and len(compact) <= 8:
            return "local"
        if compact in {"intl", "int", "inter", "internation", "internationaltrip", "abroad"}:
            return "international"
        if compact.startswith("inter") or compact.startswith("intl"):
            return "international"
        if self._similarity(compact, "local") >= 0.80:
            return "local"
        if self._similarity(compact, "international") >= 0.80:
            return "international"
        return None

    def _resolve_room_type(self, text: str) -> str | None:
        clean = text.strip().lower()
        compact = re.sub(r"[^a-z\u0600-\u06ff]+", " ", clean).strip()
        words = compact.split()
        joined = " ".join(words)
        has_double_hint = any(token in clean for token in ("double", "duble", "bouble", "ثنائي"))
        has_triple_hint = any(token in clean for token in ("triple", "tripl", "ثلاثي"))
        if clean == "1" or "single" in clean or "فردي" in clean:
            return "Single"
        if (
            clean == "2"
            or has_double_hint
        ):
            return "Double"
        if (
            clean == "3"
            or has_triple_hint
        ):
            return "Triple"
        return None

    def _resolve_room_choice(self, text: str, session: SessionState) -> dict[str, str] | None:
        choices = self._available_room_choices(session)
        clean = text.strip().lower()
        if clean.isdigit():
            index = int(clean) - 1
            if 0 <= index < len(choices):
                return choices[index]

        compact = re.sub(r"[^a-z\u0600-\u06ff]+", " ", clean).strip()
        has_single_hint = "single" in clean or "فردي" in clean
        has_double_hint = any(token in clean for token in ("double", "duble", "bouble", "ثنائي"))
        has_triple_hint = any(token in clean for token in ("triple", "tripl", "ثلاثي"))
        has_boys_hint = any(token in compact for token in ("boys", "boy", "male", "اولاد", "ولاد", "شباب", "ذكور"))
        has_girls_hint = any(token in compact for token in ("girls", "girl", "female", "بنات", "نساء", "اناث"))
        if has_single_hint:
            return next((choice for choice in choices if choice["room_type"] == "Single"), None) or {
                "room_type": "Single",
                "room_group": "",
                "reply": "single room",
                "label": "Single room",
            }
        if has_double_hint:
            if has_boys_hint:
                return next((choice for choice in choices if choice["room_type"] == "Double" and choice["room_group"] == "boys"), None) or {
                    "room_type": "Double",
                    "room_group": "boys",
                    "reply": "double boys room",
                    "label": "Double boys room",
                }
            if has_girls_hint:
                return next((choice for choice in choices if choice["room_type"] == "Double" and choice["room_group"] == "girls"), None) or {
                    "room_type": "Double",
                    "room_group": "girls",
                    "reply": "double girls room",
                    "label": "Double girls room",
                }
            return next((choice for choice in choices if choice["room_type"] == "Double"), None) or {
                "room_type": "Double",
                "room_group": "",
                "reply": "double room",
                "label": "Double room",
            }
        if has_triple_hint:
            if has_boys_hint:
                return next((choice for choice in choices if choice["room_type"] == "Triple" and choice["room_group"] == "boys"), None) or {
                    "room_type": "Triple",
                    "room_group": "boys",
                    "reply": "triple boys room",
                    "label": "Triple boys room",
                }
            if has_girls_hint:
                return next((choice for choice in choices if choice["room_type"] == "Triple" and choice["room_group"] == "girls"), None) or {
                    "room_type": "Triple",
                    "room_group": "girls",
                    "reply": "triple girls room",
                    "label": "Triple girls room",
                }
            return next((choice for choice in choices if choice["room_type"] == "Triple"), None) or {
                "room_type": "Triple",
                "room_group": "",
                "reply": "triple room",
                "label": "Triple room",
            }
        return None

    def _copy_text(self, gateway, message_key: str, fallback: str, language: str = "en", **values: Any) -> str:
        text = fallback
        try:
            sheet_text = gateway.get_message_copy(message_key, language=language)
            if sheet_text:
                text = sheet_text

            if values:
                try:
                    text = text.format(**values)
                except (KeyError, ValueError):
                    pass
        except Exception as e:
            agent_logger.error(f"Error resolving copy {message_key}: {e}")
        text = re.sub(r"\{[a-zA-Z0-9_]+\}", "", text or "")
        text = " ".join(str(text or "").split())
        text = self._maybe_rewrite_with_ai(message_key, text, language)
        text = sanitize_traveler_reply(text)
        agent_logger.debug(f"Resolved copy: {message_key} [{language}] -> {text}")
        agent_logger.info(f"OUTGOING: {text}")
        return text

    def _maybe_rewrite_with_ai(self, message_key: str, text: str, language: str) -> str:
        if not text or not self.conversation_ai or message_key not in _AI_REWRITABLE_MESSAGE_KEYS:
            return text
        if isinstance(self.conversation_ai, GeminiAgent):
            return text
        session = self._active_session
        if session is None:
            return text
        try:
            rewritten = self.conversation_ai.rewrite_message(
                message_key=message_key,
                base_text=text,
                language=language,
                user_text=self._active_user_text,
                required_action=self._required_action_hint(session),
                session_context={
                    "session_id": session.id,
                    "stage": session.stage,
                    "language": session.language,
                    "customer_name": session.customer_name,
                    "birthday": session.birthday,
                    "gender": session.gender,
                    "nationality": session.nationality,
                    "raw_phone": session.raw_phone,
                    "country_code": session.country_code,
                    "trip_type": session.trip_type,
                    "selected_trip_name": session.selected_trip_name,
                    "selected_trip_id": session.selected_trip_id,
                    "room_type": session.room_type,
                    "flight_option": session.flight_option,
                    "currency": session.currency,
                    "booking_status": session.booking_status,
                    "has_passport_attachment": bool(session.passport_attachment_ref),
                    "passport_required": self._passport_required_for_session(session),
                    "customer_message_intent": self._classify_customer_message(self._active_user_text),
                    "persona_intent_repeat_count": session._persona_intent_repeat_count,
                    "conversation_history": session.messages[-12:],
                },
            )
        except Exception as exc:
            agent_logger.warning("Conversation AI rewrite failed for %s: %s", message_key, exc)
            return text
        if not rewritten:
            return text
        if self._rewrite_violates_constraints(message_key, rewritten):
            return text
        return rewritten

    def _required_action_hint(self, session: SessionState) -> str:
        if session.stage == "awaiting_phone":
            return "Ask for the WhatsApp number."
        if session.stage == "awaiting_country_code":
            return "Explain that you need the country code only to format the WhatsApp number correctly, then ask for that code."
        if session.stage == "awaiting_trip_type":
            return "Help the traveler choose local or international."
        if session.stage == "awaiting_confirmation":
            return "Help the traveler choose one of the offered trips or stop."
        if session.stage == "awaiting_passport_upload":
            return "Ask only for the passport attachment upload."
        if session.stage == "awaiting_group_size":
            return "Ask only for the group size as a number."
        if session.stage == "awaiting_room_type":
            return "Help the traveler choose a room type from the available options."
        if session.stage == "awaiting_flight":
            return "Ask whether flights should be included."
        if session.stage == "awaiting_currency":
            return "Ask for the preferred payment currency."
        if session.stage in {"completed", "booking_created"}:
            return "Keep the tone helpful and confirm the saved booking draft without asking for deposit confirmation."
        return "Reply naturally and keep the traveler on the current workflow step."

    def _rewrite_violates_constraints(self, message_key: str, rewritten: str) -> bool:
        lowered = rewritten.lower()
        if message_key in {
            "session.ask_phone_first",
            "session.ask_phone_first_repeat",
            "session.ask_phone",
            "session.explain_phone_request",
            "session.explain_phone_request_repeat",
            "session.privacy_phone_request",
            "session.phone_negative",
            "session.phone_offtrack",
        }:
            if "whatsapp" not in lowered or "number" not in lowered:
                return True
            if not rewritten.rstrip().endswith((".", "?", "!")):
                return True
        if message_key in {"session.ask_passport_upload", "session.passport_upload_pending"}:
            forbidden = (
                "passport number",
                "passport expiry",
                "expiry date",
                "passport nationality",
                "full name exactly",
            )
            if any(bit in lowered for bit in forbidden):
                return True
        if message_key in {"session.booking_created_complete", "session.booking_completed"}:
            forbidden_payment_prompts = (
                "would you like to pay",
                "would you like to confirm",
                "pay the deposit",
                "reply yes",
                "reply no",
                "confirm the deposit",
            )
            if any(bit in lowered for bit in forbidden_payment_prompts):
                return True
        return False

    def _phone_step_persona_fallback(self, intent: str, repeat_count: int) -> str:
        if intent == "greeting":
            if repeat_count > 1:
                return (
                    f"I am here with you. I am {self.agent_persona_name} from Ravel Traveler. "
                    "Please send your WhatsApp number so I can open the right traveler profile."
                )
            return (
                f"Hi, I am {self.agent_persona_name}, Ravel Traveler's sales agent. "
                "Please share your WhatsApp number so I can check your traveler profile safely."
            )
        if intent == "privacy_concern":
            if repeat_count > 1:
                return (
                    "I understand the concern. I only use the number to match the correct Ravel Traveler profile "
                    "and avoid mixing your details with another traveler. Please send your WhatsApp number when ready."
                )
            return (
                "Your WhatsApp number is used only to check or create your Ravel Traveler profile safely. "
                "Please send the number so I can continue."
            )
        if intent == "clarification":
            if repeat_count > 1:
                return (
                    "Same reason, and I will keep it simple: the number lets me find the correct traveler profile "
                    "before we talk about trips or reservations. Please send your WhatsApp number."
                )
            return (
                "Of course. I ask for your WhatsApp number so I can find your Ravel Traveler profile safely "
                "before checking trips or reservations. Please send the number when ready."
            )
        if intent == "negative":
            return (
                "No problem. I cannot check your traveler profile or continue the reservation flow without a WhatsApp number. "
                "If you want to continue, please send it."
            )
        return (
            "I can help with the trip details after I find the right traveler profile. "
            "Please send your WhatsApp number first."
        )

    @staticmethod
    def _phone_step_message_key(intent: str, repeat_count: int) -> str:
        if intent == "greeting":
            return "session.ask_phone_first_repeat" if repeat_count > 1 else "session.ask_phone_first"
        if intent == "privacy_concern":
            return "session.privacy_phone_request"
        if intent == "clarification":
            return "session.explain_phone_request_repeat" if repeat_count > 1 else "session.explain_phone_request"
        if intent == "negative":
            return "session.phone_negative"
        return "session.phone_offtrack"

    @staticmethod
    def _register_persona_intent(session: SessionState, intent: str) -> int:
        if session._last_persona_intent == intent:
            session._persona_intent_repeat_count += 1
        else:
            session._last_persona_intent = intent
            session._persona_intent_repeat_count = 1
        return session._persona_intent_repeat_count

    @staticmethod
    def _classify_customer_message(text: str) -> str:
        lowered = (text or "").strip().casefold()
        compact = re.sub(r"[^\w\s\u0600-\u06ff]+", " ", lowered)
        words = set(compact.split())
        if not lowered:
            return "empty"
        if SessionFlowManager._is_greeting_intent(text):
            return "greeting"
        if any(token in lowered for token in {"why", "what", "mean", "understand", "?", "ليه", "لماذا", "يعني", "مش فاهم"}):
            return "clarification"
        if any(token in compact for token in {"privacy", "safe", "secure", "why number", "personal", "خصوصية", "امان", "آمن"}):
            return "privacy_concern"
        if words & {"no", "stop", "cancel", "لا", "الغاء"}:
            return "negative"
        return "workflow_reply"

    @staticmethod
    def _is_greeting_intent(text: str) -> bool:
        compact = re.sub(r"[^\w\s\u0600-\u06ff]+", " ", (text or "").strip().casefold())
        words = set(compact.split())
        greetings = {"hi", "hello", "hey", "مرحبا", "اهلا", "أهلا", "السلام", "هاي"}
        return bool(words & greetings) and len(words) <= 4

    def _result_message(self, result: dict[str, Any], gateway, language: str = "en") -> str:
        return self._preview_message(result, gateway, language)

    def _preview_message(self, result: dict[str, Any], gateway, language: str = "en") -> str:
        trip_result = result.get("trip_result") or {}
        open_trips = trip_result.get("open_trips", [])
        tbd_trips = trip_result.get("date_tbd_trips", [])

        if result.get("handoff_required"):
            return self._copy_text(gateway, "session.handoff", self._handoff_message(gateway, language, result.get("handoff_reason", "")), language=language)

        if open_trips:
            top = open_trips[:3]
            trip_lines = []
            for i, item in enumerate(top, start=1):
                availability = "currently available"
                price = item.get("public_price") or "price not set"
                trip_lines.append(f"{i}. {item['trip_name']} ({item['start_date']} to {item['end_date']}, {availability}, {price})")
            return self._copy_text(
                gateway,
                "session.preview_open_trips",
                "Here are upcoming options:\n{trip_lines}\nWhich trip are you most interested in? Please reply with the trip number (e.g. 1) or 'no' to stop.",
                language=language,
                trip_lines="\n".join(trip_lines),
            )

        if tbd_trips:
            names = []
            for i, item in enumerate(tbd_trips[:3], start=1):
                names.append(f"{i}. {item['trip_name']}")
            return self._copy_text(
                gateway,
                "session.preview_date_tbd",
                "I found trips in this category, but the dates are not confirmed yet:\n{trip_names}\nWhich trip are you most interested in? Please reply with the trip number (e.g. 1) or 'no' to stop.",
                language=language,
                trip_names="\n".join(names),
            )

        return self._copy_text(
            gateway,
            "session.result_no_trip",
            "There are no confirmed upcoming trips in that category right now.",
            language=language,
        )

    def _confirmation_message(self, result: dict[str, Any], gateway, language: str = "en") -> str:
        if result.get("handoff_required"):
            return self._copy_text(gateway, "session.handoff", self._handoff_message(gateway, language, result.get("handoff_reason", "")), language=language)
        lead = (result.get("write_result") or {}).get("lead_update") or {}
        lead_id = lead.get("lead_id", "new lead")
        return f"Confirmed. I saved {lead_id} and updated the traveler profile from the intake form."

    def _is_positive_confirmation(self, text: str) -> bool:
        compact = self._normalize_choice_text(text)
        if compact in {"yes", "y", "confirm", "confirmed", "ok", "okay", "interested", "book", "تمام", "اه", "نعم"}:
            return True
        return any(
            phrase in compact
            for phrase in {
                "ineedit",
                "iwantit",
                "wantit",
                "needit",
                "bookit",
                "takethis",
                "thisone",
                "thatone",
                "okayineedit",
                "okiwant",
                "yesiwant",
                "yesineed",
                "iaminterested",
            }
        )

    def _is_negative_confirmation(self, text: str) -> bool:
        compact = self._normalize_choice_text(text)
        if compact in {"no", "n", "later", "notnow", "stop", "لا", "لأ", "بعدين"}:
            return True
        return any(phrase in compact for phrase in {"dontwant", "notinterested", "cancel"})

    @staticmethod
    def _normalize_choice_text(text: str) -> str:
        lowered = str(text or "").casefold()
        return re.sub(r"[^0-9a-z\u0600-\u06ff]+", "", lowered)

    @staticmethod
    def _similarity(left: str, right: str) -> float:
        if not left or not right:
            return 0.0
        return SequenceMatcher(None, left, right).ratio()
