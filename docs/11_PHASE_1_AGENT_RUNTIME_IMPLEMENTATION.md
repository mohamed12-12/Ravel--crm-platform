# 11 Phase 1 Agent Runtime Implementation

## Executive Summary

This phase introduces a real read-only tool-calling agent runtime for Rahma Traveler while keeping the deterministic session flow intact for rollback. The new path is activated with `AI_AGENT_MODE=tool_calling` and uses Gemini to decide whether to call approved read-only CRM tools before producing a natural customer reply.

The phase also fixes the blank-profile presentation issue by returning explicit field states and by preventing the UI from showing a seemingly complete profile when required CRM fields are missing.

## Files Inspected

- [docs/01_CURRENT_ARCHITECTURE.md](./01_CURRENT_ARCHITECTURE.md)
- [docs/02_AI_AGENT_AUDIT.md](./02_AI_AGENT_AUDIT.md)
- [docs/03_GAP_ANALYSIS.md](./03_GAP_ANALYSIS.md)
- [docs/04_ROOT_CAUSE_ANALYSIS.md](./04_ROOT_CAUSE_ANALYSIS.md)
- [docs/05_RECOMMENDED_ARCHITECTURE.md](./05_RECOMMENDED_ARCHITECTURE.md)
- [docs/06_IMPLEMENTATION_PHASES.md](./06_IMPLEMENTATION_PHASES.md)
- [docs/07_RISK_ANALYSIS.md](./07_RISK_ANALYSIS.md)
- [docs/08_QUICK_WINS.md](./08_QUICK_WINS.md)
- [docs/09_LONG_TERM_ROADMAP.md](./09_LONG_TERM_ROADMAP.md)
- [docs/10_FINAL_RECOMMENDATIONS.md](./10_FINAL_RECOMMENDATIONS.md)
- `services/ai_agent/ai_agent_app/server.py`
- `services/ai_agent/ai_agent_app/agent/session_flow.py`
- `services/ai_agent/ai_agent_app/agent/gemini_agent.py`
- `services/ai_agent/ai_agent_app/agent/tool_registry.py`
- `services/ai_agent/ai_agent_app/agent/read_only_tools.py`
- `services/ai_agent/ai_agent_app/config.py`
- `services/ai_agent/llm/llm_factory.py`
- `services/ai_agent/ai_agent_app/web/static/app.js`
- `services/ai_agent/ai_agent_app/web/templates/index.html`
- `services/ai_agent/ai_agent_app/README.md`
- `.env.example`
- `tests/test_phase1_readonly_agent.py`
- `tests/test_phase2_gemini_tool_loop.py`
- `tests/test_phase3_gemini_live_session.py`
- `tests/test_phase4_business_validation.py`
- `tests/test_phase5_gemini_write_tools.py`
- `tests/test_phase11_demo_features.py`

## Root Cause Confirmed

The active demo was previously running with `AI_AGENT_MODE=deterministic`, which meant `SessionFlowManager` controlled the chat and Gemini was only used for limited rewriting. That architecture produced the chatbot feel described in the audit.

This phase addresses the first safe step by adding a separate `tool_calling` runtime that becomes the controller in agent mode. It uses Gemini plus read-only CRM tools and does not expose any CRM write capability.

## Architecture Before

- `SessionFlowManager` owned the session conversation for deterministic mode.
- Gemini existed as a helper for reply rewriting and a separate experimental tool loop.
- CRM reads were split between deterministic flow logic and the Gemini live-session glue.
- The UI surfaced internal stages and deterministic quick-action controls.
- Traveler profile rendering could show a record structure even when required fields were incomplete.

## Architecture After

- `AI_AGENT_MODE=tool_calling` routes chat messages into `ToolCallingSessionRuntime`.
- The runtime builds session context, loads persona and recent memory, and invokes `GeminiAgent` with a read-only tool registry.
- Gemini may call only read-only tools:
  - `find_traveler_by_phone`
  - `get_traveler_profile`
  - `get_traveler_trip_history`
  - `search_available_trips`
  - `get_trip_details`
- Tool results are validated through existing service-layer methods and returned to Gemini for the final natural-language response.
- Session memory is updated with recent messages, the identified traveler, trip preferences, and the latest tool results.
- Deterministic mode remains available through `SessionFlowManager` for regression and rollback.

## Runtime Mode Behavior

- `tool_calling`: uses the new agent runtime and never exposes write tools.
- `deterministic`: uses the legacy `SessionFlowManager`.
- Mode mismatch between an existing session and the active app mode returns a safe 409 error instead of silently switching behavior.
- The app does not silently fall back from agent mode to deterministic mode.

## Tools Exposed

The tool-calling runtime exposes only these read-only tools:

1. `find_traveler_by_phone`
2. `get_traveler_profile`
3. `get_traveler_trip_history`
4. `search_available_trips`
5. `get_trip_details`

These tools all use the existing CRM service layer. The model does not receive SQL access and no write tools are available in this phase.

## Memory Behavior

The runtime keeps session-level memory for:

- Recent customer messages
- Recent agent messages
- Identified traveler ID
- Verified traveler profile
- Current travel intent
- Collected preferences
- Last tool result
- Current language
- Unresolved question

Verified CRM facts are kept separate from unverified customer statements. The phase does not add durable vector memory or RAG.

## Error Handling

The runtime returns safe failures for:

- Unknown tool
- Invalid arguments
- Tool failure
- Model timeout
- Empty model response
- Malformed tool call
- CRM failure
- Maximum tool rounds reached

Technical failures are logged in the backend, but the customer receives a professional fallback response that does not expose stack traces or infrastructure details.

## Files Changed

- `services/ai_agent/ai_agent_app/agent/tool_calling_runtime.py`
- `services/ai_agent/ai_agent_app/agent/read_only_tools.py`
- `services/ai_agent/ai_agent_app/agent/tool_registry.py`
- `services/ai_agent/ai_agent_app/agent/gemini_agent.py`
- `services/ai_agent/ai_agent_app/agent/__init__.py`
- `services/ai_agent/ai_agent_app/server.py`
- `services/ai_agent/ai_agent_app/config.py`
- `services/ai_agent/llm/llm_factory.py`
- `services/ai_agent/ai_agent_app/web/static/app.js`
- `services/ai_agent/ai_agent_app/README.md`
- `.env.example`

## Tests Added

Planned and added coverage for:

- Agent mode uses the new runtime.
- Deterministic mode still uses `SessionFlowManager`.
- Mode isolation between agent and deterministic sessions.
- Returning traveler lookup.
- Unknown traveler lookup.
- Duplicate phone lookup.
- Incomplete traveler profile handling.
- Natural Arabic trip-type request.
- Natural English trip-type request.
- Trip search with real matches.
- Trip search with no matches.
- Tool failure handling.
- Model timeout handling.
- Maximum tool-round handling.
- No write tool availability.
- No lead or booking creation in this phase.
- No blank profile labels in the UI.
- Regression suite compatibility.

## Test Results

Not yet run at the time of this document creation.

## Known Limitations

- The current phase is read-only only.
- Deterministic mode still owns the legacy flow.
- The runtime uses Gemini and the existing CRM services, but it is not yet a long-term memory or evaluation platform.
- The demo trip data still needs a separate data correction because one visible open trip has a historical start date.

## Deliberately Deferred

- CRM write tools
- Lead creation
- Booking creation
- Traveler updates
- Handoffs
- Durable vector memory
- RAG
- Multi-provider support beyond Gemini
- Long-term decision audit storage
- Phase 2 context expansion and write enablement

