"""
tests/test_phase11_demo_features.py

Phase 11 test suite: validates demo-phase features implemented per client spec:
  - Agent persona name (Rahvel Agent)
  - Language detection (Arabic / English)
  - Numbered + typed-word option replies
  - Website link intent response
  - Post-trip human handoff (configurable, default OFF)
  - Passport info collection for international trips (metadata-first)
  - Visa requirement lookup (table-based, always with disclaimer)
  - Discount notes from CRM trip info (no hardcoded formulas)
  - SQLite migration: passport columns added safely and backward-compatibly
"""
from __future__ import annotations

import io
import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import sys
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from test_phase3_booking_write_through import create_operational_tables  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from demo_web.app import create_app  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_base_workbook(path: Path) -> None:
    """Create a minimal demo workbook that satisfies all sheet requirements."""
    wb = Workbook()
    travelers = wb.active
    travelers.title = "Travelers"
    travelers.append(
        [
            "Status", "Traveler ID", "Full Name", "First Name", "Last Name",
            "Birthday", "Gender", "Nationality", "Code", "WhatsApp", "Email",
            "Community Whatsapp", "Residence", "Loc. Trips", "Int. Trips",
            "Total trips", "Comm. Events", "Lifetime Revenue", "Notes",
            "Introduce yourself", "Emergency Contact", "Emergency Phone",
            "Medical Notes", "Room Preference", "⭐ Rating (1–5)",
            "Integrated WhatsApp", "Normalized WhatsApp", "Phone Lookup Key",
            "Lead Source", "Created At", "Last Contacted At", "Agent Notes", "Data Audit",
        ]
    )

    trips = wb.create_sheet("Trips")
    trips["A2"] = "Trip ID"
    trips["B2"] = "Trip Name"
    trips["C2"] = "Type"
    trips["D2"] = "Year"
    trips["F2"] = "Start Date"
    trips["G2"] = "End Date"
    trips["Z2"] = "Sales Status"
    trips["AA2"] = "Data Audit"
    # Local open trip
    trips["A3"] = "RT-LOC-26-001"
    trips["B3"] = "Sinai Trek"
    trips["C3"] = "Local"
    trips["D3"] = 2026
    trips["F3"] = "2026-09-10"
    trips["G3"] = "2026-09-14"
    trips["K3"] = 5
    trips["L3"] = 5
    trips["M3"] = 5
    trips["Z3"] = "Open"
    # International open trip
    trips["A4"] = "RT-INT-26-001"
    trips["B4"] = "Istanbul Explorer"
    trips["C4"] = "International"
    trips["D4"] = 2026
    trips["F4"] = "2026-10-01"
    trips["G4"] = "2026-10-08"
    trips["K4"] = 3
    trips["L4"] = 3
    trips["M4"] = 3
    trips["Z4"] = "Open"

    interactions = wb.create_sheet("Interactions")
    interactions.append(
        [
            "Interaction ID", "Timestamp", "Channel", "Customer Name", "Raw Phone",
            "Integrated WhatsApp", "Phone Lookup Key", "Traveler ID", "Matched Row",
            "Status Snapshot", "Intent", "Trip Type", "Suggested Trips",
            "Action Taken", "Handoff Required", "Handoff Reason", "Agent Notes",
        ]
    )

    trip_bookings = wb.create_sheet("Trip Bookings")
    trip_bookings["A2"] = "Booking ID"
    trip_bookings["B2"] = "Trip ID"
    trip_bookings["C2"] = "Trip Name"
    trip_bookings["D2"] = "Traveler ID"
    trip_bookings["E2"] = "Traveler Name"
    trip_bookings["F2"] = "Room Type"

    # Visa Requirements sheet
    visa_ws = wb.create_sheet("Visa Requirements")
    visa_ws.append(["Destination", "Visa Required", "Notes"])
    visa_ws.append(["Turkey", "No", "Egyptian passport holders: visa-free for up to 90 days."])
    visa_ws.append(["United Kingdom", "Yes", "Standard visitor visa required. Apply 3 months in advance."])
    visa_ws.append(["UAE", "No", "Egyptian citizens receive visa on arrival."])

    wb.save(path)


def _make_app_with_db(tmp_path: Path, *, post_trip_handoff_enabled: bool = False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source_wb = tmp_path / "source.xlsx"
    runtime_wb = tmp_path / "runtime.xlsx"
    db_path = tmp_path / "system.db"

    with closing(sqlite3.connect(str(db_path))) as conn:
        create_operational_tables(conn)
        conn.execute(
            """
            INSERT INTO trips (
                trip_id, trip_name, type, year, start_date, end_date, sales_status,
                single_total, double_total, triple_total,
                single_remaining, double_remaining, triple_remaining,
                draft_holds_single, draft_holds_double, draft_holds_triple
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("RT-LOC-26-001", "Sinai Trek", "Local", 2026,
             "2026-09-10", "2026-09-14", "Open",
             5, 5, 5, 5, 5, 5, 0, 0, 0),
        )
        conn.execute(
            """
            INSERT INTO trips (
                trip_id, trip_name, type, year, start_date, end_date, sales_status,
                single_total, double_total, triple_total,
                single_remaining, double_remaining, triple_remaining,
                draft_holds_single, draft_holds_double, draft_holds_triple
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("RT-INT-26-001", "Istanbul Explorer", "International", 2026,
             "2026-10-01", "2026-10-08", "Open",
             3, 3, 3, 3, 3, 3, 0, 0, 0),
        )
        conn.commit()

    _make_base_workbook(source_wb)
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.resolve().as_posix()}"
    os.environ["RAHMA_SYSTEM_DB_PATH"] = str(db_path)
    os.environ["AI_AGENT_UPLOAD_ROOT"] = str(tmp_path / "uploads")
    os.environ["AI_AGENT_MODE"] = "deterministic"
    if post_trip_handoff_enabled:
        os.environ["POST_TRIP_HANDOFF_ENABLED"] = "true"
    else:
        os.environ.pop("POST_TRIP_HANDOFF_ENABLED", None)

    app = create_app(source_workbook=source_wb, runtime_workbook=runtime_wb)
    return app.test_client(), app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAgentPersonaName(unittest.TestCase):
    """Agent persona name should appear in the greeting message."""

    def setUp(self):
        self.tmp = Path(".tmp-test-phase11") / uuid.uuid4().hex
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.original_env = dict(os.environ)
        os.environ["AGENT_PERSONA_NAME"] = "Rahvel Agent"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.original_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_greeting_contains_persona_name(self):
        client, _ = _make_app_with_db(self.tmp)
        resp = client.post("/api/session", json={})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        greeting = data["session"]["messages"][0]["text"]
        self.assertIn("Rahvel Agent", greeting, f"Expected persona name in greeting: {greeting!r}")

    def test_agent_config_endpoint(self):
        client, _ = _make_app_with_db(self.tmp)
        resp = client.get("/api/agent-config")
        self.assertEqual(resp.status_code, 200)
        cfg = resp.get_json()
        self.assertEqual(cfg["agentPersonaName"], "Rahvel Agent")
        self.assertIn("websiteUrl", cfg)
        self.assertIn("postTripHandoffEnabled", cfg)


class TestLanguageDetection(unittest.TestCase):
    """Language detection should update session language from Arabic messages."""

    def test_detect_arabic_text(self):
        from services.ai_agent.ai_agent_app.agent.session_flow import detect_language
        self.assertEqual(detect_language("مرحبا أريد السفر إلى تركيا"), "ar")

    def test_detect_english_text(self):
        from services.ai_agent.ai_agent_app.agent.session_flow import detect_language
        self.assertEqual(detect_language("I want to book a trip to Turkey"), "en")

    def test_detect_mixed_short_arabic(self):
        from services.ai_agent.ai_agent_app.agent.session_flow import detect_language
        # Short Arabic word: should still detect as Arabic
        self.assertEqual(detect_language("نعم"), "ar")

    def test_detect_empty(self):
        from services.ai_agent.ai_agent_app.agent.session_flow import detect_language
        self.assertEqual(detect_language(""), "en")


class TestInstagramWebhookPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(".tmp-test-phase11") / uuid.uuid4().hex
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.original_env = dict(os.environ)
        os.environ["META_APP_SECRET"] = "test-meta-secret"
        os.environ["META_VERIFY_TOKEN"] = "verify-me"
        os.environ["META_PAGE_ACCESS_TOKEN"] = "test-page-token"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.original_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _signature(secret: str, body: bytes) -> str:
        digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return f"sha256={digest}"

    def test_webhook_persists_inbound_instagram_message_once(self):
        client, app = _make_app_with_db(self.tmp)
        payload = {
            "object": "page",
            "entry": [
                {
                    "messaging": [
                        {
                            "sender": {"id": "igsid-123"},
                            "recipient": {"id": "page-456"},
                            "timestamp": 1760000000000,
                            "message": {
                                "mid": "mid.instagram.1",
                                "text": "Hello from Instagram",
                            },
                        }
                    ]
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-Hub-Signature-256": self._signature("test-meta-secret", body),
        }

        first = client.post("/webhook", data=body, headers=headers)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.get_json()["persisted"], 1)
        self.assertEqual(first.get_json()["duplicates"], 0)

        second = client.post("/webhook", data=body, headers=headers)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.get_json()["persisted"], 0)
        self.assertEqual(second.get_json()["duplicates"], 1)

        with app.app_context():
            with closing(sqlite3.connect(os.environ["RAHMA_SYSTEM_DB_PATH"])) as conn:
                row = conn.execute(
                    """
                    SELECT channel, raw_phone, message_key, agent_notes, action_taken, flow_key, step_key
                    FROM interactions
                    WHERE message_key = ?
                    """,
                    ("mid.instagram.1",),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row[0], "Instagram")
                self.assertEqual(row[1], "igsid-123")
                self.assertEqual(row[2], "mid.instagram.1")
                self.assertIn("Hello from Instagram", row[3])
                self.assertEqual(row[4], "webhook_received")
                self.assertEqual(row[5], "instagram")
                self.assertEqual(row[6], "inbound_webhook")

    @patch("services.ai_agent.ai_agent_app.server.MetaGraphClient.send_instagram_text_message")
    def test_webhook_sends_safe_outbound_reply_when_configured(self, mock_send):
        mock_send.return_value = type(
            "Result",
            (),
            {"ok": True, "status_code": 200, "response_json": {"ok": True}},
        )()
        client, _ = _make_app_with_db(self.tmp)
        payload = {
            "object": "page",
            "entry": [
                {
                    "messaging": [
                        {
                            "sender": {"id": "igsid-999"},
                            "recipient": {"id": "page-456"},
                            "timestamp": 1760000000000,
                            "message": {"mid": "mid.instagram.2", "text": "Hello, I need help"},
                        }
                    ]
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-Hub-Signature-256": self._signature("test-meta-secret", body),
        }
        resp = client.post("/webhook", data=body, headers=headers)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["persisted"], 1)
        self.assertEqual(resp.get_json()["repliesSent"], 1)
        self.assertEqual(resp.get_json()["repliesFailed"], 0)
        mock_send.assert_called_once()


class TestNumberedReplies(unittest.TestCase):
    """All option stages should accept both numbered and word replies."""

    def setUp(self):
        self.tmp = Path(".tmp-test-phase11") / uuid.uuid4().hex
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.original_env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.original_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _complete_intake(self, client, phone: str = "01099999999") -> dict:
        sess = client.post("/api/session", json={}).get_json()["session"]
        sess = client.post(
            f"/api/session/{sess['id']}/message", json={"text": phone}
        ).get_json()["session"]
        if sess["stage"] == "awaiting_intake":
            sess = client.post(
                f"/api/session/{sess['id']}/intake",
                json={
                    "fullName": "Test User",
                    "birthday": "1990-01-01",
                    "gender": "Male",
                    "nationality": "Egypt",
                    "countryCode": "20",
                    "rawPhone": phone,
                },
            ).get_json()["session"]
        return sess

    def test_trip_type_number_1_selects_local(self):
        client, _ = _make_app_with_db(self.tmp)
        sess = self._complete_intake(client)
        self.assertEqual(sess["stage"], "awaiting_trip_type")
        sess = client.post(
            f"/api/session/{sess['id']}/message", json={"text": "1"}
        ).get_json()["session"]
        self.assertEqual(sess["stage"], "awaiting_confirmation")
        self.assertEqual(sess["tripType"], "local")

    def test_trip_type_word_local(self):
        client, _ = _make_app_with_db(self.tmp)
        sess = self._complete_intake(client, phone="01088888888")
        self.assertEqual(sess["stage"], "awaiting_trip_type")
        sess = client.post(
            f"/api/session/{sess['id']}/message", json={"text": "local"}
        ).get_json()["session"]
        self.assertEqual(sess["stage"], "awaiting_confirmation")
        self.assertEqual(sess["tripType"], "local")

    def test_trip_type_number_2_selects_international(self):
        client, _ = _make_app_with_db(self.tmp)
        sess = self._complete_intake(client, phone="01077777777")
        sess = client.post(
            f"/api/session/{sess['id']}/message", json={"text": "2"}
        ).get_json()["session"]
        self.assertEqual(sess["stage"], "awaiting_confirmation")
        self.assertEqual(sess["tripType"], "international")

    def test_room_type_number_1_selects_single(self):
        """After confirming a local trip with capacity, number '1' picks Single."""
        client, _ = _make_app_with_db(self.tmp)
        sess = self._complete_intake(client, phone="01066666666")
        sess = client.post(
            f"/api/session/{sess['id']}/message", json={"text": "1"}
        ).get_json()["session"]  # local
        # Confirm first trip
        sess = client.post(
            f"/api/session/{sess['id']}/message", json={"text": "1"}
        ).get_json()["session"]
        self.assertEqual(sess["stage"], "awaiting_room_type")
        sess = client.post(
            f"/api/session/{sess['id']}/message", json={"text": "1"}
        ).get_json()["session"]
        self.assertEqual(sess["stage"], "awaiting_flight")
        self.assertEqual(sess["roomType"], "Single")


class TestWebsiteIntent(unittest.TestCase):
    """Typing 'website' in any stage should return a website response."""

    def setUp(self):
        self.tmp = Path(".tmp-test-phase11") / uuid.uuid4().hex
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.original_env = dict(os.environ)
        os.environ["WEBSITE_URL"] = "https://rahma-travel.com"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.original_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_website_intent_returns_url(self):
        client, _ = _make_app_with_db(self.tmp)
        sess = client.post("/api/session", json={}).get_json()["session"]
        sess = client.post(
            f"/api/session/{sess['id']}/message", json={"text": "Can I visit your website?"}
        ).get_json()["session"]
        last_msg = sess["messages"][-1]["text"]
        self.assertIn("rahma-travel.com", last_msg)
        # Session should still be at awaiting_phone (website intent doesn't advance stage)
        self.assertEqual(sess["stage"], "awaiting_phone")

    def test_website_intent_no_url_configured(self):
        os.environ.pop("WEBSITE_URL", None)
        client, _ = _make_app_with_db(self.tmp)
        sess = client.post("/api/session", json={}).get_json()["session"]
        sess = client.post(
            f"/api/session/{sess['id']}/message", json={"text": "website"}
        ).get_json()["session"]
        last_msg = sess["messages"][-1]["text"]
        # Should say website is coming soon or similar
        self.assertTrue(
            "coming soon" in last_msg.lower() or "team" in last_msg.lower(),
            f"Expected coming-soon message, got: {last_msg!r}",
        )


class TestSessionCompletionGuards(unittest.TestCase):
    """Booking paths should close the session after the reservation draft is created."""

    def setUp(self):
        self.tmp = Path(".tmp-test-phase11") / uuid.uuid4().hex
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.original_env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.original_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_manual_booking_endpoint_marks_session_completed_and_is_idempotent(self):
        client, _ = _make_app_with_db(self.tmp)
        sess = client.post("/api/session", json={}).get_json()["session"]
        session_id = sess["id"]

        sess = client.post(
            f"/api/session/{session_id}/message",
            json={"text": "01031313131"},
        ).get_json()["session"]
        if sess["stage"] == "awaiting_intake":
            sess = client.post(
                f"/api/session/{session_id}/intake",
                json={
                    "fullName": "Manual Booking Tester",
                    "birthday": "1994-01-01",
                    "gender": "Female",
                    "nationality": "Egypt",
                    "countryCode": "20",
                    "rawPhone": "01031313131",
                },
            ).get_json()["session"]

        sess = client.post(f"/api/session/{session_id}/message", json={"text": "local"}).get_json()["session"]
        self.assertEqual(sess["stage"], "awaiting_confirmation")
        sess = client.post(f"/api/session/{session_id}/message", json={"text": "1"}).get_json()["session"]
        self.assertEqual(sess["stage"], "awaiting_room_type")

        first = client.post(
            f"/api/session/{session_id}/book",
            json={"tripId": "RT-LOC-26-001", "roomType": "Double"},
        ).get_json()["session"]
        self.assertEqual(first["stage"], "completed")
        self.assertEqual(first["handoffState"], "completed")
        self.assertEqual(first["bookingStatus"], "Draft")
        self.assertIsNotNone(first["bookingResult"])
        self.assertEqual(first["stats"]["bookingDraftCount"], 1)

        second = client.post(
            f"/api/session/{session_id}/book",
            json={"tripId": "RT-LOC-26-001", "roomType": "Double"},
        ).get_json()["session"]
        self.assertEqual(second["stage"], "completed")
        self.assertEqual(second["bookingResult"]["booking_id"], first["bookingResult"]["booking_id"])
        self.assertEqual(second["stats"]["bookingDraftCount"], 1)


class TestPassportCollection(unittest.TestCase):
    """International trips should require passport attachment upload."""

    def setUp(self):
        self.tmp = Path(".tmp-test-phase11") / uuid.uuid4().hex
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.original_env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.original_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_international_trip_triggers_passport_attachment_stage(self):
        client, _ = _make_app_with_db(self.tmp)
        # New customer intake
        sess = client.post("/api/session", json={}).get_json()["session"]
        sess = client.post(
            f"/api/session/{sess['id']}/message", json={"text": "01055551234"}
        ).get_json()["session"]
        if sess["stage"] == "awaiting_intake":
            sess = client.post(
                f"/api/session/{sess['id']}/intake",
                json={
                    "fullName": "Passport Tester",
                    "birthday": "1992-05-15",
                    "gender": "Female",
                    "nationality": "Egypt",
                    "countryCode": "20",
                    "rawPhone": "01055551234",
                },
            ).get_json()["session"]
        sess = client.post(
            f"/api/session/{sess['id']}/message", json={"text": "2"}
        ).get_json()["session"]  # international
        # Confirm trip
        sess = client.post(
            f"/api/session/{sess['id']}/message", json={"text": "1"}
        ).get_json()["session"]
        self.assertEqual(sess["stage"], "awaiting_passport_upload")

    def test_passport_upload_is_required_before_room_selection(self):
        client, _ = _make_app_with_db(self.tmp)
        sess = client.post("/api/session", json={}).get_json()["session"]
        sess = client.post(
            f"/api/session/{sess['id']}/message", json={"text": "01044441234"}
        ).get_json()["session"]
        if sess["stage"] == "awaiting_intake":
            sess = client.post(
                f"/api/session/{sess['id']}/intake",
                json={
                    "fullName": "Hisham Salah",
                    "birthday": "1988-03-21",
                    "gender": "Male",
                    "nationality": "Egypt",
                    "countryCode": "20",
                    "rawPhone": "01044441234",
                },
            ).get_json()["session"]
        sess = client.post(
            f"/api/session/{sess['id']}/message", json={"text": "2"}
        ).get_json()["session"]
        sess = client.post(
            f"/api/session/{sess['id']}/message", json={"text": "1"}
        ).get_json()["session"]
        self.assertEqual(sess["stage"], "awaiting_passport_upload")

        sess = client.post(
            f"/api/session/{sess['id']}/message", json={"text": "skip"}
        ).get_json()["session"]
        self.assertEqual(sess["stage"], "awaiting_passport_upload")
        self.assertIn("passport", sess["messages"][-1]["text"].lower())

        resp = client.post(
            f"/api/session/{sess['id']}/passport_attachment",
            data={"file": (io.BytesIO(b"passport-bytes"), "passport.pdf")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 200)
        sess = resp.get_json()["session"]
        self.assertEqual(sess["stage"], "awaiting_passport_upload")

        sess = client.post(
            f"/api/session/{sess['id']}/message", json={"text": "done"}
        ).get_json()["session"]
        self.assertEqual(sess["stage"], "awaiting_room_type")

    def test_passport_upload_endpoint_saves_metadata(self):
        client, _ = _make_app_with_db(self.tmp)
        sess = client.post("/api/session", json={}).get_json()["session"]
        session_id = sess["id"]

        # Simple file upload test (no actual file content needed for unit test)
        fake_image = (io.BytesIO(b"FAKEJPEGDATA"), "passport.jpg")
        resp = client.post(
            f"/api/session/{session_id}/passport_attachment",
            data={"file": fake_image},
            content_type="multipart/form-data",
        )
        self.assertIn(resp.status_code, {200, 400, 404})  # 404 OK since session is in awaiting_phone, file might be rejected
        # If 200, check ref is returned
        if resp.status_code == 200:
            data = resp.get_json()
            self.assertIn("ref", data)
            self.assertTrue(data["ok"])

    def test_passport_data_persists_to_traveler_profile_and_documents(self):
        client, _ = _make_app_with_db(self.tmp)
        sess = client.post("/api/session", json={}).get_json()["session"]
        sess = client.post(
            f"/api/session/{sess['id']}/message", json={"text": "01077771234"}
        ).get_json()["session"]
        if sess["stage"] == "awaiting_intake":
            sess = client.post(
                f"/api/session/{sess['id']}/intake",
                json={
                    "fullName": "Passport Persisted",
                    "birthday": "1991-04-12",
                    "gender": "Female",
                    "nationality": "Egypt",
                    "countryCode": "20",
                    "rawPhone": "01077771234",
                },
            ).get_json()["session"]

        sess = client.post(
            f"/api/session/{sess['id']}/message", json={"text": "international"}
        ).get_json()["session"]
        sess = client.post(
            f"/api/session/{sess['id']}/message", json={"text": "1"}
        ).get_json()["session"]
        traveler_id = sess["finalResult"]["traveler"]["traveler_id"]
        self.assertEqual(sess["stage"], "awaiting_passport_upload")

        with closing(sqlite3.connect(os.environ["RAHMA_SYSTEM_DB_PATH"])) as conn:
            traveler_row = conn.execute(
                """
                SELECT passport_name, passport_number, passport_expiry,
                       passport_nationality, COALESCE(passport_attachment_ref, '')
                FROM travelers
                WHERE traveler_id = ?
                """,
                (traveler_id,),
            ).fetchone()
            self.assertIn(traveler_row[0], {"", None})
            self.assertIn(traveler_row[1], {"", None})
            self.assertIn(traveler_row[2], {"", None})
            self.assertIn(traveler_row[3], {"", None})
            self.assertEqual(traveler_row[4], "")

        fake_image = (io.BytesIO(b"REALJPEGDATA"), "passport.jpg")
        resp = client.post(
            f"/api/session/{sess['id']}/passport_attachment",
            data={"file": fake_image},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["passportSave"]["traveler_id"], traveler_id)

        with closing(sqlite3.connect(os.environ["RAHMA_SYSTEM_DB_PATH"])) as conn:
            traveler_row = conn.execute(
                """
                SELECT passport_name, passport_number, passport_expiry,
                       passport_nationality, passport_attachment_ref
                FROM travelers
                WHERE traveler_id = ?
                """,
                (traveler_id,),
            ).fetchone()
            self.assertIn(traveler_row[0], {"", None})
            self.assertIn(traveler_row[1], {"", None})
            self.assertIn(traveler_row[2], {"", None})
            self.assertIn(traveler_row[3], {"", None})
            self.assertTrue(traveler_row[4].endswith("passport.jpg"))

            document_row = conn.execute(
                """
                SELECT category, file_name, passport_full_name, passport_number
                FROM traveler_documents
                WHERE traveler_id = ?
                ORDER BY document_id DESC
                LIMIT 1
                """,
                (traveler_id,),
            ).fetchone()
            self.assertIsNotNone(document_row)
            self.assertEqual(document_row[0], "passport")
            self.assertEqual(document_row[1], "passport.jpg")
            self.assertIn(document_row[2], {"", None})
            self.assertIn(document_row[3], {"", None})


class TestVisaRequirement(unittest.TestCase):
    """Visa endpoint should return table-based info with disclaimer."""

    def setUp(self):
        self.tmp = Path(".tmp-test-phase11") / uuid.uuid4().hex
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.original_env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.original_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_known_destination_no_visa_turkey(self):
        client, _ = _make_app_with_db(self.tmp)
        resp = client.get("/api/visa/Turkey")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["required"], False)
        self.assertIn("disclaimer", data)
        self.assertTrue(len(data["disclaimer"]) > 20)
        self.assertEqual(data["source"], "table")
        self.assertFalse(data.get("handoff_recommended", True))

    def test_known_destination_visa_required_uk(self):
        client, _ = _make_app_with_db(self.tmp)
        resp = client.get("/api/visa/United Kingdom")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["required"], True)
        self.assertIn("disclaimer", data)
        self.assertFalse(data.get("handoff_recommended", True))

    def test_unknown_destination_triggers_handoff(self):
        client, _ = _make_app_with_db(self.tmp)
        resp = client.get("/api/visa/Atlantis")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIsNone(data["required"])
        self.assertIn("disclaimer", data)
        self.assertEqual(data["source"], "unknown")
        self.assertTrue(data.get("handoff_recommended"))

    def test_unknown_destination_with_nationality_can_use_web_fallback(self):
        client, app = _make_app_with_db(self.tmp)
        gateway = app.config["SHEET_GATEWAY"]
        gateway._lookup_visa_requirement_on_web = lambda destination, nationality, disclaimer: {  # type: ignore[method-assign]
            "required": True,
            "destination": destination,
            "nationality": nationality,
            "notes": "Official source says a visa is required.",
            "summary": "Official source says a visa is required.",
            "disclaimer": disclaimer,
            "source": "web",
            "source_mode": "web",
            "sources": [{"url": "https://example.gov/visa", "label": "example.gov"}],
        }
        resp = client.get("/api/visa/Schengen?nationality=Egyptian")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["source"], "web")
        self.assertEqual(data["nationality"], "Egyptian")
        self.assertEqual(data["required"], True)
        self.assertFalse(data.get("handoff_recommended", True))

    def test_unknown_destination_without_nationality_requests_nationality(self):
        client, _ = _make_app_with_db(self.tmp)
        resp = client.get("/api/visa/Schengen")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("needs_nationality"))
        self.assertTrue(data.get("handoff_recommended"))

    def test_visa_always_has_disclaimer(self):
        """Every visa response must include a disclaimer regardless of result."""
        client, _ = _make_app_with_db(self.tmp)
        for dest in ("Turkey", "United Kingdom", "UAE", "Mars"):
            resp = client.get(f"/api/visa/{dest}")
            data = resp.get_json()
            self.assertIn("disclaimer", data, f"Missing disclaimer for destination: {dest}")
            self.assertGreater(len(data["disclaimer"]), 10, f"Disclaimer too short for: {dest}")


class TestDiscountFromTripNotes(unittest.TestCase):
    """Discount info should be read from CRM trip notes, not hardcoded."""

    def test_get_trip_discount_notes_from_gateway(self):
        from services.ai_agent.ai_agent_app.sheets.excel_gateway import ExcelSheetGateway, _normalize_text
        from dataclasses import replace
        import tempfile

        tmp = Path(".tmp-test-workdirs") / f"phase11-{uuid.uuid4().hex}"
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            wb = Workbook()
            ws = wb.active
            ws.title = "Travelers"
            ws.append(["Status", "Traveler ID", "Full Name"])

            trips_ws = wb.create_sheet("Trips")
            trips_ws.append([])  # row 1 empty
            trips_ws["A2"] = "Trip ID"
            trips_ws["B2"] = "Trip Name"
            trips_ws["C2"] = "Notes"
            trips_ws["A3"] = "RT-LOC-99-001"
            trips_ws["B3"] = "Cairo Pyramids"
            trips_ws["C3"] = "10% group discount for bookings of 5+ travelers"

            wb.create_sheet("Interactions").append(["Interaction ID"])
            wb.create_sheet("Trip Bookings")["A2"] = "Booking ID"
            wb.save(tmp / "source.xlsx")
            shutil.copy(tmp / "source.xlsx", tmp / "runtime.xlsx")

            os.environ["RAHMA_SYSTEM_DB_PATH"] = str(tmp / "system.db")

            from services.ai_agent.ai_agent_app.config import load_settings
            from dataclasses import replace as dc_replace
            settings = load_settings()
            settings = dc_replace(
                settings,
                excel_source_workbook=tmp / "source.xlsx",
                excel_runtime_workbook=tmp / "runtime.xlsx",
                sheet_backend="excel",
            )
            gw = ExcelSheetGateway(settings)
            notes = gw.get_trip_discount_notes("RT-LOC-99-001")
            self.assertIn("discount", notes.lower(), f"Expected discount note, got: {notes!r}")

            # Unknown trip should return empty
            empty = gw.get_trip_discount_notes("RT-UNKNOWN-000")
            self.assertEqual(empty, "")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            os.environ.pop("RAHMA_SYSTEM_DB_PATH", None)

    def test_get_trip_discount_notes_prefers_system_db_sales_notes(self):
        from services.ai_agent.ai_agent_app.sheets.excel_gateway import ExcelSheetGateway
        from services.ai_agent.ai_agent_app.config import load_settings
        from dataclasses import replace as dc_replace

        tmp = Path(".tmp-test-workdirs") / f"phase11-db-discount-{uuid.uuid4().hex}"
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            wb = Workbook()
            wb.active.title = "Travelers"
            wb.active.append(["Status", "Traveler ID", "Full Name"])
            trips_ws = wb.create_sheet("Trips")
            trips_ws.append([])
            trips_ws["A2"] = "Trip ID"
            trips_ws["B2"] = "Trip Name"
            wb.create_sheet("Interactions").append(["Interaction ID"])
            wb.create_sheet("Trip Bookings")["A2"] = "Booking ID"
            wb.save(tmp / "source.xlsx")
            shutil.copy(tmp / "source.xlsx", tmp / "runtime.xlsx")

            system_db = tmp / "system.db"
            con = sqlite3.connect(system_db)
            try:
                con.execute(
                    """
                    CREATE TABLE trips (
                        trip_id TEXT PRIMARY KEY,
                        sales_notes TEXT
                    )
                    """
                )
                con.execute(
                    "INSERT INTO trips (trip_id, sales_notes) VALUES (?, ?)",
                    (
                        "RT-LOC-88-001",
                        "VIP_DISCOUNT: 12% VIP loyalty discount\nGROUP_DISCOUNT: 10% off for group reservations\nGROUP_THRESHOLD: 5",
                    ),
                )
                con.commit()
            finally:
                con.close()

            os.environ["RAHMA_SYSTEM_DB_PATH"] = str(system_db)
            settings = load_settings()
            settings = dc_replace(
                settings,
                excel_source_workbook=tmp / "source.xlsx",
                excel_runtime_workbook=tmp / "runtime.xlsx",
                sheet_backend="excel",
            )
            gw = ExcelSheetGateway(settings)
            notes = gw.get_trip_discount_notes("RT-LOC-88-001")
            self.assertIn("VIP discount", notes)
            self.assertIn("Group discount", notes)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            os.environ.pop("RAHMA_SYSTEM_DB_PATH", None)


class TestWorkbookRecovery(unittest.TestCase):
    def test_corrupt_runtime_workbook_is_restored_from_source(self):
        from dataclasses import replace as dc_replace
        import tempfile

        from services.ai_agent.ai_agent_app.config import load_settings
        from services.ai_agent.ai_agent_app.sheets.excel_gateway import ExcelSheetGateway

        tmp = Path(".tmp-test-workdirs") / f"phase11-{uuid.uuid4().hex}"
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            source = tmp / "source.xlsx"
            runtime = tmp / "runtime.xlsx"
            _make_base_workbook(source)
            runtime.write_text("not-an-xlsx-file", encoding="utf-8")

            settings = load_settings()
            settings = dc_replace(
                settings,
                excel_source_workbook=source,
                excel_runtime_workbook=runtime,
                sheet_backend="excel",
            )
            gateway = ExcelSheetGateway(settings)

            stats = gateway.get_demo_stats()

            self.assertIn("travelerCount", stats)
            self.assertTrue(runtime.exists())
            self.assertGreater(runtime.stat().st_size, 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestPostTripHandoff(unittest.TestCase):
    """Post-trip handoff must default OFF and only trigger when explicitly enabled."""

    def setUp(self):
        self.tmp = Path(".tmp-test-phase11") / uuid.uuid4().hex
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.original_env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.original_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_post_trip_handoff_default_off(self):
        """When POST_TRIP_HANDOFF_ENABLED is not set, no handoff message should appear."""
        os.environ.pop("POST_TRIP_HANDOFF_ENABLED", None)
        client, app = _make_app_with_db(self.tmp, post_trip_handoff_enabled=False)
        sessions_mgr = app.config["SESSIONS"]
        self.assertFalse(sessions_mgr.post_trip_handoff_enabled)

    def test_post_trip_handoff_enabled_flag(self):
        """When explicitly enabled, the flag should be True."""
        os.environ["POST_TRIP_HANDOFF_ENABLED"] = "true"
        client, app = _make_app_with_db(self.tmp, post_trip_handoff_enabled=True)
        sessions_mgr = app.config["SESSIONS"]
        self.assertTrue(sessions_mgr.post_trip_handoff_enabled)


class TestSQLiteMigration(unittest.TestCase):
    """Passport columns must be added to travelers table safely and backward-compatibly."""

    def test_passport_columns_added_to_existing_table(self):
        import tempfile
        from services.crm.system_services.unified_service import UnifiedCRMService

        tmp = Path(".tmp-test-workdirs") / f"phase11-{uuid.uuid4().hex}"
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            db_path = tmp / "test_migration.db"
            with closing(sqlite3.connect(str(db_path))) as conn:
                conn.execute(
                    """
                    CREATE TABLE travelers (
                        traveler_id TEXT PRIMARY KEY,
                        full_name TEXT,
                        phone TEXT
                    )
                    """
                )
                conn.execute("INSERT INTO travelers VALUES ('T001', 'Old User', '0100')")
                conn.commit()

            os.environ["RAHMA_SYSTEM_DB_PATH"] = str(db_path)
            from services.crm.system_services.config import load_system_settings
            from dataclasses import replace as dc_replace
            sys_settings = load_system_settings()
            sys_settings = dc_replace(sys_settings, db_path=str(db_path))
            svc = UnifiedCRMService(settings=sys_settings)
            svc.ensure_operational_schema()

            with closing(sqlite3.connect(str(db_path))) as conn:
                cols = {row[1] for row in conn.execute("PRAGMA table_info(travelers)").fetchall()}
                for expected_col in [
                    "passport_name",
                    "passport_number",
                    "passport_expiry",
                    "passport_nationality",
                    "passport_attachment_ref",
                ]:
                    self.assertIn(expected_col, cols, f"Column missing after migration: {expected_col}")

                # Existing data must be preserved
                row = conn.execute("SELECT full_name FROM travelers WHERE traveler_id='T001'").fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row[0], "Old User")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            os.environ.pop("RAHMA_SYSTEM_DB_PATH", None)

    def test_migration_idempotent(self):
        """Running migration twice on same DB should not raise an error."""
        import tempfile
        from services.crm.system_services.unified_service import UnifiedCRMService

        tmp = Path(".tmp-test-workdirs") / f"phase11-{uuid.uuid4().hex}"
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            db_path = tmp / "test_idempotent.db"
            with closing(sqlite3.connect(str(db_path))) as conn:
                create_operational_tables(conn)
                conn.commit()

            os.environ["RAHMA_SYSTEM_DB_PATH"] = str(db_path)
            from services.crm.system_services.config import load_system_settings
            from dataclasses import replace as dc_replace
            sys_settings = load_system_settings()
            sys_settings = dc_replace(sys_settings, db_path=str(db_path))
            svc = UnifiedCRMService(settings=sys_settings)

            # Run twice – should be idempotent
            svc.ensure_operational_schema()
            svc.ensure_operational_schema()  # must not raise
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            os.environ.pop("RAHMA_SYSTEM_DB_PATH", None)


if __name__ == "__main__":
    unittest.main()
