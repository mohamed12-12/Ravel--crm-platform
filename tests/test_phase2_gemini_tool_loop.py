from __future__ import annotations

import json
import sqlite3
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from services.ai_agent.ai_agent_app.agent.gemini_agent import GeminiAgent
from services.ai_agent.llm.gemini_provider import GeminiProviderResponse


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


def function_call_response(name: str, args: dict[str, object], response_id: str = "resp-fn") -> GeminiProviderResponse:
    return GeminiProviderResponse(
        text="",
        response_id=response_id,
        raw={
            "responseId": response_id,
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"functionCall": {"name": name, "args": args}},
                        ]
                    }
                }
            ],
        },
        usage_metadata={"inputTokens": 1, "outputTokens": 1},
    )


def text_response(text: str, response_id: str = "resp-text") -> GeminiProviderResponse:
    return GeminiProviderResponse(
        text=text,
        response_id=response_id,
        raw={
            "responseId": response_id,
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": text},
                        ]
                    }
                }
            ],
        },
        usage_metadata={"inputTokens": 1, "outputTokens": 1},
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
            "Automatic CRM writes are disabled in this phase. I can only validate whether the action is allowed.",
        )
        self.assertIn("Unsupported tool requested", result["error"])

    def test_invalid_tool_input_rejected(self) -> None:
        with self._patch_service():
            agent, _ = self._build_agent([function_call_response("search_traveler", {"country_code": "20"})])
            result = agent.respond(user_message="find profile", session_context={"session_id": "sess-5"})

        self.assertEqual(
            result["reply"],
            "Automatic CRM writes are disabled in this phase. I can only validate whether the action is allowed.",
        )
        self.assertIn("Invalid tool input", result["error"])

    def test_write_tool_request_rejected(self) -> None:
        with self._patch_service():
            agent, _ = self._build_agent([function_call_response("update_traveler", {"traveler_id": "TR00001"})])
            result = agent.respond(user_message="update this traveler", session_context={"session_id": "sess-6"})

        self.assertEqual(
            result["reply"],
            "Automatic CRM writes are disabled in this phase. I can only validate whether the action is allowed.",
        )
        self.assertIn("Unsupported tool requested", result["error"])

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
            "Automatic CRM writes are disabled in this phase. I can only validate whether the action is allowed.",
        )
        self.assertIn("Maximum Gemini tool-call limit reached", result["error"])

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


if __name__ == "__main__":
    unittest.main()
