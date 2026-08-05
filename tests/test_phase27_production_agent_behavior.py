from __future__ import annotations

import json
import os
import shutil
import sqlite3
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from services.ai_agent.ai_agent_app.agent import AgentSafetyLayer
from services.ai_agent.ai_agent_app.agent.gemini_agent import GeminiAgent
from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
from services.ai_agent.ai_agent_app.agent.tool_registry import build_tool_calling_registry

from test_phase11_demo_features import _make_app_with_db
from test_phase2_gemini_tool_loop import LoopProviderStub, function_call_response, text_response


class TestProductionAgentBehavior(unittest.TestCase):
    @staticmethod
    def _extract_prompt_payload(calls):
        for message in reversed(calls[-1]["messages"]):
            if isinstance(message, dict) and isinstance(message.get("parts"), list):
                parts = message["parts"]
                if parts and isinstance(parts[0], dict) and parts[0].get("text"):
                    return json.loads(parts[0]["text"])
        raise AssertionError("No Gemini prompt payload found")

    def setUp(self) -> None:
        self.tmp = Path(".tmp-test-phase27") / uuid.uuid4().hex
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.original_env = dict(os.environ)
        os.environ["AI_AGENT_MODE"] = "tool_calling"
        os.environ["AGENT_PERSONA_NAME"] = "Rahvel Agent"

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.original_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _patch_service(self):
        db_path = Path(self.tmp / "system.db").resolve()
        conn = sqlite3.connect(str(db_path))
        return conn

    def test_coordinator_is_used_in_tool_calling_mode(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        app.config["AI_AGENT_MODE"] = "tool_calling"
        app.config["SESSIONS"] = ToolCallingSessionRuntime(settings=app.config["SETTINGS"])
        agent = GeminiAgent(
            settings=app.config["SETTINGS"],
            provider=LoopProviderStub([text_response("Hello. Ask me anything.")]),
            tool_registry=build_tool_calling_registry(),
        )
        app.config["SESSIONS"]._conversation_ai = agent
        with patch.object(app.config["SESSIONS"]._coordinator, "think", wraps=app.config["SESSIONS"]._coordinator.think) as think_spy:
            session = client.post("/api/session", json={}).get_json()["session"]
            # A bare greeting before identity is a deterministic, backend-owned
            # reply (never spend a model call on "hello"), so use free-form
            # content that requires the model's interpretation instead.
            client.post(
                f"/api/session/{session['id']}/message",
                json={"text": "We are four people going to Turkey in August without flights."},
            )
        self.assertGreaterEqual(think_spy.call_count, 1)

    def test_persona_and_memory_reach_model_context(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        app.config["AI_AGENT_MODE"] = "tool_calling"
        app.config["SESSIONS"] = ToolCallingSessionRuntime(settings=app.config["SETTINGS"])
        agent = GeminiAgent(
            settings=app.config["SETTINGS"],
            provider=LoopProviderStub([text_response("Got it.")]),
            tool_registry=build_tool_calling_registry(),
        )
        app.config["SESSIONS"]._conversation_ai = agent
        session = client.post("/api/session", json={}).get_json()["session"]
        client.post(f"/api/session/{session['id']}/message", json={"text": "We are four people going to Turkey in August without flights."})
        payload = self._extract_prompt_payload(agent.provider.calls)
        self.assertIn("persona", payload["session_context"])
        self.assertIn("memory", payload["session_context"])
        self.assertEqual(payload["session_context"]["persona"]["name"], "Rahvel Agent")
        self.assertIn("customer_intent", payload["session_context"]["memory"]["short_term"])

    def test_observation_updates_memory_and_state(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        app.config["AI_AGENT_MODE"] = "tool_calling"
        app.config["SESSIONS"] = ToolCallingSessionRuntime(settings=app.config["SETTINGS"])
        agent = GeminiAgent(
            settings=app.config["SETTINGS"],
            provider=LoopProviderStub(
                [
                    function_call_response("search_available_trips", {"trip_type": "international", "query": "Turkey"}, call_id="call-1", thought_signature="sig-1"),
                    text_response("I found Turkey Explorer."),
                ]
            ),
            tool_registry=build_tool_calling_registry(),
        )
        app.config["SESSIONS"]._conversation_ai = agent
        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(f"/api/session/{session['id']}/message", json={"text": "Show me trips to Turkey"}).get_json()["session"]

        runtime: ToolCallingSessionRuntime = app.config["SESSIONS"]
        agent_state = runtime._state_by_session[session["id"]]
        self.assertEqual(agent_state.last_observation, "Trip matched")
        self.assertTrue(runtime._memory.observations)
        self.assertEqual(session["toolsUsed"], ["search_available_trips"])

    def test_safety_rejects_write_tools(self) -> None:
        safety = AgentSafetyLayer()
        ok, reason = safety.validate_tool_args("create_lead", {"customer_name": "Test"}, allowed_tools={"find_traveler_by_phone"})
        self.assertFalse(ok)
        self.assertIn("unsupported", reason.lower())

    def test_memory_prevents_repeated_questions(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        app.config["AI_AGENT_MODE"] = "tool_calling"
        app.config["SESSIONS"] = ToolCallingSessionRuntime(settings=app.config["SETTINGS"])
        agent = GeminiAgent(
            settings=app.config["SETTINGS"],
            provider=LoopProviderStub([text_response("I have enough information now."), text_response("I can already see the preferences you shared.")]),
            tool_registry=build_tool_calling_registry(),
        )
        app.config["SESSIONS"]._conversation_ai = agent
        session = client.post("/api/session", json={}).get_json()["session"]
        client.post(f"/api/session/{session['id']}/message", json={"text": "We are four people going to Turkey in August without flights."})
        client.post(f"/api/session/{session['id']}/message", json={"text": "Available trips?"})
        payload = self._extract_prompt_payload(agent.provider.calls)
        self.assertEqual(payload["session_context"]["group_size"], 4)
        self.assertEqual(payload["session_context"]["trip_type"], "international")

    def test_unsupported_tools_do_not_pass_safety(self) -> None:
        safety = AgentSafetyLayer()
        ok, reason = safety.validate_tool_args("create_booking", {"trip_id": "RT1"}, allowed_tools={"search_available_trips"})
        self.assertFalse(ok)
        self.assertIn("unsupported", reason.lower())


if __name__ == "__main__":
    unittest.main()
