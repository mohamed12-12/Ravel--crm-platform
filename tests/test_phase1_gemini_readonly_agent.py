from __future__ import annotations

import json
import os
import sqlite3
import socket
import unittest
from urllib import error as urllib_error
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from services.ai_agent.ai_agent_app.agent.gemini_agent import GeminiAgent
from services.ai_agent.ai_agent_app.agent.read_only_tools import ReadOnlyCRMTools
from services.ai_agent.ai_agent_app.agent.tool_registry import build_tool_calling_registry
from services.ai_agent.ai_agent_app.conversation_ai import DEFAULT_AGENT_CONVERSATION_PROMPT
from services.ai_agent.ai_agent_app.config import load_settings
from services.ai_agent.llm.gemini_provider import GeminiProvider, GeminiProviderResponse
from services.ai_agent.llm.llm_factory import build_llm_provider


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
        trips = self.connection.execute("SELECT * FROM trips ORDER BY trip_name ASC").fetchall()
        open_trips = []
        date_tbd_trips = []
        for row in trips:
            record = dict(row)
            if trip_type and str(record.get("type", "")).casefold() != trip_type:
                continue
            if record.get("start_date"):
                open_trips.append(record)
            else:
                date_tbd_trips.append(record)
        return {"open_trips": open_trips, "date_tbd_trips": date_tbd_trips}


class StubProvider:
    def __init__(self, reply: str = "Friendly reply.") -> None:
        self.reply = reply
        self.calls: list[dict] = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return GeminiProviderResponse(
            text=self.reply,
            response_id="resp-123",
            raw={"ok": True},
            usage_metadata={"inputTokens": 42, "outputTokens": 12},
        )


class TestPhase1GeminiReadOnlyAgent(unittest.TestCase):
    def test_default_conversation_prompt_preserves_nanovate_branding(self) -> None:
        self.assertIn("created by nanovate.io for Ravel Traveler", DEFAULT_AGENT_CONVERSATION_PROMPT)
        self.assertIn("do not attribute the assistant to any model provider", DEFAULT_AGENT_CONVERSATION_PROMPT)

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
        self.settings = SimpleNamespace(default_country_code="20")

    def tearDown(self) -> None:
        self.conn.close()

    def test_feature_flag_defaults_to_deterministic(self) -> None:
        with patch.dict(os.environ, {"AI_AGENT_MODE": "deterministic"}, clear=False):
            settings = load_settings()
        self.assertEqual(settings.ai_agent_mode, "deterministic")
        self.assertFalse(settings.gemini_agent_enabled)

    def test_llm_factory_respects_deterministic_mode(self) -> None:
        settings = SimpleNamespace(ai_agent_mode="deterministic", gemini_api_key="x", gemini_model="gemini-2.5-flash")
        self.assertIsNone(build_llm_provider(settings))

    def test_read_only_tools_cover_lookup_paths(self) -> None:
        with patch("services.ai_agent.ai_agent_app.agent.read_only_tools.get_system_service", return_value=self.fake_service):
            tools = ReadOnlyCRMTools(self.settings)
            traveler_search = tools.search_traveler_by_phone(raw_phone="1112223333", country_code="20")
            profile = tools.get_traveler_profile(raw_phone="1112223333", country_code="20")
            trips = tools.search_trips(trip_type="local")
            lead = tools.lookup_lead(lead_id="LD00001")
            booking = tools.lookup_booking(booking_id="B00001")
            passport = tools.get_passport_status(traveler_id="TR00001")

        self.assertEqual(traveler_search["match_status"], "single_match")
        self.assertEqual(profile["traveler"]["traveler_id"], "TR00001")
        self.assertEqual(len(trips["open_trips"]), 1)
        self.assertEqual(lead["leads"][0]["lead_id"], "LD00001")
        self.assertEqual(booking["bookings"][0]["booking_id"], "B00001")
        self.assertEqual(passport["traveler"]["passport_number"], "A1234567")
        self.assertEqual(len(passport["documents"]), 1)

    def test_gemini_agent_uses_prompt_and_keeps_read_only_context(self) -> None:
        provider = StubProvider("Please share your WhatsApp number so I can check your profile safely.")
        with patch("services.ai_agent.ai_agent_app.agent.read_only_tools.get_system_service", return_value=self.fake_service):
            agent = GeminiAgent(
                settings=SimpleNamespace(
                    ai_agent_mode="gemini",
                    default_country_code="20",
                    ai_agent_system_prompt="You are Rahvel Agent.",
                ),
                provider=provider,
            )
            result = agent.respond(
                user_message="hi",
                session_context={
                    "session_id": "sess-1",
                    "language": "en",
                    "raw_phone": "1112223333",
                    "country_code": "20",
                    "trip_type": "local",
                },
                conversation_history=[{"role": "user", "text": "hello"}],
            )

        self.assertIn("WhatsApp number", result["reply"])
        self.assertEqual(result["mode"], "gemini")
        self.assertTrue(provider.calls)
        payload = json.loads(provider.calls[0]["messages"][0]["parts"][0]["text"])
        self.assertEqual(payload["language"], "en")
        self.assertEqual(payload["session_context"]["raw_phone"], "1112223333")
        self.assertIn("search_traveler", payload["available_tools"])
        self.assertIn("traveler_lookup", payload["crm_context"])

    def test_gemini_agent_handles_arabic_and_write_refusal(self) -> None:
        provider = StubProvider("أرسل رقم الواتساب من فضلك.")
        with patch("services.ai_agent.ai_agent_app.agent.read_only_tools.get_system_service", return_value=self.fake_service):
            agent = GeminiAgent(
                settings=SimpleNamespace(
                    ai_agent_mode="gemini",
                    default_country_code="20",
                    ai_agent_system_prompt="You are Rahvel Agent.",
                ),
                provider=provider,
            )
            rewrite = agent.rewrite_message(
                message_key="session.ask_phone_first",
                base_text="Please share your WhatsApp number.",
                language="ar",
                session_context={"session_id": "sess-2", "raw_phone": "1112223333", "country_code": "20"},
                user_text="مرحبا",
                required_action="Ask for the WhatsApp number.",
            )
            refusal = agent.respond(
                user_message="create lead for me",
                session_context={"session_id": "sess-2"},
            )

        self.assertEqual(rewrite, "أرسل رقم الواتساب من فضلك.")
        self.assertNotEqual(refusal["reply"], "Write operations are disabled in Phase 1.")
        self.assertTrue(refusal["reply"])

    def test_gemini_agent_invalid_response_falls_back_safely(self) -> None:
        provider = StubProvider("   ")
        with patch("services.ai_agent.ai_agent_app.agent.read_only_tools.get_system_service", return_value=self.fake_service):
            agent = GeminiAgent(
                settings=SimpleNamespace(
                    ai_agent_mode="gemini",
                    default_country_code="20",
                    ai_agent_system_prompt="You are Rahvel Agent.",
                ),
                provider=provider,
            )
            result = agent.respond(user_message="hello", session_context={"session_id": "sess-3"})

        self.assertEqual(
            result["reply"],
            "Sorry, I couldn't prepare that response properly. Could you try that again?",
        )

    def test_gemini_provider_retries_after_timeout(self) -> None:
        provider = GeminiProvider(api_key="key", model="gemini-2.5-flash", timeout_seconds=1, retries=1)

        class FakeResponse:
            def __init__(self, payload: dict[str, object]) -> None:
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        calls = {"count": 0}

        def fake_urlopen(*_args, **_kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise socket.timeout()
            return FakeResponse(
                {
                    "responseId": "abc123",
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {"text": "Hello from Gemini."},
                                ]
                            }
                        }
                    ],
                    "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 4},
                }
            )

        with patch("services.ai_agent.llm.gemini_provider.request.urlopen", side_effect=fake_urlopen):
            result = provider.generate(
                system_prompt="You are Rahvel Agent.",
                messages=[{"role": "user", "parts": [{"text": "hello"}]}],
                request_id="prompt-1",
            )

        self.assertEqual(result.response_id, "abc123")
        self.assertEqual(result.text, "Hello from Gemini.")
        self.assertEqual(calls["count"], 2)

    def test_gemini_provider_normalizes_contents_and_tools(self) -> None:
        provider = GeminiProvider(api_key="key", model="gemini-2.5-flash", timeout_seconds=1, retries=0)
        captured: dict[str, object] = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(
                    {
                        "responseId": "abc123",
                        "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
                    }
                ).encode("utf-8")

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse()

        with patch("services.ai_agent.llm.gemini_provider.request.urlopen", side_effect=fake_urlopen):
            result = provider.generate(
                system_prompt="You are Rahvel Agent.",
                messages=[
                    {"role": "user", "text": "hello"},
                    {"role": "assistant", "parts": [{"text": "Hi there"}]},
                    {"role": "user", "parts": [{"functionResponse": {"name": "lookup_lead", "response": {"leads": []}}}]},
                ],
                tools=[
                    {
                        "functionDeclarations": [
                            {
                                "name": "find_traveler_by_phone",
                                "description": "Find traveler by phone",
                                "parameters": {"type": "object", "properties": {"raw_phone": {"type": "string"}}},
                            }
                        ]
                    }
                ],
                request_id="prompt-1",
            )

        self.assertIn(":generateContent", str(captured["url"]))
        body = captured["body"]
        self.assertIsInstance(body, dict)
        self.assertEqual(body["system_instruction"]["parts"][0]["text"], "You are Rahvel Agent.")
        self.assertEqual(body["contents"][0]["role"], "user")
        self.assertEqual(body["contents"][0]["parts"][0]["text"], "hello")
        self.assertEqual(body["contents"][1]["role"], "model")
        self.assertEqual(body["contents"][1]["parts"][0]["text"], "Hi there")
        self.assertEqual(body["contents"][2]["parts"][0]["functionResponse"]["name"], "lookup_lead")
        self.assertIn("functionDeclarations", body["tools"][0])
        self.assertEqual(result.text, "ok")

    def test_gemini_provider_parses_http_400_body_safely(self) -> None:
        provider = GeminiProvider(api_key="key", model="gemini-2.5-flash")
        body = json.dumps(
            {
                "error": {
                    "status": "INVALID_ARGUMENT",
                    "message": "Invalid JSON payload received. Unknown name \"text\" at 'contents[0]': Cannot find field.",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.BadRequest",
                            "fieldViolations": [
                                {"field": "contents[0]", "description": "Unknown name text"}
                            ],
                        }
                    ],
                }
            }
        ).encode("utf-8")

        exc = urllib_error.HTTPError(
            url="https://example.invalid",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=None,
        )

        class Reader:
            def read(self):
                return body

        exc.fp = Reader()
        parsed = provider._parse_google_error(body.decode("utf-8"))
        self.assertEqual(parsed["status"], "INVALID_ARGUMENT")
        self.assertIn("Unknown name", parsed["message"])
        self.assertEqual(parsed["invalid_field"], "contents[0]")
        self.assertNotIn("key", json.dumps(parsed))

    def test_tool_calling_registry_uses_gemini_compatible_schemas(self) -> None:
        registry = build_tool_calling_registry()
        self.assertTrue(registry)
        for name, spec in registry.items():
            self.assertTrue(name)
            self.assertTrue(spec.description.strip())
            schema = spec.input_schema
            self.assertEqual(schema.get("type"), "object")
            self.assertNotIn("$schema", schema)
            self.assertNotIn("$ref", schema)
            self.assertNotIn("oneOf", schema)
            self.assertNotIn("anyOf", schema)
            self.assertNotIn("allOf", schema)
            self.assertNotIn("definitions", schema)
            self.assertNotIn("$defs", schema)


if __name__ == "__main__":
    unittest.main()
