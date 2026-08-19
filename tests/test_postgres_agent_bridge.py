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
    # _create_app pops "app"/"app.*" from sys.modules to force a fresh
    # Flask-SQLAlchemy registration -- that must not leak into later tests
    # that do a plain `from app import ...` expecting their own fresh
    # import, so snapshot and restore the exact module objects afterward.
    original_app_modules = {
        name: module for name, module in sys.modules.items() if name == "app" or name.startswith("app.")
    }
    tmpdir = Path(".tmp-test-workdirs") / f"postgres-bridge-{uuid.uuid4().hex}"
    tmpdir.mkdir(parents=True, exist_ok=True)
    app, db = _create_app(tmpdir)
    try:
        yield app, db
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        for name in list(sys.modules):
            if name == "app" or name.startswith("app."):
                del sys.modules[name]
        sys.modules.update(original_app_modules)
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


def test_lowercase_trip_type_matches_title_cased_trip_records_in_postgres_mode(bridge_app):
    """The AI agent always sends trip_type as lowercase ('local'/'international'),
    while Trip.type is stored title-cased ('Local'). This must still match.
    """
    app, _db = bridge_app
    client = app.test_client()
    app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://staging/redacted"

    response = client.post(
        "/api/crm/agent/read",
        json={"action": "search_trips", "payload": {"trip_type": "local", "query": ""}},
    )

    assert response.status_code == 200
    trips = response.get_json()["result"]["trips"]
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


def test_mixed_booking_holds_increment_by_room_count_and_gender_pool(bridge_app):
    app, db = bridge_app
    client = app.test_client()
    app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://staging/redacted"
    from app.models import Trip, TripBooking

    with app.app_context():
        trip = db.session.get(Trip, "RTPG001")
        trip.double_total = 5
        trip.double_remaining = 5
        trip.boys_double = 2
        trip.girls_double = 1
        db.session.commit()

    response = client.post(
        "/api/crm/agent/write",
        json={
            "action": "create_booking_draft",
            "payload": {
                "traveler_id": "TRPG001",
                "trip_id": "RTPG001",
                "room_type": "Double",
                "room_group": "mixed",
                "channel": "web",
                "boys_count": 4,
                "girls_count": 2,
                "family_units": 1,
                "group_size": 6,
                "room_requirements": {
                    "requirements": [
                        {"room_type": "Double", "room_group": "family", "rooms": 1},
                        {"room_type": "Double", "room_group": "boys", "rooms": 2},
                        {"room_type": "Double", "room_group": "girls", "rooms": 1},
                    ],
                    "boys_rooms_requested": 2,
                    "girls_rooms_requested": 1,
                },
            },
            "session_context": {"session_id": "mixed-booking-holds", "language": "en", "booking_confirmed": True},
        },
    )

    assert response.status_code == 200
    assert response.get_json()["result"]["booking_result"]["write_result_contract"]["status"] == "created"
    with app.app_context():
        trip = db.session.get(Trip, "RTPG001")
        booking = db.session.query(TripBooking).one()
        assert trip.draft_holds_double == 4
        assert trip.draft_holds_boys_double == 2
        assert trip.draft_holds_girls_double == 1
        assert booking.boys_count == 4
        assert booking.girls_count == 2
        assert booking.family_units == 1


def test_gendered_requirements_can_use_unsplit_aggregate_capacity(bridge_app):
    app, db = bridge_app
    client = app.test_client()
    app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://staging/redacted"
    from app.models import Trip

    with app.app_context():
        trip = db.session.get(Trip, "RTPG001")
        trip.double_total = 3
        trip.double_remaining = 3
        trip.boys_double = 0
        trip.girls_double = 0
        db.session.commit()

    response = client.post(
        "/api/crm/agent/write",
        json={
            "action": "create_booking_draft",
            "payload": {
                "traveler_id": "TRPG001",
                "trip_id": "RTPG001",
                "room_type": "Double",
                "channel": "web",
                "room_requirements": {
                    "requirements": [
                        {"room_type": "Double", "room_group": "boys", "rooms": 1},
                        {"room_type": "Double", "room_group": "girls", "rooms": 1},
                    ],
                },
            },
            "session_context": {"session_id": "unsplit-gendered-capacity", "language": "en", "booking_confirmed": True},
        },
    )

    assert response.status_code == 200
    with app.app_context():
        trip = db.session.get(Trip, "RTPG001")
        assert trip.draft_holds_double == 2
        assert trip.draft_holds_boys_double == 1
        assert trip.draft_holds_girls_double == 1


def test_a_gendered_pool_shortage_is_rejected_not_silently_overbooked(bridge_app):
    """The two existing mixed-booking tests only exercise requests that fit
    exactly within the gendered pools -- neither would fail if
    _assert_room_capacity's gender_capacity block were deleted entirely.
    This pins the rejection itself: 1 boys_double available, 2 requested.
    """
    app, db = bridge_app
    client = app.test_client()
    app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://staging/redacted"
    from app.models import Trip, TripBooking

    with app.app_context():
        trip = db.session.get(Trip, "RTPG001")
        trip.double_total = 5
        trip.double_remaining = 5
        trip.boys_double = 1
        trip.girls_double = 5
        db.session.commit()

    response = client.post(
        "/api/crm/agent/write",
        json={
            "action": "create_booking_draft",
            "payload": {
                "traveler_id": "TRPG001",
                "trip_id": "RTPG001",
                "room_type": "Double",
                "room_group": "mixed",
                "channel": "web",
                "boys_count": 4,
                "girls_count": 2,
                "group_size": 6,
                "room_requirements": {
                    "requirements": [
                        {"room_type": "Double", "room_group": "boys", "rooms": 2},
                        {"room_type": "Double", "room_group": "girls", "rooms": 1},
                    ],
                    "boys_rooms_requested": 2,
                    "girls_rooms_requested": 1,
                },
            },
            "session_context": {"session_id": "gendered-shortage", "language": "en", "booking_confirmed": True},
        },
    )

    assert response.status_code == 200
    contract = response.get_json()["result"]["write_result_contract"]
    assert contract["status"] == "failed"
    assert contract["executed"] is False

    # Nothing was written, and the aggregate/gendered holds are untouched --
    # a rejected request must not partially reserve capacity.
    with app.app_context():
        trip = db.session.get(Trip, "RTPG001")
        assert trip.draft_holds_double == 0
        assert trip.draft_holds_boys_double == 0
        assert trip.draft_holds_girls_double == 0
        assert TripBooking.query.count() == 0


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


def test_new_phone_number_creates_a_traveler_record_linked_to_the_lead(bridge_app):
    """Bug: create_lead for a phone number with no Traveler match saved the
    Lead with traveler_id=None and never created a Traveler row at all, so
    find_traveler_by_phone could never match this customer again in a later
    session -- every returning customer without a completed booking looked
    brand new forever. The shared_service/sqlite path already creates a
    Traveler via record_agent_outcome; PostgresAgentBridgeService had no
    equivalent.
    """
    app, db = bridge_app
    client = app.test_client()
    app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://staging/redacted"
    from app.models import Lead, Traveler

    payload = {
        "action": "create_lead",
        "payload": {
            "customer_name": "Brand New Traveler",
            "raw_phone": "01099998888",
            "country_code": "20",
            "preferred_trip_type": "Local",
            "channel": "web",
            "birthday": "1998-05-12",
            "nationality": "Egyptian",
            "preferred_currency": "EGP",
        },
        "session_context": {"session_id": "new-traveler-pg-1", "language": "en"},
    }

    response = client.post("/api/crm/agent/write", json=payload)

    assert response.status_code == 200
    result = response.get_json()["result"]
    lead_id = result["result_id"]
    assert lead_id
    with app.app_context():
        lead = db.session.get(Lead, lead_id)
        assert lead is not None
        assert lead.traveler_id, "Lead was saved without a linked Traveler record"
        traveler = db.session.get(Traveler, lead.traveler_id)
        assert traveler is not None
        assert traveler.full_name == "Brand New Traveler"
        assert traveler.nationality == "Egyptian"
        assert traveler.birthday.isoformat() == "1998-05-12"
        assert db.session.query(Traveler).filter_by(phone_lookup_key=traveler.phone_lookup_key).count() == 1


def test_retrying_create_lead_for_a_new_phone_does_not_create_a_second_traveler(bridge_app):
    app, db = bridge_app
    client = app.test_client()
    app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://staging/redacted"
    from app.models import Traveler

    payload = {
        "action": "create_lead",
        "payload": {
            "customer_name": "Retry New Traveler",
            "raw_phone": "01077776666",
            "country_code": "20",
            "channel": "web",
        },
        "session_context": {"session_id": "new-traveler-pg-retry", "language": "en"},
    }

    first = client.post("/api/crm/agent/write", json=payload)
    second = client.post("/api/crm/agent/write", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    first_result = first.get_json()["result"]
    second_result = second.get_json()["result"]
    assert second_result["result_id"] == first_result["result_id"]
    assert second_result["write_result_contract"]["status"] == "duplicate"
    with app.app_context():
        assert db.session.query(Traveler).filter_by(full_name="Retry New Traveler").count() == 1


def test_repeated_handoff_request_surfaces_reused_status_at_the_top_level(bridge_app):
    """_execute_create_handoff previously never propagated the nested
    handoff_case.write_result_contract to its own top-level result, so
    _normalize_result's blanket executed=True default made every handoff
    -- fresh or a deduplicate_open reuse -- look identical at the top level.
    Callers that read the top-level contract (like the deterministic manual
    -handoff path) had no way to tell a reused handoff apart from a real one.
    """
    app, _db = bridge_app
    client = app.test_client()
    app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://staging/redacted"

    payload = {
        "action": "create_handoff",
        "payload": {
            "traveler_id": "TRPG001",
            "reason_code": "customer_requested_human",
            "reason_text": "Customer asked for an agent.",
            "deduplicate_open": True,
            "user_requested_human": True,
        },
        "session_context": {"session_id": "handoff-reuse-pg-1", "language": "en", "user_requested_human": True},
    }

    first = client.post("/api/crm/agent/write", json=payload)
    second = client.post("/api/crm/agent/write", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    first_result = first.get_json()["result"]
    second_result = second.get_json()["result"]
    assert second_result["result_id"] == first_result["result_id"]
    assert second_result["write_result_contract"]["status"] == "reused"
    assert second_result["executed"] is False


def test_sqlite_mode_still_works(bridge_app):
    app, _db = bridge_app
    client = app.test_client()

    response = client.post(
        "/api/crm/agent/read",
        json={"action": "get_traveler_profile", "payload": {"traveler_id": "TRPG001"}},
    )

    assert response.status_code == 200
    assert response.get_json()["result"]["traveler"]["traveler_id"] == "TRPG001"


# ---------------------------------------------------------------------------
# Live-transcript bug: "esclate me" consistently produced "I couldn't submit
# the review request automatically" once escalate-intent detection was fixed.
# Root cause: idempotency_key was added to the TripBooking/Lead/HandoffQueue
# SQLAlchemy models (used by _handoff_by_idempotency's raw SQL lookup) but no
# Alembic migration ever added the column -- a database built purely from
# `flask db upgrade` is missing it, so the very first idempotency lookup in
# create_handoff_case raises a real "no such column" error at the database
# level. That was previously invisible: write_tool_executor.execute()'s
# local-handler except Exception only ever logged a sanitized reason CODE at
# INFO, not the real exception -- see the new ERROR-level log added there.
# ---------------------------------------------------------------------------
def test_handoff_write_fails_loudly_when_idempotency_key_column_is_missing(bridge_app, caplog):
    app, db = bridge_app
    client = app.test_client()
    app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://staging/redacted"

    with app.app_context():
        db.session.execute(db.text("ALTER TABLE handoff_queue DROP COLUMN idempotency_key"))
        db.session.commit()

    payload = {
        "action": "create_handoff",
        "payload": {
            "traveler_id": "TRPG001",
            "reason_code": "customer_requested_human",
            "reason_text": "Customer asked for an agent.",
            "user_requested_human": True,
        },
        "session_context": {"session_id": "handoff-drift-1", "language": "en", "user_requested_human": True},
    }

    with caplog.at_level("ERROR", logger="rahma_agent"):
        response = client.post("/api/crm/agent/write", json=payload)

    assert response.status_code == 200
    result = response.get_json()["result"]
    assert result["write_result_contract"]["status"] == "failed"
    assert result["executed"] is False
    assert any(
        "Local write handler failed" in record.message and "idempotency_key" in record.message
        for record in caplog.records
    ), "expected the real schema-drift exception to be logged at ERROR, not silently swallowed"


def test_handoff_write_succeeds_once_the_idempotency_key_column_is_reconciled(bridge_app):
    """Same missing-column scenario as above, but after applying the fix
    this migration's upgrade() would perform (see
    database/migrations/versions/a3f6c9e2d817_*): the write goes through
    cleanly and a real handoff record is created.
    """
    app, db = bridge_app
    client = app.test_client()
    app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://staging/redacted"
    from app.models import HandoffQueue

    with app.app_context():
        db.session.execute(db.text("ALTER TABLE handoff_queue DROP COLUMN idempotency_key"))
        db.session.execute(db.text("ALTER TABLE handoff_queue ADD COLUMN idempotency_key TEXT"))
        db.session.commit()

    response = client.post(
        "/api/crm/agent/write",
        json={
            "action": "create_handoff",
            "payload": {
                "traveler_id": "TRPG001",
                "reason_code": "customer_requested_human",
                "reason_text": "Customer asked for an agent.",
                "user_requested_human": True,
            },
            "session_context": {"session_id": "handoff-reconciled-1", "language": "en", "user_requested_human": True},
        },
    )

    assert response.status_code == 200
    result = response.get_json()["result"]
    assert result["write_result_contract"]["status"] == "created"
    assert result["executed"] is True
    with app.app_context():
        assert db.session.query(HandoffQueue).count() == 1


# ---------------------------------------------------------------------------
# Confirmed live bug: _next_prefixed_id's `ORDER BY <col> DESC LIMIT 1`
# sorts lexicographically (text), not numerically. A non-sequential row
# (e.g. "H-B3AE7949", from an old script or manual insert) sorts above
# every real sequential ID and gets picked as "last"; blindly extracting
# whatever digits happen to appear in it ("B3AE7949" -> "37949") computes a
# next-number that collides with an existing row instead of advancing past
# it -- reproducible with pure string/int arithmetic, independent of any
# specific live database's actual contents.
# ---------------------------------------------------------------------------
def test_next_prefixed_id_ignores_non_conforming_rows_and_sorts_numerically(bridge_app):
    from app.services.agent_crm_bridge import PostgresAgentBridgeService
    from app.models import HandoffQueue

    app, db = bridge_app
    with app.app_context():
        db.session.add_all([
            HandoffQueue(handoff_id="H-B3AE7949", reason="legacy/manual test data"),
            HandoffQueue(handoff_id="H-914C4F11", reason="legacy/manual test data"),
            HandoffQueue(handoff_id="H-00037950", reason="the only real sequential row"),
        ])
        db.session.commit()

        next_id = PostgresAgentBridgeService()._next_prefixed_id("handoff_queue", "handoff_id", "H-", 8)

        assert next_id == "H-00037951"


def test_create_handoff_case_does_not_collide_with_the_exact_live_non_conforming_rows(bridge_app):
    """The literal reproduction: seed the exact three IDs confirmed live,
    then create a real handoff through create_handoff_case() (not just the
    private ID helper) and confirm it gets a genuinely new ID.
    """
    from app.models import HandoffQueue

    app, db = bridge_app
    client = app.test_client()
    app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://staging/redacted"

    with app.app_context():
        db.session.add_all([
            HandoffQueue(handoff_id="H-B3AE7949", reason="legacy/manual test data"),
            HandoffQueue(handoff_id="H-914C4F11", reason="legacy/manual test data"),
            HandoffQueue(handoff_id="H-00037950", reason="the only real sequential row"),
        ])
        db.session.commit()

    response = client.post(
        "/api/crm/agent/write",
        json={
            "action": "create_handoff",
            "payload": {
                "traveler_id": "TRPG001",
                "reason_code": "customer_requested_human",
                "reason_text": "Customer asked for an agent.",
                "user_requested_human": True,
            },
            "session_context": {"session_id": "handoff-collision-repro-1", "language": "en", "user_requested_human": True},
        },
    )

    assert response.status_code == 200
    result = response.get_json()["result"]
    assert result["write_result_contract"]["status"] == "created"
    assert result["result_id"] == "H-00037951"
    with app.app_context():
        assert db.session.query(HandoffQueue).filter_by(handoff_id="H-00037951").count() == 1
        assert db.session.query(HandoffQueue).count() == 4


def test_next_prefixed_id_starts_from_one_when_every_row_is_non_conforming(bridge_app):
    from app.services.agent_crm_bridge import PostgresAgentBridgeService
    from app.models import HandoffQueue

    app, db = bridge_app
    with app.app_context():
        db.session.add_all([
            HandoffQueue(handoff_id="H-B3AE7949", reason="legacy/manual test data"),
            HandoffQueue(handoff_id="H-914C4F11", reason="legacy/manual test data"),
        ])
        db.session.commit()

        next_id = PostgresAgentBridgeService()._next_prefixed_id("handoff_queue", "handoff_id", "H-", 8)

        assert next_id == "H-00000001"


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
