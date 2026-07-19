# 06 Implementation Phases

## Executive Summary

The safest path is incremental. First fix the demo credibility issues and expose AI mode clearly. Then introduce a production orchestrator around the existing Gemini/tool/CRM components. Finally, add durable memory, audit, prompt-injection controls, and multi-channel parity.

## Findings

The repository already has enough pieces to avoid a rebuild:

- CRM data and routes are established.
- `UnifiedCRMService` centralizes operational CRM logic.
- Gemini tool calling and write validation are tested.
- The deterministic flow is reliable enough to keep as fallback.

## Evidence

- `tests/test_phase2_gemini_tool_loop.py` covers read-tool function calls.
- `tests/test_phase4_business_validation.py` covers validation behavior.
- `tests/test_phase5_gemini_write_tools.py` covers controlled write tools.
- `tests/test_phase11_demo_features.py` covers demo-specific behavior and safeguards.
- `services/ai_agent/ai_agent_app/server.py` already has mode switching between deterministic and Gemini paths.

## Root Causes

- The current system needs integration sequencing, not replacement.
- Existing tests encode MVP behavior, so the migration must preserve compatibility while adding agentic behavior.

## Impact

Phased implementation reduces risk, keeps demos working, and makes improvements visible at each step.

## Recommended Solution

### Phase 0: Data and demo credibility fixes

Priority: Critical.

Complexity: Low.

- Correct demo trip dates, especially `RT-INT-26-DEM.start_date='2006-07-28'`.
- Recalculate or backfill traveler counters for `TR00586`.
- Add an admin/data-quality warning when `sales_status='Open'` but `start_date` is in the past.
- Add tests for trip date filtering and stale open trips.

### Phase 1: Make mode behavior explicit

Priority: High.

Complexity: Low.

- Add a visible AI mode indicator that distinguishes deterministic vs Gemini.
- In AI mode, hide quick-action scaffolding unless it represents a real suggested action.
- Add a health endpoint field that reports whether Gemini provider is active.
- Keep deterministic mode as fallback.

### Phase 2: Introduce `AgentOrchestrator`

Priority: High.

Complexity: Medium.

- Create one service that owns turn handling.
- Wrap current `GeminiAgent.respond`.
- Move `server._build_gemini_session_context` and `_refresh_gemini_preview_from_context` into a context service.
- Keep existing route signatures stable.

### Phase 3: Centralize tools and policy

Priority: High.

Complexity: Medium.

- Route all CRM reads/writes through the tool layer in AI mode.
- Keep `UnifiedCRMService` as the implementation backend.
- Require `ActionValidator` before all writes.
- Add a confirmation policy for lead, booking, and handoff writes.

### Phase 4: Durable memory and audit

Priority: High.

Complexity: Medium.

- Add persisted agent session memory.
- Persist summaries, unresolved tasks, selected trip, traveler id, lead id, booking id, and handoff status.
- Add an AI decision audit table or log store with prompt id, response id, tool calls, validations, writes, errors, and latency.

### Phase 5: Prompt-injection and context hardening

Priority: High.

Complexity: Medium.

- Add a context sanitizer.
- Label CRM notes, sales notes, and customer messages as untrusted data.
- Strip or quarantine instruction-like content from CRM/user fields.
- Add tests with malicious CRM notes and customer instructions.

### Phase 6: UI agent experience

Priority: Medium.

Complexity: Medium.

- Replace stage chips with customer-friendly status.
- Show "Agent checked CRM", "Agent found trips", or "Human review needed" instead of raw state labels.
- Move demo internals into an optional debug panel.
- Make chat the primary experience.

### Phase 7: Channel parity

Priority: Medium.

Complexity: High.

- Use the orchestrator for Instagram/WhatsApp flows.
- Keep `services/instagram/reply_engine.py` deterministic fallback for safety.
- Add durable inbound/outbound retry handling.

## Priority

Critical:

- Phase 0 data and demo credibility fixes.
- Phase 1 mode clarity.
- Phase 2 orchestrator boundary.

High:

- Phase 3 centralized tools and policy.
- Phase 4 durable memory and audit.
- Phase 5 prompt-injection and context hardening.

Medium:

- Phase 6 UI agent experience.
- Phase 7 channel parity.

## Estimated Complexity

Overall complexity is medium to high. The early phases are low-risk, contained fixes; the orchestrator, durable memory, audit, and channel-unification phases require coordinated backend, UI, and test changes.

## Dependencies

- Operational DB migrations.
- Existing tests plus new orchestrator tests.
- Gemini API key and stable model config.
- Agreement on UX changes for the demo portal.

## Risks

- Enabling Gemini before context sanitization may expose prompt-injection risk.
- UI changes could obscure useful demo debugging if no debug panel remains.
- Durable memory introduces privacy and data lifecycle responsibilities.
