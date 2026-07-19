# Planner Design

## Purpose

Separate decision-making from transport and execution so the agent can reason about what to do next before it acts.

## Design

`AgentPlanner` receives:

- current goal
- current context
- available tool names

It returns a `PlannerDecision` describing whether to:

- ask the user for more detail
- select a tool
- continue with a safe response

## Flow

1. The runtime builds context.
2. The planner inspects the latest user intent.
3. The planner selects a tool when a tool is useful.
4. The runtime executes the tool and stores the result.
5. The runtime can re-plan on the next turn.

## Interfaces

- `AgentPlanner.plan(...)`
- `PlannerDecision`

## Future Extensions

- multi-step plans
- subgoal decomposition
- confidence scoring
- tool-cost and latency awareness

