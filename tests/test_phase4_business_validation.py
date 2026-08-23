from __future__ import annotations

import sqlite3
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from services.ai_agent.ai_agent_app.agent.gemini_agent import GeminiAgent
from services.ai_agent.validation import APPROVED, NEED_MORE_INFORMATION, REJECTED, ActionValidator
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
        rows = self.connection.execute(
            "SELECT * FROM travelers WHERE phone_lookup_key = ?",
            (phone["lookup_key"],),
        ).fetchall()
        if not rows:
            return SimpleNamespace(
                lookup_phone=phone,
                match_status="not_found",
                handoff_required=False,
                handoff_reason="",
                name_match_status="",
                actions=["collect_new_traveler_data"],
                traveler=None,
            )
        if len(rows) > 1:
            return SimpleNamespace(
                lookup_phone=phone,
                match_status="multiple_matches",
                handoff_required=True,
                handoff_reason="duplicate_phone_match",
                name_match_status="",
                actions=["human_review_duplicate_phone"],
                traveler=None,
            )
        traveler = dict(rows[0])
        status = str(traveler.get("status") or "").strip().lower()
        if status in {"blacklisted", "blacklist", "blocked", "archived", "inactive"}:
            return SimpleNamespace(
                lookup_phone=phone,
                match_status="single_match",
                handoff_required=True,
                handoff_reason=f"{status}_traveler",
                name_match_status="matched",
                actions=["human_review"],
                traveler=traveler,
            )
        return SimpleNamespace(
            lookup_phone=phone,
            match_status="single_match",
            handoff_required=False,
            handoff_reason="",
            name_match_status="matched",
            actions=["continue_sales_flow"],
            traveler=traveler,
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
                            {
                                "functionCall": {"name": name, "args": args, "id": f"call-{response_id}"},
                                "thoughtSignature": "test-thought-signature",
                            },
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


class TestPhase4BusinessValidation(unittest.TestCase):
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
                end_date TEXT,
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
                booking_status TEXT,
                payment_status TEXT,
                room_type TEXT
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
        self.conn.executemany(
            "INSERT INTO travelers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("TRACTIVE", "Active Traveler", "Active", "", "", "", "", "", "+201111111111", "1111111111", "20:1111111111"),
                ("TRBLOCK", "Blocked Traveler", "Blacklisted", "", "", "", "", "", "+201222222222", "1222222222", "20:1222222222"),
                ("TRINTL", "Intl Traveler", "Active", "", "", "", "", "", "+201333333333", "1333333333", "20:1333333333"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO trips VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("RT-LOC-26-001", "Local Escape", "Local", "2026-08-10", "2026-08-15", "Open", "2000$", 5),
                ("RT-INT-26-001", "Istanbul Explorer", "International", "2026-09-10", "2026-09-18", "Open", "2500$", 4),
                ("RT-INT-26-TBD", "TBD Europe", "International", "", "", "Open", "3000$", 3),
                ("RT-LOC-26-CLS", "Closed Trip", "Local", "2026-07-01", "2026-07-05", "Closed", "1800$", 0),
            ],
        )
        self.conn.executemany(
            "INSERT INTO leads VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("LDOPEN1", "Active Traveler", "TRACTIVE", "1111111111", "+201111111111", "20:1111111111", "2026-06-28T10:00:00", "Qualified"),
                ("LDCLOSED", "Active Traveler", "TRACTIVE", "1111111111", "+201111111111", "20:1111111111", "2026-06-20T10:00:00", "Lost"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO trip_bookings VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("B-ACTIVE", "RT-LOC-26-001", "TRACTIVE", "LDOPEN1", "2026-06-28T11:00:00", "Draft", "Pending", "Double"),
                ("B-CANCEL", "RT-INT-26-001", "TRINTL", "", "2026-06-27T11:00:00", "Cancelled", "Refunded", "Double"),
            ],
        )
        self.conn.commit()
        self.settings = SimpleNamespace(default_country_code="20", ai_agent_mode="gemini", ai_agent_system_prompt="You are Rahvel Agent.")

    def tearDown(self) -> None:
        self.conn.close()

    @contextmanager
    def _patch_service(self):
        with patch(
            "services.ai_agent.ai_agent_app.agent.read_only_tools.get_system_service",
            return_value=FakeCRMService(self.conn),
        ):
            yield

    def _build_validator(self) -> ActionValidator:
        return ActionValidator(self.settings)

    def test_blocked_traveler_booking_is_rejected(self) -> None:
        with self._patch_service():
            validator = self._build_validator()
            result = validator.validate_action(
                action="create_booking_draft",
                payload={"raw_phone": "1222222222", "country_code": "20", "trip_id": "RT-LOC-26-001", "room_type": "Double"},
                session_context={"session_id": "sess-blocked"},
            )

        self.assertEqual(result.decision, REJECTED)
        self.assertIn("blocks booking draft creation", " ".join(result.reasons))

    def test_duplicate_traveler_is_rejected(self) -> None:
        with self._patch_service():
            validator = self._build_validator()
            result = validator.validate_action(
                action="create_traveler",
                payload={"raw_phone": "1111111111", "country_code": "20"},
                session_context={"session_id": "sess-traveler"},
            )

        self.assertEqual(result.decision, REJECTED)
        self.assertIn("existing traveler", " ".join(result.reasons).lower())

    def test_duplicate_open_lead_is_rejected(self) -> None:
        with self._patch_service():
            validator = self._build_validator()
            result = validator.validate_action(
                action="create_lead",
                payload={"traveler_id": "TRACTIVE"},
                session_context={"session_id": "sess-lead"},
            )

        self.assertEqual(result.decision, REJECTED)
        self.assertIn("open lead", " ".join(result.reasons).lower())

    def test_missing_passport_for_international_booking_needs_more_information(self) -> None:
        with self._patch_service():
            validator = self._build_validator()
            result = validator.validate_action(
                action="create_booking_draft",
                payload={"traveler_id": "TRINTL", "trip_id": "RT-INT-26-001", "room_type": "Double"},
                session_context={"session_id": "sess-passport"},
            )

        self.assertEqual(result.decision, NEED_MORE_INFORMATION)
        self.assertIn("passport_attachment_ref", result.missing_information)

    def test_local_booking_can_be_approved(self) -> None:
        with self._patch_service():
            validator = self._build_validator()
            result = validator.validate_action(
                action="create_booking_draft",
                payload={"traveler_id": "TRINTL", "trip_id": "RT-LOC-26-001", "room_type": "Double"},
                session_context={"session_id": "sess-local"},
            )

        self.assertEqual(result.decision, APPROVED)

    def test_invalid_trip_is_rejected(self) -> None:
        with self._patch_service():
            validator = self._build_validator()
            result = validator.validate_action(
                action="create_booking_draft",
                payload={"traveler_id": "TRINTL", "trip_id": "RT-MISSING", "room_type": "Double"},
                session_context={"session_id": "sess-trip"},
            )

        self.assertEqual(result.decision, REJECTED)
        self.assertIn("was not found", " ".join(result.reasons))

    def test_duplicate_booking_is_rejected(self) -> None:
        with self._patch_service():
            validator = self._build_validator()
            result = validator.validate_action(
                action="create_booking_draft",
                payload={"traveler_id": "TRACTIVE", "trip_id": "RT-LOC-26-001", "room_type": "Double"},
                session_context={"session_id": "sess-dup-booking"},
            )

        self.assertEqual(result.decision, REJECTED)
        self.assertIn("active booking already exists", " ".join(result.reasons).lower())

    def test_missing_room_type_needs_more_information(self) -> None:
        with self._patch_service():
            validator = self._build_validator()
            result = validator.validate_action(
                action="create_booking_draft",
                payload={"traveler_id": "TRINTL", "trip_id": "RT-LOC-26-001"},
                session_context={"session_id": "sess-room"},
            )

        self.assertEqual(result.decision, NEED_MORE_INFORMATION)
        self.assertIn("room_type", result.missing_information)

    def test_handoff_request_is_approved(self) -> None:
        with self._patch_service():
            validator = self._build_validator()
            result = validator.validate_action(
                action="create_handoff",
                payload={"traveler_id": "TRACTIVE", "user_requested_human": True},
                session_context={"session_id": "sess-handoff"},
            )

        self.assertEqual(result.decision, APPROVED)
        self.assertIn("requested a human agent", " ".join(result.reasons))

    def test_controlled_capacity_handoff_is_approved(self) -> None:
        with self._patch_service():
            validator = self._build_validator()
            result = validator.validate_action(
                action="create_handoff",
                payload={"reason_code": "room_capacity", "reason_text": "Room inventory needs review."},
                session_context={"session_id": "sess-capacity", "state": "capacity_handoff_required"},
            )

        self.assertEqual(result.decision, APPROVED)
        self.assertIn("controlled human review", " ".join(result.reasons))

    def test_passport_upload_failure_handoff_is_approved(self) -> None:
        with self._patch_service():
            validator = self._build_validator()
            result = validator.validate_action(
                action="create_handoff",
                payload={
                    "traveler_id": "TRINTL",
                    "reason_code": "passport_upload_failed",
                    "reason_text": "Customer could not upload the required passport attachment.",
                },
                session_context={"session_id": "sess-passport-upload-failed"},
            )

        self.assertEqual(result.decision, APPROVED)
        self.assertIn("controlled human review", " ".join(result.reasons))

    def test_missing_trip_price_handoff_is_approved(self) -> None:
        with self._patch_service():
            validator = self._build_validator()
            result = validator.validate_action(
                action="create_handoff",
                payload={
                    "traveler_id": "TRACTIVE",
                    "reason_code": "missing_trip_price",
                    "reason_text": "CRM is missing a usable price for the selected room.",
                },
                session_context={"session_id": "sess-missing-trip-price", "required_step": "create_pricing_handoff"},
            )

        self.assertEqual(result.decision, APPROVED)
        self.assertIn("controlled human review", " ".join(result.reasons))

    def test_validation_decision_is_logged(self) -> None:
        with self._patch_service():
            validator = self._build_validator()
            with self.assertLogs("rahma_agent", level="INFO") as captured:
                validator.validate_action(
                    action="create_handoff",
                    payload={"traveler_id": "TRACTIVE", "user_requested_human": True},
                    session_context={"session_id": "sess-log"},
                )

        combined = "\n".join(captured.output)
        self.assertIn("Validation decision", combined)
        self.assertIn("create_handoff", combined)
        self.assertIn("sess-log", combined)

    def test_gemini_can_use_validation_tool_result(self) -> None:
        with self._patch_service():
            agent = GeminiAgent(
                settings=self.settings,
                provider=LoopProviderStub(
                    [
                        function_call_response(
                            "validate_business_action",
                            {
                                "action": "create_booking_draft",
                                "traveler_id": "TRINTL",
                                "trip_id": "RT-INT-26-001",
                                "room_type": "Double",
                            },
                        ),
                        text_response("Before I can prepare that booking, I still need your passport attachment."),
                    ]
                ),
            )
            result = agent.respond(
                user_message="Please book the international trip for me.",
                session_context={"session_id": "sess-gemini"},
            )

        self.assertEqual(result["tool_requests"][0]["name"], "validate_business_action")
        self.assertIn("passport attachment", result["reply"].lower())


if __name__ == "__main__":
    unittest.main()
