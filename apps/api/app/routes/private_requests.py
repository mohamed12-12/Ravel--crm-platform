from __future__ import annotations

import math
from datetime import date, datetime, timezone

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import or_

from app.extensions import db
from app.models import BookingTransaction, Lead, PrivateTripRequest, Traveler, Trip, TripBooking
from app.models.booking_transaction import ENTRY_PAYMENT, SOURCE_PRIVATE_TRIP_DEPOSIT
from app.security import current_user, current_user_id, employee_session_required, has_permission
from app.services.assignments import active_assignees, apply_assignment, assignment_history, resolve_user_id
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
    return render_template(
        "admin/private_requests.html",
        requests=requests,
        columns=columns,
        stages=PRIVATE_REQUEST_STAGES,
        selected_stage=selected_stage,
        service_types=PRIVATE_SERVICE_TYPES,
        scopes=PRIVATE_TRIP_SCOPES,
        currencies=PRIVATE_BUDGET_CURRENCIES,
    )


# A plain <select> of every traveler (previously capped at 300, alphabetical)
# silently made every traveler past that cap impossible to link from this
# form at all. These two endpoints back a type-to-search picker instead --
# same @employee_session_required gate as every other route on this
# blueprint, since they expose traveler/lead names and phone numbers.
_PICKER_RESULT_LIMIT = 20


@private_requests_bp.route("/search/travelers")
@employee_session_required
def search_travelers():
    term = str(request.args.get("q") or "").strip()
    if len(term) < 2:
        return jsonify({"results": []})
    like = f"%{term}%"
    matches = (
        Traveler.query.filter(
            or_(
                Traveler.full_name.ilike(like),
                Traveler.traveler_id.ilike(like),
                Traveler.whatsapp_raw.ilike(like),
                Traveler.normalized_whatsapp.ilike(like),
            )
        )
        .order_by(Traveler.full_name.asc())
        .limit(_PICKER_RESULT_LIMIT)
        .all()
    )
    return jsonify(
        {
            "results": [
                {
                    "id": traveler.traveler_id,
                    "label": f"{traveler.full_name or traveler.traveler_id} — {traveler.traveler_id}",
                }
                for traveler in matches
            ]
        }
    )


@private_requests_bp.route("/search/leads")
@employee_session_required
def search_leads():
    term = str(request.args.get("q") or "").strip()
    if len(term) < 2:
        return jsonify({"results": []})
    like = f"%{term}%"
    matches = (
        Lead.query.filter(
            or_(
                Lead.customer_name.ilike(like),
                Lead.lead_id.ilike(like),
                Lead.raw_phone.ilike(like),
            )
        )
        .order_by(Lead.created_at.desc())
        .limit(_PICKER_RESULT_LIMIT)
        .all()
    )
    return jsonify(
        {
            "results": [
                {
                    "id": lead.lead_id,
                    "label": f"{lead.customer_name or lead.lead_id} — {lead.lead_id}",
                }
                for lead in matches
            ]
        }
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
        employees=active_assignees(),
        can_assign=has_permission("assign_work"),
        assigned_user=item.assigned_user,
        assigned_history=assignment_history("private_trip_request", item.request_id),
    )


@private_requests_bp.route("/<string:request_id>/assign", methods=["POST"])
@employee_session_required
def assign(request_id: str):
    if not has_permission("assign_work"):
        abort(403)
    item = db.get_or_404(PrivateTripRequest, request_id)
    try:
        new_user_id = resolve_user_id(request.form.get("assigned_to_user_id"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("private_requests.detail", request_id=item.request_id))
    changed = apply_assignment(
        item,
        resource_type="private_trip_request",
        resource_id=item.request_id,
        new_user_id=new_user_id,
        actor=current_user(),
        reason=str(request.form.get("assignment_reason") or "").strip(),
    )
    if changed:
        db.session.commit()
        flash(f"{item.request_id} assignment updated.", "success")
    return redirect(url_for("private_requests.detail", request_id=item.request_id))


@private_requests_bp.route("/<string:request_id>/followup", methods=["POST"])
@employee_session_required
def update_followup(request_id: str):
    item = db.get_or_404(PrivateTripRequest, request_id)
    priority = str(request.form.get("priority") or "").strip()
    if priority in {"High", "Medium", "Low"}:
        item.priority = priority
    item.follow_up_due_date = _parse_date(request.form.get("follow_up_due_date")) or item.follow_up_due_date
    item.channel = str(request.form.get("channel") or item.channel or "").strip() or item.channel
    db.session.commit()
    flash(f"{item.request_id} follow-up details updated.", "success")
    return redirect(url_for("private_requests.detail", request_id=item.request_id))


@private_requests_bp.route("/<string:request_id>/quick-action", methods=["POST"])
@employee_session_required
def quick_action(request_id: str):
    item = db.get_or_404(PrivateTripRequest, request_id)
    data = request.get_json(silent=True) or request.form.to_dict()
    action = str(data.get("action") or "").strip()
    now = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)

    # Deliberately does not touch `stage` -- that's the consultation/deposit/
    # design pipeline milestone (moved via update_stage, its own state
    # machine), while these track whether an employee is actively on top of
    # the request, same distinction as leads.py's quick_action but without
    # reusing its lead-stage-transition semantics, which do not apply here.
    action_map = {
        "mark_contacted": {
            "follow_up_status": "Contacted",
            "current_step": "Follow up on private trip details",
            "customer_response_status": "Contacted",
            "last_contact_at": now,
        },
        "waiting_customer": {
            "follow_up_status": "Awaiting customer reply",
            "current_step": "Wait for customer response",
            "customer_response_status": "Waiting Customer",
        },
    }
    if action not in action_map:
        return jsonify({"error": "Invalid quick action"}), 400

    patch = action_map[action]
    item.follow_up_status = patch.get("follow_up_status", item.follow_up_status)
    item.current_step = patch.get("current_step", item.current_step)
    item.customer_response_status = patch.get("customer_response_status", item.customer_response_status)
    item.last_contact_at = patch.get("last_contact_at", item.last_contact_at)
    if data.get("follow_up_due_date"):
        item.follow_up_due_date = _parse_date(data.get("follow_up_due_date"))
    db.session.commit()
    return jsonify({"status": "ok"})


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
    # PRIVATE_REQUEST_TRANSITIONS already encodes "paid" as the only stage that
    # may move to "converted" -- but unlike update_stage(), this route never
    # checked it, so an employee could convert straight from "registered" and
    # skip the whole consultation/deposit/design pipeline the stages exist to
    # enforce (and, with no deposit recorded yet, create a booking with no
    # payment evidence at all).
    if not can_transition_private_stage(item.stage, "converted"):
        flash(
            f"{item.request_id} must reach the 'paid' stage before it can be converted "
            f"(currently '{item.stage}').",
            "error",
        )
        return redirect(url_for("private_requests.detail", request_id=item.request_id))
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
