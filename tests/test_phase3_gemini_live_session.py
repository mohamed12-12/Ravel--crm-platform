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

from test_phase11_demo_features import _make_app_with_db
from test_phase2_gemini_tool_loop import (
    FakeCRMService,
    LoopProviderStub,
    function_call_response,
    text_response,
)


class DummyGeminiSessionAgent(GeminiAgent):
    def __init__(self, responses: list[dict] | None = None) -> None:
        self.responses = responses or []
        self.calls: list[dict] = []

    def respond(
        self,
        *,
        user_message: str,
        session_context: dict | None = None,
        conversation_history: list[dict] | None = None,
    ) -> dict:
        self.calls.append(
            {
                "user_message": user_message,
                "session_context": dict(session_context or {}),
                "conversation_history": list(conversation_history or []),
            }
        )
        if self.responses:
            index = min(len(self.calls) - 1, len(self.responses) - 1)
            return dict(self.responses[index])
        return {
            "reply": f"Gemini handled: {user_message}",
            "tool_requests": [],
            "mode": "gemini",
        }


class TestPhase3GeminiLiveSession(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(".tmp-test-phase3-gemini") / uuid.uuid4().hex
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.original_env = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.original_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _enable_gemini(app, agent: GeminiAgent) -> None:
        app.config["AI_AGENT_MODE"] = "gemini"
        app.config["SESSIONS"].conversation_ai = agent

    def test_deterministic_mode_still_uses_old_flow(self) -> None:
        client, _app = _make_app_with_db(self.tmp)
        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "01012345678"},
        ).get_json()["session"]

        self.assertEqual(session["stage"], "awaiting_intake")
        self.assertEqual(session["agentMode"], "deterministic")
        self.assertEqual(session["toolsUsed"], [])
        self.assertFalse(session["fallbackUsed"])

    def test_gemini_mode_routes_through_gemini_agent(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        agent = DummyGeminiSessionAgent(
            responses=[
                {
                    "reply": "Hello from Gemini mode.",
                    "tool_requests": [],
                    "mode": "gemini",
                }
            ]
        )
        self._enable_gemini(app, agent)

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "hello"},
        ).get_json()["session"]

        self.assertEqual(session["stage"], "awaiting_phone")
        self.assertEqual(session["messages"][-1]["text"], "Hello from Gemini mode.")
        self.assertEqual(session["agentMode"], "gemini")
        self.assertEqual(session["toolsUsed"], [])
        self.assertFalse(session["fallbackUsed"])
        self.assertEqual(agent.calls[0]["user_message"], "hello")

    def test_gemini_mode_can_answer_traveler_lookup(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        db_path = Path(os.environ["RAHMA_SYSTEM_DB_PATH"])
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            INSERT INTO travelers (
                traveler_id, status, full_name, phone_code,
                whatsapp_raw, integrated_whatsapp, normalized_whatsapp, phone_lookup_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "TR00088",
                "VIP",
                "Mona Ali",
                "20",
                "1112223333",
                "+201112223333",
                "+201112223333",
                "20:1112223333",
            ),
        )
        conn.commit()
        with patch(
            "services.ai_agent.ai_agent_app.agent.read_only_tools.get_system_service",
            return_value=FakeCRMService(conn),
        ):
            session = client.post("/api/session", json={}).get_json()["session"]
            agent = GeminiAgent(
                settings=SimpleNamespace(
                    ai_agent_mode="gemini",
                    default_country_code="20",
                    ai_agent_system_prompt="You are Rahvel Agent.",
                ),
                provider=LoopProviderStub(
                    [
                        function_call_response(
                            "search_traveler",
                            {"raw_phone": "1112223333", "country_code": "20"},
                        ),
                        text_response("I found Mona Ali in Rahma CRM with traveler ID TR00088."),
                    ]
                ),
            )
            self._enable_gemini(app, agent)
            session = client.post(
                f"/api/session/{session['id']}/message",
                json={"text": "Check traveler 1112223333"},
            ).get_json()["session"]

        self.assertIn("Mona Ali", session["messages"][-1]["text"])
        self.assertEqual(session["agentMode"], "gemini")
        self.assertEqual(session["toolsUsed"], ["search_traveler"])
        self.assertFalse(session["fallbackUsed"])
        conn.close()

    def test_gemini_mode_can_answer_trip_lookup(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        conn = sqlite3.connect(str(Path(os.environ["RAHMA_SYSTEM_DB_PATH"])))
        with patch(
            "services.ai_agent.ai_agent_app.agent.read_only_tools.get_system_service",
            return_value=FakeCRMService(conn),
        ):
            session = client.post("/api/session", json={}).get_json()["session"]
            agent = GeminiAgent(
                settings=SimpleNamespace(
                    ai_agent_mode="gemini",
                    default_country_code="20",
                    ai_agent_system_prompt="You are Rahvel Agent.",
                ),
                provider=LoopProviderStub(
                    [
                        function_call_response(
                            "search_trips",
                            {"trip_type": "local", "query": "Sinai"},
                        ),
                        text_response("The available local option is Sinai Trek."),
                    ]
                ),
            )
            self._enable_gemini(app, agent)
            session = client.post(
                f"/api/session/{session['id']}/message",
                json={"text": "Show me local Sinai trips"},
            ).get_json()["session"]

        self.assertIn("Sinai Trek", session["messages"][-1]["text"])
        self.assertEqual(session["toolsUsed"], ["search_trips"])
        conn.close()

    def test_gemini_failure_falls_back_safely(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        agent = DummyGeminiSessionAgent(
            responses=[
                {
                    "reply": "Automatic CRM writes are disabled in this phase. I can only validate whether the action is allowed.",
                    "tool_requests": [],
                    "mode": "gemini",
                    "error": "provider timeout",
                }
            ]
        )
        self._enable_gemini(app, agent)

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "hello"},
        ).get_json()["session"]

        self.assertTrue(session["fallbackUsed"])
        self.assertIn("try again", session["messages"][-1]["text"].lower())

    def test_write_request_rejected_in_live_session(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        agent = DummyGeminiSessionAgent(
            responses=[
                {
                    "reply": "Automatic CRM writes are disabled in this phase. I can only validate whether the action is allowed.",
                    "tool_requests": [],
                    "mode": "gemini",
                    "error": "write_request_rejected",
                }
            ]
        )
        self._enable_gemini(app, agent)

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "create lead for me"},
        ).get_json()["session"]

        self.assertEqual(
            session["messages"][-1]["text"],
            "Automatic CRM writes are disabled in this phase. I can only validate whether the action is allowed.",
        )
        self.assertFalse(session["fallbackUsed"])
        self.assertEqual(session["toolsUsed"], [])

    def test_session_memory_passed_to_gemini(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        agent = DummyGeminiSessionAgent(
            responses=[
                {"reply": "First Gemini reply.", "tool_requests": [], "mode": "gemini"},
                {"reply": "Second Gemini reply.", "tool_requests": [], "mode": "gemini"},
            ]
        )
        self._enable_gemini(app, agent)

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "hello"},
        ).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "what did I say?"},
        ).get_json()["session"]

        second_call = agent.calls[1]
        self.assertGreaterEqual(len(second_call["conversation_history"]), 3)
        self.assertEqual(second_call["conversation_history"][-1]["text"], "First Gemini reply.")
        self.assertEqual(session["messages"][-1]["text"], "Second Gemini reply.")

    def test_arabic_and_english_responses_work(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        agent = DummyGeminiSessionAgent()

        def respond_for_language(*, user_message: str, session_context=None, conversation_history=None):
            agent.calls.append(
                {
                    "user_message": user_message,
                    "session_context": dict(session_context or {}),
                    "conversation_history": list(conversation_history or []),
                }
            )
            if str((session_context or {}).get("language") or "").startswith("ar"):
                return {"reply": "أهلاً بك، كيف أساعدك اليوم؟", "tool_requests": [], "mode": "gemini"}
            return {"reply": "Hello, how can I help you today?", "tool_requests": [], "mode": "gemini"}

        agent.respond = respond_for_language  # type: ignore[method-assign]
        self._enable_gemini(app, agent)

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "مرحبا"},
        ).get_json()["session"]
        self.assertEqual(session["messages"][-1]["text"], "أهلاً بك، كيف أساعدك اليوم؟")

        second = client.post("/api/session", json={}).get_json()["session"]
        second = client.post(
            f"/api/session/{second['id']}/message",
            json={"text": "hello"},
        ).get_json()["session"]
        self.assertEqual(second["messages"][-1]["text"], "Hello, how can I help you today?")


if __name__ == "__main__":
    unittest.main()
