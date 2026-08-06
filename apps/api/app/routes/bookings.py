# app/routes/bookings.py
import logging

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, abort
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

logger = logging.getLogger(__name__)

bookings_bp = Blueprint('bookings', __name__, url_prefix='/bookings')

BOOKING_STATUSES = ['Draft', 'Waiting Customer', 'Pending Confirmation', 'Confirmed', 'Payment Pending', 'Paid', 'Completed', 'Cancelled']
BOOKING_MANUAL_STATUS_OPTIONS = ['Draft', 'Completed', 'Cancelled']
PAYMENT_STATUSES = ['Pending', 'Deposit Paid', 'Fully Paid', 'Refunded']
PAYMENT_TRANSITIONS = {
    'Pending': {'Deposit Paid', 'Fully Paid', 'Refunded'},
    'Deposit Paid': {'Fully Paid', 'Refunded'},
    'Fully Paid': {'Refunded'},
    'Refunded': set(),
}


def _allowed_status_options(current_status: str | None) -> list[str]:
    """Return the simplified employee status menu while preserving legacy current values."""
    current = (current_status or 'Draft').strip() or 'Draft'
    return list(dict.fromkeys([current, *BOOKING_MANUAL_STATUS_OPTIONS]))


def _allowed_payment_options(current_status: str | None) -> list[str]:
    """Return the complete employee payment menu in lifecycle order."""
    current = (current_status or 'Pending').strip() or 'Pending'
    return list(dict.fromkeys([current, *PAYMENT_STATUSES]))


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


def _validate_payment_transition(
    current_status: str | None,
    new_status: str | None,
    *,
    allow_employee_correction: bool = False,
    correction_note: str = '',
) -> None:
    current = (current_status or 'Pending').strip() or 'Pending'
    target = (new_status or '').strip()
    if not target or target == current:
        return
    if target not in PAYMENT_TRANSITIONS.get(current, set()):
        if allow_employee_correction and target in PAYMENT_STATUSES:
            if not correction_note.strip():
                raise ValueError(
                    'Add a reason before making a non-standard payment status change.'
                )
            return
        raise ValueError(f"Invalid payment status transition: {current} -> {target}")


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

    pagination = query.order_by(TripBooking.draft_created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False)
    bookings = pagination.items

    # Summary counts
    counts = {s: TripBooking.query.filter_by(booking_status=s).count() for s in BOOKING_STATUSES}

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
    return render_template('bookings/detail.html',
                           booking=booking,
                           traveler=traveler,
                           trip=trip,
                           commercial_context=commercial_context,
                           event_trail=event_trail,
                           status_history=status_history,
                           history_count=history_count,
                           assigned_history=assigned_history,
                           employees=active_assignees(),
                           can_assign=has_permission('assign_work'),
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
        if data.get('assigned_to_user_id'):
            db.session.expire_all()
            created_booking = db.session.get(TripBooking, booking_id)
            apply_assignment(
                created_booking,
                resource_type='booking',
                resource_id=booking_id,
                new_user_id=resolve_user_id(data.get('assigned_to_user_id'), allow_blank=False),
                actor=current_user(),
                reason=data.get('assignment_reason', ''),
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
    note_for_service = (data.get('booking_notes') or '').strip()
    employee_correction = (
        str(data.get('employee_correction') or '').strip().lower() in {'1', 'true', 'yes'}
        and not request.is_json
    )
    expected_history_count = data.get('expected_history_count')
    if expected_history_count not in (None, ''):
        actual_history_count = BookingStatusHistory.query.filter_by(booking_id=booking_id).count()
        try:
            if int(expected_history_count) != actual_history_count:
                raise ValueError("Booking was updated by another employee")
        except ValueError as e:
            if request.is_json:
                return jsonify({'error': str(e)}), 409
            flash(str(e), 'error')
            return redirect(url_for('bookings.detail', booking_id=booking_id))
    if new_status and new_status not in BOOKING_STATUSES:
        flash('Invalid status transition', 'error')
        return redirect(url_for('bookings.detail', booking_id=booking_id))
    if new_payment and new_payment not in PAYMENT_STATUSES:
        flash('Invalid payment status transition', 'error')
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
    payment_for_service = new_payment if new_payment and new_payment != (booking.payment_status or '') else None
    try:
        service = UnifiedCRMService()
        if status_for_service or payment_for_service or note_for_service:
            service.update_booking_status(
                booking_id,
                new_status=status_for_service,
                new_payment_status=payment_for_service,
                changed_by=actor,
                change_source='crm-ui',
                notes=note_for_service,
                allow_employee_correction=employee_correction,
            )
        db.session.expire_all()
        booking = db.get_or_404(TripBooking, booking_id)
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
        db.session.commit()
    except ValueError as e:
        message = 'Invalid status transition' if 'Invalid booking status transition' in str(e) else str(e)
        flash(message, 'error')
        return redirect(url_for('bookings.detail', booking_id=booking_id))
    except Exception as e:
        flash('CRM service unavailable', 'error')
        return redirect(url_for('bookings.detail', booking_id=booking_id))

    if request.is_json:
        return jsonify({'status': 'ok', 'booking': booking.to_dict()})
    if status_for_service and payment_for_service:
        flash('Changes saved', 'success')
    elif payment_for_service:
        flash('Payment status updated successfully', 'success')
    elif status_for_service:
        flash('Status updated successfully', 'success')
    else:
        flash('Changes saved', 'success')
    return redirect(url_for('bookings.detail', booking_id=booking_id))


@bookings_bp.route('/<string:booking_id>/delete', methods=['POST'])
def delete(booking_id):
    booking = db.get_or_404(TripBooking, booking_id)
    traveler_id = booking.traveler_id or ''
    trip_id = booking.trip_id or ''
    lead_id = booking.lead_id or ''

    BookingStatusHistory.query.filter_by(booking_id=booking_id).delete(synchronize_session=False)
    BookingEventTrail.query.filter(BookingEventTrail.booking_id == booking_id).delete(synchronize_session=False)

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
            service.recalculate_traveler_stats(traveler_id)
    except Exception:
        pass

    _remove_sheet_record("Bookings", booking_id)
    flash(f"Booking {booking_id} deleted successfully.", 'success')
    return redirect(url_for('bookings.index'))


def _sync_booking_event(booking: TripBooking, *, event_type: str, event_label: str, notes: str = '') -> None:
    try:
        service = UnifiedCRMService()
        event = service.create_booking_event(
            event_type=event_type,
            event_label=event_label,
            traveler_id=booking.traveler_id or '',
            lead_id=booking.lead_id or '',
            booking_id=booking.booking_id,
            trip_id=booking.trip_id or '',
            interaction_id=booking.interaction_id or '',
            channel=booking.booking_source or 'system-ui',
            actor='system-ui',
            notes=notes,
        )
        service.sync_agent_write_to_sheet(
            traveler_id=booking.traveler_id or '',
            lead_id=booking.lead_id or '',
            interaction_id=booking.interaction_id or '',
            booking_id=booking.booking_id,
            trip_id=booking.trip_id or '',
            event_ids=[event['event_id']],
        )
    except Exception:
        # UI writes must not fail after the DB commit because sheet sync is retryable.
        pass
