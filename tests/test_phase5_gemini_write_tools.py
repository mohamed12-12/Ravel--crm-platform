from __future__ import annotations

import json
import sqlite3
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from services.ai_agent.ai_agent_app.agent.gemini_agent import GeminiAgent

from test_phase2_gemini_tool_loop import LoopProviderStub, function_call_response, text_response, FakeCRMService


class WriteCRMService(FakeCRMService):
    def __init__(self, connection: sqlite3.Connection) -> None:
        super().__init__(connection)
        self.created_leads: list[dict] = []
        self.updated_lead_stages: list[dict] = []
        self.created_bookings: list[dict] = []
        self.created_handoffs: list[dict] = []
        self.connection.execute("PRAGMA foreign_keys = OFF")

    def upsert_lead(self, **kwargs):
        created_at = "2026-07-04T10:00:00"
        lead_id = kwargs.get("lead_id") or f"LD{len(self.created_leads) + 100:05d}"
        row = {
            "lead_id": lead_id,
            "created_at": created_at,
            "updated_at": created_at,
            "customer_name": kwargs.get("customer_name", ""),
            "raw_phone": kwargs.get("raw_phone", ""),
            "integrated_whatsapp": kwargs.get("raw_phone", ""),
            "phone_lookup_key": f"{kwargs.get('country_code', '20')}:{''.join(ch for ch in str(kwargs.get('raw_phone', '')) if ch.isdigit())[-10:]}",
            "traveler_id": kwargs.get("traveler_id") or None,
            "traveler_status": kwargs.get("traveler_status") or None,
            "customer_tier": kwargs.get("customer_tier") or None,
            "match_status": kwargs.get("match_status") or None,
            "lead_stage": kwargs.get("lead_stage", "New Lead"),
            "lead_source": kwargs.get("lead_source", "Gemini Agent"),
            "channel": kwargs.get("channel", "web"),
            "preferred_trip_type": kwargs.get("preferred_trip_type") or None,
            "group_size": int(kwargs.get("group_size") or 1),
            "interested_trip_ids": kwargs.get("interested_trip_ids") or None,
            "suggested_trip_ids": kwargs.get("suggested_trip_ids") or None,
            "priority": kwargs.get("priority", "Medium"),
            "follow_up_status": kwargs.get("follow_up_status") or None,
            "follow_up_due_date": kwargs.get("follow_up_due_date") or None,
            "last_interaction_id": kwargs.get("last_interaction_id") or None,
            "interaction_count": 1,
            "notes": kwargs.get("notes") or None,
            "booking_id": kwargs.get("booking_id") or None,
            "flow_key": kwargs.get("flow_key") or None,
            "current_step": kwargs.get("current_step") or None,
            "handoff_required": 1 if kwargs.get("handoff_required") else 0,
            "handoff_reason": kwargs.get("handoff_reason") or None,
            "language": kwargs.get("language") or None,
            "handoff_id": None,
        }
        self.connection.execute(
            """
            INSERT INTO leads (
                lead_id, created_at, updated_at, customer_name, raw_phone, integrated_whatsapp,
                phone_lookup_key, traveler_id, traveler_status, customer_tier, match_status,
                lead_stage, lead_source, channel, preferred_trip_type, group_size,
                interested_trip_ids, suggested_trip_ids, priority, follow_up_status, follow_up_due_date,
                last_interaction_id, interaction_count, notes, booking_id, flow_key, current_step,
                handoff_required, handoff_reason, language, handoff_id
            ) VALUES (:lead_id, :created_at, :updated_at, :customer_name, :raw_phone, :integrated_whatsapp,
                      :phone_lookup_key, :traveler_id, :traveler_status, :customer_tier, :match_status,
                      :lead_stage, :lead_source, :channel, :preferred_trip_type, :group_size,
                      :interested_trip_ids, :suggested_trip_ids, :priority, :follow_up_status, :follow_up_due_date,
                      :last_interaction_id, :interaction_count, :notes, :booking_id, :flow_key, :current_step,
                      :handoff_required, :handoff_reason, :language, :handoff_id)
            """,
            row,
        )
        self.connection.commit()
        self.created_leads.append(dict(row))
        return {
            "lead_id": lead_id,
            "lead_stage": row["lead_stage"],
            "priority": row["priority"],
            "follow_up_status": row["follow_up_status"],
            "follow_up_due_date": row["follow_up_due_date"],
            "interaction_id": row["last_interaction_id"] or "",
            "created": True,
        }

    def update_lead_stage(self, lead_id: str, **kwargs):
        stage = kwargs.get("requested_stage") or "Qualified"
        self.connection.execute(
            "UPDATE leads SET lead_stage = ?, priority = ?, follow_up_status = ?, follow_up_due_date = ?, updated_at = ? WHERE lead_id = ?",
            (
                stage,
                kwargs.get("priority") or "Medium",
                kwargs.get("follow_up_status") or None,
                kwargs.get("follow_up_due_date") or None,
                "2026-07-04T10:00:00",
                lead_id,
            ),
        )
        self.connection.commit()
        result = {
            "lead_id": lead_id,
            "lead_stage": stage,
            "priority": kwargs.get("priority") or "Medium",
            "follow_up_status": kwargs.get("follow_up_status") or "",
            "follow_up_due_date": kwargs.get("follow_up_due_date") or "",
        }
        self.updated_lead_stages.append(result)
        return result

    def create_booking_draft(self, **kwargs):
        trip_id = kwargs.get("trip_id", "")
        traveler_id = kwargs.get("traveler_id", "")
        trip = self.connection.execute("SELECT * FROM trips WHERE trip_id = ?", (trip_id,)).fetchone()
        if not trip:
            raise ValueError(f"Trip was not found for booking: {trip_id}")
        remaining = int(trip["remaining_places"] or 0)
        booking_id = f"B-{len(self.created_bookings) + 1:05d}"
        booking = {
            "booking_id": booking_id,
            "trip_id": trip_id,
            "trip_name": trip["trip_name"],
            "traveler_id": traveler_id,
            "traveler_name": kwargs.get("traveler_name", ""),
            "room_type": kwargs.get("room_type", ""),
            "flight_option": kwargs.get("flight_option", ""),
            "date_option": kwargs.get("date_option", ""),
            "currency": kwargs.get("currency", ""),
            "booking_status": "Draft",
            "draft_created_at": "2026-07-04T10:00:00",
            "booking_source": kwargs.get("source", ""),
            "lead_id": kwargs.get("lead_id", ""),
            "interaction_id": "INT-000001",
            "payment_status": "Pending",
            "passport_required": 1 if kwargs.get("passport_required") else 0,
            "passport_status": kwargs.get("passport_status", ""),
            "group_size": int(kwargs.get("group_size") or 1),
            "booking_notes": kwargs.get("agent_notes", ""),
            "available_before_draft": remaining,
            "available_after_draft": max(remaining - 1, 0),
            "lead_update": None,
        }
        self.connection.execute(
            """
            INSERT INTO trip_bookings (
                booking_id, trip_id, trip_name, traveler_id, traveler_name, room_type, flight_option,
                date_option, currency, booking_status, draft_created_at, booking_source, lead_id,
                interaction_id, payment_status, passport_required, passport_status, group_size, booking_notes
            ) VALUES (
                :booking_id, :trip_id, :trip_name, :traveler_id, :traveler_name, :room_type, :flight_option,
                :date_option, :currency, :booking_status, :draft_created_at, :booking_source, :lead_id,
                :interaction_id, :payment_status, :passport_required, :passport_status, :group_size, :booking_notes
            )
            """,
            booking,
        )
        self.connection.execute(
            "UPDATE trips SET remaining_places = ? WHERE trip_id = ?",
            (max(remaining - 1, 0), trip_id),
        )
        self.connection.commit()
        booking["write_result"] = {
            "booking_draft": {
                "booking_id": booking_id,
                "trip_id": trip_id,
                "traveler_id": traveler_id,
                "lead_id": kwargs.get("lead_id", ""),
            },
            "interaction_log": {"interaction_id": "INT-000001"},
            "lead_update": None,
            "event_trail": [],
        }
        self.created_bookings.append(booking)
        return booking

    def create_handoff_case(self, **kwargs):
        handoff_id = f"H-{len(self.created_handoffs) + 1:08d}"
        reason_code = kwargs.get("reason_code", "")
        reason_text = kwargs.get("reason_text", "")
        lead_id = kwargs.get("lead_id", "")
        traveler_id = kwargs.get("traveler_id", "")
        trip_id = kwargs.get("trip_id", "")
        priority = kwargs.get("priority", "High")
        self.connection.execute(
            """
            INSERT INTO handoff_queue (
                handoff_id, created_at, lead_id, traveler_id, trip_id, flow_key, reason, priority, channel, status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                handoff_id,
                "2026-07-04T10:00:00",
                lead_id or None,
                traveler_id or None,
                trip_id or None,
                kwargs.get("flow_key", "") or None,
                reason_text or reason_code or "manual_handoff",
                priority,
                kwargs.get("channel", "") or None,
                "Pending",
                kwargs.get("notes", "") or None,
            ),
        )
        if kwargs.get("update_lead", True) and lead_id:
            self.connection.execute(
                "UPDATE leads SET handoff_required = 1, handoff_reason = ?, handoff_id = ?, lead_stage = ? WHERE lead_id = ?",
                (reason_code or reason_text, handoff_id, kwargs.get("lead_stage_override", "Needs Review"), lead_id),
            )
        self.connection.commit()
        result = {
            "handoff_id": handoff_id,
            "lead_id": lead_id,
            "traveler_id": traveler_id,
            "trip_id": trip_id,
            "reason_code": reason_code,
            "reason_text": reason_text or reason_code or "manual_handoff",
            "priority": priority,
            "status": "Pending",
            "package": {},
            "event_id": "",
        }
        self.created_handoffs.append(result)
        return result


class TestPhase5GeminiWriteTools(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE travelers (
                traveler_id TEXT PRIMARY KEY,
                status TEXT,
                full_name TEXT,
                phone_code TEXT,
                whatsapp_raw TEXT,
                integrated_whatsapp TEXT,
                normalized_whatsapp TEXT,
                phone_lookup_key TEXT,
                passport_name TEXT,
                passport_number TEXT,
                passport_expiry TEXT,
                passport_nationality TEXT,
                passport_attachment_ref TEXT
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
                created_at TEXT,
                updated_at TEXT,
                customer_name TEXT,
                raw_phone TEXT,
                integrated_whatsapp TEXT,
                phone_lookup_key TEXT,
                traveler_id TEXT,
                traveler_status TEXT,
                customer_tier TEXT,
                match_status TEXT,
                lead_stage TEXT,
                lead_source TEXT,
                channel TEXT,
                preferred_trip_type TEXT,
                group_size INTEGER,
                interested_trip_ids TEXT,
                suggested_trip_ids TEXT,
                priority TEXT,
                follow_up_status TEXT,
                follow_up_due_date TEXT,
                last_interaction_id TEXT,
                interaction_count INTEGER,
                notes TEXT,
                booking_id TEXT,
                flow_key TEXT,
                current_step TEXT,
                handoff_required INTEGER,
                handoff_reason TEXT,
                language TEXT,
                handoff_id TEXT
            );
            CREATE TABLE trip_bookings (
                booking_id TEXT PRIMARY KEY,
                trip_id TEXT,
                trip_name TEXT,
                traveler_id TEXT,
                traveler_name TEXT,
                room_type TEXT,
                flight_option TEXT,
                date_option TEXT,
                currency TEXT,
                booking_status TEXT,
                draft_created_at TEXT,
                booking_source TEXT,
                lead_id TEXT,
                interaction_id TEXT,
                payment_status TEXT,
                passport_required INTEGER,
                passport_status TEXT,
                group_size INTEGER,
                booking_notes TEXT
            );
            CREATE TABLE handoff_queue (
                handoff_id TEXT PRIMARY KEY,
                created_at TEXT,
                lead_id TEXT,
                traveler_id TEXT,
                trip_id TEXT,
                flow_key TEXT,
                reason TEXT,
                priority TEXT,
                channel TEXT,
                status TEXT,
                notes TEXT
            );
            """
        )
        self.conn.executemany(
            "INSERT INTO travelers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("TR00001", "VIP", "Mona Ali", "20", "1112223333", "+201112223333", "+201112223333", "20:1112223333", "Mona Ali", "P111", "2030-12-31", "Egyptian", "passport/TR00001/passport.jpg"),
                ("TR00002", "Active", "Omar Nabil", "20", "2223334444", "+202223334444", "+202223334444", "20:2223334444", "", "", "", "", ""),
                ("TR00003", "Blacklisted", "Blocked Traveler", "20", "3334445555", "+203334445555", "+203334445555", "20:3334445555", "", "", "", "", ""),
            ],
        )
        self.conn.executemany(
            "INSERT INTO trips VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("RT-LOC-26-001", "Sinai Trek", "Local", "2026-08-10", "2026-08-15", "Open", "2000$", 5),
                ("RT-INT-26-001", "Istanbul Explorer", "International", "2026-09-10", "2026-09-18", "Open", "2500$", 4),
                ("RT-LOC-26-CLS", "Closed Trip", "Local", "2026-07-01", "2026-07-05", "Closed", "1800$", 0),
            ],
        )
        self.conn.executemany(
            "INSERT INTO leads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("LD00001", "2026-07-04T09:00:00", "2026-07-04T09:00:00", "Mona Ali", "1112223333", "+201112223333", "20:1112223333", "TR00001", "VIP", "VIP", "single_match", "Qualified", "Manual Web UI", "web", "international", 1, "RT-INT-26-001", "RT-INT-26-001", "High", "Follow Up Soon", "2026-07-06", "", 1, "", "", "", "", 0, "", "en", None),
            ],
        )
        self.conn.executemany(
            "INSERT INTO trip_bookings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("B00001", "RT-LOC-26-001", "Sinai Trek", "TR00001", "Mona Ali", "Double", "With Flight", "", "EGP", "Draft", "2026-07-04T09:30:00", "Manual UI", "LD00001", "INT-000001", "Pending", 0, "", 1, "Existing booking"),
            ],
        )
        self.conn.commit()
        self.settings = SimpleNamespace(ai_agent_mode="gemini", default_country_code="20", ai_agent_system_prompt="You are Rahvel Agent.")
        self.service = WriteCRMService(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    @contextmanager
    def _patched_service(self):
        with patch("services.ai_agent.ai_agent_app.agent.read_only_tools.get_system_service", return_value=self.service):
            yield

    def _build_agent(self, responses: list, *, write_tools_enabled: bool = True) -> GeminiAgent:
        return GeminiAgent(
            settings=self.settings,
            provider=LoopProviderStub(responses),
            write_tools_enabled=write_tools_enabled,
        )

    def test_approved_create_lead_executes(self) -> None:
        with self._patched_service():
            agent = self._build_agent(
                [
                    function_call_response(
                        "create_lead",
                        {
                            "customer_name": "Amina Hassan",
                            "raw_phone": "4445556666",
                            "country_code": "20",
                            "lead_source": "Instagram",
                            "channel": "web",
                            "preferred_trip_type": "Local",
                        },
                    ),
                    text_response("Lead created successfully."),
                ]
            )
            result = agent.respond(user_message="create lead", session_context={"session_id": "sess-lead", "raw_phone": "4445556666", "customer_name": "Amina Hassan"})

        self.assertEqual(len(self.service.created_leads), 1)
        self.assertEqual(result["write_results"][0]["name"], "create_lead")
        self.assertTrue(result["write_results"][0]["executed"])
        self.assertIn("Lead", result["reply"])

    def test_rejected_create_lead_does_not_execute(self) -> None:
        with self._patched_service():
            agent = self._build_agent(
                [
                    function_call_response(
                        "create_lead",
                        {
                            "customer_name": "Mona Ali",
                            "raw_phone": "1112223333",
                            "country_code": "20",
                        },
                    ),
                    text_response(""),
                ]
            )
            result = agent.respond(user_message="create lead", session_context={"session_id": "sess-lead-block", "raw_phone": "1112223333", "customer_name": "Mona Ali"})

        self.assertEqual(len(self.service.created_leads), 0)
        self.assertFalse(result["write_results"][0]["executed"])
        self.assertIn("open lead", result["reply"].lower())

    def test_duplicate_lead_blocked(self) -> None:
        with self._patched_service():
            agent = self._build_agent(
                [
                    function_call_response(
                        "create_lead",
                        {
                            "customer_name": "Mona Ali",
                            "raw_phone": "1112223333",
                            "country_code": "20",
                        },
                    ),
                    text_response(""),
                ]
            )
            result = agent.respond(user_message="create lead", session_context={"session_id": "sess-dup", "traveler_id": "TR00001", "raw_phone": "1112223333"})

        self.assertFalse(result["write_results"][0]["executed"])
        self.assertEqual(len(self.service.created_leads), 0)

    def test_approved_create_booking_draft_executes(self) -> None:
        with self._patched_service():
            agent = self._build_agent(
                [
                    function_call_response(
                        "create_booking_draft",
                        {
                            "traveler_id": "TR00002",
                            "trip_id": "RT-LOC-26-001",
                            "room_type": "Double",
                            "flight_option": "Without Flight",
                            "lead_id": "LD00099",
                        },
                    ),
                    text_response("Booking draft created."),
                ]
            )
            result = agent.respond(
                user_message="create booking draft",
                session_context={
                    "session_id": "sess-book",
                    "traveler_id": "TR00002",
                    "trip_id": "RT-LOC-26-001",
                    "room_type": "Double",
                    "flight_option": "Without Flight",
                    "lead_id": "LD00099",
                },
            )

        self.assertEqual(len(self.service.created_bookings), 1)
        self.assertTrue(result["write_results"][0]["executed"])
        self.assertIn("Booking draft", result["reply"])

    def test_booking_draft_without_lead_creates_and_links_current_lead(self) -> None:
        with self._patched_service():
            agent = self._build_agent(
                [
                    function_call_response(
                        "create_booking_draft",
                        {
                            "traveler_id": "TR00002",
                            "trip_id": "RT-LOC-26-001",
                            "room_type": "Single",
                            "flight_option": "Without Flight",
                        },
                    ),
                    text_response("Booking draft created."),
                ]
            )
            result = agent.respond(
                user_message="create booking draft",
                session_context={
                    "session_id": "sess-book-with-lead",
                    "traveler_id": "TR00002",
                    "trip_id": "RT-LOC-26-001",
                    "room_type": "Single",
                    "flight_option": "Without Flight",
                },
            )

        self.assertTrue(result["write_results"][0]["executed"])
        self.assertEqual(len(self.service.created_leads), 1)
        self.assertEqual(len(self.service.created_bookings), 1)
        self.assertEqual(self.service.created_bookings[0]["lead_id"], self.service.created_leads[0]["lead_id"])

    def test_missing_room_blocks_booking_draft(self) -> None:
        with self._patched_service():
            agent = self._build_agent(
                [
                    function_call_response(
                        "create_booking_draft",
                        {
                            "traveler_id": "TR00002",
                            "trip_id": "RT-LOC-26-001",
                            "flight_option": "Without Flight",
                        },
                    ),
                    text_response(""),
                ]
            )
            result = agent.respond(
                user_message="create booking draft",
                session_context={"session_id": "sess-room", "traveler_id": "TR00002", "trip_id": "RT-LOC-26-001", "flight_option": "Without Flight"},
            )

        self.assertFalse(result["write_results"][0]["executed"])
        self.assertIn("room type", result["reply"].lower())
        self.assertEqual(len(self.service.created_bookings), 0)

    def test_invalid_trip_blocks_booking_draft(self) -> None:
        with self._patched_service():
            agent = self._build_agent(
                [
                    function_call_response(
                        "create_booking_draft",
                        {
                            "traveler_id": "TR00002",
                            "trip_id": "RT-MISSING",
                            "room_type": "Double",
                            "flight_option": "Without Flight",
                        },
                    ),
                    text_response(""),
                ]
            )
            result = agent.respond(
                user_message="create booking draft",
                session_context={"session_id": "sess-trip", "traveler_id": "TR00002", "trip_id": "RT-MISSING", "room_type": "Double", "flight_option": "Without Flight"},
            )

        self.assertFalse(result["write_results"][0]["executed"])
        self.assertIn("trip rt-missing was not found", result["reply"].lower())
        self.assertEqual(len(self.service.created_bookings), 0)

    def test_international_booking_sets_passport_required(self) -> None:
        with self._patched_service():
            agent = self._build_agent(
                [
                    function_call_response(
                        "create_booking_draft",
                        {
                            "traveler_id": "TR00001",
                            "trip_id": "RT-INT-26-001",
                            "room_type": "Double",
                            "flight_option": "With Flight",
                            "passport_attachment_ref": "passport/TR00001/passport.jpg",
                            "lead_id": "LD00001",
                        },
                    ),
                    text_response("Booking draft created."),
                ]
            )
            result = agent.respond(
                user_message="create booking draft",
                session_context={
                    "session_id": "sess-intl",
                    "traveler_id": "TR00001",
                    "trip_id": "RT-INT-26-001",
                    "room_type": "Double",
                    "flight_option": "With Flight",
                    "passport_attachment_ref": "passport/TR00001/passport.jpg",
                    "lead_id": "LD00001",
                },
            )

        booking = result["write_results"][0]["result"]["booking_result"]
        self.assertTrue(booking["passport_required"])
        self.assertEqual(booking["passport_status"], "provided")
        self.assertEqual(len(self.service.created_bookings), 1)

    def test_approved_handoff_creates_handoff(self) -> None:
        with self._patched_service():
            agent = self._build_agent(
                [
                    function_call_response(
                        "create_handoff",
                        {
                            "traveler_id": "TR00003",
                            "lead_id": "LD00077",
                            "user_requested_human": True,
                            "reason_text": "Traveler asked for a human agent.",
                        },
                    ),
                    text_response("Handoff created."),
                ]
            )
            result = agent.respond(
                user_message="I need a human agent",
                session_context={"session_id": "sess-handoff", "traveler_id": "TR00003", "lead_id": "LD00077", "user_requested_human": True},
            )

        self.assertEqual(len(self.service.created_handoffs), 1)
        self.assertTrue(result["write_results"][0]["executed"])
        self.assertIn("handoff", result["reply"].lower())

    def test_write_audit_log_records_approved_and_rejected_actions(self) -> None:
        with self._patched_service():
            approved_agent = self._build_agent(
                [
                    function_call_response(
                        "create_handoff",
                        {
                            "traveler_id": "TR00003",
                            "lead_id": "LD00077",
                            "user_requested_human": True,
                            "reason_text": "Traveler asked for a human agent.",
                        },
                    ),
                    text_response("Handoff created."),
                ]
            )
            rejected_agent = self._build_agent(
                [
                    function_call_response(
                        "create_lead",
                        {"customer_name": "Mona Ali", "raw_phone": "1112223333", "country_code": "20"},
                    ),
                    text_response(""),
                ]
            )
            with self.assertLogs("rahma_agent", level="INFO") as captured:
                approved_agent.respond(
                    user_message="handoff",
                    session_context={"session_id": "sess-audit-1", "traveler_id": "TR00003", "lead_id": "LD00077", "user_requested_human": True},
                )
                rejected_agent.respond(
                    user_message="create lead",
                    session_context={"session_id": "sess-audit-2", "raw_phone": "1112223333", "customer_name": "Mona Ali"},
                )

        combined = "\n".join(captured.output)
        self.assertIn("Write audit", combined)
        self.assertIn("sess-audit-1", combined)
        self.assertIn("sess-audit-2", combined)

    def test_deterministic_mode_unchanged(self) -> None:
        with self._patched_service():
            agent = self._build_agent(
                [
                    function_call_response("create_lead", {"customer_name": "Amina Hassan", "raw_phone": "4445556666"}),
                ],
                write_tools_enabled=False,
            )
            result = agent.respond(user_message="create lead", session_context={"session_id": "sess-det", "raw_phone": "4445556666", "customer_name": "Amina Hassan"})

        self.assertEqual(result["mode"], "gemini")
        self.assertEqual(result["tool_requests"], [])
        self.assertIn("automatic crm writes are disabled", result["reply"].lower())

    def test_read_only_tools_still_work(self) -> None:
        with self._patched_service():
            agent = self._build_agent(
                [
                    function_call_response("search_traveler", {"raw_phone": "1112223333", "country_code": "20"}),
                    text_response("Mona Ali is a VIP traveler."),
                ]
            )
            result = agent.respond(user_message="check traveler", session_context={"session_id": "sess-ro", "raw_phone": "1112223333"})

        self.assertEqual(result["tool_requests"][0]["name"], "search_traveler")
        self.assertIn("Mona Ali", result["reply"])

    def test_forbidden_write_tool_rejected(self) -> None:
        with self._patched_service():
            agent = self._build_agent(
                [
                    function_call_response("create_traveler", {"raw_phone": "4445556666"}),
                ]
            )
            result = agent.respond(user_message="create traveler", session_context={"session_id": "sess-forbidden", "raw_phone": "4445556666"})

        self.assertIn("unsupported tool requested", result["error"].lower())
        self.assertEqual(len(self.service.created_leads), 0)


if __name__ == "__main__":
    unittest.main()
