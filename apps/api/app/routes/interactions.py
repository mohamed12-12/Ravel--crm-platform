# app/routes/interactions.py
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from app.models.interaction import Interaction
from app.models.traveler import Traveler
from app.extensions import db
from sqlalchemy import or_
from datetime import datetime, timezone
import uuid

interactions_bp = Blueprint('interactions', __name__, url_prefix='/interactions')


@interactions_bp.route('/')
def index():
    q = request.args.get('q', '')
    page = request.args.get('page', 1, type=int)
    per_page = 30

    query = Interaction.query
    if q:
        query = query.filter(or_(
            Interaction.interaction_id.ilike(f'%{q}%'),
            Interaction.traveler_id.ilike(f'%{q}%'),
            Interaction.customer_name.ilike(f'%{q}%'),
            Interaction.intent.ilike(f'%{q}%'),
        ))

    pagination = query.order_by(Interaction.timestamp.desc()).paginate(
        page=page, per_page=per_page, error_out=False)

    return render_template('interactions/index.html',
                           interactions=pagination.items,
                           pagination=pagination,
                           total_count=pagination.total,
                           q=q)


@interactions_bp.route('/', methods=['POST'])
def create():
    data = request.get_json() or request.form.to_dict()
    interaction_id = f"I-{uuid.uuid4().hex[:8].upper()}"

    interaction = Interaction(
        interaction_id=interaction_id,
        traveler_id=data.get('traveler_id') or None,
        customer_name=data.get('customer_name', ''),
        raw_phone=data.get('raw_phone', ''),
        channel=data.get('channel', 'WhatsApp'),
        intent=data.get('intent', ''),
        action_taken=data.get('action_taken', ''),
        agent_notes=data.get('agent_notes', ''),
        handoff_required=bool(data.get('handoff_required', False)),
        handoff_reason=data.get('handoff_reason', ''),
        flow_key=data.get('flow_key', ''),
        step_key=data.get('step_key', ''),
        language=data.get('language', 'ar'),
        outcome=data.get('outcome', ''),
        timestamp=datetime.now(timezone.utc),
    )
    db.session.add(interaction)
    db.session.commit()

    if request.is_json:
        return jsonify({'status': 'ok', 'interaction_id': interaction_id}), 201
    flash('Interaction logged.', 'success')
    return redirect(url_for('interactions.index'))
