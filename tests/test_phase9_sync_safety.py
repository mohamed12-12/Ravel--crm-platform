from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import unittest
import uuid
from pathlib import Path
from contextlib import closing

from openpyxl import Workbook

SYSTEM_ROOT = Path(__file__).resolve().parent.parent / "apps" / "api"
if str(SYSTEM_ROOT) not in sys.path:
    sys.path.insert(0, str(SYSTEM_ROOT))

from services.crm.system_services.unified_service import UnifiedCRMService
from services.crm.system_services.config import SystemServiceSettings
from services.crm.system_services.field_mapping import SHEET_TABLE_MAPPINGS

def seed_workbook(path: Path) -> None:
    wb = Workbook()
    
    # Create Travelers
    ws_travelers = wb.active
    ws_travelers.title = "Travelers"
    ws_travelers.append(list(SHEET_TABLE_MAPPINGS["Travelers"]["columns"].values()))
    
    wb.save(path)
    wb.close()

class FakeFailingService(UnifiedCRMService):
    def sync_record_to_sheet(self, mapping_name: str, record_id: str) -> dict[str, Any]:
        # We simulate a hard failure in the sync layer (e.g., Google Sheet API timeout)
        self._record_sync_failure(mapping_name, record_id, "Simulated Google Sheets Timeout")
        return {"status": "failed", "backend": self.settings.sheet_backend, "reason": "Simulated Google Sheets Timeout"}

class Phase9SyncSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root = Path(".tmp-test-workdirs")
        self.tmp_root.mkdir(exist_ok=True)
        self.tmp_path = self.tmp_root / f"phase9-sync-{uuid.uuid4().hex}"
        self.tmp_path.mkdir()
        self._original_env = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._original_env)
        shutil.rmtree(self.tmp_path, ignore_errors=True)

    def test_sync_failure_does_not_corrupt_db_and_queues_issue(self):
        db_path = self.tmp_path / "system.db"
        workbook_path = self.tmp_path / "runtime.xlsx"
        seed_workbook(workbook_path)

        # Setup standard app logic
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
        from app.models.traveler import Traveler

        app = create_app()
        app.config["TESTING"] = True
        
        with app.app_context():
            db.create_all()
        
        settings = SystemServiceSettings(
            repo_root=SYSTEM_ROOT,
            system_root=SYSTEM_ROOT,
            db_path=db_path,
            sheet_backend="excel",
            excel_source_workbook=str(workbook_path),
            excel_runtime_workbook=str(workbook_path),
            sheet_export_enabled=True,
            allow_source_workbook_writes=False,
        )
        
        service = FakeFailingService(settings)
        service.ensure_operational_schema()
        
        # Act: Write to DB normally, but use FakeFailingService for the sheet sync portion.
        # record_agent_outcome commits to DB, then calls sync_agent_write_to_sheet (which fails)
        result = service.record_agent_outcome(
            channel="web",
            raw_phone="1011122233",
            customer_name="Sync Test Traveler",
            status_snapshot="ACTIVE",
            intent="booking_request"
        )
        
        # 1. DB transaction must still be valid! Traveler MUST exist in DB.
        with app.app_context():
            t = Traveler.query.filter_by(phone_lookup_key="20:1011122233").first()
            self.assertIsNotNone(t, "DB transaction rolled back when it should have been safely committed")
            self.assertEqual(t.full_name, "Sync Test Traveler")
            traveler_id = t.traveler_id
            
        # 2. Queue must register the failed sync
        queue = service.get_sync_queue()
        self.assertGreater(len(queue), 0, "Failed sync was not logged to sync_queue")
        
        # Find the traveler failure
        traveler_issue = next((issue for issue in queue if issue["mapping_name"] == "Travelers"), None)
        self.assertIsNotNone(traveler_issue)
        self.assertEqual(traveler_issue["record_id"], traveler_id)
        self.assertEqual(traveler_issue["status"], "failed")
        self.assertEqual(traveler_issue["error_message"], "Simulated Google Sheets Timeout")

if __name__ == "__main__":
    unittest.main()
