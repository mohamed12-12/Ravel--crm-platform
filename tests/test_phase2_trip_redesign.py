from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import uuid
import unittest
from contextlib import closing
from pathlib import Path
import sys

CURRENT_DIR = Path(__file__).resolve().parent
SYSTEM_ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
if str(SYSTEM_ROOT) not in sys.path:
    sys.path.insert(0, str(SYSTEM_ROOT))

from app.extensions import db
from app.models.trip import Trip
from services.crm.system_services import UnifiedCRMService
from services.crm.system_services.config import SystemServiceSettings
from test_phase3_booking_write_through import create_operational_tables


def _create_temp_app(db_path: Path):
    from app import create_app
    return create_app("development")


class Phase2TripRedesignTests(unittest.TestCase):
    def setUp(self) -> None:
        # Other test files (e.g. test_admin_login_protection.py) pop "app.*"
        # from sys.modules to force a fresh app/db per test and don't restore
        # it afterward. If that happens to run before this file, the module-
        # level `db`/Trip imported at collection time end up bound to a stale
        # SQLAlchemy instance that create_app() never registers, raising
        # "app not registered with this SQLAlchemy instance". Force our own
        # consistent, fresh import here and rebind the module-level names so
        # every reference below matches the app this test actually creates.
        global db, Trip
        for module_name in list(sys.modules):
            if module_name == "app" or module_name.startswith("app."):
                sys.modules.pop(module_name, None)
        from app.extensions import db as _fresh_db
        from app.models.trip import Trip as _fresh_trip
        db, Trip = _fresh_db, _fresh_trip

        self.tmpdir = Path(".tmp-test-workdirs") / f"phase2-trip-{uuid.uuid4().hex}"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tmpdir / "app.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{self.db_path.resolve().as_posix()}"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(self.db_path)
        # Pin auth off: app/__init__.py defaults CRM_AUTH_ENABLED to "true" when
        # unset, so inheriting it from the ambient environment made these
        # unauthenticated route tests 302-redirect in full-suite order.
        os.environ["CRM_AUTH_ENABLED"] = "false"
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            create_operational_tables(conn)
        self.app = _create_temp_app(self.db_path)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("RAHMA_SYSTEM_DB_PATH", None)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _seed_trip_table(self) -> None:
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            conn.execute(
                """
                INSERT INTO trips (
                    trip_id, trip_name, type, year, start_date, end_date, sales_status,
                    single_total, double_total, triple_total, single_remaining, double_remaining,
                    triple_remaining, draft_holds_single, draft_holds_double, draft_holds_triple
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "RT-LOC-26-001",
                    "Existing Trip",
                    "Local",
                    2026,
                    "2026-10-01",
                    "2026-10-05",
                    "Open",
                    2,
                    4,
                    3,
                    2,
                    4,
                    3,
                    0,
                    0,
                    0,
                ),
            )
            conn.commit()

    def test_create_auto_generates_trip_id_when_blank(self) -> None:
        response = self.client.post(
            "/trips/",
            data={
                "trip_name": "Auto ID Trip",
                "type": "Local",
                "year": "2026",
                "sales_status": "Open",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            trip = Trip.query.filter_by(trip_name="Auto ID Trip").first()
            self.assertIsNotNone(trip)
            self.assertTrue((trip.trip_id or "").startswith("RT-LOC-26-"))

    def test_trip_room_splits_survive_create_and_sync(self) -> None:
        self._seed_trip_table()
        response = self.client.post(
            "/trips/",
            data={
                "trip_name": "Split Rooms",
                "trip_id": "",
                "type": "International",
                "year": "2026",
                "sales_status": "Open",
                "double_total": "4",
                "double_remaining": "3",
                "boys_double": "2",
                "girls_double": "1",
                "triple_total": "6",
                "triple_remaining": "5",
                "boys_triple": "3",
                "girls_triple": "2",
                "public_price": "$1200",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            trip = Trip.query.filter_by(trip_name="Split Rooms").first()
            self.assertEqual(trip.boys_double, 2)
            self.assertEqual(trip.girls_double, 1)
            self.assertEqual(trip.boys_triple, 3)
            self.assertEqual(trip.girls_triple, 2)

    def test_trip_program_fields_survive_create_and_update(self) -> None:
        response = self.client.post(
            "/trips/",
            data={
                "trip_name": "Program Trip",
                "trip_id": "RT-LOC-26-PROG",
                "type": "Local",
                "year": "2026",
                "sales_status": "Open",
                "itinerary": "Day 1: Arrival and welcome dinner\nDay 2: Mountain hike",
                "inclusions": "Hotel stay\nLocal transport",
                "exclusions": "Personal expenses\nOptional activities",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            trip = db.session.get(Trip, "RT-LOC-26-PROG")
            self.assertEqual(trip.itinerary, "Day 1: Arrival and welcome dinner\nDay 2: Mountain hike")
            self.assertEqual(trip.inclusions, "Hotel stay\nLocal transport")
            self.assertEqual(trip.exclusions, "Personal expenses\nOptional activities")
            self.assertEqual(trip.to_dict()["program"]["itinerary"][0]["details"], "Arrival and welcome dinner")

        update = self.client.post(
            "/trips/RT-LOC-26-PROG",
            data={
                "trip_name": "Program Trip Updated",
                "type": "Local",
                "sales_status": "Open",
                "itinerary": "Day 1: Museum visit",
                "inclusions": "Guide",
                "exclusions": "Flights",
            },
            follow_redirects=False,
        )
        self.assertEqual(update.status_code, 302)
        with self.app.app_context():
            trip = db.session.get(Trip, "RT-LOC-26-PROG")
            self.assertEqual(trip.trip_name, "Program Trip Updated")
            self.assertEqual(trip.itinerary, "Day 1: Museum visit")
            self.assertEqual(trip.inclusions, "Guide")
            self.assertEqual(trip.exclusions, "Flights")

    def test_trip_room_prices_survive_create_and_update(self) -> None:
        response = self.client.post(
            "/trips/",
            data={
                "trip_name": "Room Price Trip",
                "trip_id": "RT-LOC-26-PRICE",
                "type": "Local",
                "year": "2026",
                "sales_status": "Open",
                "public_price": "2000 EGP",
                "single_price_egp": "3000",
                "single_price_usd": "90",
                "double_price_egp": "2000",
                "double_price_usd": "60",
                "triple_price_egp": "1500",
                "triple_price_usd": "45",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            trip = db.session.get(Trip, "RT-LOC-26-PRICE")
            self.assertEqual(trip.room_prices["Single"]["EGP"], "3000")
            self.assertEqual(trip.room_prices["Double"]["USD"], "60")
            self.assertEqual(trip.to_dict()["room_prices"]["Triple"]["USD"], "45")

        update = self.client.post(
            "/trips/RT-LOC-26-PRICE",
            data={
                "trip_name": "Room Price Trip Updated",
                "type": "Local",
                "sales_status": "Open",
                "public_price": "2100 EGP",
                "single_total": "0",
                "double_total": "0",
                "triple_total": "0",
                "single_remaining": "0",
                "double_remaining": "0",
                "triple_remaining": "0",
                "boys_double": "0",
                "girls_double": "0",
                "boys_triple": "0",
                "girls_triple": "0",
                "single_price_egp": "3200",
                "single_price_usd": "95",
                "double_price_egp": "2200",
                "double_price_usd": "65",
                "triple_price_egp": "1700",
                "triple_price_usd": "50",
            },
            follow_redirects=False,
        )
        self.assertEqual(update.status_code, 302)
        with self.app.app_context():
            trip = db.session.get(Trip, "RT-LOC-26-PRICE")
            self.assertEqual(trip.trip_name, "Room Price Trip Updated")
            self.assertEqual(trip.room_prices["Single"]["USD"], "95")
            self.assertEqual(trip.room_prices["Double"]["EGP"], "2200")


if __name__ == "__main__":
    unittest.main()
