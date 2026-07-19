# Agent State Machine

## Purpose

Replace brittle UI-driven stages with a compact internal state object that tracks what the agent is trying to accomplish.

## Design

`AgentState` stores:

- goal
- subgoal
- completed tasks
- pending tasks
- last tool
- last observation
- confidence
- customer intent
- turn count

## Flow

1. The runtime creates or loads agent state for the session.
2. The planner updates the next step.
3. Tool results update the observation fields.
4. The state is carried forward to the next turn.

## Interfaces

- `mark_task_completed(...)`
- `add_pending_task(...)`
- `to_dict()`

## Future Extensions

- explicit state transitions
- goal graphs
- confidence decay
- recovery states for errors and retries

