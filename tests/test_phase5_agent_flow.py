from __future__ import annotations

import unittest

from services.ai_agent.ai_agent_app.agent.session_flow import SessionFlowManager


class MockGateway:
    def __init__(self, *, handoff_required: bool = False, existing_traveler: dict | None = None):
        self.handoff_required = handoff_required
        self.created_handoffs: list[dict] = []
        self.last_booking_payload: dict | None = None
        self.existing_traveler = existing_traveler or {
            "traveler_id": "TR001",
            "full_name": "Amina Hassan",
            "code": "20",
            "status": "VIP",
            "local_trips_count": 2,
            "international_trips_count": 1,
            "total_trips": 3,
        }

    def get_message_copy(self, *args, **kwargs):
        return None

    def preview_customer(self, **kwargs):
        return {
            "match_status": "single_match",
            "handoff_required": self.handoff_required,
            "handoff_reason": "phone_name_conflict" if self.handoff_required else "",
            "traveler": self.existing_traveler,
            "trip_result": {
                "open_trips": [
                    {
                        "trip_id": "RT-LOC-26-001",
                        "trip_name": "Sinai Trek",
                        "start_date": "2026-09-10",
                        "end_date": "2026-09-14",
                        "remaining_places": 5,
                        "available_single": 2,
                        "available_double": 2,
                        "available_triple": 1,
                        "boys_double": 1,
                        "girls_double": 1,
                        "boys_triple": 1,
                        "girls_triple": 1,
                    }
                ],
                "date_tbd_trips": [],
            },
        }

    def run_sales_cycle(self, **kwargs):
        lead_stage = "Handoff Needed" if self.handoff_required else "Qualified"
        return {
            "traveler": self.existing_traveler,
            "trip_result": self.preview_customer().get("trip_result"),
            "write_result": {"created_traveler": None, "lead_update": {"lead_id": "LD001", "lead_stage": lead_stage}},
            "handoff_required": self.handoff_required,
            "handoff_reason": "phone_name_conflict" if self.handoff_required else "",
        }

    def create_booking(self, **kwargs):
        self.last_booking_payload = kwargs
        return {
            "booking_id": "B-100",
            "booking_status": "Draft",
            "payment_status": "Pending",
            "trip_name": "Sinai Trek",
            "room_type": kwargs.get("room_type", "Double"),
            "available_after_draft": 1,
            "write_result": {"booking_draft": {"booking_id": "B-100"}},
        }

    def get_trip_discount_notes(self, *_args, **_kwargs):
        return ""

    def traveler_has_completed_trips(self, *_args, **_kwargs):
        return False

    def create_handoff_case(self, **kwargs):
        self.created_handoffs.append(kwargs)
        return {"handoff_id": "H-00000001", "priority": kwargs.get("priority", "High"), "reason_text": kwargs.get("reason_text", "")}

    def get_visa_requirement(self, destination: str, nationality: str = ""):
        if destination.lower() == "turkey":
            return {
                "required": False,
                "summary": "Egyptian passport holders do not need a visa for short tourist visits to Turkey.",
                "disclaimer": "Always verify with the official embassy or consulate before travel.",
            }
        return {
            "required": None,
            "summary": "I need the traveler's nationality or passport country to check visa rules accurately.",
            "disclaimer": "Always verify with the official embassy or consulate before travel.",
            "needs_nationality": True,
        }


class PersonaRewriteStub:
    def __init__(self):
        self.calls: list[dict] = []

    def rewrite_message(self, **kwargs):
        self.calls.append(kwargs)
        intent = (kwargs.get("session_context") or {}).get("customer_message_intent")
        base = kwargs.get("base_text", "")
        if intent == "greeting":
            return "Hi, I am Rahvel Agent. Please share your WhatsApp number so I can check your profile safely."
        if intent == "clarification":
            return "I need your WhatsApp number to find your Rahma Traveler profile safely. Please send it when ready."
        if intent == "privacy_concern":
            return "I use it only to check your Rahma Traveler profile safely. Please share your WhatsApp number."
        return base


class BlockingGateway(MockGateway):
    def preview_customer(self, **kwargs):
        return {
            "match_status": "single_match",
            "handoff_required": True,
            "handoff_reason": "blacklisted_customer",
            "traveler": self.existing_traveler,
            "trip_result": None,
        }

    def run_sales_cycle(self, **kwargs):
        return {
            "traveler": self.existing_traveler,
            "trip_result": None,
            "write_result": {
                "created_traveler": None,
                "lead_update": {"lead_id": "LD001", "lead_stage": "Handoff Needed"},
            },
            "handoff_required": True,
            "handoff_reason": "blacklisted_customer",
        }


class ConflictGateway(MockGateway):
    def preview_customer(self, **kwargs):
        return {
            "match_status": "single_match",
            "handoff_required": True,
            "handoff_reason": "phone_name_conflict",
            "traveler": self.existing_traveler,
            "trip_result": None,
        }

    def run_sales_cycle(self, **kwargs):
        return {
            "traveler": self.existing_traveler,
            "trip_result": None,
            "write_result": {
                "created_traveler": None,
                "lead_update": {"lead_id": "LD002", "lead_stage": "Review Required"},
            },
            "handoff_required": True,
            "handoff_reason": "phone_name_conflict",
        }


class Phase5AgentFlowTests(unittest.TestCase):
    def setUp(self):
        self.manager = SessionFlowManager(default_country_code="20", handoff_keywords="human,agent,support")
        self.gateway = MockGateway()
        self.session = self.manager.create_session(self.gateway)

    def _drive_phone(self, session, gateway):
        self.manager.handle_message(session, "+201012345678", gateway)
        if session.stage == "awaiting_country_code":
            self.manager.handle_message(session, "20", gateway)

    def _drive_to_booking_completion(self, session, gateway):
        self._drive_phone(session, gateway)
        self.manager.handle_message(session, "local", gateway)
        self.manager.handle_message(session, "1", gateway)
        self.manager.handle_message(session, "double boys room", gateway)
        self.manager.handle_message(session, "With flights", gateway)
        self.manager.handle_message(session, "EGP", gateway)

    def test_happy_path_completes_after_booking_draft_creation(self):
        self._drive_to_booking_completion(self.session, self.gateway)
        self.assertEqual(self.session.stage, "post_booking_support")
        self.assertTrue(self.session.booking_completed)
        self.assertEqual(self.session.booking_status, "Draft")
        self.assertEqual(self.session.handoff_state, "completed")

    def test_returning_traveler_preserves_identity(self):
        self._drive_to_booking_completion(self.session, self.gateway)
        self.assertEqual(self.session.final_result["traveler"]["traveler_id"], "TR001")
        self.assertIsNone(self.session.final_result["write_result"]["created_traveler"])

    def test_phone_first_existing_traveler_is_recognized_without_name(self):
        self.assertEqual(self.session.customer_name, "")
        self._drive_phone(self.session, self.gateway)
        self.assertEqual(self.session.stage, "awaiting_trip_type")
        self.assertEqual(self.session.customer_name, "Amina Hassan")
        self.assertNotIn("Traveler ID", self.session.messages[-2]["text"])
        self.assertIn("traveler profile", self.session.messages[-2]["text"])
        self.assertIn("Status: VIP", self.session.messages[-2]["text"])
        self.assertIn("VIP discount", self.session.messages[-2]["text"])

    def test_awaiting_phone_clarification_does_not_switch_to_country_code(self):
        self.manager.handle_message(self.session, "what?", self.gateway)
        self.assertEqual(self.session.stage, "awaiting_phone")
        self.assertIn("WhatsApp number", self.session.messages[-1]["text"])

    def test_invalid_long_phone_is_rejected_before_lookup(self):
        self.manager.handle_message(self.session, "012922823692000", self.gateway)
        self.assertEqual(self.session.stage, "awaiting_phone")
        self.assertIn("valid WhatsApp number", self.session.messages[-1]["text"])

    def test_repeated_phone_clarification_uses_different_persona_copy(self):
        self.manager.handle_message(self.session, "why?", self.gateway)
        first_reply = self.session.messages[-1]["text"]
        self.manager.handle_message(self.session, "why?", self.gateway)
        second_reply = self.session.messages[-1]["text"]

        self.assertEqual(self.session.stage, "awaiting_phone")
        self.assertIn("WhatsApp number", second_reply)
        self.assertNotEqual(first_reply, second_reply)
        self.assertIn("Same reason", second_reply)

    def test_persona_ai_handles_phone_step_greeting_without_advancing(self):
        ai = PersonaRewriteStub()
        manager = SessionFlowManager(default_country_code="20", conversation_ai=ai)
        session = manager.create_session(self.gateway)
        ai.calls.clear()

        manager.handle_message(session, "hi", self.gateway)

        self.assertEqual(session.stage, "awaiting_phone")
        self.assertIn("Rahvel Agent", session.messages[-1]["text"])
        self.assertIn("WhatsApp number", session.messages[-1]["text"])
        self.assertEqual(ai.calls[-1]["session_context"]["customer_message_intent"], "greeting")
        self.assertEqual(ai.calls[-1]["required_action"], "Ask for the WhatsApp number.")

    def test_persona_ai_handles_phone_step_clarification_without_advancing(self):
        ai = PersonaRewriteStub()
        manager = SessionFlowManager(default_country_code="20", conversation_ai=ai)
        session = manager.create_session(self.gateway)
        ai.calls.clear()

        manager.handle_message(session, "why?", self.gateway)

        self.assertEqual(session.stage, "awaiting_phone")
        self.assertIn("profile safely", session.messages[-1]["text"])
        self.assertIn("WhatsApp number", session.messages[-1]["text"])
        self.assertEqual(ai.calls[-1]["session_context"]["customer_message_intent"], "clarification")

    def test_awaiting_country_code_clarification_stays_on_same_step(self):
        self.manager.handle_message(self.session, "+999123456789", self.gateway)
        self.assertEqual(self.session.stage, "awaiting_country_code")
        self.manager.handle_message(self.session, "what?", self.gateway)
        self.assertEqual(self.session.stage, "awaiting_country_code")
        self.assertIn("country code", self.session.messages[-1]["text"].lower())

    def test_new_traveler_goes_to_intake_before_trip_questions(self):
        gateway = MockGateway()
        gateway.preview_customer = lambda **kwargs: {  # type: ignore[method-assign]
            "match_status": "not_found",
            "handoff_required": False,
            "handoff_reason": "",
            "traveler": None,
            "trip_result": {"open_trips": [], "date_tbd_trips": []},
        }
        session = self.manager.create_session(gateway)
        self._drive_phone(session, gateway)
        self.assertEqual(session.stage, "awaiting_intake")

    def test_unclear_input_enters_clarification_without_closing(self):
        self._drive_phone(self.session, self.gateway)
        self.manager.handle_message(self.session, "maybe", self.gateway)
        self.assertEqual(self.session.stage, "awaiting_clarification")
        self.assertNotEqual(self.session.stage, "completed")

    def test_invalid_choice_recovers_to_previous_step(self):
        self._drive_phone(self.session, self.gateway)
        self.manager.handle_message(self.session, "maybe", self.gateway)
        self.assertEqual(self.session.stage, "awaiting_clarification")
        self.manager.handle_message(self.session, "local", self.gateway)
        self.assertEqual(self.session.stage, "awaiting_confirmation")

    def test_trip_type_typo_local_is_understood(self):
        self._drive_phone(self.session, self.gateway)
        self.manager.handle_message(self.session, "loca", self.gateway)
        self.assertEqual(self.session.stage, "awaiting_confirmation")
        self.assertEqual(self.session.trip_type, "local")

    def test_natural_single_trip_confirmation_is_understood(self):
        self._drive_phone(self.session, self.gateway)
        self.manager.handle_message(self.session, "local", self.gateway)
        self.manager.handle_message(self.session, "okay i need it", self.gateway)
        self.assertEqual(self.session.stage, "awaiting_room_type")
        self.assertEqual(self.session.selected_trip_id, "RT-LOC-26-001")

    def test_handoff_path_ends_in_handed_off_state(self):
        gateway = MockGateway(handoff_required=True)
        session = self.manager.create_session(gateway)
        self._drive_phone(session, gateway)
        self.assertEqual(session.stage, "handed_off")
        self.assertEqual(session.handoff_state, "handed_off")

    def test_blocked_traveler_stops_automation(self):
        gateway = BlockingGateway()
        session = self.manager.create_session(gateway)
        self._drive_phone(session, gateway)
        self.assertEqual(session.stage, "handed_off")
        self.assertEqual(session.handoff_state, "handed_off")
        self.assertEqual(session.final_result["handoff_reason"], "blacklisted_customer")

    def test_identity_conflict_stops_automation(self):
        gateway = ConflictGateway()
        session = self.manager.create_session(gateway)
        self._drive_phone(session, gateway)
        self.assertEqual(session.stage, "handed_off")
        self.assertEqual(session.handoff_state, "handed_off")
        self.assertEqual(session.final_result["handoff_reason"], "phone_name_conflict")

    def test_arabic_then_english_continuity_keeps_session_open(self):
        self.manager.handle_message(self.session, "أريد رحلة", self.gateway)
        self.assertIn(self.session.stage, {"awaiting_phone", "awaiting_trip_type", "awaiting_intake", "awaiting_country_code"})
        self.manager.handle_message(self.session, "+201012345678", self.gateway)
        self.assertNotEqual(self.session.stage, "completed")

    def test_session_closes_after_booking_draft_creation(self):
        self._drive_to_booking_completion(self.session, self.gateway)
        self.assertEqual(self.session.stage, "post_booking_support")
        self.assertTrue(self.session.booking_completed)

    def test_lead_and_booking_stages_preserve_traveler_identity(self):
        self._drive_to_booking_completion(self.session, self.gateway)
        self.assertEqual(self.session.final_result["traveler"]["traveler_id"], "TR001")
        self.assertEqual(self.session.final_result["write_result"]["lead_update"]["lead_id"], "LD001")
        self.assertEqual(self.session.selected_trip_id, "RT-LOC-26-001")

    def test_free_text_room_input_recognizes_double_and_triple(self):
        self._drive_phone(self.session, self.gateway)
        self.manager.handle_message(self.session, "local", self.gateway)
        self.manager.handle_message(self.session, "1", self.gateway)
        self.manager.handle_message(self.session, "bouble boys", self.gateway)
        self.assertEqual(self.session.room_type, "Double")
        self.assertEqual(self.session.room_group, "boys")

        other = self.manager.create_session(self.gateway)
        self._drive_phone(other, self.gateway)
        self.manager.handle_message(other, "local", self.gateway)
        self.manager.handle_message(other, "1", self.gateway)
        self.manager.handle_message(other, "triple girls", self.gateway)
        self.assertEqual(other.room_type, "Triple")
        self.assertEqual(other.room_group, "girls")

    def test_room_prompt_lists_boys_and_girls_room_choices(self):
        self._drive_phone(self.session, self.gateway)
        self.manager.handle_message(self.session, "local", self.gateway)
        self.manager.handle_message(self.session, "1", self.gateway)
        prompt = self.session.messages[-1]["text"]
        self.assertIn("Double boys room", prompt)
        self.assertIn("Double girls room", prompt)
        self.assertIn("Triple boys room", prompt)
        self.assertNotIn("1 available", prompt)
        self.assertNotIn("2 available", prompt)
        self.assertIn("group", prompt.lower())

    def test_international_trip_collects_flight_before_passport(self):
        self._drive_phone(self.session, self.gateway)
        self.manager.handle_message(self.session, "international", self.gateway)
        self.manager.handle_message(self.session, "1", self.gateway)

        self.assertEqual(self.session.stage, "awaiting_room_type")
        self.assertNotIn("passport", self.session.messages[-1]["text"].lower())

    def test_domestic_trip_without_flight_support_skips_flight_prompt(self):
        original_preview = self.gateway.preview_customer

        def preview_customer(**kwargs):
            result = original_preview(**kwargs)
            trip = result["trip_result"]["open_trips"][0]
            trip["supports_flights"] = False
            trip["type"] = "Local"
            return result

        self.gateway.preview_customer = preview_customer  # type: ignore[method-assign]
        self._drive_phone(self.session, self.gateway)
        self.manager.handle_message(self.session, "local", self.gateway)
        self.manager.handle_message(self.session, "1", self.gateway)
        self.manager.handle_message(self.session, "single room", self.gateway)

        self.assertEqual(self.session.flight_option, "Not Applicable")
        self.assertEqual(self.session.stage, "awaiting_currency")
        self.assertNotIn("flight request", self.session.messages[-1]["text"].lower())

    def test_group_booking_message_captures_group_size_before_room_choice(self):
        self._drive_phone(self.session, self.gateway)
        self.manager.handle_message(self.session, "local", self.gateway)
        self.manager.handle_message(self.session, "1", self.gateway)
        self.manager.handle_message(self.session, "we are 5 travelers", self.gateway)
        self.assertEqual(self.session.group_size, 5)
        self.assertEqual(self.session.stage, "awaiting_room_type")
        self.assertIn("group reservation for 5 travelers", self.session.messages[-1]["text"].lower())

    def test_group_size_is_written_into_booking_payload(self):
        self._drive_phone(self.session, self.gateway)
        self.manager.handle_message(self.session, "local", self.gateway)
        self.manager.handle_message(self.session, "1", self.gateway)
        self.manager.handle_message(self.session, "group booking", self.gateway)
        self.assertEqual(self.session.stage, "awaiting_group_size")
        self.manager.handle_message(self.session, "5", self.gateway)
        self.manager.handle_message(self.session, "double boys room", self.gateway)
        self.manager.handle_message(self.session, "With flights", self.gateway)
        self.manager.handle_message(self.session, "EGP", self.gateway)
        self.assertEqual(self.gateway.last_booking_payload["group_size"], 5)

    def test_flight_prompt_mentions_without_flights_default(self):
        self._drive_phone(self.session, self.gateway)
        self.manager.handle_message(self.session, "local", self.gateway)
        self.manager.handle_message(self.session, "1", self.gateway)
        self.manager.handle_message(self.session, "single room", self.gateway)
        self.assertIn("without flights by default", self.session.messages[-1]["text"].lower())

    def test_customer_can_request_human_handoff(self):
        self._drive_phone(self.session, self.gateway)
        self.manager.handle_message(self.session, "I need a human agent", self.gateway)
        self.assertEqual(self.session.stage, "handed_off")
        self.assertEqual(self.session.handoff_state, "handed_off")
        self.assertEqual(len(self.gateway.created_handoffs), 1)

    def test_visa_intent_uses_gateway_and_keeps_session_open(self):
        self.session.nationality = "Egyptian"
        self.manager.handle_message(self.session, "Do I need a visa for Turkey?", self.gateway)
        self.assertEqual(self.session.stage, "awaiting_phone")
        self.assertIn("turkey", self.session.messages[-1]["text"].lower())


if __name__ == "__main__":
    unittest.main()
