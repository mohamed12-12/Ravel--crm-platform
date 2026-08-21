from __future__ import annotations

import logging
import math
from datetime import date, datetime, timezone

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import or_

from app.extensions import db
from app.models import AdditionalFee, Lead, PrivateTripRequest, PrivateTripTransaction, Traveler
from app.models.private_trip_transaction import SOURCE_PRIVATE_DEPOSIT_STAGE
from app.security import current_actor, current_user, current_user_id, employee_session_required, has_permission
from app.services.additional_fees import add_fee, fees_for_private_request, void_fee
from app.services.assignments import (
    active_assignees,
    apply_assignment,
    assignment_history,
    auto_assign_private_request,
    resolve_user_id,
)
from app.services.private_trip_ledger import all_transactions, ledger_totals
from app.services.private_trip_money import (
    PAYMENT_METHOD_LABELS,
    method_label,
    money_for,
    money_for_many,
    record_payment,
    record_refund,
    refund_ceiling,
    reverse_entry,
    set_agreed_price,
)
from app.services.traveler_stats import recalculate_traveler_stats
from services.crm.system_services.fee_rules import ADDITIONAL_FEE_CATEGORIES
from services.crm.system_services.private_trips import (
    PRIVATE_BUDGET_CURRENCIES,
    PRIVATE_REQUEST_STAGES,
    PRIVATE_REQUEST_TRANSITIONS,
    PRIVATE_SERVICE_TYPES,
    PRIVATE_TRIP_SCOPES,
    can_transition_private_stage,
    design_due_from,
    normalize_private_scope,
    normalize_private_service_type,
    normalize_private_stage,
    utc_now,
)

logger = logging.getLogger(__name__)

private_requests_bp = Blueprint("private_requests", __name__, url_prefix="/admin/private-requests")


def _can_move_money() -> bool:
    """Whether this employee may give money back or undo a recorded entry.

    Recording a payment is ordinary sales work, available to any employee with
    CRM write access. Refunds and reversals move money the other way, so they
    take the same permission the booking pages already require for a refund
    (`change_payment_status`: admin and manager only).
    """
    if not current_app.config.get("CRM_AUTH_ENABLED", False):
        return True
    return has_permission("change_payment_status")


def _refresh_traveler_stats(traveler_id: str | None) -> None:
    """Keep the traveler profile's private-trip count and revenue in step.

    Best-effort, exactly as the booking routes treat it: a counter that is one
    request stale is a display problem, while letting it abort the money write
    that has just been committed would be a real one.
    """
    if not traveler_id:
        return
    try:
        recalculate_traveler_stats(traveler_id)
    except Exception:
        logger.error("Traveler stats recalculation failed traveler_id=%s", traveler_id, exc_info=True)
        db.session.rollback()


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
    # Retired stages ("converted") are not offered as columns, but rows that
    # are still sitting in one must not vanish from the board -- they are only
    # rendered while any request actually holds them.
    stages = list(PRIVATE_REQUEST_STAGES) + [
        stage for stage in columns if stage not in PRIVATE_REQUEST_STAGES and columns[stage]
    ]
    return render_template(
        "admin/private_requests.html",
        requests=requests,
        columns=columns,
        stages=stages,
        money=money_for_many(requests),
        selected_stage=selected_stage,
        service_types=PRIVATE_SERVICE_TYPES,
        scopes=PRIVATE_TRIP_SCOPES,
        currencies=PRIVATE_BUDGET_CURRENCIES,
        employees=active_assignees(),
        can_assign=has_permission("assign_work"),
        today=date.today(),
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
    requested_owner = str(request.form.get("assigned_to_user_id") or "").strip()
    if requested_owner and not has_permission("assign_work"):
        flash("You do not have permission to assign private requests.", "error")
        return redirect(url_for("private_requests.index"))
    priority = str(request.form.get("priority") or "").strip()
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
        priority=priority if priority in {"High", "Medium", "Low"} else "Medium",
        channel=str(request.form.get("channel") or "").strip() or None,
        follow_up_due_date=_parse_date(request.form.get("follow_up_due_date")),
        stage="registered",
        stage_changed_at=now,
        created_at=now,
        created_by=current_user_id(),
    )
    db.session.add(item)
    # Ownership from the moment it exists, exactly like a lead: an explicit
    # choice wins, otherwise the same round-robin sales rotation applies, so a
    # request is never left with nobody accountable for its consultation SLA.
    try:
        if requested_owner:
            apply_assignment(
                item,
                resource_type="private_trip_request",
                resource_id=item.request_id,
                new_user_id=resolve_user_id(requested_owner, allow_blank=False),
                actor=current_user(),
                reason=str(request.form.get("assignment_reason") or "").strip(),
            )
        else:
            auto_assign_private_request(
                item,
                actor=current_user(),
                reason="Automatic round-robin sales assignment on private request creation",
            )
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("private_requests.index"))
    db.session.commit()
    owner = item.assigned_to or "nobody yet"
    flash(f"Private request {item.request_id} created and assigned to {owner}.", "success")
    return redirect(url_for("private_requests.detail", request_id=item.request_id))


@private_requests_bp.route("/<string:request_id>")
@employee_session_required
def detail(request_id: str):
    item = db.get_or_404(PrivateTripRequest, request_id)
    # The pipeline is strictly linear -- every stage has exactly one forward
    # transition plus "lost" (PRIVATE_REQUEST_TRANSITIONS). The page used to
    # render a Move button for all 11 stages, so 9 of the 10 visible buttons
    # were invalid transitions that only produced a "Cannot move X to Y" flash.
    # Resolve the single legal next step here instead.
    forward_stages = sorted(PRIVATE_REQUEST_TRANSITIONS.get(item.stage, set()) - {"lost"})
    fees = fees_for_private_request(item.request_id)
    totals = ledger_totals(item.request_id)
    return render_template(
        "admin/private_request_detail.html",
        item=item,
        stages=PRIVATE_REQUEST_STAGES,
        next_stage=forward_stages[0] if forward_stages else "",
        can_mark_lost=can_transition_private_stage(item.stage, "lost"),
        service_types=PRIVATE_SERVICE_TYPES,
        scopes=PRIVATE_TRIP_SCOPES,
        currencies=PRIVATE_BUDGET_CURRENCIES,
        employees=active_assignees(),
        can_assign=has_permission("assign_work"),
        assigned_user=item.assigned_user,
        assigned_history=assignment_history("private_trip_request", item.request_id),
        today=date.today(),
        # Money. Every figure here is read from the ledger and the fee rows --
        # nothing on this page reports an amount that was not recorded.
        money=money_for(item, totals=totals, fees=fees),
        ledger=totals,
        transactions=all_transactions(item.request_id),
        fees=fees,
        fee_categories=ADDITIONAL_FEE_CATEGORIES,
        payment_methods=PAYMENT_METHOD_LABELS,
        method_label=method_label,
        max_refund=refund_ceiling(item, totals=totals),
        can_move_money=_can_move_money(),
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


@private_requests_bp.route("/<string:request_id>/update", methods=["POST"])
@employee_session_required
def update(request_id: str):
    """Correct the request brief.

    Everything on this page was previously read-only apart from stage,
    follow-up and assignment: an agent-captured destination, date, party size
    or budget could not be fixed from the CRM at all, which meant a typo in an
    intake had to be worked around by hand. Deliberately does NOT touch
    `stage` (that is update_stage's state machine), the deposit fields (written
    only by a real deposit_paid transition), or `converted_booking_id`.
    """

    item = db.get_or_404(PrivateTripRequest, request_id)
    service_type = normalize_private_service_type(request.form.get("service_type"))
    trip_scope = normalize_private_scope(request.form.get("trip_scope"))
    destination = str(request.form.get("destination") or "").strip()
    if not service_type or not trip_scope or not destination:
        flash("Service type, trip scope, and destination are required.", "error")
        return redirect(url_for("private_requests.detail", request_id=item.request_id))

    item.service_type = service_type
    item.trip_scope = trip_scope
    item.destination = destination
    item.start_date_pref = _parse_date(request.form.get("start_date_pref"))
    item.end_date_pref = _parse_date(request.form.get("end_date_pref"))
    item.dates_flexible = bool(request.form.get("dates_flexible"))
    item.party_size = max(int(_parse_float(request.form.get("party_size")) or item.party_size or 1), 1)
    item.boys_count = int(_parse_float(request.form.get("boys_count")) or 0)
    item.girls_count = int(_parse_float(request.form.get("girls_count")) or 0)
    item.budget_amount = _parse_float(request.form.get("budget_amount"))
    currency = str(request.form.get("budget_currency") or "").strip().upper()
    item.budget_currency = currency if currency in PRIVATE_BUDGET_CURRENCIES else None
    item.notes = str(request.form.get("notes") or "").strip() or None
    # Linking is additive only: a blank picker means "left it alone", never
    # "unlink the traveler the agent already verified".
    traveler_id = str(request.form.get("traveler_id") or "").strip()
    if traveler_id:
        item.traveler_id = traveler_id
    lead_id = str(request.form.get("lead_id") or "").strip()
    if lead_id:
        item.lead_id = lead_id
    db.session.commit()
    flash(f"Private request {item.request_id} updated.", "success")
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

    # "Deposit paid" is a claim about money, so it has to be backed by money.
    # The deposit is recorded in the ledger -- either from the amount typed on
    # this form, or from payments already recorded in the money panel -- and
    # the milestone fields are then a snapshot of it rather than a second,
    # independent record that can drift away from the ledger.
    if target == "deposit_paid":
        totals = ledger_totals(item.request_id)
        typed_amount = _parse_float(request.form.get("deposit_amount"))
        if typed_amount:
            currency = str(request.form.get("deposit_currency") or "").strip().upper()
            try:
                entry = record_payment(
                    item,
                    amount=typed_amount,
                    currency=currency or None,
                    occurred_on=request.form.get("deposit_paid_on"),
                    method=request.form.get("deposit_method") or "bank_transfer",
                    notes=f"Private trip deposit for {item.request_id}.",
                    # Policy: the private-trip deposit funds design work and is
                    # not refundable once taken. Recorded on the entry so the
                    # refund ceiling enforces the policy instead of restating it.
                    is_non_refundable=True,
                    source=SOURCE_PRIVATE_DEPOSIT_STAGE,
                    # One deposit entry per request, whatever a double-submitted
                    # form or a retried request does.
                    idempotency_key=f"private-deposit:{item.request_id}",
                    actor_user_id=current_user_id(),
                    actor_name=current_actor(),
                )
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "error")
                return redirect(url_for("private_requests.detail", request_id=item.request_id))
            deposit_amount = entry.amount
            deposit_currency = entry.currency
            deposit_on = entry.occurred_on
        elif totals.total_paid > 0:
            deposit_amount = totals.total_paid
            deposit_currency = totals.currency
            deposit_on = totals.last_payment_on or now.date()
        else:
            flash(
                f"Record the deposit payment before marking {item.request_id} as deposit paid -- "
                "the amount is what the design deadline and the refund limit are calculated from.",
                "error",
            )
            return redirect(url_for("private_requests.detail", request_id=item.request_id))

        item.mark_stage(target, when=now)
        item.deposit_amount = deposit_amount
        item.deposit_currency = deposit_currency
        item.deposit_paid_at = datetime.combine(deposit_on, datetime.min.time()) if deposit_on else now
        item.deposit_is_refundable = False
        item.design_due_at = design_due_from(item.deposit_paid_at).replace(tzinfo=None)
    else:
        item.mark_stage(target, when=now)

    if target == "lost":
        item.lost_reason = str(request.form.get("lost_reason") or "").strip() or item.lost_reason
    db.session.commit()
    _refresh_traveler_stats(item.traveler_id)
    flash(f"Private request {item.request_id} moved to {target}.", "success")
    return redirect(url_for("private_requests.detail", request_id=item.request_id))


# ---------------------------------------------------------------------------
# Money
#
# A private trip is priced by hand and settled in instalments, so these routes
# are where its revenue comes from. Two rules hold across all of them: every
# amount is recorded, never inferred, and every entry is denominated in the
# request's one currency (app/services/private_trip_money.py enforces both).
#
# The "convert to booking" route that used to live here is gone. It built a
# synthetic Trip + TripBooking so a private trip's money could travel through
# the booking revenue engine; now that a request carries its own price, fees
# and payments, converting one would make the same money countable twice.
# ---------------------------------------------------------------------------


@private_requests_bp.route("/<string:request_id>/price", methods=["POST"])
@employee_session_required
def update_price(request_id: str):
    """Set what the customer agreed to pay. Not revenue -- see the money panel."""
    item = db.get_or_404(PrivateTripRequest, request_id)
    try:
        set_agreed_price(
            item,
            amount=request.form.get("agreed_price_amount"),
            currency=request.form.get("agreed_price_currency"),
        )
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("private_requests.detail", request_id=item.request_id))
    db.session.commit()
    flash(
        f"{item.request_id} priced at {item.agreed_price_amount:,.2f} {item.agreed_price_currency}.",
        "success",
    )
    return redirect(url_for("private_requests.detail", request_id=item.request_id))


@private_requests_bp.route("/<string:request_id>/payments", methods=["POST"])
@employee_session_required
def add_payment(request_id: str):
    item = db.get_or_404(PrivateTripRequest, request_id)
    try:
        entry = record_payment(
            item,
            amount=request.form.get("amount"),
            currency=request.form.get("currency"),
            occurred_on=request.form.get("occurred_on"),
            method=request.form.get("method"),
            reference=request.form.get("reference"),
            notes=request.form.get("notes"),
            is_non_refundable=bool(request.form.get("is_non_refundable")),
            actor_user_id=current_user_id(),
            actor_name=current_actor(),
        )
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("private_requests.detail", request_id=item.request_id))
    _refresh_traveler_stats(item.traveler_id)
    flash(
        f"Recorded {entry.amount:,.2f} {entry.currency} received on {item.request_id} ({entry.public_ref}).",
        "success",
    )
    return redirect(url_for("private_requests.detail", request_id=item.request_id))


@private_requests_bp.route("/<string:request_id>/refunds", methods=["POST"])
@employee_session_required
def add_refund(request_id: str):
    if not _can_move_money():
        abort(403)
    item = db.get_or_404(PrivateTripRequest, request_id)
    try:
        entry = record_refund(
            item,
            amount=request.form.get("amount"),
            reason=request.form.get("reason"),
            currency=request.form.get("currency"),
            occurred_on=request.form.get("occurred_on"),
            method=request.form.get("method"),
            reference=request.form.get("reference"),
            notes=request.form.get("notes"),
            actor_user_id=current_user_id(),
            actor_name=current_actor(),
        )
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("private_requests.detail", request_id=item.request_id))
    _refresh_traveler_stats(item.traveler_id)
    flash(
        f"Recorded a {entry.amount:,.2f} {entry.currency} refund on {item.request_id} ({entry.public_ref}).",
        "success",
    )
    return redirect(url_for("private_requests.detail", request_id=item.request_id))


@private_requests_bp.route("/<string:request_id>/transactions/<int:transaction_id>/reverse", methods=["POST"])
@employee_session_required
def reverse_transaction(request_id: str, transaction_id: int):
    """Undo a recorded entry by mirroring it. The ledger is append-only."""
    if not _can_move_money():
        abort(403)
    item = db.get_or_404(PrivateTripRequest, request_id)
    entry = db.session.get(PrivateTripTransaction, transaction_id)
    if entry is None or entry.request_id != item.request_id:
        abort(404)
    try:
        mirror = reverse_entry(
            entry,
            reason=request.form.get("reason"),
            actor_user_id=current_user_id(),
            actor_name=current_actor(),
        )
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("private_requests.detail", request_id=item.request_id))
    _refresh_traveler_stats(item.traveler_id)
    flash(f"{entry.public_ref} reversed by {mirror.public_ref}.", "success")
    return redirect(url_for("private_requests.detail", request_id=item.request_id))


@private_requests_bp.route("/<string:request_id>/fees", methods=["POST"])
@employee_session_required
def add_private_fee(request_id: str):
    item = db.get_or_404(PrivateTripRequest, request_id)
    try:
        fee = add_fee(
            private_request=item,
            label=request.form.get("label"),
            amount=request.form.get("amount"),
            currency=request.form.get("currency"),
            category=request.form.get("category"),
            notes=request.form.get("notes"),
            actor_user_id=current_user_id(),
            actor_name=current_actor(),
        )
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("private_requests.detail", request_id=item.request_id))
    flash(f"Added {fee.label} ({fee.amount:,.2f} {fee.currency}) to {item.request_id}.", "success")
    return redirect(url_for("private_requests.detail", request_id=item.request_id))


@private_requests_bp.route("/<string:request_id>/fees/<int:fee_id>/void", methods=["POST"])
@employee_session_required
def void_private_fee(request_id: str, fee_id: int):
    if not _can_move_money():
        abort(403)
    item = db.get_or_404(PrivateTripRequest, request_id)
    fee = db.session.get(AdditionalFee, fee_id)
    if fee is None or fee.private_request_id != item.request_id:
        abort(404)
    try:
        void_fee(fee, reason=request.form.get("reason"), actor_user_id=current_user_id())
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("private_requests.detail", request_id=item.request_id))
    flash(f"{fee.public_ref} removed from {item.request_id}.", "success")
    return redirect(url_for("private_requests.detail", request_id=item.request_id))

