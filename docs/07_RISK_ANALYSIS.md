# 07 Risk Analysis

## Executive Summary

The largest risks are not that the CRM lacks features. The largest risks are unsafe autonomy, unclear orchestration boundaries, prompt injection, stale data, and weak auditability. These should be addressed before presenting the system as a production AI sales agent.

## Findings

| Risk | Status | Severity | Evidence |
| --- | --- | --- | --- |
| Deterministic mode mistaken for AI agent | Active | High | `.env` sets `AI_AGENT_MODE=deterministic`; screenshots show staged prompts. |
| Unsafe or unclear CRM write ownership | Partial | High | Deterministic flow writes through gateway; Gemini writes through validator/executor. |
| Prompt injection through CRM/customer text | Missing/Partial | High | No explicit context sanitizer found. |
| Incomplete decision audit | Partial | High | Logs exist, but no persisted AI decision table. |
| Stale trip data | Active | High | `RT-INT-26-DEM` is open but dated `2006-07-28`, excluded from upcoming search. |
| Incomplete traveler history | Active | Medium | `TR00586` trip counters are `NULL`. |
| Provider lock-in | Partial | Medium | `llm_factory.py` returns Gemini only. |
| Process-local memory loss | Active | Medium | `GeminiAgent._memory` is an in-memory dict. |
| Workbook fallback instability | Active in checkout | Medium | Configured Excel artifacts fail `openpyxl` reads from shell. |
| UI overexposes internals | Active | Medium | `web/static/app.js` displays stage and deterministic quick actions. |

## Evidence

- `services/ai_agent/ai_agent_app/agent/write_tool_executor.py` records write audit to logs.
- `services/ai_agent/validation/action_validator.py` validates actions but is not a persisted audit store.
- `services/ai_agent/ai_agent_app/prompts/gemini_agent_system.md` contains safety instructions, but policy text is not the same as context sanitization.
- `services/crm/system_services/unified_service.py:_trip_is_candidate` filters past dated trips.
- `services/ai_agent/ai_agent_app/web/static/app.js` renders stage-specific placeholders and quick actions.
- `services/instagram/reply_engine.py` is intentionally deterministic and phone-first.

## Root Causes

- The project has multiple safety mechanisms, but they live in different layers.
- The deterministic MVP path still owns many writes and transitions.
- Agent audit is log-based rather than data-model-based.
- Demo data quality is not guarded by import/admin validation.

## Impact

- Sales users may trust an "AI agent" claim while the live flow is deterministic.
- Prompt injection could manipulate responses if CRM notes or user messages are inserted untrusted.
- Debugging a bad AI decision would require log reconstruction.
- Customers can be told no trips exist even while the CRM shows an open trip with bad date data.

## Recommended Solution

- Treat Gemini mode as experimental until context sanitization and decision audit are in place.
- Require all AI-mode writes to go through `ActionValidator` and `GeminiWriteToolExecutor`.
- Add persisted audit records.
- Add data-quality checks for trip windows and traveler counters.
- Keep deterministic fallback, but clearly label it.
- Add prompt-injection tests using CRM notes and sales notes.

## Priority

High before production exposure. Data quality is critical before demos.

## Estimated Complexity

Medium. Most safety logic exists, but audit persistence and sanitizer tests are new work.

## Dependencies

- Database migration for decision audit.
- Sanitizer design.
- Test fixtures with malicious CRM notes.
- Admin workflow for data-quality alerts.

## Risks

- Over-restrictive sanitizer could remove useful sales context.
- Too much logging can capture sensitive data if not redacted.
- Data correction without validation lets stale trip issues recur.

