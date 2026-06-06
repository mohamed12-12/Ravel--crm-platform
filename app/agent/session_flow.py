from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from app.logger import agent_logger
from scripts.phase1_readonly_agent import normalize_trip_type


@dataclass
class SessionState:
    id: str
    stage: str = "awaiting_phone"
    language: str = "en"
    customer_name: str = ""
    birthday: str = ""
    gender: str = ""
    nationality: str = ""
    raw_phone: str = ""
    country_code: str = ""
    trip_type: str = ""
    messages: list[dict[str, str]] = field(default_factory=list)
    preview: dict[str, Any] | None = None
    final_result: dict[str, Any] | None = None
    booking_result: dict[str, Any] | None = None
    selected_trip_id: str = ""
    selected_trip_name: str = ""
    room_type: str = ""
    flight_option: str = ""
    currency: str = ""


class SessionFlowManager:
    def __init__(self, human_handoff_phone: str = "") -> None:
        self._sessions: dict[str, SessionState] = {}
        self.human_handoff_phone = human_handoff_phone

    def clear(self) -> None:
        self._sessions.clear()

    def create_session(self, gateway=None) -> SessionState:
        session = SessionState(id=uuid.uuid4().hex)
        session.messages.append(
            {
                "role": "assistant",
                "text": self._copy_text(
                    gateway,
                    "session.ask_phone_first",
                    "Hello. I am Rahma Traveler's sales agent. Please share your WhatsApp number so I can check your profile safely.",
                    language=session.language,
                ),
            }
        )
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> SessionState | None:
        return self._sessions.get(session_id)

    def submit_intake(self, session: SessionState, payload: dict[str, Any], gateway) -> SessionState:
        if session.stage == "completed":
            session.messages.append(
                {
                    "role": "assistant",
                    "text": self._copy_text(gateway, "session.completed", "This session is already completed. Start a new session to continue.", language=session.language),
                }
            )
            return session

        full_name = str(payload.get("fullName", "")).strip()
        birthday = str(payload.get("birthday", "")).strip()
        gender = str(payload.get("gender", "")).strip()
        nationality = str(payload.get("nationality", "")).strip()
        raw_phone = str(payload.get("rawPhone", "")).strip() or session.raw_phone
        country_code = str(payload.get("countryCode", "")).strip()
        language = str(payload.get("language", "en")).strip().lower() or "en"

        missing = [
            label
            for label, value in {
                "full name": full_name,
                "birthday": birthday,
                "gender": gender,
                "nationality": nationality,
                "WhatsApp number": raw_phone,
            }.items()
            if not value
        ]
        if missing:
            session.messages.append({"role": "assistant", "text": f"Please complete: {', '.join(missing)}."})
            return session

        session.customer_name = full_name
        session.birthday = birthday
        session.gender = gender
        session.nationality = nationality
        session.raw_phone = raw_phone
        session.country_code = country_code
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
            session.stage = "completed"
            session.messages.append({"role": "assistant", "text": self._handoff_message(gateway, session.language, result.get("handoff_reason", ""))})
            return session

        session.stage = "awaiting_trip_type"
        session.messages.append(
            {
                "role": "assistant",
                "text": self._copy_text(
                    gateway,
                    "session.ask_trip_type",
                    "Great. Is this inquiry for a local trip or an international trip?",
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

        session.messages.append({"role": "user", "text": clean_text})

        if session.stage == "completed":
            session.messages.append(
                {
                    "role": "assistant",
                    "text": self._copy_text(gateway, "session.completed", "This session is already completed. Start a new session to continue.", language=session.language),
                }
            )
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
            session.raw_phone = clean_text
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
                session.stage = "completed"
                session.messages.append({"role": "assistant", "text": self._handoff_message(gateway, session.language, result.get("handoff_reason", ""))})
                return session

            traveler = preview.get("traveler") if isinstance(preview.get("traveler"), dict) else {}
            if preview.get("match_status") == "single_match" and traveler:
                session.customer_name = str(traveler.get("full_name") or "").strip()
                session.country_code = str(traveler.get("code") or session.country_code or "").strip()

            if preview.get("match_status") == "not_found":
                session.stage = "awaiting_intake"
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

            session.stage = "awaiting_trip_type"
            session.messages.append(
                {
                    "role": "assistant",
                    "text": self._copy_text(
                        gateway,
                        "session.ask_trip_type",
                        "I found your traveler profile. Is this inquiry for a local trip or an international trip?",
                        language=session.language,
                    ),
                }
            )
            return session

        if session.stage == "awaiting_trip_type":
            normalized = normalize_trip_type(clean_text)
            if normalized is None:
                session.messages.append(
                    {
                        "role": "assistant",
                        "text": self._copy_text(
                            gateway,
                            "session.ask_trip_type_retry",
                            "Please reply with either local or international so I can check upcoming trips.",
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
            session.stage = "awaiting_confirmation"
            session.messages.append({"role": "assistant", "text": self._preview_message(preview, gateway, session.language)})
            return session

        if session.stage == "awaiting_confirmation":
            if self._is_negative_confirmation(clean_text):
                session.stage = "completed"
                session.messages.append(
                    {
                        "role": "assistant",
                        "text": "No lead was written. The customer can be added to waitlist follow-up in the next automation phase.",
                    }
                )
                return session

            selected_trip = None
            open_trips = (session.preview.get("trip_result") or {}).get("open_trips", [])
            date_tbd = (session.preview.get("trip_result") or {}).get("date_tbd_trips", [])
            all_offered = open_trips + date_tbd

            if self._is_positive_confirmation(clean_text):
                if len(all_offered) == 1:
                    selected_trip = all_offered[0]
                elif len(open_trips) == 1:
                    selected_trip = open_trips[0]
            
            # Check if they typed a number (1, 2, 3)
            if selected_trip is None and clean_text.strip() in ["1", "2", "3", "4", "5"]:
                idx = int(clean_text.strip()) - 1
                if idx < len(all_offered):
                    selected_trip = all_offered[idx]
            elif selected_trip is None:
                for trip in all_offered:
                    if trip["trip_name"].lower() in clean_text.lower() or clean_text.lower() in trip["trip_name"].lower():
                        selected_trip = trip
                        break
                    
            if not selected_trip:
                session.messages.append(
                    {
                        "role": "assistant",
                        "text": "Please choose one of the available trips by typing its number or name, or reply 'no' to stop.",
                    }
                )
                return session

            session.selected_trip_id = selected_trip["trip_id"]
            session.selected_trip_name = selected_trip["trip_name"]

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
            
            # Transition to Shared Booking Module
            session.stage = "awaiting_room_type"
            session.messages.append(
                {
                    "role": "assistant", 
                    "text": self._room_type_prompt(
                        gateway, 
                        session,
                    )
                }
            )
            return session

        if session.stage == "awaiting_room_type":
            room = clean_text.lower()
            if "single" in room or "فردي" in room:
                session.room_type = "Single"
            elif "double" in room or "ثنائي" in room:
                session.room_type = "Double"
            elif "triple" in room or "ثلاثي" in room:
                session.room_type = "Triple"
            else:
                session.messages.append({"role": "assistant", "text": "Please choose: Single, Double, or Triple."})
                return session

            if not self._room_has_capacity(session, session.room_type):
                available = self._available_room_types(session)
                if available:
                    options = ", ".join(available)
                    text = f"{session.room_type} is not available for this trip. Available room options: {options}."
                else:
                    text = "There is no remaining draftable room capacity for this trip. I will keep this as a follow-up for the sales team."
                session.room_type = ""
                session.messages.append({"role": "assistant", "text": text})
                return session
            
            session.stage = "awaiting_flight"
            session.messages.append(
                {
                    "role": "assistant",
                    "text": self._copy_text(
                        gateway,
                        "session.ask_flight",
                        "Would you like to book flights with us? (Yes/No)",
                        language=session.language
                    )
                }
            )
            return session

        if session.stage == "awaiting_flight":
            if self._is_positive_confirmation(clean_text):
                session.flight_option = "With Flight"
            else:
                session.flight_option = "Without Flight"
            
            session.stage = "awaiting_currency"
            session.messages.append(
                {
                    "role": "assistant",
                    "text": self._copy_text(
                        gateway,
                        "session.ask_currency",
                        "What is your preferred currency for payment? (EGP/USD)",
                        language=session.language
                    )
                }
            )
            return session

        if session.stage == "awaiting_currency":
            curr = clean_text.upper()
            if "USD" in curr or "دولار" in curr:
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
                session.stage = "completed"
                return session

            if not session.final_result:
                agent_logger.error(f"Cannot create booking: final_result is missing.")
                session.messages.append({"role": "assistant", "text": "I encountered an error: lead data is missing. Please start a new session."})
                session.stage = "completed"
                return session

            agent_logger.info(f"Finalizing booking: traveler={session.customer_name}, trip={trip_id}, room={session.room_type}, flight={session.flight_option}, currency={session.currency}")
            
            booking = gateway.create_booking(
                traveler_id=(session.final_result.get("traveler") or {}).get("traveler_id", "TBD"),
                traveler_name=session.customer_name,
                trip_id=trip_id,
                room_type=session.room_type,
                flight_option=session.flight_option,
                currency=session.currency,
                channel="web-demo",
                lead_id=(session.final_result.get("write_result") or {}).get("lead_update", {}).get("lead_id", "TBD"),
                source="Web Shared Booking Module",
                agent_notes=f"Room: {session.room_type}, Flight: {session.flight_option}, Currency: {session.currency}",
            )
            session.booking_result = booking
            session.stage = "completed"
            
            booking_id = (booking.get("write_result") or {}).get("booking_draft", {}).get("booking_id", "NEW")
            session.messages.append(
                {
                    "role": "assistant",
                    "text": self._copy_text(
                        gateway,
                        "session.confirm_booking",
                        f"Thank you. I have created a booking draft {booking_id}. Would you like to confirm it by paying the deposit?",
                        language=session.language,
                        booking_id=booking_id
                    )
                }
            )
            return session

        session.messages.append(
            {
                "role": "assistant",
                "text": self._copy_text(
                    gateway,
                    "session.fallback",
                    "I could not process that message in the current stage. Please start a new session.",
                ),
            }
        )
        return session

    def _handoff_message(self, gateway, language: str, reason: str = "") -> str:
        if reason == "phone_name_conflict":
            return self._copy_text(
                gateway,
                "handoff.phone_name_conflict",
                "This WhatsApp number already exists for another traveler name in the CRM. I paused automation so the sales team can review it safely.",
                language=language
            )
        if reason == "blacklisted_customer":
            return self._copy_text(
                gateway,
                "handoff.blacklisted_customer",
                "This number is blacklisted.",
                language=language
            )
        if reason == "duplicate_phone_match":
            return self._copy_text(
                gateway,
                "handoff.duplicate_phone_match",
                "This phone number is linked to multiple traveler profiles in our CRM. I have paused automation to ensure your record is updated correctly.",
                language=language
            )
        
        fallback = "I flagged this request for a senior team review. A team member will follow up shortly."
        if self.human_handoff_phone:
            fallback = f"I flagged this request for a senior team review. Please contact our office directly at {self.human_handoff_phone} for support."
            
        return self._copy_text(
            gateway,
            "handoff.generic",
            fallback,
            language=language,
            phone=self.human_handoff_phone
        )

    def _room_type_prompt(self, gateway, session: SessionState) -> str:
        available = self._available_room_types(session)
        if available:
            fallback = f"What type of room do you prefer? Available: {', '.join(available)}."
        else:
            fallback = "What type of room do you prefer? (Single, Double, or Triple)"
        text = self._copy_text(
            gateway,
            "session.ask_room_type",
            fallback,
            language=session.language,
        )
        if available and "available" not in text.lower():
            text = f"{text} Available: {', '.join(available)}."
        return text

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
        trip = self._selected_trip(session)
        if not trip:
            return ["Single", "Double", "Triple"]
        room_fields = {
            "Single": "available_single",
            "Double": "available_double",
            "Triple": "available_triple",
        }
        available = []
        for room, field in room_fields.items():
            value = trip.get(field)
            if value is None:
                continue
            try:
                if int(value) > 0:
                    available.append(room)
            except (TypeError, ValueError):
                continue
        return available

    def _room_has_capacity(self, session: SessionState, room_type: str) -> bool:
        trip = self._selected_trip(session)
        if not trip:
            return True
        field = {
            "Single": "available_single",
            "Double": "available_double",
            "Triple": "available_triple",
        }.get(room_type)
        if not field:
            return False
        value = trip.get(field)
        if value is None:
            return False
        try:
            return int(value) > 0
        except (TypeError, ValueError):
            return False

    def _copy_text(self, gateway, message_key: str, fallback: str, language: str = "en", **values: Any) -> str:
        text = fallback
        try:
            sheet_text = gateway.get_message_copy(message_key, language=language)
            if sheet_text:
                text = sheet_text
            
            if values:
                text = text.format(**values)
                
            agent_logger.debug(f"Resolved copy: {message_key} [{language}] -> {text}")
            agent_logger.info(f"OUTGOING: {text}")
            return text
        except Exception as e:
            agent_logger.error(f"Error resolving copy {message_key}: {e}")
            agent_logger.info(f"OUTGOING: {text}")
            return text

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
                remaining = item.get("remaining_places")
                if remaining is None:
                    availability = "remaining places not configured"
                else:
                    availability = f"{remaining} places remaining"
                trip_lines.append(f"{i}. {item['trip_name']} ({item['start_date']} to {item['end_date']}, {availability})")
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
        normalized = text.strip().lower()
        return normalized in {"yes", "y", "confirm", "confirmed", "ok", "okay", "interested", "book", "تمام", "اه", "نعم"}

    def _is_negative_confirmation(self, text: str) -> bool:
        normalized = text.strip().lower()
        return normalized in {"no", "n", "later", "not now", "stop", "لا", "لأ", "بعدين"}
