from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ARCHIVE_LIKE_STATUSES = {"inactive", "archived", "blacklisted", "blacklist", "blocked"}
ACTIVE_LIKE_STATUSES = {"", "active", "live", "repeat", "vip"}
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
                assistant_message="This WhatsApp number matches more than one CRM profile, so a team member needs to review it before we continue.",
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
                assistant_message="Please send a valid WhatsApp number with the country code if it is outside Egypt.",
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
                    assistant_message="I found your CRM profile, but this request needs review by the Rahma Traveler team before we continue.",
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
                assistant_message="I will check this WhatsApp number in Rahma CRM first, then continue with your trip request.",
                reason="phone_collected",
            )

        return WorkflowDecision(
            state="identity_required",
            customer_status="Waiting for WhatsApp number",
            allowed_tools=IDENTITY_TOOLS,
            required_step="collect_whatsapp_number",
            customer_message_key="identity_required",
            assistant_message="Please share your WhatsApp number first so I can check your Rahma Traveler profile safely.",
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
        group_size_collected = bool(collection_state.get("group_size"))
        flight_option_collected = bool(collection_state.get("flight_option"))
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
                    else "Are you looking for a local trip or an international trip?"
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
                    "هل المسافرون شباب أم بنات؟ (Traveler group)\n\n"
                    "1) شباب (Boys / Male)\n"
                    "2) بنات (Girls / Female)"
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
                assistant_message=self._room_inventory_prompt(selected_trip, room_group=room_group, arabic=arabic),
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

        capacity = self._room_capacity(selected_trip, str(session_context.get("room_type") or ""), room_group)
        requested_group_size = self._as_int(session_context.get("group_size")) or 1
        if capacity is not None and requested_group_size > capacity:
            return WorkflowDecision(
                state="capacity_handoff_required",
                customer_status="Human review required",
                allowed_tools={"create_handoff"},
                required_step="create_capacity_handoff",
                customer_message_key="capacity_handoff_required",
                assistant_message=(
                    f"لا يمكن تأكيد حجز {requested_group_size} مسافرين في هذا الخيار؛ المتاح من CRM هو {capacity} فقط.\n"
                    "سأرسل الطلب إلى فريق رحمة ترافل، وسيتواصل معك الموظف لتأكيد البدائل المتاحة."
                    if arabic
                    else f"I cannot confirm {requested_group_size} travelers in this option because CRM shows only {capacity} available.\n"
                    "I will send this request to the Rahma Traveler team, and an employee will call you to confirm the available alternatives."
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
                    else "Do you want this trip with flights or without flights?"
                ),
                **common,
            )

        selected_trip_type = str(selected_trip.get("type") or selected_trip.get("trip_type") or "").strip().lower()
        passport_required = selected_trip_type == "international"
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
                    else "Please attach a passport image or PDF so I can continue with this international trip."
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
                    "\u0644\u0645 \u0623\u062c\u062f \u0645\u0644\u0641\u0627 \u0644\u0647\u0630\u0627 \u0627\u0644\u0631\u0642\u0645 \u0641\u064a CRM.\n"
                    "\u0645\u0627 \u0627\u0633\u0645\u0643 \u0627\u0644\u0643\u0627\u0645\u0644\u061f (Full name)"
                    if arabic
                    else "I could not find a traveler profile for this WhatsApp number. What is your full name?"
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
                else "I have the new traveler details needed to save the request in Rahma CRM."
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
            "\u0627\u0643\u062a\u0628\u0647 \u0628\u0635\u064a\u063a\u0629 YYYY-MM-DD."
            if arabic
            else "What is your date of birth? Please send it in YYYY-MM-DD format."
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
            lines.append(f"Single: {single} available")

        double_total = self._as_int(selected_trip.get("available_double"))
        double_boys = self._as_int(selected_trip.get("boys_double"))
        double_girls = self._as_int(selected_trip.get("girls_double"))
        if double_boys or double_girls:
            if double_boys and room_group != "girls":
                lines.append(f"Double boys: {double_boys} available")
            if double_girls and room_group != "boys":
                lines.append(f"Double girls: {double_girls} available")
        elif double_total > 0:
            lines.append(f"Double: {double_total} available")

        triple_total = self._as_int(selected_trip.get("available_triple"))
        triple_boys = self._as_int(selected_trip.get("boys_triple"))
        triple_girls = self._as_int(selected_trip.get("girls_triple"))
        if triple_boys or triple_girls:
            if triple_boys and room_group != "girls":
                lines.append(f"Triple boys: {triple_boys} available")
            if triple_girls and room_group != "boys":
                lines.append(f"Triple girls: {triple_girls} available")
        elif triple_total > 0:
            lines.append(f"Triple: {triple_total} available")

        if not lines:
            if room_group in {"boys", "girls"}:
                return (
                    "لا توجد غرف متاحة لهذا الاختيار حالياً حسب بيانات CRM.\n"
                    "سأرسل طلبك إلى فريق رحمة ترافل لمراجعة البدائل."
                    if arabic
                    else "CRM shows no room availability for this traveler group right now.\n"
                    "I will send your request to the Rahma Traveler team to review the alternatives."
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
                room_name, available = line.rsplit(": ", 1)
                formatted_lines.append(f"{len(formatted_lines) + 1}) {labels.get(room_name, room_name)}: {available}")
            return (
                "\u0627\u062e\u062a\u0631 \u0646\u0648\u0639 \u0627\u0644\u063a\u0631\u0641\u0629 \u0627\u0644\u0645\u0641\u0636\u0644:\n\n"
                "\u0627\u0644\u062a\u0648\u0641\u0631 \u0627\u0644\u062d\u0627\u0644\u064a \u0645\u0646 CRM:\n"
                + "\n".join(formatted_lines)
                + "\n\n"
                "\u0627\u0643\u062a\u0628 \u0627\u0644\u062e\u064a\u0627\u0631 \u0627\u0644\u0645\u0646\u0627\u0633\u0628 \u0623\u0648 \u0631\u0642\u0645\u0647."
            )

        formatted_lines = [f"{index}) {line}" for index, line in enumerate(lines, 1)]
        return (
            "Please choose your preferred room option:\n\n"
            "Current availability from CRM:\n"
            + "\n".join(formatted_lines)
            + "\n\nReply with the room name or its number."
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

    def block_tool_result(self, tool_name: str, decision: WorkflowDecision) -> dict[str, Any]:
        return {
            "status": "workflow_blocked",
            "tool": tool_name,
            "required_step": decision.required_step or "collect_whatsapp_number",
            "customer_message_key": decision.customer_message_key or "identity_required",
            "assistant_message": decision.assistant_message
            or "Please share your WhatsApp number first so I can check your Rahma Traveler profile safely.",
            "workflow_state": decision.state,
            "executed": False,
        }
