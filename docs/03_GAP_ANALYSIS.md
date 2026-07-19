# 03 Gap Analysis

## Executive Summary

The core gap is architectural: the system has agent-like modules, but the live behavior is still controlled by Flask routes and deterministic session state. A production AI sales agent should reason over goals, use tools deliberately, maintain memory, validate writes, and adapt language. The current system performs many of those operations procedurally before or around the model.

## Findings

| Production Agent Requirement | Current Status | Gap |
| --- | --- | --- |
| Reusable persona | Partial | Persona is split across prompt files, settings, and fallback strings. |
| Model abstraction | Partial | Gemini wrapper exists; provider factory is Gemini-only. |
| Structured tools | Partial | Tools exist, but deterministic mode does not use model tool choice. |
| Tool choice by model | Partial/Missing in active demo | `.env` selects deterministic mode, so backend flow decides most actions. |
| Conversation memory | Partial | Session message list and in-memory Gemini cache only. |
| Traveler/CRM memory | Partial | CRM lookup is available, but not modeled as durable agent memory. |
| Context builder | Partial | Context is assembled in `server.py`, `GeminiAgent`, and `PromptBuilder`, with duplicated responsibility. |
| Orchestrator | Missing as explicit component | No single class owns turn planning, policy, tool execution, memory, and audit. |
| CRM write safety | Partial | Validation exists; confirmations are split across deterministic flow, validator, and UI. |
| Prompt-injection defense | Missing/Partial | No clear sanitizer for CRM notes, sales notes, or customer-supplied text as untrusted context. |
| Output validation | Partial | Tool inputs are validated; final replies are not schema-enforced beyond string cleanup. |
| Error recovery | Partial | Fallbacks exist; no robust retry/resume strategy for tool/model failures. |
| Decision trace | Partial | Logs exist; no persisted trace table/model for every AI decision. |

## Evidence

- Active mode: `.env` has `AI_AGENT_MODE=deterministic`.
- Deterministic orchestration: `SessionFlowManager.handle_message` directly calls `gateway.preview_customer`, `gateway.run_sales_cycle`, and `gateway.create_booking`.
- Gemini tool loop: `GeminiAgent._run_tool_loop` can execute function calls returned by Gemini.
- Context split: `server._build_gemini_session_context`, `GeminiAgent._collect_crm_context`, and `PromptBuilder.build_chat_prompt` all contribute to final prompt context.
- Memory split: `SessionState.messages` is serialized to the UI and `GeminiAgent._memory` is process-local.
- Safety: `ActionValidator.validate_action` and `GeminiWriteToolExecutor.execute` validate writes, but deterministic writes in `SessionFlowManager` use `gateway.run_sales_cycle` and `gateway.create_booking` directly.
- UI exposure: `web/static/app.js` renders stage-specific placeholders, quick actions, CRM snapshot, trip matching output, write activity, and lead intelligence.

## Root Causes

- The project preserved a reliable MVP flow and layered AI on top, rather than replacing the flow with a tool-using orchestrator.
- Business process safety was encoded as procedural stages.
- Context construction evolved in several places as new capabilities were added.
- The demo UI was designed to prove backend transitions, so it exposes internals that make the agent feel deterministic.

## Impact

- Customers must often conform to the workflow instead of the agent adapting to them.
- The side panels can show data, but the chat does not always sound like it is reasoning from that data.
- The model can be present while not clearly in control.
- Production hardening remains incomplete for memory, prompt injection, and audit.

## Recommended Solution

Close the gaps in phases:

1. Turn Gemini mode on only for a controlled AI-agent demo path.
2. Move all AI turns through a single orchestrator.
3. Convert deterministic state transitions into tool/policy outcomes.
4. Add durable memory and decision audit.
5. Sanitize and label CRM/customer context before prompt insertion.
6. Update the UI to show agent actions without exposing rigid stage mechanics.

## Priority

High for orchestration and context. Medium for provider extensibility. High for prompt injection before production exposure.

## Estimated Complexity

Medium to high. The work is mostly integration and boundary-setting, not a full rebuild.

## Dependencies

- Existing tests for Gemini tool loop and write validation.
- Operational DB availability.
- Agreement on production confirmation policy for leads/bookings/handoffs.
- UI design changes for AI mode.

## Risks

- Too much autonomy too early could create incorrect CRM writes.
- Keeping deterministic and Gemini paths indefinitely will increase duplication.
- Memory persistence may introduce privacy/data-retention obligations.

