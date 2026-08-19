# app/routes/handoffs.py
import json
import re
from typing import Any

from flask import Blueprint, render_template, request, jsonify, url_for
from app.extensions import db, socketio
from app.models.handoff import HandoffQueue
from app.models.traveler import Traveler
from app.services.conversations import session_id_from_handoff_notes
from datetime import datetime, timezone
import uuid

handoffs_bp = Blueprint('handoffs', __name__, url_prefix='/admin/handoffs')


def _load_handoff_payload(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _legacy_context_payload(text: str) -> dict[str, Any]:
    for line in str(text or "").splitlines():
        if line.startswith("handoff_context="):
            return _load_handoff_payload(line.split("=", 1)[1].strip())
    return {}


def _strip_legacy_context(text: str) -> str:
    lines = [line for line in str(text or "").splitlines() if not line.startswith("handoff_context=")]
    return "\n".join(line for line in lines).strip()


def _extract_employee_notes(text: str) -> str:
    match = re.search(r"(?:^|\n)Employee notes:\n(?P<notes>.*)\Z", text, re.DOTALL)
    return match.group("notes").strip() if match else ""


def _strip_employee_notes(text: str) -> str:
    return re.sub(r"\n{0,2}Employee notes:\n.*\Z", "", text, flags=re.DOTALL).strip()


def _metadata_step(metadata: dict[str, Any]) -> str:
    validation = metadata.get("validation") if isinstance(metadata.get("validation"), dict) else {}
    for value in (
        metadata.get("step"),
        metadata.get("current_step"),
        metadata.get("required_step"),
        validation.get("action"),
        validation.get("decision"),
    ):
        cleaned = str(value or "").strip()
        if cleaned:
            return cleaned
    return ""


def _parse_handoff_notes(raw_notes: str | None, *, reason: str = "", flow_key: str = "") -> dict[str, Any]:
    text = str(raw_notes or "").strip()
    parsed: dict[str, Any] = {
        "display_notes": "",
        "reason_code": "",
        "reason_text": str(reason or "").strip(),
        "customer_name": "",
        "customer_summary": "",
        "agent_summary": "",
        "resolution_notes": "",
        "step": "",
        "flow_key": str(flow_key or "").strip(),
    }
    if not text:
        return parsed

    payload = _load_handoff_payload(text) if text.startswith("{") else {}
    legacy_display = ""
    if not payload:
        payload = _legacy_context_payload(text)
        legacy_display = _strip_legacy_context(text)

    if payload:
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        parsed.update({
            "reason_code": str(payload.get("reason_code") or "").strip(),
            "reason_text": str(payload.get("reason_text") or parsed["reason_text"] or "").strip(),
            "customer_name": str(payload.get("customer_name") or "").strip(),
            "customer_summary": str(payload.get("customer_summary") or "").strip(),
            "agent_summary": str(payload.get("agent_summary") or "").strip(),
            "resolution_notes": str(
                payload.get("resolution_notes") or payload.get("notes") or _extract_employee_notes(legacy_display)
            ).strip(),
            "step": _metadata_step(metadata),
        })
        display_notes = _strip_employee_notes(legacy_display)
        if not display_notes:
            display_notes = parsed["agent_summary"] or parsed["customer_summary"] or parsed["reason_text"]
        parsed["display_notes"] = display_notes.strip()
        return parsed

    parsed["display_notes"] = text
    parsed["resolution_notes"] = text
    return parsed


def _notes_with_resolution(raw_notes: str | None, resolution_notes: str) -> str:
    text = str(raw_notes or "").strip()
    resolution = str(resolution_notes or "").strip()
    if text.startswith("{"):
        payload = _load_handoff_payload(text)
        if payload:
            payload["notes"] = resolution
            payload["resolution_notes"] = resolution
            return json.dumps(payload, ensure_ascii=False)

    payload = _legacy_context_payload(text)
    if payload:
        visible = _strip_employee_notes(_strip_legacy_context(text))
        lines = [visible] if visible else []
        if resolution:
            lines.append(f"Employee notes:\n{resolution}")
        compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        lines.append(f"handoff_context={compact}")
        return "\n\n".join(line for line in lines if line)

    return resolution

@handoffs_bp.route('/')
def index():
    # Fetch all handoffs and join with traveler to get names
    # Note: Using outer join because some handoffs might not have a traveler record yet
    # Optimization: Order by priority and created_at descending so newest are on top
    handoffs_query = db.session.query(HandoffQueue, Traveler.full_name, Traveler.whatsapp_raw)\
        .outerjoin(Traveler, HandoffQueue.traveler_id == Traveler.traveler_id)\
        .order_by(HandoffQueue.created_at.desc())\
        .all()
    
    # Organize by status
    pending = []
    in_progress = []
    resolved = []
    all_handoffs = []
    
    for h, name, phone in handoffs_query:
        data = h.to_dict()
        data['traveler_name'] = name or "Unknown Traveler"
        data['traveler_phone'] = phone or "N/A"
        note_details = _parse_handoff_notes(data.get('notes'), reason=data.get('reason'), flow_key=data.get('flow_key'))
        data.update(note_details)
        data['session_id'] = session_id_from_handoff_notes(data.get('notes'), data.get('idempotency_key'))
        data['traveler_url'] = url_for('travelers.detail', traveler_id=data['traveler_id']) if data.get('traveler_id') else ""
        data['lead_url'] = url_for('leads.detail', lead_id=data['lead_id']) if data.get('lead_id') else ""
        data['trip_url'] = url_for('trips.detail', trip_id=data['trip_id']) if data.get('trip_id') else ""
        data['session_url'] = url_for('interactions.session_detail', session_id=data['session_id']) if data.get('session_id') else ""
        data.pop('notes', None)
        data.pop('idempotency_key', None)
        
        # Standardize status for the board
        status = h.status
        if status == 'New': status = 'Pending'
        
        if status == 'Pending':
            pending.append(data)
            all_handoffs.append(data)
        elif status == 'In Progress':
            in_progress.append(data)
            all_handoffs.append(data)
        elif status == 'Resolved':
            # Cap resolved items to prevent infinite payload size over time
            if len(resolved) < 50:
                resolved.append(data)
                all_handoffs.append(data)
            
    return render_template('admin/handoffs.html', 
                           pending=pending, 
                           in_progress=in_progress, 
                           resolved=resolved,
                           handoff_items=all_handoffs)

@handoffs_bp.route('/', methods=['POST'])
def create_handoff():
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400
        
    handoff_id = data.get('handoff_id') or f"H-{uuid.uuid4().hex[:8].upper()}"
    
    handoff = HandoffQueue(
        handoff_id=handoff_id,
        lead_id=data.get('lead_id'),
        traveler_id=data.get('traveler_id'),
        trip_id=data.get('trip_id'),
        flow_key=data.get('flow_key'),
        reason=data.get('reason'),
        priority=data.get('priority', 'Medium'),
        channel=data.get('channel', 'WhatsApp'),
        status='Pending',
        created_at=datetime.now(timezone.utc)
    )
    
    db.session.add(handoff)
    db.session.commit()
    
    # Get traveler name for notification
    traveler = Traveler.query.filter_by(traveler_id=data.get('traveler_id')).first()
    traveler_name = traveler.full_name if traveler else "Unknown Traveler"
    
    # Emit WebSocket event
    socketio.emit('new_handoff', {
        'handoff_id': handoff_id,
        'traveler_name': traveler_name,
        'reason': data.get('reason'),
        'priority': data.get('priority', 'Medium'),
        'created_at': handoff.created_at.isoformat()
    })
    
    return jsonify({"status": "success", "handoff_id": handoff_id}), 201

@handoffs_bp.route('/<id>', methods=['PUT'])
def update_handoff(id):
    handoff = db.get_or_404(HandoffQueue, id)
    data = request.json
    
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    if 'status' in data:
        handoff.status = data['status']
    if 'owner' in data:
        handoff.owner = data['owner']
    if 'resolution_notes' in data:
        handoff.notes = _notes_with_resolution(handoff.notes, data['resolution_notes'])
    if 'notes' in data:
        handoff.notes = data['notes']
    if 'assigned_to' in data:
        handoff.assigned_to = data['assigned_to']
        
    db.session.commit()
    return jsonify({"status": "success"})

@handoffs_bp.route('/<id>', methods=['DELETE'])
def delete_handoff(id):
    handoff = db.get_or_404(HandoffQueue, id)
    db.session.delete(handoff)
    db.session.commit()
    return jsonify({"status": "success"})

@handoffs_bp.route('/pending')
def pending_count():
    count = HandoffQueue.query.filter(HandoffQueue.status.in_(['Pending', 'New'])).count()
    return jsonify({"count": count})
