# app/routes/travelers.py
import re
from pathlib import Path
from datetime import datetime, timezone
import csv
import io
import shutil

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, Response, current_app, send_file, abort
from werkzeug.utils import secure_filename
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.booking import TripBooking, CEBooking
from app.models.booking_event import BookingEventTrail
from app.models.booking_status_history import BookingStatusHistory
from app.models.handoff import HandoffQueue
from app.models.interaction import Interaction
from app.models.lead import Lead
from app.models.traveler import Traveler
from app.models.traveler_document import TravelerDocument
from services.crm.system_services import UnifiedCRMService
from services.crm.system_services.phone_normalization import normalize_phone_input
from services.data_authority import load_data_authority
from app.security import can_view_all_records, current_user_id, has_permission

travelers_bp = Blueprint('travelers', __name__, url_prefix='/travelers')
ARCHIVE_LIKE_STATUSES = {"inactive", "archived", "blacklisted", "blocked"}
ARCHIVED_FILTER_STATUSES = {"inactive", "archived"}
BLACKLIST_FILTER_STATUSES = {"blacklisted", "blocked"}
_ALLOWED_DOC_EXTENSIONS = {"jpg", "jpeg", "png", "pdf"}
_ALLOWED_DOC_MIME_TYPES = {"image/jpeg", "image/png", "application/pdf"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _build_revenue_summary(lifetime_revenue_usd: float | int | None, preferred_currency: str, usd_to_egp_rate: float) -> dict[str, str]:
    usd_value = float(lifetime_revenue_usd or 0.0)
    rate = float(usd_to_egp_rate or 0.0)
    egp_value = usd_value * rate if rate > 0 else 0.0
    preferred = str(preferred_currency or "USD").strip().upper()
    if preferred == "EGP":
        primary = {"label": "Lifetime Revenue (EGP)", "value": f"EGP {egp_value:,.2f}"}
        secondary = {"label": "Lifetime Revenue (USD)", "value": f"${usd_value:,.2f}"}
    else:
        primary = {"label": "Lifetime Revenue (USD)", "value": f"${usd_value:,.2f}"}
        secondary = {"label": "Lifetime Revenue (EGP)", "value": f"EGP {egp_value:,.2f}"}
    return {
        "primary_label": primary["label"],
        "primary_value": primary["value"],
        "secondary_label": secondary["label"],
        "secondary_value": secondary["value"],
        "exchange_note": f"1 USD = {rate:,.2f} EGP" if rate > 0 else "",
    }


def _allowed_document(filename: str, mimetype: str = "") -> bool:
    return (
        "." in filename
        and filename.rsplit('.', 1)[1].lower() in _ALLOWED_DOC_EXTENSIONS
        and (not mimetype or mimetype.lower() in _ALLOWED_DOC_MIME_TYPES)
    )


def _documents_root() -> Path:
    root = current_app.config.get('TRAVELER_UPLOAD_ROOT')
    if root:
        return Path(root)
    return Path(current_app.instance_path) / 'uploads' / 'travelers'


def _safe_document_path(base_dir: Path, filename: str) -> Path:
    safe_name = secure_filename(filename)
    if not safe_name:
        raise ValueError('Invalid filename')
    dest = (base_dir / safe_name).resolve()
    base_resolved = base_dir.resolve()
    if base_resolved not in dest.parents and dest != base_resolved:
        raise ValueError('Unsafe upload path')
    return dest


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
        query = query.filter(db.func.lower(Traveler.status).in_(ARCHIVED_FILTER_STATUSES))
    elif normalized_status == "blacklisted":
        query = query.filter(db.func.lower(Traveler.status).in_(BLACKLIST_FILTER_STATUSES))
    elif normalized_status == "blocked":
        query = query.filter(db.func.lower(Traveler.status).in_(BLACKLIST_FILTER_STATUSES))
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
        "preferred_currency",
        "lifetime_revenue",
        "passport_name",
        "passport_number",
        "passport_expiry",
        "passport_nationality",
        "passport_attachment_ref",
    ):
        if key in data and data.get(key) is not None:
            # Handle float casting for lifetime_revenue
            if key == "lifetime_revenue":
                try:
                    payload[key] = float(data.get(key))
                except (ValueError, TypeError):
                    payload[key] = 0.0
            elif key == "preferred_currency":
                payload[key] = str(data.get(key) or "").strip().upper() or None
            else:
                payload[key] = data.get(key)
    if "birthday" in data:
        payload["birthday"] = _parse_date(data.get("birthday"))
    if "rating" in data:
        payload["rating"] = _parse_rating(data.get("rating"))
    if "passport_expiry" in data:
        payload["passport_expiry"] = _parse_date(data.get("passport_expiry"))
    return payload


def _sync_traveler_sheet(traveler_id: str) -> None:
    try:
        UnifiedCRMService().sync_record_to_sheet("Travelers", traveler_id)
    except Exception:
        pass


def _remove_sheet_record(mapping_name: str, record_id: str) -> None:
    try:
        UnifiedCRMService().remove_record_from_sheet(mapping_name, record_id)
    except Exception:
        pass


def _delete_traveler_document_files(documents: list[TravelerDocument]) -> None:
    docs_root = _documents_root().resolve()
    for document in documents:
        try:
            storage_path = Path(document.storage_path)
            if not storage_path.is_absolute():
                storage_path = (_documents_root() / storage_path).resolve()
            else:
                storage_path = storage_path.resolve()
            if docs_root not in storage_path.parents and storage_path != docs_root:
                continue
            if storage_path.exists():
                storage_path.unlink()
        except OSError:
            continue


def _delete_traveler_document_directory(traveler_id: str) -> None:
    docs_root = _documents_root().resolve()
    traveler_dir = (docs_root / traveler_id).resolve()
    if docs_root not in traveler_dir.parents:
        return
    shutil.rmtree(traveler_dir, ignore_errors=True)


def _restrict_travelers_to_viewable(query):
    """Mirror detail()'s visibility rule for the list view too: a traveler
    with no leads at all, or with any lead that is unclaimed or assigned to
    the current user, stays visible; a traveler whose every lead belongs to
    someone else does not. Bookings are not part of this rule, matching the
    existing detail() check, which only ever consulted lead ownership.
    """
    if has_permission('view_all'):
        return query
    uid = current_user_id()
    leads_with_traveler = db.session.query(Lead.traveler_id).filter(Lead.traveler_id.isnot(None))
    viewable_leads = leads_with_traveler.filter(
        or_(Lead.assigned_to_user_id.is_(None), Lead.assigned_to_user_id == uid)
    )
    return query.filter(or_(
        ~Traveler.traveler_id.in_(leads_with_traveler),
        Traveler.traveler_id.in_(viewable_leads),
    ))


@travelers_bp.route('/')
def index():
    q = request.args.get('q', '').strip()
    status = request.args.get('status', '')
    nationality = request.args.get('nationality', '')
    page = request.args.get('page', 1, type=int)
    per_page = 20

    query = _apply_traveler_filters(Traveler.query, q, status, nationality)
    query = _restrict_travelers_to_viewable(query)

    pagination = query.order_by(Traveler.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    travelers = pagination.items
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
        UnifiedCRMService().recalculate_traveler_stats(traveler_id)
    except Exception:
        pass
    traveler = db.get_or_404(Traveler, traveler_id)
    try:
        UnifiedCRMService().ensure_operational_schema()
    except Exception:
        pass
    leads = Lead.query.filter_by(traveler_id=traveler_id).order_by(Lead.created_at.desc()).all()
    if not can_view_all_records():
        lead_owners = {lead.assigned_to_user_id for lead in leads}
        owns_or_unclaimed = not lead_owners or None in lead_owners or current_user_id() in lead_owners
        if not owns_or_unclaimed:
            abort(403)
    bookings = TripBooking.query.filter_by(traveler_id=traveler_id).order_by(TripBooking.draft_created_at.desc()).all()
    ce_bookings = CEBooking.query.filter_by(traveler_id=traveler_id).order_by(CEBooking.created_at.desc()).all()
    documents = TravelerDocument.query.filter_by(traveler_id=traveler_id).order_by(TravelerDocument.uploaded_at.desc()).all()
    docs_root = _documents_root().resolve()
    passport_documents = []
    for doc in documents:
        is_passport = (doc.category or "").strip().lower() == "passport"
        doc_path = Path(doc.storage_path)
        if not doc_path.is_absolute():
            doc_path = (_documents_root() / doc_path).resolve()
        else:
            doc_path = doc_path.resolve()
        doc_exists = (docs_root in doc_path.parents or doc_path == docs_root) and doc_path.exists()
        if is_passport:
            passport_documents.append(
                {
                    "document": doc,
                    "exists": doc_exists,
                }
            )
    passport_document = passport_documents[0]["document"] if passport_documents else None
    passport_document_exists = passport_documents[0]["exists"] if passport_documents else False
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
    revenue_summary = _build_revenue_summary(
        traveler.lifetime_revenue,
        traveler.preferred_currency or "",
        current_app.config.get("USD_TO_EGP_RATE", 50.0),
    )

    return render_template(
        'travelers/detail.html',
        traveler=traveler,
        revenue_summary=revenue_summary,
        leads=leads,
        bookings=bookings,
        ce_bookings=ce_bookings,
        documents=documents,
        passport_document=passport_document,
        passport_documents=passport_documents,
        passport_document_exists=passport_document_exists,
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
        preferred_currency=(data.get('preferred_currency') or '').strip().upper() or None,
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
        last_contacted_at=datetime.now(timezone.utc),
    )
    db.session.add(new_traveler)
    db.session.commit()
    _sync_traveler_sheet(new_id)
    flash(f"Traveler {new_id} created successfully!", "success")
    return redirect(url_for('travelers.index'))

@travelers_bp.route('/<traveler_id>', methods=['PUT', 'POST'])
def update(traveler_id):
    traveler = db.get_or_404(Traveler, traveler_id)
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
    traveler.last_contacted_at = datetime.now(timezone.utc)
    db.session.commit()
    _sync_traveler_sheet(traveler_id)
    if request.is_json:
        return jsonify({"status": "success", "message": "Traveler updated", "traveler_id": traveler_id})
    flash("Traveler profile updated.", "success")
    return redirect(url_for('travelers.detail', traveler_id=traveler_id))

@travelers_bp.route('/<traveler_id>', methods=['DELETE'])
def delete(traveler_id):
    traveler = db.get_or_404(Traveler, traveler_id)
    lead_ids = [row[0] for row in db.session.query(Lead.lead_id).filter(Lead.traveler_id == traveler_id).all() if row[0]]
    interaction_ids = [row[0] for row in db.session.query(Interaction.interaction_id).filter(Interaction.traveler_id == traveler_id).all() if row[0]]
    booking_ids = [row[0] for row in db.session.query(TripBooking.booking_id).filter(TripBooking.traveler_id == traveler_id).all() if row[0]]
    documents = TravelerDocument.query.filter_by(traveler_id=traveler_id).all()

    _delete_traveler_document_files(documents)
    _delete_traveler_document_directory(traveler_id)

    event_filters = [BookingEventTrail.traveler_id == traveler_id]
    handoff_filters = [HandoffQueue.traveler_id == traveler_id]
    if lead_ids:
        event_filters.append(BookingEventTrail.lead_id.in_(lead_ids))
        handoff_filters.append(HandoffQueue.lead_id.in_(lead_ids))
    if interaction_ids:
        event_filters.append(BookingEventTrail.interaction_id.in_(interaction_ids))
    if booking_ids:
        event_filters.append(BookingEventTrail.booking_id.in_(booking_ids))

    BookingEventTrail.query.filter(or_(*event_filters)).delete(synchronize_session=False)
    HandoffQueue.query.filter(or_(*handoff_filters)).delete(synchronize_session=False)
    # booking_status_history.booking_id -> trip_bookings.booking_id is
    # NOT NULL with no cascade relationship configured on either model, so
    # deleting a TripBooking that ever had a status change logged (the
    # normal case for any real booking, not just a fresh test record) would
    # otherwise fail with a foreign key violation on a backend that enforces
    # it (Postgres in production; SQLite does not by default, which is why
    # this was never caught locally).
    if booking_ids:
        BookingStatusHistory.query.filter(BookingStatusHistory.booking_id.in_(booking_ids)).delete(synchronize_session=False)
    TripBooking.query.filter(TripBooking.traveler_id == traveler_id).delete(synchronize_session=False)
    CEBooking.query.filter(CEBooking.traveler_id == traveler_id).delete(synchronize_session=False)
    Interaction.query.filter(Interaction.traveler_id == traveler_id).delete(synchronize_session=False)
    Lead.query.filter(Lead.traveler_id == traveler_id).delete(synchronize_session=False)
    # TravelerDocument rows are handled by Traveler.documents' own
    # cascade='all, delete-orphan' relationship (see models/traveler.py) --
    # db.session.delete(traveler) below already deletes them; no explicit
    # bulk delete needed here (unlike TripBooking/CEBooking/Interaction/Lead
    # above, which have no delete cascade configured on their relationships
    # and are handled procedurally instead).
    db.session.delete(traveler)
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        current_app.logger.error("Traveler delete failed traveler_id=%s error=%s", traveler_id, exc, exc_info=True)
        return jsonify({
            "status": "error",
            "message": "This traveler still has records that could not be removed automatically. Please contact support.",
        }), 409

    _remove_sheet_record("Travelers", traveler_id)
    for lead_id in lead_ids:
        _remove_sheet_record("Leads", lead_id)
    return jsonify({"status": "success", "message": "Traveler deleted permanently"})

@travelers_bp.route('/<traveler_id>/documents', methods=['POST'])
def upload_document(traveler_id):
    traveler = db.get_or_404(Traveler, traveler_id)
    if 'file' not in request.files:
        flash('Please select a document file to upload.', 'danger')
        return redirect(url_for('travelers.detail', traveler_id=traveler_id))

    f = request.files['file']
    if not f or not f.filename:
        flash('Please choose a valid file.', 'danger')
        return redirect(url_for('travelers.detail', traveler_id=traveler_id))

    if not _allowed_document(f.filename, f.mimetype or ""):
        flash('Unsupported file type. Please upload JPG, JPEG, PNG, or PDF only.', 'danger')
        return redirect(url_for('travelers.detail', traveler_id=traveler_id))

    f.stream.seek(0, 2)
    size = f.stream.tell()
    f.stream.seek(0)
    if size > MAX_UPLOAD_BYTES:
        flash('File is too large. Maximum size is 10MB.', 'danger')
        return redirect(url_for('travelers.detail', traveler_id=traveler_id))

    docs_root = _documents_root() / traveler_id
    docs_root.mkdir(parents=True, exist_ok=True)
    original_name = f.filename
    storage_path = _safe_document_path(docs_root, original_name)
    f.save(storage_path)

    category = (request.form.get('category') or 'passport').strip() or 'passport'
    document = TravelerDocument(
        traveler_id=traveler_id,
        category=category,
        file_name=storage_path.name,
        original_file_name=original_name,
        mime_type=f.mimetype,
        file_extension=storage_path.suffix.lower().lstrip('.'),
        file_size=size,
        storage_path=str(storage_path),
        uploaded_by=request.form.get('uploaded_by') or 'crm-ui',
        passport_full_name=request.form.get('passport_full_name') or traveler.passport_name,
        passport_number=request.form.get('passport_number') or traveler.passport_number,
        passport_nationality=request.form.get('passport_nationality') or traveler.passport_nationality,
        passport_expiry=_parse_date(request.form.get('passport_expiry')),
        verification_status=request.form.get('verification_status') or 'pending',
        notes=request.form.get('notes'),
    )
    traveler.passport_name = document.passport_full_name or traveler.passport_name
    traveler.passport_number = document.passport_number or traveler.passport_number
    traveler.passport_nationality = document.passport_nationality or traveler.passport_nationality
    traveler.passport_expiry = document.passport_expiry or traveler.passport_expiry
    traveler.passport_attachment_ref = str(storage_path.relative_to(_documents_root())) if storage_path.is_relative_to(_documents_root()) else str(storage_path)
    db.session.add(document)
    db.session.commit()

    flash('Document uploaded successfully.', 'success')
    return redirect(url_for('travelers.detail', traveler_id=traveler_id))


@travelers_bp.route('/<traveler_id>/documents/passport/view')
def view_passport(traveler_id):
    traveler = db.get_or_404(Traveler, traveler_id)
    passport_doc = (
        TravelerDocument.query.filter_by(traveler_id=traveler.traveler_id)
        .filter(db.func.lower(TravelerDocument.category) == "passport")
        .order_by(TravelerDocument.uploaded_at.desc())
        .first()
    )
    if passport_doc is None:
        flash("No passport file is available for this traveler.", "warning")
        return redirect(url_for("travelers.detail", traveler_id=traveler_id))

    return _serve_traveler_document(traveler_id, passport_doc.document_id)


@travelers_bp.route('/<traveler_id>/documents/<int:document_id>/view')
def view_document(traveler_id, document_id: int):
    return _serve_traveler_document(traveler_id, document_id)


def _serve_traveler_document(traveler_id: str, document_id: int):
    traveler = db.get_or_404(Traveler, traveler_id)
    passport_doc = TravelerDocument.query.filter_by(document_id=document_id, traveler_id=traveler.traveler_id).first()
    if passport_doc is None:
        flash("Document not found for this traveler.", "warning")
        return redirect(url_for("travelers.detail", traveler_id=traveler_id))

    storage_path = Path(passport_doc.storage_path)
    if not storage_path.is_absolute():
        storage_path = (_documents_root() / storage_path).resolve()
    else:
        storage_path = storage_path.resolve()

    docs_root = _documents_root().resolve()
    if docs_root not in storage_path.parents and storage_path != docs_root:
        flash("The passport file path is invalid or outside the uploads folder.", "danger")
        return redirect(url_for("travelers.detail", traveler_id=traveler_id))
    if not storage_path.exists():
        flash("The passport file is missing on disk.", "warning")
        return redirect(url_for("travelers.detail", traveler_id=traveler_id))

    return send_file(
        storage_path,
        as_attachment=False,
        download_name=passport_doc.original_file_name or passport_doc.file_name,
    )


@travelers_bp.route('/export')
def export():
    q = request.args.get('q', '').strip()
    status = request.args.get('status', '')
    nationality = request.args.get('nationality', '')

    query = _apply_traveler_filters(Traveler.query, q, status, nationality)
    query = _restrict_travelers_to_viewable(query)

    travelers = query.all()

    def csv_safe(value):
        text = "" if value is None else str(value)
        return f"'{text}" if text.startswith(("=", "+", "-", "@")) else value

    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(['ID', 'Name', 'Phone', 'Email', 'Status', 'Nationality', 'Residence', 'Lead Source', 'Total Trips', 'Revenue', 'Rating'])
    for t in travelers:
        cw.writerow([csv_safe(value) for value in [
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
        ]])

    output = si.getvalue()
    authority = load_data_authority()
    return Response(
        output,
        mimetype="text/csv",
        headers={
            "Content-disposition": "attachment; filename=travelers_export.csv",
            "X-Data-Authority": authority.authority,
            "X-Data-Schema-Version": authority.schema_version,
            "X-Generated-At": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        },
    )
