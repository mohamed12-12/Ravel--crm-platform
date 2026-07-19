# Production Agent Architecture

## Purpose

Define a modular production agent layer on top of the existing Rahma Traveler tool-calling runtime without replacing Phase 1 behavior.

## Design

The production layer is a coordinator around the current Gemini-driven runtime. It separates:

- persona
- memory
- planning
- context assembly
- tool management
- safety checks
- observation storage
- agent state

## Flow

1. A user message enters the runtime.
2. The agent state is updated.
3. Context is assembled from persona, memory, CRM facts, conversation, and tool results.
4. The planner decides whether a tool is needed.
5. The runtime executes allowed tools through the existing Gemini loop.
6. The observation layer records what was learned.
7. Memory and agent state are updated.
8. A final answer is returned.

## Interfaces

- `AgentPersona`
- `AgentMemory`
- `AgentState`
- `AgentPlanner`
- `ContextBuilder`
- `ToolManager`
- `AgentSafetyLayer`
- `ProductionAgentCoordinator`

## Future Extensions

- durable long-term memory
- RAG retrieval
- multiple model providers
- write-phase orchestration
- evaluation traces

