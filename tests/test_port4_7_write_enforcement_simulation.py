from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from services.ai_agent.ai_agent_app.agent.tool_routing_audit import (
    CanonicalAgentState,
    ToolGroup,
    ToolRouterMode,
    evaluate_tool_route,
    normalize_router_mode,
    should_enforce_tool_route,
)
from services.ai_agent.ai_agent_app.config import load_settings


class Port47WriteEnforcementSimulationTests(unittest.TestCase):
    def test_booking_write_from_trip_discovery_is_blocked_in_simulation(self) -> None:
        decision = evaluate_tool_route(
            current_state=CanonicalAgentState.TRIP_DISCOVERY,
            requested_tool_name="create_booking_draft",
            mode="enforce",
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.would_block)
        self.assertTrue(should_enforce_tool_route(decision))
        self.assertEqual(decision.tool_group, ToolGroup.BOOKING_WRITE.value)

    def test_booking_write_from_booking_draft_is_blocked_in_simulation(self) -> None:
        decision = evaluate_tool_route(
            current_state=CanonicalAgentState.BOOKING_DRAFT,
            requested_tool_name="create_booking_draft",
            mode="enforce",
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.would_block)
        self.assertTrue(should_enforce_tool_route(decision))

    def test_booking_write_from_confirmation_ready_state_is_allowed(self) -> None:
        decision = evaluate_tool_route(
            current_state=CanonicalAgentState.BOOKING_CONFIRMATION_REQUIRED,
            requested_tool_name="create_booking_draft",
            mode="enforce",
        )

        self.assertTrue(decision.allowed)
        self.assertFalse(decision.would_block)
        self.assertFalse(should_enforce_tool_route(decision))

    def test_handoff_from_human_review_is_blocked_without_controlled_reason(self) -> None:
        decision = evaluate_tool_route(
            current_state=CanonicalAgentState.HUMAN_REVIEW,
            requested_tool_name="create_handoff",
            session_context={"workflow_policy": {"state": "human_review"}},
            mode="enforce",
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.would_block)
        self.assertTrue(should_enforce_tool_route(decision))

    def test_handoff_from_human_review_is_allowed_with_controlled_reason(self) -> None:
        decision = evaluate_tool_route(
            current_state=CanonicalAgentState.HUMAN_REVIEW,
            requested_tool_name="create_handoff",
            session_context={
                "workflow_policy": {
                    "state": "human_review",
                    "handoff_required": True,
                    "reason": "duplicate_phone_match",
                }
            },
            mode="enforce",
        )

        self.assertTrue(decision.allowed)
        self.assertFalse(decision.would_block)
        self.assertFalse(should_enforce_tool_route(decision))
        self.assertEqual(decision.reason_code, "allowed_controlled_handoff_review")

    def test_handoff_from_unrelated_state_is_blocked(self) -> None:
        decision = evaluate_tool_route(
            current_state=CanonicalAgentState.TRIP_DISCOVERY,
            requested_tool_name="create_handoff",
            session_context={"workflow_policy": {"state": "trip_selection_required"}},
            mode="enforce",
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.would_block)
        self.assertTrue(should_enforce_tool_route(decision))

    def test_returning_traveler_with_open_lead_and_explicit_request_can_handoff(self) -> None:
        # Reproduces the reported production case: a returning traveler stuck in
        # new_traveler_profile_required (or any other pre-booking state) with an
        # existing lead who explicitly asks for a human should not be blocked.
        decision = evaluate_tool_route(
            current_state=CanonicalAgentState.NEW_TRAVELER_PROFILE_REQUIRED,
            requested_tool_name="create_handoff",
            session_context={"lead_id": "LD00001", "user_requested_human": True},
            mode="enforce",
        )

        self.assertTrue(decision.allowed)
        self.assertFalse(decision.would_block)
        self.assertFalse(should_enforce_tool_route(decision))
        self.assertEqual(decision.reason_code, "allowed_explicit_handoff_existing_lead")

    def test_handoff_still_blocked_without_existing_lead_even_with_explicit_request(self) -> None:
        decision = evaluate_tool_route(
            current_state=CanonicalAgentState.NEW_TRAVELER_PROFILE_REQUIRED,
            requested_tool_name="create_handoff",
            session_context={"user_requested_human": True},
            mode="enforce",
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.would_block)
        self.assertTrue(should_enforce_tool_route(decision))

    def test_handoff_still_blocked_with_existing_lead_but_no_explicit_request(self) -> None:
        # An existing lead alone is not enough -- this must not become a general
        # "returning customer" bypass. The signal has to be explicit.
        decision = evaluate_tool_route(
            current_state=CanonicalAgentState.NEW_TRAVELER_PROFILE_REQUIRED,
            requested_tool_name="create_handoff",
            session_context={"lead_id": "LD00001"},
            mode="enforce",
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.would_block)
        self.assertTrue(should_enforce_tool_route(decision))

    def test_read_tools_are_not_blocked_by_write_only_enforcement_simulation(self) -> None:
        decision = evaluate_tool_route(
            current_state=CanonicalAgentState.TRIP_DISCOVERY,
            requested_tool_name="search_available_trips",
            mode="enforce",
        )

        self.assertTrue(decision.allowed)
        self.assertFalse(decision.would_block)
        self.assertFalse(should_enforce_tool_route(decision))
        self.assertEqual(decision.tool_group, ToolGroup.TRIP_READ.value)

    def test_read_tool_would_block_does_not_become_enforced(self) -> None:
        decision = evaluate_tool_route(
            current_state=CanonicalAgentState.IDENTITY_REQUIRED,
            requested_tool_name="search_available_trips",
            mode="enforce",
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.would_block)
        self.assertFalse(should_enforce_tool_route(decision))
        self.assertEqual(decision.tool_group, ToolGroup.TRIP_READ.value)

    def test_unknown_tools_are_classified_safely(self) -> None:
        decision = evaluate_tool_route(
            current_state=CanonicalAgentState.TRAVELER_VERIFIED,
            requested_tool_name="mystery_write",
            mode="enforce",
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.would_block)
        self.assertFalse(should_enforce_tool_route(decision))
        self.assertEqual(decision.tool_group, ToolGroup.UNKNOWN.value)
        self.assertEqual(decision.safe_customer_message_key, "request_not_available")

    def test_runtime_default_router_mode_remains_dry_run(self) -> None:
        self.assertEqual(normalize_router_mode(None), ToolRouterMode.DRY_RUN)
        with patch.dict(os.environ, {"AGENT_TOOL_ROUTER_MODE": ""}, clear=False):
            settings = load_settings()
        self.assertEqual(settings.agent_tool_router_mode, "dry_run")


if __name__ == "__main__":
    unittest.main()
