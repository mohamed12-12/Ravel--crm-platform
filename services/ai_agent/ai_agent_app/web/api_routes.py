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

    confirmed = payload.get("confirmed", payload.get("explicitConfirmation", payload.get("customerConfirmed", False)))
    if str(confirmed).strip().lower() not in {"1", "true", "yes", "y"} and confirmed is not True:
        return jsonify({
            "error": "request_not_ready",
            "message": "Please confirm the booking details before creating a booking draft.",
            "write_result_contract": {
                "status": "blocked",
                "executed": False,
                "reused": False,
                "record_type": "booking",
                "record_id": "",
                "idempotency_key": str(payload.get("idempotencyKey", "") or ""),
                "customer_confirmation_allowed": False,
                "error_code": "request_not_ready",
                "safe_customer_message_key": "booking.confirmation_required",
                "audit": {},
            },
        }), 409

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
        session_id=payload.get("sessionId", ""),
        idempotency_key=payload.get("idempotencyKey", ""),
        require_explicit_confirmation=True,
        customer_confirmed=confirmed,
    )
    status_code = 409 if (result.get("write_result_contract") or {}).get("status") == "blocked" else 200
    return jsonify(result), status_code

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
