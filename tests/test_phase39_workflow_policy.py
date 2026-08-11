from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import unittest
import uuid
from pathlib import Path

from services.ai_agent.ai_agent_app.agent.gemini_agent import GeminiAgent
from services.ai_agent.ai_agent_app.agent.response_format import format_agent_reply
from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
from services.ai_agent.ai_agent_app.agent.tool_registry import build_agent_tool_registry, build_tool_calling_registry
from services.ai_agent.ai_agent_app.agent.workflow_policy import ConversationWorkflowPolicy
from services.ai_agent.ai_agent_app.server import _sync_session_lead_snapshot
from services.crm.system_services.unified_service import UnifiedCRMService

from test_phase11_demo_features import _make_app_with_db
from test_phase2_gemini_tool_loop import LoopProviderStub, function_call_response, text_response


class TestWorkflowPolicyIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(".tmp-test-phase39") / uuid.uuid4().hex
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.original_env = dict(os.environ)
        os.environ["AI_AGENT_MODE"] = "tool_calling"

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.original_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _tool_calling_app(self):
        client, app = _make_app_with_db(self.tmp)
        app.config["AI_AGENT_MODE"] = "tool_calling"
        app.config["SESSIONS"] = ToolCallingSessionRuntime(settings=app.config["SETTINGS"])
        return client, app

    @staticmethod
    def _extract_prompt_payload(provider: LoopProviderStub) -> dict:
        for call in reversed(provider.calls):
            for message in reversed(call["messages"]):
                parts = message.get("parts") if isinstance(message, dict) else None
                if parts and isinstance(parts[0], dict) and parts[0].get("text"):
                    return json.loads(parts[0]["text"])
        raise AssertionError("No prompt payload found")

    def _seed_traveler(self, full_name: str = "Mona Ali", status: str = "Active") -> None:
        db_path = Path(os.environ["RAHMA_SYSTEM_DB_PATH"])
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            INSERT INTO travelers (
                traveler_id, status, full_name, phone_code,
                whatsapp_raw, integrated_whatsapp, normalized_whatsapp, phone_lookup_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("TR00999", status, full_name, "20", "1112223333", "+201112223333", "+201112223333", "20:1112223333"),
        )
        conn.commit()
        conn.close()

    def _seed_lead(self, *, full_name: str = "Mona Ali", trip_type: str = "local") -> str:
        service = UnifiedCRMService()
        result = service.upsert_lead(
            customer_name=full_name,
            raw_phone="01112223333",
            traveler_id="TR00999",
            lead_stage="Qualified",
            lead_source="Web Demo",
            channel="web",
            preferred_trip_type=trip_type,
            interested_trip_ids="RT-LOC-26-TES",
            suggested_trip_ids="RT-LOC-26-TES",
            group_size=1,
            current_step="trip_selection_required",
            language="ar",
        )
        return str(result["lead_id"])

    def test_new_tool_calling_session_requests_phone_but_chat_is_enabled(self) -> None:
        client, _app = self._tool_calling_app()
        session = client.post("/api/session", json={}).get_json()["session"]

        self.assertEqual(session["agentMode"], "tool_calling")
        self.assertTrue(session["chat_enabled"])
        self.assertEqual(session["customer_status"], "Waiting for WhatsApp number")
        self.assertIn("WhatsApp", session["messages"][0]["text"])

    def test_trip_search_before_identity_is_workflow_blocked(self) -> None:
        client, app = self._tool_calling_app()
        provider = LoopProviderStub(
            [
                function_call_response("search_available_trips", {"trip_type": "international", "destination": "Turkey"}),
                text_response("Please share your WhatsApp number first so I can check your Rahma Traveler profile safely."),
            ]
        )
        app.config["SESSIONS"]._conversation_ai = GeminiAgent(
            settings=app.config["SETTINGS"],
            provider=provider,
            tool_registry=build_tool_calling_registry(),
        )

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(f"/api/session/{session['id']}/message", json={"text": "Show me trips to Turkey"}).get_json()["session"]
        payload = self._extract_prompt_payload(provider)

        self.assertEqual(payload["session_context"]["workflow_policy"]["state"], "identity_required")
        self.assertNotIn("trip_search", payload["crm_context"])
        self.assertIn("WhatsApp", session["messages"][-1]["text"])
        self.assertNotIn("trip_result", session.get("preview") or {})

    def test_direct_available_trip_name_is_resolved_before_identity(self) -> None:
        client, app = self._tool_calling_app()
        provider = LoopProviderStub([])
        app.config["SESSIONS"]._conversation_ai = GeminiAgent(
            settings=app.config["SETTINGS"],
            provider=provider,
            tool_registry=build_tool_calling_registry(),
        )

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "I want the Istanbul Explorer trip"},
        ).get_json()["session"]

        self.assertEqual(session["stage"], "public_trip_details")
        self.assertEqual(session["customer_status"], "Ready")
        self.assertEqual(session["toolsUsed"], ["search_trips"])
        self.assertIn("Istanbul Explorer", session["messages"][-1]["text"])
        self.assertNotIn("local trip or an international trip", session["messages"][-1]["text"].lower())
        self.assertEqual(provider.calls, [])

    def test_arabic_trip_wrapper_typo_resolves_direct_trip_reference(self) -> None:
        client, app = self._tool_calling_app()
        provider = LoopProviderStub([])
        app.config["SESSIONS"]._conversation_ai = GeminiAgent(
            settings=app.config["SETTINGS"],
            provider=provider,
            tool_registry=build_tool_calling_registry(),
        )

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "انا عايز رحله Istanbul Explorer"},
        ).get_json()["session"]

        self.assertEqual(session["stage"], "public_trip_details")
        self.assertIn("وجدت رحلة مطابقة", session["messages"][-1]["text"])
        self.assertIn("Istanbul Explorer", session["messages"][-1]["text"])
        self.assertEqual(provider.calls, [])

    def test_affirmative_reply_normalization_is_class_scoped(self) -> None:
        self.assertTrue(ToolCallingSessionRuntime._looks_affirmative("yes"))
        self.assertTrue(ToolCallingSessionRuntime._looks_affirmative("\u0646\u0639\u0645"))
        self.assertFalse(ToolCallingSessionRuntime._looks_affirmative("I need another trip"))

    def test_selected_international_trip_stays_locked_through_booking_questions(self) -> None:
        client, app = self._tool_calling_app()
        self._seed_traveler(full_name="Youssef Khaled")
        provider = LoopProviderStub(
            [
                text_response("Welcome back. Would you like a local trip or an international trip?"),
                function_call_response("search_available_trips", {"trip_type": "international"}),
                text_response(
                    "We have one available international trip:\n\n"
                    "Istanbul Explorer\nDates: October 1, 2026 to October 8, 2026\n\n"
                    "Would you like to book this trip?"
                ),
            ]
        )
        runtime = app.config["SESSIONS"]
        runtime._conversation_ai = GeminiAgent(
            settings=app.config["SETTINGS"],
            provider=provider,
            read_only_tools=runtime._read_only_tools,
            tool_registry=build_tool_calling_registry(),
        )

        session = client.post("/api/session").get_json()["session"]
        session_id = session["id"]

        session = client.post(f"/api/session/{session_id}/message", json={"text": "01112223333"}).get_json()["session"]
        self.assertEqual(session["stage"], "trip_type_required")
        # Trip type collection is backend-owned deterministic (see
        # _BACKEND_OWNED_COLLECTION_STEPS): the model is never called.
        self.assertEqual(len(provider.calls), 0)

        session = client.post(f"/api/session/{session_id}/message", json={"text": "2"}).get_json()["session"]
        self.assertEqual(session["stage"], "trip_selection_required")
        # Trip search and numbered result rendering are backend-owned.
        self.assertEqual(len(provider.calls), 0)

        expected_trip_id = "RT-INT-26-001"
        session = client.post(f"/api/session/{session_id}/message", json={"text": "yes"}).get_json()["session"]
        self.assertEqual(session["stage"], "traveler_gender_required")
        self.assertEqual(session["selectedTripId"], expected_trip_id)

        session = client.post(f"/api/session/{session_id}/message", json={"text": "1)"}).get_json()["session"]
        self.assertEqual(session["stage"], "room_type_required")
        self.assertEqual(session["roomGroup"], "boys")

        session = client.post(f"/api/session/{session_id}/message", json={"text": "1_"}).get_json()["session"]
        self.assertEqual(session["stage"], "group_size_required")
        self.assertEqual(session["roomType"], "Single")

        session = client.post(f"/api/session/{session_id}/message", json={"text": "1"}).get_json()["session"]
        self.assertEqual(session["stage"], "flight_option_required")
        self.assertEqual(session["groupSize"], 1)

        session = client.post(f"/api/session/{session_id}/message", json={"text": "without"}).get_json()["session"]
        self.assertEqual(session["stage"], "awaiting_passport_upload")
        self.assertEqual(session["tripType"], "international")
        self.assertEqual(session["selectedTripId"], expected_trip_id)
        self.assertEqual(session["selectedTripName"], "Istanbul Explorer")
        self.assertIn("passport", session["messages"][-1]["text"].lower())
        # The entire booking-collection flow (gender, room, group size, flight,
        # passport) is backend-owned deterministic - the model is never called.
        self.assertEqual(len(provider.calls), 0)
        self.assertNotIn("Sinai Trek", "\n".join(message["text"] for message in session["messages"]))

    def test_arabic_digit_phone_submission_runs_crm_lookup(self) -> None:
        client, app = self._tool_calling_app()
        self._seed_traveler()
        app.config["SESSIONS"]._conversation_ai = GeminiAgent(
            settings=app.config["SETTINGS"],
            provider=LoopProviderStub([text_response("I found your CRM profile. We can continue with your trip request.")]),
            tool_registry=build_tool_calling_registry(),
        )

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(f"/api/session/{session['id']}/message", json={"text": "رقمي ٠١١١٢٢٢٣٣٣٣"}).get_json()["session"]

        self.assertEqual(session["customer_status"], "Ready")
        self.assertEqual(session["stage"], "trip_type_required")
        self.assertIn("find_traveler_by_phone", session["toolsUsed"])
        self.assertEqual(session["preview"]["traveler"]["traveler_id"], "TR00999")

    def test_verified_traveler_without_trip_type_stays_in_trip_type_step(self) -> None:
        client, app = self._tool_calling_app()
        self._seed_traveler()
        provider = LoopProviderStub([text_response("Welcome back. Would you like a local trip or an international trip?")])
        app.config["SESSIONS"]._conversation_ai = GeminiAgent(
            settings=app.config["SETTINGS"],
            provider=provider,
            tool_registry=build_tool_calling_registry(),
        )

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(f"/api/session/{session['id']}/message", json={"text": "01112223333"}).get_json()["session"]

        # The trip-type question is a backend-owned deterministic step (see
        # _BACKEND_OWNED_COLLECTION_STEPS): it must never reach the model, so
        # there is no risk of it hallucinating a premature trip search.
        self.assertEqual(len(provider.calls), 0)
        self.assertEqual(session["stage"], "trip_type_required")
        reply_text = session["messages"][-1]["text"]
        self.assertIn("local", reply_text.lower())
        self.assertIn("international", reply_text.lower())
        self.assertNotIn("trip_search", reply_text)
        self.assertEqual(session["customer_status"], "Ready")

    def test_booking_collection_steps_are_backend_ordered(self) -> None:
        policy = ConversationWorkflowPolicy()
        base_context = {
            "workflow": {
                "lookup_status": "found",
                "identity_verified": True,
                "verified_status": "Active",
                "verified_traveler": {
                    "traveler_id": "TR00999",
                    "status": "Active",
                    "full_name": "Mona Ali",
                },
            },
            "known_traveler": {
                "traveler_id": "TR00999",
                "status": "Active",
                "full_name": "Mona Ali",
            },
            "trip_type": "international",
            "selected_trip_id": "RT-INT-26-001",
            "trip_result": {"open_trips": [{"trip_id": "RT-INT-26-001"}], "date_tbd_trips": []},
            "selected_trip": {
                "trip_id": "RT-INT-26-001",
                "trip_name": "DEmo",
                "type": "International",
                "available_single": 3,
                "available_double": 3,
                "available_triple": 3,
                "boys_double": 2,
                "girls_double": 1,
                "boys_triple": 2,
                "girls_triple": 1,
            },
            "room_group": "boys",
            "collection_state": {"room_group": True},
        }

        room_step = policy.evaluate(base_context)
        self.assertEqual(room_step.state, "room_type_required")
        self.assertNotIn("create_booking_draft", room_step.allowed_tools)
        self.assertNotIn("search_available_trips", room_step.allowed_tools)
        room_message = policy.block_tool_result("create_booking_draft", room_step)["assistant_message"]
        self.assertIn("Single", room_message)
        self.assertIn("Double boys", room_message)
        self.assertNotIn("Double girls", room_message)
        self.assertIn("Triple boys", room_message)
        self.assertNotIn("Triple girls", room_message)
        self.assertNotIn("available", room_message.lower().replace("available room options", ""))

        group_step = policy.evaluate({
            **base_context,
            "room_type": "Double",
            "collection_state": {"room_group": True, "room_type": True},
        })
        self.assertEqual(group_step.state, "group_size_required")
        self.assertNotIn("search_available_trips", group_step.allowed_tools)
        self.assertIn("how many travelers", policy.block_tool_result("create_booking_draft", group_step)["assistant_message"].lower())

        flight_step = policy.evaluate({
            **base_context,
            "room_type": "Double",
            "group_size": 1,
            "collection_state": {"room_group": True, "room_type": True, "group_size": True},
        })
        self.assertEqual(flight_step.state, "flight_option_required")
        self.assertNotIn("search_available_trips", flight_step.allowed_tools)
        self.assertIn("with flights or without flights", policy.block_tool_result("create_booking_draft", flight_step)["assistant_message"].lower())

        passport_step = policy.evaluate({
            **base_context,
            "room_type": "Double",
            "group_size": 1,
            "flight_option": "Without Flight",
            "collection_state": {"room_group": True, "room_type": True, "group_size": True, "flight_option": True},
        })
        self.assertEqual(passport_step.state, "awaiting_passport_upload")
        self.assertNotIn("search_available_trips", passport_step.allowed_tools)
        self.assertIn("passport", policy.block_tool_result("create_booking_draft", passport_step)["assistant_message"].lower())

        passport_fields_common = {
            "room_type": "Double",
            "group_size": 1,
            "flight_option": "Without Flight",
            "passport_on_file": True,
            "collection_state": {
                "room_group": True,
                "room_type": True,
                "group_size": True,
                "flight_option": True,
            },
        }

        passport_number_step = policy.evaluate({**base_context, **passport_fields_common})
        self.assertEqual(passport_number_step.state, "passport_number_required")

        passport_expiry_step = policy.evaluate({
            **base_context,
            **passport_fields_common,
            "passport_number": "A1234567",
        })
        self.assertEqual(passport_expiry_step.state, "passport_expiry_required")

        passport_country_step = policy.evaluate({
            **base_context,
            **passport_fields_common,
            "passport_number": "A1234567",
            "passport_expiry": "2030-05-01",
        })
        self.assertEqual(passport_country_step.state, "passport_country_required")

        currency_step = policy.evaluate({
            **base_context,
            **passport_fields_common,
            "passport_number": "A1234567",
            "passport_expiry": "2030-05-01",
            "passport_nationality": "Egyptian",
        })
        self.assertEqual(currency_step.state, "currency_required")
        self.assertEqual(currency_step.required_step, "collect_payment_currency")

        booking_step = policy.evaluate({
            **base_context,
            **passport_fields_common,
            "passport_number": "A1234567",
            "passport_expiry": "2030-05-01",
            "passport_nationality": "Egyptian",
            "currency": "USD",
        })
        self.assertEqual(booking_step.state, "booking_ready")
        self.assertIn("create_booking_draft", booking_step.allowed_tools)
        self.assertNotIn("search_available_trips", booking_step.allowed_tools)

    def test_new_traveler_collects_profile_details_before_lead_save(self) -> None:
        policy = ConversationWorkflowPolicy()
        base_context = {
            "workflow": {"lookup_status": "not_found"},
            "raw_phone": "01570652480",
        }

        name_step = policy.evaluate(base_context)
        self.assertEqual(name_step.state, "traveler_not_found")
        self.assertEqual(name_step.required_step, "collect_new_traveler_name")
        self.assertIn("full name", name_step.assistant_message.lower())

        nationality_step = policy.evaluate({
            **base_context,
            "customer_name": "Test(no22)",
        })
        self.assertEqual(nationality_step.state, "nationality_required")
        self.assertEqual(nationality_step.required_step, "collect_nationality")

        birthday_step = policy.evaluate({
            **base_context,
            "customer_name": "Test(no22)",
            "nationality": "Egyptian",
        })
        self.assertEqual(birthday_step.state, "birthday_required")
        self.assertEqual(birthday_step.required_step, "collect_birthday")
        self.assertIn("any clear format", birthday_step.assistant_message)
        self.assertNotIn("YYYY-MM-DD format", birthday_step.assistant_message)

        ready_step = policy.evaluate({
            **base_context,
            "customer_name": "Test(no22)",
            "nationality": "Egyptian",
            "birthday": "1998-02-20",
        })
        self.assertEqual(ready_step.state, "traveler_not_found")
        self.assertEqual(ready_step.required_step, "save_new_traveler_lead")
        self.assertIn("create_lead", ready_step.allowed_tools)

    def test_booking_currency_is_final_step_with_mixed_group_breakdown(self) -> None:
        policy = ConversationWorkflowPolicy()
        base_context = {
            "workflow": {
                "identity_verified": True,
                "verified_traveler": {
                    "traveler_id": "TR00999",
                    "status": "Active",
                    "full_name": "Mona Ali",
                    "nationality": "Egyptian",
                },
            },
            "known_traveler": {
                "traveler_id": "TR00999",
                "status": "Active",
                "full_name": "Mona Ali",
                "nationality": "Egyptian",
            },
            "trip_type": "local",
            "selected_trip_id": "RT-LOC-26-MIX",
            "selected_trip": {
                "trip_id": "RT-LOC-26-MIX",
                "type": "Local",
                "room_prices": {
                    "Double": {"EGP": "5000", "USD": "120"},
                },
                "available_double": 10,
            },
            "room_group": "boys",
            "room_type": "Double",
            "group_size": 3,
            "flight_option": "Not Applicable",
            "collection_state": {
                "room_group": True,
                "room_type": True,
                "group_size": True,
                "flight_option": True,
            },
        }

        nationality_type_step = policy.evaluate(base_context)
        self.assertEqual(nationality_type_step.state, "group_nationality_type_required")
        self.assertEqual(nationality_type_step.required_step, "collect_group_nationality_type")

        count_step = policy.evaluate({**base_context, "group_nationality_type": "mixed"})
        self.assertEqual(count_step.state, "group_nationality_counts_required")
        self.assertEqual(count_step.required_step, "collect_group_nationality_counts")

        currency_step = policy.evaluate({
            **base_context,
            "group_nationality_type": "mixed",
            "group_nationality_counts": {"egyptian": 2, "foreigner": 1},
        })
        self.assertEqual(currency_step.state, "currency_required")
        self.assertIn("10,000 EGP", currency_step.assistant_message)
        self.assertIn("$120", currency_step.assistant_message)

        booking_step = policy.evaluate({
            **base_context,
            "group_nationality_type": "mixed",
            "group_nationality_counts": {"egyptian": 2, "foreigner": 1},
            "currency": "USD",
        })
        self.assertEqual(booking_step.state, "booking_ready")

    def test_created_new_traveler_continues_to_trip_flow_after_lead_save(self) -> None:
        _client, app = self._tool_calling_app()
        runtime = app.config["SESSIONS"]
        session = runtime.create_session()
        session.customer_name = "Hend Said"
        session.raw_phone = "01224567599"
        session.pending_raw_phone = "01224567599"
        session.country_code = "20"
        session.nationality = "Egyptian"
        session.birthday = "2000-04-28"
        session.currency = "EGP"
        session.preview = {"workflow": {"lookup_status": "not_found", "identity_verified": False}}
        session.final_result = {
            "traveler": {},
            "lead_id": "LD00999",
            "write_result": {
                "created_traveler": {
                    "traveler_id": "TR00999",
                    "full_name": "Hend Said",
                    "status": "Active",
                },
                "lead_update": {"lead_id": "LD00999"},
            },
        }

        context = runtime._build_context(session, "iwant to travel")
        decision = ConversationWorkflowPolicy().evaluate(context)

        self.assertEqual(context["workflow"]["lookup_status"], "found")
        self.assertTrue(context["workflow"]["identity_verified"])
        self.assertEqual(decision.state, "trip_type_required")
        self.assertEqual(decision.required_step, "collect_trip_type")

    def test_arabic_workflow_questions_are_bilingual_and_readable(self) -> None:
        policy = ConversationWorkflowPolicy()
        verified_context = {
            "language": "ar",
            "workflow": {
                "lookup_status": "found",
                "verified_traveler": {"traveler_id": "TR00999", "status": "Active"},
            },
            "known_traveler": {"traveler_id": "TR00999", "status": "Active"},
        }

        trip_type_step = policy.evaluate(verified_context)
        self.assertIn("\u0645\u062d\u0644\u064a\u0629 (Local)", trip_type_step.assistant_message)
        self.assertIn("\u062f\u0648\u0644\u064a\u0629 (International)", trip_type_step.assistant_message)

        room_message = policy._room_inventory_prompt(
            {
                "available_single": 3,
                "boys_double": 2,
                "girls_double": 1,
                "boys_triple": 2,
                "girls_triple": 1,
            },
            arabic=True,
        )
        self.assertIn("\u0627\u0644\u062e\u064a\u0627\u0631\u0627\u062a \u0627\u0644\u0645\u062a\u0627\u062d\u0629:", room_message)
        self.assertIn("Single (\u0641\u0631\u062f\u064a\u0629)", room_message)
        self.assertIn("Double - Girls (\u062b\u0646\u0627\u0626\u064a\u0629 \u0628\u0646\u0627\u062a)", room_message)
        self.assertIn("\n4) Triple - Boys (\u062b\u0644\u0627\u062b\u064a\u0629 \u0634\u0628\u0627\u0628)", room_message)
        self.assertNotIn("CRM", room_message)
        self.assertNotIn("available", room_message)
        self.assertIn("\u0627\u0643\u062a\u0628 \u0627\u0644\u062e\u064a\u0627\u0631 \u0627\u0644\u0645\u0646\u0627\u0633\u0628 \u0623\u0648 \u0631\u0642\u0645\u0647.", room_message)

    def test_customer_reply_formatter_removes_markdown_and_preserves_lists(self) -> None:
        reply = format_agent_reply(
            "I found **DEmo** * **Dates:** July 28, 2026 * **Price:** $1000 * **Status:** Available"
        )
        self.assertEqual(
            reply,
            "I found DEmo\nDates: July 28, 2026\nPrice: $1000\nStatus: Available",
        )
        self.assertNotIn("*", reply)
        self.assertNotIn("**", reply)

    def test_customer_reply_formatter_preserves_explicit_numbers_after_item_details(self) -> None:
        reply = format_agent_reply(
            "1) DEmo\nDates: July 28, 2026 to August 10, 2026\n\n2) Today Demo\nDates: July 28, 2026 to July 30, 2026"
        )
        self.assertIn("\n1) DEmo\n", f"\n{reply}\n")
        self.assertIn("\n2) Today Demo\n", f"\n{reply}\n")

    def test_gender_filter_uses_room_occupancy_before_capacity_handoff(self) -> None:
        policy = ConversationWorkflowPolicy()
        context = {
            "workflow": {"lookup_status": "found", "identity_verified": True, "verified_status": "Active"},
            "known_traveler": {"traveler_id": "TR00999", "status": "Active"},
            "trip_type": "local",
            "selected_trip_id": "RT-LOC-26-001",
            "selected_trip": {
                "type": "Local",
                "available_single": 4,
                "available_double": 3,
                "boys_double": 2,
                "girls_double": 1,
                "available_triple": 3,
                "boys_triple": 2,
                "girls_triple": 1,
            },
            "room_group": "girls",
            "room_type": "Double",
            "group_size": 2,
            "collection_state": {"room_group": True, "room_type": True, "group_size": True},
        }
        decision = policy.evaluate(context)
        self.assertNotEqual(decision.state, "capacity_handoff_required")
        self.assertFalse(decision.handoff_required)
        room_prompt = policy._room_inventory_prompt(context["selected_trip"], room_group="girls")
        self.assertIn("Double girls", room_prompt)
        self.assertNotIn("Double boys", room_prompt)
        self.assertNotIn("1 available", room_prompt)

    def test_mixed_room_partial_availability_creates_handoff(self) -> None:
        policy = ConversationWorkflowPolicy()
        context = {
            "workflow": {"lookup_status": "found", "identity_verified": True, "verified_status": "Active"},
            "known_traveler": {"traveler_id": "TR00999", "status": "Active"},
            "trip_type": "local",
            "selected_trip_id": "RT-LOC-26-001",
            "selected_trip": {
                "type": "Local",
                "available_double": 2,
                "boys_double": 0,
                "girls_double": 2,
            },
            "room_group": "mixed",
            "room_type": "Double",
            "room_requirements": {
                "requirements": [
                    {"room_type": "Double", "room_group": "boys", "rooms": 1},
                    {"room_type": "Double", "room_group": "girls", "rooms": 1},
                ],
                "boys_rooms_requested": 1,
                "girls_rooms_requested": 1,
            },
            "group_size": 2,
            "flight_option": "Without Flight",
            "collection_state": {"room_group": True, "room_type": True, "group_size": True, "flight_option": True},
        }

        decision = policy.evaluate(context)

        self.assertEqual(decision.state, "capacity_handoff_required")
        self.assertTrue(decision.handoff_required)
        self.assertIn("Girls Double Room", decision.assistant_message)
        self.assertIn("Boys Double Room", decision.assistant_message)
        self.assertIn("not available", decision.assistant_message)

    def test_blacklisted_profile_creates_one_critical_handoff_without_gemini(self) -> None:
        client, app = self._tool_calling_app()
        self._seed_traveler(full_name="Yasmine Bassem", status="Blacklisted")
        provider = LoopProviderStub([text_response("This model response must not be used.")])
        runtime: ToolCallingSessionRuntime = app.config["SESSIONS"]
        runtime._conversation_ai = GeminiAgent(
            settings=app.config["SETTINGS"],
            provider=provider,
            read_only_tools=runtime._read_only_tools,
            tool_registry=build_tool_calling_registry(),
        )

        session = client.post("/api/session", json={}).get_json()["session"]
        session_id = session["id"]
        session = client.post(
            f"/api/session/{session_id}/message",
            json={"text": "01112223333"},
        ).get_json()["session"]

        self.assertEqual(session["stage"], "human_handoff_required")
        self.assertEqual(session["customer_status"], "Human review required")
        self.assertEqual(session["handoffState"], "handed_off")
        self.assertIn("create_handoff", session["toolsUsed"])
        self.assertEqual(provider.calls, [])

        db_path = Path(os.environ["RAHMA_SYSTEM_DB_PATH"])
        with sqlite3.connect(str(db_path)) as connection:
            row = connection.execute(
                """
                SELECT handoff_id, traveler_id, reason, priority, status
                FROM handoff_queue
                WHERE traveler_id = ?
                """,
                ("TR00999",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[1], "TR00999")
        self.assertIn("blacklisted", row[2].lower())
        self.assertEqual(row[3], "Critical")
        self.assertEqual(row[4], "Pending")

        session = client.post(
            f"/api/session/{session_id}/message",
            json={"text": "What does this mean?"},
        ).get_json()["session"]
        with sqlite3.connect(str(db_path)) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM handoff_queue WHERE traveler_id = ?",
                ("TR00999",),
            ).fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(session["stage"], "human_handoff_required")
        self.assertEqual(provider.calls, [])

    def test_policy_handoff_sets_session_state_when_api_omits_session_update(self) -> None:
        client, app = self._tool_calling_app()
        self._seed_traveler(full_name="Yasmine Bassem", status="Blacklisted")
        provider = LoopProviderStub([text_response("This model response must not be used.")])
        runtime: ToolCallingSessionRuntime = app.config["SESSIONS"]
        runtime._conversation_ai = GeminiAgent(
            settings=app.config["SETTINGS"],
            provider=provider,
            read_only_tools=runtime._read_only_tools,
            tool_registry=build_tool_calling_registry(),
        )

        class ApiWriteResultWithoutSessionUpdate:
            @staticmethod
            def execute(*, action, payload, session_context):
                self.assertEqual(action, "create_handoff")
                return {
                    "executed": True,
                    "result_id": "H-API-0001",
                    "assistant_message": "Handoff created.",
                }

        runtime._write_executor = ApiWriteResultWithoutSessionUpdate()
        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "01112223333"},
        ).get_json()["session"]

        self.assertEqual(session["stage"], "human_handoff_required")
        self.assertEqual(session["handoffState"], "handed_off")
        self.assertEqual(session["finalResult"]["handoff_id"], "H-API-0001")
        self.assertEqual(session["finalResult"]["handoff_reason"], "blacklisted_customer")
        self.assertEqual(provider.calls, [])

    def test_preferences_before_phone_are_preserved_after_identity(self) -> None:
        client, app = self._tool_calling_app()
        self._seed_traveler()
        app.config["SESSIONS"]._conversation_ai = GeminiAgent(
            settings=app.config["SETTINGS"],
            provider=LoopProviderStub(
                [
                    text_response("Please share your WhatsApp number first so I can check your CRM profile safely."),
                    text_response("Profile verified. I kept your Turkey preference for August."),
                ]
            ),
            tool_registry=build_tool_calling_registry(),
        )

        session = client.post("/api/session", json={}).get_json()["session"]
        client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "We are four people. I want an international trip to Turkey in August without flights."},
        )
        session = client.post(f"/api/session/{session['id']}/message", json={"text": "01112223333"}).get_json()["session"]

        self.assertEqual(session["tripType"], "international")
        self.assertEqual(session["tripQuery"], "Turkey")
        self.assertEqual(session["selectedTripName"], "")
        self.assertEqual(session["preferredDate"], "August")
        self.assertEqual(session["groupSize"], 4)
        # The seeded demo trips don't literally contain "Turkey" in their fields,
        # so the query-filtered search genuinely finds zero matches here. Task 3.3
        # makes that an honest "No matching trips" status instead of the generic
        # "Searching trips" a not-yet-run search would show -- this assertion is
        # about the preserved preferences above, not about search-result content.
        self.assertEqual(session["customer_status"], "No matching trips")
        self.assertEqual(session["stage"], "no_trips_available")

    def test_required_step_answers_update_session_fields(self) -> None:
        client, app = self._tool_calling_app()
        self._seed_traveler()
        app.config["SESSIONS"]._conversation_ai = GeminiAgent(
            settings=app.config["SETTINGS"],
            provider=LoopProviderStub([text_response("Profile verified. We can continue.")]),
            tool_registry=build_tool_calling_registry(),
        )

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(f"/api/session/{session['id']}/message", json={"text": "01112223333"}).get_json()["session"]

        runtime = app.config["SESSIONS"]
        runtime._sessions[session["id"]].stage = "nationality_required"
        session = client.post(f"/api/session/{session['id']}/message", json={"text": "Egyptian"}).get_json()["session"]
        self.assertEqual(session["nationality"], "Egyptian")

        runtime._sessions[session["id"]].stage = "birthday_required"
        session = client.post(f"/api/session/{session['id']}/message", json={"text": "1998-02-20"}).get_json()["session"]
        self.assertEqual(session["birthday"], "1998-02-20")

        runtime._sessions[session["id"]].stage = "currency_required"
        session = client.post(f"/api/session/{session['id']}/message", json={"text": "USD"}).get_json()["session"]
        self.assertEqual(runtime._sessions[session["id"]].currency, "USD")
        self.assertEqual(session["preview"]["collection_state"]["currency"], True)

    def test_unverified_model_crm_claim_is_grounded_to_policy_message(self) -> None:
        client, app = self._tool_calling_app()
        app.config["SESSIONS"]._conversation_ai = GeminiAgent(
            settings=app.config["SETTINGS"],
            provider=LoopProviderStub([text_response("I found your VIP profile and your status is Active.")]),
            tool_registry=build_tool_calling_registry(),
        )

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(f"/api/session/{session['id']}/message", json={"text": "Am I VIP?"}).get_json()["session"]

        self.assertIn("WhatsApp", session["messages"][-1]["text"])
        self.assertNotIn("VIP profile", session["messages"][-1]["text"])

    def test_tool_calling_can_save_a_lead_from_chat(self) -> None:
        client, app = self._tool_calling_app()
        app.config["SESSIONS"]._conversation_ai = GeminiAgent(
            settings=app.config["SETTINGS"],
            provider=LoopProviderStub(
                [
                    function_call_response(
                        "create_lead",
                        {
                            "customer_name": "Amina Hassan Salem",
                            "raw_phone": "01112223333",
                            "country_code": "20",
                            "preferred_trip_type": "international",
                            "lead_source": "Web Demo",
                            "channel": "web",
                            "notes": "Customer asked for an international trip to Turkey in August.",
                        },
                    ),
                    text_response("Lead saved successfully."),
                ]
            ),
            tool_registry=build_agent_tool_registry(include_write_tools=True, include_validation_tool=False),
            write_tools_enabled=True,
        )

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "01112223333"},
        ).get_json()["session"]
        self.assertEqual(session["stage"], "traveler_not_found")

        for text, expected_stage in (
            ("Amina Hassan Salem", "nationality_required"),
            ("Egyptian", "birthday_required"),
            ("26/05/2003", "trip_type_required"),
        ):
            session = client.post(f"/api/session/{session['id']}/message", json={"text": text}).get_json()["session"]
            self.assertEqual(session["stage"], expected_stage)

        final_result = session["finalResult"]
        self.assertIn("create_lead", session["toolsUsed"])
        self.assertIn("write_result", final_result)
        self.assertIn("lead_update", final_result["write_result"])
        self.assertTrue(final_result["write_result"]["lead_update"]["lead_id"])
        self.assertTrue(final_result["write_result"]["lead_update"]["lead_stage"])

    def test_international_trip_requires_passport_upload_before_booking_ready(self) -> None:
        decision = ConversationWorkflowPolicy().evaluate(
            {
                "workflow": {
                    "identity_verified": True,
                    "verified_traveler": {"traveler_id": "TR00999", "status": "Active", "full_name": "Mona Ali"},
                },
                "known_traveler": {"traveler_id": "TR00999", "status": "Active", "full_name": "Mona Ali"},
                "trip_type": "international",
                "selected_trip_id": "RT-INT-26-DEM",
                "selected_trip": {"trip_id": "RT-INT-26-DEM", "type": "International"},
                "collection_state": {
                    "room_group": True,
                    "room_type": True,
                    "group_size": True,
                    "flight_option": True,
                    "nationality": True,
                    "birthday": True,
                    "currency": True,
                },
                "nationality": "Egyptian",
                "birthday": "1998-02-20",
                "currency": "USD",
                "room_group": "boys",
                "passport_on_file": False,
            }
        )

        self.assertEqual(decision.state, "awaiting_passport_upload")

    def test_session_sync_updates_live_lead_snapshot_after_trip_fallback(self) -> None:
        _client, app = self._tool_calling_app()
        self._seed_traveler(full_name="youssef khaled")
        lead_id = self._seed_lead(full_name="youssef khaled", trip_type="local")
        runtime = app.config["SESSIONS"]
        session = runtime.create_session()
        session.customer_name = "youssef khaled"
        session.raw_phone = "01270482380"
        session.pending_raw_phone = "01270482380"
        session.country_code = "20"
        session.trip_type = "local"
        session.selected_trip_id = "RT-INT-26-DEM"
        session.selected_trip_name = "DEmo"
        session.group_size = 3
        session.room_type = "Single"
        session.flight_option = "Without Flight"
        session.language = "ar"
        session.stage = "awaiting_passport_upload"
        session.preview = {
            "traveler": {"traveler_id": "TR00999", "full_name": "youssef khaled", "status": "Active"},
            "trip_result": {
                "open_trips": [
                    {"trip_id": "RT-INT-26-DEM", "trip_name": "DEmo", "type": "International"},
                ],
                "date_tbd_trips": [],
            },
        }
        session.final_result = {
            "traveler": {"traveler_id": "TR00999", "full_name": "youssef khaled", "status": "Active"},
            "write_result": {"lead_update": {"lead_id": lead_id}},
        }

        sync_result = _sync_session_lead_snapshot(app.config["SHEET_GATEWAY"], session)

        self.assertEqual(sync_result["lead_id"], lead_id)
        with sqlite3.connect(os.environ["RAHMA_SYSTEM_DB_PATH"]) as conn:
            row = conn.execute(
                """
                SELECT preferred_trip_type, interested_trip_ids, suggested_trip_ids, group_size,
                       current_step, passport_status
                FROM leads
                WHERE lead_id = ?
                """,
                (lead_id,),
            ).fetchone()
        self.assertEqual(row[0], "International")
        self.assertEqual(row[1], "RT-INT-26-DEM")
        self.assertIn("RT-INT-26-DEM", row[2] or "")
        self.assertEqual(row[3], 3)
        self.assertEqual(row[4], "awaiting_passport_upload")
        self.assertEqual(row[5], "pending")

    def test_passport_upload_route_syncs_attachment_to_linked_lead(self) -> None:
        client, app = self._tool_calling_app()
        self._seed_traveler(full_name="Mona Ali")
        lead_id = self._seed_lead(full_name="Mona Ali", trip_type="international")
        session = app.config["SESSIONS"].create_session()
        session.customer_name = "Mona Ali"
        session.raw_phone = "01112223333"
        session.pending_raw_phone = "01112223333"
        session.country_code = "20"
        session.trip_type = "international"
        session.selected_trip_id = "RT-INT-26-DEM"
        session.selected_trip_name = "DEmo"
        session.group_size = 1
        session.room_type = "Single"
        session.flight_option = "Without Flight"
        session.stage = "awaiting_passport_upload"
        session.preview = {
            "traveler": {"traveler_id": "TR00999", "full_name": "Mona Ali", "status": "Active"},
            "trip_result": {
                "open_trips": [
                    {"trip_id": "RT-INT-26-DEM", "trip_name": "DEmo", "type": "International"},
                ],
                "date_tbd_trips": [],
            },
        }
        session.final_result = {
            "traveler": {"traveler_id": "TR00999", "full_name": "Mona Ali", "status": "Active"},
            "write_result": {"lead_update": {"lead_id": lead_id}},
        }

        response = client.post(
            f"/api/session/{session.id}/passport_attachment",
            data={"file": (io.BytesIO(b"passport"), "passport.jpg")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["leadSync"]["passport_attachment_ref"].endswith("passport.jpg"))
        self.assertEqual(payload["leadSync"]["passport_status"], "received")
        with sqlite3.connect(os.environ["RAHMA_SYSTEM_DB_PATH"]) as conn:
            row = conn.execute(
                """
                SELECT passport_attachment_ref, passport_status
                FROM leads
                WHERE lead_id = ?
                """,
                (lead_id,),
            ).fetchone()
        self.assertTrue((row[0] or "").endswith("passport.jpg"))
        self.assertEqual(row[1], "received")


if __name__ == "__main__":
    unittest.main()
