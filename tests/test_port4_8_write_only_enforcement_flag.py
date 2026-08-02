from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services.ai_agent.ai_agent_app.agent.gemini_agent import GeminiAgent
from services.ai_agent.ai_agent_app.config import load_settings

from test_phase2_gemini_tool_loop import LoopProviderStub, function_call_response, text_response


class _StubReadOnlyTools:
    def __init__(self) -> None:
        self.service = None

    def search_traveler(self, *args, **kwargs):
        return {}

    def lookup_lead(self, *args, **kwargs):
        return {}

    def get_passport_status(self, *args, **kwargs):
        return {}

    def get_traveler_profile(self, *args, **kwargs):
        return {}

    def get_traveler_trip_history(self, *args, **kwargs):
        return {}

    def search_trips(self, *args, **kwargs):
        return {"trips": []}

    def get_trip_details(self, *args, **kwargs):
        return {}

    def get_trip_media(self, *args, **kwargs):
        return {}

    def get_booking_status(self, *args, **kwargs):
        return {}


class Port48WriteOnlyEnforcementFlagTests(unittest.TestCase):
    def _build_agent(self, responses: list, *, write_enforcement: bool = False) -> GeminiAgent:
        settings = SimpleNamespace(
            ai_agent_mode="tool_calling",
            default_country_code="20",
            ai_agent_system_prompt="You are Rahma Traveler's sales agent.",
            gemini_model="gemini-test",
            agent_tool_router_mode="dry_run",
            agent_write_tool_enforcement=write_enforcement,
        )
        return GeminiAgent(
            settings=settings,
            provider=LoopProviderStub(responses),
            read_only_tools=_StubReadOnlyTools(),
            write_tools_enabled=True,
        )

    def test_default_flag_false_preserves_dry_run_behavior(self) -> None:
        agent = self._build_agent(
            [
                function_call_response(
                    "create_booking_draft",
                    {"trip_id": "RT-LOC-26-001", "room_type": "Double"},
                ),
                text_response("Booking request B-ALLOW has been created."),
            ],
            write_enforcement=False,
        )
        executed_calls: list[str] = []

        def fake_execute(name: str, args: dict, session_context: dict | None):
            executed_calls.append(name)
            return {
                "executed": True,
                "booking_draft": {"booking_id": "B-ALLOW"},
                "write_result_contract": {
                    "status": "success",
                    "executed": True,
                    "reused": False,
                    "record_type": "booking",
                    "record_id": "B-ALLOW",
                },
            }

        with patch.object(agent, "_execute_tool", side_effect=fake_execute):
            result = agent.respond(
                user_message="book this trip",
                session_context={
                    "session_id": "sess-port48-false",
                    "workflow_policy": {
                        "state": "trip_search_ready",
                        "allowed_tools": ["create_booking_draft"],
                        "identity_verified": True,
                    },
                },
            )

        self.assertEqual(executed_calls, ["create_booking_draft"])
        self.assertTrue(result["tool_requests"][0]["tool_route"]["would_block"])
        self.assertTrue(result["tool_requests"][0]["executed"])

    def test_flag_true_blocks_booking_write_from_trip_discovery(self) -> None:
        agent = self._build_agent(
            [
                function_call_response(
                    "create_booking_draft",
                    {"trip_id": "RT-LOC-26-001", "room_type": "Double"},
                ),
                text_response("Booking draft B-FAKE created successfully."),
            ],
            write_enforcement=True,
        )

        with patch.object(agent, "_execute_tool", side_effect=AssertionError("write tool should not execute")):
            result = agent.respond(
                user_message="book this trip",
                session_context={
                    "session_id": "sess-port48-trip-discovery",
                    "workflow_policy": {
                        "state": "trip_search_ready",
                        "allowed_tools": ["create_booking_draft"],
                        "identity_verified": True,
                    },
                },
            )

        event = result["tool_requests"][0]
        self.assertFalse(event["executed"])
        self.assertEqual(event["result"]["write_result_contract"]["status"], "blocked")
        self.assertNotIn("created successfully", result["reply"].lower())
        self.assertNotIn("B-FAKE", result["reply"])
        self.assertNotIn("B-FAKE", result["reply"])

    def test_flag_true_blocks_booking_write_from_booking_draft(self) -> None:
        agent = self._build_agent(
            [
                function_call_response(
                    "create_booking_draft",
                    {"trip_id": "RT-LOC-26-001", "room_type": "Double"},
                ),
                text_response("Booking draft B-FAKE created successfully."),
            ],
            write_enforcement=True,
        )

        with patch.object(agent, "_execute_tool", side_effect=AssertionError("write tool should not execute")):
            result = agent.respond(
                user_message="book now",
                session_context={
                    "session_id": "sess-port48-booking-draft",
                    "workflow_policy": {
                        "state": "room_type_required",
                        "allowed_tools": ["create_booking_draft"],
                        "identity_verified": True,
                    },
                },
            )

        self.assertFalse(result["tool_requests"][0]["executed"])
        self.assertEqual(result["tool_requests"][0]["result"]["write_result_contract"]["status"], "blocked")

    def test_flag_true_allows_booking_write_from_confirmation_state(self) -> None:
        agent = self._build_agent(
            [
                function_call_response(
                    "create_booking_draft",
                    {"trip_id": "RT-LOC-26-001", "room_type": "Double"},
                ),
                text_response("Booking request B-CONF has been created."),
            ],
            write_enforcement=True,
        )
        executed_calls: list[str] = []

        def fake_execute(name: str, args: dict, session_context: dict | None):
            executed_calls.append(name)
            return {
                "executed": True,
                "booking_draft": {"booking_id": "B-CONF"},
                "write_result_contract": {
                    "status": "success",
                    "executed": True,
                    "reused": False,
                    "record_type": "booking",
                    "record_id": "B-CONF",
                },
            }

        with patch.object(agent, "_execute_tool", side_effect=fake_execute):
            result = agent.respond(
                user_message="yes, confirm",
                session_context={
                    "session_id": "sess-port48-confirm",
                    "workflow_policy": {
                        "state": "booking_confirmation_required",
                        "allowed_tools": ["create_booking_draft"],
                        "identity_verified": True,
                    },
                },
            )

        self.assertEqual(executed_calls, ["create_booking_draft"])
        self.assertTrue(result["tool_requests"][0]["executed"])
        self.assertIn("B-CONF", result["reply"])

    def test_flag_true_blocks_uncontrolled_handoff_from_human_review(self) -> None:
        agent = self._build_agent(
            [
                function_call_response(
                    "create_handoff",
                    {"reason_text": "Need review"},
                ),
                text_response("Handoff request H-FAKE created successfully."),
            ],
            write_enforcement=True,
        )

        with patch.object(agent, "_execute_tool", side_effect=AssertionError("handoff should not execute")):
            result = agent.respond(
                user_message="handoff please",
                session_context={
                    "session_id": "sess-port48-review-block",
                    "workflow_policy": {
                        "state": "human_review",
                        "allowed_tools": ["create_handoff"],
                        "identity_verified": True,
                    },
                },
            )

        self.assertFalse(result["tool_requests"][0]["executed"])
        self.assertEqual(result["tool_requests"][0]["result"]["write_result_contract"]["status"], "blocked")
        self.assertNotIn("H-FAKE", result["reply"])

    def test_flag_true_allows_controlled_handoff_from_human_review(self) -> None:
        agent = self._build_agent(
            [
                function_call_response(
                    "create_handoff",
                    {"reason_text": "Duplicate phone review"},
                ),
                text_response("Handoff request H-CTRL has been created."),
            ],
            write_enforcement=True,
        )
        executed_calls: list[str] = []

        def fake_execute(name: str, args: dict, session_context: dict | None):
            executed_calls.append(name)
            return {
                "executed": True,
                "handoff_case": {"handoff_id": "H-CTRL"},
                "write_result_contract": {
                    "status": "success",
                    "executed": True,
                    "reused": False,
                    "record_type": "handoff",
                    "record_id": "H-CTRL",
                },
            }

        with patch.object(agent, "_execute_tool", side_effect=fake_execute):
            result = agent.respond(
                user_message="please connect me to a human",
                session_context={
                    "session_id": "sess-port48-review-allow",
                    "user_requested_human": True,
                    "workflow_policy": {
                        "state": "human_review",
                        "allowed_tools": ["create_handoff"],
                        "identity_verified": True,
                        "handoff_required": True,
                        "reason": "duplicate_phone_match",
                    },
                },
            )

        self.assertEqual(executed_calls, ["create_handoff"])
        self.assertTrue(result["tool_requests"][0]["executed"])
        self.assertIn("H-CTRL", result["reply"])

    def test_read_tools_still_execute_when_write_only_enforcement_is_enabled(self) -> None:
        agent = self._build_agent(
            [
                function_call_response(
                    "search_available_trips",
                    {"trip_type": "Local"},
                ),
                text_response("Here are the available trips."),
            ],
            write_enforcement=True,
        )
        executed_calls: list[str] = []

        def fake_execute(name: str, args: dict, session_context: dict | None):
            executed_calls.append(name)
            return {
                "status": "success",
                "trips": [{"trip_id": "RT-LOC-26-001", "trip_name": "Sinai Trek"}],
            }

        with patch.object(agent, "_execute_tool", side_effect=fake_execute):
            result = agent.respond(
                user_message="show trips",
                session_context={
                    "session_id": "sess-port48-read",
                    "workflow_policy": {
                        "state": "identity_required",
                        "allowed_tools": ["search_available_trips"],
                        "identity_verified": True,
                    },
                },
            )

        self.assertEqual(executed_calls, ["search_available_trips"])
        self.assertTrue(result["tool_requests"][0]["tool_route"]["would_block"])
        self.assertTrue(result["tool_requests"][0]["executed"])

    def test_blocked_result_does_not_leak_internal_terms(self) -> None:
        agent = self._build_agent(
            [
                function_call_response(
                    "create_booking_draft",
                    {"trip_id": "RT-LOC-26-001", "room_type": "Double"},
                ),
                text_response("The create_booking_draft tool is blocked in booking_confirmation_required by the router reason_code schema."),
            ],
            write_enforcement=True,
        )

        with patch.object(agent, "_execute_tool", side_effect=AssertionError("write tool should not execute")):
            result = agent.respond(
                user_message="book now",
                session_context={
                    "session_id": "sess-port48-no-leak",
                    "workflow_policy": {
                        "state": "trip_search_ready",
                        "allowed_tools": ["create_booking_draft"],
                        "identity_verified": True,
                    },
                },
            )

        lowered = result["reply"].lower()
        self.assertNotIn("create_booking_draft", lowered)
        self.assertNotIn("booking_confirmation_required", lowered)
        self.assertNotIn("reason_code", lowered)
        self.assertNotIn("b-fake", lowered)
        self.assertTrue(lowered.strip())

    def test_runtime_default_flag_remains_false(self) -> None:
        with patch.dict(os.environ, {"AGENT_WRITE_TOOL_ENFORCEMENT": ""}, clear=False):
            settings = load_settings()
        self.assertFalse(settings.agent_write_tool_enforcement)


if __name__ == "__main__":
    unittest.main()
