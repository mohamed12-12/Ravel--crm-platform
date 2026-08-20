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


# ---------------------------------------------------------------------------
# The create form's traveler picker used to be a plain <select> preloaded
# with Traveler.query.order_by(full_name).limit(300) -- alphabetically past
# the 300th traveler was simply not selectable, ever, at creation time. It's
# now a type-to-search box backed by these two endpoints.
# ---------------------------------------------------------------------------
def test_search_travelers_matches_by_name_id_or_phone(private_request_app) -> None:
    app, db = private_request_app
    client = app.test_client()
    _login_as_employee(app, client)
    with app.app_context():
        from app.models.traveler import Traveler

        db.session.add(Traveler(traveler_id="TR00301", full_name="Maged Aweis Alani", status="Active", whatsapp_raw="01270482380"))
        db.session.commit()

    by_name = client.get("/admin/private-requests/search/travelers?q=Maged").get_json()
    assert by_name["results"] == [{"id": "TR00301", "label": "Maged Aweis Alani — TR00301"}]

    by_id = client.get("/admin/private-requests/search/travelers?q=TR00301").get_json()
    assert by_id["results"][0]["id"] == "TR00301"

    by_phone = client.get("/admin/private-requests/search/travelers?q=01270482380").get_json()
    assert by_phone["results"][0]["id"] == "TR00301"

    no_match = client.get("/admin/private-requests/search/travelers?q=zzzzzz").get_json()
    assert no_match["results"] == []

    too_short = client.get("/admin/private-requests/search/travelers?q=a").get_json()
    assert too_short["results"] == []


def test_search_travelers_requires_employee_session(private_request_app) -> None:
    app, db = private_request_app
    client = app.test_client()
    response = client.get("/admin/private-requests/search/travelers?q=Maged")
    assert response.status_code in (302, 401, 403)


def test_search_leads_matches_by_name_id_or_phone(private_request_app) -> None:
    app, db = private_request_app
    client = app.test_client()
    _login_as_employee(app, client)
    with app.app_context():
        from app.models.lead import Lead
        from services.crm.system_services.private_trips import utc_now

        db.session.add(
            Lead(
                lead_id="LD00099",
                customer_name="Sara Youssef",
                raw_phone="01112223344",
                created_at=utc_now().replace(tzinfo=None),
            )
        )
        db.session.commit()

    by_name = client.get("/admin/private-requests/search/leads?q=Sara").get_json()
    assert by_name["results"] == [{"id": "LD00099", "label": "Sara Youssef — LD00099"}]

    by_id = client.get("/admin/private-requests/search/leads?q=LD00099").get_json()
    assert by_id["results"][0]["id"] == "LD00099"


def test_create_still_accepts_traveler_and_lead_ids_from_the_picker(private_request_app) -> None:
    """The picker submits the same hidden traveler_id/lead_id fields the old
    <select>/<input> did -- create() itself needs no change."""
    app, db = private_request_app
    client = app.test_client()
    csrf_token = _login_as_employee(app, client)
    with app.app_context():
        from app.models.traveler import Traveler

        db.session.add(Traveler(traveler_id="TR00302", full_name="Picked Traveler", status="Active"))
        db.session.commit()

    response = client.post(
        "/admin/private-requests/",
        data={
            "csrf_token": csrf_token,
            "traveler_id": "TR00302",
            "lead_id": "",
            "service_type": "consultation",
            "trip_scope": "Local",
            "destination": "Luxor",
            "party_size": "2",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        from app.models.private_trip_request import PrivateTripRequest

        item = PrivateTripRequest.query.filter_by(traveler_id="TR00302").one()
        assert item.destination == "Luxor"


def _login_as(app, client, *, role: str) -> str:
    """Same contract as _login_as_employee, but lets a test pick the role --
    needed here because assign_work is admin/manager-only (see security.py's
    ROLE_PERMISSIONS), unlike _login_as_employee's hardcoded 'agent'.
    """
    from app.models.user import User

    with app.app_context():
        db_module = __import__("app.extensions", fromlist=["db"]).db
        user = User(username=f"{role}-{uuid.uuid4().hex[:8]}", full_name=f"Test {role.title()}", password_hash="x", role=role, is_active=True)
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


# ---------------------------------------------------------------------------
# Employee follow-up parity with Leads: private requests used to have no
# "who's on this / are we waiting on the customer" tracking at all, only the
# consultation/deposit/design pipeline stage -- so this pins the new
# quick-action, follow-up, and assignment routes actually persist.
# ---------------------------------------------------------------------------
def test_quick_action_updates_followup_fields_without_touching_stage(private_request_app) -> None:
    app, db = private_request_app
    client = app.test_client()
    csrf_token = _login_as_employee(app, client)
    with app.app_context():
        _seed_request(db, request_id="PRT-000010", stage="consultation_scheduled")

    response = client.post(
        "/admin/private-requests/PRT-000010/quick-action",
        json={"action": "mark_contacted"},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"

    with app.app_context():
        from app.models.private_trip_request import PrivateTripRequest

        item = db.session.get(PrivateTripRequest, "PRT-000010")
        assert item.stage == "consultation_scheduled"
        assert item.customer_response_status == "Contacted"
        assert item.follow_up_status == "Contacted"
        assert item.last_contact_at is not None


def test_quick_action_rejects_unknown_action(private_request_app) -> None:
    app, db = private_request_app
    client = app.test_client()
    csrf_token = _login_as_employee(app, client)
    with app.app_context():
        _seed_request(db, request_id="PRT-000011", stage="registered")

    response = client.post(
        "/admin/private-requests/PRT-000011/quick-action",
        json={"action": "not_a_real_action"},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert response.status_code == 400


def test_update_followup_sets_priority_and_next_followup_date(private_request_app) -> None:
    app, db = private_request_app
    client = app.test_client()
    csrf_token = _login_as_employee(app, client)
    with app.app_context():
        _seed_request(db, request_id="PRT-000012", stage="registered")

    response = client.post(
        "/admin/private-requests/PRT-000012/followup",
        data={"csrf_token": csrf_token, "priority": "High", "follow_up_due_date": "2026-09-01", "channel": "whatsapp"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        from app.models.private_trip_request import PrivateTripRequest

        item = db.session.get(PrivateTripRequest, "PRT-000012")
        assert item.priority == "High"
        assert item.follow_up_due_date == date(2026, 9, 1)
        assert item.channel == "whatsapp"


def test_assign_requires_assign_work_permission(private_request_app) -> None:
    app, db = private_request_app
    client = app.test_client()
    csrf_token = _login_as(app, client, role="agent")
    with app.app_context():
        _seed_request(db, request_id="PRT-000013", stage="registered")

    response = client.post(
        "/admin/private-requests/PRT-000013/assign",
        data={"csrf_token": csrf_token, "assigned_to_user_id": ""},
    )
    assert response.status_code == 403


def test_manager_can_assign_a_private_request_to_an_employee(private_request_app) -> None:
    app, db = private_request_app
    client = app.test_client()
    csrf_token = _login_as(app, client, role="manager")
    with app.app_context():
        _seed_request(db, request_id="PRT-000014", stage="registered")
        from app.models.user import User

        employee = User(username="employee-1", full_name="Sales Person", password_hash="x", role="sales", is_active=True)
        db.session.add(employee)
        db.session.commit()
        employee_id = employee.id

    response = client.post(
        f"/admin/private-requests/PRT-000014/assign",
        data={"csrf_token": csrf_token, "assigned_to_user_id": str(employee_id), "assignment_reason": "New request"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        from app.models.private_trip_request import PrivateTripRequest

        item = db.session.get(PrivateTripRequest, "PRT-000014")
        assert item.assigned_to_user_id == employee_id
        assert item.assigned_to == "Sales Person"

    detail = client.get("/admin/private-requests/PRT-000014").get_data(as_text=True)
    assert "Sales Person" in detail


def test_detail_page_renders_employee_followup_panel(private_request_app) -> None:
    app, db = private_request_app
    client = app.test_client()
    _login_as_employee(app, client)
    with app.app_context():
        _seed_request(db, request_id="PRT-000015", stage="registered")

    body = client.get("/admin/private-requests/PRT-000015").get_data(as_text=True)
    assert "Employee Follow-up" in body
    assert "Mark Customer Contacted" in body
    assert "Waiting for Customer" in body


# ---------------------------------------------------------------------------
# Layout rewrite. PRIVATE_REQUEST_TRANSITIONS is strictly linear -- one forward
# move per stage plus "lost" -- but the page used to render a Move button for
# all 11 stages side by side, so 9 of the 10 buttons an employee could see only
# ever produced a "Cannot move X to Y" flash. It also printed raw datetimes
# ("2026-08-22 16:19:10.254575") and showed the linked lead/traveler/booking as
# unclickable plain text.
# ---------------------------------------------------------------------------
def test_detail_page_links_every_related_record(private_request_app) -> None:
    """The lead, traveler and converted booking were plain unclickable text, so
    an employee had to go find each one by hand. Also exercises the url_for
    branches a request with no links never reaches.
    """
    app, db = private_request_app
    client = app.test_client()
    _login_as_employee(app, client)
    with app.app_context():
        from app.models.lead import Lead
        from services.crm.system_services.private_trips import utc_now

        item = _seed_request(db, request_id="PRT-000030", stage="paid", with_deposit=True)
        db.session.add(
            Lead(
                lead_id="LD00777",
                customer_name="Private Traveler",
                raw_phone="01012345678",
                created_at=utc_now().replace(tzinfo=None),
            )
        )
        item.lead_id = "LD00777"
        db.session.commit()

    body = client.get("/admin/private-requests/PRT-000030").get_data(as_text=True)
    assert "/leads/LD00777" in body
    assert "/travelers/TR-PRT-000030" in body
    # Still the paid-stage conversion form, unchanged.
    assert "Convert To Booking" in body


def test_index_board_renders_with_the_new_request_form(private_request_app) -> None:
    app, db = private_request_app
    client = app.test_client()
    _login_as_employee(app, client)
    with app.app_context():
        _seed_request(db, request_id="PRT-000031", stage="registered")

    body = client.get("/admin/private-requests/").get_data(as_text=True)
    assert "New Private Request" in body
    assert "PRT-000031" in body
    # Ownership is visible from the board, not only from the detail page.
    assert "Unassigned" in body


def test_detail_page_only_offers_the_one_legal_forward_stage(private_request_app) -> None:
    app, db = private_request_app
    client = app.test_client()
    _login_as_employee(app, client)
    with app.app_context():
        _seed_request(db, request_id="PRT-000020", stage="consultation_scheduled")

    body = client.get("/admin/private-requests/PRT-000020").get_data(as_text=True)

    # The single legal next stage, offered twice (header + Actions panel).
    assert body.count('name="stage" value="consultation_done"') == 2
    assert 'name="stage" value="lost"' in body
    for illegal in ("deposit_pending", "deposit_paid", "designing", "design_delivered", "payment_pending", "paid", "converted"):
        assert f'name="stage" value="{illegal}"' not in body


def test_detail_page_offers_no_forward_move_once_closed(private_request_app) -> None:
    app, db = private_request_app
    client = app.test_client()
    _login_as_employee(app, client)
    with app.app_context():
        _seed_request(db, request_id="PRT-000021", stage="converted")

    body = client.get("/admin/private-requests/PRT-000021").get_data(as_text=True)
    assert 'name="stage" value=' not in body
    assert "cannot move any further" in body


def test_detail_page_formats_timestamps_instead_of_printing_raw_datetimes(private_request_app) -> None:
    app, db = private_request_app
    client = app.test_client()
    _login_as_employee(app, client)
    with app.app_context():
        item = _seed_request(db, request_id="PRT-000022", stage="registered")
        raw_due = str(item.consultation_due_at)

    body = client.get("/admin/private-requests/PRT-000022").get_data(as_text=True)
    assert raw_due not in body
    assert "Consultation due" in body


def test_mark_lost_records_the_reason(private_request_app) -> None:
    app, db = private_request_app
    client = app.test_client()
    csrf_token = _login_as_employee(app, client)
    with app.app_context():
        _seed_request(db, request_id="PRT-000023", stage="registered")

    response = client.post(
        "/admin/private-requests/PRT-000023/stage",
        data={"csrf_token": csrf_token, "stage": "lost", "lost_reason": "Budget too low"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        from app.models.private_trip_request import PrivateTripRequest

        item = db.session.get(PrivateTripRequest, "PRT-000023")
        assert item.stage == "lost"
        assert item.lost_reason == "Budget too low"


# ---------------------------------------------------------------------------
# Nothing on the brief (destination, dates, party size, budget) could be
# corrected from the CRM at all, so a typo in an agent-captured intake had to
# be worked around by hand.
# ---------------------------------------------------------------------------
def test_update_corrects_the_request_brief(private_request_app) -> None:
    app, db = private_request_app
    client = app.test_client()
    csrf_token = _login_as_employee(app, client)
    with app.app_context():
        _seed_request(db, request_id="PRT-000024", stage="consultation_scheduled")

    response = client.post(
        "/admin/private-requests/PRT-000024/update",
        data={
            "csrf_token": csrf_token,
            "service_type": "consultation",
            "trip_scope": "Local",
            "destination": "Hurghada",
            "start_date_pref": "2026-09-20",
            "end_date_pref": "2026-09-27",
            "dates_flexible": "1",
            "party_size": "5",
            "budget_amount": "8000",
            "budget_currency": "EGP",
            "notes": "Corrected after a call with the customer.",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        from app.models.private_trip_request import PrivateTripRequest

        item = db.session.get(PrivateTripRequest, "PRT-000024")
        assert item.destination == "Hurghada"
        assert item.service_type == "consultation"
        assert item.trip_scope == "Local"
        assert item.start_date_pref == date(2026, 9, 20)
        assert item.end_date_pref == date(2026, 9, 27)
        assert item.dates_flexible is True
        assert item.party_size == 5
        assert item.budget_amount == 8000.0
        assert item.budget_currency == "EGP"
        assert item.notes == "Corrected after a call with the customer."
        # Untouched by design: the pipeline stage and the traveler link.
        assert item.stage == "consultation_scheduled"
        assert item.traveler_id == "TR-PRT-000024"


def test_update_keeps_the_existing_traveler_when_the_picker_is_left_blank(private_request_app) -> None:
    app, db = private_request_app
    client = app.test_client()
    csrf_token = _login_as_employee(app, client)
    with app.app_context():
        _seed_request(db, request_id="PRT-000025", stage="registered")

    client.post(
        "/admin/private-requests/PRT-000025/update",
        data={
            "csrf_token": csrf_token,
            "service_type": "full_package",
            "trip_scope": "International",
            "destination": "Maldives",
            "traveler_id": "",
            "lead_id": "",
        },
        follow_redirects=True,
    )

    with app.app_context():
        from app.models.private_trip_request import PrivateTripRequest

        item = db.session.get(PrivateTripRequest, "PRT-000025")
        assert item.traveler_id == "TR-PRT-000025"


def test_update_rejects_a_blank_destination(private_request_app) -> None:
    app, db = private_request_app
    client = app.test_client()
    csrf_token = _login_as_employee(app, client)
    with app.app_context():
        _seed_request(db, request_id="PRT-000026", stage="registered")

    response = client.post(
        "/admin/private-requests/PRT-000026/update",
        data={
            "csrf_token": csrf_token,
            "service_type": "full_package",
            "trip_scope": "International",
            "destination": "   ",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        from app.models.private_trip_request import PrivateTripRequest

        item = db.session.get(PrivateTripRequest, "PRT-000026")
        assert item.destination == "Maldives"


if __name__ == "__main__":  # pragma: no cover
    import unittest

    unittest.main()
