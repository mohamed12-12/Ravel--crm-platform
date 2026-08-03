from __future__ import annotations

import os
import shutil
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest

SYSTEM_ROOT = Path(__file__).resolve().parent.parent / "apps" / "api"
if str(SYSTEM_ROOT) not in sys.path:
    sys.path.insert(0, str(SYSTEM_ROOT))


def _create_app(tmpdir: Path):
    for module_name in list(sys.modules):
        if module_name == "app" or module_name.startswith("app."):
            sys.modules.pop(module_name, None)
    db_path = (tmpdir / "bridge.db").resolve()
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    os.environ["RAHMA_SYSTEM_DB_PATH"] = str(db_path)
    os.environ["DATA_AUTHORITY"] = "crm"
    os.environ["CRM_ACCESS_MODE"] = "shared_service"
    os.environ["CRM_AUTH_ENABLED"] = "false"
    from app import create_app
    from app.extensions import db
    from app.models import Traveler, Trip

    app = create_app("development")
    app.config.update(TESTING=True)
    with app.app_context():
        db.drop_all()
        db.create_all()
        db.session.add(
            Traveler(
                traveler_id="TRPG001",
                full_name="Postgres Bridge Traveler",
                status="Active",
                whatsapp_raw="01012345678",
                integrated_whatsapp="+201012345678",
                normalized_whatsapp="+201012345678",
                phone_lookup_key="20:1012345678",
            )
        )
        db.session.add(
            Trip(
                trip_id="RTPG001",
                trip_name="Cairo Escape",
                type="Local",
                sales_status="Open",
                start_date=date.today() + timedelta(days=10),
                end_date=date.today() + timedelta(days=13),
                single_total=2,
                single_remaining=2,
                double_total=2,
                double_remaining=2,
                triple_total=1,
                triple_remaining=1,
                public_price="$100",
            )
        )
        db.session.commit()
    return app, db


@pytest.fixture()
def bridge_app():
    tmpdir = Path(".tmp-test-workdirs") / f"postgres-bridge-{uuid.uuid4().hex}"
    tmpdir.mkdir(parents=True, exist_ok=True)
    app, db = _create_app(tmpdir)
    try:
        yield app, db
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        for key in ("DATABASE_URL", "RAHMA_SYSTEM_DB_PATH", "DATA_AUTHORITY", "CRM_ACCESS_MODE", "CRM_AUTH_ENABLED"):
            os.environ.pop(key, None)


def test_agent_read_bridge_uses_postgres_mode_when_configured(bridge_app):
    app, _db = bridge_app
    client = app.test_client()
    app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://staging/redacted"

    response = client.post(
        "/api/crm/agent/read",
        json={"action": "get_traveler_profile", "payload": {"traveler_id": "TRPG001"}},
    )

    assert response.status_code == 200
    assert response.get_json()["result"]["traveler"]["full_name"] == "Postgres Bridge Traveler"


def test_returning_traveler_lookup_and_trip_search_work_in_postgres_mode(bridge_app):
    app, _db = bridge_app
    client = app.test_client()
    app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://staging/redacted"

    traveler_response = client.post(
        "/api/crm/agent/read",
        json={"action": "search_traveler", "payload": {"raw_phone": "01012345678", "country_code": "20"}},
    )
    trip_response = client.post(
        "/api/crm/agent/read",
        json={"action": "search_trips", "payload": {"trip_type": "Local", "query": "Cairo"}},
    )

    assert traveler_response.status_code == 200
    assert traveler_response.get_json()["result"]["traveler"]["traveler_id"] == "TRPG001"
    assert trip_response.status_code == 200
    trips = trip_response.get_json()["result"]["trips"]
    assert any(trip["trip_id"] == "RTPG001" for trip in trips)


def test_handoff_and_booking_confirmation_write_to_postgres_bridge_safely(bridge_app):
    app, db = bridge_app
    client = app.test_client()
    app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://staging/redacted"
    from app.models import HandoffQueue, TripBooking

    handoff_response = client.post(
        "/api/crm/agent/write",
        json={
            "action": "create_handoff",
            "payload": {"traveler_id": "TRPG001", "reason_code": "customer_requested_human", "reason_text": "Customer asked for an agent.", "user_requested_human": True},
            "session_context": {"session_id": "handoff-pg-1", "language": "en"},
        },
    )
    booking_response = client.post(
        "/api/crm/agent/write",
        json={
            "action": "create_booking_draft",
            "payload": {"traveler_id": "TRPG001", "trip_id": "RTPG001", "room_type": "Double", "channel": "web"},
            "session_context": {"session_id": "booking-pg-1", "language": "en", "booking_confirmed": True},
        },
    )

    assert handoff_response.status_code == 200
    assert booking_response.status_code == 200
    assert handoff_response.get_json()["result"]["handoff_case"]["write_result_contract"]["status"] == "created"
    assert booking_response.get_json()["result"]["booking_result"]["write_result_contract"]["status"] == "created"
    with app.app_context():
        assert db.session.query(HandoffQueue).count() == 1
        assert db.session.query(TripBooking).count() == 1


def test_repeated_confirmation_does_not_duplicate_booking_in_postgres_mode(bridge_app):
    app, db = bridge_app
    client = app.test_client()
    app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://staging/redacted"
    from app.models import TripBooking

    first = client.post(
        "/api/crm/agent/write",
        json={
            "action": "create_booking_draft",
            "payload": {"traveler_id": "TRPG001", "trip_id": "RTPG001", "room_type": "Single", "channel": "web"},
            "session_context": {"session_id": "repeat-confirmation", "language": "en", "booking_confirmed": True},
        },
    )
    second = client.post(
        "/api/crm/agent/write",
        json={
            "action": "create_booking_draft",
            "payload": {"traveler_id": "TRPG001", "trip_id": "RTPG001", "room_type": "Single", "channel": "web"},
            "session_context": {"session_id": "repeat-confirmation", "language": "en", "booking_confirmed": True},
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    first_booking = first.get_json()["result"]["booking_result"]
    second_result = second.get_json()["result"]
    assert second_result["result_id"] == first_booking["booking_id"]
    assert second_result["write_result_contract"]["status"] == "duplicate"
    with app.app_context():
        assert db.session.query(TripBooking).count() == 1


def test_repeated_create_lead_returns_structured_duplicate_in_postgres_mode(bridge_app):
    app, db = bridge_app
    client = app.test_client()
    app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://staging/redacted"
    from app.models import Lead

    payload = {
        "action": "create_lead",
        "payload": {
            "traveler_id": "TRPG001",
            "customer_name": "Postgres Bridge Traveler",
            "preferred_trip_type": "Local",
            "trip_id": "RTPG001",
            "channel": "web",
        },
        "session_context": {"session_id": "lead-duplicate-pg", "language": "en"},
    }

    first = client.post("/api/crm/agent/write", json=payload)
    second = client.post("/api/crm/agent/write", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    first_result = first.get_json()["result"]
    duplicate_result = second.get_json()["result"]
    assert duplicate_result["result_id"] == first_result["result_id"]
    assert duplicate_result["write_result_contract"]["record_type"] == "lead"
    assert duplicate_result["write_result_contract"]["record_id"] == first_result["result_id"]
    assert duplicate_result["write_result_contract"]["status"] == "duplicate"
    assert duplicate_result["write_result_contract"]["executed"] is False
    assert duplicate_result["write_result_contract"]["reused"] is True
    assert "already recorded" in duplicate_result["assistant_message"].lower()
    assert "created for" not in duplicate_result["assistant_message"].lower()
    with app.app_context():
        assert db.session.query(Lead).count() == 1


def test_repeated_create_lead_returns_structured_duplicate_in_sqlite_mode(bridge_app):
    app, db = bridge_app
    client = app.test_client()
    from app.models import Lead

    payload = {
        "action": "create_lead",
        "payload": {
            "traveler_id": "TRPG001",
            "customer_name": "Postgres Bridge Traveler",
            "preferred_trip_type": "Local",
            "trip_id": "RTPG001",
            "channel": "web",
        },
        "session_context": {"session_id": "lead-duplicate-sqlite", "language": "en"},
    }

    first = client.post("/api/crm/agent/write", json=payload)
    second = client.post("/api/crm/agent/write", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    first_result = first.get_json()["result"]
    duplicate_result = second.get_json()["result"]
    assert duplicate_result["result_id"] == first_result["result_id"]
    assert duplicate_result["write_result_contract"]["record_type"] == "lead"
    assert duplicate_result["write_result_contract"]["record_id"] == first_result["result_id"]
    assert duplicate_result["write_result_contract"]["status"] == "duplicate"
    assert duplicate_result["write_result_contract"]["executed"] is False
    assert duplicate_result["write_result_contract"]["reused"] is True
    assert "already recorded" in duplicate_result["assistant_message"].lower()
    assert "created for" not in duplicate_result["assistant_message"].lower()
    with app.app_context():
        assert db.session.query(Lead).count() == 1


def test_sqlite_mode_still_works(bridge_app):
    app, _db = bridge_app
    client = app.test_client()

    response = client.post(
        "/api/crm/agent/read",
        json={"action": "get_traveler_profile", "payload": {"traveler_id": "TRPG001"}},
    )

    assert response.status_code == 200
    assert response.get_json()["result"]["traveler"]["traveler_id"] == "TRPG001"


def test_direct_booking_bypass_remains_blocked():
    from flask import Flask
    from services.ai_agent.ai_agent_app.web.api_routes import api_bp

    class FakeGateway:
        def create_booking(self, **kwargs):
            raise AssertionError("Gateway should not be called before confirmation.")

    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.config["SHEET_GATEWAY"] = FakeGateway()
    app.register_blueprint(api_bp)
    client = app.test_client()

    response = client.post(
        "/api/v1/bookings/draft",
        json={"travelerId": "TRPG001", "tripId": "RTPG001", "roomType": "Double"},
    )

    assert response.status_code == 409
    assert response.get_json()["write_result_contract"]["status"] == "blocked"
