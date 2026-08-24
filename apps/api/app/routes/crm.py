# app/routes/crm.py
import os
from dataclasses import replace
from pathlib import Path

from flask import Blueprint, request, jsonify, current_app
from app.services.identity import merge_travelers, find_duplicates
from app.extensions import db, limiter, socketio
import logging

logger = logging.getLogger(__name__)

AGENT_READ_ACTIONS = {
    "search_traveler",
    "find_traveler_by_phone",
    "get_traveler_profile",
    "get_traveler_profile_safe",
    "get_traveler_trip_history",
    "search_trips",
    "search_available_trips",
    "get_trip_details",
    "get_trip_media",
    "get_booking_status",
    "lookup_lead",
    "get_passport_status",
    "get_demo_stats",
    "crm_preview",
}
AGENT_WRITE_ACTIONS = {
    "create_traveler",
    "create_lead",
    "update_lead_stage",
    "create_booking_draft",
    "create_handoff",
    "create_private_trip_request",
    "set_guardian_consent",
    "flag_lead_guardian_approval",
}

crm_bp = Blueprint('crm', __name__, url_prefix='/api/crm')


def _agent_runtime():
    from services.ai_agent.ai_agent_app.agent.read_only_tools import ReadOnlyCRMTools
    from services.ai_agent.ai_agent_app.config import load_settings
    from app.services.agent_crm_bridge import PostgresAgentCRMTools
    from services.crm.system_services.config import load_system_settings
    from services.crm.system_services.unified_service import UnifiedCRMService
    from services.data_authority import load_data_authority

    uri = str(current_app.config.get("SQLALCHEMY_DATABASE_URI") or "").strip()
    authority = load_data_authority(environment=current_app.config.get("ENV", "development"))
    agent_settings = replace(
        load_settings(),
        crm_access_mode="shared_service",
        data_authority="crm",
    )
    if uri.startswith("sqlite:///"):
        db_path = Path(uri.removeprefix("sqlite:///")).resolve()
        system_settings = replace(
            load_system_settings(),
            db_path=db_path,
            data_authority=authority.authority,
            sheet_export_enabled=authority.export_enabled_for(load_system_settings().sheet_backend),
            allow_source_workbook_writes=False,
        )
        service = UnifiedCRMService(system_settings)
        return agent_settings, ReadOnlyCRMTools(agent_settings, service=service)
    return agent_settings, PostgresAgentCRMTools()


@crm_bp.route('/agent/read', methods=['POST'])
@limiter.limit(lambda: os.environ.get("CRM_AGENT_READ_RATE_LIMIT", "10000 per hour"))
def agent_read():
    data = request.get_json(silent=True) or {}
    action = str(data.get("action") or "").strip()
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    if action not in AGENT_READ_ACTIONS:
        return jsonify({"error": "unsupported_agent_read_action"}), 422
    try:
        _settings, tools = _agent_runtime()
        handler = getattr(tools, action)
        return jsonify({"result": handler(**payload)})
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 422
    except Exception as exc:
        logger.error("Agent CRM read failed: %s", exc, exc_info=True)
        return jsonify({"error": "agent_crm_read_failed"}), 500


@crm_bp.route('/agent/write', methods=['POST'])
def agent_write():
    data = request.get_json(silent=True) or {}
    action = str(data.get("action") or "").strip()
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    session_context = data.get("session_context") if isinstance(data.get("session_context"), dict) else {}
    if action not in AGENT_WRITE_ACTIONS:
        return jsonify({"error": "unsupported_agent_write_action"}), 422
    try:
        from services.ai_agent.ai_agent_app.agent.write_tool_executor import GeminiWriteToolExecutor

        settings, tools = _agent_runtime()
        executor = GeminiWriteToolExecutor(settings=settings, read_only_tools=tools)
        result = executor.execute(action=action, payload=payload, session_context=session_context)
        handoff_case = result.get("handoff_case") if isinstance(result.get("handoff_case"), dict) else {}
        if action == "create_handoff" and handoff_case and not handoff_case.get("deduplicated"):
            traveler = result.get("traveler") if isinstance(result.get("traveler"), dict) else {}
            socketio.emit(
                "new_handoff",
                {
                    "handoff_id": str(handoff_case.get("handoff_id") or ""),
                    "traveler_name": str(
                        traveler.get("full_name")
                        or payload.get("customer_name")
                        or "Unknown Traveler"
                    ),
                    "reason": str(handoff_case.get("reason_text") or "Employee review required."),
                    "priority": str(handoff_case.get("priority") or "Medium"),
                },
            )
        return jsonify({"result": result})
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 422
    except Exception as exc:
        logger.error("Agent CRM write failed: %s", exc, exc_info=True)
        return jsonify({"error": "agent_crm_write_failed"}), 500


@crm_bp.route('/agent/passport', methods=['POST'])
def agent_passport():
    data = request.get_json(silent=True) or {}
    traveler_id = str(data.get("traveler_id") or "").strip()
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    if not traveler_id:
        return jsonify({"error": "traveler_id is required"}), 422
    allowed_fields = {
        "passport_name",
        "passport_number",
        "passport_expiry",
        "passport_nationality",
        "passport_attachment_ref",
        "uploaded_by",
        "attachment_file_name",
        "attachment_original_name",
        "attachment_mime_type",
        "attachment_size",
        "notes",
    }
    clean_payload = {key: value for key, value in payload.items() if key in allowed_fields}
    try:
        _settings, tools = _agent_runtime()
        return jsonify({"result": tools.service.save_traveler_passport(traveler_id, **clean_payload)})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 422
    except Exception as exc:
        logger.error("Agent CRM passport write failed: %s", exc, exc_info=True)
        return jsonify({"error": "agent_crm_passport_write_failed"}), 500

@crm_bp.route('/resolve-identity', methods=['POST'])
def resolve_identity():
    """
    Merges duplicate travelers into a master traveler record.
    Expects: { "master_id": "TR001", "alias_ids": ["TR002", "TR003"] }
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing JSON body"}), 400
        
    master_id = data.get('master_id')
    alias_ids = data.get('alias_ids')
    
    if not master_id or not alias_ids:
        return jsonify({"error": "master_id and alias_ids are required"}), 422
        
    if not isinstance(alias_ids, list):
        return jsonify({"error": "alias_ids must be a list"}), 422

    try:
        result = merge_travelers(master_id, alias_ids, db.session)
        return jsonify({
            "status": "success",
            "message": f"Successfully merged {result['merged_count']} travelers into {master_id}",
            "details": result
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        logger.error(f"Error merging travelers: {e}", exc_info=True)
        return jsonify({"error": "An internal server error occurred during merge."}), 500

@crm_bp.route('/duplicates', methods=['GET'])
def list_duplicates():
    """
    Returns a list of potential duplicate traveler groups.
    """
    try:
        duplicates = find_duplicates(db.session)
        return jsonify({
            "count": len(duplicates),
            "duplicates": duplicates
        })
    except Exception as e:
        logger.error(f"Failed to fetch duplicates: {e}", exc_info=True)
        return jsonify({"error": "Failed to fetch duplicates", "details": str(e)}), 500
