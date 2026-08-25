import pytest
from unittest.mock import Mock, MagicMock

from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
from services.ai_agent.ai_agent_app.agent.workflow_policy import ConversationWorkflowPolicy
from services.ai_agent.ai_agent_app.agent.session_store import SessionState
from tests.test_agent_conversation_reliability import PassiveAgent, RecordingReadTools, TRIPS


class TestPhase2VerificationSuite:
    """Verification suite for Phase 2 (Conversation Gating: 2a Discovery vs Booking & 2b Consolidated Profile Intake)."""

    @pytest.fixture
    def runtime(self):
        settings = MagicMock()
        settings.default_country_code = "20"
        settings.language = "ar"
        settings.ai_provider = "none"
        settings.gemini_api_key = ""
        rt = ToolCallingSessionRuntime(settings=settings, conversation_ai=PassiveAgent())
        rt._read_only_tools = RecordingReadTools(trips=TRIPS)
        rt._write_executor = Mock()
        return rt

    # --- Scenario 1: Unverified user -> general trip discovery -> results without WhatsApp gate ---
    def test_unverified_user_trip_discovery_returns_trips_without_whatsapp_gate(self, runtime):
        session = runtime.create_session()
        session = runtime.handle_message(session, "إيه الرحلات المتاحة؟", session)

        reply = session.messages[-1]["text"]
        assert "واتساب" not in reply, "General trip discovery must NOT demand WhatsApp number upfront"
        assert "WhatsApp" not in reply
        # Should ask for trip type or return canonical search reply
        assert any(k in reply for k in ("محلية", "دولية", "الرحلات المتاحة", "اسطنبول", "سيوة"))

    # --- Scenario 2: Unverified user -> clear booking intent -> identity/WhatsApp flow ---
    def test_unverified_user_clear_booking_intent_triggers_whatsapp_gate(self, runtime):
        session = runtime.create_session()
        session = runtime.handle_message(session, "عايز أحجز رحلة سيوة", session)

        reply = session.messages[-1]["text"]
        assert "واتساب" in reply or "WhatsApp" in reply, "Clear booking intent must trigger identity/WhatsApp gate"
        assert session.stage == "identity_required"

    # --- Scenario 3: Discovery -> trip selection -> booking only when booking intent is explicit ---
    def test_discovery_to_booking_requires_explicit_booking_intent(self, runtime):
        session = runtime.create_session()

        # Step A: Discovery query
        session = runtime.handle_message(session, "رحلات محلية", session)
        reply1 = session.messages[-1]["text"]
        assert "واتساب" not in reply1

        # Step B: Explicit booking intent
        session = runtime.handle_message(session, "احجزلي رحلة سيوة", session)
        reply2 = session.messages[-1]["text"]
        assert "واتساب" in reply2 or "WhatsApp" in reply2
        assert session.stage == "identity_required"

    # --- Scenario 4: All 4 profile fields provided in one message ---
    def test_all_profile_fields_in_one_message(self, runtime):
        session = runtime.create_session()
        session.stage = "traveler_not_found"
        session.raw_phone = "201012345678"

        msg = "اسمي محمد اشرف صفوت ومصري وتاريخ ميلادي 1995-05-10 ومسافرين 2 شباب"
        session = runtime.handle_message(session, msg, session)

        assert session.customer_name == "محمد اشرف صفوت"
        assert session.nationality in ("Egyptian", "مصري")
        assert session.birthday == "1995-05-10"
        assert session.group_size == 2
        assert session.room_group == "boys"

    # --- Scenario 5: Profile fields provided in arbitrary order ---
    def test_profile_fields_arbitrary_order(self, runtime):
        session = runtime.create_session()
        session.stage = "traveler_not_found"
        session.raw_phone = "201012345678"

        msg = "مصري وتاريخ ميلادي 1995-05-10 واسمي محمد اشرف صفوت ومسافرين 2 شباب"
        session = runtime.handle_message(session, msg, session)

        assert session.customer_name == "محمد اشرف صفوت"
        assert session.nationality in ("Egyptian", "مصري")
        assert session.birthday == "1995-05-10"
        assert session.group_size == 2

    # --- Scenario 6: Partial profile response -> ask only for missing fields ---
    def test_partial_profile_response_prompts_only_missing_fields(self, runtime):
        session = runtime.create_session()
        session.stage = "traveler_not_found"
        session.raw_phone = "201012345678"

        # Provide Name and DOB only
        msg = "اسمي محمد اشرف صفوت وتاريخ ميلادي 1995-05-10"
        session = runtime.handle_message(session, msg, session)

        assert session.customer_name == "محمد اشرف صفوت"
        assert session.birthday == "1995-05-10"
        assert session.nationality == ""

        reply = session.messages[-1]["text"]
        # Must ask for missing fields (nationality)
        assert "الجنسية" in reply or "nationality" in reply.lower()
        # Must not re-ask for full name
        assert "الاسم بالكامل" not in reply

    # --- Scenario 7: Side question + profile data in the same message ---
    def test_side_question_with_profile_data_handled_without_losing_context(self, runtime):
        session = runtime.create_session()
        session.stage = "traveler_not_found"
        session.raw_phone = "201012345678"

        msg = "اسمي محمد اشرف صفوت وتاريخ ميلادي 1995-05-10 وهل شاملة الطيران؟"
        session = runtime.handle_message(session, msg, session)

        assert session.customer_name == "محمد اشرف صفوت"
        assert session.birthday == "1995-05-10"

    # --- Scenario 8: Ambiguous message -> no unsafe state transition ---
    def test_ambiguous_message_no_unsafe_state_transition(self, runtime):
        session = runtime.create_session()
        initial_stage = session.stage

        session = runtime.handle_message(session, "يمكن مش عارف", session)
        assert session.stage != "booking_created"
        assert session.stage != "booking_confirmation_required"
