# Agent Workflow Policy

Date: 2026-07-15

`services/ai_agent/ai_agent_app/agent/workflow_policy.py` centralizes the Phase 1 tool-calling workflow policy.

## Responsibilities

- Decide the internal workflow state from session data and CRM/tool results.
- Return safe customer-facing labels for the demo UI.
- Declare which tools are allowed at the current step.
- Block out-of-order tool calls with a structured `workflow_blocked` result.
- Keep Gemini responsible for natural wording, not workflow authority.

## Current States

- `identity_required`
- `identity_lookup_pending`
- `traveler_not_found`
- `duplicate_traveler_detected`
- `traveler_verified`
- `human_handoff_required`

## Guardrail

`search_available_trips` and `get_trip_details` require a verified traveler in the current policy. If Gemini asks for those tools before identity is verified, the tool loop returns a workflow block asking for WhatsApp first.
