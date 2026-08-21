"""Private trip money, additional fees, and how both reach Revenue Analytics.

Private trips used to earn revenue only by being converted into a synthetic
Trip + TripBooking, and a booking was worth exactly its room price -- there was
nowhere to record a visa, an insurance policy or a transfer the customer paid
for. This file pins the replacement:

* a private request carries its own agreed price, additional fees and an
  append-only payment ledger;
* **its revenue is money received, never the agreed price** -- the single most
  important rule here, because reporting a quote as income reports money the
  company does not have;
* additional fees count toward the booking or request they belong to, in its
  own currency, and are voided rather than deleted;
* EGP and USD are never added together and no exchange rate is ever applied;
* every claim about money ("deposit paid", "fully paid") is derived from
  recorded entries rather than typed by hand.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
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
def money_app():
    original_env = dict(os.environ)
    tmpdir = ROOT / ".tmp-test-workdirs" / f"private-money-{uuid.uuid4().hex}"
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


def _login(app, client, *, role: str = "manager") -> str:
    from app.models.user import User

    with app.app_context():
        db_module = __import__("app.extensions", fromlist=["db"]).db
        user = User(
            username=f"{role}-{uuid.uuid4().hex[:8]}",
            full_name=f"Test {role.title()}",
            password_hash="x",
            role=role,
            is_active=True,
        )
        db_module.session.add(user)
        db_module.session.commit()
        user_id, username = user.id, user.username
    token = uuid.uuid4().hex
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_id"] = user_id
        sess["username"] = username
        sess["csrf_token"] = token
    return token


def _seed_traveler(db, traveler_id: str = "TR-PM-1", name: str = "Private Payer"):
    from app.models.traveler import Traveler

    traveler = Traveler(traveler_id=traveler_id, full_name=name, status="Active")
    db.session.add(traveler)
    db.session.commit()
    return traveler


def _seed_request(
    db,
    *,
    request_id: str = "PRT-900001",
    traveler_id: str = "TR-PM-1",
    stage: str = "consultation_done",
    price: float | None = None,
    currency: str | None = None,
):
    from app.models.private_trip_request import PrivateTripRequest
    from services.crm.system_services.private_trips import utc_now

    item = PrivateTripRequest(
        request_id=request_id,
        traveler_id=traveler_id,
        service_type="full_package",
        trip_scope="International",
        destination="Maldives",
        party_size=2,
        budget_amount=40000.0,
        budget_currency="EGP",
        agreed_price_amount=price,
        agreed_price_currency=currency,
        stage=stage,
        stage_changed_at=utc_now().replace(tzinfo=None),
    )
    db.session.add(item)
    db.session.commit()
    return item


def _seed_booking(
    db,
    *,
    booking_id: str = "BK-PM-1",
    traveler_id: str = "TR-PM-1",
    currency: str = "USD",
    booking_status: str = "Confirmed",
    payment_status: str = "Fully Paid",
    group_size: int = 2,
):
    from app.models.booking import TripBooking
    from app.models.trip import Trip

    if db.session.get(Trip, "TRIP-PM-1") is None:
        db.session.add(Trip(
            trip_id="TRIP-PM-1",
            trip_name="Fee Trip",
            type="Local",
            sales_status="Open",
            room_prices_json=json.dumps({"double": {"USD": "1000", "EGP": "50000"}}),
        ))
    booking = TripBooking(
        booking_id=booking_id,
        trip_id="TRIP-PM-1",
        trip_name="Fee Trip",
        traveler_id=traveler_id,
        traveler_name="Private Payer",
        room_type="Double",
        currency=currency,
        group_size=group_size,
        booking_status=booking_status,
        payment_status=payment_status,
        draft_created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    db.session.add(booking)
    db.session.commit()
    return booking


# ===========================================================================
# Revenue is money received, not the agreed price
# ===========================================================================


def test_an_agreed_price_with_no_payment_earns_nothing(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.services.private_trip_money import money_for

        _seed_traveler(db)
        item = _seed_request(db, price=45000.0, currency="EGP")
        money = money_for(item)

        assert money.price == 45000.0
        assert money.contract_value == 45000.0
        # The whole point: a quote is not income.
        assert money.gross_revenue == 0.0
        assert money.net_revenue == 0.0
        assert money.outstanding == 45000.0
        assert money.payment_status == "Pending"


def test_a_deposit_earns_the_deposit_not_the_price(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.services.private_trip_money import money_for, record_payment

        _seed_traveler(db)
        item = _seed_request(db, price=45000.0, currency="EGP")
        record_payment(item, amount=15000, currency="EGP", occurred_on="2026-08-01")
        db.session.commit()

        money = money_for(item)
        assert money.net_revenue == 15000.0
        assert money.outstanding == 30000.0
        assert money.payment_status == "Deposit Paid"


def test_settling_the_balance_reads_as_fully_paid(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.services.private_trip_money import money_for, record_payment

        _seed_traveler(db)
        item = _seed_request(db, price=45000.0, currency="EGP")
        record_payment(item, amount=15000, currency="EGP", occurred_on="2026-08-01")
        record_payment(item, amount=30000, currency="EGP", occurred_on="2026-08-11")
        db.session.commit()

        money = money_for(item)
        assert money.net_revenue == 45000.0
        assert money.outstanding == 0.0
        assert money.payment_status == "Fully Paid"
        assert money.is_fully_collected is True


def test_money_received_with_no_agreed_price_is_revenue_but_the_balance_is_unknown(money_app) -> None:
    """Understating is the only safe direction: without a price there is no way
    to tell a deposit from a settled balance, so it never claims Fully Paid and
    never reports a zero balance it cannot support."""
    app, db = money_app
    with app.app_context():
        from app.services.private_trip_money import money_for, record_payment

        _seed_traveler(db)
        item = _seed_request(db)
        record_payment(item, amount=5000, currency="EGP", occurred_on="2026-08-01")
        db.session.commit()

        money = money_for(item)
        assert money.net_revenue == 5000.0
        assert money.contract_value is None
        assert money.outstanding is None
        assert money.payment_status == "Deposit Paid"


def test_a_refund_reduces_recognized_revenue(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.services.private_trip_money import money_for, record_payment, record_refund

        _seed_traveler(db)
        item = _seed_request(db, price=45000.0, currency="EGP")
        record_payment(item, amount=20000, currency="EGP", occurred_on="2026-08-01")
        db.session.commit()
        record_refund(item, amount=5000, reason="Trip shortened", occurred_on="2026-08-10")
        db.session.commit()

        money = money_for(item)
        assert money.gross_revenue == 20000.0
        assert money.refunded == 5000.0
        assert money.net_revenue == 15000.0
        assert money.payment_status == "Partial Refund"


def test_refunding_everything_received_nets_zero(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.services.private_trip_money import money_for, record_payment, record_refund

        _seed_traveler(db)
        item = _seed_request(db, price=45000.0, currency="EGP")
        record_payment(item, amount=20000, currency="EGP", occurred_on="2026-08-01")
        db.session.commit()
        record_refund(item, amount=20000, reason="Cancelled", occurred_on="2026-08-10")
        db.session.commit()

        money = money_for(item)
        assert money.net_revenue == 0.0
        assert money.payment_status == "Full Refund"


# ===========================================================================
# Guards on the money write path
# ===========================================================================


def test_a_refund_cannot_exceed_what_was_received(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.services.private_trip_money import record_payment, record_refund

        _seed_traveler(db)
        item = _seed_request(db, price=45000.0, currency="EGP")
        record_payment(item, amount=10000, currency="EGP", occurred_on="2026-08-01")
        db.session.commit()

        with pytest.raises(ValueError) as excinfo:
            record_refund(item, amount=10001, reason="Too much", occurred_on="2026-08-02")
        assert "cannot exceed" in str(excinfo.value)


def test_a_non_refundable_deposit_is_excluded_from_the_refund_ceiling(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.services.private_trip_money import record_payment, record_refund, refund_ceiling

        _seed_traveler(db)
        item = _seed_request(db, price=45000.0, currency="EGP")
        record_payment(
            item, amount=15000, currency="EGP", occurred_on="2026-08-01", is_non_refundable=True
        )
        record_payment(item, amount=10000, currency="EGP", occurred_on="2026-08-02")
        db.session.commit()

        # Only the refundable 10,000 may be given back, not the 25,000 received.
        assert refund_ceiling(item) == 10000.0
        with pytest.raises(ValueError):
            record_refund(item, amount=12000, reason="Cancelled", occurred_on="2026-08-05")
        record_refund(item, amount=10000, reason="Cancelled", occurred_on="2026-08-05")
        db.session.commit()


def test_a_payment_in_another_currency_is_rejected(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.services.private_trip_money import record_payment

        _seed_traveler(db)
        item = _seed_request(db, price=45000.0, currency="EGP")
        with pytest.raises(ValueError) as excinfo:
            record_payment(item, amount=1000, currency="USD", occurred_on="2026-08-01")
        assert "never converts" in str(excinfo.value)


def test_a_request_holding_payments_cannot_be_repriced_in_another_currency(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.services.private_trip_money import record_payment, set_agreed_price

        _seed_traveler(db)
        item = _seed_request(db, price=45000.0, currency="EGP")
        record_payment(item, amount=15000, currency="EGP", occurred_on="2026-08-01")
        db.session.commit()

        with pytest.raises(ValueError) as excinfo:
            set_agreed_price(item, amount=3000, currency="USD")
        assert "already holds payments in EGP" in str(excinfo.value)
        # Repricing in the same currency is still fine.
        set_agreed_price(item, amount=50000, currency="EGP")
        db.session.commit()
        assert item.agreed_price_amount == 50000.0


def test_a_payment_cannot_be_dated_in_the_future(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.services.private_trip_money import record_payment

        _seed_traveler(db)
        item = _seed_request(db, price=45000.0, currency="EGP")
        tomorrow = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
        with pytest.raises(ValueError) as excinfo:
            record_payment(item, amount=1000, currency="EGP", occurred_on=tomorrow)
        assert "future" in str(excinfo.value)


@pytest.mark.parametrize("bad_amount", ["0", "-5", "abc", "nan", "inf", ""])
def test_an_unusable_amount_is_never_recorded(money_app, bad_amount) -> None:
    app, db = money_app
    with app.app_context():
        from app.services.private_trip_money import record_payment

        _seed_traveler(db)
        item = _seed_request(db, price=45000.0, currency="EGP")
        with pytest.raises(ValueError):
            record_payment(item, amount=bad_amount, currency="EGP", occurred_on="2026-08-01")


def test_a_recorded_entry_cannot_be_edited_or_deleted(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.services.private_trip_ledger import PrivateLedgerImmutableError
        from app.services.private_trip_money import record_payment

        _seed_traveler(db)
        item = _seed_request(db, price=45000.0, currency="EGP")
        entry = record_payment(item, amount=15000, currency="EGP", occurred_on="2026-08-01")
        db.session.commit()

        entry.amount = 99999.0
        with pytest.raises(PrivateLedgerImmutableError):
            db.session.commit()
        db.session.rollback()

        db.session.delete(entry)
        with pytest.raises(PrivateLedgerImmutableError):
            db.session.commit()
        db.session.rollback()


def test_a_mistake_is_corrected_by_a_reversal_that_leaves_both_rows(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.models.private_trip_transaction import PrivateTripTransaction
        from app.services.private_trip_money import money_for, record_payment, reverse_entry

        _seed_traveler(db)
        item = _seed_request(db, price=45000.0, currency="EGP")
        entry = record_payment(item, amount=50000, currency="EGP", occurred_on="2026-08-01")
        db.session.commit()
        assert money_for(item).paid == 50000.0

        reverse_entry(entry, reason="Typo: should have been 5,000")
        db.session.commit()

        assert money_for(item).paid == 0.0
        assert PrivateTripTransaction.query.filter_by(request_id=item.request_id).count() == 2


def test_an_entry_cannot_be_reversed_twice(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.services.private_trip_money import record_payment, reverse_entry

        _seed_traveler(db)
        item = _seed_request(db, price=45000.0, currency="EGP")
        entry = record_payment(item, amount=1000, currency="EGP", occurred_on="2026-08-01")
        db.session.commit()
        reverse_entry(entry, reason="First correction")
        db.session.commit()
        with pytest.raises(ValueError) as excinfo:
            reverse_entry(entry, reason="Again")
        assert "already reversed" in str(excinfo.value)


def test_the_public_reference_identifies_the_ledger_and_the_kind(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.services.private_trip_money import record_payment, record_refund

        _seed_traveler(db)
        item = _seed_request(db, price=45000.0, currency="EGP")
        payment = record_payment(item, amount=1000, currency="EGP", occurred_on="2026-08-01")
        db.session.commit()
        refund = record_refund(item, amount=400, reason="Partial cancel", occurred_on="2026-08-02")
        db.session.commit()

        assert payment.public_ref.startswith("PTP-")
        assert refund.public_ref.startswith("PTR-")


# ===========================================================================
# The deposit stage has to be backed by money
# ===========================================================================


def test_marking_the_deposit_paid_without_money_is_refused(money_app) -> None:
    app, db = money_app
    client = app.test_client()
    token = _login(app, client)
    with app.app_context():
        _seed_traveler(db)
        _seed_request(db, request_id="PRT-900010", stage="deposit_pending", price=45000.0, currency="EGP")

    response = client.post(
        "/admin/private-requests/PRT-900010/stage",
        data={"csrf_token": token, "stage": "deposit_paid"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Record the deposit payment" in response.get_data(as_text=True)

    with app.app_context():
        from app.models.private_trip_request import PrivateTripRequest

        item = db.session.get(PrivateTripRequest, "PRT-900010")
        assert item.stage == "deposit_pending"
        assert item.deposit_amount is None


def test_the_deposit_stage_records_a_non_refundable_ledger_payment(money_app) -> None:
    app, db = money_app
    client = app.test_client()
    token = _login(app, client)
    with app.app_context():
        _seed_traveler(db)
        _seed_request(db, request_id="PRT-900011", stage="deposit_pending", price=45000.0, currency="EGP")

    response = client.post(
        "/admin/private-requests/PRT-900011/stage",
        data={
            "csrf_token": token,
            "stage": "deposit_paid",
            "deposit_amount": "15000",
            "deposit_currency": "EGP",
            "deposit_paid_on": "2026-08-05",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        from app.models.private_trip_request import PrivateTripRequest
        from app.services.private_trip_ledger import ledger_totals
        from app.services.private_trip_money import money_for

        item = db.session.get(PrivateTripRequest, "PRT-900011")
        assert item.stage == "deposit_paid"
        assert item.deposit_amount == 15000.0
        assert item.deposit_is_refundable is False
        # The design clock starts from the deposit date, not from "now".
        assert item.design_due_at is not None

        totals = ledger_totals("PRT-900011")
        assert totals.total_paid == 15000.0
        assert totals.non_refundable_paid == 15000.0
        assert totals.remaining_refundable == 0.0
        assert money_for(item).net_revenue == 15000.0


def test_a_resubmitted_deposit_form_does_not_record_the_money_twice(money_app) -> None:
    """Same discipline as the booking webhook path: a double-submitted form or a
    retried request must not double the money."""
    app, db = money_app
    client = app.test_client()
    token = _login(app, client)
    with app.app_context():
        _seed_traveler(db)
        _seed_request(db, request_id="PRT-900012", stage="deposit_pending", price=45000.0, currency="EGP")

    payload = {
        "csrf_token": token,
        "stage": "deposit_paid",
        "deposit_amount": "15000",
        "deposit_currency": "EGP",
        "deposit_paid_on": "2026-08-05",
    }
    client.post("/admin/private-requests/PRT-900012/stage", data=payload, follow_redirects=True)
    client.post("/admin/private-requests/PRT-900012/stage", data=payload, follow_redirects=True)

    with app.app_context():
        from app.models.private_trip_transaction import PrivateTripTransaction
        from app.services.private_trip_ledger import ledger_totals

        assert PrivateTripTransaction.query.filter_by(request_id="PRT-900012").count() == 1
        assert ledger_totals("PRT-900012").total_paid == 15000.0


def test_the_same_idempotency_key_never_records_the_money_twice(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.models.private_trip_transaction import PrivateTripTransaction
        from app.services.private_trip_money import money_for, record_payment

        _seed_traveler(db)
        item = _seed_request(db, price=45000.0, currency="EGP")
        first = record_payment(
            item, amount=15000, currency="EGP", occurred_on="2026-08-01",
            idempotency_key="private-deposit:PRT-900001",
        )
        db.session.commit()
        second = record_payment(
            item, amount=15000, currency="EGP", occurred_on="2026-08-01",
            idempotency_key="private-deposit:PRT-900001",
        )
        db.session.commit()

        assert second.transaction_id == first.transaction_id
        assert PrivateTripTransaction.query.filter_by(request_id=item.request_id).count() == 1
        assert money_for(item).paid == 15000.0


def test_the_request_page_shows_the_money_in_its_own_currency(money_app) -> None:
    app, db = money_app
    client = app.test_client()
    _login(app, client)
    with app.app_context():
        from app.services.additional_fees import add_fee
        from app.services.private_trip_money import record_payment

        _seed_traveler(db)
        item = _seed_request(db, request_id="PRT-907001", price=45000.0, currency="EGP")
        record_payment(item, amount=15000, currency="EGP", occurred_on="2026-08-01")
        add_fee(private_request=item, label="Airport transfer", amount="1500", currency="EGP")
        db.session.commit()

    body = client.get("/admin/private-requests/PRT-907001").get_data(as_text=True)
    assert "45,000.00 EGP" in body      # agreed price
    assert "46,500.00 EGP" in body      # price plus the fee
    assert "15,000.00 EGP" in body      # received, and the revenue recognized
    assert "31,500.00 EGP" in body      # still outstanding
    assert "Deposit Paid" in body
    assert "PTP-000001" in body


# ===========================================================================
# Additional fees
# ===========================================================================


def test_a_booking_fee_increases_what_the_booking_earns(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.models.trip import Trip
        from app.services.additional_fees import add_fee, fees_for_booking
        from services.crm.system_services.revenue_rules import booking_revenue_breakdown

        _seed_traveler(db)
        booking = _seed_booking(db)
        trip = db.session.get(Trip, "TRIP-PM-1")

        before = booking_revenue_breakdown(booking, trip, [])
        assert before.gross == 2000.0  # 2 travellers x $1,000

        add_fee(booking=booking, label="Visa processing", amount="150", currency="USD", category="visa")
        db.session.commit()

        after = booking_revenue_breakdown(booking, trip, fees_for_booking(booking.booking_id))
        assert after.gross == 2150.0
        assert after.fees == 150.0
        assert after.trip_value == 2000.0
        assert after.net == 2150.0


def test_a_fee_in_the_wrong_currency_is_refused(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.services.additional_fees import add_fee

        _seed_traveler(db)
        booking = _seed_booking(db, currency="USD")
        with pytest.raises(ValueError) as excinfo:
            add_fee(booking=booking, label="Visa", amount="150", currency="EGP")
        assert "never converts" in str(excinfo.value)


def test_a_fee_cannot_be_added_to_a_booking_with_no_currency(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.services.additional_fees import add_fee

        _seed_traveler(db)
        booking = _seed_booking(db, booking_id="BK-PM-NOCUR", currency=None)
        with pytest.raises(ValueError) as excinfo:
            add_fee(booking=booking, label="Visa", amount="150", currency="USD")
        assert "before adding fees" in str(excinfo.value)


def test_a_voided_fee_stops_counting_but_stays_on_the_record(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.models.additional_fee import AdditionalFee
        from app.models.trip import Trip
        from app.services.additional_fees import add_fee, fees_for_booking, void_fee
        from services.crm.system_services.revenue_rules import booking_revenue_breakdown

        _seed_traveler(db)
        booking = _seed_booking(db)
        trip = db.session.get(Trip, "TRIP-PM-1")
        fee = add_fee(booking=booking, label="Insurance", amount="200", currency="USD")
        db.session.commit()
        assert booking_revenue_breakdown(booking, trip, fees_for_booking(booking.booking_id)).gross == 2200.0

        void_fee(fee, reason="Customer declined")
        db.session.commit()

        assert booking_revenue_breakdown(booking, trip, fees_for_booking(booking.booking_id)).gross == 2000.0
        # The row survives, with the reason.
        stored = db.session.get(AdditionalFee, fee.fee_id)
        assert stored is not None
        assert stored.is_active is False
        assert stored.void_reason == "Customer declined"


def test_voiding_a_fee_requires_a_reason(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.services.additional_fees import add_fee, void_fee

        _seed_traveler(db)
        booking = _seed_booking(db)
        fee = add_fee(booking=booking, label="Insurance", amount="200", currency="USD")
        db.session.commit()
        with pytest.raises(ValueError):
            void_fee(fee, reason="   ")


def test_a_private_fee_raises_the_total_owed_but_not_the_revenue(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.services.additional_fees import add_fee
        from app.services.private_trip_money import money_for, record_payment

        _seed_traveler(db)
        item = _seed_request(db, price=45000.0, currency="EGP")
        record_payment(item, amount=45000, currency="EGP", occurred_on="2026-08-01")
        db.session.commit()
        assert money_for(item).payment_status == "Fully Paid"

        add_fee(private_request=item, label="Airport transfer", amount="1500", currency="EGP", category="transfer")
        db.session.commit()

        money = money_for(item)
        assert money.fees == 1500.0
        assert money.contract_value == 46500.0
        assert money.outstanding == 1500.0
        # The fee is owed, not received -- revenue is unchanged.
        assert money.net_revenue == 45000.0
        assert money.payment_status == "Deposit Paid"


def test_a_fee_only_counts_as_revenue_once_the_booking_does(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.models.trip import Trip
        from app.services.additional_fees import add_fee, fees_for_booking
        from services.crm.system_services.revenue_rules import booking_revenue_breakdown

        _seed_traveler(db)
        booking = _seed_booking(
            db, booking_id="BK-PM-DRAFT", booking_status="Draft", payment_status="Pending"
        )
        trip = db.session.get(Trip, "TRIP-PM-1")
        add_fee(booking=booking, label="Visa", amount="150", currency="USD")
        db.session.commit()

        assert booking_revenue_breakdown(booking, trip, fees_for_booking("BK-PM-DRAFT")) is None


def test_a_booking_fee_can_be_added_and_removed_through_the_page(money_app) -> None:
    app, db = money_app
    client = app.test_client()
    token = _login(app, client)
    with app.app_context():
        _seed_traveler(db)
        _seed_booking(db, booking_id="BK-PM-UI")

    added = client.post(
        "/bookings/BK-PM-UI/fees",
        data={"csrf_token": token, "label": "Airport transfer", "amount": "75", "currency": "USD", "category": "transfer"},
        follow_redirects=True,
    )
    assert added.status_code == 200
    body = added.get_data(as_text=True)
    assert "Airport transfer" in body
    assert "2,075.00 USD" in body  # trip value 2,000 + 75 fee

    with app.app_context():
        from app.models.additional_fee import AdditionalFee

        fee = AdditionalFee.query.filter_by(booking_id="BK-PM-UI").one()
        fee_id = fee.fee_id

    removed = client.post(
        f"/bookings/BK-PM-UI/fees/{fee_id}/void",
        data={"csrf_token": token, "reason": "Not needed"},
        follow_redirects=True,
    )
    assert removed.status_code == 200
    with app.app_context():
        from app.models.additional_fee import AdditionalFee

        assert db.session.get(AdditionalFee, fee_id).is_active is False


# ===========================================================================
# Traveler profile
# ===========================================================================


def test_the_private_trip_counter_counts_committed_trips_only(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.models.traveler import Traveler
        from app.services.private_trip_money import record_payment
        from app.services.traveler_stats import recalculate_traveler_stats

        _seed_traveler(db)
        # Committed by stage.
        _seed_request(db, request_id="PRT-901001", stage="designing")
        # Committed by money, even at an early stage.
        early = _seed_request(db, request_id="PRT-901002", stage="registered")
        record_payment(early, amount=1000, currency="EGP", occurred_on="2026-08-01")
        db.session.commit()
        # Neither: an open request with no money.
        _seed_request(db, request_id="PRT-901003", stage="consultation_scheduled")
        # Never: the customer walked away.
        _seed_request(db, request_id="PRT-901004", stage="lost")

        recalculate_traveler_stats("TR-PM-1")
        assert db.session.get(Traveler, "TR-PM-1").private_trips_count == 2


def test_the_profile_shows_the_private_trip_count_and_its_money(money_app) -> None:
    app, db = money_app
    client = app.test_client()
    _login(app, client)
    with app.app_context():
        from app.services.private_trip_money import record_payment
        from app.services.traveler_stats import recalculate_traveler_stats

        _seed_traveler(db)
        item = _seed_request(db, request_id="PRT-902001", stage="designing", price=45000.0, currency="EGP")
        record_payment(item, amount=15000, currency="EGP", occurred_on="2026-08-01")
        db.session.commit()
        recalculate_traveler_stats("TR-PM-1")

    body = client.get("/travelers/TR-PM-1").get_data(as_text=True)
    assert "Private Trips" in body
    assert "PRT-902001" in body
    # Received, and the balance still owed -- the agreed price is not revenue.
    assert "15,000.00 EGP" in body
    assert "30,000.00 EGP" in body


def test_private_money_reaches_the_profile_revenue_hero(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.routes.travelers import _attach_booking_revenue_totals
        from app.models.traveler import Traveler
        from app.services.private_trip_money import record_payment

        traveler = _seed_traveler(db)
        item = _seed_request(db, price=45000.0, currency="EGP")
        record_payment(item, amount=20000, currency="EGP", occurred_on="2026-08-01")
        db.session.commit()

        totals = _attach_booking_revenue_totals([db.session.get(Traveler, traveler.traveler_id)])
        assert totals["TR-PM-1"]["EGP"] == 20000.0
        assert totals["TR-PM-1"]["USD"] == 0.0


# ===========================================================================
# Revenue Analytics
# ===========================================================================


def test_private_payments_are_attributed_to_the_month_the_money_arrived(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.services.private_trip_money import record_payment
        from app.services.revenue_analytics import build_revenue_dashboard

        _seed_traveler(db)
        item = _seed_request(db, price=45000.0, currency="EGP")
        # `today` is passed so the September entry is not judged against the
        # real clock -- the point being tested is which month the money lands
        # in, not what today's date happens to be.
        record_payment(item, amount=15000, currency="EGP", occurred_on="2026-08-05")
        record_payment(
            item, amount=10000, currency="EGP", occurred_on="2026-09-09", today=date(2026, 9, 20)
        )
        db.session.commit()

        dashboard = build_revenue_dashboard(
            selected_year=2026, now=datetime(2026, 9, 20, tzinfo=timezone.utc)
        )
        by_month = {row["month"]: row for row in dashboard["monthly_rows"]}
        assert by_month[8]["EGP"] == 15000.0
        assert by_month[9]["EGP"] == 10000.0
        assert dashboard["total"]["EGP"] == 25000.0
        assert dashboard["total"]["USD"] == 0.0


def test_the_dashboard_separates_bookings_from_private_trips_and_currencies(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.services.private_trip_money import record_payment
        from app.services.revenue_analytics import build_revenue_dashboard

        _seed_traveler(db)
        _seed_booking(db)  # $2,000, Confirmed + Fully Paid
        item = _seed_request(db, price=45000.0, currency="EGP")
        record_payment(item, amount=15000, currency="EGP", occurred_on="2026-08-05")
        db.session.commit()

        dashboard = build_revenue_dashboard(
            selected_year=2026, now=datetime(2026, 9, 20, tzinfo=timezone.utc)
        )
        sources = dict(dashboard["source_rows"])
        assert sources["Trip bookings"]["USD"] == 2000.0
        assert sources["Trip bookings"]["EGP"] == 0.0
        assert sources["Private trips"]["EGP"] == 15000.0
        assert sources["Private trips"]["USD"] == 0.0
        # And the two currencies are never blended into one figure.
        assert dashboard["total"]["USD"] == 2000.0
        assert dashboard["total"]["EGP"] == 15000.0


def test_the_agreed_price_appears_only_as_money_still_to_collect(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.services.private_trip_money import record_payment
        from app.services.revenue_analytics import build_revenue_dashboard

        _seed_traveler(db)
        item = _seed_request(db, price=45000.0, currency="EGP")
        record_payment(item, amount=15000, currency="EGP", occurred_on="2026-08-05")
        db.session.commit()

        dashboard = build_revenue_dashboard(
            selected_year=2026, now=datetime(2026, 9, 20, tzinfo=timezone.utc)
        )
        pipeline = dashboard["pipeline"]
        assert pipeline["agreed"]["EGP"] == 45000.0
        assert pipeline["collected"]["EGP"] == 15000.0
        assert pipeline["outstanding"]["EGP"] == 30000.0
        # Never revenue.
        assert dashboard["total"]["EGP"] == 15000.0


def test_money_received_with_no_price_is_flagged_for_attention(money_app) -> None:
    app, db = money_app
    with app.app_context():
        from app.services.private_trip_money import record_payment
        from app.services.revenue_analytics import build_revenue_dashboard

        _seed_traveler(db)
        item = _seed_request(db, request_id="PRT-903001")
        record_payment(item, amount=5000, currency="EGP", occurred_on="2026-08-05")
        db.session.commit()

        dashboard = build_revenue_dashboard(
            selected_year=2026, now=datetime(2026, 9, 20, tzinfo=timezone.utc)
        )
        reasons = " ".join(row["reason"] for row in dashboard["needs_attention"])
        assert "PRT-903001" in [row["record_id"] for row in dashboard["needs_attention"]]
        assert "no agreed price" in reasons
        # Still counted as revenue: the money did arrive.
        assert dashboard["total"]["EGP"] == 5000.0


def test_a_legacy_converted_request_is_not_counted_twice(money_app) -> None:
    """Its money lives on the booking it produced, so its own ledger is
    excluded -- otherwise the same payment would be revenue in two places."""
    app, db = money_app
    with app.app_context():
        from app.services.private_trip_money import record_payment
        from app.services.revenue_analytics import build_revenue_dashboard

        _seed_traveler(db)
        _seed_booking(db, booking_id="BK-PM-LEGACY")
        item = _seed_request(db, request_id="PRT-904001", price=45000.0, currency="EGP")
        record_payment(item, amount=15000, currency="EGP", occurred_on="2026-08-05")
        db.session.commit()
        item.converted_booking_id = "BK-PM-LEGACY"
        db.session.commit()

        dashboard = build_revenue_dashboard(
            selected_year=2026, now=datetime(2026, 9, 20, tzinfo=timezone.utc)
        )
        assert dashboard["total"]["EGP"] == 0.0
        assert dashboard["total"]["USD"] == 2000.0


def test_every_payment_and_refund_is_listed_on_the_revenue_page(money_app) -> None:
    app, db = money_app
    client = app.test_client()
    with app.app_context():
        from app.models.booking_transaction import BookingTransaction, ENTRY_PAYMENT
        from app.services.private_trip_money import record_payment, record_refund

        _seed_traveler(db)
        _seed_booking(db)
        db.session.add(BookingTransaction(
            booking_id="BK-PM-1",
            traveler_id="TR-PM-1",
            entry_type=ENTRY_PAYMENT,
            amount=2000.0,
            currency="USD",
            occurred_on=date(2026, 4, 2),
        ))
        item = _seed_request(db, request_id="PRT-905001", price=45000.0, currency="EGP")
        record_payment(item, amount=15000, currency="EGP", occurred_on="2026-08-05")
        db.session.commit()
        record_refund(item, amount=2000, reason="Adjustment", occurred_on="2026-08-20")
        db.session.commit()

    body = client.get("/admin/revenue-analytics?year=2026").get_data(as_text=True)
    assert "Payments and refunds" in body
    assert "PRT-905001" in body
    assert "BK-PM-1" in body
    assert "PTP-000001" in body  # the private payment
    assert "PTR-000002" in body  # the private refund
    assert "Payment" in body and "Refund" in body


def test_a_month_that_nets_below_zero_is_shown_as_negative(money_app) -> None:
    """A refund recorded in a later month than the payment behind it makes that
    month net negative. Printing that as a positive figure would misstate the
    month's money, so both the data and the page have to carry the sign."""
    app, db = money_app
    client = app.test_client()
    with app.app_context():
        from app.services.private_trip_money import record_payment, record_refund
        from app.services.revenue_analytics import build_revenue_dashboard

        _seed_traveler(db)
        item = _seed_request(db, price=45000.0, currency="EGP")
        record_payment(item, amount=5000, currency="EGP", occurred_on="2026-07-05")
        db.session.commit()
        record_refund(item, amount=2000, reason="Partial cancellation", occurred_on="2026-08-10")
        db.session.commit()

        dashboard = build_revenue_dashboard(
            selected_year=2026, now=datetime(2026, 8, 21, tzinfo=timezone.utc)
        )
        by_month = {row["month"]: row for row in dashboard["monthly_rows"]}
        assert by_month[7]["EGP"] == 5000.0
        assert by_month[8]["EGP"] == -2000.0
        # The year as a whole is still positive.
        assert dashboard["total"]["EGP"] == 3000.0

    body = client.get("/admin/revenue-analytics?year=2026").get_data(as_text=True)
    assert "-2,000.00 EGP" in body


def test_the_revenue_page_renders_with_no_data_at_all(money_app) -> None:
    app, db = money_app
    client = app.test_client()
    response = client.get("/admin/revenue-analytics")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Total Net Revenue (USD)" in body
    assert "No recognized revenue yet." in body


def test_the_revenue_page_renders_the_full_dashboard(money_app) -> None:
    app, db = money_app
    client = app.test_client()
    with app.app_context():
        from app.services.additional_fees import add_fee
        from app.services.private_trip_money import record_payment

        _seed_traveler(db)
        booking = _seed_booking(db)
        add_fee(booking=booking, label="Visa", amount="150", currency="USD", category="visa")
        item = _seed_request(db, request_id="PRT-906001", price=45000.0, currency="EGP")
        record_payment(item, amount=15000, currency="EGP", occurred_on="2026-08-05")
        db.session.commit()

    body = client.get("/admin/revenue-analytics?year=2026").get_data(as_text=True)
    assert "Monthly trend" in body
    assert "Where the money comes from" in body
    assert "Private trip collections" in body
    assert "Private — International" in body
    assert "additional fees" in body
    # The rule is stated on the page, not just in the code.
    assert "Agreed price is <strong>not</strong> revenue" in body


# ===========================================================================
# The derived payment status
# ===========================================================================


@pytest.mark.parametrize(
    "contract,paid,refunded,expected",
    [
        (1000.0, 0.0, 0.0, "Pending"),
        (1000.0, 400.0, 0.0, "Deposit Paid"),
        (1000.0, 1000.0, 0.0, "Fully Paid"),
        (1000.0, 1000.005, 0.0, "Fully Paid"),
        (1000.0, 1200.0, 0.0, "Fully Paid"),
        (1000.0, 1000.0, 300.0, "Partial Refund"),
        (1000.0, 1000.0, 1000.0, "Full Refund"),
        (None, 500.0, 0.0, "Deposit Paid"),
        (None, 0.0, 0.0, "Pending"),
        (0.0, 500.0, 0.0, "Deposit Paid"),
    ],
)
def test_the_payment_status_is_derived_from_recorded_money(contract, paid, refunded, expected) -> None:
    from services.crm.system_services.payment_rules import derive_payment_status

    assert derive_payment_status(contract, paid, refunded) == expected


def test_the_retired_converted_stage_is_still_readable_but_unreachable() -> None:
    """Existing rows must keep loading. Nothing can move into it."""
    from services.crm.system_services.private_trips import (
        PRIVATE_REQUEST_STAGES,
        PRIVATE_REQUEST_TRANSITIONS,
        can_transition_private_stage,
        normalize_private_stage,
    )

    assert "converted" not in PRIVATE_REQUEST_STAGES
    assert normalize_private_stage("converted") == "converted"
    assert can_transition_private_stage("paid", "converted") is False
    assert can_transition_private_stage("converted", "lost") is False
    for stage, targets in PRIVATE_REQUEST_TRANSITIONS.items():
        assert "converted" not in targets, stage
