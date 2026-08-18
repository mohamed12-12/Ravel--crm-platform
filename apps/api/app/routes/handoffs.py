# app/routes/handoffs.py
from flask import Blueprint, render_template, request, jsonify
from app.extensions import db, socketio
from app.models.handoff import HandoffQueue
from app.models.traveler import Traveler
from app.services.conversations import session_id_from_handoff_notes
from datetime import datetime, timezone
import uuid

handoffs_bp = Blueprint('handoffs', __name__, url_prefix='/admin/handoffs')


def _display_handoff_notes(raw_notes: str | None) -> str:
    text = str(raw_notes or "").strip()
    if not text:
        return ""
    lines = [line for line in text.splitlines() if not line.startswith("handoff_context=")]
    return "\n".join(line for line in lines).strip()

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
    
    for h, name, phone in handoffs_query:
        data = h.to_dict()
        data['traveler_name'] = name or "Unknown Traveler"
        data['traveler_phone'] = phone or "N/A"
        data['display_notes'] = _display_handoff_notes(data.get('notes'))
        data['session_id'] = session_id_from_handoff_notes(data.get('notes'), data.get('idempotency_key'))
        
        # Standardize status for the board
        status = h.status
        if status == 'New': status = 'Pending'
        
        if status == 'Pending':
            pending.append(data)
        elif status == 'In Progress':
            in_progress.append(data)
        elif status == 'Resolved':
            # Cap resolved items to prevent infinite payload size over time
            if len(resolved) < 50:
                resolved.append(data)
            
    return render_template('admin/handoffs.html', 
                           pending=pending, 
                           in_progress=in_progress, 
                           resolved=resolved)

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
