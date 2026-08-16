"""Computes what stage of the sales workflow a session is actually in
(identity/trip-search/booking/etc, via WorkflowDecision) and which tools
are allowed from there -- the gate that keeps the agent from, say,
creating a booking before a traveler is verified. Read this alongside
tool_registry.py's per-tool `allowed_states` (services/ai_agent/TOOL_INVENTORY_AND_CONTRACTS.md
has the human-readable version of that mapping).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.ai_agent.ai_agent_app.agent.date_parsing import compute_age
from services.crm.system_services.trip_pricing import price_for_room_and_currency


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
        # Every WorkflowDecision.assistant_message below is rendered VERBATIM
        # to the customer on several code paths (_ground_reply,
        # _natural_interruption_fallback, _backend_required_step_reply) --
        # none of them translate it. These were English-only, so an
        # all-Arabic conversation could suddenly get one pure-English reply
        # the moment a turn landed on one of these branches (e.g. a price
        # question asked before identity was verified).
        arabic = self._is_arabic(session_context)

        if lookup_status == "duplicate":
            return WorkflowDecision(
                state="duplicate_traveler_detected",
                customer_status="Human review required",
                allowed_tools=IDENTITY_TOOLS,
                required_step="human_review",
                customer_message_key="duplicate_traveler_review",
                assistant_message=(
                    "رقم الواتساب ده مرتبط بأكتر من ملف مسافر، فمحتاجين موظف من الفريق يراجعه قبل ما نكمل."
                    if arabic
                    else "This WhatsApp number matches more than one traveler profile, so a team member needs to review it before we continue."
                ),
                handoff_required=True,
                reason="duplicate_phone_match",
            )

        if lookup_status == "not_found":
            # A saved new-traveler lead means the write layer already created (or
            # matched) a Traveler record for this phone number. Re-running the
            # new-traveler intake branch after that point returns
            # save_new_traveler_lead forever, which has no work left to do, so
            # the conversation can never reach trip selection or booking. Treat
            # the linked traveler as verified and continue the real workflow.
            saved_traveler = self._saved_new_traveler(session_context)
            if saved_traveler:
                return self._post_identity_decision(
                    session_context,
                    traveler=saved_traveler,
                    status=str(saved_traveler.get("status") or "Active"),
                )
            return self._new_traveler_decision(session_context)

        if lookup_status == "invalid_phone":
            return WorkflowDecision(
                state="identity_required",
                customer_status="Waiting for WhatsApp number",
                allowed_tools=IDENTITY_TOOLS,
                required_step="collect_valid_whatsapp_number",
                customer_message_key="invalid_phone",
                assistant_message=(
                    "من فضلك ابعت رقم واتساب صحيح. لو الرقم خارج مصر، اكتب كود الدولة معاه، مثال: +966512345678."
                    if arabic
                    else "Please send a valid WhatsApp mobile number. If it is outside Egypt, include the country code, for example +966512345678."
                ),
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
                    assistant_message=(
                        "لقيت ملفك كمسافر، بس الطلب ده محتاج مراجعة من فريق Ravel Traveler قبل ما نكمل."
                        if arabic
                        else "I found your traveler profile, but this request needs review by the Ravel Traveler team before we continue."
                    ),
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
                assistant_message=(
                    "هراجع رقم الواتساب ده الأول، وبعدين نكمل طلب رحلتك."
                    if arabic
                    else "I will check this WhatsApp number first, then continue with your trip request."
                ),
                reason="phone_collected",
            )

        return WorkflowDecision(
            state="identity_required",
            customer_status="Waiting for WhatsApp number",
            allowed_tools=IDENTITY_TOOLS,
            required_step="collect_whatsapp_number",
            customer_message_key="identity_required",
            assistant_message=(
                "من فضلك شاركني رقم الواتساب الخاص بك الأول عشان أقدر أراجع ملفك في Ravel Traveler بأمان."
                if arabic
                else "Please share your WhatsApp number first so I can check your Ravel Traveler profile safely."
            ),
            reason="missing_identity",
        )

    @staticmethod
    def _saved_new_traveler(session_context: dict[str, Any]) -> dict[str, Any]:
        """Return the traveler record linked to an already-saved new-traveler lead."""

        if not session_context.get("new_traveler_lead_saved"):
            return {}
        known = session_context.get("known_traveler") if isinstance(session_context.get("known_traveler"), dict) else {}
        traveler_id = str(known.get("traveler_id") or session_context.get("traveler_id") or "").strip()
        if not traveler_id:
            return {}
        traveler = dict(known) if known.get("traveler_id") else {"traveler_id": traveler_id}
        traveler.setdefault("full_name", str(session_context.get("customer_name") or ""))
        if not str(traveler.get("status") or "").strip():
            traveler["status"] = "Active"
        return traveler

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
        group_nationality_type = str(session_context.get("group_nationality_type") or "").strip().lower()
        group_nationality_counts = session_context.get("group_nationality_counts") if isinstance(session_context.get("group_nationality_counts"), dict) else {}
        flights_supported = self._trip_supports_flights(selected_trip)
        flight_option_collected = bool(collection_state.get("flight_option")) or not flights_supported
        currency = str(session_context.get("currency") or collection_state.get("currency_value") or "").strip()
        has_trip_results = bool(list(trip_result.get("open_trips") or []) or list(trip_result.get("date_tbd_trips") or []))
        arabic = self._is_arabic(session_context)

        common = {
            "identity_verified": True,
            "verified_traveler": dict(traveler),
            "verified_status": status,
            "reason": "crm_identity_verified",
        }

        open_lead_id = str(session_context.get("open_lead_id") or "").strip()
        duplicate_lead_choice = str(session_context.get("duplicate_lead_choice") or "").strip()
        if open_lead_id and duplicate_lead_choice not in {"continue", "new"}:
            return WorkflowDecision(
                state="duplicate_lead_choice_required",
                customer_status="Waiting for customer response",
                allowed_tools=PRE_TRIP_SEARCH_TOOLS,
                required_step="collect_duplicate_lead_choice",
                customer_message_key="duplicate_lead_choice_required",
                assistant_message=self._duplicate_lead_prompt(open_lead_id, arabic=arabic),
                **common,
            )

        guardian_decision = self._guardian_decision(
            session_context,
            common={**common, "allowed_tools": PRE_TRIP_SEARCH_TOOLS},
            arabic=arabic,
        )
        if guardian_decision is not None:
            return guardian_decision

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
            # A trip_result dict with both list keys present (even if both are
            # empty) means the search actually ran and came back empty -- that
            # must never look the same as "haven't searched yet", or a genuine
            # zero-result trip category reads to the customer as an ignored
            # request instead of an honest "nothing open right now".
            search_has_run = "open_trips" in trip_result or "date_tbd_trips" in trip_result
            if search_has_run:
                return WorkflowDecision(
                    state="no_trips_available",
                    customer_status="No matching trips",
                    allowed_tools=PRE_BOOKING_TOOLS,
                    required_step="handle_empty_trip_results",
                    customer_message_key="no_trips_available",
                    assistant_message=self._no_trips_available_message(trip_type, arabic=arabic),
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
                    "هل المسافرون شباب أم بنات؟\n\n"
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

        requested_group_size = self._as_int(session_context.get("group_size")) or 1
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

        if requested_group_size > 1 and group_nationality_type not in {"single", "mixed"}:
            return WorkflowDecision(
                state="group_nationality_type_required",
                customer_status="Waiting for customer response",
                allowed_tools=SELECTED_TRIP_TOOLS,
                required_step="collect_group_nationality_type",
                customer_message_key="group_nationality_type_required",
                assistant_message=self._group_nationality_type_prompt(arabic=arabic),
                **common,
            )

        if requested_group_size > 1 and group_nationality_type == "mixed" and not self._group_nationality_counts_complete(group_nationality_counts, requested_group_size):
            return WorkflowDecision(
                state="group_nationality_counts_required",
                customer_status="Waiting for customer response",
                allowed_tools=SELECTED_TRIP_TOOLS,
                required_step="collect_group_nationality_counts",
                customer_message_key="group_nationality_counts_required",
                assistant_message=self._group_nationality_counts_prompt(requested_group_size, arabic=arabic),
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

        if passport_required:
            passport_number = str(session_context.get("passport_number") or "").strip()
            passport_expiry = str(session_context.get("passport_expiry") or "").strip()
            passport_nationality = str(session_context.get("passport_nationality") or "").strip()
            if not passport_number:
                return WorkflowDecision(
                    state="passport_number_required",
                    customer_status="Waiting for customer response",
                    allowed_tools=SELECTED_TRIP_TOOLS,
                    required_step="collect_passport_number",
                    customer_message_key="passport_number_required",
                    assistant_message=self._passport_number_prompt(arabic=arabic),
                    **common,
                )
            if not passport_expiry:
                return WorkflowDecision(
                    state="passport_expiry_required",
                    customer_status="Waiting for customer response",
                    allowed_tools=SELECTED_TRIP_TOOLS,
                    required_step="collect_passport_expiry",
                    customer_message_key="passport_expiry_required",
                    assistant_message=self._passport_expiry_prompt(arabic=arabic),
                    **common,
                )
            if not passport_nationality:
                return WorkflowDecision(
                    state="passport_country_required",
                    customer_status="Waiting for customer response",
                    allowed_tools=SELECTED_TRIP_TOOLS,
                    required_step="collect_passport_country",
                    customer_message_key="passport_country_required",
                    assistant_message=self._passport_country_prompt(arabic=arabic),
                    **common,
                )

        if not currency:
            return WorkflowDecision(
                state="currency_required",
                customer_status="Waiting for customer response",
                allowed_tools=SELECTED_TRIP_TOOLS,
                required_step="collect_payment_currency",
                customer_message_key="currency_required",
                assistant_message=self._final_currency_prompt(
                    session_context,
                    selected_trip=selected_trip,
                    arabic=arabic,
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

    @staticmethod
    def _no_trips_available_message(trip_type: str, *, arabic: bool = False) -> str:
        """Note: this is deliberately not a promise to proactively notify the
        customer -- no such outbound-notification pipeline exists anywhere in
        this codebase (services/notifications is an unimplemented placeholder,
        and Lead.waitlist_id is written by spreadsheet import but never read by
        anything). ToolCallingSessionRuntime._escalate_no_matching_trip creates
        a real handoff instead, and this text is overridden by that method's
        own honest reply the first time this state is reached -- this string
        only remains as the fallback for any other path that renders
        WorkflowDecision.assistant_message directly.
        """
        normalized_type = str(trip_type or "").strip().lower()
        if arabic:
            label = "محلية" if normalized_type == "local" else "دولية"
            return (
                f"للأسف مفيش رحلات {label} متاحة (مفتوحة) دلوقتي. "
                "قيدت طلبك عشان فريق Ravel يراجعه ويتواصل معاك لو توفرت رحلة مناسبة."
            )
        label = normalized_type or "matching"
        return (
            f"There are no {label} trips open right now. "
            "I've logged your request so the Ravel team can review it and reach out if a suitable trip opens up."
        )

    @staticmethod
    def _duplicate_lead_prompt(open_lead_id: str, *, arabic: bool = False) -> str:
        if arabic:
            return (
                f"عندك طلب سابق لسه شغال برقم {open_lead_id}.\n"
                "تحب نكمل على نفس الطلب، ولا نبدأ طلب جديد؟\n\n"
                "1. أكمل الطلب الحالي\n"
                "2. ابدأ طلب جديد"
            )
        return (
            f"You already have an open request on file, {open_lead_id}.\n"
            "Would you like to continue with that request, or start a new one?\n\n"
            "1. Continue the existing request\n"
            "2. Start a new request"
        )

    @staticmethod
    def _passport_number_prompt(*, arabic: bool = False) -> str:
        return (
            "ما رقم جواز السفر؟"
            if arabic
            else "What is the passport number?"
        )

    @staticmethod
    def _passport_expiry_prompt(*, arabic: bool = False) -> str:
        return (
            "متى تنتهي صلاحية جواز السفر؟\n"
            "اكتبها بأي صيغة واضحة، مثل 21/08/2030."
            if arabic
            else "When does the passport expire? Type it in any clear format, for example 21/08/2030."
        )

    @staticmethod
    def _passport_country_prompt(*, arabic: bool = False) -> str:
        return (
            "ما هي جنسية جواز السفر (الدولة المصدرة)؟"
            if arabic
            else "What is the issuing country/nationality on the passport?"
        )

    def _new_traveler_decision(self, session_context: dict[str, Any]) -> WorkflowDecision:
        collection_state = session_context.get("collection_state") if isinstance(session_context.get("collection_state"), dict) else {}
        arabic = self._is_arabic(session_context)
        customer_name = str(session_context.get("customer_name") or "").strip()
        nationality = str(session_context.get("nationality") or collection_state.get("nationality_value") or "").strip()
        birthday = str(session_context.get("birthday") or collection_state.get("birthday_value") or "").strip()

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

        guardian_decision = self._guardian_decision(session_context, common=common, arabic=arabic)
        if guardian_decision is not None:
            return guardian_decision

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
    def _group_nationality_type_prompt(*, arabic: bool = False) -> str:
        return (
            "هل كل المسافرين نفس الجنسية/نفس فئة السعر، أم المجموعة مختلطة بين مصريين وأجانب؟\n\n1. نفس الفئة\n2. مختلطة"
            if arabic
            else "Is everyone in the group the same nationality/pricing group, or is it mixed between Egyptians and foreigners?\n\n1. Same group\n2. Mixed group"
        )

    @staticmethod
    def _group_nationality_counts_prompt(group_size: int, *, arabic: bool = False) -> str:
        return (
            f"من فضلك اكتب عدد المصريين وعدد الأجانب من إجمالي {group_size} مسافرين، مثل: 2 مصري و1 أجنبي."
            if arabic
            else f"Please send how many Egyptians and how many foreigners are in the {group_size}-traveler group, for example: 2 Egyptians and 1 foreigner."
        )

    @staticmethod
    def _group_nationality_counts_complete(counts: dict[str, Any], group_size: int) -> bool:
        try:
            egyptians = int(counts.get("egyptian") or 0)
            foreigners = int(counts.get("foreigner") or 0)
        except (TypeError, ValueError):
            return False
        return egyptians >= 0 and foreigners >= 0 and (egyptians + foreigners) == int(group_size or 0)

    @staticmethod
    def _parse_money(value: str) -> float:
        text = str(value or "").strip()
        if not text:
            return 0.0
        cleaned = "".join(ch for ch in text if ch.isdigit() or ch in ".-")
        try:
            return float(cleaned) if cleaned else 0.0
        except ValueError:
            return 0.0

    @staticmethod
    def _format_money(amount: float, currency: str) -> str:
        prefix = "$" if currency == "USD" else ""
        suffix = "" if currency == "USD" else f" {currency}"
        return f"{prefix}{amount:,.0f}{suffix}"

    def _pricing_counts_for_context(self, session_context: dict[str, Any]) -> dict[str, int]:
        group_size = self._as_int(session_context.get("group_size")) or 1
        group_type = str(session_context.get("group_nationality_type") or "").strip().lower()
        raw_counts = session_context.get("group_nationality_counts") if isinstance(session_context.get("group_nationality_counts"), dict) else {}
        if group_type == "mixed" and self._group_nationality_counts_complete(raw_counts, group_size):
            return {
                "egyptian": int(raw_counts.get("egyptian") or 0),
                "foreigner": int(raw_counts.get("foreigner") or 0),
            }
        nationality = str(session_context.get("nationality") or "").strip().lower()
        traveler = session_context.get("known_traveler") if isinstance(session_context.get("known_traveler"), dict) else {}
        if not nationality:
            nationality = str(traveler.get("nationality") or "").strip().lower()
        is_egyptian = any(token in nationality for token in ("egypt", "مصر"))
        return {"egyptian": group_size if is_egyptian else 0, "foreigner": 0 if is_egyptian else group_size}

    def _pricing_breakdown_text(self, session_context: dict[str, Any], selected_trip: dict[str, Any], *, arabic: bool = False) -> str:
        room_type = str(session_context.get("room_type") or "").strip()
        if not selected_trip or not room_type:
            return ""
        counts = self._pricing_counts_for_context(session_context)
        egp_price = price_for_room_and_currency(selected_trip, room_type=room_type, currency="EGP", fallback_public_price=False)
        usd_price = price_for_room_and_currency(selected_trip, room_type=room_type, currency="USD", fallback_public_price=False)
        egp_total = self._parse_money(egp_price) * counts["egyptian"]
        usd_total = self._parse_money(usd_price) * counts["foreigner"]
        lines: list[str] = []
        if arabic:
            lines.append("تفصيل السعر المؤكد من CRM قبل اختيار عملة الدفع:")
            if counts["egyptian"]:
                lines.append(f"- المصريون: {counts['egyptian']} x {egp_price or 'غير مسجل'} EGP = {self._format_money(egp_total, 'EGP') if egp_price else 'غير مسجل'}")
            if counts["foreigner"]:
                lines.append(f"- الأجانب: {counts['foreigner']} x {usd_price or 'unlisted'} USD = {self._format_money(usd_total, 'USD') if usd_price else 'unlisted'}")
        else:
            lines.append("Verified CRM price breakdown before payment currency selection:")
            if counts["egyptian"]:
                lines.append(f"- Egyptians: {counts['egyptian']} x {egp_price or 'unlisted'} EGP = {self._format_money(egp_total, 'EGP') if egp_price else 'unlisted'}")
            if counts["foreigner"]:
                lines.append(f"- Foreigners: {counts['foreigner']} x {usd_price or 'unlisted'} USD = {self._format_money(usd_total, 'USD') if usd_price else 'unlisted'}")
        return "\n".join(lines)

    def _final_currency_prompt(self, session_context: dict[str, Any], *, selected_trip: dict[str, Any], arabic: bool = False) -> str:
        breakdown = self._pricing_breakdown_text(session_context, selected_trip, arabic=arabic)
        prompt = self._currency_prompt(arabic=arabic)
        return f"{breakdown}\n\n{prompt}" if breakdown else prompt

    @staticmethod
    def _is_minor_from_context(session_context: dict[str, Any]) -> bool:
        explicit = session_context.get("is_minor")
        if isinstance(explicit, bool):
            return explicit
        birthday = str(session_context.get("birthday") or "").strip()
        if not birthday:
            known_traveler = session_context.get("known_traveler") if isinstance(session_context.get("known_traveler"), dict) else {}
            birthday = str(known_traveler.get("birthday") or "").strip()
        age = compute_age(birthday)
        return age is not None and age < 18

    def _guardian_decision(self, session_context: dict[str, Any], *, common: dict[str, Any], arabic: bool) -> WorkflowDecision | None:
        if not self._is_minor_from_context(session_context):
            return None
        guardian_name = str(session_context.get("guardian_name") or "").strip()
        guardian_phone = str(session_context.get("guardian_phone") or "").strip()
        if not guardian_name:
            return WorkflowDecision(
                state="guardian_name_required",
                customer_status="Waiting for customer response",
                required_step="collect_guardian_name",
                customer_message_key="guardian_name_required",
                assistant_message=self._guardian_name_prompt(arabic=arabic),
                **common,
            )
        if not guardian_phone:
            return WorkflowDecision(
                state="guardian_phone_required",
                customer_status="Waiting for customer response",
                required_step="collect_guardian_phone",
                customer_message_key="guardian_phone_required",
                assistant_message=self._guardian_phone_prompt(arabic=arabic),
                **common,
            )
        return None

    @staticmethod
    def _guardian_name_prompt(*, arabic: bool = False) -> str:
        return (
            "بما أن المسافر أقل من 18 سنة، محتاج موافقة ولي الأمر.\n"
            "ما اسم ولي الأمر بالكامل؟"
            if arabic
            else "Since the traveler is under 18, I need a parent or guardian's consent.\nWhat is the guardian's full name?"
        )

    @staticmethod
    def _guardian_phone_prompt(*, arabic: bool = False) -> str:
        return (
            "ما رقم هاتف ولي الأمر، للتواصل معه لتأكيد الموافقة؟"
            if arabic
            else "What is the guardian's phone number, so we can confirm consent with them?"
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
                    "لا توجد غرف متاحة لهذا الاختيار حاليا حسب بياناتنا.\n"
                    "سأرسل طلبك إلى فريق Ravel Traveler لمراجعة البدائل المتاحة."
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
            capacity = self._gendered_room_capacity(selected_trip, room_type, group) or 0
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

    def _gender_split_tracked(self, selected_trip: dict[str, Any], room_type: str) -> bool:
        """True when this trip actually maintains boys/girls sub-inventory for room_type.

        UnifiedCRMService's trip projection always emits boys_double /
        girls_double / boys_triple / girls_triple, defaulting to 0 -- so a trip
        that simply does not split its doubles by gender was indistinguishable
        from one whose boys doubles are sold out. Reading the gendered key
        blindly therefore reported zero capacity and escalated to a human for
        bookings the trip could actually take (e.g. available_double = 6 while
        both gender keys are 0). Treat "both sides zero" as "not split" and let
        callers fall back to the ungendered pool.
        """
        room = str(room_type or "").strip().lower()
        boys = self._as_int(selected_trip.get(f"boys_{room}")) or 0
        girls = self._as_int(selected_trip.get(f"girls_{room}")) or 0
        return bool(boys or girls)

    def _gendered_room_capacity(self, selected_trip: dict[str, Any], room_type: str, room_group: str) -> int | None:
        room = str(room_type or "").strip().lower()
        group = str(room_group or "").strip().lower()
        if group in {"boys", "girls"} and self._gender_split_tracked(selected_trip, room):
            return self._as_int(selected_trip.get(f"{group}_{room}"))
        pool_key = f"available_{room}"
        if pool_key not in selected_trip or selected_trip.get(pool_key) is None:
            return None
        return self._as_int(selected_trip.get(pool_key))

    def _room_capacity(self, selected_trip: dict[str, Any], room_type: str, room_group: str) -> int | None:
        room_key = str(room_type or "").strip().lower()
        group_key = str(room_group or "").strip().lower()
        def available(key: str) -> int | None:
            if key not in selected_trip or selected_trip.get(key) is None:
                return None
            return self._as_int(selected_trip.get(key))

        if room_key == "single":
            return available("available_single")
        if room_key in {"double", "triple"}:
            return self._gendered_room_capacity(selected_trip, room_key, group_key)
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

