"""HTTP-level tests for apps/api/app/routes/private_requests.py.

Phase 6 added a full pipeline (create -> stage transitions -> convert), but
shipped with no route-level test at all -- an audit found the transition
table (PRIVATE_REQUEST_TRANSITIONS, which correctly declares "paid" as the
only stage allowed to move to "converted") was enforced by update_stage()
but never checked by convert() itself. An employee could hit Convert from
the very first stage, "registered", skipping consultation, deposit, and
design entirely, and -- since no deposit exists yet at that point -- the
resulting booking would carry no payment evidence at all.

This file pins the fix: convert() now validates the stage before doing
anything else, and the "Convert To Booking" form only renders once the
request has actually reached "paid".
"""
from __future__ import annotations

import os
import shutil
import sys
import uuid
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
API_ROOT = ROOT / "apps" / "api"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))


def _reset_app_modules() -> None:
    for module_name in list(sys.modules):
        if module_name == "app" or module_name.startswith("app."):
            sys.modules.pop(module_name, None)


@pytest.fixture()
def private_request_app():
    original_env = dict(os.environ)
    tmpdir = ROOT / ".tmp-test-workdirs" / f"private-request-routes-{uuid.uuid4().hex}"
    tmpdir.mkdir(parents=True, exist_ok=True)
    db_path = tmpdir / "app.db"
    os.environ["CRM_AUTH_ENABLED"] = "false"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.resolve().as_posix()}"
    os.environ["RAHMA_SYSTEM_DB_PATH"] = str(db_path)
    os.environ["SHEET_BACKEND"] = "excel"
    _reset_app_modules()
    from app import create_app
    from app.extensions import db

    app = create_app("development")
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with app.app_context():
        db.drop_all()
        db.create_all()
    try:
        yield app, db
    finally:
        os.environ.clear()
        os.environ.update(original_env)
        shutil.rmtree(tmpdir, ignore_errors=True)
        _reset_app_modules()


def _login_as_employee(app, client) -> str:
    """private_requests.py's write routes require a real employee session
    (@employee_session_required checks current_user(), not CRM_AUTH_ENABLED)
    plus a matching CSRF token (a custom session-based check, not Flask-WTF,
    so WTF_CSRF_ENABLED=False does not bypass it) -- without both, every
    POST below 302s to /login or 400s on CSRF, and every assertion that
    follows would be checking the wrong response entirely. Returns the csrf
    token to send back as form data on each POST.
    """
    from app.models.user import User

    with app.app_context():
        db_module = __import__("app.extensions", fromlist=["db"]).db
        user = User(username=f"agent-{uuid.uuid4().hex[:8]}", full_name="Test Agent", password_hash="x", role="agent", is_active=True)
        db_module.session.add(user)
        db_module.session.commit()
        user_id, username = user.id, user.username
    csrf_token = uuid.uuid4().hex
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_id"] = user_id
        sess["username"] = username
        sess["csrf_token"] = csrf_token
    return csrf_token


def _seed_request(db, *, request_id: str, stage: str, with_deposit: bool = False):
    from app.models.private_trip_request import PrivateTripRequest
    from app.models.traveler import Traveler
    from services.crm.system_services.private_trips import utc_now

    traveler = Traveler(traveler_id=f"TR-{request_id}", full_name="Private Traveler", status="Active")
    db.session.add(traveler)

    item = PrivateTripRequest(
        request_id=request_id,
        traveler_id=traveler.traveler_id,
        service_type="full_package",
        trip_scope="International",
        destination="Maldives",
        party_size=2,
        budget_amount=5000.0,
        budget_currency="USD",
        stage=stage,
        stage_changed_at=utc_now().replace(tzinfo=None),
    )
    if with_deposit:
        item.deposit_amount = 500.0
        item.deposit_currency = "USD"
        item.deposit_paid_at = utc_now().replace(tzinfo=None)
        item.deposit_is_refundable = False
    db.session.add(item)
    db.session.commit()
    return item


def test_convert_is_rejected_before_the_paid_stage(private_request_app) -> None:
    app, db = private_request_app
    client = app.test_client()
    csrf_token = _login_as_employee(app, client)
    with app.app_context():
        _seed_request(db, request_id="PRT-000001", stage="registered")

    response = client.post(
        "/admin/private-requests/PRT-000001/convert",
        data={"csrf_token": csrf_token},
        follow_redirects=True,
    )

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "must reach the &#39;paid&#39; stage" in body or "must reach the 'paid' stage" in body

    with app.app_context():
        from app.models.booking import TripBooking
        from app.models.private_trip_request import PrivateTripRequest

        item = db.session.get(PrivateTripRequest, "PRT-000001")
        assert item.converted_booking_id is None
        assert item.stage == "registered"
        assert TripBooking.query.count() == 0


@pytest.mark.parametrize(
    "stage",
    ["consultation_scheduled", "consultation_done", "deposit_pending", "deposit_paid", "designing", "design_delivered", "payment_pending"],
)
def test_convert_is_rejected_at_every_stage_before_paid(private_request_app, stage) -> None:
    app, db = private_request_app
    client = app.test_client()
    csrf_token = _login_as_employee(app, client)
    with app.app_context():
        _seed_request(db, request_id="PRT-000002", stage=stage, with_deposit=True)

    response = client.post(
        "/admin/private-requests/PRT-000002/convert",
        data={"csrf_token": csrf_token},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "must reach the" in response.get_data(as_text=True)

    with app.app_context():
        from app.models.booking import TripBooking
        from app.models.private_trip_request import PrivateTripRequest

        item = db.session.get(PrivateTripRequest, "PRT-000002")
        assert item.converted_booking_id is None
        assert item.stage == stage
        assert TripBooking.query.count() == 0


def test_convert_succeeds_once_the_request_is_at_the_paid_stage(private_request_app) -> None:
    app, db = private_request_app
    client = app.test_client()
    csrf_token = _login_as_employee(app, client)
    with app.app_context():
        _seed_request(db, request_id="PRT-000003", stage="paid", with_deposit=True)

    response = client.post(
        "/admin/private-requests/PRT-000003/convert",
        data={"csrf_token": csrf_token, "room_type": "Double", "currency": "USD", "total_price": "5000"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        from app.models.booking import TripBooking
        from app.models.booking_transaction import BookingTransaction
        from app.models.private_trip_request import PrivateTripRequest

        item = db.session.get(PrivateTripRequest, "PRT-000003")
        assert item.stage == "converted"
        assert item.converted_booking_id is not None

        booking = db.session.get(TripBooking, item.converted_booking_id)
        assert booking is not None

        txn = BookingTransaction.query.filter_by(booking_id=booking.booking_id).one()
        assert txn.is_non_refundable is True


def test_the_convert_form_only_renders_at_the_paid_stage(private_request_app) -> None:
    app, db = private_request_app
    client = app.test_client()
    _login_as_employee(app, client)
    with app.app_context():
        _seed_request(db, request_id="PRT-000004", stage="deposit_paid", with_deposit=True)
        _seed_request(db, request_id="PRT-000005", stage="paid", with_deposit=True)

    not_yet = client.get("/admin/private-requests/PRT-000004").get_data(as_text=True)
    assert "Convert To Booking" not in not_yet
    assert "Conversion is available once this request reaches" in not_yet

    ready = client.get("/admin/private-requests/PRT-000005").get_data(as_text=True)
    assert "Convert To Booking" in ready


if __name__ == "__main__":  # pragma: no cover
    import unittest

    unittest.main()
