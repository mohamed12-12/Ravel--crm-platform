# 01 Current Architecture

## Executive Summary

Rahma Traveler currently has two related but different applications:

- `apps/api`: the operational Flask CRM with travelers, leads, bookings, trips, handoffs, interactions, SQLAlchemy models, and admin templates.
- `services/ai_agent/ai_agent_app`: the demo sales-agent portal that owns the browser chat simulation, session state, deterministic conversation flow, sheet gateway compatibility, Gemini integration, and a bridge into the operational CRM service.

The system is functional, but the active demo configuration is still deterministic. The local `.env` sets `AI_AGENT_MODE=deterministic`, so the simulator behavior shown in the screenshots is driven primarily by `SessionFlowManager`, not by a production-style LLM agent orchestrator.

## Findings

| Area | Status | Finding |
| --- | --- | --- |
| Flask CRM | Complete | `apps/api/app/__init__.py` creates the CRM Flask app and registers CRM blueprints. |
| Operational data model | Complete | SQLAlchemy models exist for travelers, leads, bookings, trips, interactions, handoffs, documents, events, and users. |
| Demo portal | Complete | `services/ai_agent/ai_agent_app/server.py` provides `/api/session`, `/api/session/<id>/message`, reset, health, preview, passport upload, webhook, and visa routes. |
| Deterministic sales flow | Complete | `SessionFlowManager` is a finite-state workflow for phone lookup, trip type, confirmation, passport, room, flight, currency, lead, booking, and handoff. |
| Gemini live agent | Partial | `GeminiAgent` exists with tool-loop support, but the current `.env` runs deterministic mode. |
| Tool layer | Partial | Structured read/write tool registries exist, but deterministic mode does not let the model decide when to use them. |
| Memory | Partial | Session messages and an in-memory Gemini message cache exist; no durable agent memory abstraction exists. |
| CRM bridge | Complete | `system_bridge.py` connects demo actions to `UnifiedCRMService`. |
| Tests | Complete | Regression tests cover deterministic flow, Gemini read tools, validation, write tools, booking, demo alignment, and safeguards. |

## Evidence

- `apps/api/app/__init__.py:create_app` registers CRM blueprints for travelers, trips, bookings, leads, interactions, handoffs, admin, copy, and CRM identity routes.
- `apps/api/app/models/*.py` contains CRM entities such as `Traveler`, `Lead`, `Trip`, `TripBooking`, `Interaction`, and `HandoffQueue`.
- `services/ai_agent/ai_agent_app/server.py:create_app` wires the demo Flask app, sheet gateway, session manager, auth, and Gemini/deterministic mode.
- `services/ai_agent/ai_agent_app/agent/session_flow.py:SessionFlowManager` owns the deterministic state machine.
- `services/ai_agent/ai_agent_app/agent/gemini_agent.py:GeminiAgent` implements a Gemini tool loop and in-memory conversation cache.
- `services/ai_agent/ai_agent_app/agent/tool_registry.py` and `write_tool_registry.py` define structured read/write tools.
- `services/ai_agent/ai_agent_app/system_bridge.py` calls `UnifiedCRMService` for preview, lead writes, booking drafts, handoff, and passport persistence.
- `.env` currently has `AI_AGENT_MODE=deterministic`, `AI_PROVIDER=gemini`, and `SHEET_BACKEND=excel`.
- Screenshots show the deterministic opening and menu-style prompts: WhatsApp first, local/international menu, confirmation buttons, and stage chip such as `awaiting_confirmation`.

## Root Causes

- The demo entry point defaults to the deterministic workflow unless `AI_AGENT_MODE=gemini` and a configured Gemini provider are available.
- The deterministic workflow grew into the de facto orchestrator: it decides lookup, trip search, lead creation, booking creation, and handoff.
- The Gemini implementation was added beside the existing workflow rather than becoming the single orchestration layer.
- The UI still exposes workflow stages and quick actions, making the system feel like a scripted demo even when AI components exist.

## Impact

- The user experience feels like a chatbot/state machine because the conversation is guided by fixed stages and buttons.
- The LLM does not consistently appear to decide which CRM tools to call.
- CRM data can appear in side panels while the chat itself feels less contextual.
- Demo and operational CRM can disagree when date or stats fields are stale.

## Recommended Solution

Keep the existing CRM and deterministic workflow, but reposition them:

- Make a new production agent orchestrator the entry point for AI sessions.
- Keep `SessionFlowManager` as a fallback/compatibility workflow, not the primary intelligence layer.
- Make tools, validation, memory, context building, and persona explicit services.
- Use the current `UnifiedCRMService` as the tool backend rather than duplicating CRM logic.
- Move UI away from stage labels and fixed quick-action buttons in AI mode.

## Priority

High. The system already has the raw parts of an AI agent, but the active composition makes the customer-facing behavior look scripted.

## Estimated Complexity

Medium to high. The CRM and tools already exist, but the app needs a clearer orchestration boundary and durable memory/audit design.

## Dependencies

- Stable operational DB schema and migrations.
- `UnifiedCRMService` contracts for traveler/trip/lead/booking/handoff actions.
- Gemini provider configuration and credentials.
- Test fixtures for deterministic and Gemini modes.
- UI updates to represent agent decisions instead of workflow internals.

## Risks

- Switching the default to Gemini without guardrails could regress demo reliability.
- Existing tests may assume deterministic state transitions.
- The configured Excel artifacts are not cleanly readable by `openpyxl` in this checkout, so any sheet fallback path remains operationally risky.
- The runtime DB is modified in the worktree; audits should distinguish code findings from local data state.

