# 02 AI Agent Audit

## Executive Summary

The system has meaningful AI-agent components, but it is not yet operating as a production AI agent. It is best described as a deterministic sales workflow with optional Gemini support and tool-loop experiments. The biggest gap is not the absence of LLM code; it is that the model is not the central controller of persona, memory, tools, CRM context, validation, and state.

## Findings

| Capability | Status | Evidence | Assessment |
| --- | --- | --- | --- |
| Persona layer | Partial | `Settings.agent_persona_name`, `agent_conversation.md`, `gemini_agent_system.md`, fallback strings in `session_flow.py` | Persona is split across config, prompts, and code. |
| Model layer | Partial | `llm_factory.py`, `gemini_provider.py` | A provider wrapper exists, but the factory returns only `GeminiProvider`; OpenAI fields exist in settings but no OpenAI provider is implemented. |
| Tool layer | Partial | `tool_registry.py`, `write_tool_registry.py`, `read_only_tools.py`, `write_tool_executor.py` | Structured tools exist, but deterministic mode bypasses LLM tool choice. |
| Memory layer | Partial | `SessionState.messages`, `GeminiAgent._memory` | Memory is chat/session history only; it is not durable or semantically organized. |
| Context builder | Partial | `PromptBuilder`, `GeminiAgent._collect_crm_context`, `server._build_gemini_session_context` | Context exists, but it is split and partly inferred outside the model. |
| Orchestrator | Partial | `SessionFlowManager`, `GeminiAgent`, `server._route_live_message_with_gemini` | There is no single production agent orchestrator owning model, tools, memory, validation, and state. |
| CRM integration | Partial | `UnifiedCRMService`, `system_bridge.py`, read/write tools | CRM operations are solid, but deterministic mode lets Flask/service code decide many actions before the model. |
| Tool safety | Partial | `ActionValidator`, `GeminiWriteToolExecutor`, deterministic confirmation gates | Write validation exists, but confirmation policy is split between flow logic, validator, and UI. |
| Prompt injection protection | Missing/Partial | Prompt rules exist; no context sanitizer found for CRM fields | There is no explicit sanitizer/quoting policy for CRM notes before prompt insertion. |
| Output validation | Partial | Tool input validation and `sanitize_reply`; no schema validation for final replies | Tool calls are validated; free-form final model output is mostly trusted after light cleanup. |
| Error handling | Partial | Provider exceptions, fallback replies, route try/except | Failures usually return generic fallback or HTTP 500; no durable retry/audit queue for AI decisions. |
| Logging/audit | Partial | `agent_logger`, provider/tool logs, write audit logs | Logs exist, but there is no first-class persisted AI decision trace. |

## Evidence

- `services/ai_agent/ai_agent_app/agent/gemini_agent.py:respond` builds context, calls Gemini, runs tools, and returns `tool_requests`, `write_results`, `prompt_id`, `response_id`, and `usage_metadata`.
- `services/ai_agent/ai_agent_app/agent/gemini_agent.py:_memory` is a process-local dictionary keyed by session id.
- `services/ai_agent/ai_agent_app/server.py:_route_live_message_with_gemini` sets `session.stage = "gemini_conversation"`, merges regex-derived hints, refreshes preview context, calls `GeminiAgent.respond`, then applies tool results to the session.
- `services/ai_agent/ai_agent_app/agent/session_flow.py:handle_message` contains direct state transitions and CRM calls in deterministic mode.
- `services/ai_agent/validation/action_validator.py` validates supported actions, identity, open leads, booking fields, traveler status, and handoff thresholds.
- `services/ai_agent/ai_agent_app/prompts/gemini_agent_system.md` says the model should use tools and avoid rigid behavior, but `.env` currently selects deterministic mode.
- `tests/test_phase2_gemini_tool_loop.py`, `tests/test_phase4_business_validation.py`, and `tests/test_phase5_gemini_write_tools.py` prove Gemini tool-loop and validation behavior exist.

## Root Causes

- AI capabilities were added incrementally around an existing scripted demo.
- The deterministic flow remains the stable path and is the configured default.
- The code has no explicit `AgentOrchestrator` abstraction that composes model, tools, memory, context, validation, and policy.
- Tool safety is implemented at execution time, but model autonomy is constrained by route/session glue and deterministic shortcuts.

## Impact

- The agent cannot consistently adapt conversation strategy to the customer's natural language.
- It often appears to be collecting fixed form fields rather than pursuing a goal.
- The UI and backend reveal stage-machine behavior instead of agent decisions.
- Production concerns like durable memory, prompt-injection safety, and decision audit are not yet complete.

## Recommended Solution

Create a thin production orchestration layer around existing components:

- `AgentOrchestrator`: owns turn handling.
- `PersonaService`: loads configurable persona and policy.
- `ContextBuilder`: creates sanitized, role-separated CRM/business context.
- `ToolRouter`: exposes structured tools backed by `UnifiedCRMService`.
- `MemoryStore`: persists conversation, traveler, lead, and session memory.
- `PolicyEngine`: centralizes confirmation and write safety.
- `DecisionAuditStore`: persists model prompts, tool calls, validations, and outcomes.

## Priority

High. This is the main reason the system still feels like a chatbot.

## Estimated Complexity

Medium. Most backend primitives already exist, but their ownership boundaries need to be made explicit.

## Dependencies

- Existing `UnifiedCRMService` methods.
- Existing Gemini provider and tests.
- Stable prompt files and environment configuration.
- Decision about whether Gemini mode becomes the default for the demo.

## Risks

- More autonomy without stronger context sanitization could increase prompt-injection and incorrect-write risk.
- Moving orchestration too quickly could break the current demo guarantees.
- If AI mode remains off in `.env`, users will not experience the new agent behavior even if code exists.

