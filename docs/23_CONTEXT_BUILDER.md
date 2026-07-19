# Context Builder

## Purpose

Construct the model-facing context from structured agent inputs instead of passing raw conversation data directly.

## Design

`ContextBuilder` merges:

- persona
- memory
- current goal
- CRM facts
- conversation
- tool results
- system constraints

## Flow

1. The runtime gathers the latest session and CRM facts.
2. The builder creates a single bundle for the model layer.
3. The bundle stays separate from UI state and CRM persistence.

## Interfaces

- `ContextBuilder.build(...)`
- `AgentContextBundle`

## Future Extensions

- retrieval-augmented context
- structured task summaries
- safety context overlays
- per-channel context shaping

