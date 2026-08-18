# app/routes/leads.py
import logging
import re
from pathlib import Path

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, abort
from app.models.booking import TripBooking
from app.models.lead import Lead
from app.models.trip import Trip
from app.models.traveler import Traveler
from app.models.interaction import Interaction
from app.models.booking_event import BookingEventTrail
from app.models.handoff import HandoffQueue
from app.models.user import User
from app.services.assignments import (
    active_assignees,
    auto_assign_lead,
    apply_assignment,
    assignment_history,
    exact_legacy_user,
    resolve_user_id,
)
from app.services.booking_automation import (
    auto_create_booking_from_lead,
    sync_lead_group_size_to_booking,
)
from app.services.conversations import get_conversations_for_lead
from app.extensions import db, socketio
from sqlalchemy import or_
from datetime import datetime, date, timezone
import uuid
from services.crm.system_services import UnifiedCRMService
from services.crm.system_services.phone_normalization import normalize_phone_input
from app.security import (
    can_view_assigned_record,
    current_actor,
    current_role,
    current_user,
    current_user_id,
    has_permission,
)

logger = logging.getLogger(__name__)

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
EMPLOYEE_LEAD_STAGES = set(PIPELINE_STAGES)

# Fields the Edit Lead form may submit, split by how update() handles them.
#
# _SIMPLE_LEAD_FIELDS actually drives the plain passthrough assignments in
# update(), so it can never drift into stale documentation.
# _CUSTOM_HANDLED_LEAD_FIELDS names the rest -- fields with bespoke handling
# (validation, parsing, permissions) that update() reads explicitly.
#
# Together they are the contract behind
# test_edit_lead_form_fields_are_all_handled_by_the_update_route: every
# named input rendered in templates/leads/detail.html's Edit Lead form must
# appear in one of these sets. Without that guard a field can be added to
# the template, silently dropped by the route, and still report "Changes
# saved" to the employee -- which is exactly how group_size shipped broken.
_SIMPLE_LEAD_FIELDS = (
    'customer_name',
    'priority',
    'lead_source',
    'channel',
    'preferred_trip_type',
    'interested_trip_ids',
    'notes',
)

_CUSTOM_HANDLED_LEAD_FIELDS = frozenset({
    'csrf_token',
    'expected_updated_at',
    'employee_correction',
    '_method',
    'raw_phone',
    'country_code',
    'lead_stage',
    'stage_change_reason',
    'group_size',
    'current_step',
    'follow_up_status',
    'follow_up_due_date',
    'customer_response_status',
    'mark_contacted',
    'assigned_to_user_id',
    'assigned_to',
    'assignment_reason',
    'booking_id',
})

HANDLED_LEAD_FORM_FIELDS = frozenset(_SIMPLE_LEAD_FIELDS) | _CUSTOM_HANDLED_LEAD_FIELDS


def _parse_group_size(value: str | None) -> int:
    """Parse an employee-entered group size. Raises ValueError with an
    employee-facing message so callers can flash it directly."""
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


STEP_LABELS = {
    'booking_ready': 'Ready to create booking draft',
    'traveler_not_found': 'Collect new traveler details',
    'lead_qualification': 'Qualify travel request',
    'create_lead': 'Create lead record',
    'collect_trip_type': 'Confirm trip type',
    'select_trip': 'Select a trip',
    'collect_traveler_gender': 'Confirm traveler group',
    'collect_room_type': 'Select room type',
    'collect_group_size': 'Confirm group size',
    'collect_flight_preference': 'Confirm flight preference',
    'collect_passport_attachment': 'Request passport attachment',
    'create_capacity_handoff': 'Arrange capacity review',
}


def _remove_sheet_record(mapping_name: str, record_id: str) -> None:
    try:
        UnifiedCRMService().remove_record_from_sheet(mapping_name, record_id)
    except Exception:
        pass


def _display_text(value: object, fallback: str = '-') -> str:
    """Repair legacy UTF-8-as-Latin-1 text for display without altering CRM data."""
    text = str(value or '').strip()
    if not text:
        return fallback
    try:
        repaired = text.encode('latin-1').decode('utf-8')
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    return repaired if repaired else text


def _display_step(value: object) -> str:
    raw = _display_text(value, fallback='')
    if not raw:
        return 'No next action recorded'
    if raw in STEP_LABELS:
        return STEP_LABELS[raw]
    if re.fullmatch(r'[a-z0-9_]+', raw):
        return raw.replace('_', ' ').title()
    return raw


def _display_source(value: object) -> str:
    """Render legacy source labels as plain text for the lead list."""
    source = _display_text(value, fallback="")
    source = re.sub(r"^[^\w]+[-:\s]*", "", source).strip()
    return source or "Unknown"


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


def _validate_lead_transition(
    current_stage: str | None,
    new_stage: str | None,
    *,
    allow_employee_correction: bool = False,
    correction_note: str = '',
) -> None:
    current = _canonical_stage(current_stage)
    target = _canonical_stage(new_stage)
    if target == current:
        return
    allowed = _allowed_transitions(current)
    if target not in allowed:
        if allow_employee_correction and target in EMPLOYEE_LEAD_STAGES:
            if not correction_note.strip():
                raise ValueError(
                    'Add a reason before reopening or correcting a lead stage.'
                )
            return
        raise ValueError(f"Invalid lead stage transition: {current} -> {target}")


def _parse_date(v):
    if not v:
        return None
    try:
        return datetime.strptime(v, '%Y-%m-%d').date()
    except Exception:
        return None


def _append_note(existing: str | None, note: str, actor: str) -> str | None:
    clean_note = (note or '').strip()
    if not clean_note:
        return existing
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat(sep=' ')
    entry = f"[{stamp} by {actor}] {clean_note}"
    return f"{existing.rstrip()}\n{entry}" if existing else entry


@leads_bp.route('/')
def index():
    q = request.args.get('q', '')
    stage = request.args.get('stage', '')
    priority = request.args.get('priority', '')
    queue = request.args.get('queue', '')
    employee_id = request.args.get('employee_id', type=int)
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
    today = date.today()
    if queue == 'assigned_to_me':
        query = query.filter(Lead.assigned_to_user_id == current_user_id())
    elif employee_id and has_permission('view_all'):
        query = query.filter(Lead.assigned_to_user_id == employee_id)
    elif queue == 'unassigned':
        query = query.filter(Lead.assigned_to_user_id.is_(None))
    elif queue == 'inactive_owner':
        query = query.filter(Lead.assigned_user.has(User.is_active.is_(False)))
    elif queue == 'overdue':
        query = query.filter(Lead.follow_up_due_date < today)
    elif queue == 'today':
        query = query.filter(Lead.follow_up_due_date == today)
    elif queue == 'waiting_customer':
        query = query.filter(Lead.lead_stage.in_(PIPELINE_GROUPS['Waiting Customer Reply']))
    elif queue == 'new':
        query = query.filter(Lead.lead_stage.in_(PIPELINE_GROUPS['New Lead']))
    elif queue == 'high_priority':
        query = query.filter(Lead.priority.in_(['High', 'Critical']))
    elif queue == 'documents_missing':
        query = query.filter(Lead.passport_status == 'pending')
    elif queue == 'handoff_required':
        query = query.filter(Lead.handoff_required == True)

    if not has_permission('view_all'):
        query = query.filter(or_(Lead.assigned_to_user_id.is_(None), Lead.assigned_to_user_id == current_user_id()))

    pagination = query.order_by(
        db.func.coalesce(Lead.updated_at, Lead.created_at).desc(),
        Lead.created_at.desc(),
    ).paginate(
        page=page, per_page=per_page, error_out=False)
    leads = pagination.items

    # Compute new clear analytics counters
    total_count = Lead.query.count()
    new_today_count = Lead.query.filter(db.func.date(Lead.created_at) == today).count()
    qualified_count = Lead.query.filter(Lead.lead_stage.in_(PIPELINE_GROUPS['Qualified'])).count()
    handoff_required_count = Lead.query.filter(Lead.handoff_required == True).count()
    overdue_follow_up_count = Lead.query.filter(Lead.follow_up_due_date < today).count()
    booked_count = Lead.query.filter(Lead.lead_stage.in_(PIPELINE_GROUPS['Booking Draft'])).count()
    blocked_count = Lead.query.filter(Lead.lead_stage.in_(PIPELINE_GROUPS['Handoff Needed'])).count()
    available_trips = (
        Trip.query
        .filter(Trip.sales_status == 'Open')
        .order_by(Trip.type.asc(), Trip.start_date.asc(), Trip.trip_name.asc())
        .all()
    )
    
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
                           current_queue=queue,
                           current_employee_id=employee_id,
                           employees=active_assignees() if has_permission('view_all') else [],
                           can_assign=has_permission('assign_work'),
                           available_trips=available_trips,
                           stages=PIPELINE_STAGES,
                           stage_aliases=PIPELINE_ALIASES,
                           display_text=_display_text,
                           display_step=_display_step,
                           display_source=_display_source,
                           today=today)


@leads_bp.route('/<string:lead_id>')
def detail(lead_id):
    lead = db.get_or_404(Lead, lead_id)
    if not can_view_assigned_record(lead.assigned_to_user_id):
        abort(403)
    traveler = db.session.get(Traveler, lead.traveler_id) if lead.traveler_id else None
    passport_file_name = Path(lead.passport_attachment_ref).name if lead.passport_attachment_ref else ""
    commercial_context = UnifiedCRMService.resolve_commercial_context(
        traveler=traveler.to_dict() if traveler else None,
        trip_type=lead.preferred_trip_type,
        requested_group_size=lead.group_size or 1,
        trip_id=(lead.suggested_trip_ids or lead.interested_trip_ids or "").split(",")[0].strip(),
    )
    interactions = []
    if lead.traveler_id:
        interactions = Interaction.query.filter_by(traveler_id=lead.traveler_id)\
            .order_by(Interaction.timestamp.desc()).limit(20).all()
    conversations = get_conversations_for_lead(lead.lead_id, lead.traveler_id)
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
    assigned_history = assignment_history('lead', lead.lead_id)
    # Lets the edit form offer trips by NAME instead of making the employee
    # type a raw trip_id into a free-text box.
    available_trips = (
        Trip.query
        .filter(Trip.sales_status == 'Open')
        .order_by(Trip.type.asc(), Trip.start_date.asc(), Trip.trip_name.asc())
        .all()
    )
    return render_template('leads/detail.html',
                           lead=lead,
                           traveler=traveler,
                           passport_file_name=passport_file_name,
                           commercial_context=commercial_context,
                           interactions=interactions,
                           conversations=conversations,
                           event_trail=event_trail,
                           assigned_history=assigned_history,
                           employees=active_assignees(),
                           can_assign=has_permission('assign_work'),
                           assigned_user=lead.assigned_user,
                           stages=PIPELINE_STAGES,
                           available_trips=available_trips,
                           today=date.today())


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
        group_size=data.get('group_size', '1'),
        force_create_new_lead=True,
    )

    lead_id = preview["write_result"]["lead_update"]["lead_id"]

    # 4. Synchronize Flask-SQLAlchemy session cache to ensure the SQLite inserts (done via direct SQL) are fully loaded
    db.session.commit()
    db.session.expire_all()
    created_lead = db.session.get(Lead, lead_id)
    if created_lead:
        if data.get('current_step'):
            created_lead.current_step = data.get('current_step')
        if data.get('assigned_to_user_id') and not has_permission('assign_work'):
            flash('You do not have permission to assign leads.', 'error')
            return redirect(url_for('leads.index'))
        if data.get('assigned_to_user_id'):
            apply_assignment(
                created_lead,
                resource_type='lead',
                resource_id=created_lead.lead_id,
                new_user_id=resolve_user_id(data.get('assigned_to_user_id'), allow_blank=False),
                actor=current_user(),
                reason=data.get('assignment_reason', ''),
            )
        else:
            auto_assign_lead(
                created_lead,
                actor=current_user(),
                reason='Automatic round-robin sales assignment on lead creation',
            )
        db.session.commit()

    if preview.get("handoff_required"):
        traveler_dict = preview.get("traveler") or {}
        traveler_id = traveler_dict.get("traveler_id") if isinstance(traveler_dict, dict) else None
        reason = preview.get("handoff_reason", "Conflict or blacklist detected during manual lead creation.")
        handoff = service.create_handoff_case(
            lead_id=lead_id,
            traveler_id=str(traveler_id or ""),
            flow_key="manual_lead",
            reason_code=reason,
            reason_text="Conflict or blacklist detected during manual lead creation.",
            priority="Critical" if reason == "blacklisted_customer" else "High",
            channel=channel,
            customer_name=customer_name,
            customer_summary=notes or customer_name,
            agent_summary="Manual lead flow requested review.",
            notes=notes,
            metadata={
                "trip_type": preferred_trip_type,
                "trip_id": interested_trip_ids,
                "group_size": data.get('group_size', '1'),
                "raw_phone": raw_phone,
            },
        )
        handoff_id = handoff["handoff_id"]
        db.session.expire_all()

        socketio.emit('new_handoff', {
            'handoff_id': handoff_id,
            'traveler_name': customer_name,
            'reason': handoff["reason_text"],
            'priority': handoff["priority"],
            'created_at': datetime.now(timezone.utc).isoformat(),
        })
        flash(f"Lead created with Handoff Required. {handoff['reason_text']}", 'warning')
    else:
        # Retrieve traveler info to report status
        traveler_dict = preview.get("traveler") or preview.get("write_result", {}).get("created_traveler") or {}
        traveler_id = traveler_dict.get("traveler_id")
        t_status = "Active"
        if traveler_id:
            db_traveler = db.session.get(Traveler, traveler_id)
            if db_traveler:
                t_status = db_traveler.status
        
        flash(f"Lead '{customer_name}' successfully created and linked to traveler {traveler_id} (Status: {t_status}).", 'success')

    return redirect(url_for('leads.detail', lead_id=lead_id))


@leads_bp.route('/<string:lead_id>', methods=['POST'])
def update(lead_id):
    method_override = request.form.get('_method', '').upper()
    if method_override == 'DELETE':
        return delete(lead_id)

    lead = db.get_or_404(Lead, lead_id)
    data = request.form.to_dict()
    expected_updated_at = (data.get('expected_updated_at') or '').strip()
    current_updated_at = lead.updated_at.isoformat() if lead.updated_at else ''
    if expected_updated_at and expected_updated_at != current_updated_at:
        flash('Booking was updated by another employee', 'error')
        return redirect(url_for('leads.detail', lead_id=lead_id))
    actor = current_actor()
    previous_stage = _canonical_stage(lead.lead_stage)
    stage_change_reason = (data.get('stage_change_reason') or '').strip()
    employee_correction = (
        str(data.get('employee_correction') or '').strip().lower() in {'1', 'true', 'yes'}
    )
    assignment_requested = 'assigned_to_user_id' in data or 'assigned_to' in data
    requested_user_id = lead.assigned_to_user_id
    if assignment_requested:
        if not has_permission('assign_work'):
            flash('You do not have permission', 'error')
            return redirect(url_for('leads.detail', lead_id=lead_id))
        try:
            if 'assigned_to_user_id' in data:
                requested_user_id = resolve_user_id(data.get('assigned_to_user_id'))
            else:
                legacy_user = exact_legacy_user(data.get('assigned_to'), include_inactive=False)
                if data.get('assigned_to', '').strip() and not legacy_user:
                    raise ValueError('Choose an active employee from the list')
                requested_user_id = legacy_user.id if legacy_user else None
        except ValueError as exc:
            flash(str(exc), 'error')
            return redirect(url_for('leads.detail', lead_id=lead_id))

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
            _validate_lead_transition(
                lead.lead_stage,
                next_stage,
                allow_employee_correction=employee_correction,
                correction_note=stage_change_reason,
            )
        except ValueError as exc:
            flash(str(exc), 'error')
            return redirect(url_for('leads.detail', lead_id=lead_id))
        lead.lead_stage = next_stage
    for field in _SIMPLE_LEAD_FIELDS:
        setattr(lead, field, data.get(field, getattr(lead, field)))
    previous_group_size = lead.group_size or 1
    if 'group_size' in data:
        try:
            lead.group_size = _parse_group_size(data.get('group_size'))
        except ValueError as exc:
            flash(str(exc), 'error')
            return redirect(url_for('leads.detail', lead_id=lead_id))
    if data.get('current_step'):
        lead.current_step = data.get('current_step')
    lead.follow_up_status = data.get('follow_up_status', lead.follow_up_status)
    lead.follow_up_due_date = _parse_date(data.get('follow_up_due_date')) or lead.follow_up_due_date
    if assignment_requested:
        apply_assignment(
            lead,
            resource_type='lead',
            resource_id=lead.lead_id,
            new_user_id=requested_user_id,
            actor=current_user(),
            reason=data.get('assignment_reason', ''),
        )
    if data.get('customer_response_status'):
        lead.customer_response_status = data.get('customer_response_status')
    if data.get('mark_contacted') == '1':
        lead.last_contact_at = datetime.now(timezone.utc).replace(microsecond=0)
        lead.customer_response_status = lead.customer_response_status or 'Contacted'
    lead.updated_at = datetime.now(timezone.utc)
    if data.get('booking_id'):
        lead.booking_id = data.get('booking_id')

    db.session.commit()
    try:
        stage_changed = previous_stage != _canonical_stage(lead.lead_stage)
        event_notes = data.get('notes', '') or lead.current_step or 'Lead updated from CRM'
        if stage_changed:
            event_notes = f"Lead stage changed: {previous_stage} -> {lead.lead_stage}."
            if stage_change_reason:
                event_notes = f"{event_notes} Reason: {stage_change_reason}"
        UnifiedCRMService().create_booking_event(
            event_type="lead_follow_up_updated",
            event_label="Lead follow-up updated",
            traveler_id=lead.traveler_id or '',
            lead_id=lead.lead_id,
            trip_id=(lead.interested_trip_ids or lead.suggested_trip_ids or '').split(',')[0].strip(),
            channel=lead.channel or 'crm-ui',
            actor=actor,
            notes=event_notes,
            metadata={
                "previous_lead_stage": previous_stage,
                "lead_stage": lead.lead_stage,
                "stage_change_reason": stage_change_reason,
                "follow_up_status": lead.follow_up_status,
                "follow_up_due_date": lead.follow_up_due_date.isoformat() if lead.follow_up_due_date else "",
                "assigned_to": lead.assigned_to or "",
            },
        )
    except Exception:
        pass
    try:
        auto_create_booking_from_lead(lead, trigger_source="employee", actor_label=actor, actor_user=current_user())
    except Exception:
        pass
    group_size_sync = None
    try:
        group_size_sync = sync_lead_group_size_to_booking(
            lead,
            previous_group_size=previous_group_size,
            actor_label=actor,
        )
    except Exception:
        logger.error("Lead group size propagation failed lead_id=%s", lead_id, exc_info=True)
        db.session.rollback()
    if group_size_sync and group_size_sync.get("updated"):
        flash(
            f"Changes saved. Booking {group_size_sync['booking_id']} group size updated to "
            f"{group_size_sync['group_size']}.",
            'success',
        )
    elif group_size_sync and group_size_sync.get("skipped_reason") == "booking_closed":
        flash(
            f"Changes saved to the lead. Booking {group_size_sync['booking_id']} is "
            f"{group_size_sync['booking_status']}, so its group size was left unchanged - "
            "update the booking directly if it really needs to change.",
            'warning',
        )
    else:
        flash('Changes saved', 'success')
    return redirect(url_for('leads.detail', lead_id=lead_id))


def delete(lead_id):
    lead = db.get_or_404(Lead, lead_id)

    BookingEventTrail.query.filter(BookingEventTrail.lead_id == lead_id).delete(synchronize_session=False)
    HandoffQueue.query.filter(HandoffQueue.lead_id == lead_id).delete(synchronize_session=False)
    bookings_unlinked = TripBooking.query.filter(TripBooking.lead_id == lead_id).update(
        {
            TripBooking.lead_id: None,
            TripBooking.customer_response_status: db.func.coalesce(
                TripBooking.customer_response_status,
                "Lead deleted",
            ),
        },
        synchronize_session=False,
    )
    travelers_unlinked = Traveler.query.filter(Traveler.last_lead_id == lead_id).update(
        {Traveler.last_lead_id: None},
        synchronize_session=False,
    )
    db.session.delete(lead)
    db.session.commit()
    logger.info(
        "Lead %s deleted: unlinked %d booking(s), %d traveler last_lead_id reference(s)",
        lead_id,
        bookings_unlinked,
        travelers_unlinked,
    )

    _remove_sheet_record("Leads", lead_id)
    flash("Lead deleted permanently.", 'success')
    return redirect(url_for('leads.index'))


@leads_bp.route('/<string:lead_id>/advance', methods=['POST'])
def advance_stage(lead_id):
    """Advance lead to next stage logically based on pipeline groups."""
    lead = db.get_or_404(Lead, lead_id)
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
        lead.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        try:
            auto_create_booking_from_lead(lead, trigger_source="employee", actor_label=current_actor(), actor_user=current_user())
        except Exception:
            pass
    return jsonify({'status': 'ok', 'new_stage': lead.lead_stage})


@leads_bp.route('/<string:lead_id>/quick-action', methods=['POST'])
def quick_action(lead_id):
    lead = db.get_or_404(Lead, lead_id)
    data = request.get_json(silent=True) or request.form.to_dict()
    action = (data.get('action') or '').strip()
    note = (data.get('note') or '').strip()
    actor = current_actor()
    now = datetime.now(timezone.utc).replace(microsecond=0)

    action_map = {
        'mark_contacted': {
            'stage': 'Contacted',
            'follow_up_status': 'Contacted',
            'current_step': 'Send trip details',
            'customer_response_status': 'Contacted',
            'last_contact_at': now,
        },
        'waiting_customer': {
            'stage': 'Waiting Customer Reply',
            'follow_up_status': 'Awaiting customer reply',
            'current_step': 'Wait for customer response',
            'customer_response_status': 'Waiting Customer',
        },
        'request_documents': {
            'stage': _canonical_stage(lead.lead_stage),
            'follow_up_status': 'Documents requested',
            'current_step': 'Request passport or missing documents',
            'customer_response_status': 'Waiting Customer',
            'passport_status': lead.passport_status or 'pending',
        },
        'request_deposit': {
            'stage': 'Proposal Sent',
            'follow_up_status': 'Awaiting Deposit',
            'current_step': 'Request deposit',
            'customer_response_status': 'Waiting Customer',
        },
        'escalate_handoff': {
            'stage': 'Handoff Needed',
            'follow_up_status': 'Needs human review',
            'current_step': 'Escalate to human support',
            'handoff_required': True,
        },
    }
    if action not in action_map:
        return jsonify({'error': 'Invalid quick action'}), 400

    patch = action_map[action]
    target_stage = patch.get('stage')
    try:
        _validate_lead_transition(lead.lead_stage, target_stage)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    lead.lead_stage = target_stage
    lead.follow_up_status = patch.get('follow_up_status', lead.follow_up_status)
    lead.current_step = patch.get('current_step', lead.current_step)
    lead.customer_response_status = patch.get('customer_response_status', lead.customer_response_status)
    lead.last_contact_at = patch.get('last_contact_at', lead.last_contact_at)
    lead.passport_status = patch.get('passport_status', lead.passport_status)
    lead.handoff_required = patch.get('handoff_required', lead.handoff_required)
    if data.get('follow_up_due_date'):
        lead.follow_up_due_date = _parse_date(data.get('follow_up_due_date'))
    lead.notes = _append_note(lead.notes, note, actor) if note else lead.notes
    lead.updated_at = now
    db.session.commit()

    event = UnifiedCRMService().create_booking_event(
        event_type="lead_quick_action",
        event_label="Lead quick action",
        traveler_id=lead.traveler_id or '',
        lead_id=lead.lead_id,
        trip_id=(lead.interested_trip_ids or lead.suggested_trip_ids or '').split(',')[0].strip(),
        channel=lead.channel or 'crm-ui',
        actor=actor,
        notes=note or patch.get('current_step', ''),
        metadata={"action": action, "lead_stage": lead.lead_stage},
        occurred_at=now,
    )
    try:
        auto_create_booking_from_lead(lead, trigger_source="employee", actor_label=actor, actor_user=current_user())
    except Exception:
        pass
    return jsonify({'status': 'ok', 'lead_stage': lead.lead_stage, 'event_id': event['event_id']})


@leads_bp.route('/<string:lead_id>/handoff', methods=['POST'])
def create_handoff(lead_id):
    """Trigger handoff from a lead."""
    lead = db.get_or_404(Lead, lead_id)
    data = request.get_json() or {}
    service = UnifiedCRMService()
    handoff = service.create_handoff_case(
        lead_id=lead_id,
        traveler_id=lead.traveler_id or '',
        trip_id=(lead.interested_trip_ids or lead.suggested_trip_ids or '').split(',')[0].strip(),
        flow_key=lead.flow_key or 'manual_admin_handoff',
        reason_code='manual_admin_handoff',
        reason_text=data.get('reason', lead.notes or 'Manual handoff from admin.'),
        priority=data.get('priority', lead.priority or 'Medium'),
        channel=lead.channel or 'WhatsApp',
        customer_name=lead.customer_name or '',
        customer_summary=lead.notes or '',
        agent_summary='Manual handoff from admin UI.',
        notes=lead.notes or '',
        metadata={
            "trip_type": lead.preferred_trip_type or "",
            "trip_id": (lead.interested_trip_ids or lead.suggested_trip_ids or '').split(',')[0].strip(),
            "group_size": lead.group_size or 1,
            "raw_phone": lead.raw_phone or "",
        },
        lead_stage_override='Handoff Needed',
    )
    handoff_id = handoff["handoff_id"]
    db.session.expire_all()

    traveler = db.session.get(Traveler, lead.traveler_id) if lead.traveler_id else None
    socketio.emit('new_handoff', {
        'handoff_id': handoff_id,
        'traveler_name': traveler.full_name if traveler else lead.customer_name,
        'reason': handoff["reason_text"],
        'priority': handoff["priority"],
        'created_at': datetime.now(timezone.utc).isoformat(),
    })
    return jsonify({'status': 'ok', 'handoff_id': handoff_id})
