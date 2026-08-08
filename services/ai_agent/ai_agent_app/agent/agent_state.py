"""Per-turn scratch state for ProductionAgentCoordinator (production_agent.py)
-- goal/subgoal/task tracking and a turn counter, folded into the prompt
context each turn. Distinct from SessionState (session_flow.py), which is
the actual durable per-customer conversation state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentState:
    goal: str = ""
    subgoal: str = ""
    completed_tasks: list[str] = field(default_factory=list)
    pending_tasks: list[str] = field(default_factory=list)
    last_tool: str = ""
    last_observation: str = ""
    confidence: float = 0.0
    customer_intent: str = ""
    turn_count: int = 0

    def mark_task_completed(self, task: str) -> None:
        if task and task not in self.completed_tasks:
            self.completed_tasks.append(task)
        if task in self.pending_tasks:
            self.pending_tasks.remove(task)

    def add_pending_task(self, task: str) -> None:
        if task and task not in self.pending_tasks:
            self.pending_tasks.append(task)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "subgoal": self.subgoal,
            "completed_tasks": list(self.completed_tasks),
            "pending_tasks": list(self.pending_tasks),
            "last_tool": self.last_tool,
            "last_observation": self.last_observation,
            "confidence": self.confidence,
            "customer_intent": self.customer_intent,
            "turn_count": self.turn_count,
        }

