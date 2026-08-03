from __future__ import annotations

import unittest
import uuid

from flask import Flask

from services.ai_agent.ai_agent_app.agent.gemini_agent import GeminiAgent
from services.ai_agent.ai_agent_app.agent.write_response_gating import (
    customer_message_from_write_result,
    gate_customer_write_reply,
    write_result_allows_success,
)


class Port2WriteResultResponseGatingTests(unittest.TestCase):
    def test_booking_success_response_requires_real_booking_id(self) -> None:
        missing_id = {"status": "success", "executed": True, "record_type": "booking", "record_id": ""}
        with_id = {"status": "success", "executed": True, "record_type": "booking", "record_id": "B-PORT2"}

        self.assertFalse(write_result_allows_success(missing_id, "booking"))
        self.assertTrue(write_result_allows_success(with_id, "booking"))
        self.assertIn("B-PORT2", customer_message_from_write_result(with_id, "booking"))

    def test_booking_blocked_response_does_not_claim_success(self) -> None:
        blocked = {"status": "blocked", "executed": False, "record_type": "booking", "record_id": ""}
        reply = gate_customer_write_reply(
            proposed_reply="Booking draft B-FAKE created successfully.",
            write_result=blocked,
            record_type="booking",
        )

        self.assertNotIn("B-FAKE", reply)
        self.assertNotIn("created successfully", reply.lower())
        self.assertIn("could not create", reply.lower())

    def test_booking_failed_response_does_not_claim_success(self) -> None:
        failed = {"status": "failed", "executed": False, "record_type": "booking", "record_id": ""}
        reply = gate_customer_write_reply(
            proposed_reply="Your booking has been created.",
            write_result=failed,
            record_type="booking",
        )

        self.assertNotIn("has been created", reply.lower())
        self.assertIn("could not create", reply.lower())

    def test_reused_duplicate_booking_uses_already_recorded_language(self) -> None:
        duplicate = {"status": "duplicate", "executed": False, "reused": True, "record_type": "booking", "record_id": "B-OLD"}
        reply = gate_customer_write_reply(
            proposed_reply="Booking draft B-OLD created for Paris.",
            write_result=duplicate,
            record_type="booking",
        )

        self.assertIn("B-OLD", reply)
        self.assertIn("already recorded", reply.lower())
        self.assertNotIn("created for", reply.lower())

    def test_handoff_failed_response_does_not_claim_success(self) -> None:
        failed = {"status": "failed", "executed": False, "record_type": "handoff", "record_id": ""}
        reply = gate_customer_write_reply(
            proposed_reply="Handoff case H-FAKE created.",
            write_result=failed,
            record_type="handoff",
        )

        self.assertNotIn("H-FAKE", reply)
        self.assertIn("could not submit", reply.lower())

    def test_lead_failed_response_does_not_claim_success(self) -> None:
        failed = {"status": "failed", "executed": False, "record_type": "lead", "record_id": ""}
        reply = gate_customer_write_reply(
            proposed_reply="Lead LD-FAKE created.",
            write_result=failed,
            record_type="lead",
        )

        self.assertNotIn("LD-FAKE", reply)
        self.assertIn("could not save", reply.lower())

    def test_reused_duplicate_lead_uses_already_recorded_language(self) -> None:
        duplicate = {"status": "duplicate", "executed": False, "reused": True, "record_type": "lead", "record_id": "LD-OLD"}
        reply = gate_customer_write_reply(
            proposed_reply="Lead LD-OLD created for Amina.",
            write_result=duplicate,
            record_type="lead",
        )

        self.assertIn("LD-OLD", reply)
        self.assertIn("already recorded", reply.lower())
        self.assertNotIn("created for", reply.lower())

    def test_gemini_final_reply_is_gated_by_blocked_write_result(self) -> None:
        reply = GeminiAgent._gate_reply_with_write_results(
            "Booking draft B-FAKE created successfully.",
            [
                {
                    "name": "create_booking_draft",
                    "write": True,
                    "result": {
                        "executed": False,
                        "write_result_contract": {
                            "status": "blocked",
                            "executed": False,
                            "record_type": "booking",
                            "record_id": "",
                        },
                    },
                }
            ],
            "en",
        )

        self.assertNotIn("B-FAKE", reply)
        self.assertIn("could not create", reply.lower())


class Port2DirectApiResponseTests(unittest.TestCase):
    def test_direct_booking_blocked_response_does_not_leak_internal_state_or_route(self) -> None:
        from services.ai_agent.ai_agent_app.web.api_routes import api_bp

        class FakeGateway:
            def create_booking(self, **kwargs):  # pragma: no cover - should not be called
                raise AssertionError("direct booking write should not run")

        app = Flask(f"port2-route-{uuid.uuid4().hex}")
        app.secret_key = "test-secret"
        app.config["SHEET_GATEWAY"] = FakeGateway()
        app.register_blueprint(api_bp)
        client = app.test_client()

        response = client.post(
            "/api/v1/bookings/draft",
            json={"travelerId": "TRPORT2", "tripId": "TRIP-PORT-2", "roomType": "Double"},
        )

        self.assertEqual(response.status_code, 409)
        body = response.get_data(as_text=True)
        self.assertNotIn("booking_confirmation_required", body)
        self.assertNotIn("create_booking_draft", body)
        self.assertNotIn("/api/v1/bookings/draft", body)
        payload = response.get_json()
        self.assertEqual(payload["error"], "request_not_ready")
        self.assertEqual(payload["write_result_contract"]["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
