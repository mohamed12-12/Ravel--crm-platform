from __future__ import annotations

import unittest
from types import SimpleNamespace

from services.ai_agent.ai_agent_app.agent.gemini_agent import GeminiAgent
from services.ai_agent.ai_agent_app.agent.tool_registry import build_agent_tool_registry
from services.ai_agent.ai_agent_app.agent.tool_routing_audit import (
    CanonicalAgentState,
    ToolGroup,
    canonical_state_from_context,
    evaluate_tool_route,
    should_enforce_tool_route,
)
from test_phase2_gemini_tool_loop import LoopProviderStub, function_call_response, text_response


class Port4StateMappingTests(unittest.TestCase):
    def test_latest_workflow_states_map_to_canonical_states(self) -> None:
        cases = {
            "identity_required": CanonicalAgentState.IDENTITY_REQUIRED,
            "trip_type_required": CanonicalAgentState.TRIP_DISCOVERY,
            "trip_selection_required": CanonicalAgentState.TRIP_DISCOVERY,
            "traveler_gender_required": CanonicalAgentState.TRIP_SELECTED,
            "room_type_required": CanonicalAgentState.BOOKING_DRAFT,
            "traveler_not_found": CanonicalAgentState.NEW_TRAVELER_PROFILE_REQUIRED,
            "nationality_required": CanonicalAgentState.NEW_TRAVELER_PROFILE_REQUIRED,
            "birthday_required": CanonicalAgentState.NEW_TRAVELER_PROFILE_REQUIRED,
            "currency_required": CanonicalAgentState.NEW_TRAVELER_PROFILE_REQUIRED,
            "booking_ready": CanonicalAgentState.BOOKING_CONFIRMATION_REQUIRED,
            "booking_confirmation_required": CanonicalAgentState.BOOKING_CONFIRMATION_REQUIRED,
            "human_handoff_required": CanonicalAgentState.HANDOFF_PENDING,
            "duplicate_traveler_detected": CanonicalAgentState.HUMAN_REVIEW,
            "post_booking_support": CanonicalAgentState.POST_BOOKING_SUPPORT,
        }
        for latest_state, canonical in cases.items():
            with self.subTest(latest_state=latest_state):
                self.assertEqual(
                    canonical_state_from_context({"workflow_policy": {"state": latest_state}}),
                    canonical,
                )

    def test_context_fallback_maps_verified_and_selected_trip(self) -> None:
        self.assertEqual(
            canonical_state_from_context({"traveler_id": "TR1"}),
            CanonicalAgentState.TRAVELER_VERIFIED,
        )
        self.assertEqual(
            canonical_state_from_context({"traveler_id": "TR1", "trip_type": "local"}),
            CanonicalAgentState.TRIP_DISCOVERY,
        )
        self.assertEqual(
            canonical_state_from_context({"traveler_id": "TR1", "selected_trip_id": "RT1"}),
            CanonicalAgentState.TRIP_SELECTED,
        )
        self.assertEqual(
            canonical_state_from_context(
                {"traveler_id": "TR1", "selected_trip_id": "RT1", "collection_state": {"room_type": True}}
            ),
            CanonicalAgentState.BOOKING_DRAFT,
        )


class Port4ToolRoutingAuditTests(unittest.TestCase):
    def test_identity_required_would_block_trip_and_booking_tools(self) -> None:
        trip = evaluate_tool_route(
            current_state=CanonicalAgentState.IDENTITY_REQUIRED,
            requested_tool_name="search_available_trips",
            mode="dry_run",
        )
        booking = evaluate_tool_route(
            current_state=CanonicalAgentState.IDENTITY_REQUIRED,
            requested_tool_name="create_booking_draft",
            mode="dry_run",
        )

        self.assertFalse(trip.allowed)
        self.assertTrue(trip.would_block)
        self.assertEqual(trip.tool_group, ToolGroup.TRIP_READ.value)
        self.assertFalse(booking.allowed)
        self.assertTrue(booking.would_block)
        self.assertEqual(booking.tool_group, ToolGroup.BOOKING_WRITE.value)

    def test_trip_discovery_would_block_booking_write(self) -> None:
        decision = evaluate_tool_route(
            current_state=CanonicalAgentState.TRIP_DISCOVERY,
            requested_tool_name="create_booking_draft",
            mode="dry_run",
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.would_block)
        self.assertEqual(decision.reason_code, "tool_group_not_allowed_for_state")

    def test_booking_confirmation_required_allows_booking_write_audit(self) -> None:
        decision = evaluate_tool_route(
            current_state=CanonicalAgentState.BOOKING_CONFIRMATION_REQUIRED,
            requested_tool_name="create_booking_draft",
            mode="dry_run",
        )

        self.assertTrue(decision.allowed)
        self.assertFalse(decision.would_block)

    def test_human_review_would_block_write_tools(self) -> None:
        lead = evaluate_tool_route(
            current_state=CanonicalAgentState.HUMAN_REVIEW,
            requested_tool_name="create_lead",
            mode="dry_run",
        )
        booking = evaluate_tool_route(
            current_state=CanonicalAgentState.HUMAN_REVIEW,
            requested_tool_name="create_booking_draft",
            mode="dry_run",
        )

        self.assertTrue(lead.would_block)
        self.assertTrue(booking.would_block)

        handoff = evaluate_tool_route(
            current_state=CanonicalAgentState.HUMAN_REVIEW,
            requested_tool_name="create_handoff",
            mode="dry_run",
        )
        self.assertTrue(handoff.would_block)

    def test_new_traveler_profile_required_allows_controlled_lead_write(self) -> None:
        decision = evaluate_tool_route(
            current_state=CanonicalAgentState.NEW_TRAVELER_PROFILE_REQUIRED,
            requested_tool_name="create_lead",
            mode="dry_run",
        )

        self.assertTrue(decision.allowed)
        self.assertFalse(decision.would_block)

    def test_human_review_allows_controlled_handoff_only_with_precondition(self) -> None:
        duplicate = evaluate_tool_route(
            requested_tool_name="create_handoff",
            session_context={
                "workflow_policy": {
                    "state": "duplicate_traveler_detected",
                    "handoff_required": True,
                    "reason": "duplicate_phone_match",
                }
            },
            mode="dry_run",
        )
        capacity = evaluate_tool_route(
            requested_tool_name="create_handoff",
            session_context={
                "workflow_policy": {
                    "state": "capacity_handoff_required",
                    "handoff_required": True,
                    "reason": "room_capacity",
                }
            },
            mode="dry_run",
        )
        unsupported = evaluate_tool_route(
            current_state=CanonicalAgentState.HUMAN_REVIEW,
            requested_tool_name="create_handoff",
            session_context={"workflow_policy": {"handoff_required": True, "reason": "unsupported_request"}},
            mode="dry_run",
        )
        unrelated = evaluate_tool_route(
            current_state=CanonicalAgentState.HUMAN_REVIEW,
            requested_tool_name="create_handoff",
            session_context={"workflow_policy": {"state": "human_review"}},
            mode="dry_run",
        )

        self.assertTrue(duplicate.allowed)
        self.assertFalse(duplicate.would_block)
        self.assertEqual(duplicate.reason_code, "allowed_controlled_handoff_review")
        self.assertTrue(capacity.allowed)
        self.assertFalse(capacity.would_block)
        self.assertTrue(unsupported.allowed)
        self.assertFalse(unsupported.would_block)
        self.assertFalse(unrelated.allowed)
        self.assertTrue(unrelated.would_block)

    def test_handoff_pending_allows_handoff_write(self) -> None:
        decision = evaluate_tool_route(
            current_state=CanonicalAgentState.HANDOFF_PENDING,
            requested_tool_name="create_handoff",
            mode="dry_run",
        )

        self.assertTrue(decision.allowed)
        self.assertFalse(decision.would_block)

    def test_unknown_tool_would_block_in_audit(self) -> None:
        decision = evaluate_tool_route(
            current_state=CanonicalAgentState.TRAVELER_VERIFIED,
            requested_tool_name="magic_write",
            mode="dry_run",
        )

        self.assertEqual(decision.tool_group, ToolGroup.UNKNOWN.value)
        self.assertTrue(decision.would_block)
        self.assertEqual(decision.safe_customer_message_key, "request_not_available")

    def test_enforce_decision_is_unit_only_and_write_scoped(self) -> None:
        write_decision = evaluate_tool_route(
            current_state=CanonicalAgentState.TRIP_DISCOVERY,
            requested_tool_name="create_booking_draft",
            mode="enforce",
        )
        read_decision = evaluate_tool_route(
            current_state=CanonicalAgentState.IDENTITY_REQUIRED,
            requested_tool_name="search_available_trips",
            mode="enforce",
        )

        self.assertTrue(should_enforce_tool_route(write_decision))
        self.assertFalse(should_enforce_tool_route(read_decision))


class Port4GeminiRuntimeAuditTests(unittest.TestCase):
    def test_dry_run_audit_does_not_block_tool_execution_when_workflow_allows_it(self) -> None:
        class FakeReadOnlyTools:
            service = object()

            def search_available_trips(self, **kwargs):
                return {"status": "success", "open_trips": [{"trip_id": "RT-DRY-RUN"}], "executed": True}

        provider = LoopProviderStub(
            [
                function_call_response("search_available_trips", {"trip_type": "international"}),
                text_response("Please share your WhatsApp number first so I can check your profile safely."),
            ]
        )
        agent = GeminiAgent(
            settings=SimpleNamespace(
                ai_agent_mode="gemini",
                agent_tool_router_mode="dry_run",
                default_country_code="20",
                ai_agent_system_prompt="You are Ravel Agent.",
                gemini_model="test-model",
            ),
            provider=provider,
            read_only_tools=FakeReadOnlyTools(),
            action_validator=SimpleNamespace(),
            tool_registry=build_agent_tool_registry(include_write_tools=False),
            max_tool_calls=2,
        )

        result = agent.respond(
            user_message="Show me international trips",
            session_context={
                "session_id": "port4-dry-run",
                "workflow_policy": {
                    "state": "identity_required",
                    "allowed_tools": ["search_available_trips"],
                },
            },
        )

        event = result["tool_requests"][0]
        self.assertEqual(event["result"]["status"], "success")
        self.assertEqual(event["tool_route"]["state"], CanonicalAgentState.IDENTITY_REQUIRED.value)
        self.assertEqual(event["tool_route"]["tool_group"], ToolGroup.TRIP_READ.value)
        self.assertTrue(event["tool_route"]["would_block"])


if __name__ == "__main__":
    unittest.main()
