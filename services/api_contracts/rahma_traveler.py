from __future__ import annotations

from copy import deepcopy
from typing import Any


JsonDict = dict[str, Any]


def _error_schema() -> JsonDict:
    return {
        "type": "object",
        "properties": {
            "error": {"type": "string"},
            "message": {"type": "string"},
            "details": {},
        },
        "additionalProperties": True,
    }


def _session_schema() -> JsonDict:
    return {
        "type": "object",
        "required": ["id", "stage", "messages"],
        "properties": {
            "id": {"type": "string", "example": "62dfd16f7a3a4f3d8e6a"},
            "stage": {"type": "string", "example": "trip_type_required"},
            "runtimeMode": {"type": "string", "enum": ["deterministic", "gemini", "tool_calling"]},
            "agentMode": {"type": "string", "enum": ["deterministic", "gemini", "tool_calling"]},
            "customerStatus": {"type": "string", "example": "Waiting for customer response"},
            "chatEnabled": {"type": "boolean"},
            "requiresIntake": {"type": "boolean"},
            "language": {"type": "string", "enum": ["en", "ar"]},
            "rawPhone": {"type": "string"},
            "customerName": {"type": "string"},
            "selectedTripId": {"type": "string"},
            "selectedTripName": {"type": "string"},
            "tripType": {"type": "string", "enum": ["local", "international", ""]},
            "roomType": {"type": "string", "enum": ["Single", "Double", "Triple", ""]},
            "roomGroup": {"type": "string", "enum": ["boys", "girls", ""]},
            "groupSize": {"type": "integer", "minimum": 1},
            "flightOption": {"type": "string"},
            "passportAttachmentRef": {"type": "string"},
            "toolsUsed": {"type": "array", "items": {"type": "string"}},
            "fallbackUsed": {"type": "boolean"},
            "messages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["role", "text"],
                    "properties": {
                        "role": {"type": "string", "enum": ["assistant", "user", "system"]},
                        "text": {"type": "string"},
                    },
                },
            },
            "stats": {"type": "object", "additionalProperties": True},
            "preview": {"type": "object", "additionalProperties": True},
            "finalResult": {"type": "object", "additionalProperties": True},
            "bookingResult": {"type": "object", "additionalProperties": True},
        },
        "additionalProperties": True,
    }


def _agent_read_actions() -> list[str]:
    return [
        "search_traveler",
        "find_traveler_by_phone",
        "get_traveler_profile",
        "get_traveler_profile_safe",
        "get_traveler_trip_history",
        "search_trips",
        "search_available_trips",
        "get_trip_details",
        "get_booking_status",
        "lookup_lead",
        "get_passport_status",
    ]


def _agent_write_actions() -> list[str]:
    return ["create_lead", "update_lead_stage", "create_booking_draft", "create_handoff"]


def _base_contract(*, title: str, description: str, base_url: str | None = None) -> JsonDict:
    servers = [{"url": base_url}] if base_url else [{"url": "/"}]
    return {
        "openapi": "3.1.0",
        "info": {
            "title": title,
            "version": "3.0.0",
            "description": description,
        },
        "servers": servers,
        "tags": [],
        "paths": {},
        "components": {
            "securitySchemes": {
                "CrmApiKey": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-CRM-API-Key",
                    "description": "CRM API token for server-to-server requests.",
                },
                "BearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "Alternative bearer token form for CRM API token authentication.",
                },
                "CsrfToken": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-CSRF-Token",
                    "description": "Required for browser-session CRM writes when auth is enabled.",
                },
            },
            "schemas": {
                "Error": _error_schema(),
                "Session": _session_schema(),
            },
        },
    }


def _json_response(schema: JsonDict, *, description: str = "Success") -> JsonDict:
    return {
        "description": description,
        "content": {"application/json": {"schema": schema}},
    }


def _error_responses(*codes: str) -> JsonDict:
    return {code: _json_response({"$ref": "#/components/schemas/Error"}, description="Error") for code in codes}


def build_agent_openapi_contract(*, base_url: str | None = None) -> JsonDict:
    contract = _base_contract(
        title="Rahma Traveler AI Sales Agent API",
        description=(
            "Backend API for Rahma Traveler's AI sales demo. The chat runtime owns session "
            "state, CRM matching, controlled lead writes, booking draft creation, passport "
            "attachment references, and read-only operational previews."
        ),
        base_url=base_url,
    )
    contract["tags"] = [
        {"name": "System", "description": "Health, bootstrap, and non-sensitive configuration."},
        {"name": "Sessions", "description": "AI agent chat session lifecycle."},
        {"name": "Documents", "description": "Passport attachment upload for international bookings."},
        {"name": "CRM Preview", "description": "Read-only demo CRM snapshots."},
        {"name": "Webhook", "description": "Instagram/Meta webhook verification and ingestion."},
    ]
    contract["paths"] = {
        "/api/health": {
            "get": {
                "tags": ["System"],
                "summary": "Check service and database health.",
                "responses": {
                    "200": _json_response(
                        {
                            "type": "object",
                            "properties": {
                                "status": {"type": "string", "example": "ok"},
                                "runtimeWorkbookExists": {"type": "boolean"},
                                "sourceWorkbookExists": {"type": "boolean"},
                                "activeDbPath": {"type": "string"},
                                "counts": {"type": "object", "additionalProperties": {"type": "integer"}},
                            },
                        }
                    )
                },
            }
        },
        "/api/bootstrap": {
            "get": {
                "tags": ["System"],
                "summary": "Load initial frontend state and CRM statistics.",
                "responses": {
                    "200": _json_response({"type": "object", "additionalProperties": True}),
                },
            }
        },
        "/api/agent-config": {
            "get": {
                "tags": ["System"],
                "summary": "Return non-sensitive agent configuration.",
                "responses": {
                    "200": _json_response(
                        {
                            "type": "object",
                            "properties": {
                                "agentPersonaName": {"type": "string"},
                                "websiteUrl": {"type": "string"},
                                "postTripHandoffEnabled": {"type": "boolean"},
                                "defaultCountryCode": {"type": "string"},
                                "aiAgentMode": {"type": "string"},
                            },
                        }
                    )
                },
            }
        },
        "/api/openapi.json": {
            "get": {
                "tags": ["System"],
                "summary": "Return this OpenAPI contract.",
                "responses": {"200": _json_response({"type": "object", "additionalProperties": True})},
            }
        },
        "/api/session": {
            "post": {
                "tags": ["Sessions"],
                "summary": "Create a new AI sales session.",
                "requestBody": {
                    "required": False,
                    "content": {"application/json": {"schema": {"type": "object", "additionalProperties": True}}},
                },
                "responses": {
                    "200": _json_response(
                        {
                            "type": "object",
                            "required": ["session"],
                            "properties": {"session": {"$ref": "#/components/schemas/Session"}},
                        }
                    )
                },
            }
        },
        "/api/session/{session_id}": {
            "get": {
                "tags": ["Sessions"],
                "summary": "Fetch a session by ID.",
                "parameters": [{"name": "session_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {
                    "200": _json_response({"type": "object", "properties": {"session": {"$ref": "#/components/schemas/Session"}}}),
                    **_error_responses("404"),
                },
            }
        },
        "/api/session/{session_id}/message": {
            "post": {
                "tags": ["Sessions"],
                "summary": "Send a customer chat message to the AI agent.",
                "description": "In tool-calling mode, backend identity policy, workflow policy, CRM tools, and controlled write validation execute before the response is returned.",
                "parameters": [{"name": "session_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["text"],
                                "properties": {"text": {"type": "string", "example": "I want an international trip"}},
                            }
                        }
                    },
                },
                "responses": {
                    "200": _json_response({"type": "object", "properties": {"session": {"$ref": "#/components/schemas/Session"}}}),
                    **_error_responses("400", "404", "409", "500"),
                },
            }
        },
        "/api/session/{session_id}/intake": {
            "post": {
                "tags": ["Sessions"],
                "summary": "Submit legacy deterministic-mode intake details.",
                "parameters": [{"name": "session_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "fullName": {"type": "string"},
                                    "birthday": {"type": "string", "format": "date"},
                                    "gender": {"type": "string"},
                                    "nationality": {"type": "string"},
                                    "countryCode": {"type": "string"},
                                    "phone": {"type": "string"},
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": _json_response({"type": "object", "properties": {"session": {"$ref": "#/components/schemas/Session"}}}),
                    **_error_responses("404", "409", "500"),
                },
            }
        },
        "/api/session/{session_id}/book": {
            "post": {
                "tags": ["Sessions"],
                "summary": "Create a legacy deterministic-mode booking draft.",
                "parameters": [{"name": "session_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["tripId", "roomType"],
                                "properties": {
                                    "tripId": {"type": "string", "example": "RT-INT-26-DEM"},
                                    "roomType": {"type": "string", "enum": ["Single", "Double", "Triple"]},
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": _json_response({"type": "object", "properties": {"session": {"$ref": "#/components/schemas/Session"}}}),
                    **_error_responses("400", "404", "409"),
                },
            }
        },
        "/api/session/{session_id}/passport_attachment": {
            "post": {
                "tags": ["Documents"],
                "summary": "Upload a passport attachment for an AI agent session.",
                "parameters": [{"name": "session_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "multipart/form-data": {
                            "schema": {
                                "type": "object",
                                "required": ["file"],
                                "properties": {"file": {"type": "string", "format": "binary"}},
                            }
                        }
                    },
                },
                "responses": {
                    "200": _json_response(
                        {
                            "type": "object",
                            "properties": {
                                "ok": {"type": "boolean"},
                                "ref": {"type": "string"},
                                "session": {"$ref": "#/components/schemas/Session"},
                            },
                            "additionalProperties": True,
                        }
                    ),
                    **_error_responses("400", "404", "413", "415"),
                },
            }
        },
        "/api/lead/{lead_id}/qualify": {
            "post": {
                "tags": ["CRM Preview"],
                "summary": "Mark a demo lead as qualified.",
                "parameters": [{"name": "lead_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": _json_response({"type": "object", "properties": {"ok": {"type": "boolean"}}})},
            }
        },
        "/api/crm/preview": {
            "get": {
                "tags": ["CRM Preview"],
                "summary": "Return a read-only CRM traveler preview.",
                "responses": {
                    "200": _json_response(
                        {
                            "type": "object",
                            "properties": {"travelers": {"type": "array", "items": {"type": "object", "additionalProperties": True}}},
                        }
                    )
                },
            }
        },
        "/api/reset": {
            "post": {
                "tags": ["System"],
                "summary": "Reset demo runtime data when demo mode allows it.",
                "responses": {
                    "200": _json_response({"type": "object", "properties": {"ok": {"type": "boolean"}, "stats": {"type": "object"}}}),
                    **_error_responses("401", "409"),
                },
            }
        },
        "/api/visa/{destination}": {
            "get": {
                "tags": ["CRM Preview"],
                "summary": "Return table-based visa requirement guidance with disclaimer metadata.",
                "parameters": [
                    {"name": "destination", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"name": "nationality", "in": "query", "required": False, "schema": {"type": "string"}},
                ],
                "responses": {"200": _json_response({"type": "object", "additionalProperties": True})},
            }
        },
        "/webhook": {
            "get": {
                "tags": ["Webhook"],
                "summary": "Verify Meta webhook subscription.",
                "parameters": [
                    {"name": "hub.mode", "in": "query", "schema": {"type": "string"}},
                    {"name": "hub.verify_token", "in": "query", "schema": {"type": "string"}},
                    {"name": "hub.challenge", "in": "query", "schema": {"type": "string"}},
                ],
                "responses": {"200": {"description": "Webhook challenge response"}},
            },
            "post": {
                "tags": ["Webhook"],
                "summary": "Receive Instagram webhook events.",
                "responses": {
                    "200": _json_response(
                        {
                            "type": "object",
                            "properties": {
                                "status": {"type": "string"},
                                "events": {"type": "integer"},
                                "persisted": {"type": "integer"},
                                "duplicates": {"type": "integer"},
                                "repliesSent": {"type": "integer"},
                                "repliesFailed": {"type": "integer"},
                            },
                        }
                    )
                },
            },
        },
    }
    return deepcopy(contract)


def build_crm_openapi_contract(*, base_url: str | None = None) -> JsonDict:
    contract = _base_contract(
        title="Rahma Traveler CRM API",
        description=(
            "Operational CRM API contract for agent read/write integration, lead and booking "
            "workflow actions, traveler documents, duplicate review, and employee follow-up operations."
        ),
        base_url=base_url,
    )
    contract["security"] = [{"CrmApiKey": []}, {"BearerAuth": []}]
    contract["tags"] = [
        {"name": "Agent CRM Bridge", "description": "Validated read/write bridge used by the AI agent."},
        {"name": "Leads", "description": "Lead pipeline and follow-up workflow actions."},
        {"name": "Bookings", "description": "Booking status, payment, assignment, and follow-up actions."},
        {"name": "Travelers", "description": "Traveler profile and document operations."},
        {"name": "Trips", "description": "Trip catalog and inventory operations."},
        {"name": "Admin", "description": "Duplicate resolution and operational admin APIs."},
        {"name": "Copy", "description": "Reusable copy templates and rendering."},
    ]
    contract["paths"] = {
        "/api/openapi.json": {
            "get": {
                "security": [],
                "tags": ["Admin"],
                "summary": "Return this CRM OpenAPI contract.",
                "responses": {"200": _json_response({"type": "object", "additionalProperties": True})},
            }
        },
        "/api/crm/agent/read": {
            "post": {
                "tags": ["Agent CRM Bridge"],
                "summary": "Execute an approved read-only CRM action for the AI agent.",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["action"],
                                "properties": {
                                    "action": {"type": "string", "enum": _agent_read_actions()},
                                    "payload": {"type": "object", "additionalProperties": True},
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": _json_response({"type": "object", "properties": {"result": {"type": "object", "additionalProperties": True}}}),
                    **_error_responses("401", "403", "422", "500"),
                },
            }
        },
        "/api/crm/agent/write": {
            "post": {
                "tags": ["Agent CRM Bridge"],
                "summary": "Execute a backend-validated controlled CRM write for the AI agent.",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["action"],
                                "properties": {
                                    "action": {"type": "string", "enum": _agent_write_actions()},
                                    "payload": {"type": "object", "additionalProperties": True},
                                    "session_context": {"type": "object", "additionalProperties": True},
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": _json_response({"type": "object", "properties": {"result": {"type": "object", "additionalProperties": True}}}),
                    **_error_responses("401", "403", "422", "500"),
                },
            }
        },
        "/api/crm/agent/passport": {
            "post": {
                "tags": ["Agent CRM Bridge"],
                "summary": "Save passport metadata/reference for a traveler through the CRM bridge.",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["traveler_id", "payload"],
                                "properties": {
                                    "traveler_id": {"type": "string", "example": "TR00589"},
                                    "payload": {
                                        "type": "object",
                                        "properties": {
                                            "passport_attachment_ref": {"type": "string"},
                                            "attachment_file_name": {"type": "string"},
                                            "attachment_mime_type": {"type": "string"},
                                            "notes": {"type": "string"},
                                        },
                                        "additionalProperties": True,
                                    },
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": _json_response({"type": "object", "properties": {"result": {"type": "object", "additionalProperties": True}}}),
                    **_error_responses("401", "403", "422", "500"),
                },
            }
        },
        "/api/crm/duplicates": {
            "get": {
                "tags": ["Admin"],
                "summary": "List potential duplicate traveler groups.",
                "responses": {
                    "200": _json_response(
                        {
                            "type": "object",
                            "properties": {
                                "count": {"type": "integer"},
                                "duplicates": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                            },
                        }
                    ),
                    **_error_responses("401", "403", "500"),
                },
            }
        },
        "/api/crm/resolve-identity": {
            "post": {
                "tags": ["Admin"],
                "summary": "Merge duplicate traveler records into a master traveler.",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["master_id", "alias_ids"],
                                "properties": {
                                    "master_id": {"type": "string"},
                                    "alias_ids": {"type": "array", "items": {"type": "string"}},
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": _json_response({"type": "object", "additionalProperties": True}),
                    **_error_responses("400", "401", "403", "404", "422", "500"),
                },
            }
        },
        "/api/copy/render": {
            "get": {"tags": ["Copy"], "summary": "Render a copy template by key.", "responses": {"200": _json_response({"type": "object", "additionalProperties": True})}},
            "post": {"tags": ["Copy"], "summary": "Render a copy template with JSON values.", "responses": {"200": _json_response({"type": "object", "additionalProperties": True})}},
        },
        "/api/copy/templates": {
            "get": {"tags": ["Copy"], "summary": "List copy templates.", "responses": {"200": _json_response({"type": "object", "additionalProperties": True})}},
        },
        "/api/copy/validate": {
            "post": {"tags": ["Copy"], "summary": "Validate copy template variables.", "responses": {"200": _json_response({"type": "object", "additionalProperties": True})}},
        },
        "/leads/{lead_id}/advance": {
            "post": {
                "tags": ["Leads"],
                "summary": "Advance a lead to the next allowed pipeline stage.",
                "parameters": [{"name": "lead_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": _json_response({"type": "object", "properties": {"status": {"type": "string"}, "new_stage": {"type": "string"}}}), **_error_responses("401", "403", "404")},
            }
        },
        "/leads/{lead_id}/quick-action": {
            "post": {
                "tags": ["Leads"],
                "summary": "Apply a predefined employee follow-up action to a lead.",
                "parameters": [{"name": "lead_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["action"],
                                "properties": {
                                    "action": {
                                        "type": "string",
                                        "enum": ["mark_contacted", "waiting_customer", "request_documents", "request_deposit", "escalate_handoff"],
                                    },
                                    "note": {"type": "string"},
                                    "follow_up_due_date": {"type": "string", "format": "date"},
                                },
                            }
                        }
                    },
                },
                "responses": {"200": _json_response({"type": "object", "additionalProperties": True}), **_error_responses("400", "401", "403", "404")},
            }
        },
        "/leads/{lead_id}/handoff": {
            "post": {
                "tags": ["Leads"],
                "summary": "Create a human handoff case from a lead.",
                "parameters": [{"name": "lead_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "requestBody": {
                    "required": False,
                    "content": {
                        "application/json": {
                            "schema": {"type": "object", "properties": {"reason": {"type": "string"}, "priority": {"type": "string"}}}
                        }
                    },
                },
                "responses": {"200": _json_response({"type": "object", "additionalProperties": True}), **_error_responses("401", "403", "404")},
            }
        },
        "/bookings/{booking_id}/status": {
            "post": {
                "tags": ["Bookings"],
                "summary": "Update booking, payment, assignment, and follow-up fields.",
                "parameters": [{"name": "booking_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "requestBody": {
                    "required": False,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "booking_status": {"type": "string"},
                                    "payment_status": {"type": "string"},
                                    "booking_notes": {"type": "string"},
                                    "assigned_to_user_id": {"type": "integer", "nullable": True},
                                    "priority": {"type": "string"},
                                    "next_action": {"type": "string"},
                                    "next_follow_up_at": {"type": "string"},
                                    "customer_response_status": {"type": "string"},
                                    "expected_history_count": {"type": "integer"},
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": _json_response({"type": "object", "properties": {"status": {"type": "string"}, "booking_id": {"type": "string"}}}),
                    **_error_responses("400", "401", "403", "404", "409"),
                },
            }
        },
        "/travelers/{traveler_id}": {
            "put": {
                "tags": ["Travelers"],
                "summary": "Update a traveler profile.",
                "parameters": [{"name": "traveler_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": _json_response({"type": "object", "additionalProperties": True}), **_error_responses("401", "403", "404")},
            },
            "delete": {
                "tags": ["Travelers"],
                "summary": "Mark a traveler inactive.",
                "parameters": [{"name": "traveler_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": _json_response({"type": "object", "additionalProperties": True}), **_error_responses("401", "403", "404")},
            },
        },
        "/travelers/{traveler_id}/documents": {
            "post": {
                "tags": ["Travelers"],
                "summary": "Upload a traveler document such as a passport.",
                "parameters": [{"name": "traveler_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "multipart/form-data": {
                            "schema": {
                                "type": "object",
                                "required": ["file"],
                                "properties": {
                                    "file": {"type": "string", "format": "binary"},
                                    "category": {"type": "string", "example": "passport"},
                                    "verification_status": {"type": "string", "example": "pending"},
                                },
                            }
                        }
                    },
                },
                "responses": {"302": {"description": "Redirects to traveler detail in browser UI"}, **_error_responses("401", "403", "404")},
            }
        },
        "/trips/{trip_id}/inventory": {
            "post": {
                "tags": ["Trips"],
                "summary": "Adjust trip room inventory.",
                "parameters": [{"name": "trip_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "available_single": {"type": "integer"},
                                    "available_double": {"type": "integer"},
                                    "available_triple": {"type": "integer"},
                                    "boys_double": {"type": "integer"},
                                    "girls_double": {"type": "integer"},
                                    "boys_triple": {"type": "integer"},
                                    "girls_triple": {"type": "integer"},
                                },
                            }
                        }
                    },
                },
                "responses": {"200": _json_response({"type": "object", "additionalProperties": True}), **_error_responses("400", "401", "403", "404")},
            }
        },
    }
    return deepcopy(contract)
