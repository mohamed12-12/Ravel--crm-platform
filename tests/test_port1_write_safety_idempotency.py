from __future__ import annotations

import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path

from flask import Flask

SYSTEM_ROOT = Path(__file__).resolve().parent.parent / "apps" / "api"
if str(SYSTEM_ROOT) not in sys.path:
    sys.path.insert(0, str(SYSTEM_ROOT))


def _create_temp_app():
    for module_name in list(sys.modules):
        if module_name == "app" or module_name.startswith("app."):
            sys.modules.pop(module_name, None)
    from app import create_app

    return create_app("development")


def _load_app_objects():
    from app.extensions import db
    from app.models.booking import TripBooking
    from app.models.lead import Lead
    from app.models.traveler import Traveler
    from app.models.trip import Trip
    from services.crm.system_services import UnifiedCRMService

    return db, TripBooking, Lead, Traveler, Trip, UnifiedCRMService


class Port1WriteSafetyIdempotencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(".tmp-test-workdirs") / f"port1-idempotency-{uuid.uuid4().hex}"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tmpdir / "app.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{self.db_path.resolve().as_posix()}"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(self.db_path)
        self.app = _create_temp_app()
        self.db, self.TripBooking, self.Lead, self.Traveler, self.Trip, UnifiedCRMService = _load_app_objects()
        self.app.config["TESTING"] = True
        with self.app.app_context():
            self.db.drop_all()
            self.db.create_all()
            self.db.session.add(
                self.Traveler(
                    traveler_id="TRPORT1",
                    full_name="Port One Traveler",
                    integrated_whatsapp="20:1000000000",
                    normalized_whatsapp="+201000000000",
                    phone_lookup_key="20:1000000000",
                )
            )
            self.db.session.add(
                self.Trip(
                    trip_id="TRIP-PORT-1",
                    trip_name="Port Safety Trip",
                    type="Local",
                    sales_status="Open",
                    single_total=2,
                    double_total=3,
                    triple_total=2,
                    single_remaining=2,
                    double_remaining=3,
                    triple_remaining=2,
                    draft_holds_single=0,
                    draft_holds_double=0,
                    draft_holds_triple=0,
                    public_price="$1,000",
                )
            )
            self.db.session.commit()
        self.service = UnifiedCRMService()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("RAHMA_SYSTEM_DB_PATH", None)

    def test_repeated_booking_confirmation_reuses_existing_booking(self) -> None:
        first = self.service.create_booking_draft(
            traveler_id="TRPORT1",
            traveler_name="Port One Traveler",
            trip_id="TRIP-PORT-1",
            room_type="Double",
            channel="web",
            source="Gemini Agent",
            session_id="session-repeat",
            require_explicit_confirmation=True,
            customer_confirmed=True,
        )
        second = self.service.create_booking_draft(
            traveler_id="TRPORT1",
            traveler_name="Port One Traveler",
            trip_id="TRIP-PORT-1",
            room_type="Double",
            channel="web",
            source="Gemini Agent",
            session_id="session-repeat",
            require_explicit_confirmation=True,
            customer_confirmed=True,
        )

        self.assertEqual(second["booking_id"], first["booking_id"])
        self.assertEqual(second["write_result_contract"]["status"], "reused")
        self.assertFalse(second["write_result_contract"]["executed"])
        self.assertTrue(second["write_result_contract"]["reused"])
        with self.app.app_context():
            self.assertEqual(self.TripBooking.query.count(), 1)

    def test_active_booking_duplicate_does_not_create_second_booking(self) -> None:
        first = self.service.create_booking_draft(
            traveler_id="TRPORT1",
            traveler_name="Port One Traveler",
            trip_id="TRIP-PORT-1",
            room_type="Single",
            channel="web",
            source="Gemini Agent",
            session_id="session-one",
            require_explicit_confirmation=True,
            customer_confirmed=True,
        )
        second = self.service.create_booking_draft(
            traveler_id="TRPORT1",
            traveler_name="Port One Traveler",
            trip_id="TRIP-PORT-1",
            room_type="Single",
            channel="web",
            source="Gemini Agent",
            session_id="session-two",
            require_explicit_confirmation=True,
            customer_confirmed=True,
        )

        self.assertEqual(second["booking_id"], first["booking_id"])
        self.assertEqual(second["write_result_contract"]["status"], "duplicate")
        with self.app.app_context():
            self.assertEqual(self.TripBooking.query.count(), 1)

    def test_unconfirmed_booking_write_is_blocked(self) -> None:
        result = self.service.create_booking_draft(
            traveler_id="TRPORT1",
            traveler_name="Port One Traveler",
            trip_id="TRIP-PORT-1",
            room_type="Double",
            channel="web",
            source="Gemini Agent",
            session_id="session-unconfirmed",
            require_explicit_confirmation=True,
            customer_confirmed=False,
        )

        self.assertEqual(result["write_result_contract"]["status"], "blocked")
        self.assertFalse(result["write_result_contract"]["executed"])
        with self.app.app_context():
            self.assertEqual(self.TripBooking.query.count(), 0)

    def test_repeated_lead_creation_reuses_existing_lead(self) -> None:
        first = self.service.upsert_lead(
            customer_name="Port One Traveler",
            raw_phone="01000000000",
            traveler_id="TRPORT1",
            lead_stage="New Lead",
            lead_source="Gemini Agent",
            channel="web",
            preferred_trip_type="Local",
            interested_trip_ids="TRIP-PORT-1",
            session_id="lead-session",
        )
        second = self.service.upsert_lead(
            customer_name="Port One Traveler",
            raw_phone="01000000000",
            traveler_id="TRPORT1",
            lead_stage="New Lead",
            lead_source="Gemini Agent",
            channel="web",
            preferred_trip_type="Local",
            interested_trip_ids="TRIP-PORT-1",
            session_id="lead-session",
        )

        self.assertEqual(second["lead_id"], first["lead_id"])
        self.assertEqual(second["write_result_contract"]["status"], "reused")
        with self.app.app_context():
            self.assertEqual(self.Lead.query.count(), 1)

    def test_repeated_handoff_request_reuses_existing_open_handoff(self) -> None:
        first = self.service.create_handoff_case(
            traveler_id="TRPORT1",
            reason_code="customer_requested_human",
            reason_text="Customer asked for a human agent.",
            channel="web",
            session_id="handoff-session",
            deduplicate_open=True,
        )
        second = self.service.create_handoff_case(
            traveler_id="TRPORT1",
            reason_code="customer_requested_human",
            reason_text="Customer asked for a human agent.",
            channel="web",
            session_id="handoff-session",
            deduplicate_open=True,
        )

        self.assertEqual(second["handoff_id"], first["handoff_id"])
        self.assertEqual(second["write_result_contract"]["status"], "reused")
        with self.service.connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM handoff_queue").fetchone()[0]
        self.assertEqual(count, 1)


class DirectBookingRouteGuardTests(unittest.TestCase):
    def test_direct_booking_route_requires_confirmation(self) -> None:
        from services.ai_agent.ai_agent_app.web.api_routes import api_bp

        class FakeGateway:
            def __init__(self) -> None:
                self.calls = []

            def create_booking(self, **kwargs):
                self.calls.append(kwargs)
                return {"booking_id": "B-SHOULD-NOT-RUN"}

        gateway = FakeGateway()
        app = Flask(__name__)
        app.secret_key = "test-secret"
        app.config["SHEET_GATEWAY"] = gateway
        app.register_blueprint(api_bp)
        client = app.test_client()

        response = client.post(
            "/api/v1/bookings/draft",
            json={"travelerId": "TRPORT1", "tripId": "TRIP-PORT-1", "roomType": "Double"},
        )

        self.assertEqual(response.status_code, 409)
        payload = response.get_json()
        self.assertEqual(payload["write_result_contract"]["status"], "blocked")
        self.assertFalse(payload["write_result_contract"]["executed"])
        self.assertEqual(gateway.calls, [])

    def test_direct_booking_route_passes_confirmation_to_gateway(self) -> None:
        from services.ai_agent.ai_agent_app.web.api_routes import api_bp

        class FakeGateway:
            def __init__(self) -> None:
                self.calls = []

            def create_booking(self, **kwargs):
                self.calls.append(kwargs)
                return {
                    "booking_id": "B-CONFIRMED",
                    "write_result_contract": {"status": "success", "executed": True},
                }

        gateway = FakeGateway()
        app = Flask(f"route-test-{uuid.uuid4().hex}")
        app.secret_key = "test-secret"
        app.config["SHEET_GATEWAY"] = gateway
        app.register_blueprint(api_bp)
        client = app.test_client()

        response = client.post(
            "/api/v1/bookings/draft",
            json={
                "travelerId": "TRPORT1",
                "travelerName": "Port One Traveler",
                "tripId": "TRIP-PORT-1",
                "roomType": "Double",
                "confirmed": True,
                "sessionId": "direct-session",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(gateway.calls[0]["require_explicit_confirmation"], True)
        self.assertEqual(gateway.calls[0]["customer_confirmed"], True)
        self.assertEqual(gateway.calls[0]["session_id"], "direct-session")


if __name__ == "__main__":
    unittest.main()
