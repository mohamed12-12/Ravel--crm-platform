# app/routes/leads.py
import re

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from app.models.lead import Lead
from app.models.traveler import Traveler
from app.models.interaction import Interaction
from app.models.booking_event import BookingEventTrail
from app.models.handoff import HandoffQueue
from app.extensions import db, socketio
from sqlalchemy import or_
from datetime import datetime, date
import uuid
from services.crm.system_services import UnifiedCRMService
from services.crm.system_services.phone_normalization import normalize_phone_input

leads_bp = Blueprint('leads', __name__, url_prefix='/leads')

PIPELINE_STAGES = [
    'New Lead',
    'Contacted',
    'Qualified',
    'Waiting Customer Reply',
    'Proposal Sent',
    'Booking Draft',
    'Won',
    'Lost',
    'Handoff Needed',
]

PIPELINE_ALIASES = {
    'New': 'New Lead',
    'New Lead': 'New Lead',
    'New Inquiry': 'New Lead',
    'Existing Traveler': 'Qualified',
    'Interested': 'Contacted',
    'Follow Up Needed': 'Waiting Customer Reply',
    'VIP Follow Up': 'Waiting Customer Reply',
    'Repeat Follow Up': 'Waiting Customer Reply',
    'Needs Review': 'Handoff Needed',
    'VIP Priority': 'Qualified',
    'Repeat Priority': 'Qualified',
    'Booked': 'Booking Draft',
    'Booking Draft Created': 'Booking Draft',
    'VIP Booking Draft': 'Booking Draft',
    'Repeat Booking Draft': 'Booking Draft',
    'Blocked': 'Handoff Needed',
}

PIPELINE_TRANSITIONS = {
    'New Lead': {'Contacted', 'Qualified', 'Handoff Needed', 'Lost'},
    'Contacted': {'Qualified', 'Waiting Customer Reply', 'Proposal Sent', 'Handoff Needed', 'Lost'},
    'Qualified': {'Waiting Customer Reply', 'Proposal Sent', 'Booking Draft', 'Handoff Needed', 'Won', 'Lost'},
    'Waiting Customer Reply': {'Proposal Sent', 'Booking Draft', 'Handoff Needed', 'Won', 'Lost'},
    'Proposal Sent': {'Booking Draft', 'Won', 'Lost', 'Handoff Needed'},
    'Booking Draft': {'Won', 'Lost', 'Handoff Needed'},
    'Won': set(),
    'Lost': set(),
    'Handoff Needed': {'Contacted', 'Qualified', 'Waiting Customer Reply', 'Proposal Sent', 'Booking Draft', 'Won', 'Lost'},
}

PIPELINE_GROUPS = {
    'New Lead': ['New Lead', 'New', 'New Inquiry', 'Existing Traveler'],
    'Contacted': ['Contacted', 'Interested'],
    'Qualified': ['Qualified', 'VIP Priority', 'Repeat Priority'],
    'Waiting Customer Reply': ['Waiting Customer Reply', 'Follow Up Needed', 'VIP Follow Up', 'Repeat Follow Up'],
    'Proposal Sent': ['Proposal Sent'],
    'Booking Draft': ['Booking Draft', 'Booked', 'Booking Draft Created', 'VIP Booking Draft', 'Repeat Booking Draft'],
    'Won': ['Won'],
    'Lost': ['Lost'],
    'Handoff Needed': ['Handoff Needed', 'Needs Review', 'Blocked'],
}


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


def _canonical_stage(stage: str | None) -> str:
    raw = (stage or '').strip()
    return PIPELINE_ALIASES.get(raw, raw or 'New Lead')


def _allowed_transitions(stage: str) -> set[str]:
    return PIPELINE_TRANSITIONS.get(stage, set())


def _validate_lead_transition(current_stage: str | None, new_stage: str | None) -> None:
    current = _canonical_stage(current_stage)
    target = _canonical_stage(new_stage)
    if target == current:
        return
    allowed = _allowed_transitions(current)
    if target not in allowed:
        raise ValueError(f"Invalid lead stage transition: {current} -> {target}")


@leads_bp.route('/')
def index():
    q = request.args.get('q', '')
    stage = request.args.get('stage', '')
    priority = request.args.get('priority', '')
    page = request.args.get('page', 1, type=int)
    per_page = 25

    query = Lead.query
    if q:
        query = query.filter(or_(
            Lead.customer_name.ilike(f'%{q}%'),
            Lead.raw_phone.ilike(f'%{q}%'),
            Lead.lead_id.ilike(f'%{q}%'),
            Lead.notes.ilike(f'%{q}%'),
            Lead.current_step.ilike(f'%{q}%'),
        ))
    if stage and stage in PIPELINE_GROUPS:
        query = query.filter(Lead.lead_stage.in_(PIPELINE_GROUPS[stage]))
    if priority:
        query = query.filter(Lead.priority == priority)

    pagination = query.order_by(Lead.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False)
    leads = pagination.items

    # Compute new clear analytics counters
    today = date.today()
    total_count = Lead.query.count()
    new_today_count = Lead.query.filter(db.func.date(Lead.created_at) == today).count()
    qualified_count = Lead.query.filter(Lead.lead_stage.in_(PIPELINE_GROUPS['Qualified'])).count()
    handoff_required_count = Lead.query.filter(Lead.handoff_required == True).count()
    overdue_follow_up_count = Lead.query.filter(Lead.follow_up_due_date < today).count()
    booked_count = Lead.query.filter(Lead.lead_stage.in_(PIPELINE_GROUPS['Booking Draft'])).count()
    blocked_count = Lead.query.filter(Lead.lead_stage.in_(PIPELINE_GROUPS['Handoff Needed'])).count()
    
    analytics = {
        "total": total_count,
        "new_today": new_today_count,
        "qualified": qualified_count,
        "handoff_required": handoff_required_count,
        "overdue": overdue_follow_up_count,
        "booked": booked_count,
        "blocked": blocked_count
    }

    return render_template('leads/index.html',
                           leads=leads,
                           pagination=pagination,
                           analytics=analytics,
                           q=q,
                           current_stage=stage,
                           current_priority=priority,
                           stages=PIPELINE_STAGES,
                           stage_aliases=PIPELINE_ALIASES,
                           today=today)


@leads_bp.route('/<string:lead_id>')
def detail(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    traveler = Traveler.query.get(lead.traveler_id) if lead.traveler_id else None
    interactions = []
    if lead.traveler_id:
        interactions = Interaction.query.filter_by(traveler_id=lead.traveler_id)\
            .order_by(Interaction.timestamp.desc()).limit(20).all()
    try:
        UnifiedCRMService().ensure_operational_schema()
    except Exception:
        pass
    event_trail = BookingEventTrail.query.filter(
        or_(
            BookingEventTrail.lead_id == lead.lead_id,
            BookingEventTrail.traveler_id == lead.traveler_id,
        )
    ).order_by(BookingEventTrail.occurred_at.asc()).all()
    return render_template('leads/detail.html',
                           lead=lead,
                           traveler=traveler,
                           interactions=interactions,
                           event_trail=event_trail,
                           stages=PIPELINE_STAGES)


@leads_bp.route('/', methods=['POST'])
def create():
    data = request.form.to_dict()
    service = UnifiedCRMService()
    customer_name = data.get('customer_name', '').strip()
    raw_phone = data.get('raw_phone', '').strip()
    requested_stage = (data.get('lead_stage', '') or '').strip()
    lead_stage = _canonical_stage(requested_stage) if requested_stage in PIPELINE_STAGES else requested_stage
    priority = data.get('priority', '').strip()
    lead_source = data.get('lead_source', '').strip() or 'Manual Web UI'
    channel = data.get('channel', '').strip() or 'System UI'
    preferred_trip_type = data.get('preferred_trip_type', '').strip()
    interested_trip_ids = data.get('interested_trip_ids', '').strip()
    follow_up_due_date = data.get('follow_up_due_date', '').strip()
    language = data.get('language', 'ar').strip()
    notes = data.get('notes', '').strip()

    # 1. Normalize the phone number using Service logic
    egypt_local = bool(re.fullmatch(r"0?(10|11|12|15)\d{8}", re.sub(r"\D", "", raw_phone or "")))
    default_country_code = data.get("country_code", "").strip() or ("20" if egypt_local else "")
    phone_info = normalize_phone_input(
        raw_phone,
        default_country_code,
        default_country_is_explicit=bool(default_country_code),
    ).to_dict()
    phone_info["raw_phone"] = raw_phone
    lookup_key = phone_info.get("lookup_key") or service.normalize_phone(raw_phone, phone_info.get("country_code", "")).get("lookup_key")
    normalized_whatsapp = phone_info.get("normalized_whatsapp")
    local_number = phone_info.get("local_number")

    # 2. Create lead and link/generate traveler using record_agent_outcome.
    # The service resolves blacklisted travelers into a blocked lead + handoff.
    preview = service.record_agent_outcome(
        full_name=customer_name,
        raw_phone=raw_phone,
        country_code=phone_info.get("country_code") or "",
        trip_type=preferred_trip_type or None,
        channel=channel,
        source=lead_source,
        agent_notes=notes,
        preferred_trip_id=interested_trip_ids,
        language=language,
        manual_lead_stage=lead_stage,
        manual_priority=priority,
        manual_follow_up_due_date=follow_up_due_date,
    )

    lead_id = preview["write_result"]["lead_update"]["lead_id"]

    # 4. Synchronize Flask-SQLAlchemy session cache to ensure the SQLite inserts (done via direct SQL) are fully loaded
    db.session.commit()
    db.session.expire_all()

    if preview.get("handoff_required"):
        traveler_dict = preview.get("traveler") or {}
        traveler_id = traveler_dict.get("traveler_id") if isinstance(traveler_dict, dict) else None
        handoff_id = f"H-{uuid.uuid4().hex[:8].upper()}"
        reason = preview.get("handoff_reason", "Conflict or blacklist detected during manual lead creation.")

        handoff = HandoffQueue(
            handoff_id=handoff_id,
            lead_id=lead_id,
            traveler_id=traveler_id,
            flow_key="manual_lead",
            reason=reason,
            priority="Critical" if reason == "blacklisted_customer" else "High",
            channel=channel,
            status='Pending',
            created_at=datetime.utcnow(),
        )

        # Mark lead in DB session
        lead = Lead.query.get(lead_id)
        if lead:
            lead.handoff_required = True
            lead.handoff_id = handoff_id
            if reason == "blacklisted_customer":
                lead.lead_stage = "Blocked"
                lead.priority = "Critical"
            else:
                lead.lead_stage = "Needs Review"
                lead.priority = "High"

        db.session.add(handoff)
        db.session.commit()
        try:
            service.sync_record_to_sheet("Handoff Queue", handoff_id)
        except Exception:
            pass

        socketio.emit('new_handoff', {
            'handoff_id': handoff_id,
            'traveler_name': customer_name,
            'reason': reason,
            'priority': handoff.priority,
            'created_at': handoff.created_at.isoformat(),
        })
        flash(f"Lead created with Handoff Required ({reason}). Marked for review.", 'warning')
    else:
        # Retrieve traveler info to report status
        traveler_dict = preview.get("traveler") or preview.get("write_result", {}).get("created_traveler") or {}
        traveler_id = traveler_dict.get("traveler_id")
        t_status = "Active"
        if traveler_id:
            db_traveler = Traveler.query.get(traveler_id)
            if db_traveler:
                t_status = db_traveler.status
        
        flash(f"Lead '{customer_name}' successfully created and linked to traveler {traveler_id} (Status: {t_status}).", 'success')

    return redirect(url_for('leads.detail', lead_id=lead_id))


@leads_bp.route('/<string:lead_id>', methods=['POST'])
def update(lead_id):
    method_override = request.form.get('_method', '').upper()
    if method_override == 'DELETE':
        return delete(lead_id)

    lead = Lead.query.get_or_404(lead_id)
    data = request.form.to_dict()

    def to_date(v):
        if not v:
            return None
        try:
            return datetime.strptime(v, '%Y-%m-%d').date()
        except Exception:
            return None

    lead.customer_name = data.get('customer_name', lead.customer_name)
    
    new_phone = data.get('raw_phone', '').strip()
    if new_phone and new_phone != lead.raw_phone:
        # Check blacklist prior to updating existing lead's phone number
        service = UnifiedCRMService()
        egypt_local = bool(re.fullmatch(r"0?(10|11|12|15)\d{8}", re.sub(r"\D", "", new_phone or "")))
        default_country_code = data.get("country_code", "").strip() or ("20" if egypt_local else "")
        phone_info = normalize_phone_input(
            new_phone,
            default_country_code,
            default_country_is_explicit=bool(default_country_code),
        ).to_dict()
        phone_info["raw_phone"] = new_phone
        lookup_key = phone_info.get("lookup_key") or service.normalize_phone(new_phone, phone_info.get("country_code", "")).get("lookup_key")
        normalized_whatsapp = phone_info.get("normalized_whatsapp")
        local_number = phone_info.get("local_number")

        blacklisted_traveler = Traveler.query.filter(
            or_(
                db.func.trim(Traveler.status).ilike("blacklisted"),
                db.func.trim(Traveler.status).ilike("blacklist")
            )
        ).filter(_phone_match_filter(service, phone_info)).first()

        if blacklisted_traveler:
            flash(f"Error: Phone number '{new_phone}' is blacklisted (Traveler {blacklisted_traveler.traveler_id} is Blacklisted). Lead phone was not updated.", "danger")
            return redirect(url_for('leads.detail', lead_id=lead_id))
        
        lead.raw_phone = new_phone

    if data.get('lead_stage'):
        next_stage = _canonical_stage(data.get('lead_stage'))
        try:
            _validate_lead_transition(lead.lead_stage, next_stage)
        except ValueError as exc:
            flash(str(exc), 'error')
            return redirect(url_for('leads.detail', lead_id=lead_id))
        lead.lead_stage = next_stage
    lead.priority = data.get('priority', lead.priority)
    lead.lead_source = data.get('lead_source', lead.lead_source)
    lead.channel = data.get('channel', lead.channel)
    lead.preferred_trip_type = data.get('preferred_trip_type', lead.preferred_trip_type)
    lead.interested_trip_ids = data.get('interested_trip_ids', lead.interested_trip_ids)
    lead.notes = data.get('notes', lead.notes)
    if data.get('current_step'):
        lead.current_step = data.get('current_step')
    lead.follow_up_status = data.get('follow_up_status', lead.follow_up_status)
    lead.follow_up_due_date = to_date(data.get('follow_up_due_date')) or lead.follow_up_due_date
    lead.updated_at = datetime.utcnow()
    if data.get('booking_id'):
        lead.booking_id = data.get('booking_id')

    db.session.commit()
    flash('Lead updated.', 'success')
    return redirect(url_for('leads.detail', lead_id=lead_id))


def delete(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    lead.lead_stage = 'Lost'
    lead.updated_at = datetime.utcnow()
    db.session.commit()
    flash(f"Lead marked as Lost.", 'info')
    return redirect(url_for('leads.index'))


@leads_bp.route('/<string:lead_id>/advance', methods=['POST'])
def advance_stage(lead_id):
    """Advance lead to next stage logically based on pipeline groups."""
    lead = Lead.query.get_or_404(lead_id)
    current_stage = _canonical_stage(lead.lead_stage)
    if current_stage not in PIPELINE_TRANSITIONS:
        current_stage = 'New Lead'
    next_stage = None
    for candidate in PIPELINE_STAGES:
        if candidate in _allowed_transitions(current_stage):
            next_stage = candidate
            break
    if next_stage:
        lead.lead_stage = next_stage
        lead.updated_at = datetime.utcnow()
        db.session.commit()
    return jsonify({'status': 'ok', 'new_stage': lead.lead_stage})


@leads_bp.route('/<string:lead_id>/handoff', methods=['POST'])
def create_handoff(lead_id):
    """Trigger handoff from a lead."""
    lead = Lead.query.get_or_404(lead_id)
    data = request.get_json() or {}
    handoff_id = f"H-{uuid.uuid4().hex[:8].upper()}"

    handoff = HandoffQueue(
        handoff_id=handoff_id,
        lead_id=lead_id,
        traveler_id=lead.traveler_id,
        flow_key=lead.flow_key,
        reason=data.get('reason', lead.notes or 'Manual handoff from admin.'),
        priority=data.get('priority', lead.priority or 'Medium'),
        channel=lead.channel or 'WhatsApp',
        status='Pending',
        created_at=datetime.utcnow(),
    )
    lead.handoff_required = True
    lead.handoff_id = handoff_id
    lead.lead_stage = 'Handoff Needed'
    db.session.add(handoff)
    db.session.commit()
    try:
        service = UnifiedCRMService()
        event = service.create_booking_event(
            event_type='handoff_required',
            event_label='Handoff required',
            traveler_id=lead.traveler_id or '',
            lead_id=lead.lead_id,
            channel=lead.channel or 'system-ui',
            actor='system-ui',
            notes=handoff.reason or '',
            metadata={'handoff_id': handoff_id, 'priority': handoff.priority},
        )
        service.sync_agent_write_to_sheet(
            traveler_id=lead.traveler_id or '',
            lead_id=lead.lead_id,
            event_ids=[event['event_id']],
        )
    except Exception:
        pass

    traveler = Traveler.query.get(lead.traveler_id) if lead.traveler_id else None
    socketio.emit('new_handoff', {
        'handoff_id': handoff_id,
        'traveler_name': traveler.full_name if traveler else lead.customer_name,
        'reason': handoff.reason,
        'priority': handoff.priority,
        'created_at': handoff.created_at.isoformat(),
    })
    return jsonify({'status': 'ok', 'handoff_id': handoff_id})
