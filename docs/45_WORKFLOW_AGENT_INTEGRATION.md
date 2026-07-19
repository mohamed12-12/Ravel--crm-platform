# Workflow Agent Integration

Date: 2026-07-15

## Integrated Flow

Customer message -> `ToolCallingSessionRuntime` -> `ConversationWorkflowPolicy` -> `ProductionAgentCoordinator` -> Gemini -> Tool loop -> CRM read-only tool -> Observation/Memory -> Policy re-evaluation -> final response.

## Changed Files

- `workflow_policy.py`: backend workflow authority.
- `tool_calling_runtime.py`: phone extraction, CRM lookup, verified fact storage, policy state.
- `gemini_agent.py`: policy-aware CRM context, workflow-block tool result, reply grounding.
- `prompt_builder.py`: grounding policy payload.
- `tool_registry.py`: workflow prerequisite metadata.
- `server.py`: safe workflow status labels.
- `web/static/app.js`: verified-only CRM snapshot and pre-identity trip panel guard.

## Non-Goals

- No CRM service logic was changed.
- No broad CRM writes were added.
- Traveler creation remains deferred to a controlled-write phase.
