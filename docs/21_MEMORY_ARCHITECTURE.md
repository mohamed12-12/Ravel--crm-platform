# Memory Architecture

## Purpose

Provide a simple memory abstraction that can support short-term task tracking now and longer-term traveler memory later.

## Design

`AgentMemory` keeps three buckets:

- `short_term`: active goal, last tool, current subgoal
- `long_term`: traveler profile hints and durable preferences
- `observations`: compact summaries of tool results

## Flow

1. The coordinator reads memory when building context.
2. The planner uses the current task context.
3. Tool execution produces observations.
4. Observations are appended to memory.
5. Durable traveler facts can be promoted into long-term memory later.

## Interfaces

- `update_short_term(...)`
- `update_long_term(...)`
- `add_observation(...)`
- `snapshot()`

## Future Extensions

- vector memory
- embedding-backed retrieval
- CRM-linked traveler preference summaries
- recency and salience scoring

