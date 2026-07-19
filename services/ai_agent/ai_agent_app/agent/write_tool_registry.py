from __future__ import annotations

from services.ai_agent.ai_agent_app.agent.tool_registry import ToolSpec


def _tool_spec(name: str, description: str, properties: dict[str, object]) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": properties,
        },
        output_schema={"type": "object"},
        allowed_write=True,
    )


def build_write_tool_registry() -> dict[str, ToolSpec]:
    return {
        "create_lead": _tool_spec(
            "create_lead",
            "Create a new lead after validation approves the request. Do not create travelers.",
            {
                "customer_name": {"type": "string"},
                "full_name": {"type": "string"},
                "raw_phone": {"type": "string"},
                "traveler_id": {"type": "string"},
                "country_code": {"type": "string"},
                "lead_source": {"type": "string"},
                "channel": {"type": "string"},
                "preferred_trip_type": {"type": "string"},
                "interested_trip_ids": {"type": "string"},
                "suggested_trip_ids": {"type": "string"},
                "priority": {"type": "string"},
                "follow_up_status": {"type": "string"},
                "follow_up_due_date": {"type": "string"},
                "notes": {"type": "string"},
                "group_size": {"type": "integer"},
                "language": {"type": "string"},
                "trip_id": {"type": "string"},
                "selected_trip_id": {"type": "string"},
                "flight_option": {"type": "string"},
                "room_type": {"type": "string"},
                "room_group": {"type": "string"},
                "booking_id": {"type": "string"},
                "flow_key": {"type": "string"},
                "current_step": {"type": "string"},
                "handoff_required": {"type": "boolean"},
                "handoff_reason": {"type": "string"},
            },
        ),
        "update_lead_stage": _tool_spec(
            "update_lead_stage",
            "Update the stage of an existing lead after validation approves the change.",
            {
                "lead_id": {"type": "string"},
                "requested_stage": {"type": "string"},
                "priority": {"type": "string"},
                "follow_up_status": {"type": "string"},
                "follow_up_due_date": {"type": "string"},
                "notes": {"type": "string"},
                "channel": {"type": "string"},
                "flow_key": {"type": "string"},
                "current_step": {"type": "string"},
            },
        ),
        "create_booking_draft": _tool_spec(
            "create_booking_draft",
            "Create a booking draft for an existing traveler and approved trip after validation approves it.",
            {
                "traveler_id": {"type": "string"},
                "raw_phone": {"type": "string"},
                "country_code": {"type": "string"},
                "lead_id": {"type": "string"},
                "trip_id": {"type": "string"},
                "room_type": {"type": "string"},
                "room_group": {"type": "string"},
                "flight_option": {"type": "string"},
                "date_option": {"type": "string"},
                "currency": {"type": "string"},
                "booking_notes": {"type": "string"},
                "passport_attachment_ref": {"type": "string"},
                "group_size": {"type": "integer"},
                "traveler_name": {"type": "string"},
                "channel": {"type": "string"},
                "source": {"type": "string"},
                "agent_notes": {"type": "string"},
            },
        ),
        "create_handoff": _tool_spec(
            "create_handoff",
            "Create a human handoff case after validation approves it and stop automation for the session.",
            {
                "traveler_id": {"type": "string"},
                "raw_phone": {"type": "string"},
                "country_code": {"type": "string"},
                "lead_id": {"type": "string"},
                "trip_id": {"type": "string"},
                "flow_key": {"type": "string"},
                "reason_code": {"type": "string"},
                "reason_text": {"type": "string"},
                "priority": {"type": "string"},
                "channel": {"type": "string"},
                "customer_name": {"type": "string"},
                "agent_summary": {"type": "string"},
                "customer_summary": {"type": "string"},
                "notes": {"type": "string"},
                "update_lead": {"type": "boolean"},
            },
        ),
    }
