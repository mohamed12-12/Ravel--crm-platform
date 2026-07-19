from __future__ import annotations

import unittest
from types import SimpleNamespace

from services.ai_agent.ai_agent_app.agent import (
    AgentMemory,
    AgentPersona,
    AgentPlanner,
    AgentState,
    ContextBuilder,
    ProductionAgentCoordinator,
)
from services.ai_agent.ai_agent_app.agent.observation import summarize_observation
from services.ai_agent.ai_agent_app.agent.safety import AgentSafetyLayer
from services.ai_agent.ai_agent_app.agent.tool_manager import ToolManager
from services.ai_agent.ai_agent_app.agent.tool_registry import build_tool_calling_registry


class TestProductionAgentArchitecture(unittest.TestCase):
    def test_persona_loading(self) -> None:
        persona = AgentPersona.from_settings(SimpleNamespace(agent_persona_name="Rahvel Agent"))
        self.assertEqual(persona.name, "Rahvel Agent")
        self.assertIn("sales assistant", persona.company_identity.lower())

    def test_memory_update(self) -> None:
        memory = AgentMemory()
        memory.update_short_term(goal="help")
        memory.update_long_term(traveler_id="TR1")
        memory.add_observation({"label": "Traveler found"})
        snapshot = memory.snapshot()
        self.assertEqual(snapshot["short_term"]["goal"], "help")
        self.assertEqual(snapshot["long_term"]["traveler_id"], "TR1")
        self.assertEqual(snapshot["observations"][0]["label"], "Traveler found")

    def test_planner_selects_tool(self) -> None:
        planner = AgentPlanner()
        decision = planner.plan(goal="help", context={"last_user_message": "show me trips to turkey"}, tools=["search_available_trips"])
        self.assertEqual(decision.action, "tool")
        self.assertEqual(decision.tool_name, "search_available_trips")

    def test_context_builder(self) -> None:
        persona = AgentPersona.from_settings(SimpleNamespace(agent_persona_name="Rahvel Agent"))
        memory = AgentMemory()
        state = AgentState(goal="help")
        bundle = ContextBuilder().build(
            persona=persona,
            memory=memory,
            state=state,
            crm_facts={"traveler": {"traveler_id": "TR1"}},
            conversation=[{"role": "user", "text": "hi"}],
            tool_results=[],
        )
        self.assertEqual(bundle.goal, "help")
        self.assertEqual(bundle.crm_facts["traveler"]["traveler_id"], "TR1")
        self.assertIn("CRM writes are controlled and backend-validated", bundle.system_constraints)

    def test_safety_layer(self) -> None:
        safety = AgentSafetyLayer()
        ok, error = safety.validate_tool_args("search_available_trips", {"trip_type": "local"})
        self.assertTrue(ok)
        self.assertEqual(error, "")

    def test_tool_manager_describes_tools(self) -> None:
        manager = ToolManager(build_tool_calling_registry())
        descriptions = manager.describe()
        self.assertTrue(descriptions)
        self.assertEqual(descriptions[0].permissions, "read")

    def test_agent_state_transitions(self) -> None:
        state = AgentState(goal="help")
        state.add_pending_task("collect trip type")
        state.mark_task_completed("collect trip type")
        state.last_tool = "search_available_trips"
        state.last_observation = "Trip matched"
        data = state.to_dict()
        self.assertEqual(data["last_tool"], "search_available_trips")
        self.assertEqual(data["last_observation"], "Trip matched")
        self.assertEqual(data["pending_tasks"], [])

    def test_observation_storage(self) -> None:
        obs = summarize_observation("search_available_trips", {"open_trips": [{"trip_id": "RT1"}]})
        self.assertEqual(obs.label, "Trip matched")
        self.assertIn("tool", obs.details)

    def test_phase1_compatibility(self) -> None:
        registry = build_tool_calling_registry()
        self.assertIn("search_available_trips", registry)
        self.assertNotIn("create_lead", registry)

    def test_coordinator_observation_updates_memory(self) -> None:
        persona = AgentPersona.from_settings(SimpleNamespace(agent_persona_name="Rahvel Agent"))
        memory = AgentMemory()
        state = AgentState(goal="help")
        coordinator = ProductionAgentCoordinator(
            persona=persona,
            memory=memory,
            planner=AgentPlanner(),
            context_builder=ContextBuilder(),
            tool_manager=ToolManager(build_tool_calling_registry()),
            safety=AgentSafetyLayer(),
        )
        turn = coordinator.think(
            state,
            crm_facts={"trip_type": "international"},
            conversation=[{"role": "user", "text": "show trips"}],
            tool_results=[],
        )
        obs = coordinator.observe(state, "search_available_trips", {"open_trips": [{"trip_id": "RT1"}]})
        self.assertEqual(turn.decision["action"], "tool")
        self.assertEqual(obs["label"], "Trip matched")
        self.assertTrue(memory.observations)


if __name__ == "__main__":
    unittest.main()
