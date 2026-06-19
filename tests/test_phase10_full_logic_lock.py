"""
Phase 10 — Full Logic Lock
=========================================================
Locks all critical system behaviours before further feature expansion.

Tests covered:
  1. New trip appears to agent — trip added to DB shows in build_trip_result
  2. Cancelled trip never shown to agent
  3. Date-TBD trip shows in tbd list
  4. Agent booking creates real DB lead + sheet records
  5. New traveler gets correct sequential TR-prefixed ID (TR00518 after TR00517)
  6. TR ID skips gaps correctly
  7. First traveler always gets TR00001
  8. Duplicate phone triggers human handoff — no new traveler
  9. Blacklisted traveler blocks outcome, lead is Blocked/Critical
 10. Name conflict on matching phone triggers phone_name_conflict handoff
 11. Existing traveler is matched, not duplicated
 12. create_booking_draft rejects Cancelled trips
 13. create_booking_draft rejects exhausted room types
 14. create_booking_draft succeeds with available capacity
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import unittest
import uuid
from contextlib import closing
from pathlib import Path

from openpyxl import Workbook, load_workbook

# ── path plumbing ──────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
SYSTEM_ROOT = REPO_ROOT / "apps" / "api"
if str(SYSTEM_ROOT) not in sys.path:
    sys.path.insert(0, str(SYSTEM_ROOT))

from services.crm.system_services.unified_service import UnifiedCRMService
from services.crm.system_services.config import SystemServiceSettings

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_phase3_booking_write_through import create_operational_tables


# ── helpers ────────────────────────────────────────────────────────────────────

def _seed_workbook(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Travelers"
    ws.append([
        "Status", "Traveler ID", "Full Name", "Code", "WhatsApp",
        "Integrated WhatsApp", "Phone Lookup Key", "Lead Source",
        "Created At", "Data Audit",
    ])
    trips = wb.create_sheet("Trips")
    trips["A2"] = "Trip ID"; trips["B2"] = "Trip Name"; trips["C2"] = "Type"
    trips["D2"] = "Year"; trips["F2"] = "Start Date"; trips["G2"] = "End Date"
    trips["Z2"] = "Sales Status"; trips["AA2"] = "Data Audit"
    interactions = wb.create_sheet("Interactions")
    interactions.append([
        "Interaction ID", "Timestamp", "Channel", "Customer Name", "Raw Phone",
        "Integrated WhatsApp", "Phone Lookup Key", "Traveler ID", "Matched Row",
        "Status Snapshot", "Intent", "Trip Type", "Suggested Trips",
        "Action Taken", "Handoff Required", "Handoff Reason", "Agent Notes",
    ])
    leads = wb.create_sheet("Leads")
    leads.append([
        "Lead ID", "Created At", "Customer Name", "Raw Phone",
        "Traveler ID", "Lead Stage", "Priority", "Follow Up Status",
        "Handoff Required", "Handoff Reason",
    ])
    trip_bookings = wb.create_sheet("Trip Bookings")
    trip_bookings["A2"] = "Booking ID"; trip_bookings["B2"] = "Trip ID"
    trip_bookings["C2"] = "Trip Name"; trip_bookings["D2"] = "Traveler ID"
    trip_bookings["E2"] = "Traveler Name"; trip_bookings["F2"] = "Room Type"
    trip_bookings["G2"] = "Booking Status"; trip_bookings["H2"] = "Payment Status"
    event_trail = wb.create_sheet("Booking Event Trail")
    event_trail.append([
        "Event ID", "Occurred At", "Event Type", "Event Label",
        "Traveler ID", "Lead ID", "Booking ID", "Trip ID",
    ])
    wb.save(path)
    wb.close()


def _insert_trip(
    connection: sqlite3.Connection,
    trip_id: str,
    trip_name: str,
    trip_type: str,
    sales_status: str = "Open",
    single: int = 5,
    double: int = 5,
    triple: int = 5,
    start_date: str | None = "2026-10-01",
    end_date: str | None = "2026-10-07",
) -> None:
    connection.execute(
        """
        INSERT INTO trips (
            trip_id, trip_name, type, year, start_date, end_date, sales_status,
            single_total, double_total, triple_total,
            single_remaining, double_remaining, triple_remaining,
            draft_holds_single, draft_holds_double, draft_holds_triple
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0)
        """,
        (trip_id, trip_name, trip_type, 2026, start_date, end_date,
         sales_status, single, double, triple, single, double, triple),
    )
    connection.commit()


def _insert_traveler(
    connection: sqlite3.Connection,
    traveler_id: str,
    full_name: str,
    phone_key: str,
    status: str = "Active",
) -> None:
    connection.execute(
        """
        INSERT INTO travelers (
            traveler_id, status, full_name, phone_lookup_key,
            integrated_whatsapp, normalized_whatsapp,
            created_at, last_contacted_at
        ) VALUES (?, ?, ?, ?, ?, ?, '2026-01-01T00:00:00', '2026-01-01T00:00:00')
        """,
        (
            traveler_id, status, full_name, phone_key,
            f"+{phone_key.replace(':', '')}",
            f"+{phone_key.replace(':', '')}",
        ),
    )
    connection.commit()


class Phase10FullLogicLockTests(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp_root = Path(".tmp-test-workdirs")
        self.tmp_root.mkdir(exist_ok=True)
        self.tmp_path = self.tmp_root / f"p10-{uuid.uuid4().hex}"
        self.tmp_path.mkdir()
        self._original_env = dict(os.environ)

        self.db_path = self.tmp_path / "system.db"
        self.workbook = self.tmp_path / "runtime.xlsx"
        _seed_workbook(self.workbook)

        with closing(sqlite3.connect(self.db_path)) as conn:
            create_operational_tables(conn)

        settings = SystemServiceSettings(
            repo_root=REPO_ROOT,
            system_root=SYSTEM_ROOT,
            db_path=self.db_path,
            sheet_backend="excel",
            excel_source_workbook=str(self.workbook),
            excel_runtime_workbook=str(self.workbook),
        )
        self.svc = UnifiedCRMService(settings)
        self.svc.ensure_operational_schema()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._original_env)
        shutil.rmtree(self.tmp_path, ignore_errors=True)

    # ── 1-3. Trip visibility to agent ─────────────────────────────────────────

    def test_new_open_trip_visible_to_agent(self) -> None:
        """Trip inserted into DB must immediately appear in build_trip_result."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            _insert_trip(conn, "RT-LOC-26-500", "New Agent Trip", "Local")

        result = self.svc.build_trip_result("local")
        ids = [t["trip_id"] for t in result["open_trips"]]
        self.assertIn("RT-LOC-26-500", ids)

    def test_cancelled_trip_never_shown_to_agent(self) -> None:
        """Cancelled trips must be excluded from both trip lists."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            _insert_trip(conn, "RT-LOC-26-CANCEL", "Cancelled Trip",
                         "Local", sales_status="Cancelled")

        result = self.svc.build_trip_result("local")
        all_ids = (
            [t["trip_id"] for t in result["open_trips"]] +
            [t["trip_id"] for t in result["date_tbd_trips"]]
        )
        self.assertNotIn("RT-LOC-26-CANCEL", all_ids)

    def test_date_tbd_trip_in_tbd_list(self) -> None:
        """Trip with no dates must appear in date_tbd_trips."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            _insert_trip(conn, "RT-INT-26-TBD", "TBD International",
                         "International", start_date=None, end_date=None)

        result = self.svc.build_trip_result("international")
        tbd_ids = [t["trip_id"] for t in result["date_tbd_trips"]]
        self.assertIn("RT-INT-26-TBD", tbd_ids)

    # ── 4. Agent booking creates real DB lead + sheet records ─────────────────

    def test_agent_booking_creates_lead_and_booking_in_db_and_sheet(self) -> None:
        """Full agent flow: record_agent_outcome + create_booking_draft writes
        traveler, lead, and booking to both DB and the Excel workbook."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            _insert_trip(conn, "RT-LOC-26-BOOK", "Booking Test Trip", "Local")

        outcome = self.svc.record_agent_outcome(
            full_name="Booking Test Traveler",
            raw_phone="1099887766",
            country_code="20",
            trip_type="local",
            channel="web",
            source="Web Demo",
            agent_notes="phase10 test",
        )
        traveler_id = outcome["write_result"]["created_traveler"]["traveler_id"]
        lead_id = outcome["write_result"]["lead_update"]["lead_id"]

        booking = self.svc.create_booking_draft(
            traveler_id=traveler_id,
            traveler_name="Booking Test Traveler",
            trip_id="RT-LOC-26-BOOK",
            room_type="Double",
            currency="EGP",
            lead_id=lead_id,
            channel="web",
        )

        self.assertIsNotNone(booking.get("booking_id"))
        self.assertEqual(booking["trip_id"], "RT-LOC-26-BOOK")
        self.assertEqual(booking["room_type"], "Double")

        # DB verification
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            self.assertIsNotNone(
                conn.execute("SELECT 1 FROM travelers WHERE traveler_id = ?",
                             (traveler_id,)).fetchone()
            )
            self.assertIsNotNone(
                conn.execute("SELECT 1 FROM leads WHERE lead_id = ?",
                             (lead_id,)).fetchone()
            )
            b = conn.execute("SELECT * FROM trip_bookings WHERE booking_id = ?",
                             (booking["booking_id"],)).fetchone()
            self.assertIsNotNone(b)
            self.assertEqual(b["booking_status"], "Draft")

        # Sheet verification
        wb = load_workbook(self.workbook, data_only=True)
        try:
            ws = wb["Trip Bookings"]
            sheet_ids = [ws.cell(r, 1).value
                         for r in range(3, ws.max_row + 1)
                         if ws.cell(r, 1).value]
            self.assertIn(booking["booking_id"], sheet_ids,
                          "Booking must be mirrored to Trip Bookings sheet")
        finally:
            wb.close()

    # ── 5-7. Traveler ID sequencing ───────────────────────────────────────────

    def test_new_traveler_gets_next_sequential_tr_id(self) -> None:
        """next_traveler_id must return TR00518 when TR00517 is the highest."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            _insert_traveler(conn, "TR00515", "Alice", "20:1000000001")
            _insert_traveler(conn, "TR00517", "Bob",   "20:1000000002")

        self.assertEqual(self.svc.next_traveler_id(), "TR00518")

    def test_tr_id_skips_above_gap(self) -> None:
        """Max wins even with gaps: TR00519 + TR00520 → next is TR00521."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            _insert_traveler(conn, "TR00519", "Gapped", "20:1000000010")
            _insert_traveler(conn, "TR00520", "Final",  "20:1000000011")

        self.assertEqual(self.svc.next_traveler_id(), "TR00521")

    def test_empty_db_first_traveler_is_tr00001(self) -> None:
        """Empty travelers table → first ID must be TR00001."""
        self.assertEqual(self.svc.next_traveler_id(), "TR00001")

    # ── 8. Duplicate phone → handoff ──────────────────────────────────────────

    def test_duplicate_phone_triggers_handoff_no_new_traveler(self) -> None:
        """Two travelers sharing a phone key → multiple_matches handoff."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            _insert_traveler(conn, "TR00001", "Alice Smith", "20:1111111111")
            _insert_traveler(conn, "TR00002", "Bob Jones",  "20:1111111111")

        resolution = self.svc.resolve_identity(
            full_name="Either Person",
            raw_phone="1111111111",
            country_code="20",
        )
        self.assertEqual(resolution.match_status, "multiple_matches")
        self.assertTrue(resolution.handoff_required)
        # No traveler created — only existing matches returned
        self.assertIsNone(resolution.traveler)

    # ── 9. Blacklisted traveler → Blocked lead ────────────────────────────────

    def test_blacklisted_traveler_blocks_lead_and_sets_critical_priority(self) -> None:
        """Phone matching a Blacklisted traveler → lead is Blocked, Critical."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            _insert_traveler(conn, "TR00010", "Bad Actor",
                             "20:9999999999", status="Blacklisted")

        outcome = self.svc.record_agent_outcome(
            full_name="Bad Actor",
            raw_phone="9999999999",
            country_code="20",
            trip_type=None,
            channel="web",
            source="Web Demo",
            agent_notes="blacklist test",
        )
        self.assertTrue(outcome["handoff_required"])
        self.assertEqual(outcome["handoff_reason"], "blacklisted_customer")
        lead = outcome["write_result"]["lead_update"]
        self.assertEqual(lead["lead_stage"], "Blocked")
        self.assertEqual(lead["priority"], "Critical")
        self.assertIsNone(outcome["write_result"]["created_traveler"])

    # ── 10. Name conflict → phone_name_conflict handoff ───────────────────────

    def test_name_conflict_triggers_phone_name_conflict_handoff(self) -> None:
        """Same phone, clearly different name → phone_name_conflict handoff."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            _insert_traveler(conn, "TR00020", "Ahmed Selim", "20:5550001111")

        resolution = self.svc.resolve_identity(
            full_name="Completely Different Person",
            raw_phone="5550001111",
            country_code="20",
        )
        self.assertTrue(resolution.handoff_required)
        self.assertEqual(resolution.handoff_reason, "phone_name_conflict")
        self.assertEqual(resolution.match_status, "single_match")
        self.assertEqual(resolution.name_match_status, "conflict")

    # ── 11. Existing traveler matched, not duplicated ─────────────────────────

    def test_existing_traveler_is_matched_not_duplicated(self) -> None:
        """Same phone + matching name → single_match, no new row in DB."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            _insert_traveler(conn, "TR00040", "Sara Existing", "20:4440000001")

        resolution = self.svc.resolve_identity(
            full_name="Sara Existing",
            raw_phone="4440000001",
            country_code="20",
        )
        self.assertEqual(resolution.match_status, "single_match")
        self.assertFalse(resolution.handoff_required)
        self.assertEqual(resolution.traveler["traveler_id"], "TR00040")

        # No duplicates created
        with closing(sqlite3.connect(self.db_path)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM travelers").fetchone()[0]
        self.assertEqual(count, 1)

    # ── 12. Booking rejects cancelled trip ────────────────────────────────────

    def test_archived_or_inactive_traveler_routes_to_review_without_sales_flow(self) -> None:
        """Archived/inactive traveler should trigger review instead of normal sales."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            _insert_traveler(conn, "TR00041", "Archived Traveler", "20:4440000002", status="Archived")

        resolution = self.svc.resolve_identity(
            full_name="Archived Traveler",
            raw_phone="4440000002",
            country_code="20",
        )
        self.assertEqual(resolution.match_status, "single_match")
        self.assertTrue(resolution.handoff_required)
        self.assertEqual(resolution.handoff_reason, "archived_traveler")
        self.assertEqual(resolution.actions, ["human_review_archived_traveler"])


    def test_create_booking_draft_rejects_cancelled_trip(self) -> None:
        """create_booking_draft must raise for a Cancelled trip."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            _insert_trip(conn, "RT-LOC-26-CANC-TEST", "Cancelled",
                         "Local", sales_status="Cancelled")
            _insert_traveler(conn, "TR00030", "Test Traveler", "20:7770000001")

        with self.assertRaises(Exception) as ctx:
            self.svc.create_booking_draft(
                traveler_id="TR00030",
                traveler_name="Test Traveler",
                trip_id="RT-LOC-26-CANC-TEST",
                room_type="Single",
            )
        self.assertIn("Cancelled", str(ctx.exception))

    # ── 13. Booking rejects full-capacity room ────────────────────────────────

    def test_create_booking_draft_rejects_sold_out_room(self) -> None:
        """create_booking_draft must raise when Single rooms are exhausted."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            _insert_trip(conn, "RT-LOC-26-FULL-SGL", "Full Single",
                         "Local", single=2, double=2, triple=2)
            conn.execute(
                "UPDATE trips SET single_remaining = 0 WHERE trip_id = ?",
                ("RT-LOC-26-FULL-SGL",)
            )
            conn.commit()
            _insert_traveler(conn, "TR00031", "Room Test Traveler", "20:7770000002")

        with self.assertRaises(Exception) as ctx:
            self.svc.create_booking_draft(
                traveler_id="TR00031",
                traveler_name="Room Test Traveler",
                trip_id="RT-LOC-26-FULL-SGL",
                room_type="Single",
            )
        err = str(ctx.exception).lower()
        self.assertTrue(
            any(kw in err for kw in ("single", "capacity", "sold", "available", "room")),
            f"Expected capacity error. Got: {err}",
        )

    # ── 14. Booking succeeds with available capacity ──────────────────────────

    def test_create_booking_draft_succeeds_with_capacity(self) -> None:
        """create_booking_draft must succeed when the room type has capacity."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            _insert_trip(conn, "RT-LOC-26-AVAIL", "Available Trip", "Local")
            _insert_traveler(conn, "TR00032", "Success Traveler", "20:7770000003")

        booking = self.svc.create_booking_draft(
            traveler_id="TR00032",
            traveler_name="Success Traveler",
            trip_id="RT-LOC-26-AVAIL",
            room_type="Triple",
            currency="EGP",
        )
        self.assertIsNotNone(booking.get("booking_id"))
        self.assertEqual(booking["room_type"], "Triple")

        # DB must record Draft status
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            b = conn.execute("SELECT booking_status FROM trip_bookings WHERE booking_id = ?",
                             (booking["booking_id"],)).fetchone()
        self.assertIsNotNone(b)
        self.assertEqual(b["booking_status"], "Draft")


if __name__ == "__main__":
    unittest.main()
