from __future__ import annotations

import os
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
import importlib
import shutil

from services.ai_agent.ai_agent_app.config import load_settings
from services.crm.system_services.config import get_database_diagnostics, resolve_system_db_path


PROMOTED_DB = Path("tests/fixtures/operational_db_fixture.db")
WORKBOOK = Path("RT - Travelers Database.xlsx")


def _read_counts(db_path: Path) -> dict[str, int]:
    with closing(sqlite3.connect(db_path)) as connection:
        cursor = connection.cursor()
        return {
            "travelers": cursor.execute("select count(*) from travelers").fetchone()[0],
            "trips": cursor.execute("select count(*) from trips").fetchone()[0],
            "trip_bookings": cursor.execute("select count(*) from trip_bookings").fetchone()[0],
            "booking_status_history": cursor.execute("select count(*) from booking_status_history").fetchone()[0],
            "leads": cursor.execute("select count(*) from leads").fetchone()[0],
        }


class OperationalDbPromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_env = dict(os.environ)
        self.tmp_copy = Path(".tmp-test-workdirs") / "operational-db-promotion-copy.db"
        self.tmp_copy.parent.mkdir(exist_ok=True)
        shutil.copy2(PROMOTED_DB, self.tmp_copy)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._original_env)
        if self.tmp_copy.exists():
            self.tmp_copy.unlink()

    def test_promoted_db_has_expected_counts(self) -> None:
        self.assertTrue(self.tmp_copy.exists())
        self.assertEqual(
            _read_counts(self.tmp_copy),
            {
                "travelers": 571,
                "trips": 42,
                "trip_bookings": 48,
                "booking_status_history": 48,
                "leads": 0,
            },
        )

    def test_crm_and_agent_config_resolve_same_operational_db(self) -> None:
        os.environ["DATABASE_URL"] = f"sqlite:///{PROMOTED_DB.resolve().as_posix()}"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(PROMOTED_DB)
        os.environ["EXCEL_SOURCE_WORKBOOK"] = str(WORKBOOK)
        os.environ["EXCEL_RUNTIME_WORKBOOK"] = str(WORKBOOK)

        reloaded_config = importlib.reload(importlib.import_module("apps.api.app.config"))
        self.assertEqual(Path(reloaded_config.DevelopmentConfig.get_sqlalchemy_uri().replace("sqlite:///", "", 1)).resolve(), PROMOTED_DB.resolve())
        self.assertEqual(Path(reloaded_config.ProductionConfig.get_sqlalchemy_uri().replace("sqlite:///", "", 1)).resolve(), PROMOTED_DB.resolve())

        settings = load_settings()
        self.assertEqual(resolve_system_db_path().resolve(), PROMOTED_DB.resolve())
        self.assertEqual(Path(settings.excel_source_workbook).resolve(), WORKBOOK.resolve())
        self.assertEqual(Path(settings.excel_runtime_workbook).resolve(), WORKBOOK.resolve())

    def test_db_diagnostics_helper_reports_counts(self) -> None:
        diagnostics = get_database_diagnostics(self.tmp_copy)
        self.assertEqual(Path(diagnostics["db_path"]).resolve(), self.tmp_copy.resolve())
        self.assertEqual(diagnostics["counts"]["travelers"], 571)
        self.assertEqual(diagnostics["counts"]["trips"], 42)
        self.assertEqual(diagnostics["counts"]["trip_bookings"], 48)
        self.assertEqual(diagnostics["counts"]["booking_status_history"], 48)

    def test_agent_bootstrap_prefers_crm_database_label(self) -> None:
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(PROMOTED_DB)
        os.environ["EXCEL_SOURCE_WORKBOOK"] = str(WORKBOOK)
        os.environ["EXCEL_RUNTIME_WORKBOOK"] = str(WORKBOOK)

        from services.ai_agent.ai_agent_app.server import create_app

        app = create_app()
        client = app.test_client()
        resp = client.get("/api/bootstrap")
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertEqual(payload["sheetBackend"], "crm-db")
        self.assertEqual(payload["activeDbPath"], str(PROMOTED_DB.resolve()))
        self.assertEqual(payload["dbDiagnostics"]["counts"]["travelers"], 571)

    def test_system_db_resolution_matches_agent_fallback(self) -> None:
        os.environ.pop("RAHMA_SYSTEM_DB_PATH", None)
        self.assertEqual(resolve_system_db_path().resolve(), Path("apps/api/instance/rahma_traveler_dev.db").resolve())


if __name__ == "__main__":
    unittest.main()
