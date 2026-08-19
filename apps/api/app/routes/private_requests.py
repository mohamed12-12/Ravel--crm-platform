from __future__ import annotations

import math
from datetime import date, datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.extensions import db
from app.models import BookingTransaction, PrivateTripRequest, Traveler, Trip, TripBooking
from app.models.booking_transaction import ENTRY_PAYMENT, SOURCE_PRIVATE_TRIP_DEPOSIT
from app.security import current_user_id, employee_session_required
from services.crm.system_services.private_trips import (
    PRIVATE_BUDGET_CURRENCIES,
    PRIVATE_REQUEST_STAGES,
    PRIVATE_SERVICE_TYPES,
    PRIVATE_TRIP_SCOPES,
    can_transition_private_stage,
    design_due_from,
    normalize_private_scope,
    normalize_private_service_type,
    normalize_private_stage,
    utc_now,
)
from services.crm.system_services.trip_pricing import serialize_room_prices


private_requests_bp = Blueprint("private_requests", __name__, url_prefix="/admin/private-requests")


def _parse_date(value: str | None) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _parse_float(value: str | None) -> float | None:
    raw = str(value or "").strip().replace(",", "")
    if not raw:
        return None
    try:
        amount = float(raw)
    except ValueError:
        return None
    return amount if math.isfinite(amount) and amount > 0 else None


def _next_prefixed_id(model, column_name: str, prefix: str, width: int) -> str:
    column = getattr(model, column_name)
    rows = db.session.query(column).filter(column.like(f"{prefix}%")).all()
    max_number = 0
    for (value,) in rows:
        suffix = str(value or "").strip()[len(prefix):]
        if suffix.isdigit():
            max_number = max(max_number, int(suffix))
    return f"{prefix}{max_number + 1:0{width}d}"


def _request_query():
    return PrivateTripRequest.query.order_by(PrivateTripRequest.created_at.desc(), PrivateTripRequest.request_id.desc())


@private_requests_bp.route("/")
@employee_session_required
def index():
    selected_stage = normalize_private_stage(request.args.get("stage")) or ""
    query = _request_query()
    if selected_stage:
        query = query.filter(PrivateTripRequest.stage == selected_stage)
    requests = query.all()
    columns = {stage: [] for stage in PRIVATE_REQUEST_STAGES}
    for item in requests:
        columns.setdefault(item.stage or "registered", []).append(item)
    travelers = Traveler.query.order_by(Traveler.full_name.asc()).limit(300).all()
    return render_template(
        "admin/private_requests.html",
        requests=requests,
        columns=columns,
        stages=PRIVATE_REQUEST_STAGES,
        selected_stage=selected_stage,
        service_types=PRIVATE_SERVICE_TYPES,
        scopes=PRIVATE_TRIP_SCOPES,
        currencies=PRIVATE_BUDGET_CURRENCIES,
        travelers=travelers,
    )


@private_requests_bp.route("/", methods=["POST"])
@employee_session_required
def create():
    service_type = normalize_private_service_type(request.form.get("service_type"))
    trip_scope = normalize_private_scope(request.form.get("trip_scope"))
    destination = str(request.form.get("destination") or "").strip()
    party_size = int(_parse_float(request.form.get("party_size")) or 1)
    if not service_type or not trip_scope or not destination:
        flash("Service type, trip scope, and destination are required.", "error")
        return redirect(url_for("private_requests.index"))
    now = utc_now().replace(tzinfo=None)
    item = PrivateTripRequest(
        request_id=_next_prefixed_id(PrivateTripRequest, "request_id", "PRT-", 6),
        traveler_id=str(request.form.get("traveler_id") or "").strip() or None,
        lead_id=str(request.form.get("lead_id") or "").strip() or None,
        service_type=service_type,
        trip_scope=trip_scope,
        destination=destination,
        start_date_pref=_parse_date(request.form.get("start_date_pref")),
        end_date_pref=_parse_date(request.form.get("end_date_pref")),
        dates_flexible=bool(request.form.get("dates_flexible")),
        party_size=max(party_size, 1),
        boys_count=int(_parse_float(request.form.get("boys_count")) or 0),
        girls_count=int(_parse_float(request.form.get("girls_count")) or 0),
        budget_amount=_parse_float(request.form.get("budget_amount")),
        budget_currency=str(request.form.get("budget_currency") or "").strip().upper() or None,
        notes=str(request.form.get("notes") or "").strip() or None,
        stage="registered",
        stage_changed_at=now,
        created_at=now,
        created_by=current_user_id(),
    )
    db.session.add(item)
    db.session.commit()
    flash(f"Private request {item.request_id} created.", "success")
    return redirect(url_for("private_requests.detail", request_id=item.request_id))


@private_requests_bp.route("/<string:request_id>")
@employee_session_required
def detail(request_id: str):
    item = db.get_or_404(PrivateTripRequest, request_id)
    return render_template(
        "admin/private_request_detail.html",
        item=item,
        stages=PRIVATE_REQUEST_STAGES,
        service_types=PRIVATE_SERVICE_TYPES,
        scopes=PRIVATE_TRIP_SCOPES,
        currencies=PRIVATE_BUDGET_CURRENCIES,
    )


@private_requests_bp.route("/<string:request_id>/stage", methods=["POST"])
@employee_session_required
def update_stage(request_id: str):
    item = db.get_or_404(PrivateTripRequest, request_id)
    target = normalize_private_stage(request.form.get("stage"))
    if not can_transition_private_stage(item.stage, target):
        flash(f"Cannot move {item.request_id} from {item.stage} to {target or 'unknown'}.", "error")
        return redirect(url_for("private_requests.detail", request_id=item.request_id))
    now = utc_now().replace(tzinfo=None)
    item.mark_stage(target, when=now)
    if target == "deposit_paid":
        amount = _parse_float(request.form.get("deposit_amount"))
        currency = str(request.form.get("deposit_currency") or item.budget_currency or "").strip().upper()
        if amount:
            item.deposit_amount = amount
            item.deposit_currency = currency if currency in PRIVATE_BUDGET_CURRENCIES else item.budget_currency
            item.deposit_paid_at = now
            item.deposit_is_refundable = False
            item.design_due_at = design_due_from(now).replace(tzinfo=None)
    if target == "lost":
        item.lost_reason = str(request.form.get("lost_reason") or "").strip() or item.lost_reason
    db.session.commit()
    flash(f"Private request {item.request_id} moved to {target}.", "success")
    return redirect(url_for("private_requests.detail", request_id=item.request_id))


@private_requests_bp.route("/<string:request_id>/convert", methods=["POST"])
@employee_session_required
def convert(request_id: str):
    item = db.get_or_404(PrivateTripRequest, request_id)
    if item.converted_booking_id:
        flash("This private request is already converted.", "info")
        return redirect(url_for("bookings.detail", booking_id=item.converted_booking_id))
    traveler = db.session.get(Traveler, item.traveler_id) if item.traveler_id else None
    if traveler is None:
        flash("Link a traveler before converting this private request.", "error")
        return redirect(url_for("private_requests.detail", request_id=item.request_id))
    room_type = str(request.form.get("room_type") or "Double").strip().title()
    if room_type not in {"Single", "Double", "Triple"}:
        room_type = "Double"
    currency = str(request.form.get("currency") or item.deposit_currency or item.budget_currency or "EGP").strip().upper()
    if currency not in PRIVATE_BUDGET_CURRENCIES:
        currency = "EGP"
    total_price = _parse_float(request.form.get("total_price")) or item.budget_amount or item.deposit_amount or 0
    unit_price = round(total_price / max(int(item.party_size or 1), 1), 2) if total_price else 0
    now = utc_now().replace(tzinfo=None)
    trip = Trip(
        trip_id=_next_prefixed_id(Trip, "trip_id", "RT-PRV-", 6),
        trip_name=str(request.form.get("trip_name") or f"Private {item.destination}").strip(),
        trip_name_ar=str(request.form.get("trip_name_ar") or f"Private {item.destination}").strip(),
        type=item.trip_scope,
        year=now.year,
        start_date=item.start_date_pref,
        end_date=item.end_date_pref,
        sales_status="Closed",
        is_private=True,
        single_total=1 if room_type == "Single" else 0,
        double_total=1 if room_type == "Double" else 0,
        triple_total=1 if room_type == "Triple" else 0,
        public_price=f"{total_price:,.2f} {currency}" if total_price else "",
        room_prices_json=serialize_room_prices({room_type: {currency: str(unit_price or total_price)}}),
        public_description=f"Private/custom trip created from {item.request_id}.",
        sales_notes=f"Private request {item.request_id}; service={item.service_type}; deposit non-refundable.",
    )
    booking = TripBooking(
        booking_id=_next_prefixed_id(TripBooking, "booking_id", "BK", 6),
        trip_id=trip.trip_id,
        trip_name=trip.trip_name,
        traveler_id=traveler.traveler_id,
        traveler_name=traveler.full_name,
        room_type=room_type,
        room_group="mixed" if item.boys_count and item.girls_count else None,
        currency=currency,
        group_size=max(int(item.party_size or 1), 1),
        boys_count=int(item.boys_count or 0),
        girls_count=int(item.girls_count or 0),
        booking_status="Payment Pending",
        payment_status="Deposit Paid" if item.deposit_amount else "Pending",
        booking_source="Private Request",
        lead_id=item.lead_id,
        booking_notes=f"Converted from private request {item.request_id}. Deposit is non-refundable.",
        missing_info=False,
    )
    db.session.add(trip)
    db.session.add(booking)
    db.session.flush()
    if item.deposit_amount and item.deposit_currency:
        db.session.add(BookingTransaction(
            booking_id=booking.booking_id,
            traveler_id=traveler.traveler_id,
            entry_type=ENTRY_PAYMENT,
            amount=float(item.deposit_amount),
            currency=str(item.deposit_currency).upper(),
            occurred_on=(item.deposit_paid_at or now).date()
            if isinstance(item.deposit_paid_at or now, datetime)
            else now.date(),
            method="private_deposit",
            notes=f"Non-refundable private trip deposit from {item.request_id}.",
            source=SOURCE_PRIVATE_TRIP_DEPOSIT,
            is_non_refundable=not bool(item.deposit_is_refundable),
        ))
    item.converted_booking_id = booking.booking_id
    item.mark_stage("converted", when=now)
    db.session.commit()
    flash(f"Private request {item.request_id} converted to booking {booking.booking_id}.", "success")
    return redirect(url_for("bookings.detail", booking_id=booking.booking_id))
