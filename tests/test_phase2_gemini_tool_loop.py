from __future__ import annotations

import json
import sqlite3
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from services.ai_agent.ai_agent_app.agent.gemini_agent import GeminiAgent
from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
from services.ai_agent.ai_agent_app.server import _extract_gemini_message_hints
from services.ai_agent.llm.gemini_provider import GeminiProviderResponse
from services.ai_agent.validation.validation_rules import normalize_flight_option, normalize_trip_type


class FakeCRMService:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.connection.row_factory = sqlite3.Row

    @contextmanager
    def connect(self):
        yield self.connection

    def normalize_phone(self, raw_phone: str, country_code: str = "20") -> dict[str, str]:
        digits = "".join(ch for ch in raw_phone if ch.isdigit())
        local_number = digits[-10:] if len(digits) >= 10 else digits
        lookup_key = f"{country_code}:{local_number}" if country_code and local_number else ""
        normalized = f"+{country_code}{local_number}" if country_code and local_number else ""
        return {
            "raw_phone": raw_phone,
            "country_code": country_code,
            "local_number": local_number,
            "normalized_whatsapp": normalized,
            "lookup_key": lookup_key,
        }

    def lookup_key_variants(self, phone: dict[str, str]) -> list[str]:
        return [phone.get("lookup_key", ""), phone.get("normalized_whatsapp", "")] if phone else []

    def resolve_identity(self, full_name: str, raw_phone: str, country_code: str = "20"):
        phone = self.normalize_phone(raw_phone, country_code)
        row = self.connection.execute(
            "SELECT * FROM travelers WHERE phone_lookup_key = ?",
            (phone["lookup_key"],),
        ).fetchone()
        if row is None:
            return SimpleNamespace(
                lookup_phone=phone,
                match_status="not_found",
                handoff_required=False,
                handoff_reason="",
                name_match_status="",
                actions=["collect_new_traveler_data"],
                traveler=None,
            )
        return SimpleNamespace(
            lookup_phone=phone,
            match_status="single_match",
            handoff_required=False,
            handoff_reason="",
            name_match_status="matched",
            actions=["continue_sales_flow"],
            traveler=dict(row),
        )

    def build_trip_result(self, trip_type: str | None, today=None):
        trip_type = (trip_type or "").strip().casefold()
        rows = self.connection.execute("SELECT * FROM trips ORDER BY trip_name ASC").fetchall()
        open_trips = []
        date_tbd_trips = []
        for row in rows:
            record = dict(row)
            if trip_type and str(record.get("type", "")).casefold() != trip_type:
                continue
            if record.get("start_date"):
                open_trips.append(record)
            else:
                date_tbd_trips.append(record)
        return {"open_trips": open_trips, "date_tbd_trips": date_tbd_trips}


class LoopProviderStub:
    def __init__(self, responses: list[GeminiProviderResponse]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.responses) == 1:
            return self.responses[0]
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


def function_call_response(
    name: str,
    args: dict[str, object],
    response_id: str = "resp-fn",
    *,
    call_id: str = "call-1",
    thought_signature: str = "sig-1",
) -> GeminiProviderResponse:
    return GeminiProviderResponse(
        text="",
        response_id=response_id,
        raw={
            "responseId": response_id,
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"functionCall": {"name": name, "args": args, "id": call_id}, "thoughtSignature": thought_signature},
                        ]
                    }
                }
            ],
        },
        usage_metadata={"inputTokens": 1, "outputTokens": 1},
    )


def text_response(text: str, response_id: str = "resp-text", *, finish_reason: str = "STOP") -> GeminiProviderResponse:
    return GeminiProviderResponse(
        text=text,
        response_id=response_id,
        raw={
            "responseId": response_id,
            "candidates": [
                {
                    "finishReason": finish_reason,
                    "content": {
                        "parts": [
                            {"text": text},
                        ]
                    }
                }
            ],
        },
        usage_metadata={"inputTokens": 1, "outputTokens": 1},
        finish_reason=finish_reason,
    )


class TestPhase2GeminiToolLoop(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(
            """
            CREATE TABLE travelers (
                traveler_id TEXT PRIMARY KEY,
                full_name TEXT,
                status TEXT,
                passport_name TEXT,
                passport_number TEXT,
                passport_expiry TEXT,
                passport_nationality TEXT,
                passport_attachment_ref TEXT,
                integrated_whatsapp TEXT,
                raw_phone TEXT,
                phone_lookup_key TEXT
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
            (
                "TR00001",
                "Mona Ali",
                "VIP",
                "Mona Ali",
                "A1234567",
                "2030-05-01",
                "Egyptian",
                "passport/mona.jpg",
                "+201112223333",
                "1112223333",
                "20:1112223333",
            ),
        )
        self.conn.execute(
            "INSERT INTO trips VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("RT-LOC-26-001", "Siwa Escape", "Local", "2026-09-10", "Open", "2000$", 5),
        )
        self.conn.execute(
            "INSERT INTO trips VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("RT-INT-26-001", "Georgia Discovery", "International", None, "Date TBD", "2500$", 4),
        )
        self.conn.execute(
            "INSERT INTO leads VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("LD00001", "Mona Ali", "TR00001", "1112223333", "+201112223333", "20:1112223333", "2026-06-28T10:00:00", "Qualified"),
        )
        self.conn.execute(
            "INSERT INTO trip_bookings VALUES (?, ?, ?, ?, ?, ?)",
            ("B00001", "RT-LOC-26-001", "TR00001", "LD00001", "2026-06-28T11:00:00", "Draft"),
        )
        self.conn.execute(
            "INSERT INTO traveler_documents (traveler_id, file_name, category, file_ref, verification_status, passport_full_name, passport_number, passport_nationality, passport_expiry, uploaded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("TR00001", "passport.jpg", "passport", "passport/mona.jpg", "pending", "Mona Ali", "A1234567", "Egyptian", "2030-05-01", "2026-06-28T11:00:00"),
        )
        self.conn.commit()
        self.fake_service = FakeCRMService(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def _build_agent(self, responses: list[GeminiProviderResponse], max_tool_calls: int = 4) -> tuple[GeminiAgent, LoopProviderStub]:
        provider = LoopProviderStub(responses)
        agent = GeminiAgent(
            settings=SimpleNamespace(
                ai_agent_mode="gemini",
                default_country_code="20",
                ai_agent_system_prompt="You are Rahvel Agent.",
            ),
            provider=provider,
            max_tool_calls=max_tool_calls,
        )
        return agent, provider

    def _patch_service(self):
        return patch("services.ai_agent.ai_agent_app.agent.read_only_tools.get_system_service", return_value=self.fake_service)

    def test_gemini_requests_traveler_lookup(self) -> None:
        with self._patch_service():
            agent, provider = self._build_agent(
                [
                    function_call_response("search_traveler", {"raw_phone": "1112223333", "country_code": "20"}),
                    text_response("I found Mona Ali in the CRM."),
                ]
            )
            result = agent.respond(user_message="find my profile", session_context={"session_id": "sess-1", "raw_phone": "1112223333"})

        self.assertEqual(result["tool_requests"][0]["name"], "search_traveler")
        self.assertIn("Mona Ali", result["reply"])
        self.assertGreaterEqual(len(provider.calls), 2)
        first_parts = provider.calls[1]["messages"][-1]["parts"]
        self.assertEqual(first_parts[0]["functionResponse"]["id"], "call-1")

    def test_token_limit_finish_retries_once_with_complete_replacement(self) -> None:
        with self._patch_service():
            agent, provider = self._build_agent(
                [
                    text_response("يمكنني مساعدتك في الاستفسار عن رحلة Today Demo أو", "resp-cut", finish_reason="MAX_TOKENS"),
                    text_response("يمكنني مساعدتك في الاستفسار عن رحلة Today Demo. هل تريد متابعة الحجز الحالي؟", "resp-retry"),
                ]
            )
            result = agent.respond(
                user_message="كيف يمكنك مساعدتي؟",
                session_context={"session_id": "sess-retry", "language": "ar", "selected_trip_name": "Today Demo"},
            )

        self.assertEqual(result["reply"], "يمكنني مساعدتك في الاستفسار عن رحلة Today Demo. هل تريد متابعة الحجز الحالي؟")
        self.assertNotIn(" أو", result["reply"])
        self.assertEqual(result["response_id"], "resp-retry")
        self.assertEqual(len(provider.calls), 2)
        retry_payload = json.loads(provider.calls[1]["messages"][0]["parts"][0]["text"])
        self.assertEqual(retry_payload["task"], "replace_incomplete_customer_reply")
        self.assertIn("partial_incomplete_reply", retry_payload)

    def test_two_incomplete_model_attempts_return_language_fallback(self) -> None:
        with self._patch_service():
            agent, provider = self._build_agent(
                [
                    text_response("I can help you with Today Demo and", "resp-cut", finish_reason="MAX_TOKENS"),
                    text_response("I can help you with Today Demo and", "resp-cut-2", finish_reason="MAX_TOKENS"),
                ]
            )
            result = agent.respond(
                user_message="how can you help?",
                session_context={"session_id": "sess-fallback", "language": "en", "selected_trip_name": "Today Demo"},
            )

        self.assertEqual(result["reply"], "Sorry, I couldn\u2019t prepare that response properly. Could you try that again?")
        self.assertEqual(len(provider.calls), 2)

    def test_thought_signature_is_preserved_in_followup_turn(self) -> None:
        with self._patch_service():
            agent, provider = self._build_agent(
                [
                    function_call_response(
                        "search_traveler",
                        {"raw_phone": "1112223333", "country_code": "20"},
                        call_id="call-42",
                        thought_signature="sig-42",
                    ),
                    text_response("I found Mona Ali in the CRM."),
                ]
            )
            agent.respond(user_message="find my profile", session_context={"session_id": "sess-sig", "raw_phone": "1112223333"})

        model_turn = provider.calls[1]["messages"][-2]
        self.assertEqual(model_turn["role"], "model")
        self.assertIn("thoughtSignature", model_turn["parts"][0])
        self.assertEqual(model_turn["parts"][0]["thoughtSignature"], "sig-42")
        self.assertEqual(provider.calls[1]["messages"][-1]["parts"][0]["functionResponse"]["id"], "call-42")

    def test_missing_thought_signature_fails_safely(self) -> None:
        with self._patch_service():
            agent, _ = self._build_agent(
                [
                    GeminiProviderResponse(
                        text="",
                        response_id="resp-no-sig",
                        raw={
                            "responseId": "resp-no-sig",
                            "candidates": [
                                {
                                    "content": {
                                        "parts": [
                                            {"functionCall": {"name": "search_traveler", "args": {"raw_phone": "1112223333", "country_code": "20"}, "id": "call-missing"}},
                                        ]
                                    }
                                }
                            ],
                        },
                    )
                ]
            )
            result = agent.respond(user_message="find my profile", session_context={"session_id": "sess-missing"})

        self.assertEqual(
            result["reply"],
            "I cannot save changes automatically in this step. I can only check whether the action is allowed.",
        )
        self.assertEqual(result["error"], "tool_request_failed")

    def test_gemini_requests_trip_search(self) -> None:
        with self._patch_service():
            agent, _ = self._build_agent(
                [
                    function_call_response("search_trips", {"trip_type": "Local", "query": "Siwa"}),
                    text_response("I found Siwa Escape for you."),
                ]
            )
            result = agent.respond(user_message="show local trips", session_context={"session_id": "sess-2", "trip_type": "Local"})

        self.assertEqual(result["tool_requests"][0]["name"], "search_trips")
        self.assertIn("Siwa Escape", result["reply"])

    def test_gemini_requests_booking_lookup(self) -> None:
        with self._patch_service():
            agent, _ = self._build_agent(
                [
                    function_call_response("get_booking_status", {"booking_id": "B00001"}),
                    text_response("Booking B00001 is currently Draft."),
                ]
            )
            result = agent.respond(user_message="check booking status", session_context={"session_id": "sess-3", "booking_id": "B00001"})

        self.assertEqual(result["tool_requests"][0]["name"], "get_booking_status")
        self.assertIn("Draft", result["reply"])

    def test_invalid_tool_name_rejected(self) -> None:
        with self._patch_service():
            agent, _ = self._build_agent([function_call_response("create_booking", {"trip_id": "RT-LOC-26-001"})])
            result = agent.respond(user_message="book this", session_context={"session_id": "sess-4"})

        self.assertEqual(
            result["reply"],
            "I cannot save changes automatically in this step. I can only check whether the action is allowed.",
        )
        self.assertEqual(result["error"], "tool_request_failed")

    def test_invalid_tool_input_rejected(self) -> None:
        with self._patch_service():
            agent, _ = self._build_agent([function_call_response("search_traveler", {"country_code": "20"})])
            result = agent.respond(user_message="find profile", session_context={"session_id": "sess-5"})

        self.assertEqual(
            result["reply"],
            "I cannot save changes automatically in this step. I can only check whether the action is allowed.",
        )
        self.assertEqual(result["error"], "tool_request_failed")

    def test_write_tool_request_rejected(self) -> None:
        with self._patch_service():
            agent, _ = self._build_agent([function_call_response("update_traveler", {"traveler_id": "TR00001"})])
            result = agent.respond(user_message="update this traveler", session_context={"session_id": "sess-6"})

        self.assertEqual(
            result["reply"],
            "I cannot save changes automatically in this step. I can only check whether the action is allowed.",
        )
        self.assertEqual(result["error"], "tool_request_failed")

    def test_max_tool_call_limit_enforced(self) -> None:
        with self._patch_service():
            agent, _ = self._build_agent(
                [
                    function_call_response("search_traveler", {"raw_phone": "1112223333", "country_code": "20"}),
                    function_call_response("search_traveler", {"raw_phone": "1112223333", "country_code": "20"}),
                ],
                max_tool_calls=1,
            )
            result = agent.respond(user_message="find profile", session_context={"session_id": "sess-7"})

        self.assertEqual(
            result["reply"],
            "I cannot save changes automatically in this step. I can only check whether the action is allowed.",
        )
        self.assertEqual(result["error"], "tool_request_failed")

    def test_max_tool_call_limit_uses_workflow_message_when_available(self) -> None:
        with self._patch_service():
            agent, _ = self._build_agent(
                [
                    function_call_response("search_traveler", {"raw_phone": "1112223333", "country_code": "20"}),
                    function_call_response("search_traveler", {"raw_phone": "1112223333", "country_code": "20"}),
                ],
                max_tool_calls=1,
            )
            result = agent.respond(
                user_message="find profile",
                session_context={
                    "session_id": "sess-7b",
                    "workflow_policy": {
                        "assistant_message": "Do you want this trip with flights or without flights?",
                    },
                },
            )

        self.assertEqual(result["reply"], "Do you want this trip with flights or without flights?")
        self.assertFalse(result.get("error"))
        self.assertEqual(result.get("warning", ""), "tool_request_failed")

    def test_final_answer_uses_tool_result(self) -> None:
        with self._patch_service():
            agent, _ = self._build_agent(
                [
                    function_call_response("search_traveler", {"raw_phone": "1112223333", "country_code": "20"}),
                    text_response("Mona Ali is a VIP traveler and I found her profile."),
                ]
            )
            result = agent.respond(user_message="who is this", session_context={"session_id": "sess-8", "raw_phone": "1112223333"})

        self.assertIn("Mona Ali", result["reply"])
        self.assertEqual(result["tool_requests"][0]["name"], "search_traveler")

    def test_arabic_response(self) -> None:
        with self._patch_service():
            agent, _ = self._build_agent([text_response("أهلاً بك. أرسل رقم الواتساب من فضلك.")])
            result = agent.respond(user_message="مرحبا", session_context={"session_id": "sess-9", "language": "ar"})

        self.assertIn("الواتساب", result["reply"])

    def test_english_response(self) -> None:
        with self._patch_service():
            agent, _ = self._build_agent([text_response("Hello. Please share your WhatsApp number.")])
            result = agent.respond(user_message="hello", session_context={"session_id": "sess-10", "language": "en"})

        self.assertIn("WhatsApp number", result["reply"])

    def test_blank_final_turn_uses_workflow_message(self) -> None:
        with self._patch_service():
            agent, _ = self._build_agent(
                [
                    function_call_response("search_available_trips", {"trip_type": "international", "query": "Turkey"}),
                    text_response(""),
                ]
            )
            result = agent.respond(
                user_message="Show me trips to Turkey",
                session_context={
                    "session_id": "sess-11",
                    "language": "en",
                    "trip_type": "international",
                    "workflow_policy": {
                        "identity_verified": True,
                        "allowed_tools": ["search_available_trips"],
                        "assistant_message": "Please choose your room option. Current inventory: Single: 3 available.",
                    },
                },
            )

        self.assertEqual(result["reply"], "Please choose your room option. Current inventory: Single available.")
        self.assertNotIn("disabled", result["reply"].lower())

    def test_flight_preference_normalization_is_shared_across_runtime_layers(self) -> None:
        cases = {
            "\u0644\u0627 \u0627\u0631\u064a\u062f \u0637\u064a\u0631\u0627\u0646": "Without Flight",
            "withot": "Without Flight",
            "without": "Without Flight",
            "without flight": "Without Flight",
            "w/out": "Without Flight",
            "with flight": "With Flight",
        }

        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(normalize_flight_option(message), expected)
                self.assertEqual(
                    ToolCallingSessionRuntime._extract_hints(message, stage="flight_option_required")["candidate_flight_option"],
                    expected,
                )
                self.assertEqual(
                    _extract_gemini_message_hints(message, default_country_code="20")["candidate_flight_option"],
                    expected,
                )
    def test_trip_type_normalization_is_shared_across_runtime_layers(self) -> None:
        cases = {
            "\u0645\u062d\u0644\u064a\u0647": "local",
            "\u0631\u062d\u0644\u0629 \u0645\u062d\u0644\u064a\u0629": "local",
            "int": "international",
            "intl trip": "international",
        }

        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(normalize_trip_type(message), expected)
                self.assertEqual(ToolCallingSessionRuntime._extract_hints(message)["candidate_trip_type"], expected)
                self.assertEqual(
                    _extract_gemini_message_hints(message, default_country_code="20")["candidate_trip_type"],
                    expected,
                )

    def test_internal_instruction_leak_uses_backend_customer_message(self) -> None:
        with self._patch_service():
            agent, _ = self._build_agent(
                [text_response('assistant_message and do not improvise around the policy. Let\'s look at the request.')]
            )
            result = agent.respond(
                user_message="\u0645\u062d\u0644\u064a\u0629",
                session_context={
                    "session_id": "sess-internal-leak",
                    "workflow_policy": {
                        "assistant_message": "Are you looking for a local trip or an international trip?",
                    },
                },
            )

        self.assertEqual(result["reply"], "Are you looking for a local trip or an international trip?")
        self.assertNotIn("assistant_message", result["reply"])


if __name__ == "__main__":
    unittest.main()
