# app/routes/travelers.py
import re
from datetime import datetime
import csv
import io

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, Response
from sqlalchemy import or_

from app.extensions import db
from app.models.booking import TripBooking, CEBooking
from app.models.booking_event import BookingEventTrail
from app.models.handoff import HandoffQueue
from app.models.interaction import Interaction
from app.models.lead import Lead
from app.models.traveler import Traveler
from services.crm.system_services import UnifiedCRMService
from services.crm.system_services.phone_normalization import normalize_phone_input

travelers_bp = Blueprint('travelers', __name__, url_prefix='/travelers')
ARCHIVE_LIKE_STATUSES = {"inactive", "archived", "blacklisted", "blocked"}


def _phone_match_filter(service: UnifiedCRMService, phone_info: dict[str, str]):
    lookup_keys = service.lookup_key_variants(phone_info)
    clauses = [
        Traveler.normalized_whatsapp == phone_info.get("normalized_whatsapp"),
        Traveler.integrated_whatsapp == phone_info.get("normalized_whatsapp"),
        Traveler.whatsapp_raw == phone_info.get("local_number"),
        Traveler.whatsapp_raw == phone_info.get("raw_phone"),
    ]
    if lookup_keys:
        clauses.append(Traveler.phone_lookup_key.in_(lookup_keys))
    return or_(*clauses)

TRAVELER_SEARCH_FIELDS = (
    Traveler.traveler_id,
    Traveler.full_name,
    Traveler.whatsapp_raw,
    Traveler.normalized_whatsapp,
    Traveler.integrated_whatsapp,
    Traveler.email,
    Traveler.nationality,
    Traveler.residence,
    Traveler.lead_source,
    Traveler.notes,
    Traveler.agent_notes,
    Traveler.phone_lookup_key,
    Traveler.primary_language,
)


def _apply_traveler_filters(query, q: str, status: str, nationality: str):
    query = query.filter(
        or_(
            Traveler.full_name.isnot(None),
            Traveler.whatsapp_raw.isnot(None),
            Traveler.integrated_whatsapp.isnot(None),
            Traveler.normalized_whatsapp.isnot(None),
            Traveler.email.isnot(None),
            Traveler.phone_lookup_key.isnot(None),
        )
    ).filter(
        or_(
            Traveler.full_name != "",
            Traveler.whatsapp_raw != "",
            Traveler.integrated_whatsapp != "",
            Traveler.normalized_whatsapp != "",
            Traveler.email != "",
            Traveler.phone_lookup_key != "",
        )
    )
    if q:
        term = f"%{q}%"
        query = query.filter(or_(*[field.ilike(term) for field in TRAVELER_SEARCH_FIELDS]))
    normalized_status = (status or "").strip().lower()
    if normalized_status == "archived":
        query = query.filter(db.func.lower(Traveler.status).in_(ARCHIVE_LIKE_STATUSES))
    elif normalized_status:
        query = query.filter(db.func.lower(Traveler.status) == normalized_status)
    else:
        query = query.filter(~db.func.lower(Traveler.status).in_(ARCHIVE_LIKE_STATUSES))
    if nationality:
        query = query.filter(Traveler.nationality == nationality)
    return query


def _parse_rating(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value):
    if not value:
        return None
    if hasattr(value, "isoformat"):
        return value
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except (TypeError, ValueError):
            continue
    return None


def _traveler_update_payload(data):
    payload = {}
    for key in (
        "full_name",
        "status",
        "whatsapp_raw",
        "integrated_whatsapp",
        "normalized_whatsapp",
        "email",
        "residence",
        "nationality",
        "lead_source",
        "primary_language",
        "gender",
        "room_preference",
        "emergency_contact",
        "emergency_phone",
        "medical_notes",
        "notes",
        "introduce_yourself",
        "agent_notes",
        "community_whatsapp",
        "phone_code",
        "phone_lookup_key",
        "lifetime_revenue",
    ):
        if key in data and data.get(key) is not None:
            # Handle float casting for lifetime_revenue
            if key == "lifetime_revenue":
                try:
                    payload[key] = float(data.get(key))
                except (ValueError, TypeError):
                    payload[key] = 0.0
            else:
                payload[key] = data.get(key)
    if "birthday" in data:
        payload["birthday"] = _parse_date(data.get("birthday"))
    if "rating" in data:
        payload["rating"] = _parse_rating(data.get("rating"))
    return payload


def _sync_traveler_sheet(traveler_id: str) -> None:
    try:
        UnifiedCRMService().sync_record_to_sheet("Travelers", traveler_id)
    except Exception:
        pass


def _refresh_traveler_sheet_stats(traveler_ids) -> None:
    ids = [str(traveler_id or "").strip() for traveler_id in traveler_ids if str(traveler_id or "").strip()]
    if not ids:
        return
    try:
        refreshed = UnifiedCRMService().refresh_travelers_sheet_stats(ids)
        if refreshed:
            db.session.expire_all()
    except Exception:
        pass

@travelers_bp.route('/')
def index():
    q = request.args.get('q', '').strip()
    status = request.args.get('status', '')
    nationality = request.args.get('nationality', '')
    page = request.args.get('page', 1, type=int)
    per_page = 20

    query = _apply_traveler_filters(Traveler.query, q, status, nationality)

    pagination = query.order_by(Traveler.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    travelers = pagination.items
    traveler_ids = [traveler.traveler_id for traveler in travelers]
    _refresh_traveler_sheet_stats(traveler_ids)
    if traveler_ids:
        fresh_travelers = Traveler.query.filter(Traveler.traveler_id.in_(traveler_ids)).all()
        fresh_by_id = {traveler.traveler_id: traveler for traveler in fresh_travelers}
        travelers = [fresh_by_id.get(traveler_id) for traveler_id in traveler_ids if fresh_by_id.get(traveler_id)]

    # Filter options for the UI
    statuses = db.session.query(Traveler.status).distinct().all()
    nationalities = db.session.query(Traveler.nationality).distinct().all()

    return render_template('travelers/index.html', 
                           travelers=travelers, 
                           pagination=pagination,
                           total_count=pagination.total,
                           statuses=[s[0] for s in statuses if s[0]],
                           nationalities=[n[0] for n in nationalities if n[0]],
                           q=q,
                           current_status=status,
                           current_nationality=nationality)

@travelers_bp.route('/<traveler_id>')
def detail(traveler_id):
    try:
        UnifiedCRMService().refresh_traveler_sheet_stats(traveler_id)
    except Exception:
        pass
    traveler = Traveler.query.get_or_404(traveler_id)
    try:
        UnifiedCRMService().ensure_operational_schema()
    except Exception:
        pass
    leads = Lead.query.filter_by(traveler_id=traveler_id).order_by(Lead.created_at.desc()).all()
    bookings = TripBooking.query.filter_by(traveler_id=traveler_id).order_by(TripBooking.draft_created_at.desc()).all()
    ce_bookings = CEBooking.query.filter_by(traveler_id=traveler_id).order_by(CEBooking.created_at.desc()).all()
    interactions = Interaction.query.filter_by(traveler_id=traveler_id).order_by(Interaction.timestamp.desc()).all()
    handoffs = HandoffQueue.query.filter_by(traveler_id=traveler_id).order_by(HandoffQueue.created_at.desc()).all()
    event_filters = [BookingEventTrail.traveler_id == traveler_id]
    lead_ids = [lead.lead_id for lead in leads if lead.lead_id]
    booking_ids = [booking.booking_id for booking in bookings if booking.booking_id]
    if lead_ids:
        event_filters.append(BookingEventTrail.lead_id.in_(lead_ids))
    if booking_ids:
        event_filters.append(BookingEventTrail.booking_id.in_(booking_ids))
    event_trail = BookingEventTrail.query.filter(or_(*event_filters)).order_by(BookingEventTrail.occurred_at.asc()).limit(100).all()

    # Build trip-type lookup for bookings
    from app.models.trip import Trip
    trip_type_map = {}
    trip_ids = [b.trip_id for b in bookings if b.trip_id]
    if trip_ids:
        trips = Trip.query.filter(Trip.trip_id.in_(trip_ids)).all()
        trip_type_map = {t.trip_id: t.type for t in trips}

    return render_template(
        'travelers/detail.html',
        traveler=traveler,
        leads=leads,
        bookings=bookings,
        ce_bookings=ce_bookings,
        trip_type_map=trip_type_map,
        interactions=interactions,
        handoffs=handoffs,
        event_trail=event_trail,
    )

@travelers_bp.route('/', methods=['POST'])
def create():
    service = UnifiedCRMService()
    data = request.form.to_dict()
    whatsapp_raw = data.get('whatsapp_raw', '').strip()
    phone_code = data.get('phone_code', '').strip()

    # Normalize phone using shared helper logic
    egypt_local = bool(re.fullmatch(r"0?(10|11|12|15)\d{8}", re.sub(r"\D", "", whatsapp_raw or "")))
    default_country_code = phone_code or ("20" if egypt_local else "")
    phone = normalize_phone_input(
        whatsapp_raw,
        default_country_code,
        default_country_is_explicit=bool(default_country_code),
    ).to_dict()
    phone["raw_phone"] = whatsapp_raw
    lookup_key = service.normalize_phone(whatsapp_raw, phone.get("country_code", "")).get("lookup_key")
    normalized_whatsapp = phone.get("normalized_e164")
    local_number = phone.get("local_number")

    if lookup_key:
        # 1. Check if the phone number belongs to a blacklisted traveler
        blacklisted_traveler = Traveler.query.filter(
            or_(
                db.func.trim(Traveler.status).ilike("blacklisted"),
                db.func.trim(Traveler.status).ilike("blacklist")
            )
        ).filter(_phone_match_filter(service, phone)).first()

        if blacklisted_traveler:
            flash(f"Error: Phone number '{whatsapp_raw}' is blacklisted (Traveler {blacklisted_traveler.traveler_id} is Blacklisted). Traveler profile was not saved.", "danger")
            return redirect(url_for('travelers.index'))

        # 2. Check if a traveler already exists with this number (Deduplication)
        existing_traveler = Traveler.query.filter(
            or_(
                Traveler.phone_lookup_key == lookup_key,
                Traveler.normalized_whatsapp == normalized_whatsapp,
                Traveler.integrated_whatsapp == normalized_whatsapp,
                Traveler.whatsapp_raw == local_number,
                Traveler.whatsapp_raw == whatsapp_raw
            )
        ).first()

        if existing_traveler:
            flash(f"Note: Traveler with phone number '{whatsapp_raw}' already exists as {existing_traveler.traveler_id} (Status: {existing_traveler.status}). Redirecting to profile.", "warning")
            return redirect(url_for('travelers.detail', traveler_id=existing_traveler.traveler_id))

    new_id = service.next_traveler_id()
    new_traveler = Traveler(
        traveler_id=new_id,
        status=data.get('status', 'New') or 'New',
        full_name=data.get('full_name', '').strip(),
        birthday=_parse_date(data.get('birthday')),
        gender=data.get('gender') or None,
        nationality=data.get('nationality') or None,
        phone_code=phone['country_code'] or None,
        whatsapp_raw=phone['local_number'] or data.get('whatsapp_raw') or None,
        integrated_whatsapp=phone['normalized_whatsapp'] or None,
        normalized_whatsapp=phone['normalized_whatsapp'] or None,
        phone_lookup_key=phone['lookup_key'] or None,
        email=data.get('email') or None,
        residence=data.get('residence') or None,
        lead_source=data.get('lead_source') or None,
        primary_language=data.get('primary_language') or None,
        room_preference=data.get('room_preference') or None,
        rating=_parse_rating(data.get('rating')) or None,
        emergency_contact=data.get('emergency_contact') or None,
        emergency_phone=data.get('emergency_phone') or None,
        medical_notes=data.get('medical_notes') or None,
        notes=data.get('notes') or None,
        introduce_yourself=data.get('introduce_yourself') or None,
        agent_notes=data.get('agent_notes') or None,
        community_whatsapp=data.get('community_whatsapp') or None,
        last_contacted_at=datetime.utcnow(),
    )
    db.session.add(new_traveler)
    db.session.commit()
    _sync_traveler_sheet(new_id)
    flash(f"Traveler {new_id} created successfully!", "success")
    return redirect(url_for('travelers.index'))

@travelers_bp.route('/<traveler_id>', methods=['PUT', 'POST'])
def update(traveler_id):
    traveler = Traveler.query.get_or_404(traveler_id)
    data = request.get_json(silent=True) or request.form.to_dict()
    updates = _traveler_update_payload(data)
    for key, value in updates.items():
        if hasattr(traveler, key) and key != 'traveler_id':
            setattr(traveler, key, value)
    if 'whatsapp_raw' in data or 'phone_code' in data:
        new_raw = data.get('whatsapp_raw', '').strip() or traveler.whatsapp_raw or ''
        new_code = data.get('phone_code', '').strip() or traveler.phone_code or ''
        service = UnifiedCRMService()
        egypt_local = bool(re.fullmatch(r"0?(10|11|12|15)\d{8}", re.sub(r"\D", "", new_raw or "")))
        default_country_code = new_code or ("20" if egypt_local else "")
        normalized = normalize_phone_input(
            new_raw,
            default_country_code,
            default_country_is_explicit=bool(default_country_code),
        ).to_dict()
        normalized["raw_phone"] = new_raw
        lookup_key = service.normalize_phone(new_raw, normalized.get("country_code", "")).get("lookup_key")
        normalized_whatsapp = normalized['normalized_e164']
        local_number = normalized['local_number']

        if lookup_key and lookup_key != traveler.phone_lookup_key:
            duplicate_traveler = Traveler.query.filter(
                Traveler.traveler_id != traveler_id,
                or_(
                    Traveler.phone_lookup_key == lookup_key,
                    Traveler.normalized_whatsapp == normalized_whatsapp,
                    Traveler.integrated_whatsapp == normalized_whatsapp,
                    Traveler.whatsapp_raw == local_number,
                    Traveler.whatsapp_raw == new_raw,
                )
            ).first()
            if duplicate_traveler:
                message = (
                    f"Phone number '{new_raw}' already belongs to traveler "
                    f"{duplicate_traveler.traveler_id} ({duplicate_traveler.full_name})."
                )
                if request.is_json:
                    return jsonify({"status": "error", "error": message, "duplicate_traveler_id": duplicate_traveler.traveler_id}), 400
                flash(message, "danger")
                return redirect(url_for('travelers.detail', traveler_id=traveler_id))

            blacklisted_traveler = Traveler.query.filter(
                or_(
                    db.func.trim(Traveler.status).ilike("blacklisted"),
                    db.func.trim(Traveler.status).ilike("blacklist")
                )
            ).filter(_phone_match_filter(service, normalized)).first()

            if blacklisted_traveler:
                if request.is_json:
                    return jsonify({"status": "error", "error": f"Phone number '{new_raw}' belongs to a blacklisted traveler."}), 400
                flash(f"Error: Phone number '{new_raw}' is blacklisted (Traveler {blacklisted_traveler.traveler_id} is Blacklisted). Traveler phone was not updated.", "danger")
                return redirect(url_for('travelers.detail', traveler_id=traveler_id))

        traveler.phone_code = normalized['country_code'] or traveler.phone_code
        traveler.whatsapp_raw = normalized['local_number'] or new_raw or traveler.whatsapp_raw
        traveler.integrated_whatsapp = normalized['normalized_e164'] or traveler.integrated_whatsapp
        traveler.normalized_whatsapp = normalized['normalized_e164'] or traveler.normalized_whatsapp
        traveler.phone_lookup_key = lookup_key or traveler.phone_lookup_key
    traveler.last_contacted_at = datetime.utcnow()
    db.session.commit()
    _sync_traveler_sheet(traveler_id)
    if request.is_json:
        return jsonify({"status": "success", "message": "Traveler updated", "traveler_id": traveler_id})
    flash("Traveler profile updated.", "success")
    return redirect(url_for('travelers.detail', traveler_id=traveler_id))

@travelers_bp.route('/<traveler_id>', methods=['DELETE'])
def delete(traveler_id):
    traveler = Traveler.query.get_or_404(traveler_id)
    traveler.status = 'Inactive'
    traveler.last_contacted_at = datetime.utcnow()
    db.session.commit()
    _sync_traveler_sheet(traveler_id)
    return jsonify({"status": "success", "message": "Traveler marked as Inactive"})

@travelers_bp.route('/export')
def export():
    q = request.args.get('q', '').strip()
    status = request.args.get('status', '')
    nationality = request.args.get('nationality', '')

    query = _apply_traveler_filters(Traveler.query, q, status, nationality)

    travelers = query.all()
    _refresh_traveler_sheet_stats([traveler.traveler_id for traveler in travelers])
    if travelers:
        traveler_ids = [traveler.traveler_id for traveler in travelers]
        fresh_travelers = Traveler.query.filter(Traveler.traveler_id.in_(traveler_ids)).all()
        fresh_by_id = {traveler.traveler_id: traveler for traveler in fresh_travelers}
        travelers = [fresh_by_id.get(traveler_id) for traveler_id in traveler_ids if fresh_by_id.get(traveler_id)]

    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(['ID', 'Name', 'Phone', 'Email', 'Status', 'Nationality', 'Residence', 'Lead Source', 'Total Trips', 'Revenue', 'Rating'])
    for t in travelers:
        cw.writerow([
            t.traveler_id,
            t.full_name,
            t.whatsapp_raw or t.normalized_whatsapp,
            t.email,
            t.status,
            t.nationality,
            t.residence,
            t.lead_source,
            t.total_trips,
            t.lifetime_revenue,
            t.rating,
        ])

    output = si.getvalue()
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=travelers_export.csv"}
    )
