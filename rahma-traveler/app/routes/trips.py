# app/routes/trips.py
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from app.models.trip import Trip
from app.models.booking import TripBooking
from app.extensions import db
from sqlalchemy import or_
import uuid
from datetime import datetime, date
from system_services import UnifiedCRMService

trips_bp = Blueprint('trips', __name__, url_prefix='/trips')


def _sync_trip_after_commit(trip_id: str) -> None:
    try:
        UnifiedCRMService().sync_trip_to_sheet(trip_id)
    except Exception as exc:
        flash(f"Trip saved in the system, but sheet sync needs attention: {exc}", "warning")


@trips_bp.route('/')
def index():
    q = request.args.get('q', '')
    status = request.args.get('status', '')
    trip_type = request.args.get('type', '')

    query = Trip.query
    if q:
        query = query.filter(or_(
            Trip.trip_name.ilike(f'%{q}%'),
            Trip.trip_id.ilike(f'%{q}%'),
            Trip.trip_leader.ilike(f'%{q}%'),
        ))
    if status:
        query = query.filter(Trip.sales_status == status)
    if trip_type:
        query = query.filter(Trip.type == trip_type)

    trips = query.order_by(Trip.start_date.desc()).all()

    statuses = [r[0] for r in db.session.query(Trip.sales_status).distinct().all() if r[0]]
    types = [r[0] for r in db.session.query(Trip.type).distinct().all() if r[0]]

    return render_template('trips/index.html',
                           trips=trips,
                           q=q,
                           current_status=status,
                           current_type=trip_type,
                           statuses=statuses,
                           types=types)


@trips_bp.route('/<string:trip_id>')
def detail(trip_id):
    trip = Trip.query.get_or_404(trip_id)
    bookings = TripBooking.query.filter_by(trip_id=trip_id)\
        .order_by(TripBooking.draft_created_at.desc()).all()
    return render_template('trips/detail.html', trip=trip, bookings=bookings)


@trips_bp.route('/', methods=['POST'])
def create():
    data = request.form.to_dict()

    # Auto-generate trip ID if blank
    trip_id = data.get('trip_id', '').strip()
    if not trip_id:
        trip_id = f"TR-{uuid.uuid4().hex[:6].upper()}"

    if Trip.query.get(trip_id):
        flash(f"Trip ID '{trip_id}' already exists.", 'error')
        return redirect(url_for('trips.index'))

    def to_date(v):
        if not v:
            return None
        try:
            return datetime.strptime(v, '%Y-%m-%d').date()
        except Exception:
            return None

    def to_int(v):
        try:
            return int(v) if v else 0
        except Exception:
            return 0

    trip = Trip(
        trip_id=trip_id,
        trip_name=data.get('trip_name', ''),
        type=data.get('type', ''),
        year=to_int(data.get('year')),
        trip_leader=data.get('trip_leader', ''),
        start_date=to_date(data.get('start_date')),
        end_date=to_date(data.get('end_date')),
        sales_status=data.get('sales_status', 'Open'),
        single_total=to_int(data.get('single_total')),
        double_total=to_int(data.get('double_total')),
        triple_total=to_int(data.get('triple_total')),
        single_remaining=to_int(data.get('single_remaining')),
        double_remaining=to_int(data.get('double_remaining')),
        triple_remaining=to_int(data.get('triple_remaining')),
        public_price=data.get('public_price', ''),
        public_description=data.get('public_description', ''),
        sales_notes=data.get('sales_notes', ''),
    )
    db.session.add(trip)
    db.session.commit()
    _sync_trip_after_commit(trip.trip_id)
    flash(f"Trip '{trip.trip_name}' created successfully.", 'success')
    return redirect(url_for('trips.detail', trip_id=trip.trip_id))


@trips_bp.route('/<string:trip_id>', methods=['POST'])
def update(trip_id):
    """Handles the edit form POST (method override via _method field)."""
    method_override = request.form.get('_method', '').upper()
    if method_override == 'DELETE':
        return delete(trip_id)

    trip = Trip.query.get_or_404(trip_id)
    data = request.form.to_dict()

    def to_date(v):
        if not v:
            return None
        try:
            return datetime.strptime(v, '%Y-%m-%d').date()
        except Exception:
            return None

    def to_int(v):
        try:
            return int(v) if v else 0
        except Exception:
            return 0

    trip.trip_name = data.get('trip_name', trip.trip_name)
    trip.type = data.get('type', trip.type)
    trip.trip_leader = data.get('trip_leader', trip.trip_leader)
    trip.start_date = to_date(data.get('start_date')) or trip.start_date
    trip.end_date = to_date(data.get('end_date')) or trip.end_date
    trip.sales_status = data.get('sales_status', trip.sales_status)
    trip.single_total = to_int(data.get('single_total'))
    trip.double_total = to_int(data.get('double_total'))
    trip.triple_total = to_int(data.get('triple_total'))
    trip.single_remaining = to_int(data.get('single_remaining'))
    trip.double_remaining = to_int(data.get('double_remaining'))
    trip.triple_remaining = to_int(data.get('triple_remaining'))
    trip.public_price = data.get('public_price', trip.public_price)
    trip.public_description = data.get('public_description', trip.public_description)
    trip.sales_notes = data.get('sales_notes', trip.sales_notes)

    db.session.commit()
    _sync_trip_after_commit(trip.trip_id)
    flash(f"Trip '{trip.trip_name}' updated.", 'success')
    return redirect(url_for('trips.detail', trip_id=trip_id))


def delete(trip_id):
    trip = Trip.query.get_or_404(trip_id)
    trip.sales_status = 'Cancelled'
    db.session.commit()
    _sync_trip_after_commit(trip.trip_id)
    flash(f"Trip '{trip.trip_name}' marked as Cancelled.", 'info')
    return redirect(url_for('trips.index'))


@trips_bp.route('/<string:trip_id>/inventory', methods=['POST'])
def update_inventory(trip_id):
    """Quick inventory adjustment via JSON API."""
    trip = Trip.query.get_or_404(trip_id)
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400

    for field in ['single_remaining', 'double_remaining', 'triple_remaining',
                  'single_total', 'double_total', 'triple_total']:
        if field in data:
            try:
                setattr(trip, field, int(data[field]))
            except (ValueError, TypeError):
                pass

    db.session.commit()
    _sync_trip_after_commit(trip.trip_id)
    return jsonify({'status': 'ok', 'trip': trip.to_dict()})
