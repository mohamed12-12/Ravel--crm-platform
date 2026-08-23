"""Assembles persona/memory/state/CRM-facts into the AgentContextBundle
ProductionAgentCoordinator.think() returns each turn -- its fields end up
merged into the session context Gemini's prompt is built from (see
tool_calling_runtime.py's use of turn.context after calling
self._coordinator.think()).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.ai_agent.ai_agent_app.agent.agent_state import AgentState
from services.ai_agent.ai_agent_app.agent.memory import AgentMemory
from services.ai_agent.ai_agent_app.agent.persona import AgentPersona


@dataclass(frozen=True)
class AgentContextBundle:
    persona: dict[str, Any]
    memory: dict[str, Any]
    goal: str
    crm_facts: dict[str, Any]
    conversation: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    system_constraints: list[str]


class ContextBuilder:
    def build(
        self,
        *,
        persona: AgentPersona,
        memory: AgentMemory,
        state: AgentState,
        crm_facts: dict[str, Any],
        conversation: list[dict[str, Any]],
        tool_results: list[dict[str, Any]],
    ) -> AgentContextBundle:
        return AgentContextBundle(
            persona=persona.__dict__,
            memory=memory.snapshot(),
            goal=state.goal,
            crm_facts=dict(crm_facts),
            conversation=[dict(item) for item in conversation],
            tool_results=[dict(item) for item in tool_results],
            system_constraints=[
                "CRM writes are controlled and backend-validated",
                "No payment actions",
                "No unverified CRM claims",
                "Brand DNA is tone and audience guidance only; CRM facts and workflow policy stay authoritative",
            ],
        )
