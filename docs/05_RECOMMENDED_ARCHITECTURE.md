# 05 Recommended Architecture

## Executive Summary

Do not rebuild Rahma Traveler. Keep the CRM, models, repositories/services, tests, and current deterministic flow. Add a clear production agent layer above existing services, and convert the current deterministic flow into reusable tools, policies, and fallback behavior.

## Findings

The recommended architecture should separate these concerns:

- Channel adapter: web demo, WhatsApp, Instagram, future channels.
- Agent orchestrator: one turn in, one audited decision out.
- Persona service: configurable voice and business policy.
- Memory service: session, traveler, CRM, lead, and durable conversation memory.
- Context builder: sanitized, structured context from CRM and memory.
- Tool layer: typed CRM tools backed by `UnifiedCRMService`.
- Policy/validation layer: confirmations, write gating, human handoff.
- Model provider layer: Gemini now, other providers later.
- Decision audit: persisted trace of prompts, context ids, tool calls, validation, writes, and errors.

## Evidence

Reusable pieces already exist:

- `UnifiedCRMService` is the correct backend for CRM operations.
- `ReadOnlyCRMTools` already exposes traveler/trip/booking/passport/lead lookups.
- `GeminiWriteToolExecutor` already wraps create lead, update lead stage, create booking draft, and create handoff.
- `ActionValidator` already validates many business write constraints.
- `GeminiProvider` and `PromptBuilder` already support Gemini function calling.
- `SessionFlowManager` can become fallback policy or deterministic regression harness.

## Root Causes

- Existing components are useful but not composed around a single agent contract.
- State, context, tool execution, and UI behavior are currently split.
- The model is a participant, not always the owner of the customer turn.

## Impact

With the recommended architecture:

- The agent can respond naturally to customer intent.
- Tool use becomes explicit, auditable, and safe.
- CRM writes remain protected by existing validation.
- The UI can show meaningful agent activity without exposing stage-machine internals.
- Future channels can share the same agent core.

## Recommended Solution

### Proposed modules

```text
services/ai_agent/
  orchestrator/
    agent_orchestrator.py
    turn_result.py
  persona/
    persona_service.py
    persona_config.yaml
  memory/
    memory_store.py
    session_memory.py
    traveler_memory.py
  context/
    context_builder.py
    context_sanitizer.py
  tools/
    crm_tools.py
    tool_router.py
  policy/
    confirmation_policy.py
    safety_policy.py
  audit/
    decision_audit.py
```

### Turn contract

```text
AgentTurnInput:
  channel
  session_id
  user_message
  attachments
  known_identity

AgentTurnResult:
  assistant_reply
  tool_events
  memory_updates
  crm_write_results
  handoff_status
  audit_id
  ui_hints
```

### Component responsibilities

- `AgentOrchestrator`: calls memory, builds context, invokes model, routes tools, validates writes, records audit.
- `ContextBuilder`: gathers traveler profile, trip history, lead state, booking state, inventory, business rules, and session facts.
- `ContextSanitizer`: labels all CRM/customer text as data, strips or quarantines instruction-like text, and limits prompt payloads.
- `ToolRouter`: exposes only approved typed tools and records every result.
- `PolicyEngine`: decides whether a write needs customer confirmation, human review, or rejection.
- `MemoryStore`: persists session summaries, traveler preferences, prior trip interest, unresolved tasks, and handoff state.

## Priority

High. This is the architectural path from demo workflow to production AI agent.

## Estimated Complexity

Medium to high, but incremental. The first version can wrap current `GeminiAgent`, `ActionValidator`, and `UnifiedCRMService`.

## Dependencies

- Existing CRM service contracts.
- A small persistent table or JSON store for memory and decision audit.
- Prompt and persona configuration format.
- Tests for orchestrator turn contracts.

## Risks

- Over-abstracting too early could slow delivery.
- A new orchestrator must not duplicate business rules already in `UnifiedCRMService`.
- Memory persistence must account for privacy and deletion needs.
- Prompt/context sanitization must be in place before including CRM notes at scale.

