from __future__ import annotations
from flask import Blueprint, jsonify, request, current_app
from services.ai_agent.ai_agent_app.sheets.excel_gateway import ExcelSheetGateway

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")

@api_bp.get("/stats")
def get_stats():
    gateway: ExcelSheetGateway = current_app.config["SHEET_GATEWAY"]
    return jsonify(gateway.get_demo_stats())

@api_bp.get("/crm/preview")
def get_crm_preview():
    gateway: ExcelSheetGateway = current_app.config["SHEET_GATEWAY"]
    limit = request.args.get("limit", 15, type=int)
    return jsonify({"travelers": gateway.crm_preview(limit=limit)})

@api_bp.post("/agent/interact")
def agent_interact():
    gateway: ExcelSheetGateway = current_app.config["SHEET_GATEWAY"]
    payload = request.get_json(force=True)
    
    result = gateway.run_sales_cycle(
        full_name=payload.get("name", ""),
        raw_phone=payload.get("phone", ""),
        country_code=payload.get("countryCode", ""),
        trip_type=payload.get("tripType"),
        channel=payload.get("channel", "web-crm"),
        source=payload.get("source", "Web CRM"),
        agent_notes=payload.get("notes", ""),
        birthday=payload.get("birthday", ""),
        gender=payload.get("gender", ""),
        nationality=payload.get("nationality", ""),
    )
    return jsonify(result)

@api_bp.post("/bookings/draft")
def create_booking_draft():
    gateway: ExcelSheetGateway = current_app.config["SHEET_GATEWAY"]
    payload = request.get_json(force=True)
    
    result = gateway.create_booking(
        traveler_id=payload.get("travelerId", ""),
        traveler_name=payload.get("travelerName", ""),
        trip_id=payload.get("tripId", ""),
        room_type=payload.get("roomType", ""),
        channel=payload.get("channel", "web-crm"),
        lead_id=payload.get("leadId", ""),
        source=payload.get("source", "Web CRM"),
        agent_notes=payload.get("notes", ""),
        flight_option=payload.get("flightOption", ""),
        date_option=payload.get("dateOption", ""),
        currency=payload.get("currency", ""),
    )
    return jsonify(result)

@api_bp.post("/reset")
def reset_gateway():
    gateway: ExcelSheetGateway = current_app.config["SHEET_GATEWAY"]
    if not gateway.settings.demo_data_mode:
        return jsonify({"error": "demo_reset_disabled"}), 409
    gateway.reset_runtime_workbook()
    # Also clear session manager if needed, but Blueprint doesn't easily access app.config["SESSIONS"] 
    # without current_app.
    from services.ai_agent.ai_agent_app.agent import SessionFlowManager
    sessions: SessionFlowManager = current_app.config["SESSIONS"]
    sessions.clear()
    return jsonify({"ok": True, "stats": gateway.get_demo_stats()})
