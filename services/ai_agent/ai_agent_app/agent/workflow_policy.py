from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ARCHIVE_LIKE_STATUSES = {"inactive", "archived", "blacklisted", "blacklist", "blocked"}
ACTIVE_LIKE_STATUSES = {"", "active", "live", "repeat", "vip"}
ROOM_OCCUPANCY = {"single": 1, "double": 2, "triple": 3}
IDENTITY_TOOLS = {"find_traveler_by_phone"}
WRITE_TOOLS = {"create_lead", "update_lead_stage", "create_booking_draft", "create_handoff"}
LEAD_SAVE_TOOLS = {"create_lead"}
MEDIA_TOOLS = {"get_trip_media"}
VERIFIED_TRAVELER_TOOLS = {
    "find_traveler_by_phone",
    "get_traveler_profile",
    "get_traveler_trip_history",
    "search_available_trips",
    "get_trip_details",
    *MEDIA_TOOLS,
    *WRITE_TOOLS,
}
PRE_TRIP_SEARCH_TOOLS = VERIFIED_TRAVELER_TOOLS - {"search_available_trips", "get_trip_details", "get_trip_media", "create_booking_draft"}
PRE_BOOKING_TOOLS = VERIFIED_TRAVELER_TOOLS - {"create_booking_draft", "get_trip_media"}
SELECTED_TRIP_TOOLS = (PRE_BOOKING_TOOLS - {"search_available_trips"}) | MEDIA_TOOLS
SELECTED_TRIP_BOOKING_TOOLS = VERIFIED_TRAVELER_TOOLS - {"search_available_trips"}


@dataclass(frozen=True)
class WorkflowDecision:
    state: str
    customer_status: str
    identity_verified: bool = False
    trip_search_allowed: bool = False
    allowed_tools: set[str] = field(default_factory=set)
    required_step: str = ""
    customer_message_key: str = ""
    assistant_message: str = ""
    verified_traveler: dict[str, Any] = field(default_factory=dict)
    verified_status: str = ""
    handoff_required: bool = False
    reason: str = ""

    def to_context(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "customer_status": self.customer_status,
            "identity_verified": self.identity_verified,
            "trip_search_allowed": self.trip_search_allowed,
            "allowed_tools": sorted(self.allowed_tools),
            "required_step": self.required_step,
            "customer_message_key": self.customer_message_key,
            "assistant_message": self.assistant_message,
            "verified_traveler": dict(self.verified_traveler),
            "verified_status": self.verified_status,
            "handoff_required": self.handoff_required,
            "reason": self.reason,
        }

    def allows_tool(self, tool_name: str) -> bool:
        return tool_name in self.allowed_tools


class ConversationWorkflowPolicy:
    """Backend-owned guardrails for the Gemini sales conversation.

    This policy deliberately consumes only session fields and CRM/tool results.
    It does not mutate CRM state and does not replace CRM business services.
    """

    @staticmethod
    def _is_arabic(session_context: dict[str, Any]) -> bool:
        return str(session_context.get("language") or "").strip().lower().startswith("ar")

    def evaluate(self, session_context: dict[str, Any]) -> WorkflowDecision:
        workflow = session_context.get("workflow") if isinstance(session_context.get("workflow"), dict) else {}
        known_traveler = session_context.get("known_traveler") if isinstance(session_context.get("known_traveler"), dict) else {}
        verified = workflow.get("verified_traveler") if isinstance(workflow.get("verified_traveler"), dict) else {}
        traveler = verified or known_traveler or {}
        lookup_status = str(workflow.get("lookup_status") or "").strip().lower()
        raw_phone = str(session_context.get("raw_phone") or session_context.get("pending_raw_phone") or "").strip()

        if lookup_status == "duplicate":
            return WorkflowDecision(
                state="duplicate_traveler_detected",
                customer_status="Human review required",
                allowed_tools=IDENTITY_TOOLS,
                required_step="human_review",
                customer_message_key="duplicate_traveler_review",
                assistant_message="This WhatsApp number matches more than one traveler profile, so a team member needs to review it before we continue.",
                handoff_required=True,
                reason="duplicate_phone_match",
            )

        if lookup_status == "not_found":
            return self._new_traveler_decision(session_context)

        if lookup_status == "invalid_phone":
            return WorkflowDecision(
                state="identity_required",
                customer_status="Waiting for WhatsApp number",
                allowed_tools=IDENTITY_TOOLS,
                required_step="collect_valid_whatsapp_number",
                customer_message_key="invalid_phone",
                assistant_message="Please send a valid WhatsApp mobile number. If it is outside Egypt, include the country code, for example +966512345678.",
                reason="invalid_phone",
            )

        traveler_id = str(traveler.get("traveler_id") or "").strip()
        status = str(traveler.get("status") or "").strip()
        normalized_status = status.casefold()
        if traveler_id and status:
            if normalized_status in ARCHIVE_LIKE_STATUSES:
                return WorkflowDecision(
                    state="human_handoff_required",
                    customer_status="Human review required",
                    allowed_tools={"find_traveler_by_phone", "get_traveler_profile"},
                    required_step="human_review",
                    customer_message_key="restricted_traveler_review",
                    assistant_message="I found your traveler profile, but this request needs review by the Ravel Traveler team before we continue.",
                    verified_traveler=dict(traveler),
                    verified_status=status,
                    handoff_required=True,
                    reason=f"restricted_status:{status}",
                )
            return self._post_identity_decision(session_context, traveler=traveler, status=status)

        if raw_phone:
            return WorkflowDecision(
                state="identity_lookup_pending",
                customer_status="Checking CRM",
                allowed_tools=IDENTITY_TOOLS,
                required_step="run_traveler_lookup",
                customer_message_key="identity_lookup_pending",
                assistant_message="I will check this WhatsApp number first, then continue with your trip request.",
                reason="phone_collected",
            )

        return WorkflowDecision(
            state="identity_required",
            customer_status="Waiting for WhatsApp number",
            allowed_tools=IDENTITY_TOOLS,
            required_step="collect_whatsapp_number",
            customer_message_key="identity_required",
            assistant_message="Please share your WhatsApp number first so I can check your Ravel Traveler profile safely.",
            reason="missing_identity",
        )

    def _post_identity_decision(self, session_context: dict[str, Any], *, traveler: dict[str, Any], status: str) -> WorkflowDecision:
        trip_type = str(session_context.get("trip_type") or "").strip().lower()
        selected_trip_id = str(session_context.get("selected_trip_id") or "").strip()
        trip_result = session_context.get("trip_result") if isinstance(session_context.get("trip_result"), dict) else {}
        selected_trip = session_context.get("selected_trip") if isinstance(session_context.get("selected_trip"), dict) else {}
        collection_state = session_context.get("collection_state") if isinstance(session_context.get("collection_state"), dict) else {}

        room_type_collected = bool(collection_state.get("room_type"))
        room_group = str(session_context.get("room_group") or "").strip().lower()
        room_group_collected = bool(collection_state.get("room_group") or room_group)
        room_requirements = session_context.get("room_requirements") if isinstance(session_context.get("room_requirements"), dict) else {}
        mixed_room_requirements = list(room_requirements.get("requirements") or []) if isinstance(room_requirements.get("requirements"), list) else []
        if room_group == "mixed":
            room_type_collected = bool(mixed_room_requirements)
        group_size_collected = bool(collection_state.get("group_size"))
        flights_supported = self._trip_supports_flights(selected_trip)
        flight_option_collected = bool(collection_state.get("flight_option")) or not flights_supported
        has_trip_results = bool(list(trip_result.get("open_trips") or []) or list(trip_result.get("date_tbd_trips") or []))
        arabic = self._is_arabic(session_context)

        common = {
            "identity_verified": True,
            "verified_traveler": dict(traveler),
            "verified_status": status,
            "reason": "crm_identity_verified",
        }

        if trip_type not in {"local", "international"}:
            return WorkflowDecision(
                state="trip_type_required",
                customer_status="Ready",
                allowed_tools=PRE_TRIP_SEARCH_TOOLS,
                required_step="collect_trip_type",
                customer_message_key="trip_type_required",
                assistant_message=(
                    "\u0647\u0644 \u062a\u0631\u063a\u0628 \u0641\u064a \u0631\u062d\u0644\u0629 \u0645\u062d\u0644\u064a\u0629 \u062f\u0627\u062e\u0644 \u0645\u0635\u0631 \u0623\u0645 \u0631\u062d\u0644\u0629 \u062f\u0648\u0644\u064a\u0629 \u062e\u0627\u0631\u062c \u0645\u0635\u0631\u061f\n\n"
                    "1. \u0645\u062d\u0644\u064a\u0629 (Local)\n"
                    "2. \u062f\u0648\u0644\u064a\u0629 (International)"
                    if arabic
                    else "Are you looking for a local trip or an international trip?\n\n1. Local trip\n2. International trip"
                ),
                **common,
            )

        if not selected_trip_id:
            if has_trip_results:
                return WorkflowDecision(
                    state="trip_selection_required",
                    customer_status="Waiting for customer response",
                    trip_search_allowed=True,
                    allowed_tools=PRE_BOOKING_TOOLS,
                    required_step="select_trip",
                    customer_message_key="trip_selection_required",
                    assistant_message="I found matching trips for your choice. Please pick the trip you want to continue with.",
                    **common,
                )
            return WorkflowDecision(
                state="trip_search_ready",
                customer_status="Searching trips",
                trip_search_allowed=True,
                allowed_tools=PRE_BOOKING_TOOLS,
                required_step="search_matching_trips",
                customer_message_key="trip_search_ready",
                assistant_message="Thanks. I will check the available trips that match your choice now.",
                **common,
            )

        if not room_group_collected:
            return WorkflowDecision(
                state="traveler_gender_required",
                customer_status="Waiting for customer response",
                allowed_tools=SELECTED_TRIP_TOOLS,
                required_step="collect_traveler_gender",
                customer_message_key="traveler_gender_required",
                assistant_message=(
                    "Ù‡Ù„ Ø§Ù„Ù…Ø³Ø§ÙØ±ÙˆÙ† Ø´Ø¨Ø§Ø¨ Ø£Ù… Ø¨Ù†Ø§ØªØŸ (Traveler group)\n\n"
                    "1) Ø´Ø¨Ø§Ø¨ (Boys / Male)\n"
                    "2) Ø¨Ù†Ø§Øª (Girls / Female)"
                    if arabic
                    else "Are the travelers boys/male or girls/female?\n\n1) Boys / Male\n2) Girls / Female"
                ),
                **common,
            )

        if not room_type_collected:
            return WorkflowDecision(
                state="room_type_required",
                customer_status="Waiting for customer response",
                allowed_tools=SELECTED_TRIP_TOOLS,
                required_step="collect_room_type",
                customer_message_key="room_type_required",
                assistant_message=self._mixed_room_inventory_prompt(selected_trip, arabic=arabic)
                if room_group == "mixed"
                else self._room_inventory_prompt(selected_trip, room_group=room_group, arabic=arabic),
                **common,
            )

        if not group_size_collected:
            return WorkflowDecision(
                state="group_size_required",
                customer_status="Waiting for customer response",
                allowed_tools=SELECTED_TRIP_TOOLS,
                required_step="collect_group_size",
                customer_message_key="group_size_required",
                assistant_message=(
                    "\u0643\u0645 \u0639\u062f\u062f \u0627\u0644\u0645\u0633\u0627\u0641\u0631\u064a\u0646 \u0641\u064a \u0647\u0630\u0627 \u0627\u0644\u062d\u062c\u0632\u061f (Group size)"
                    if arabic
                    else "How many travelers are going on this booking?"
                ),
                **common,
            )

        mixed_capacity_issue = self._mixed_room_capacity_issue(selected_trip, mixed_room_requirements)
        if mixed_capacity_issue:
            return WorkflowDecision(
                state="capacity_handoff_required",
                customer_status="Human review required",
                allowed_tools={"create_handoff"},
                required_step="create_capacity_handoff",
                customer_message_key="capacity_handoff_required",
                assistant_message=mixed_capacity_issue,
                handoff_required=True,
                **{**common, "reason": "room_capacity"},
            )

        room_type = str(session_context.get("room_type") or "")
        capacity = self._room_capacity(selected_trip, room_type, room_group)
        requested_group_size = self._as_int(session_context.get("group_size")) or 1
        requested_rooms = self._rooms_needed(room_type, requested_group_size)
        if capacity is not None and requested_rooms > capacity:
            return WorkflowDecision(
                state="capacity_handoff_required",
                customer_status="Human review required",
                allowed_tools={"create_handoff"},
                required_step="create_capacity_handoff",
                customer_message_key="capacity_handoff_required",
                assistant_message=(
                    (
                        "\u062e\u064a\u0627\u0631 \u0627\u0644\u063a\u0631\u0641\u0629 \u0627\u0644\u0645\u0637\u0644\u0648\u0628 \u063a\u064a\u0631 \u0645\u062a\u0627\u062d \u0644\u0644\u0645\u062c\u0645\u0648\u0639\u0629 \u0628\u0627\u0644\u0643\u0627\u0645\u0644 \u062d\u0627\u0644\u064a\u0627.\n"
                        f"\u0633\u0628\u0628 \u0627\u0644\u062a\u062d\u0648\u064a\u0644: \u0647\u0630\u0627 \u0627\u0644\u0637\u0644\u0628 \u064a\u062d\u062a\u0627\u062c {requested_rooms} \u063a\u0631\u0641\u0629 \u0645\u0646 \u0646\u0648\u0639 {room_type}\u060c \u0648\u0627\u0644\u0645\u062a\u0627\u062d \u0641\u064a CRM \u0647\u0648 {capacity} \u0641\u0642\u0637.\n"
                        "\u0633\u0623\u0631\u0633\u0644 \u0627\u0644\u0637\u0644\u0628 \u0644\u0641\u0631\u064a\u0642 Ravel Traveler \u0644\u0645\u0631\u0627\u062c\u0639\u0629 \u0627\u0644\u0628\u062f\u0627\u0626\u0644 \u0627\u0644\u0645\u062a\u0627\u062d\u0629."
                    )
                    if arabic
                    else (
                        "The requested room option is not available for the full group at the moment.\n"
                        f"Reason for escalation: this request needs {requested_rooms} {room_type.lower()} room"
                        f"{'' if requested_rooms == 1 else 's'}, but CRM shows {capacity} available.\n"
                        "I will send this request to the Ravel Traveler team, and an employee will call you to confirm the available alternatives."
                    )
                ),
                handoff_required=True,
                **common,
            )

        if not flight_option_collected:
            return WorkflowDecision(
                state="flight_option_required",
                customer_status="Waiting for customer response",
                allowed_tools=SELECTED_TRIP_TOOLS,
                required_step="collect_flight_preference",
                customer_message_key="flight_option_required",
                assistant_message=(
                    "\u0647\u0644 \u062a\u0641\u0636\u0644 \u0627\u0644\u0631\u062d\u0644\u0629 \u0645\u0639 \u0627\u0644\u0637\u064a\u0631\u0627\u0646 \u0623\u0645 \u0628\u062f\u0648\u0646 \u0637\u064a\u0631\u0627\u0646\u061f\n\n"
                    "With Flight | Without Flight"
                    if arabic
                    else "Do you want this trip with flights or without flights?\n\n1. With flights\n2. Without flights"
                ),
                **common,
            )

        passport_required = self._passport_required(
            selected_trip,
            str(session_context.get("flight_option") or ""),
            str(session_context.get("trip_type") or ""),
        )
        passport_ready = bool(session_context.get("passport_attachment_ref") or session_context.get("passport_on_file"))
        if passport_required and not passport_ready:
            return WorkflowDecision(
                state="awaiting_passport_upload",
                customer_status="Waiting for customer response",
                allowed_tools=SELECTED_TRIP_TOOLS,
                required_step="collect_passport_attachment",
                customer_message_key="passport_attachment_required",
                assistant_message=(
                    "\u0645\u0646 \u0641\u0636\u0644\u0643 \u0623\u0631\u0641\u0642 \u0635\u0648\u0631\u0629 \u0623\u0648 \u0645\u0644\u0641 PDF \u0644\u062c\u0648\u0627\u0632 \u0627\u0644\u0633\u0641\u0631 \u0644\u0645\u062a\u0627\u0628\u0639\u0629 \u0647\u0630\u0647 \u0627\u0644\u0631\u062d\u0644\u0629 \u0627\u0644\u062f\u0648\u0644\u064a\u0629."
                    if arabic
                    else "Please attach a passport image or PDF so I can continue with this trip."
                ),
                **common,
            )

        return WorkflowDecision(
            state="booking_ready",
            customer_status="Ready",
            allowed_tools=SELECTED_TRIP_BOOKING_TOOLS,
            required_step="create_booking_draft",
            customer_message_key="booking_ready",
            assistant_message="I have the trip details needed to prepare your booking draft.",
            **common,
        )

    def _new_traveler_decision(self, session_context: dict[str, Any]) -> WorkflowDecision:
        collection_state = session_context.get("collection_state") if isinstance(session_context.get("collection_state"), dict) else {}
        arabic = self._is_arabic(session_context)
        customer_name = str(session_context.get("customer_name") or "").strip()
        nationality = str(session_context.get("nationality") or collection_state.get("nationality_value") or "").strip()
        birthday = str(session_context.get("birthday") or collection_state.get("birthday_value") or "").strip()
        currency = str(session_context.get("currency") or collection_state.get("currency_value") or "").strip()

        common = {
            "allowed_tools": IDENTITY_TOOLS | LEAD_SAVE_TOOLS,
            "reason": "crm_lookup_not_found",
        }

        if not customer_name:
            return WorkflowDecision(
                state="traveler_not_found",
                customer_status="New traveler details required",
                required_step="collect_new_traveler_name",
                customer_message_key="new_traveler_name_required",
                assistant_message=(
                    "\u0644\u0645 \u0623\u062c\u062f \u0645\u0644\u0641\u0627 \u0644\u0647\u0630\u0627 \u0627\u0644\u0631\u0642\u0645.\n"
                    "\u0645\u0646 \u0641\u0636\u0644\u0643 \u0627\u0643\u062a\u0628 \u0627\u0633\u0645\u0643 \u0627\u0644\u062b\u0644\u0627\u062b\u064a\u060c \u0645\u062b\u0644: \u0645\u062d\u0645\u062f \u0623\u0634\u0631\u0641 \u0635\u0641\u0648\u062a."
                    if arabic
                    else "I could not find a traveler profile for this WhatsApp number. Please enter your full name as three parts, for example: Mohamed Ashraf Safwat."
                ),
                **common,
            )

        if not nationality:
            return WorkflowDecision(
                state="nationality_required",
                customer_status="Waiting for customer response",
                required_step="collect_nationality",
                customer_message_key="nationality_required",
                assistant_message=self._nationality_prompt(arabic=arabic),
                **common,
            )

        if not birthday:
            return WorkflowDecision(
                state="birthday_required",
                customer_status="Waiting for customer response",
                required_step="collect_birthday",
                customer_message_key="birthday_required",
                assistant_message=self._birthday_prompt(arabic=arabic),
                **common,
            )

        if not currency:
            return WorkflowDecision(
                state="currency_required",
                customer_status="Waiting for customer response",
                required_step="collect_payment_currency",
                customer_message_key="currency_required",
                assistant_message=self._currency_prompt(arabic=arabic),
                **common,
            )

        return WorkflowDecision(
            state="traveler_not_found",
            customer_status="New traveler details ready",
            allowed_tools=IDENTITY_TOOLS | LEAD_SAVE_TOOLS,
            required_step="save_new_traveler_lead",
            customer_message_key="new_traveler_details_ready",
            assistant_message=(
                "\u062a\u0645 \u062c\u0645\u0639 \u0628\u064a\u0627\u0646\u0627\u062a \u0627\u0644\u0645\u0633\u0627\u0641\u0631 \u0627\u0644\u062c\u062f\u064a\u062f \u0648\u064a\u0645\u0643\u0646 \u062d\u0641\u0638 \u0627\u0644\u0637\u0644\u0628 \u0641\u064a CRM."
                if arabic
                else "I have the new traveler details needed to save the request."
            ),
            reason="new_traveler_profile_ready",
        )

    @staticmethod
    def _nationality_prompt(*, arabic: bool = False) -> str:
        return (
            "\u0645\u0627 \u062c\u0646\u0633\u064a\u062a\u0643\u061f\n"
            "\u0627\u0643\u062a\u0628 \u0627\u0644\u062c\u0646\u0633\u064a\u0629 \u0643\u0645\u0627 \u0647\u064a \u0641\u064a \u0627\u0644\u062c\u0648\u0627\u0632 \u0623\u0648 \u0627\u0644\u0647\u0648\u064a\u0629."
            if arabic
            else "What is your nationality? Please type it as it appears in your passport or national ID."
        )

    @staticmethod
    def _birthday_prompt(*, arabic: bool = False) -> str:
        return (
            "\u0645\u0627 \u062a\u0627\u0631\u064a\u062e \u0645\u064a\u0644\u0627\u062f\u0643\u061f\n"
            "\u0627\u0643\u062a\u0628\u0647 \u0628\u0623\u064a \u0635\u064a\u063a\u0629 \u0648\u0627\u0636\u062d\u0629\u060c \u0645\u062b\u0644 21/08/1995 \u0623\u0648 21 Aug 1995."
            if arabic
            else "What is your date of birth? Type it in any clear format, for example 21/08/1995 or 21 Aug 1995."
        )

    @staticmethod
    def _currency_prompt(*, arabic: bool = False) -> str:
        return (
            "\u0645\u0627 \u0639\u0645\u0644\u0629 \u0627\u0644\u062f\u0641\u0639 \u0627\u0644\u0645\u0641\u0636\u0644\u0629 \u0644\u062f\u064a\u0643\u061f\n\n"
            "1. \u062c\u0646\u064a\u0647 \u0645\u0635\u0631\u064a (EGP)\n"
            "2. \u062f\u0648\u0644\u0627\u0631 \u0623\u0645\u0631\u064a\u0643\u064a (USD)"
            if arabic
            else "Which payment currency do you prefer?\n\n1. Egyptian Pound (EGP)\n2. US Dollar (USD)"
        )

    @staticmethod
    def _as_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _as_bool(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value or "").strip().casefold() in {"1", "true", "yes", "y", "supported", "available"}

    @classmethod
    def _trip_supports_flights(cls, selected_trip: dict[str, Any]) -> bool:
        trip = selected_trip if isinstance(selected_trip, dict) else {}
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

    @classmethod
    def _passport_required(cls, selected_trip: dict[str, Any], flight_option: str, trip_type: str) -> bool:
        trip = selected_trip if isinstance(selected_trip, dict) else {}
        for key in ("passport_required", "requires_passport"):
            if key in trip:
                return cls._as_bool(trip.get(key))
        if "passport_required_with_flight" in trip:
            return str(flight_option or "").strip() == "With Flight" and cls._as_bool(trip.get("passport_required_with_flight"))
        selected_trip_type = str(trip.get("type") or trip.get("trip_type") or trip_type or "").strip().lower()
        return selected_trip_type == "international"

    def _room_inventory_prompt(
        self,
        selected_trip: dict[str, Any],
        *,
        room_group: str = "",
        arabic: bool = False,
    ) -> str:
        if not selected_trip:
            return (
                "\u0645\u0627 \u0646\u0648\u0639 \u0627\u0644\u063a\u0631\u0641\u0629 \u0627\u0644\u0645\u0641\u0636\u0644 \u0644\u062f\u064a\u0643\u061f\n"
                "1. Single (\u0641\u0631\u062f\u064a\u0629)\n"
                "2. Double (\u062b\u0646\u0627\u0626\u064a\u0629)\n"
                "3. Triple (\u062b\u0644\u0627\u062b\u064a\u0629)"
                if arabic
                else "Which room type do you prefer?\n1. Single\n2. Double\n3. Triple"
            )

        lines: list[str] = []
        single = self._as_int(selected_trip.get("available_single"))
        if single > 0:
            lines.append("Single")

        double_total = self._as_int(selected_trip.get("available_double"))
        double_boys = self._as_int(selected_trip.get("boys_double"))
        double_girls = self._as_int(selected_trip.get("girls_double"))
        if double_boys or double_girls:
            if double_boys and room_group != "girls":
                lines.append("Double boys")
            if double_girls and room_group != "boys":
                lines.append("Double girls")
        elif double_total > 0:
            lines.append("Double")

        triple_total = self._as_int(selected_trip.get("available_triple"))
        triple_boys = self._as_int(selected_trip.get("boys_triple"))
        triple_girls = self._as_int(selected_trip.get("girls_triple"))
        if triple_boys or triple_girls:
            if triple_boys and room_group != "girls":
                lines.append("Triple boys")
            if triple_girls and room_group != "boys":
                lines.append("Triple girls")
        elif triple_total > 0:
            lines.append("Triple")

        if not lines:
            if room_group in {"boys", "girls"}:
                return (
                    "Ù„Ø§ ØªÙˆØ¬Ø¯ ØºØ±Ù Ù…ØªØ§Ø­Ø© Ù„Ù‡Ø°Ø§ Ø§Ù„Ø§Ø®ØªÙŠØ§Ø± Ø­Ø§Ù„ÙŠØ§Ù‹ Ø­Ø³Ø¨ Ø¨ÙŠØ§Ù†Ø§Øª CRM.\n"
                    "Ø³Ø£Ø±Ø³Ù„ Ø·Ù„Ø¨Ùƒ Ø¥Ù„Ù‰ ÙØ±ÙŠÙ‚ Ø±Ø­Ù…Ø© ØªØ±Ø§ÙÙ„ Ù„Ù…Ø±Ø§Ø¬Ø¹Ø© Ø§Ù„Ø¨Ø¯Ø§Ø¦Ù„."
                    if arabic
                    else "That room option is currently unavailable for this traveler group.\n"
                    "I will send your request to the Ravel Traveler team to review the alternatives."
                )
            return self._room_inventory_prompt({}, arabic=arabic)
        if arabic:
            labels = {
                "Single": "Single (\u0641\u0631\u062f\u064a\u0629)",
                "Double boys": "Double - Boys (\u062b\u0646\u0627\u0626\u064a\u0629 \u0634\u0628\u0627\u0628)",
                "Double girls": "Double - Girls (\u062b\u0646\u0627\u0626\u064a\u0629 \u0628\u0646\u0627\u062a)",
                "Double": "Double (\u062b\u0646\u0627\u0626\u064a\u0629)",
                "Triple boys": "Triple - Boys (\u062b\u0644\u0627\u062b\u064a\u0629 \u0634\u0628\u0627\u0628)",
                "Triple girls": "Triple - Girls (\u062b\u0644\u0627\u062b\u064a\u0629 \u0628\u0646\u0627\u062a)",
                "Triple": "Triple (\u062b\u0644\u0627\u062b\u064a\u0629)",
            }
            formatted_lines = []
            for line in lines:
                formatted_lines.append(f"{len(formatted_lines) + 1}) {labels.get(line, line)}")
            return (
                "\u0627\u062e\u062a\u0631 \u0646\u0648\u0639 \u0627\u0644\u063a\u0631\u0641\u0629 \u0627\u0644\u0645\u0641\u0636\u0644:\n\n"
                "\u0627\u0644\u062e\u064a\u0627\u0631\u0627\u062a \u0627\u0644\u0645\u062a\u0627\u062d\u0629:\n"
                + "\n".join(formatted_lines)
                + "\n\n"
                "\u0627\u0643\u062a\u0628 \u0627\u0644\u062e\u064a\u0627\u0631 \u0627\u0644\u0645\u0646\u0627\u0633\u0628 \u0623\u0648 \u0631\u0642\u0645\u0647."
            )

        formatted_lines = [f"{index}) {line}" for index, line in enumerate(lines, 1)]
        return (
            "Please choose your preferred room option:\n\n"
            "Available room options:\n"
            + "\n".join(formatted_lines)
            + "\n\nReply with the room name or its number."
        )

    def _mixed_room_inventory_prompt(self, selected_trip: dict[str, Any], *, arabic: bool = False) -> str:
        if arabic:
            return (
                "\u0644\u0644\u0645\u062c\u0645\u0648\u0639\u0629 \u0627\u0644\u0645\u062e\u062a\u0644\u0637\u0629\u060c \u0645\u0646 \u0641\u0636\u0644\u0643 \u062d\u062f\u062f \u0637\u0644\u0628 \u0627\u0644\u063a\u0631\u0641 \u0644\u0644\u0634\u0628\u0627\u0628 \u0648\u0627\u0644\u0628\u0646\u0627\u062a \u0628\u0634\u0643\u0644 \u0645\u0646\u0641\u0635\u0644.\n"
                "\u0645\u062b\u0627\u0644: \u063a\u0631\u0641\u0629 \u0634\u0628\u0627\u0628 \u0648\u063a\u0631\u0641\u0629 \u0628\u0646\u0627\u062a."
            )
        return (
            "For a mixed group, please specify the room request separately for boys and girls.\n"
            "Example: 1 double boys room and 1 double girls room.\n\n"
            + self._room_inventory_prompt(selected_trip, room_group="")
        )

    def _mixed_room_capacity_issue(self, selected_trip: dict[str, Any], requirements: list[dict[str, Any]]) -> str:
        if not requirements:
            return ""
        unavailable = []
        available = []
        for item in requirements:
            if not isinstance(item, dict):
                continue
            room_type = str(item.get("room_type") or "").strip().lower()
            group = str(item.get("room_group") or "").strip().lower()
            rooms = self._as_int(item.get("rooms")) or 0
            if room_type not in {"double", "triple"} or group not in {"boys", "girls"} or rooms <= 0:
                continue
            capacity = self._as_int(selected_trip.get(f"{group}_{room_type}")) or 0
            label = f"{group} {room_type} room"
            if rooms != 1:
                label += "s"
            if rooms > capacity:
                unavailable.append(label)
            else:
                available.append(label)
        if not unavailable:
            return ""
        if available:
            return (
                f"{', '.join(part.title() for part in available)} availability exists, but "
                f"{', '.join(part.title() for part in unavailable)} is not available for the requested rooms. "
                "I will send this request to the Ravel Traveler team to check suitable options."
            )
        return (
            "The requested boys/girls room categories are not available at the moment. "
            "I will send this request to the Ravel Traveler team to check suitable alternatives."
        )

    def _room_capacity(self, selected_trip: dict[str, Any], room_type: str, room_group: str) -> int | None:
        room_key = str(room_type or "").strip().lower()
        group_key = str(room_group or "").strip().lower()
        def available(key: str) -> int | None:
            if key not in selected_trip or selected_trip.get(key) is None:
                return None
            return self._as_int(selected_trip.get(key))

        if room_key == "single":
            return available("available_single")
        if room_key == "double" and group_key in {"boys", "girls"}:
            return available(f"{group_key}_double")
        if room_key == "triple" and group_key in {"boys", "girls"}:
            return available(f"{group_key}_triple")
        if room_key == "double":
            return available("available_double")
        if room_key == "triple":
            return available("available_triple")
        return None

    @staticmethod
    def _rooms_needed(room_type: str, traveler_count: int) -> int:
        occupancy = ROOM_OCCUPANCY.get(str(room_type or "").strip().lower(), 1)
        travelers = max(1, int(traveler_count or 1))
        return (travelers + occupancy - 1) // occupancy

    def block_tool_result(self, tool_name: str, decision: WorkflowDecision) -> dict[str, Any]:
        return {
            "status": "workflow_blocked",
            "tool": tool_name,
            "required_step": decision.required_step or "collect_whatsapp_number",
            "customer_message_key": decision.customer_message_key or "identity_required",
            "assistant_message": decision.assistant_message
            or "Please share your WhatsApp number first so I can check your Ravel Traveler profile safely.",
            "workflow_state": decision.state,
            "executed": False,
        }

