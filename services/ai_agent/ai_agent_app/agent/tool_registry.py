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
    return {
        "search_traveler_by_phone": ToolSpec(
            name="search_traveler_by_phone",
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
        "get_trip_details": ToolSpec(
            name="get_trip_details",
            description="Return the CRM details for one trip.",
            input_schema={
                "type": "object",
                "properties": {"trip_id": {"type": "string"}},
                "required": ["trip_id"],
            },
            output_schema={"type": "object"},
        ),
        "lookup_booking": ToolSpec(
            name="lookup_booking",
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
        "lookup_lead": ToolSpec(
            name="lookup_lead",
            description="Look up lead details by lead_id, traveler_id, or phone.",
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
    }


def render_tool_list() -> str:
    return "\n".join(spec.as_prompt_block() for spec in build_read_only_tool_registry().values())
