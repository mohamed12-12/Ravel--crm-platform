from __future__ import annotations

import unittest

from services.ai_agent.ai_agent_app.agent.session_flow import SessionFlowManager


class MockGateway:
    def __init__(self, *, handoff_required: bool = False, existing_traveler: dict | None = None):
        self.handoff_required = handoff_required
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
        self.manager = SessionFlowManager(default_country_code="20")
        self.gateway = MockGateway()
        self.session = self.manager.create_session(self.gateway)

    def _drive_phone(self, session, gateway):
        self.manager.handle_message(session, "+201012345678", gateway)
        if session.stage == "awaiting_country_code":
            self.manager.handle_message(session, "20", gateway)

    def _drive_to_booking_confirmation(self, session, gateway):
        self._drive_phone(session, gateway)
        self.manager.handle_message(session, "local", gateway)
        self.manager.handle_message(session, "1", gateway)
        self.manager.handle_message(session, "double boys room", gateway)
        self.manager.handle_message(session, "With flights", gateway)
        self.manager.handle_message(session, "EGP", gateway)

    def test_happy_path_completes_after_booking_confirmation(self):
        self._drive_to_booking_confirmation(self.session, self.gateway)
        self.assertEqual(self.session.stage, "booking_created")
        self.assertEqual(self.session.booking_status, "Draft")
        self.manager.handle_message(self.session, "yes", self.gateway)
        self.assertEqual(self.session.stage, "completed")
        self.assertEqual(self.session.handoff_state, "completed")

    def test_returning_traveler_preserves_identity(self):
        self._drive_to_booking_confirmation(self.session, self.gateway)
        self.assertEqual(self.session.final_result["traveler"]["traveler_id"], "TR001")
        self.assertIsNone(self.session.final_result["write_result"]["created_traveler"])

    def test_phone_first_existing_traveler_is_recognized_without_name(self):
        self.assertEqual(self.session.customer_name, "")
        self._drive_phone(self.session, self.gateway)
        self.assertEqual(self.session.stage, "awaiting_trip_type")
        self.assertEqual(self.session.customer_name, "Amina Hassan")
        self.assertIn("Traveler ID: TR001", self.session.messages[-2]["text"])
        self.assertIn("Status: VIP", self.session.messages[-2]["text"])

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
        self.assertEqual(self.session.stage, "awaiting_trip_type")

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

    def test_session_does_not_close_before_booking_confirmation(self):
        self._drive_to_booking_confirmation(self.session, self.gateway)
        self.assertEqual(self.session.stage, "booking_created")
        self.assertNotEqual(self.session.stage, "completed")

    def test_lead_and_booking_stages_preserve_traveler_identity(self):
        self._drive_to_booking_confirmation(self.session, self.gateway)
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


if __name__ == "__main__":
    unittest.main()
