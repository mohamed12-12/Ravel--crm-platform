# app/routes/bookings.py
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from app.models.booking import TripBooking
from app.models.booking_event import BookingEventTrail
from app.models.traveler import Traveler
from app.models.trip import Trip
from app.extensions import db
from sqlalchemy import or_
from datetime import datetime
import uuid
from services.crm.system_services import UnifiedCRMService

bookings_bp = Blueprint('bookings', __name__, url_prefix='/bookings')

BOOKING_STATUSES = ['Draft', 'Confirmed', 'Cancelled']
PAYMENT_STATUSES = ['Pending', 'Deposit Paid', 'Fully Paid', 'Refunded']


@bookings_bp.route('/')
def index():
    q = request.args.get('q', '')
    status = request.args.get('status', '')
    page = request.args.get('page', 1, type=int)
    per_page = 25

    query = TripBooking.query
    if q:
        query = query.filter(or_(
            TripBooking.traveler_name.ilike(f'%{q}%'),
            TripBooking.booking_id.ilike(f'%{q}%'),
            TripBooking.trip_name.ilike(f'%{q}%'),
        ))
    if status:
        query = query.filter(TripBooking.booking_status == status)

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
                           statuses=BOOKING_STATUSES,
                           counts=counts,
                           trips_data=trips_data)


@bookings_bp.route('/<string:booking_id>')
def detail(booking_id):
    booking = TripBooking.query.get_or_404(booking_id)
    traveler = Traveler.query.get(booking.traveler_id) if booking.traveler_id else None
    trip = Trip.query.get(booking.trip_id) if booking.trip_id else None
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
    return render_template('bookings/detail.html',
                           booking=booking,
                           traveler=traveler,
                           trip=trip,
                           event_trail=event_trail,
                           booking_statuses=BOOKING_STATUSES,
                           payment_statuses=PAYMENT_STATUSES)


@bookings_bp.route('/', methods=['POST'])
def create():
    data = request.form.to_dict()
    
    trip_id = data.get('trip_id')
    traveler_id = data.get('traveler_id')
    room_type = data.get('room_type')
    
    if not trip_id or not traveler_id or not room_type:
        flash("Trip, Traveler, and Room Type are required to create a booking.", "error")
        return redirect(url_for('bookings.index'))

    traveler = Traveler.query.get(traveler_id)
    if not traveler:
        flash(f"Traveler '{traveler_id}' was not found.", "error")
        return redirect(url_for('bookings.index'))
        
    try:
        service = UnifiedCRMService()
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
            booking_notes=data.get('booking_notes', ''),
        )
        booking_id = result["booking_id"]
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
    booking = TripBooking.query.get_or_404(booking_id)
    data = request.get_json(silent=True) or request.form.to_dict()
    new_status = data.get('booking_status')
    new_payment = data.get('payment_status')
    event_notes = []

    if new_status and new_status in BOOKING_STATUSES:
        booking.booking_status = new_status
        event_notes.append(f"Booking status: {new_status}")
    if new_payment and new_payment in PAYMENT_STATUSES:
        booking.payment_status = new_payment
        event_notes.append(f"Payment status: {new_payment}")
    if data.get('booking_notes'):
        booking.booking_notes = data['booking_notes']
        event_notes.append(data['booking_notes'])

    db.session.commit()
    if event_notes:
        _sync_booking_event(
            booking,
            event_type='payment_follow_up' if new_payment else 'booking_status_updated',
            event_label='Payment / follow-up updated' if new_payment else 'Booking status updated',
            notes=' | '.join(event_notes),
        )

    if request.is_json:
        return jsonify({'status': 'ok', 'booking': booking.to_dict()})
    flash('Booking updated.', 'success')
    return redirect(url_for('bookings.detail', booking_id=booking_id))


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
