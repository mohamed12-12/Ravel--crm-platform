from __future__ import annotations

import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services.ai_agent.ai_agent_app.agent.gemini_agent import GeminiAgent
from services.ai_agent.ai_agent_app.agent.privacy_policy import AgentPrivacyPolicy
from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
from services.ai_agent.ai_agent_app.agent.tool_registry import build_agent_tool_registry
from services.ai_agent.llm.gemini_provider import GeminiProviderResponse

from test_phase2_gemini_tool_loop import FakeCRMService, LoopProviderStub, function_call_response, text_response


class TestAgentPrivacyPolicy(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE travelers (
                traveler_id TEXT PRIMARY KEY,
                status TEXT,
                full_name TEXT,
                phone_code TEXT,
                whatsapp_raw TEXT,
                integrated_whatsapp TEXT,
                normalized_whatsapp TEXT,
                phone_lookup_key TEXT,
                local_trips_count INTEGER,
                international_trips_count INTEGER,
                total_trips INTEGER
            );
            CREATE TABLE leads (
                lead_id TEXT PRIMARY KEY,
                customer_name TEXT,
                traveler_id TEXT,
                raw_phone TEXT,
                integrated_whatsapp TEXT,
                phone_lookup_key TEXT,
                created_at TEXT,
                lead_stage TEXT
            );
            CREATE TABLE trips (
                trip_id TEXT PRIMARY KEY,
                trip_name TEXT,
                type TEXT,
                start_date TEXT,
                sales_status TEXT,
                public_price TEXT,
                remaining_places INTEGER
            );
            CREATE TABLE trip_bookings (
                booking_id TEXT PRIMARY KEY,
                trip_id TEXT,
                traveler_id TEXT,
                lead_id TEXT,
                draft_created_at TEXT,
                booking_status TEXT
            );
            CREATE TABLE traveler_documents (
                document_id INTEGER PRIMARY KEY AUTOINCREMENT,
                traveler_id TEXT,
                file_name TEXT,
                category TEXT,
                file_ref TEXT,
                verification_status TEXT,
                passport_full_name TEXT,
                passport_number TEXT,
                passport_nationality TEXT,
                passport_expiry TEXT,
                uploaded_at TEXT
            );
            """
        )
        self.conn.execute(
            "INSERT INTO travelers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("TR00586", "Active", "Mohamed Ashraf Safwat", "20", "01554158741", "+201554158741", "+201554158741", "20:1554158741", 0, 0, 0),
        )
        self.conn.commit()
        self.fake_service = FakeCRMService(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def _settings(self):
        return SimpleNamespace(
            ai_agent_mode="tool_calling",
            default_country_code="20",
            ai_agent_system_prompt="You are Rahvel Agent.",
            agent_persona_name="Rahvel Agent",
            ai_max_tool_rounds=4,
            crm_access_mode="shared_service",
            gemini_api_key="",
            gemini_model="gemini-2.5-pro",
        )

    def _build_agent(self, responses: list[GeminiProviderResponse]) -> tuple[GeminiAgent, LoopProviderStub]:
        provider = LoopProviderStub(responses)
        agent = GeminiAgent(
            settings=self._settings(),
            provider=provider,
            tool_registry=build_agent_tool_registry(include_write_tools=True, include_validation_tool=False),
            write_tools_enabled=True,
        )
        return agent, provider

    def _patch_service(self):
        return patch("services.ai_agent.ai_agent_app.agent.read_only_tools.get_system_service", return_value=self.fake_service)

    def test_arabic_other_traveler_request_is_blocked_before_model(self) -> None:
        with self._patch_service():
            runtime = ToolCallingSessionRuntime(settings=self._settings())
            agent, provider = self._build_agent([text_response("I found Mohamed Ashraf.")])
            runtime._conversation_ai = agent
            session = runtime.create_session()
            session.customer_name = "Test(no22)"
            session.raw_phone = "01570652480"
            session.pending_raw_phone = "01570652480"
            session.country_code = "20"
            session.language = "ar"

            runtime.handle_message(session, "عايز بيانات محمد اشرف", gateway=None)

        self.assertEqual(provider.calls, [])
        self.assertEqual(session.tools_used, [])
        self.assertIn("لا أستطيع مشاركة بيانات أي مسافر آخر", session.messages[-1]["text"])
        self.assertNotIn("TR00586", session.messages[-1]["text"])

    def test_different_phone_after_bound_session_is_blocked_before_model(self) -> None:
        with self._patch_service():
            runtime = ToolCallingSessionRuntime(settings=self._settings())
            agent, provider = self._build_agent([text_response("I found Mohamed Ashraf.")])
            runtime._conversation_ai = agent
            session = runtime.create_session()
            session.customer_name = "Test(no22)"
            session.raw_phone = "01570652480"
            session.pending_raw_phone = "01570652480"
            session.country_code = "20"

            runtime.handle_message(session, "01554158741", gateway=None)

        self.assertEqual(provider.calls, [])
        self.assertEqual(session.tools_used, [])
        self.assertEqual(session.messages[-1]["text"], AgentPrivacyPolicy.EN_RESPONSE)

    def test_gemini_tool_call_for_other_phone_returns_privacy_block(self) -> None:
        with self._patch_service():
            agent, _provider = self._build_agent(
                [
                    function_call_response("find_traveler_by_phone", {"raw_phone": "01554158741", "country_code": "20"}),
                    text_response("Mohamed Ashraf Safwat is active with ID TR00586."),
                ]
            )
            result = agent.respond(
                user_message="look up this other traveler",
                session_context={
                    "session_id": "privacy-1",
                    "language": "en",
                    "customer_name": "Test(no22)",
                    "raw_phone": "01570652480",
                    "pending_raw_phone": "01570652480",
                    "country_code": "20",
                },
            )

        self.assertEqual(result["tool_requests"][0]["result"]["status"], "privacy_blocked")
        self.assertEqual(result["reply"], AgentPrivacyPolicy.EN_RESPONSE)
        self.assertNotIn("TR00586", result["reply"])
        self.assertNotIn("Mohamed", result["reply"])

    def test_same_phone_lookup_remains_allowed(self) -> None:
        with self._patch_service():
            agent, _provider = self._build_agent(
                [
                    function_call_response("find_traveler_by_phone", {"raw_phone": "01570652480", "country_code": "20"}),
                    text_response("I can continue with your own session."),
                ]
            )
            result = agent.respond(
                user_message="check my profile",
                session_context={
                    "session_id": "privacy-2",
                    "language": "en",
                    "customer_name": "Test(no22)",
                    "raw_phone": "01570652480",
                    "pending_raw_phone": "01570652480",
                    "country_code": "20",
                },
            )

        self.assertNotEqual(result["tool_requests"][0]["result"]["status"], "privacy_blocked")


if __name__ == "__main__":
    unittest.main()
