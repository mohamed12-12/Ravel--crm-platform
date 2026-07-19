# 08 Quick Wins

## Executive Summary

Several changes can quickly make the system feel more agentic and less broken without rebuilding it. The fastest wins are configuration clarity, demo data correction, UI wording, and better CRM snapshot/trip diagnostics.

## Findings

| Quick Win | Priority | Complexity | Why |
| --- | --- | --- | --- |
| Correct demo trip date | Critical | Low | Fixes "no trips" despite open CRM trip. |
| Recalculate traveler counters | High | Low | Improves profile and history output. |
| Display active AI mode | High | Low | Prevents deterministic behavior being mistaken for AI-agent behavior. |
| Hide deterministic quick actions in AI demos | Medium | Low | Makes chat feel less scripted. |
| Add trip exclusion reason in debug panel | High | Low/Medium | Explains why trips are filtered out. |
| Add provider readiness to health/bootstrap | Medium | Low | Makes Gemini configuration visible. |
| Add copy/prompt placeholder test | High | Low | Prevents `{full_name}`-style failures. |
| Fix unreachable log block | Low | Low | `server._session_runs_gemini` has logging after `return`. |

## Evidence

- Local DB has `RT-INT-26-DEM.start_date='2006-07-28'`; `UnifiedCRMService._trip_is_candidate` excludes it on July 15, 2026.
- Local DB has `TR00586` counters as `NULL`.
- `.env` has `AI_AGENT_MODE=deterministic`.
- `web/static/app.js` renders quick actions and stage placeholders for deterministic mode.
- `server.py:_session_runs_gemini` returns before the `app_logger.info` block.
- `SessionFlowManager._copy_text` strips unresolved placeholders after attempting formatting.

## Root Causes

- Demo data was not guarded against impossible open-trip date windows.
- Mode visibility is backend-centric, not user-facing.
- The UI is built as a demonstration portal and exposes internal workflow mechanics.
- Placeholder quality depends on workbook/copy content and fallback strings.

## Impact

- The screenshots make the agent look wrong even where code is behaving as designed.
- Users see "no confirmed upcoming trips" without knowing the trip was excluded as past dated.
- The side panels look deterministic and mechanical.

## Recommended Solution

1. Fix `RT-INT-26-DEM` start date to a valid future date or mark it Date TBD/Closed.
2. Run traveler stat recalculation for `TR00586` or backfill zero counters explicitly.
3. Add `agentMode` and provider readiness to the UI header.
4. In Gemini mode, replace quick actions with optional suggested replies generated from tool results.
5. Add debug-only trip filter reasons: inactive status, past start date, no capacity, unsupported type.
6. Add tests asserting no unresolved placeholders in assistant messages.
7. Move the unreachable log in `_session_runs_gemini` before the return or remove it.

## Priority

Critical for the demo data fixes; high for mode visibility and placeholder safety.

## Estimated Complexity

Low to medium. These are contained changes.

## Dependencies

- DB/admin access to correct the trip record.
- Agreement on whether counters should be `0` or recalculated from bookings.
- Small UI change in `services/ai_agent/ai_agent_app/web/static/app.js`.
- Test fixture updates.

## Risks

- Direct DB edits can be overwritten by import/sync if source artifacts remain stale.
- UI mode labels might expose too much implementation detail to clients unless phrased carefully.
- Placeholder tests may fail because configured workbook/copy artifacts are currently unstable in this checkout.

