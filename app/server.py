from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, redirect, render_template, request, session

from app.agent import SessionFlowManager
from app.config import Settings, load_settings
from app.sheets import ExcelSheetGateway, build_sheet_gateway
from app.webhooks import verify_webhook, validate_meta_signature
from app.logger import app_logger, webhook_logger


def _serialize_session(gateway: ExcelSheetGateway, session) -> dict[str, Any]:
    return {
        "id": session.id,
        "stage": session.stage,
        "messages": session.messages,
        "customerName": session.customer_name,
        "birthday": session.birthday,
        "gender": session.gender,
        "nationality": session.nationality,
        "rawPhone": session.raw_phone,
        "countryCode": session.country_code,
        "tripType": session.trip_type,
        "selectedTripId": session.selected_trip_id,
        "selectedTripName": session.selected_trip_name,
        "roomType": session.room_type,
        "preview": session.preview,
        "finalResult": session.final_result,
        "bookingResult": session.booking_result,
        "stats": gateway.get_demo_stats(),
    }


def _extract_booking_payload(session) -> tuple[str, str, str]:
    final_result = session.final_result or {}
    write_result = final_result.get("write_result") or {}
    created = write_result.get("created_traveler") or {}
    traveler = final_result.get("traveler") if isinstance(final_result.get("traveler"), dict) else {}
    lead = write_result.get("lead_update") or {}

    traveler_id = created.get("traveler_id") or traveler.get("traveler_id") or ""
    traveler_name = created.get("full_name") or traveler.get("full_name") or session.customer_name
    lead_id = lead.get("lead_id") or ""
    return traveler_id, traveler_name, lead_id


def create_app(
    source_workbook: Path | None = None,
    runtime_workbook: Path | None = None,
    settings: Settings | None = None,
) -> Flask:
    base_settings = settings or load_settings()
    if source_workbook is not None or runtime_workbook is not None:
        # Test and local override mode: operate purely on local Excel workbooks.
        base_settings = replace(base_settings, sheet_backend="excel")
    if source_workbook is not None:
        base_settings = replace(base_settings, excel_source_workbook=Path(source_workbook))
    if runtime_workbook is not None:
        base_settings = replace(base_settings, excel_runtime_workbook=Path(runtime_workbook))

    app = Flask(
        __name__,
        template_folder=str((Path(__file__).resolve().parent / "web" / "templates")),
        static_folder=str((Path(__file__).resolve().parent / "web" / "static")),
    )
    app.config["SETTINGS"] = base_settings
    app.config["SHEET_GATEWAY"] = build_sheet_gateway(base_settings)
    app.config["SESSIONS"] = SessionFlowManager(human_handoff_phone=base_settings.human_handoff_phone)
    app.secret_key = base_settings.app_secret_key

    from app.web.api_routes import api_bp
    from app.web.auth import auth_bp, login_required

    if not getattr(api_bp, "_auth_guard_registered", False):
        @api_bp.before_request
        def check_api_auth():
            if not session.get("logged_in"):
                return jsonify({"error": "unauthorized"}), 401

        api_bp._auth_guard_registered = True  # type: ignore[attr-defined]

    app.register_blueprint(api_bp)
    app.register_blueprint(auth_bp)

    validation_errors = base_settings.validate()
    if validation_errors:
        raise RuntimeError("Configuration error: " + " | ".join(validation_errors))

    gateway: ExcelSheetGateway = app.config["SHEET_GATEWAY"]
    gateway.ensure_runtime_workbook(reset=base_settings.demo_reset_on_start)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/chat")
    def chat():
        return redirect("/")

    @app.get("/crm")
    @login_required
    def crm_dashboard():
        return render_template("crm_dashboard.html")

    @app.get("/crm/travelers")
    @login_required
    def crm_travelers():
        return render_template("crm_travelers.html")

    @app.get("/crm/leads")
    @login_required
    def crm_leads():
        return render_template("crm_leads.html")

    @app.get("/crm/trips")
    @login_required
    def crm_trips():
        return render_template("crm_trips.html")

    @app.get("/api/health")
    def health():
        return jsonify(
            {
                "status": "ok",
                "runtimeWorkbookExists": gateway.runtime_path.exists(),
                "sourceWorkbookExists": gateway.source_path.exists(),
            }
        )

    @app.get("/api/bootstrap")
    def bootstrap():
        gateway.ensure_runtime_workbook()
        workbook_info = gateway.workbook_info()
        return jsonify(
            {
                "runtimeWorkbook": workbook_info["runtimeWorkbook"],
                "sourceWorkbook": workbook_info["sourceWorkbook"],
                "sheetBackend": app.config["SETTINGS"].sheet_backend,
                "stats": gateway.get_demo_stats(),
            }
        )
    
    @app.get("/webhook")
    def webhook_verify():
        settings: Settings = app.config["SETTINGS"]
        return verify_webhook(settings.meta_verify_token)

    @app.post("/webhook")
    def webhook_received():
        # Wrap logic to use decorator with dynamic settings
        settings = app.config["SETTINGS"]
        @validate_meta_signature(settings.meta_app_secret)
        def process_request():
            data = request.get_json(force=True)
            webhook_logger.info(f"Received webhook event")
            # Note: We just log and return 200 for now to satisfy Meta requirements
            return jsonify({"status": "received"})
        return process_request()

    @app.get("/api/crm/preview")
    def crm_preview():
        return jsonify({"travelers": gateway.crm_preview(limit=15)})

    @app.post("/api/reset")
    def reset_demo():
        gateway.reset_runtime_workbook()
        sessions: SessionFlowManager = app.config["SESSIONS"]
        sessions.clear()
        return jsonify({"ok": True, "stats": gateway.get_demo_stats()})

    @app.post("/api/session")
    def create_session_route():
        sessions: SessionFlowManager = app.config["SESSIONS"]
        session = sessions.create_session(gateway)
        return jsonify({"session": _serialize_session(gateway, session)})

    @app.get("/api/session/<session_id>")
    def get_session(session_id: str):
        sessions: SessionFlowManager = app.config["SESSIONS"]
        session = sessions.get(session_id)
        if session is None:
            return jsonify({"error": "session_not_found"}), 404
        return jsonify({"session": _serialize_session(gateway, session)})

    @app.post("/api/session/<session_id>/message")
    def send_message(session_id: str):
        sessions: SessionFlowManager = app.config["SESSIONS"]
        session = sessions.get(session_id)
        if session is None:
            return jsonify({"error": "session_not_found"}), 404

        try:
            payload = request.get_json(force=True)
            text = str(payload.get("text", "")).strip()
            if not text:
                return jsonify({"error": "empty_message"}), 400

            sessions.handle_message(session, text, gateway)
            return jsonify({"session": _serialize_session(gateway, session)})
        except Exception as e:
            app_logger.error(f"Error handling message for session {session_id}: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @app.post("/api/session/<session_id>/intake")
    def submit_intake(session_id: str):
        sessions: SessionFlowManager = app.config["SESSIONS"]
        session = sessions.get(session_id)
        if session is None:
            return jsonify({"error": "session_not_found"}), 404

        try:
            payload = request.get_json(force=True)
            sessions.submit_intake(session, payload, gateway)
            return jsonify({"session": _serialize_session(gateway, session)})
        except Exception as e:
            app_logger.error(f"Error in submit_intake for session {session_id}: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @app.post("/api/session/<session_id>/book")
    def create_booking(session_id: str):
        sessions: SessionFlowManager = app.config["SESSIONS"]
        session = sessions.get(session_id)
        if session is None:
            return jsonify({"error": "session_not_found"}), 404
        if session.final_result is None:
            return jsonify({"error": "session_not_ready_for_booking"}), 400

        payload = request.get_json(force=True)
        trip_id = str(payload.get("tripId", "")).strip()
        room_type = str(payload.get("roomType", "")).strip()
        if not trip_id or room_type not in {"Single", "Double", "Triple"}:
            return jsonify({"error": "invalid_booking_payload"}), 400

        traveler_id, traveler_name, lead_id = _extract_booking_payload(session)
        if not traveler_id:
            return jsonify({"error": "traveler_not_resolved"}), 400

        booking_result = gateway.create_booking(
            traveler_id=traveler_id,
            traveler_name=traveler_name,
            trip_id=trip_id,
            room_type=room_type,
            channel="web-demo",
            lead_id=lead_id,
            source="Web Demo Booking",
            agent_notes="Created from redesigned web demo.",
        )
        session.booking_result = booking_result
        session.messages.append(
            {
                "role": "assistant",
                "text": (
                    f"Booking draft {booking_result['booking_id']} created for {traveler_name} "
                    f"on {booking_result['trip_name']} ({booking_result['room_type']})."
                ),
            }
        )
        return jsonify({"session": _serialize_session(gateway, session)})

    @app.post("/api/lead/<lead_id>/qualify")
    def qualify_lead(lead_id: str):
        return jsonify({"ok": gateway.qualify_lead(lead_id)})

    return app

if __name__ == "__main__":
    app = create_app()
    settings = app.config["SETTINGS"]
    app.run(host=settings.app_host, port=settings.app_port, debug=(settings.app_env == "development"))
