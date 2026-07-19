from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.ai_agent.ai_agent_app.agent.agent_state import AgentState
from services.ai_agent.ai_agent_app.agent.context_builder import ContextBuilder
from services.ai_agent.ai_agent_app.agent.memory import AgentMemory
from services.ai_agent.ai_agent_app.agent.observation import summarize_observation
from services.ai_agent.ai_agent_app.agent.persona import AgentPersona
from services.ai_agent.ai_agent_app.agent.planner import AgentPlanner
from services.ai_agent.ai_agent_app.agent.safety import AgentSafetyLayer
from services.ai_agent.ai_agent_app.agent.tool_manager import ToolManager
from services.ai_agent.ai_agent_app.logger import agent_logger


@dataclass
class ProductionAgentTurn:
    decision: dict[str, Any]
    context: dict[str, Any]
    observation: dict[str, Any] | None = None


class ProductionAgentCoordinator:
    def __init__(
        self,
        *,
        persona: AgentPersona,
        memory: AgentMemory,
        planner: AgentPlanner,
        context_builder: ContextBuilder,
        tool_manager: ToolManager,
        safety: AgentSafetyLayer,
    ) -> None:
        self.persona = persona
        self.memory = memory
        self.planner = planner
        self.context_builder = context_builder
        self.tool_manager = tool_manager
        self.safety = safety

    def think(self, state: AgentState, crm_facts: dict[str, Any], conversation: list[dict[str, Any]], tool_results: list[dict[str, Any]]) -> ProductionAgentTurn:
        state.turn_count += 1
        context = self.context_builder.build(
            persona=self.persona,
            memory=self.memory,
            state=state,
            crm_facts=crm_facts,
            conversation=conversation,
            tool_results=tool_results,
        )
        planner_context = {
            **dict(crm_facts or {}),
            "last_user_message": conversation[-1]["text"] if conversation else "",
        }
        decision = self.planner.plan(goal=state.goal, context=planner_context, tools=list(self.tool_manager.registry.keys()))
        agent_logger.info("Reasoning started turn=%s goal=%s", state.turn_count, state.goal)
        agent_logger.info("Context built turn=%s", state.turn_count)
        agent_logger.info("Planner decision turn=%s action=%s tool=%s", state.turn_count, decision.action, decision.tool_name)
        return ProductionAgentTurn(decision=decision.__dict__, context=context.__dict__)

    def observe(self, state: AgentState, tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
        observation = summarize_observation(tool_name, result).to_dict()
        state.last_tool = tool_name
        state.last_observation = observation["label"]
        self.memory.add_observation(observation)
        agent_logger.info("Observation stored turn=%s tool=%s label=%s", state.turn_count, tool_name, observation["label"])
        agent_logger.info("Memory updated turn=%s", state.turn_count)
        return observation
