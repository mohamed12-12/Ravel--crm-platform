# 04 Root Cause Analysis

## Executive Summary

The system feels like a chatbot because the currently active behavior is a scripted sales workflow with optional AI rewriting/tooling, not a goal-directed agent. The code already contains real agent building blocks, but the demo path, configuration, UI, and data quality issues make the experience appear deterministic.

## Findings

### Finding 1: Active configuration selects deterministic mode

Status: Complete as evidence, but a blocker for agent behavior.

The local `.env` sets `AI_AGENT_MODE=deterministic`. In `server.py:create_app`, a `GeminiAgent` is created only when `base_settings.gemini_agent_enabled` is true. Otherwise, `SessionFlowManager.handle_message` owns the flow.

Severity: Critical.

### Finding 2: The finite-state flow owns the business decisions

Status: Complete implementation, but not agentic.

`SessionFlowManager.handle_message` decides when to ask for WhatsApp, resolve country code, look up a traveler, ask trip type, search trips, create a lead, collect passport attachment, collect room/flight/currency, and create booking drafts.

Severity: Critical.

### Finding 3: Gemini exists but is not the single orchestrator

Status: Partial.

`GeminiAgent.respond` supports tools. However, `server._route_live_message_with_gemini` still extracts hints, refreshes previews, mutates session state, and applies tool results outside the model.

Severity: High.

### Finding 4: CRM context is split and partly precomputed

Status: Partial.

`server._build_gemini_session_context`, `GeminiAgent._collect_crm_context`, and `PromptBuilder.build_chat_prompt` each construct prompt context. This makes it hard to trace who supplied which fact.

Severity: High.

### Finding 5: UI exposes deterministic mechanics

Status: Complete as demo behavior.

`web/static/app.js` renders `session.stage`, stage-specific placeholders, quick-action buttons, CRM snapshot, trip matching, write activity, and lead intelligence. This is useful for demos but makes the customer experience look scripted.

Severity: Medium.

### Finding 6: Local data quality directly explains screenshot mismatches

Status: Complete as evidence.

The screenshot shows a CRM trip with start date `28 July 2006`. The local DB has trip `RT-INT-26-DEM` with `start_date='2006-07-28'`, `end_date='2026-08-10'`, and `sales_status='Open'`. `UnifiedCRMService._trip_is_candidate` filters dated trips using `date.fromisoformat(start_date) >= today`. On July 15, 2026, this trip is excluded, so `build_trip_result('international')` returns no trips.

Severity: High for demo credibility, medium for architecture.

### Finding 7: Traveler profile counters are null

Status: Partial data completeness.

The local DB traveler `TR00586` has `full_name='Mohamed Ashraf Safwat'` and `status='Active'`, but `local_trips_count`, `international_trips_count`, and `total_trips` are `NULL`. The chat summary and UI therefore cannot show strong history data.

Severity: Medium.

### Finding 8: Prompt injection controls are mostly policy text

Status: Missing/Partial.

Prompt files instruct the model not to invent facts, but there is no explicit context-sanitization layer for CRM notes, trip sales notes, customer messages, or copied workbook text.

Severity: High before production.

## Evidence

- `.env:AI_AGENT_MODE=deterministic`.
- `services/ai_agent/ai_agent_app/server.py:create_app` creates `GeminiAgent` only when `gemini_agent_enabled` is true.
- `services/ai_agent/ai_agent_app/server.py:send_message` routes to `_route_live_message_with_gemini` only in Gemini mode; otherwise it calls `sessions.handle_message`.
- `services/ai_agent/ai_agent_app/agent/session_flow.py:handle_message` contains stage branches for `awaiting_phone`, `awaiting_trip_type`, `awaiting_confirmation`, `awaiting_passport_upload`, `awaiting_room_type`, `awaiting_flight`, and `awaiting_currency`.
- `services/crm/system_services/unified_service.py:_trip_is_candidate` excludes past dated trips.
- Local DB read-only inspection found `TR00586` counters as `NULL` and `RT-INT-26-DEM.start_date='2006-07-28'`.
- Screenshots show menu-like prompts, quick actions, and "No confirmed upcoming trips match this request."

## Root Causes

- Production CRM features were implemented faster than a clean agent orchestration boundary.
- The deterministic MVP remained the safest demo path.
- AI was layered on as message rewriting and later as a tool-loop path, while the procedural workflow remained active.
- Demo data contains stale/invalid trip dates and incomplete traveler counters.

## Impact

- The model cannot consistently look autonomous because the backend often already decided the next step.
- The chat can show placeholders or thin summaries when data is incomplete or copy resolution fails.
- Trip matching can appear broken when the CRM detail page shows an open trip that the availability service correctly excludes as past dated.
- Users perceive a scenario simulator, not an agent.

## Recommended Solution

- Fix demo data first: correct `RT-INT-26-DEM.start_date` and recalculate traveler counters.
- Make AI mode an explicit demo option and display whether a turn used model tools.
- Build one orchestrator and move deterministic decisions behind tools/policies.
- Centralize context building and sanitize all CRM/customer fields.
- Persist decision audit records for every model call, tool call, validation, write, and fallback.

## Priority

Critical for mode/orchestration. High for trip date data. High for prompt-injection controls before external channel use.

## Estimated Complexity

Medium. Data fixes are low effort; orchestration cleanup is larger but can reuse existing modules.

## Dependencies

- Agreement on whether the client demo should default to Gemini mode.
- DB migration or admin workflow for correcting trip data.
- Test updates for agent-mode routing.
- A decision about retaining the deterministic flow as a fallback.

## Risks

- Correcting data without documenting the date rule may hide future import quality issues.
- Enabling Gemini mode without audit and sanitization could create production risk.
- Removing the deterministic flow too quickly could destabilize current tests and demos.

