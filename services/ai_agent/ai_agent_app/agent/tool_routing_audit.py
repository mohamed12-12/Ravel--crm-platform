"""A stricter, state-machine-shaped alternative to workflow_policy.py's
allowed-tools gate: maps session context to a CanonicalAgentState, then
`evaluate_tool_route()` decides whether a requested tool belongs in that
state. `ToolRouterMode` (OFF/DRY_RUN/ENFORCE, set via
AGENT_TOOL_ROUTER_MODE) controls whether a bad route is only logged
(DRY_RUN -- the default safe-demo posture, see
services/ai_agent/TOOL_INVENTORY_AND_CONTRACTS.md) or actually blocked
(ENFORCE) -- `should_enforce_tool_route()` is what gemini_agent.py checks
before deciding to block versus just audit-log a call.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CanonicalAgentState(str, Enum):
    IDENTITY_REQUIRED = "identity_required"
    NEW_TRAVELER_PROFILE_REQUIRED = "new_traveler_profile_required"
    TRAVELER_VERIFIED = "traveler_verified"
    TRIP_DISCOVERY = "trip_discovery"
    TRIP_SELECTED = "trip_selected"
    BOOKING_DRAFT = "booking_draft"
    BOOKING_CONFIRMATION_REQUIRED = "booking_confirmation_required"
    BOOKING_CREATED = "booking_created"
    HANDOFF_PENDING = "handoff_pending"
    HUMAN_REVIEW = "human_review"
    POST_BOOKING_SUPPORT = "post_booking_support"


class ToolGroup(str, Enum):
    IDENTITY_READ = "identity_read"
    TRAVELER_READ = "traveler_read"
    TRIP_READ = "trip_read"
    LEAD_WRITE = "lead_write"
    BOOKING_READ = "booking_read"
    BOOKING_WRITE = "booking_write"
    HANDOFF_WRITE = "handoff_write"
    PASSPORT_READ = "passport_read"
    PASSPORT_WRITE = "passport_write"
    VALIDATION = "validation"
    UNKNOWN = "unknown"


class ToolRouterMode(str, Enum):
    OFF = "off"
    DRY_RUN = "dry_run"
    ENFORCE = "enforce"


WRITE_TOOL_GROUPS = {
    ToolGroup.LEAD_WRITE,
    ToolGroup.BOOKING_WRITE,
    ToolGroup.HANDOFF_WRITE,
    ToolGroup.PASSPORT_WRITE,
}


TOOL_GROUP_BY_NAME: dict[str, ToolGroup] = {
    "search_traveler": ToolGroup.IDENTITY_READ,
    "find_traveler_by_phone": ToolGroup.IDENTITY_READ,
    "get_traveler_profile": ToolGroup.TRAVELER_READ,
    "get_traveler_trip_history": ToolGroup.TRAVELER_READ,
    "lookup_lead": ToolGroup.TRAVELER_READ,
    "search_trips": ToolGroup.TRIP_READ,
    "search_available_trips": ToolGroup.TRIP_READ,
    "get_trip_details": ToolGroup.TRIP_READ,
    "get_trip_media": ToolGroup.TRIP_READ,
    "get_booking_status": ToolGroup.BOOKING_READ,
    "get_passport_status": ToolGroup.PASSPORT_READ,
    "validate_business_action": ToolGroup.VALIDATION,
    "create_lead": ToolGroup.LEAD_WRITE,
    "update_lead_stage": ToolGroup.LEAD_WRITE,
    "create_booking": ToolGroup.BOOKING_WRITE,
    "create_booking_draft": ToolGroup.BOOKING_WRITE,
    "create_handoff": ToolGroup.HANDOFF_WRITE,
    "create_handoff_request": ToolGroup.HANDOFF_WRITE,
    "save_passport_document": ToolGroup.PASSPORT_WRITE,
    "upload_passport": ToolGroup.PASSPORT_WRITE,
}


LATEST_TO_CANONICAL_STATE: dict[str, CanonicalAgentState] = {
    "starting": CanonicalAgentState.IDENTITY_REQUIRED,
    "identity_required": CanonicalAgentState.IDENTITY_REQUIRED,
    "awaiting_phone": CanonicalAgentState.IDENTITY_REQUIRED,
    "identity_lookup_pending": CanonicalAgentState.IDENTITY_REQUIRED,
    "checking_crm": CanonicalAgentState.IDENTITY_REQUIRED,
    "traveler_found": CanonicalAgentState.TRAVELER_VERIFIED,
    "traveler_verified": CanonicalAgentState.TRAVELER_VERIFIED,
    "trip_type_required": CanonicalAgentState.TRIP_DISCOVERY,
    "trip_discovery": CanonicalAgentState.TRIP_DISCOVERY,
    "trip_search_ready": CanonicalAgentState.TRIP_DISCOVERY,
    "trip_results_available": CanonicalAgentState.TRIP_DISCOVERY,
    "trip_selection_required": CanonicalAgentState.TRIP_DISCOVERY,
    "no_trip_match": CanonicalAgentState.TRIP_DISCOVERY,
    "public_trip_details": CanonicalAgentState.TRIP_DISCOVERY,
    "trip_selected": CanonicalAgentState.TRIP_SELECTED,
    "trip_media_shared": CanonicalAgentState.TRIP_SELECTED,
    "traveler_gender_required": CanonicalAgentState.TRIP_SELECTED,
    "room_type_required": CanonicalAgentState.BOOKING_DRAFT,
    "group_size_required": CanonicalAgentState.BOOKING_DRAFT,
    "flight_option_required": CanonicalAgentState.BOOKING_DRAFT,
    "awaiting_passport_upload": CanonicalAgentState.BOOKING_DRAFT,
    "booking_ready": CanonicalAgentState.BOOKING_CONFIRMATION_REQUIRED,
    "booking_confirmation_required": CanonicalAgentState.BOOKING_CONFIRMATION_REQUIRED,
    "booking_created": CanonicalAgentState.BOOKING_CREATED,
    "completed": CanonicalAgentState.BOOKING_CREATED,
    "handoff_pending": CanonicalAgentState.HANDOFF_PENDING,
    "handoff_created": CanonicalAgentState.HANDOFF_PENDING,
    "handed_off": CanonicalAgentState.HANDOFF_PENDING,
    "human_handoff_required": CanonicalAgentState.HANDOFF_PENDING,
    "duplicate_traveler_detected": CanonicalAgentState.HUMAN_REVIEW,
    "capacity_handoff_required": CanonicalAgentState.HUMAN_REVIEW,
    "human_review": CanonicalAgentState.HUMAN_REVIEW,
    "traveler_not_found": CanonicalAgentState.NEW_TRAVELER_PROFILE_REQUIRED,
    "nationality_required": CanonicalAgentState.NEW_TRAVELER_PROFILE_REQUIRED,
    "birthday_required": CanonicalAgentState.NEW_TRAVELER_PROFILE_REQUIRED,
    "currency_required": CanonicalAgentState.NEW_TRAVELER_PROFILE_REQUIRED,
    "post_booking_support": CanonicalAgentState.POST_BOOKING_SUPPORT,
    "new_booking_intent": CanonicalAgentState.POST_BOOKING_SUPPORT,
}


ALLOWED_GROUPS_BY_STATE: dict[CanonicalAgentState, set[ToolGroup]] = {
    CanonicalAgentState.IDENTITY_REQUIRED: {ToolGroup.IDENTITY_READ},
    CanonicalAgentState.NEW_TRAVELER_PROFILE_REQUIRED: {
        ToolGroup.IDENTITY_READ,
        ToolGroup.LEAD_WRITE,
        ToolGroup.VALIDATION,
    },
    CanonicalAgentState.TRAVELER_VERIFIED: {
        ToolGroup.IDENTITY_READ,
        ToolGroup.TRAVELER_READ,
        ToolGroup.TRIP_READ,
        ToolGroup.LEAD_WRITE,
        ToolGroup.VALIDATION,
    },
    CanonicalAgentState.TRIP_DISCOVERY: {
        ToolGroup.IDENTITY_READ,
        ToolGroup.TRAVELER_READ,
        ToolGroup.TRIP_READ,
        ToolGroup.LEAD_WRITE,
        ToolGroup.VALIDATION,
    },
    CanonicalAgentState.TRIP_SELECTED: {
        ToolGroup.IDENTITY_READ,
        ToolGroup.TRAVELER_READ,
        ToolGroup.TRIP_READ,
        ToolGroup.PASSPORT_READ,
        ToolGroup.LEAD_WRITE,
        ToolGroup.VALIDATION,
    },
    CanonicalAgentState.BOOKING_DRAFT: {
        ToolGroup.TRAVELER_READ,
        ToolGroup.TRIP_READ,
        ToolGroup.BOOKING_READ,
        ToolGroup.PASSPORT_READ,
        ToolGroup.LEAD_WRITE,
        ToolGroup.VALIDATION,
    },
    CanonicalAgentState.BOOKING_CONFIRMATION_REQUIRED: {
        ToolGroup.TRAVELER_READ,
        ToolGroup.TRIP_READ,
        ToolGroup.BOOKING_READ,
        ToolGroup.BOOKING_WRITE,
        ToolGroup.PASSPORT_READ,
        ToolGroup.LEAD_WRITE,
        ToolGroup.VALIDATION,
    },
    CanonicalAgentState.BOOKING_CREATED: {
        ToolGroup.TRAVELER_READ,
        ToolGroup.TRIP_READ,
        ToolGroup.BOOKING_READ,
        ToolGroup.HANDOFF_WRITE,
    },
    CanonicalAgentState.HANDOFF_PENDING: {ToolGroup.HANDOFF_WRITE, ToolGroup.BOOKING_READ, ToolGroup.TRAVELER_READ},
    CanonicalAgentState.HUMAN_REVIEW: {
        ToolGroup.IDENTITY_READ,
        ToolGroup.TRAVELER_READ,
        ToolGroup.TRIP_READ,
        ToolGroup.BOOKING_READ,
        ToolGroup.PASSPORT_READ,
        ToolGroup.VALIDATION,
    },
    CanonicalAgentState.POST_BOOKING_SUPPORT: {
        ToolGroup.TRAVELER_READ,
        ToolGroup.TRIP_READ,
        ToolGroup.BOOKING_READ,
        ToolGroup.HANDOFF_WRITE,
    },
}


SAFE_CUSTOMER_MESSAGE_BY_REASON = {
    "router_off": "continue_normally",
    "allowed_by_state": "continue_normally",
    "allowed_controlled_handoff_review": "continue_normally",
    "allowed_explicit_handoff_existing_lead": "continue_normally",
    "unknown_tool": "request_not_available",
    "tool_group_not_allowed_for_state": "request_not_ready",
}


CONTROLLED_HANDOFF_REASONS = {
    "duplicate_phone",
    "duplicate_phone_match",
    "capacity_review",
    "room_capacity",
    "unsupported_request",
    "explicit_human_request",
    "customer_requested_human",
    "restricted_traveler_status",
    "restricted_status",
    "manual_review",
}

CONTROLLED_HANDOFF_STATES = {
    "duplicate_traveler_detected",
    "capacity_handoff_required",
    "human_handoff_required",
}


@dataclass(frozen=True)
class ToolRouteDecision:
    requested_tool: str
    state: str
    tool_group: str
    allowed: bool
    would_block: bool
    reason_code: str
    safe_customer_message_key: str
    mode: str
    audit: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_tool": self.requested_tool,
            "state": self.state,
            "tool_group": self.tool_group,
            "allowed": self.allowed,
            "would_block": self.would_block,
            "reason_code": self.reason_code,
            "safe_customer_message_key": self.safe_customer_message_key,
            "mode": self.mode,
            "audit": dict(self.audit),
        }


def normalize_router_mode(value: str | None) -> ToolRouterMode:
    normalized = str(value or "").strip().lower()
    if normalized in {mode.value for mode in ToolRouterMode}:
        return ToolRouterMode(normalized)
    return ToolRouterMode.DRY_RUN


def canonical_state_from_context(session_context: dict[str, Any] | None) -> CanonicalAgentState:
    context = session_context if isinstance(session_context, dict) else {}
    workflow = context.get("workflow_policy") if isinstance(context.get("workflow_policy"), dict) else {}
    workflow_state = str(workflow.get("state") or workflow.get("workflow_state") or "").strip()
    stage = str(context.get("stage") or context.get("workflow_stage") or "").strip()
    raw_state = workflow_state or stage
    normalized = raw_state.strip().lower()
    if normalized in LATEST_TO_CANONICAL_STATE:
        return LATEST_TO_CANONICAL_STATE[normalized]

    handoff_state = str(context.get("handoff_state") or "").strip().lower()
    if handoff_state in {"handed_off", "handoff_created", "handoff_pending", "assigned"}:
        return CanonicalAgentState.HANDOFF_PENDING

    if context.get("booking_id"):
        return CanonicalAgentState.BOOKING_CREATED
    if context.get("booking_confirmation_requested") or context.get("booking_confirmed"):
        return CanonicalAgentState.BOOKING_CONFIRMATION_REQUIRED
    if context.get("selected_trip_id"):
        collection_state = context.get("collection_state") if isinstance(context.get("collection_state"), dict) else {}
        if (
            collection_state.get("room_group")
            or collection_state.get("room_type")
            or collection_state.get("group_size")
            or context.get("room_type")
            or context.get("group_size")
        ):
            return CanonicalAgentState.BOOKING_DRAFT
        return CanonicalAgentState.TRIP_SELECTED
    if context.get("trip_type"):
        return CanonicalAgentState.TRIP_DISCOVERY
    if context.get("traveler_id") or isinstance(context.get("known_traveler"), dict) and context.get("known_traveler"):
        return CanonicalAgentState.TRAVELER_VERIFIED
    return CanonicalAgentState.IDENTITY_REQUIRED



def _controlled_handoff_precondition(session_context: dict[str, Any] | None) -> bool:
    context = session_context if isinstance(session_context, dict) else {}
    workflow = context.get("workflow_policy") if isinstance(context.get("workflow_policy"), dict) else {}
    workflow_state = str(workflow.get("state") or workflow.get("workflow_state") or context.get("stage") or "").strip().lower()
    if workflow_state in CONTROLLED_HANDOFF_STATES:
        return True
    if _explicit_human_request_signal(session_context):
        return True
    reason_values = {
        str(workflow.get("reason") or ""),
        str(workflow.get("required_step") or ""),
        str(workflow.get("handoff_reason") or ""),
        str(context.get("reason_code") or ""),
        str(context.get("handoff_reason") or ""),
        str(context.get("handoff_reason_code") or ""),
    }
    normalized_reasons = {value.strip().lower().split(":", 1)[0] for value in reason_values if value and value.strip()}
    if normalized_reasons & CONTROLLED_HANDOFF_REASONS:
        return True
    return bool(workflow.get("handoff_required")) and bool(normalized_reasons & CONTROLLED_HANDOFF_REASONS)


def _explicit_human_request_signal(session_context: dict[str, Any] | None) -> bool:
    """True only for a deterministic, pre-LLM "customer asked for a human" signal.

    This must never be derived from the tool call the model itself chose to make
    (e.g. args on the create_handoff function call) -- that would let the model
    grant itself the exception it's supposed to be gated behind. It reads only
    session-level context set before the model ran.
    """
    context = session_context if isinstance(session_context, dict) else {}
    workflow = context.get("workflow_policy") if isinstance(context.get("workflow_policy"), dict) else {}
    if context.get("user_requested_human") or workflow.get("user_requested_human"):
        return True
    reason_values = {
        str(workflow.get("reason") or ""),
        str(workflow.get("handoff_reason") or ""),
        str(context.get("reason_code") or ""),
        str(context.get("handoff_reason") or ""),
        str(context.get("handoff_reason_code") or ""),
    }
    normalized_reasons = {value.strip().lower().split(":", 1)[0] for value in reason_values if value and value.strip()}
    return bool(normalized_reasons & {"explicit_human_request", "customer_requested_human"})


def _has_existing_lead(session_context: dict[str, Any] | None) -> bool:
    context = session_context if isinstance(session_context, dict) else {}
    if str(context.get("lead_id") or "").strip():
        return True
    workflow = context.get("workflow_policy") if isinstance(context.get("workflow_policy"), dict) else {}
    return bool(str(workflow.get("lead_id") or "").strip())


def tool_group_for_name(tool_name: str) -> ToolGroup:
    return TOOL_GROUP_BY_NAME.get(str(tool_name or "").strip(), ToolGroup.UNKNOWN)


def evaluate_tool_route(
    *,
    current_state: CanonicalAgentState | str | None = None,
    requested_tool_name: str,
    session_context: dict[str, Any] | None = None,
    mode: str | ToolRouterMode | None = None,
) -> ToolRouteDecision:
    router_mode = mode if isinstance(mode, ToolRouterMode) else normalize_router_mode(str(mode or "dry_run"))
    state = (
        current_state
        if isinstance(current_state, CanonicalAgentState)
        else LATEST_TO_CANONICAL_STATE.get(str(current_state or "").strip().lower())
    )
    if state is None:
        state = canonical_state_from_context(session_context)

    tool_group = tool_group_for_name(requested_tool_name)
    allowed_groups = ALLOWED_GROUPS_BY_STATE.get(state, set())
    controlled_handoff_allowed = (
        state == CanonicalAgentState.HUMAN_REVIEW
        and tool_group == ToolGroup.HANDOFF_WRITE
        and _controlled_handoff_precondition(session_context)
    )
    # Narrow exception: a returning traveler with an existing open lead who has
    # explicitly asked for a human (a deterministic pre-model signal, not the
    # model's own tool-call args) may reach create_handoff from any state --
    # otherwise a returning customer asking for support gets stuck behind
    # whatever pre-booking state they happen to be in. Spontaneous/unprompted
    # handoff attempts (no explicit signal, or no existing lead) are still
    # blocked exactly as before.
    existing_lead_explicit_handoff_allowed = (
        tool_group == ToolGroup.HANDOFF_WRITE
        and state != CanonicalAgentState.IDENTITY_REQUIRED
        and _has_existing_lead(session_context)
        and _explicit_human_request_signal(session_context)
    )
    allowed = tool_group in allowed_groups or controlled_handoff_allowed or existing_lead_explicit_handoff_allowed
    if router_mode == ToolRouterMode.OFF:
        allowed = True
        reason_code = "router_off"
    elif tool_group == ToolGroup.UNKNOWN:
        reason_code = "unknown_tool"
    elif controlled_handoff_allowed:
        reason_code = "allowed_controlled_handoff_review"
    elif existing_lead_explicit_handoff_allowed:
        reason_code = "allowed_explicit_handoff_existing_lead"
    elif allowed:
        reason_code = "allowed_by_state"
    else:
        reason_code = "tool_group_not_allowed_for_state"
    would_block = router_mode != ToolRouterMode.OFF and reason_code not in {
        "allowed_by_state",
        "allowed_controlled_handoff_review",
        "allowed_explicit_handoff_existing_lead",
        "router_off",
    }
    return ToolRouteDecision(
        requested_tool=str(requested_tool_name or ""),
        state=state.value,
        tool_group=tool_group.value,
        allowed=allowed,
        would_block=would_block,
        reason_code=reason_code,
        safe_customer_message_key=SAFE_CUSTOMER_MESSAGE_BY_REASON.get(reason_code, "request_not_ready"),
        mode=router_mode.value,
        audit={
            "allowed_tool_groups": sorted(group.value for group in allowed_groups),
            "controlled_handoff_allowed": controlled_handoff_allowed,
            "existing_lead_explicit_handoff_allowed": existing_lead_explicit_handoff_allowed,
            "write_tool_group": tool_group in WRITE_TOOL_GROUPS,
        },
    )


def should_enforce_tool_route(decision: ToolRouteDecision) -> bool:
    """Port 4 keeps read tools compatible; only write groups may be blocked in enforce mode."""

    if decision.mode != ToolRouterMode.ENFORCE.value or not decision.would_block:
        return False
    return ToolGroup(decision.tool_group) in WRITE_TOOL_GROUPS if decision.tool_group in {group.value for group in ToolGroup} else False
