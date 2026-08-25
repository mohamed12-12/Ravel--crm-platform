import pytest
from unittest.mock import MagicMock

from services.ai_agent.ai_agent_app.agent.privacy_policy import AgentPrivacyPolicy
from services.ai_agent.ai_agent_app.agent.session_flow import SessionFlowManager
from services.ai_agent.ai_agent_app.agent.session_store import SessionState
from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
from services.ai_agent.ai_agent_app.agent.workflow_policy import ConversationWorkflowPolicy, WorkflowDecision


class TestPhase1Verification:
    """Comprehensive test suite verifying Phase 1 fixes."""

    # --- 1a: Privacy Policy False Refusal ---
    def test_unverified_session_phone_lookup_not_blocked_by_privacy(self):
        """1a: Unverified session providing phone number must NOT be blocked as cross-traveler refusal."""
        session_context = {
            "workflow": {"identity_verified": False},
            "customer_phone": "201012345678",
            "traveler_id": "",
            "booking_id": "",
        }
        # Customer sends an unrecognized phone number
        user_message = "رقمي 01099998888"
        eval_result = AgentPrivacyPolicy.evaluate_user_message(user_message, session_context)
        assert eval_result is None, "Unverified session providing phone should not trigger privacy refusal"

        guard_result = AgentPrivacyPolicy.guard_tool_call(
            "find_traveler_by_phone",
            {"phone": "01099998888"},
            session_context,
        )
        assert guard_result is None, "find_traveler_by_phone tool call should not be blocked for unverified session"

    # --- 1b: Post Booking / Private Trip Intent Detection ---
    def test_post_booking_new_trip_intent_semantic_matching(self):
        """1b: Various natural phrasings for starting a new trip must be detected without hardcoded phrase lists."""
        phrases = [
            "عايز ابدأ حجز تاني",
            "لأ عايز حجز جديد",
            "عايز رحلة جديدة",
            "بدء رحلة جديدة",
            "اريد حجز اخر",
            "I want to start a new booking",
            "another trip please",
            "start new trip",
            "أيوة",  # Affirmative response
        ]
        for phrase in phrases:
            assert ToolCallingSessionRuntime._post_booking_booking_intent(phrase), f"Failed for phrase: {phrase}"
            assert ToolCallingSessionRuntime._post_booking_explicit_new_booking(phrase), f"Failed for phrase: {phrase}"

    def test_start_new_booking_resets_private_trip_state(self):
        """1b: Resetting session for new booking must clear private_trip_active and request_id."""
        session = SessionState(id="test_sess", raw_phone="201012345678")
        session.private_trip_active = True
        session.private_trip_request_id = "REQ-12345"
        session.final_result = {"private_trip_request_id": "REQ-12345", "handoff_id": "HO-123"}
        session.booking_completed = True
        session.stage = "private_request_escalated"

        runtime = MagicMock(spec=ToolCallingSessionRuntime)
        ToolCallingSessionRuntime._start_new_booking_after_completion(runtime, session)

        assert session.private_trip_active is False
        assert session.private_trip_request_id == ""
        assert session.stage == "new_booking_intent"
        assert session.booking_completed is False

    # --- 1c: Room Unavailability Alternatives ---
    def test_room_unavailability_presents_alternatives_if_available(self):
        """1c: When requested room type is over capacity, policy must offer available room alternatives if present."""
        policy = ConversationWorkflowPolicy()
        selected_trip = {
            "available_single": 0,
            "available_double": 4,
            "boys_double": 4,
            "girls_double": 4,
            "available_triple": 0,
        }
        session_context = {
            "known_traveler": {"traveler_id": "TRV-1", "status": "Active"},
            "workflow": {"identity_verified": True},
            "identity_verified": True,
            "customer_name": "Mohamed Ashraf",
            "customer_phone": "201012345678",
            "trip_type": "local",
            "selected_trip": selected_trip,
            "selected_trip_id": "TRIP-101",
            "collection_state": {
                "room_group": "boys",
                "room_type": "single",
                "group_size": 2,
            },
            "room_group": "boys",
            "room_type": "single",
            "group_size": 2,
            "traveler_id": "TRV-1",
        }
        decision = policy.evaluate(session_context)
        assert decision.state == "room_type_required"
        assert decision.handoff_required is False
        assert "Double boys" in decision.assistant_message or "Double - Boys" in decision.assistant_message

    def test_room_unavailability_escalates_when_zero_alternatives(self):
        """1c: When requested room type is over capacity AND no other room can fit the group, escalate to Ops."""
        policy = ConversationWorkflowPolicy()
        selected_trip = {
            "available_single": 0,
            "available_double": 0,
            "available_triple": 0,
        }
        session_context = {
            "known_traveler": {"traveler_id": "TRV-1", "status": "Active"},
            "workflow": {"identity_verified": True},
            "identity_verified": True,
            "customer_name": "Mohamed Ashraf",
            "customer_phone": "201012345678",
            "trip_type": "local",
            "selected_trip": selected_trip,
            "selected_trip_id": "TRIP-101",
            "collection_state": {
                "room_group": "boys",
                "room_type": "double",
                "group_size": 4,
            },
            "room_group": "boys",
            "room_type": "double",
            "group_size": 4,
            "traveler_id": "TRV-1",
        }
        decision = policy.evaluate(session_context)
        assert decision.state == "capacity_handoff_required"
        assert decision.handoff_required is True

    # --- 1d: Symmetrical / Accurate Gender Room Options ---
    def test_available_room_choices_respects_gender_counts(self):
        """1d: Room choices must ONLY include gender option when count > 0, not unconditionally."""
        flow = SessionFlowManager()
        session = SessionState(id="test_sess_room", raw_phone="201012345678")

        # Trip has 3 boys_triple, 0 girls_triple
        trip_boys_only = {
            "available_single": 0,
            "available_double": 0,
            "available_triple": 3,
            "boys_triple": 3,
            "girls_triple": 0,
        }
        flow._selected_trip = MagicMock(return_value=trip_boys_only)
        choices = flow._available_room_choices(session)

        labels = [c["label"] for c in choices]
        assert "Triple boys room" in labels
        assert "Triple girls room" not in labels, "Girls room should NOT be offered when girls count is 0"

    def test_available_room_choices_falls_back_to_total_for_untracked_pool(self):
        """1d: Room choices should include both options when pool has total capacity but gender split is untracked."""
        flow = SessionFlowManager()
        session = SessionState(id="test_sess_shared", raw_phone="201012345678")

        # Trip has available_triple=5, but gender split is not tracked (boys=0, girls=0)
        trip_untracked = {
            "available_single": 0,
            "available_double": 0,
            "available_triple": 5,
            "boys_triple": 0,
            "girls_triple": 0,
        }
        flow._selected_trip = MagicMock(return_value=trip_untracked)
        choices = flow._available_room_choices(session)

        labels = [c["label"] for c in choices]
        assert "Triple boys room" in labels
        assert "Triple girls room" in labels, "Should offer both when gender counts are untracked and total > 0"
