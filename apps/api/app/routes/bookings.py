# app/routes/bookings.py
import logging
import math

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, abort, current_app
from app.models.booking import TripBooking
from app.models.booking_event import BookingEventTrail
from app.models.booking_status_history import BookingStatusHistory
from app.models.lead import Lead
from app.models.traveler import Traveler
from app.models.trip import Trip
from app.models.user import User
from app.extensions import db
from sqlalchemy import or_, and_
from datetime import datetime, date, timezone
from services.crm.system_services import UnifiedCRMService
from app.services.assignments import (
    active_assignees,
    apply_assignment,
    auto_assign_booking,
    assignment_history,
    exact_legacy_user,
    resolve_user_id,
)
from app.security import (
    can_view_assigned_record,
    current_actor,
    current_role,
    current_user,
    current_user_id,
    has_permission,
)
from app.models.additional_fee import AdditionalFee
from app.services.additional_fees import add_fee, fee_total, fees_for_booking, void_fee
from app.services.booking_audit import record_booking_created, record_booking_note
from app.services.refund_limits import (
    format_money,
    refund_allowance,
    refund_allowance_for_booking,
    validate_refund_total,
)
from app.services.traveler_stats import recalculate_traveler_stats
from services.crm.system_services import payment_rules
from services.crm.system_services.payment_rules import payment_state_token, validate_payment_transition
from services.crm.system_services.fee_rules import ADDITIONAL_FEE_CATEGORIES
from services.crm.system_services.revenue_rules import booking_revenue_breakdown
from services.crm.system_services.trip_pricing import ROOM_PRICE_TYPES

logger = logging.getLogger(__name__)

bookings_bp = Blueprint('bookings', __name__, url_prefix='/bookings')

BOOKING_STATUSES = ['Draft', 'Waiting Customer', 'Pending Confirmation', 'Confirmed', 'Payment Pending', 'Paid', 'Completed', 'Cancelled']
BOOKING_MANUAL_STATUS_OPTIONS = ['Draft', 'Completed', 'Cancelled']
# The payment vocabulary now lives in services/crm/system_services/payment_rules.py
# so this route and UnifiedCRMService enforce one identical set of rules --
# the service used to have no payment validation at all. Re-exported under
# the original names because this module is where the rest of the app (and
# the tests) have always looked for them.
REFUND_PAYMENT_STATUSES = payment_rules.REFUND_PAYMENT_STATUSES
PAYMENT_STATUSES = payment_rules.PAYMENT_STATUSES
PAYMENT_TRANSITIONS = payment_rules.PAYMENT_TRANSITIONS


def _allowed_status_options(current_status: str | None) -> list[str]:
    """Return the simplified employee status menu while preserving legacy current values."""
    current = (current_status or 'Draft').strip() or 'Draft'
    return list(dict.fromkeys([current, *BOOKING_MANUAL_STATUS_OPTIONS]))


def _allowed_payment_options(current_status: str | None) -> list[str]:
    """Return the complete employee payment menu in lifecycle order."""
    current = (current_status or 'Pending').strip() or 'Pending'
    return list(dict.fromkeys([current, *PAYMENT_STATUSES]))


def _can_change_payment_status() -> bool:
    if not current_app.config.get('CRM_AUTH_ENABLED', False):
        return True
    return has_permission('change_payment_status')


def _parse_datetime_local(value: str | None):
    raw = (value or '').strip()
    if not raw:
        return None
    for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _parse_refund_amount(value) -> float | None:
    # Form posts arrive as strings, but this endpoint also accepts JSON, where
    # a client sends a real number -- and `(value or '').strip()` raised
    # AttributeError on it, turning a valid API call into a 500.
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError('Refund amount must be a valid number.')
    if isinstance(value, (int, float)):
        amount = float(value)
    else:
        raw = str(value).strip()
        if not raw:
            return None
        try:
            amount = float(raw)
        except (TypeError, ValueError):
            raise ValueError('Refund amount must be a valid number.')
    # float() happily accepts 'nan' and 'inf'. NaN is the dangerous one: every
    # comparison against it is False, so a NaN refund slips past both the
    # negative check below and the ceiling check later, and lands in the
    # database as a number no report can add up.
    if not math.isfinite(amount):
        raise ValueError('Refund amount must be a valid number.')
    if amount < 0:
        raise ValueError('Refund amount cannot be negative.')
    return amount


_validate_payment_transition = validate_payment_transition


def _validate_completeness_for_status(
    missing_fields: list[str],
    *,
    target_booking_status: str | None,
    target_payment_status: str | None,
    allow_employee_correction: bool = False,
    correction_note: str = '',
) -> None:
    """Block a booking from silently reaching Completed / Fully Paid while
    it is still missing the fields app.services.revenue.booking_revenue
    needs to recognize any revenue at all -- that mismatch (a Completed,
    Fully Paid booking worth $0, with no visible sign anything is wrong) is
    exactly how bookings auto-created from a Lead go unnoticed. Mirrors
    _validate_payment_transition's override convention exactly: an employee
    can still push the change through with a written reason via the same
    employee_correction/notes fields already on this form.

    Deliberately narrower than app.services.revenue.REVENUE_BOOKING_STATUSES
    / REVENUE_PAYMENT_STATUSES (which also include "Confirmed"/"Paid") --
    those are earlier, everyday pipeline stages a booking legitimately
    passes through before its commercial details (trip/room/currency) are
    always finalized, and gating them here blocked normal status/refund
    updates that have nothing to do with the Completed/Fully-Paid-with-no-
    data bug this exists to catch."""
    if not missing_fields:
        return

    entering_revenue_state = (
        str(target_booking_status or '').strip().lower() == 'completed'
        or str(target_payment_status or '').strip().lower() == 'fully paid'
    )
    if not entering_revenue_state:
        return
    if allow_employee_correction and correction_note.strip():
        return
    raise ValueError(
        "This booking is missing required information: " + ", ".join(missing_fields)
        + ". Please complete the booking information before marking it Completed / Fully Paid, "
        "or add a reason to confirm anyway."
    )


def _parse_group_size(value: str | None) -> int:
    """Parse an employee-entered group size. Raises ValueError with an
    employee-facing message. Group size multiplies the room price in
    revenue_rules.booking_revenue(), so a wrong value here silently
    misstates revenue -- it is validated, never coerced silently."""
    raw = str(value or '').strip()
    if not raw:
        raise ValueError('Group size is required.')
    try:
        size = int(float(raw))
    except (TypeError, ValueError):
        raise ValueError('Group size must be a whole number.')
    if size < 1:
        raise ValueError('Group size must be at least 1.')
    return size


def _validate_refund_amount_for_status(
    *,
    target_payment_status: str | None,
    current_payment_status: str | None,
    refund_amount: float | None,
    refund_amount_submitted: bool,
    allowance=None,
) -> None:
    """A refund status with no amount recorded is never a legitimate state --
    it leaves the CRM saying money was returned without saying how much, and
    nothing else in the system can reconstruct it.

    Only fires when the booking is actually moving *into* a refund status, or
    when an amount was explicitly submitted. An unrelated edit (a follow-up
    note, a reassignment) to a legacy booking already sitting in a refund
    status with no amount is left alone -- same "only on the transition"
    principle as _validate_completeness_for_status, so this never locks an
    employee out of records they did not create.

    The same trigger governs `allowance`'s upper bound, which is what keeps
    this backward compatible: a legacy booking whose stored refund already
    exceeds today's ceiling stays fully editable for everything else, and is
    only challenged when someone actually touches the refund figure. Existing
    refunds are reported, never rewritten -- see
    scripts/report_legacy_refund_exceptions.py.
    """
    target = str(target_payment_status or '').strip()
    if target not in REFUND_PAYMENT_STATUSES:
        return
    entering_refund = target != str(current_payment_status or '').strip()
    if not entering_refund and not refund_amount_submitted:
        return
    if refund_amount is None or refund_amount <= 0:
        raise ValueError(
            f'Enter the refund amount before saving a "{target}" payment status.'
        )
    if allowance is not None:
        validate_refund_total(refund_amount, allowance)


def _append_note(existing: str | None, note: str, actor: str) -> str | None:
    clean_note = (note or '').strip()
    if not clean_note:
        return existing
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat(sep=' ')
    entry = f"[{stamp} by {actor}] {clean_note}"
    return f"{existing.rstrip()}\n{entry}" if existing else entry


def _remove_sheet_record(mapping_name: str, record_id: str) -> None:
    try:
        UnifiedCRMService().remove_record_from_sheet(mapping_name, record_id)
    except Exception:
        pass


def _is_overdue(moment) -> bool:
    if not moment:
        return False
    if isinstance(moment, datetime):
        return moment.date() < date.today()
    return moment < date.today()


@bookings_bp.route('/')
def index():
    q = request.args.get('q', '')
    status = request.args.get('status', '')
    payment = request.args.get('payment', '')
    queue = request.args.get('queue', '')
    employee_id = request.args.get('employee_id', type=int)
    page = request.args.get('page', 1, type=int)
    per_page = 25

    query = TripBooking.query
    if q:
        query = query.filter(or_(
            TripBooking.traveler_name.ilike(f'%{q}%'),
            TripBooking.booking_id.ilike(f'%{q}%'),
            TripBooking.trip_name.ilike(f'%{q}%'),
            TripBooking.booking_notes.ilike(f'%{q}%'),
            TripBooking.booking_source.ilike(f'%{q}%'),
            TripBooking.traveler_id.ilike(f'%{q}%'),
        ))
    if status:
        query = query.filter(TripBooking.booking_status == status)
    if payment:
        query = query.filter(TripBooking.payment_status == payment)
    if queue == 'assigned_to_me':
        query = query.filter(TripBooking.assigned_to_user_id == current_user_id())
    elif employee_id and has_permission('view_all'):
        query = query.filter(TripBooking.assigned_to_user_id == employee_id)
    elif queue == 'unassigned':
        query = query.filter(TripBooking.assigned_to_user_id.is_(None))
    elif queue == 'inactive_owner':
        query = query.filter(TripBooking.assigned_user.has(User.is_active.is_(False)))
    elif queue == 'overdue':
        query = query.filter(TripBooking.next_follow_up_at < datetime.combine(date.today(), datetime.min.time()))
    elif queue == 'today':
        start = datetime.combine(date.today(), datetime.min.time())
        end = datetime.combine(date.today(), datetime.max.time())
        query = query.filter(and_(TripBooking.next_follow_up_at >= start, TripBooking.next_follow_up_at <= end))
    elif queue == 'waiting_customer':
        query = query.filter(TripBooking.booking_status == 'Waiting Customer')
    elif queue == 'deposit_pending':
        query = query.filter(or_(TripBooking.payment_status == 'Pending', TripBooking.booking_status == 'Payment Pending'))
    elif queue == 'documents_missing':
        query = query.filter(TripBooking.passport_status == 'pending')
    elif queue == 'needs_info':
        query = query.filter(TripBooking.missing_info.is_(True))

    if not has_permission('view_all'):
        query = query.filter(or_(TripBooking.assigned_to_user_id.is_(None), TripBooking.assigned_to_user_id == current_user_id()))

    pagination = query.order_by(TripBooking.draft_created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False)
    bookings = pagination.items

    # Summary counts
    counts = {s: TripBooking.query.filter_by(booking_status=s).count() for s in BOOKING_STATUSES}
    needs_info_count = TripBooking.query.filter(TripBooking.missing_info.is_(True)).count()

    # Fetch active trips for the booking modal
    active_trips = Trip.query.filter(Trip.sales_status == 'Open').all()
    trips_data = [t.to_dict() for t in active_trips]

    return render_template('bookings/index.html',
                           bookings=bookings,
                           pagination=pagination,
                           total_count=pagination.total,
                           q=q,
                           current_status=status,
                           current_payment=payment,
                           current_queue=queue,
                           current_employee_id=employee_id,
                           employees=active_assignees() if has_permission('view_all') else [],
                           can_assign=has_permission('assign_work'),
                           statuses=BOOKING_STATUSES,
                           payment_statuses=PAYMENT_STATUSES,
                           counts=counts,
                           needs_info_count=needs_info_count,
                           trips_data=trips_data,
                           today=date.today())


@bookings_bp.route('/<string:booking_id>')
def detail(booking_id):
    booking = db.get_or_404(TripBooking, booking_id)
    if not can_view_assigned_record(booking.assigned_to_user_id):
        abort(403)
    traveler = db.session.get(Traveler, booking.traveler_id) if booking.traveler_id else None
    trip = db.session.get(Trip, booking.trip_id) if booking.trip_id else None
    commercial_context = UnifiedCRMService.resolve_commercial_context(
        traveler=traveler.to_dict() if traveler else None,
        trip_type=trip.type if trip else None,
        requested_group_size=booking.group_size or 1,
        trip_id=booking.trip_id or "",
    )
    try:
        UnifiedCRMService().ensure_operational_schema()
    except Exception:
        pass
    event_trail = BookingEventTrail.query.filter(
        or_(
            BookingEventTrail.booking_id == booking.booking_id,
            BookingEventTrail.lead_id == booking.lead_id,
            BookingEventTrail.traveler_id == booking.traveler_id,
        )
    ).order_by(BookingEventTrail.occurred_at.asc()).all()
    status_history = BookingStatusHistory.query.filter_by(booking_id=booking.booking_id).order_by(BookingStatusHistory.changed_at.asc(), BookingStatusHistory.history_id.asc()).all()
    history_count = len(status_history)
    assigned_history = assignment_history('booking', booking.booking_id)

    # Fix 1: reuse the same active-trip catalog + serialization the New
    # Booking modal uses, so the Trip -> Room Type selector on this page is
    # driven by identical data, not a second independent source. The
    # booking's own linked trip is included even if it's no longer "Open"
    # for sale, so an already-linked trip is never missing from the list.
    active_trips = Trip.query.filter(Trip.sales_status == 'Open').all()
    if trip and trip.trip_id not in {t.trip_id for t in active_trips}:
        active_trips = [trip] + active_trips
    trips_data = [t.to_dict() for t in active_trips]

    # Additional fees the employee has added on top of the trip price. The
    # breakdown is computed by the same rule Revenue Analytics uses, so the
    # value shown on this page is exactly what the booking contributes there.
    fees = fees_for_booking(booking.booking_id)
    fees_total = fee_total(fees, booking.currency)
    revenue_breakdown = booking_revenue_breakdown(booking, trip, fees)

    return render_template('bookings/detail.html',
                           booking=booking,
                           traveler=traveler,
                           trip=trip,
                           fees=fees,
                           fees_total=fees_total,
                           fee_categories=ADDITIONAL_FEE_CATEGORIES,
                           revenue_breakdown=revenue_breakdown,
                           commercial_context=commercial_context,
                           event_trail=event_trail,
                           status_history=status_history,
                           history_count=history_count,
                           assigned_history=assigned_history,
                           employees=active_assignees(),
                           can_assign=has_permission('assign_work'),
                           can_change_payment_status=_can_change_payment_status(),
                           missing_fields=booking.compute_missing_fields(),
                           refund_allowance=refund_allowance_for_booking(booking, trip, fees_total),
                           format_money=format_money,
                           expected_payment_state=payment_state_token(booking.payment_status, booking.refund_amount),
                           refund_payment_statuses=sorted(REFUND_PAYMENT_STATUSES),
                           trips_data=trips_data,
                           assigned_user=booking.assigned_user,
                           is_overdue=_is_overdue,
                           booking_statuses=_allowed_status_options(booking.booking_status),
                           payment_statuses=_allowed_payment_options(booking.payment_status))


@bookings_bp.route('/', methods=['POST'])
def create():
    data = request.form.to_dict()
    
    trip_id = data.get('trip_id')
    traveler_id = data.get('traveler_id')
    lead_id = (data.get('lead_id') or '').strip()
    room_type = data.get('room_type')
    group_size = data.get('group_size', '1').strip() or '1'
    
    if not trip_id or not traveler_id or not room_type:
        flash("Trip, Traveler, and Room Type are required to create a booking.", "error")
        return redirect(url_for('bookings.index'))
    if data.get('assigned_to_user_id') and not has_permission('assign_work'):
        flash('You do not have permission to assign bookings.', 'error')
        return redirect(url_for('bookings.index'))

    traveler = db.session.get(Traveler, traveler_id)
    if not traveler:
        flash(f"Traveler '{traveler_id}' was not found.", "error")
        return redirect(url_for('bookings.index'))
        
    try:
        service = UnifiedCRMService()
        trip = db.session.get(Trip, trip_id)
        passport_required = bool(trip and str(trip.type or '').strip().lower() == 'international')
        passport_status = 'provided' if traveler.passport_number or traveler.passport_attachment_ref else ('pending' if passport_required else '')
        booking_notes = (data.get('booking_notes', '') or '').strip()
        try:
            normalized_group_size = max(int(group_size), 1)
        except Exception:
            normalized_group_size = 1
        if normalized_group_size > 1:
            group_note = f"Group size: {normalized_group_size}"
            booking_notes = f"{group_note}\n{booking_notes}" if booking_notes else group_note
        result = service.create_booking(
            trip_id=trip_id,
            traveler_id=traveler_id,
            traveler_name=traveler.full_name,
            room_type=room_type,
            channel=data.get('booking_source', 'Admin'),
            flight_option=data.get('flight_option', ''),
            currency=data.get('currency', 'USD'),
            booking_status='Draft',
            booking_source=data.get('booking_source', 'Admin'),
            payment_status=data.get('payment_status', 'Pending'),
            booking_notes=booking_notes,
            passport_required=passport_required,
            passport_status=passport_status,
            group_size=normalized_group_size,
            lead_id=lead_id,
        )
        booking_id = result["booking_id"]
        db.session.expire_all()
        created_booking = db.session.get(TripBooking, booking_id)
        if data.get('assigned_to_user_id'):
            apply_assignment(
                created_booking,
                resource_type='booking',
                resource_id=booking_id,
                new_user_id=resolve_user_id(data.get('assigned_to_user_id'), allow_blank=False),
                actor=current_user(),
                reason=data.get('assignment_reason', ''),
            )
        elif created_booking:
            auto_assign_booking(
                created_booking,
                actor=current_user(),
                reason='Automatic round-robin sales assignment on booking creation',
            )
        if created_booking:
            # The row above was inserted through UnifiedCRMService's own
            # sqlite3 connection, so the ORM never saw an INSERT and the
            # session-level audit listener has nothing to react to. Every
            # other creation path flushes a TripBooking and is covered
            # automatically -- this one has to say so itself.
            record_booking_created(
                created_booking,
                source=data.get('booking_source', 'Admin'),
                notes=f"Booking created in the CRM for {traveler.full_name}.",
            )
        db.session.commit()
        flash(f"Booking {booking_id} created successfully.", 'success')
        return redirect(url_for('bookings.detail', booking_id=booking_id))
    except ValueError as e:
        flash(f"Booking failed: {str(e)}", 'error')
        return redirect(url_for('bookings.index'))
    except Exception as e:
        flash(f"An unexpected error occurred during booking: {str(e)}", 'error')
        return redirect(url_for('bookings.index'))


@bookings_bp.route('/<string:booking_id>/status', methods=['POST'])
def update_status(booking_id):
    booking = db.get_or_404(TripBooking, booking_id)
    data = request.get_json(silent=True) or request.form.to_dict()
    new_status = data.get('booking_status')
    new_payment = data.get('payment_status')
    refund_amount_present = 'refund_amount' in data
    note_for_service = (data.get('booking_notes') or '').strip()
    employee_correction = (
        str(data.get('employee_correction') or '').strip().lower() in {'1', 'true', 'yes'}
        and not request.is_json
    )
    def _conflict(message: str):
        if request.is_json:
            return jsonify({'error': message}), 409
        flash(message, 'error')
        return redirect(url_for('bookings.detail', booking_id=booking_id))

    expected_history_count = data.get('expected_history_count')
    if expected_history_count not in (None, ''):
        actual_history_count = BookingStatusHistory.query.filter_by(booking_id=booking_id).count()
        try:
            submitted_history_count = int(expected_history_count)
        except (TypeError, ValueError):
            # A malformed token is a broken client, not a concurrent edit.
            # Reporting it as "another employee changed this" sent people off
            # to reload a page that was never stale.
            return _conflict('Could not verify this booking was up to date. Reload the page and try again.')
        if submitted_history_count != actual_history_count:
            return _conflict('Booking was updated by another employee')

    # booking_status_history only gains a row when booking_status changes, so
    # the count above cannot see a concurrent payment or refund edit at all --
    # two employees refunding the same booking both passed it and the later
    # save silently overwrote the earlier one. This second token covers the
    # money fields specifically. Optional, like the count: a JSON client that
    # omits it keeps working exactly as before.
    expected_payment_state = data.get('expected_payment_state')
    if expected_payment_state not in (None, ''):
        current_payment_state = payment_state_token(booking.payment_status, booking.refund_amount)
        if str(expected_payment_state) != current_payment_state:
            return _conflict(
                'The payment details for this booking changed while you were editing. '
                'Reload the page to see the current amounts before saving.'
            )

    if new_status and new_status not in BOOKING_STATUSES:
        flash('Invalid status transition', 'error')
        return redirect(url_for('bookings.detail', booking_id=booking_id))
    if new_payment and new_payment not in PAYMENT_STATUSES:
        flash('Invalid payment status transition', 'error')
        return redirect(url_for('bookings.detail', booking_id=booking_id))

    # Booking-completion fields (Fix 1): let an employee fill in what an
    # auto-created-from-lead booking is missing, right on this same form,
    # instead of only being able to fix status/payment here.
    trip_id_submitted = 'trip_id' in data
    room_type_submitted = 'room_type' in data
    flight_option_submitted = 'flight_option' in data
    currency_submitted = 'currency' in data
    group_size_submitted = 'group_size' in data
    new_trip_id = (data.get('trip_id') or '').strip() if trip_id_submitted else None
    new_room_type = (data.get('room_type') or '').strip().title() if room_type_submitted else None
    new_flight_option = (data.get('flight_option') or '').strip() if flight_option_submitted else None
    new_currency = (data.get('currency') or '').strip().upper() if currency_submitted else None
    new_group_size = None
    if group_size_submitted:
        try:
            new_group_size = _parse_group_size(data.get('group_size'))
        except ValueError as e:
            if request.is_json:
                return jsonify({'error': str(e)}), 400
            flash(str(e), 'error')
            return redirect(url_for('bookings.detail', booking_id=booking_id))

    trip_for_update = None
    if trip_id_submitted and new_trip_id:
        trip_for_update = db.session.get(Trip, new_trip_id)
        if not trip_for_update:
            flash(f"Trip '{new_trip_id}' was not found.", 'error')
            return redirect(url_for('bookings.detail', booking_id=booking_id))
    if room_type_submitted and new_room_type and new_room_type not in ROOM_PRICE_TYPES:
        flash('Choose a valid room type (Single, Double, or Triple).', 'error')
        return redirect(url_for('bookings.detail', booking_id=booking_id))
    if currency_submitted and new_currency:
        from app.services.revenue import REVENUE_CURRENCIES
        if new_currency not in REVENUE_CURRENCIES:
            flash('Currency must be USD or EGP.', 'error')
            return redirect(url_for('bookings.detail', booking_id=booking_id))

    try:
        refund_amount = _parse_refund_amount(data.get('refund_amount')) if refund_amount_present else booking.refund_amount
    except ValueError as e:
        if request.is_json:
            return jsonify({'error': str(e)}), 400
        flash(str(e), 'error')
        return redirect(url_for('bookings.detail', booking_id=booking_id))
    try:
        _validate_payment_transition(
            booking.payment_status,
            new_payment,
            allow_employee_correction=employee_correction,
            correction_note=note_for_service,
        )
    except ValueError as e:
        if request.is_json:
            return jsonify({'error': str(e)}), 400
        flash(str(e), 'error')
        return redirect(url_for('bookings.detail', booking_id=booking_id))
    payment_for_service = new_payment if new_payment and new_payment != (booking.payment_status or '') else None
    refund_change_requested = (
        refund_amount_present
        and refund_amount != booking.refund_amount
    )
    if (payment_for_service or refund_change_requested) and not _can_change_payment_status():
        if request.is_json:
            return jsonify({'error': 'forbidden'}), 403
        flash('You do not have permission to change payment status.', 'error')
        return redirect(url_for('bookings.detail', booking_id=booking_id))
    actor = current_actor()
    assignment_requested = 'assigned_to_user_id' in data or 'assigned_to' in data
    requested_user_id = booking.assigned_to_user_id
    if assignment_requested:
        if not has_permission('assign_work'):
            if request.is_json:
                return jsonify({'error': 'forbidden'}), 403
            flash('You do not have permission', 'error')
            return redirect(url_for('bookings.detail', booking_id=booking_id))
        try:
            if 'assigned_to_user_id' in data:
                requested_user_id = resolve_user_id(data.get('assigned_to_user_id'))
            else:
                legacy_user = exact_legacy_user(data.get('assigned_to'), include_inactive=False)
                if data.get('assigned_to', '').strip() and not legacy_user:
                    raise ValueError('Choose an active employee from the list')
                requested_user_id = legacy_user.id if legacy_user else None
        except ValueError as exc:
            if request.is_json:
                return jsonify({'error': str(exc)}), 400
            flash(str(exc), 'error')
            return redirect(url_for('bookings.detail', booking_id=booking_id))
    status_for_service = new_status if new_status and new_status != (booking.booking_status or '') else None

    prospective_missing = booking.compute_missing_fields(
        trip_id=(trip_for_update.trip_id if trip_for_update else new_trip_id) if trip_id_submitted else None,
        room_type=new_room_type if room_type_submitted else None,
        currency=new_currency if currency_submitted else None,
    )
    try:
        _validate_completeness_for_status(
            prospective_missing,
            target_booking_status=status_for_service or booking.booking_status,
            target_payment_status=payment_for_service or booking.payment_status,
            allow_employee_correction=employee_correction,
            correction_note=note_for_service,
        )
    except ValueError as e:
        if request.is_json:
            return jsonify({'error': str(e)}), 400
        flash(str(e), 'error')
        return redirect(url_for('bookings.detail', booking_id=booking_id))

    # Price the refund against the booking as it will be *after* this save,
    # not as it is stored now: the same form can change the trip, room type,
    # currency or party size in the request that records the refund, and
    # validating against the stale figures would use the wrong ceiling.
    if trip_id_submitted:
        effective_trip = trip_for_update
    else:
        effective_trip = db.session.get(Trip, booking.trip_id) if booking.trip_id else None
    effective_currency = (new_currency if currency_submitted else booking.currency) or ''
    effective_allowance = refund_allowance(
        trip=effective_trip,
        room_type=(new_room_type if room_type_submitted else booking.room_type) or '',
        currency=effective_currency,
        group_size=new_group_size if (group_size_submitted and new_group_size) else booking.group_size,
        payment_status=payment_for_service or booking.payment_status,
        already_refunded=booking.refund_amount,
        booking_id=booking.booking_id,
        # Fees are part of what the customer paid, so they are part of what
        # may be refunded -- priced in the currency this save will leave the
        # booking in, since fees in any other are not part of its total.
        fees_total=fee_total(fees_for_booking(booking.booking_id), effective_currency),
    )
    try:
        _validate_refund_amount_for_status(
            target_payment_status=payment_for_service or booking.payment_status,
            current_payment_status=booking.payment_status,
            refund_amount=refund_amount,
            refund_amount_submitted=refund_amount_present,
            allowance=effective_allowance,
        )
    except ValueError as e:
        if request.is_json:
            return jsonify({'error': str(e)}), 400
        flash(str(e), 'error')
        return redirect(url_for('bookings.detail', booking_id=booking_id))

    detail_fields_changed = (
        trip_id_submitted
        or room_type_submitted
        or flight_option_submitted
        or currency_submitted
        or (group_size_submitted and new_group_size != (booking.group_size or 1))
    )
    was_missing = booking.missing_info
    previous_payment_status = booking.payment_status
    previous_refund_amount = booking.refund_amount
    try:
        if status_for_service:
            UnifiedCRMService._validate_booking_transition(
                booking.booking_status,
                status_for_service,
                allow_employee_correction=employee_correction,
                correction_note=note_for_service,
            )
            db.session.add(
                BookingStatusHistory(
                    booking_id=booking.booking_id,
                    old_status=booking.booking_status or 'Draft',
                    new_status=status_for_service,
                    changed_by=actor,
                    change_source='crm-ui',
                    notes=note_for_service or None,
                )
            )
            booking.booking_status = status_for_service
        if payment_for_service:
            booking.payment_status = payment_for_service
        effective_payment_status = payment_for_service or booking.payment_status or 'Pending'
        if refund_amount_present or payment_for_service:
            booking.refund_amount = refund_amount if effective_payment_status in REFUND_PAYMENT_STATUSES else None
        if trip_id_submitted:
            booking.trip_id = trip_for_update.trip_id if trip_for_update else None
            booking.trip_name = trip_for_update.trip_name if trip_for_update else None
            if trip_for_update:
                booking.passport_required = str(trip_for_update.type or '').strip().lower() == 'international'
                if booking.passport_status != 'provided':
                    booking.passport_status = 'pending' if booking.passport_required else ''
        if room_type_submitted:
            booking.room_type = new_room_type or None
        if flight_option_submitted:
            booking.flight_option = new_flight_option or None
        if currency_submitted:
            booking.currency = new_currency or None
        if group_size_submitted and new_group_size:
            booking.group_size = new_group_size
        if detail_fields_changed:
            booking.recompute_missing_info()
        if assignment_requested:
            apply_assignment(
                booking,
                resource_type='booking',
                resource_id=booking.booking_id,
                new_user_id=requested_user_id,
                actor=current_user(),
                reason=data.get('assignment_reason', ''),
            )
        booking.priority = (data.get('priority') or booking.priority or 'Medium').strip()
        booking.next_action = (data.get('next_action') or '').strip() or None
        booking.next_follow_up_at = _parse_datetime_local(data.get('next_follow_up_at'))
        booking.customer_response_status = (data.get('customer_response_status') or '').strip() or None
        if data.get('mark_contacted') == '1':
            booking.last_contact_at = datetime.now(timezone.utc).replace(microsecond=0)
            booking.customer_response_status = booking.customer_response_status or 'Contacted'
        booking.booking_notes = _append_note(booking.booking_notes, note_for_service, actor)
        # Only the note the employee actually typed becomes a timeline entry.
        # The audit line appended just below is written by the system and
        # already has its own structured events from the session listener, so
        # recording it here too would say the same thing twice.
        record_booking_note(booking, note_for_service, actor=actor)
        # Money changes must leave a record even when the employee types no
        # note. BookingStatusHistory only captures booking_status changes
        # (written above, inside `if status_for_service`), so a pure
        # payment/refund edit would otherwise vanish -- including an amount
        # being cleared. This is the audit trail for it.
        audit_parts: list[str] = []
        if payment_for_service:
            audit_parts.append(f"Payment status: {previous_payment_status or 'Pending'} -> {booking.payment_status}")
        if booking.refund_amount != previous_refund_amount:
            before = f"{previous_refund_amount:,.2f}" if previous_refund_amount is not None else "none"
            after = f"{booking.refund_amount:,.2f}" if booking.refund_amount is not None else "none"
            audit_parts.append(f"Refund amount: {before} -> {after}")
        if audit_parts:
            booking.booking_notes = _append_note(booking.booking_notes, "; ".join(audit_parts), actor)
        db.session.commit()
        if booking.traveler_id and (status_for_service or payment_for_service or detail_fields_changed):
            try:
                recalculate_traveler_stats(booking.traveler_id)
            except Exception:
                logger.error("Traveler stats recalculation failed booking_id=%s traveler_id=%s", booking_id, booking.traveler_id, exc_info=True)
                db.session.rollback()
    except ValueError as e:
        message = 'Invalid status transition' if 'Invalid booking status transition' in str(e) else str(e)
        flash(message, 'error')
        return redirect(url_for('bookings.detail', booking_id=booking_id))
    except Exception as e:
        db.session.rollback()
        logger.error("Booking status update failed booking_id=%s error=%s", booking_id, e, exc_info=True)
        flash('Something went wrong saving this update. Please try again.', 'error')
        return redirect(url_for('bookings.detail', booking_id=booking_id))

    if request.is_json:
        return jsonify({'status': 'ok', 'booking': booking.to_dict()})
    if was_missing and not booking.missing_info:
        flash('Booking information completed -- this booking now counts toward revenue.', 'success')
    elif status_for_service and payment_for_service:
        flash('Changes saved', 'success')
    elif payment_for_service:
        flash('Payment status updated successfully', 'success')
    elif status_for_service:
        flash('Status updated successfully', 'success')
    else:
        flash('Changes saved', 'success')
    return redirect(url_for('bookings.detail', booking_id=booking_id))


@bookings_bp.route('/<string:booking_id>/fees', methods=['POST'])
def add_booking_fee(booking_id):
    """Record an additional fee on this booking (visa, insurance, transfer...).

    Ordinary sales work, so any employee with CRM write access may add one --
    the same gate every other field on this page uses. Voiding one moves money
    the other way and takes the refund permission instead.

    A fee counts toward revenue for this booking under exactly the same
    recognition rule as the trip price: it is earned when the booking is in a
    revenue status, in the booking's own currency, never converted.
    """
    booking = db.get_or_404(TripBooking, booking_id)
    if not can_view_assigned_record(booking.assigned_to_user_id):
        abort(403)
    try:
        fee = add_fee(
            booking=booking,
            label=request.form.get('label'),
            amount=request.form.get('amount'),
            currency=request.form.get('currency'),
            category=request.form.get('category'),
            notes=request.form.get('notes'),
            actor_user_id=current_user_id(),
            actor_name=current_actor(),
        )
        record_booking_note(
            booking,
            note=f"Additional fee added: {fee.label} -- {fee.amount:,.2f} {fee.currency}.",
            actor=current_actor(),
        )
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        if request.is_json:
            return jsonify({'error': str(exc)}), 400
        flash(str(exc), 'error')
        return redirect(url_for('bookings.detail', booking_id=booking_id))
    if booking.traveler_id:
        try:
            recalculate_traveler_stats(booking.traveler_id)
        except Exception:
            logger.error("Traveler stats recalculation failed booking_id=%s", booking_id, exc_info=True)
            db.session.rollback()
    flash(f"Added {fee.label} ({fee.amount:,.2f} {fee.currency}) to this booking.", 'success')
    return redirect(url_for('bookings.detail', booking_id=booking_id))


@bookings_bp.route('/<string:booking_id>/fees/<int:fee_id>/void', methods=['POST'])
def void_booking_fee(booking_id, fee_id):
    """Take a fee off a booking, with a reason, without deleting the record."""
    booking = db.get_or_404(TripBooking, booking_id)
    if not can_view_assigned_record(booking.assigned_to_user_id):
        abort(403)
    if not _can_change_payment_status():
        abort(403)
    fee = db.session.get(AdditionalFee, fee_id)
    if fee is None or fee.booking_id != booking.booking_id:
        abort(404)
    try:
        void_fee(fee, reason=request.form.get('reason'), actor_user_id=current_user_id())
        record_booking_note(
            booking,
            note=f"Additional fee removed: {fee.label} -- {fee.amount:,.2f} {fee.currency}. Reason: {fee.void_reason}",
            actor=current_actor(),
        )
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
        return redirect(url_for('bookings.detail', booking_id=booking_id))
    if booking.traveler_id:
        try:
            recalculate_traveler_stats(booking.traveler_id)
        except Exception:
            logger.error("Traveler stats recalculation failed booking_id=%s", booking_id, exc_info=True)
            db.session.rollback()
    flash(f"{fee.public_ref} removed from this booking.", 'success')
    return redirect(url_for('bookings.detail', booking_id=booking_id))


@bookings_bp.route('/<string:booking_id>/delete', methods=['POST'])
def delete(booking_id):
    booking = db.get_or_404(TripBooking, booking_id)
    traveler_id = booking.traveler_id or ''
    trip_id = booking.trip_id or ''
    lead_id = booking.lead_id or ''

    BookingStatusHistory.query.filter_by(booking_id=booking_id).delete(synchronize_session=False)
    BookingEventTrail.query.filter(BookingEventTrail.booking_id == booking_id).delete(synchronize_session=False)
    # Fees point at this booking, so they go with it -- otherwise the delete
    # fails on the foreign key (Postgres) or leaves rows pointing at nothing
    # (SQLite), and those rows would keep contributing to revenue totals.
    AdditionalFee.query.filter(AdditionalFee.booking_id == booking_id).delete(synchronize_session=False)

    if lead_id:
        replacement_booking_id = db.session.execute(
            db.select(TripBooking.booking_id)
            .where(TripBooking.lead_id == lead_id, TripBooking.booking_id != booking_id)
            .order_by(TripBooking.draft_created_at.desc(), TripBooking.booking_id.desc())
            .limit(1)
        ).scalar_one_or_none()
        lead_rows_updated = Lead.query.filter_by(lead_id=lead_id).update(
            {Lead.booking_id: replacement_booking_id},
            synchronize_session=False,
        )
        if lead_rows_updated == 0:
            # lead_id came straight off the booking we're deleting, so this
            # should always match exactly one row. A silent 0 here would
            # leave Lead.booking_id dangling on a row that no longer exists
            # once we commit the delete below -- the same "write happened,
            # nobody checked, wrong state persists" shape as guardian
            # consent's original bug.
            logger.warning(
                "Booking delete: Lead.booking_id cleanup matched no rows booking_id=%s lead_id=%s",
                booking_id,
                lead_id,
            )

    if traveler_id:
        replacement_last_booking = db.session.execute(
            db.select(TripBooking.booking_id)
            .where(TripBooking.traveler_id == traveler_id, TripBooking.booking_id != booking_id)
            .order_by(TripBooking.draft_created_at.desc(), TripBooking.booking_id.desc())
            .limit(1)
        ).scalar_one_or_none()
        traveler_rows_updated = Traveler.query.filter_by(traveler_id=traveler_id).update(
            {Traveler.last_booking_id: replacement_last_booking},
            synchronize_session=False,
        )
        if traveler_rows_updated == 0:
            logger.warning(
                "Booking delete: Traveler.last_booking_id cleanup matched no rows booking_id=%s traveler_id=%s",
                booking_id,
                traveler_id,
            )

    db.session.delete(booking)
    db.session.commit()

    try:
        service = UnifiedCRMService()
        if trip_id:
            with service.connect() as connection:
                service._reconcile_trip_room_holds(connection, trip_id)
                connection.commit()
        if traveler_id:
            recalculate_traveler_stats(traveler_id)
    except Exception:
        pass

    _remove_sheet_record("Bookings", booking_id)
    flash(f"Booking {booking_id} deleted successfully.", 'success')
    return redirect(url_for('bookings.index'))


# A _sync_booking_event() helper used to live here: it wrote booking events
# through UnifiedCRMService's raw sqlite3 connection instead of the ORM, and
# nothing ever called it. Removed rather than wired up -- it targets a
# different connection from the one bookings.detail reads the timeline back
# out of, so events written that way were invisible on the page they existed
# for. app/services/booking_audit.py is the single audit path now.
