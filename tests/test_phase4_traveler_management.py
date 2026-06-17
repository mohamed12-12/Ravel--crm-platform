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

SYSTEM_ROOT = Path(__file__).resolve().parent.parent / "apps" / "api"
if str(SYSTEM_ROOT) not in sys.path:
    sys.path.insert(0, str(SYSTEM_ROOT))

from services.crm.system_services.field_mapping import SHEET_TABLE_MAPPINGS  # noqa: E402
from services.crm.system_services.config import SystemServiceSettings  # noqa: E402
from services.crm.system_services.unified_service import UnifiedCRMService  # noqa: E402


def create_app_db_schema(app) -> None:
    from app.extensions import db

    with app.app_context():
        db.create_all()


def seed_traveler_workbook(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Travelers"
    headers = list(SHEET_TABLE_MAPPINGS["Travelers"]["columns"].values())
    ws.append(headers)
    wb.save(path)
    wb.close()


class Phase4TravelerManagementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root = Path(".tmp-test-workdirs")
        self.tmp_root.mkdir(exist_ok=True)
        self.tmp_path = self.tmp_root / f"phase4-travelers-{uuid.uuid4().hex}"
        self.tmp_path.mkdir()
        self._original_env = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._original_env)
        shutil.rmtree(self.tmp_path, ignore_errors=True)

    def _build_app(self):
        db_path = self.tmp_path / "system.db"
        workbook_path = self.tmp_path / "runtime.xlsx"
        seed_traveler_workbook(workbook_path)

        os.environ["DATABASE_URL"] = f"sqlite:///{db_path.resolve().as_posix()}"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(db_path)
        os.environ["SHEET_BACKEND"] = "excel"
        os.environ["EXCEL_RUNTIME_WORKBOOK"] = str(workbook_path)
        os.environ["EXCEL_SOURCE_WORKBOOK"] = str(workbook_path)

        for module_name in list(sys.modules):
            if module_name == "app" or module_name.startswith("app."):
                sys.modules.pop(module_name, None)

        from app import create_app
        from app.extensions import db
        from app.models.booking import TripBooking
        from app.models.booking_event import BookingEventTrail
        from app.models.handoff import HandoffQueue
        from app.models.interaction import Interaction
        from app.models.lead import Lead
        from app.models.traveler import Traveler

        self.service = UnifiedCRMService()
        self.db = db
        self.Traveler = Traveler
        self.Lead = Lead
        self.TripBooking = TripBooking
        self.Interaction = Interaction
        self.HandoffQueue = HandoffQueue
        self.BookingEventTrail = BookingEventTrail

        app = create_app()
        create_app_db_schema(app)
        uri = app.config["SQLALCHEMY_DATABASE_URI"]
        resolved_db_path = Path(uri.replace("sqlite:////", "").replace("sqlite:///", "", 1))
        return app, resolved_db_path, workbook_path

    def test_create_uses_true_max_traveler_id_and_syncs_sheet(self) -> None:
        app, db_path, workbook_path = self._build_app()
        client = app.test_client()

        with app.app_context():
            self.db.session.add_all(
                [
                    self.Traveler(traveler_id="TR00009", full_name="Old Nine", status="New"),
                    self.Traveler(traveler_id="TR00150", full_name="Old Max", status="VIP"),
                    self.Traveler(traveler_id="BAD-ID", full_name="Broken Id", status="New"),
                ]
            )
            self.db.session.commit()

        response = client.post(
            "/travelers/",
            data={
                "full_name": "Sara Traveler",
                "whatsapp_raw": "01012345678",
                "email": "sara@example.com",
                "nationality": "Egyptian",
                "residence": "Cairo",
                "lead_source": "Referral",
                "status": "New",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)

        with app.app_context():
            traveler = self.Traveler.query.filter_by(full_name="Sara Traveler").first()
            self.assertIsNotNone(traveler)
            self.assertEqual(traveler.traveler_id, "TR00151")
            self.assertEqual(traveler.integrated_whatsapp, "+201012345678")

        wb = load_workbook(workbook_path, data_only=True)
        ws = wb["Travelers"]
        headers = {ws.cell(1, col).value: col for col in range(1, ws.max_column + 1)}
        self.assertEqual(ws.cell(2, headers["Traveler ID"]).value, "TR00151")
        self.assertEqual(ws.cell(2, headers["Full Name"]).value, "Sara Traveler")
        self.assertEqual(ws.cell(2, headers["WhatsApp"]).value, "1012345678")
        self.assertEqual(ws.cell(2, headers["Integrated WhatsApp"]).value, "+201012345678")
        wb.close()

        self.assertTrue(db_path.exists())

    def test_detail_shows_related_records_and_update_syncs_back(self) -> None:
        app, db_path, workbook_path = self._build_app()
        client = app.test_client()

        with app.app_context():
            traveler = self.Traveler(
                traveler_id="TR00200",
                full_name="Profile Traveler",
                status="New",
                whatsapp_raw="1000000000",
                integrated_whatsapp="+201000000000",
                normalized_whatsapp="+201000000000",
                phone_lookup_key="2010000000",
                nationality="Egyptian",
                residence="Giza",
                lead_source="Web Demo",
            )
            lead = self.Lead(
                lead_id="LD20001",
                customer_name="Profile Traveler",
                raw_phone="1000000000",
                integrated_whatsapp="+201000000000",
                phone_lookup_key="2010000000",
                traveler_id="TR00200",
                lead_stage="Qualified",
                lead_source="Web Demo",
                channel="WhatsApp",
                priority="High",
                follow_up_status="Awaiting reply",
            )
            booking = self.TripBooking(
                booking_id="B20001",
                trip_id="RT-LOC-26-900",
                trip_name="Desert Escape",
                traveler_id="TR00200",
                traveler_name="Profile Traveler",
                room_type="Double",
                flight_option="Without Flights",
                booking_status="Draft",
                payment_status="Pending",
                booking_source="System UI",
            )
            interaction = self.Interaction(
                interaction_id="INT20001",
                timestamp=__import__("datetime").datetime.utcnow(),
                channel="WhatsApp",
                customer_name="Profile Traveler",
                raw_phone="1000000000",
                integrated_whatsapp="+201000000000",
                phone_lookup_key="2010000000",
                traveler_id="TR00200",
                status_snapshot="Qualified",
                intent="Trip follow-up",
                trip_type="Local",
                suggested_trips="RT-LOC-26-900",
                action_taken="Follow-up sent",
                handoff_required=False,
                handoff_reason="",
                agent_notes="Ready for booking",
            )
            handoff = self.HandoffQueue(
                handoff_id="H20001",
                lead_id="LD20001",
                traveler_id="TR00200",
                trip_id="RT-LOC-26-900",
                flow_key="booking_flow",
                reason="Needs manager review",
                priority="Medium",
                channel="WhatsApp",
                status="Pending",
            )
            event = self.BookingEventTrail(
                event_id="EV20001",
                event_type="lead_qualified",
                event_label="Lead qualified",
                traveler_id="TR00200",
                lead_id="LD20001",
                booking_id="B20001",
                trip_id="RT-LOC-26-900",
                interaction_id="INT20001",
                channel="WhatsApp",
                actor="system-ui",
                notes="Qualified for booking",
                metadata_json='{"source":"test"}',
            )
            self.db.session.add_all([traveler, lead, booking, interaction, handoff, event])
            self.db.session.commit()

        detail_response = client.get("/travelers/TR00200")
        self.assertEqual(detail_response.status_code, 200)
        body = detail_response.get_data(as_text=True)
        self.assertIn("LD20001", body)
        self.assertIn("B20001", body)
        self.assertIn("INT20001", body)
        self.assertIn("Needs manager review", body)

        update_response = client.put(
            "/travelers/TR00200",
            json={
                "full_name": "Profile Traveler Updated",
                "status": "VIP",
                "whatsapp_raw": "01099998888",
                "nationality": "Egyptian",
                "residence": "Cairo",
                "lead_source": "Referral",
                "rating": 4.8,
            },
        )
        self.assertEqual(update_response.status_code, 200)

        explicit_service = UnifiedCRMService(
            SystemServiceSettings(
                repo_root=self.tmp_path,
                system_root=self.tmp_path,
                db_path=db_path,
                sheet_backend="excel",
                excel_source_workbook=workbook_path,
                excel_runtime_workbook=workbook_path,
                google_sheet_id="",
                google_application_credentials=None,
            )
        )
        explicit_service.sync_record_to_sheet("Travelers", "TR00200")

        with app.app_context():
            traveler = self.Traveler.query.get("TR00200")
            self.assertIsNotNone(traveler)
            self.assertEqual(traveler.full_name, "Profile Traveler Updated")
            self.assertEqual(traveler.status, "VIP")
            self.assertEqual(traveler.integrated_whatsapp, "+201099998888")

        wb = load_workbook(workbook_path, data_only=True)
        ws = wb["Travelers"]
        headers = {ws.cell(1, col).value: col for col in range(1, ws.max_column + 1)}
        self.assertEqual(ws.cell(2, headers["Full Name"]).value, "Profile Traveler Updated")
        self.assertEqual(ws.cell(2, headers["Status"]).value, "VIP")
        self.assertEqual(ws.cell(2, headers["Integrated WhatsApp"]).value, "+201099998888")
        wb.close()


if __name__ == "__main__":
    unittest.main()
