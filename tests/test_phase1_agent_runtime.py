from __future__ import annotations

import os
import shutil
import sqlite3
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services.ai_agent.ai_agent_app.agent.gemini_agent import GeminiAgent
from services.ai_agent.ai_agent_app.agent.session_flow import SessionFlowManager
from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
from services.ai_agent.ai_agent_app.agent.tool_registry import build_tool_calling_registry

from test_phase11_demo_features import _make_app_with_db
from test_phase2_gemini_tool_loop import LoopProviderStub, function_call_response, text_response


class TestPhase1AgentRuntime(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(".tmp-test-phase1-agent") / uuid.uuid4().hex
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.original_env = dict(os.environ)
        os.environ["AI_AGENT_MODE"] = "tool_calling"
        os.environ["AI_MAX_TOOL_ROUNDS"] = "4"

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.original_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _tool_calling_app(self):
        client, app = _make_app_with_db(self.tmp)
        app.config["AI_AGENT_MODE"] = "tool_calling"
        app.config["SESSIONS"] = ToolCallingSessionRuntime(settings=app.config["SETTINGS"])
        return client, app

    def _build_tool_agent(self, settings):
        return GeminiAgent(
            settings=settings,
            provider=LoopProviderStub([text_response("Hello from the tool-calling runtime.")]),
            tool_registry=build_tool_calling_registry(),
        )

    def test_agent_mode_uses_new_runtime(self) -> None:
        client, app = self._tool_calling_app()
        app.config["SESSIONS"]._conversation_ai = self._build_tool_agent(app.config["SETTINGS"])
        session = client.post("/api/session", json={}).get_json()["session"]
        self.assertEqual(session["agentMode"], "tool_calling")
        self.assertIsInstance(app.config["SESSIONS"], ToolCallingSessionRuntime)

    def test_deterministic_mode_still_uses_session_flow(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        app.config["AI_AGENT_MODE"] = "deterministic"
        app.config["SESSIONS"] = SessionFlowManager(
            human_handoff_phone=app.config["SETTINGS"].human_handoff_phone,
            agent_persona_name=app.config["SETTINGS"].agent_persona_name,
            website_url=app.config["SETTINGS"].website_url,
            post_trip_handoff_enabled=app.config["SETTINGS"].post_trip_handoff_enabled,
            handoff_keywords=app.config["SETTINGS"].post_trip_handoff_keywords,
            post_trip_handoff_responsible_employee=app.config["SETTINGS"].post_trip_handoff_responsible_employee,
            default_country_code=app.config["SETTINGS"].default_country_code,
            conversation_ai=None,
        )
        session = client.post("/api/session", json={}).get_json()["session"]
        self.assertEqual(session["agentMode"], "deterministic")
        self.assertEqual(app.config["SESSIONS"].__class__.__name__, "SessionFlowManager")

    def test_modes_do_not_leak_into_each_other(self) -> None:
        client, app = self._tool_calling_app()
        app.config["SESSIONS"]._conversation_ai = self._build_tool_agent(app.config["SETTINGS"])
        session = client.post("/api/session", json={}).get_json()["session"]
        app.config["AI_AGENT_MODE"] = "deterministic"
        resp = client.get(f"/api/session/{session['id']}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["session"]["agentMode"], "tool_calling")

    def test_tool_calling_can_find_traveler(self) -> None:
        client, app = self._tool_calling_app()
        db_path = Path(os.environ["RAHMA_SYSTEM_DB_PATH"])
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            INSERT INTO travelers (
                traveler_id, status, full_name, phone_code,
                whatsapp_raw, integrated_whatsapp, normalized_whatsapp, phone_lookup_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("TR00010", "Active", "Mona Ali", "20", "1112223333", "+201112223333", "+201112223333", "20:1112223333"),
        )
        conn.commit()
        conn.close()
        agent = GeminiAgent(
            settings=app.config["SETTINGS"],
            provider=LoopProviderStub(
                [
                    function_call_response("find_traveler_by_phone", {"raw_phone": "1112223333", "country_code": "20"}),
                    text_response("I found Mona Ali with traveler ID TR00010."),
                ]
            ),
            tool_registry=build_tool_calling_registry(),
        )
        app.config["SESSIONS"]._conversation_ai = agent
        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(f"/api/session/{session['id']}/message", json={"text": "find my profile"}).get_json()["session"]
        self.assertIn("Mona Ali", session["messages"][-1]["text"])
        self.assertIn("find_traveler_by_phone", session["toolsUsed"])

    def test_tool_calling_unknown_traveler(self) -> None:
        client, app = self._tool_calling_app()
        agent = GeminiAgent(
            settings=app.config["SETTINGS"],
            provider=LoopProviderStub(
                [
                    function_call_response("find_traveler_by_phone", {"raw_phone": "5550000000", "country_code": "20"}),
                    text_response("I could not find a matching traveler."),
                ]
            ),
            tool_registry=build_tool_calling_registry(),
        )
        app.config["SESSIONS"]._conversation_ai = agent
        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(f"/api/session/{session['id']}/message", json={"text": "check profile 5550000000"}).get_json()["session"]
        self.assertIn("could not find", session["messages"][-1]["text"].lower())

    def test_tool_calling_duplicate_phone(self) -> None:
        client, app = self._tool_calling_app()
        db_path = Path(os.environ["RAHMA_SYSTEM_DB_PATH"])
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            INSERT INTO travelers (
                traveler_id, status, full_name, phone_code,
                whatsapp_raw, integrated_whatsapp, normalized_whatsapp, phone_lookup_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("TR00011", "Active", "Mona Ali", "20", "1112223333", "+201112223333", "+201112223333", "20:1112223333"),
        )
        conn.execute(
            """
            INSERT INTO travelers (
                traveler_id, status, full_name, phone_code,
                whatsapp_raw, integrated_whatsapp, normalized_whatsapp, phone_lookup_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("TR00012", "Active", "Mona Ali Two", "20", "1112223333", "+201112223333", "+201112223333", "20:1112223333"),
        )
        conn.commit()
        conn.close()
        agent = GeminiAgent(
            settings=app.config["SETTINGS"],
            provider=LoopProviderStub(
                [
                    function_call_response("find_traveler_by_phone", {"raw_phone": "1112223333", "country_code": "20"}),
                    text_response("I found duplicate traveler records and need a human review."),
                ]
            ),
            tool_registry=build_tool_calling_registry(),
        )
        app.config["SESSIONS"]._conversation_ai = agent
        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(f"/api/session/{session['id']}/message", json={"text": "check profile 1112223333"}).get_json()["session"]
        self.assertIn("more than one traveler profile", session["messages"][-1]["text"].lower())

    def test_tool_calling_incomplete_profile_does_not_show_blank_labels(self) -> None:
        client, app = self._tool_calling_app()
        db_path = Path(os.environ["RAHMA_SYSTEM_DB_PATH"])
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            INSERT INTO travelers (
                traveler_id, status, full_name, phone_code,
                whatsapp_raw, integrated_whatsapp, normalized_whatsapp, phone_lookup_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("TR00013", None, None, "20", "1112220000", "+201112220000", "+201112220000", "20:1112220000"),
        )
        conn.commit()
        conn.close()
        agent = GeminiAgent(
            settings=app.config["SETTINGS"],
            provider=LoopProviderStub(
                [
                    function_call_response("get_traveler_profile", {"traveler_id": "TR00013"}),
                    text_response("The CRM record is incomplete."),
                ]
            ),
            tool_registry=build_tool_calling_registry(),
        )
        app.config["SESSIONS"]._conversation_ai = agent
        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(f"/api/session/{session['id']}/message", json={"text": "profile TR00013"}).get_json()["session"]
        self.assertNotIn("Name:", session["messages"][-1]["text"])
        self.assertIn("incomplete", session["messages"][-1]["text"].lower())

    def test_tool_calling_blocks_write_tools(self) -> None:
        client, app = self._tool_calling_app()
        agent = GeminiAgent(
            settings=app.config["SETTINGS"],
            provider=LoopProviderStub(
                [
                    function_call_response("create_lead", {"customer_name": "Test User"}),
                ]
            ),
            tool_registry={},
        )
        app.config["SESSIONS"]._conversation_ai = agent
        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(f"/api/session/{session['id']}/message", json={"text": "create lead"}).get_json()["session"]
        self.assertTrue(session["fallbackUsed"] or "create_lead" not in session["toolsUsed"])


if __name__ == "__main__":
    unittest.main()
