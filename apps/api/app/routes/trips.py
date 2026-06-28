# app/routes/trips.py
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from app.models.trip import Trip
from app.models.booking import TripBooking
from app.extensions import db
from sqlalchemy import or_
import uuid
from datetime import datetime, date, timezone
from services.crm.system_services import UnifiedCRMService

trips_bp = Blueprint('trips', __name__, url_prefix='/trips')


def _trip_commercial_config(trip: Trip) -> dict:
    return UnifiedCRMService.parse_trip_sales_notes(trip.sales_notes or "")


def _compose_trip_sales_notes(data: dict, existing_trip: Trip | None = None) -> str:
    vip_offer = data.get('vip_discount_note', '')
    group_offer = data.get('group_discount_note', '')
    group_threshold_raw = data.get('group_discount_threshold', '')
    notes_body = data.get('sales_notes', '')
    try:
        group_threshold = max(int(group_threshold_raw or 4), 2)
    except Exception:
        group_threshold = 4
    return UnifiedCRMService.compose_trip_sales_notes(
        existing_notes=existing_trip.sales_notes if existing_trip else "",
        vip_offer=vip_offer,
        group_offer=group_offer,
        group_threshold=group_threshold,
        notes_body=notes_body,
    )


def _generate_trip_id(trip_type: str, year: int | None, trip_name: str) -> str:
    prefix = "RT-INT" if (trip_type or "").strip() == "International" else "RT-LOC"
    year_part = str(year % 100).zfill(2) if year else datetime.now(timezone.utc).strftime("%y")
    slug = "".join(ch for ch in (trip_name or "").upper() if ch.isalnum())[:3] or "TRP"
    return f"{prefix}-{year_part}-{slug}"


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
    trip = db.get_or_404(Trip, trip_id)
    commercial_config = _trip_commercial_config(trip)
    back_query = {
        key: value
        for key, value in (
            ("q", request.args.get("q", "").strip()),
            ("status", request.args.get("status", "").strip()),
            ("type", request.args.get("type", "").strip()),
        )
        if value
    }
    bookings = TripBooking.query.filter_by(trip_id=trip_id)\
        .order_by(TripBooking.draft_created_at.desc()).all()
    active_room_bookings = {
        room: count or 0
        for room, count in db.session.query(
            TripBooking.room_type,
            db.func.count(TripBooking.booking_id),
        )
        .filter(TripBooking.trip_id == trip_id)
        .filter(TripBooking.booking_status != "Cancelled")
        .group_by(TripBooking.room_type)
        .all()
    }
    room_counts = {
        "single_booked": active_room_bookings.get("Single", 0),
        "double_booked": active_room_bookings.get("Double", 0),
        "triple_booked": active_room_bookings.get("Triple", 0),
    }
    display_remaining = {
        "single": max((trip.single_remaining or 0) - room_counts["single_booked"], 0),
        "double": max((trip.double_remaining or 0) - room_counts["double_booked"], 0),
        "triple": max((trip.triple_remaining or 0) - room_counts["triple_booked"], 0),
    }
    return render_template(
        'trips/detail.html',
        trip=trip,
        commercial_config=commercial_config,
        bookings=bookings,
        room_counts=room_counts,
        display_remaining=display_remaining,
        back_query=back_query,
    )


@trips_bp.route('/', methods=['POST'])
def create():
    data = request.form.to_dict()

    # Auto-generate trip ID if blank
    trip_name = data.get('trip_name', '').strip()
    trip_id = data.get('trip_id', '').strip()
    trip_type = data.get('type', '').strip()
    trip_year = None
    try:
        trip_year = int(data.get('year')) if data.get('year') else None
    except Exception:
        trip_year = None
    if not trip_id:
        trip_id = _generate_trip_id(trip_type, trip_year, trip_name)
    trip_id = trip_id.upper()

    if db.session.get(Trip, trip_id):
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
        trip_name=trip_name,
        type=trip_type,
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
        boys_double=to_int(data.get('boys_double')),
        girls_double=to_int(data.get('girls_double')),
        boys_triple=to_int(data.get('boys_triple')),
        girls_triple=to_int(data.get('girls_triple')),
        public_price=data.get('public_price', ''),
        public_description=data.get('public_description', ''),
        sales_notes=_compose_trip_sales_notes(data),
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

    trip = db.get_or_404(Trip, trip_id)
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
    trip.boys_double = to_int(data.get('boys_double'))
    trip.girls_double = to_int(data.get('girls_double'))
    trip.boys_triple = to_int(data.get('boys_triple'))
    trip.girls_triple = to_int(data.get('girls_triple'))
    trip.public_price = data.get('public_price', trip.public_price)
    trip.public_description = data.get('public_description', trip.public_description)
    trip.sales_notes = _compose_trip_sales_notes(data, trip)

    db.session.commit()
    _sync_trip_after_commit(trip.trip_id)
    flash(f"Trip '{trip.trip_name}' updated.", 'success')
    return redirect(url_for('trips.detail', trip_id=trip_id))


def delete(trip_id):
    trip = db.get_or_404(Trip, trip_id)
    trip.sales_status = 'Cancelled'
    db.session.commit()
    _sync_trip_after_commit(trip.trip_id)
    flash(f"Trip '{trip.trip_name}' marked as Cancelled.", 'info')
    return redirect(url_for('trips.index'))


@trips_bp.route('/<string:trip_id>/inventory', methods=['POST'])
def update_inventory(trip_id):
    """Quick inventory adjustment via JSON API."""
    trip = db.get_or_404(Trip, trip_id)
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
