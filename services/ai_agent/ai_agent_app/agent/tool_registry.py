from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    allowed_write: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_prompt_block(self) -> str:
        return f"- {self.name}: {self.description}"


def build_read_only_tool_registry() -> dict[str, ToolSpec]:
    registry = {
        "search_traveler": ToolSpec(
            name="search_traveler",
            description="Search the CRM for traveler matches using WhatsApp or phone data.",
            input_schema={
                "type": "object",
                "properties": {
                    "raw_phone": {"type": "string"},
                    "country_code": {"type": "string"},
                },
                "required": ["raw_phone"],
            },
            output_schema={"type": "object"},
        ),
        "find_traveler_by_phone": ToolSpec(
            name="find_traveler_by_phone",
            description="Normalize a phone number, search the CRM, and return a safe traveler match result.",
            input_schema={
                "type": "object",
                "properties": {
                    "raw_phone": {"type": "string"},
                    "country_code": {"type": "string"},
                },
                "required": ["raw_phone"],
            },
            output_schema={"type": "object"},
            metadata={"workflow_prerequisites": ["valid_phone"]},
        ),
        "get_traveler_profile": ToolSpec(
            name="get_traveler_profile",
            description="Return a traveler profile by traveler_id or phone.",
            input_schema={
                "type": "object",
                "properties": {
                    "traveler_id": {"type": "string"},
                    "raw_phone": {"type": "string"},
                    "country_code": {"type": "string"},
                },
            },
            output_schema={"type": "object"},
            metadata={"workflow_prerequisites": ["traveler_verified"]},
        ),
        "get_traveler_trip_history": ToolSpec(
            name="get_traveler_trip_history",
            description="Return a safe summary of a traveler's trip history from CRM and booking data.",
            input_schema={
                "type": "object",
                "properties": {
                    "traveler_id": {"type": "string"},
                    "raw_phone": {"type": "string"},
                    "country_code": {"type": "string"},
                },
            },
            output_schema={"type": "object"},
            metadata={"workflow_prerequisites": ["traveler_verified"]},
        ),
        "search_trips": ToolSpec(
            name="search_trips",
            description="Search eligible trips by trip type and optional keyword.",
            input_schema={
                "type": "object",
                "properties": {
                    "trip_type": {"type": "string"},
                    "query": {"type": "string"},
                },
            },
            output_schema={"type": "object"},
        ),
        "search_available_trips": ToolSpec(
            name="search_available_trips",
            description="Search available trips with CRM rules and common trip filters.",
            input_schema={
                "type": "object",
                "properties": {
                    "trip_type": {"type": "string"},
                    "destination": {"type": "string"},
                    "query": {"type": "string"},
                    "preferred_date": {"type": "string"},
                    "travelers": {"type": "string"},
                    "flight_option": {"type": "string"},
                    "room_type": {"type": "string"},
                },
            },
            output_schema={"type": "object"},
            metadata={"workflow_prerequisites": ["traveler_verified"]},
        ),
        "get_trip_details": ToolSpec(
            name="get_trip_details",
            description="Return the CRM details for one trip.",
            input_schema={
                "type": "object",
                "properties": {"trip_id": {"type": "string"}},
                "required": ["trip_id"],
            },
            output_schema={"type": "object"},
            metadata={"workflow_prerequisites": ["traveler_verified"]},
        ),
        "get_trip_media": ToolSpec(
            name="get_trip_media",
            description="Return verified official CRM trip images for one selected trip. This tool is read-only and never uploads or modifies media.",
            input_schema={
                "type": "object",
                "properties": {"trip_id": {"type": "string"}},
                "required": ["trip_id"],
            },
            output_schema={"type": "object"},
            metadata={"workflow_prerequisites": ["traveler_verified", "selected_trip"]},
        ),
        "get_booking_status": ToolSpec(
            name="get_booking_status",
            description="Look up booking details by booking_id, traveler_id, or lead_id.",
            input_schema={
                "type": "object",
                "properties": {
                    "booking_id": {"type": "string"},
                    "traveler_id": {"type": "string"},
                    "lead_id": {"type": "string"},
                },
            },
            output_schema={"type": "object"},
        ),
        "get_passport_status": ToolSpec(
            name="get_passport_status",
            description="Return passport fields and attachment metadata for a traveler.",
            input_schema={
                "type": "object",
                "properties": {
                    "traveler_id": {"type": "string"},
                    "raw_phone": {"type": "string"},
                    "country_code": {"type": "string"},
                },
            },
            output_schema={"type": "object"},
        ),
        "lookup_lead": ToolSpec(
            name="lookup_lead",
            description="Look up one or more CRM leads by lead_id, traveler_id, or WhatsApp number.",
            input_schema={
                "type": "object",
                "properties": {
                    "lead_id": {"type": "string"},
                    "traveler_id": {"type": "string"},
                    "raw_phone": {"type": "string"},
                    "country_code": {"type": "string"},
                },
            },
            output_schema={"type": "object"},
        ),
    }
    return registry


def build_tool_calling_registry() -> dict[str, ToolSpec]:
    registry = build_read_only_tool_registry()
    allowed = {
        "find_traveler_by_phone",
        "get_traveler_profile",
        "get_traveler_trip_history",
        "search_available_trips",
        "get_trip_details",
        "get_trip_media",
    }
    return {name: spec for name, spec in registry.items() if name in allowed}


def build_agent_tool_registry(*, include_write_tools: bool = False, include_validation_tool: bool = True) -> dict[str, ToolSpec]:
    registry = build_read_only_tool_registry()
    if include_validation_tool:
        registry["validate_business_action"] = ToolSpec(
            name="validate_business_action",
            description=(
                "Validate whether a future CRM action would be allowed. "
                "This tool never writes to CRM and only returns APPROVED, REJECTED, or NEED_MORE_INFORMATION."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "create_traveler",
                            "update_traveler",
                            "create_lead",
                            "update_lead_stage",
                            "create_booking_draft",
                            "update_booking",
                            "upload_passport",
                            "create_handoff",
                        ],
                    },
                    "traveler_id": {"type": "string"},
                    "lead_id": {"type": "string"},
                    "booking_id": {"type": "string"},
                    "trip_id": {"type": "string"},
                    "raw_phone": {"type": "string"},
                    "country_code": {"type": "string"},
                    "full_name": {"type": "string"},
                    "room_type": {"type": "string"},
                    "requested_stage": {"type": "string"},
                    "payment_status": {"type": "string"},
                    "flight_option": {"type": "string"},
                    "currency": {"type": "string"},
                    "booking_notes": {"type": "string"},
                    "passport_attachment_ref": {"type": "string"},
                    "user_requested_human": {"type": "boolean"},
                    "ai_confidence": {"type": "number"},
                    "validation_failures": {"type": "integer"},
                    "repeated_validation_failures": {"type": "integer"},
                },
                "required": ["action"],
            },
            output_schema={"type": "object"},
        )
    if include_write_tools:
        from services.ai_agent.ai_agent_app.agent.write_tool_registry import build_write_tool_registry

        registry.update(build_write_tool_registry())
    return registry


def render_tool_list(*, include_write_tools: bool = False) -> str:
    return "\n".join(spec.as_prompt_block() for spec in build_agent_tool_registry(include_write_tools=include_write_tools).values())
