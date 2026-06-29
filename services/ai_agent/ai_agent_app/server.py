from __future__ import annotations

import os
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
from typing import Any
from werkzeug.utils import secure_filename

from flask import Flask, jsonify, redirect, render_template, request, session

from services.ai_agent.ai_agent_app.agent import GeminiAgent, SessionFlowManager
from services.ai_agent.ai_agent_app.config import Settings, load_settings
from services.ai_agent.ai_agent_app.conversation_ai import GeminiConversationAI
from services.ai_agent.ai_agent_app.sheets import ExcelSheetGateway, build_sheet_gateway
from services.ai_agent.ai_agent_app.system_bridge import get_system_service
from services.ai_agent.llm import build_llm_provider
from services.instagram import MetaApiSettings, MetaGraphClient, build_instagram_reply
from services.instagram.payload_parser import parse_instagram_webhook
from services.instagram.webhooks import verify_webhook, validate_meta_signature
from services.ai_agent.ai_agent_app.logger import app_logger, webhook_logger

try:
    from services.crm.system_services.config import get_database_diagnostics
except Exception:  # pragma: no cover - diagnostics should never block startup
    get_database_diagnostics = None  # type: ignore[assignment]

ALLOWED_ATTACHMENT_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "pdf", "heic", "webp"}
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB


def _allowed_attachment(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_ATTACHMENT_EXTENSIONS


def _room_choice_label(room_type: str, room_group: str = "") -> str:
    if room_type == "Single":
        return "Single room"
    if room_group == "boys":
        return f"{room_type} boys room"
    if room_group == "girls":
        return f"{room_type} girls room"
    return f"{room_type} room"


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
        "phoneNormalization": session.phone_normalization,
        "pendingRawPhone": session.pending_raw_phone,
        "tripType": session.trip_type,
        "selectedTripId": session.selected_trip_id,
        "selectedTripName": session.selected_trip_name,
        "roomType": session.room_type,
        "roomGroup": session.room_group,
        "groupSize": session.group_size,
        "roomChoiceLabel": _room_choice_label(session.room_type, session.room_group) if session.room_type else "",
        "leadStatus": session.lead_status,
        "bookingStatus": session.booking_status,
        "handoffState": session.handoff_state,
        "preview": session.preview,
        "finalResult": session.final_result,
        "bookingResult": session.booking_result,
        "passportName": session.passport_name,
        "passportNumber": session.passport_number,
        "passportExpiry": session.passport_expiry,
        "passportNationality": session.passport_nationality,
        "passportAttachmentRef": session.passport_attachment_ref,
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
    using_runtime_overrides = source_workbook is not None or runtime_workbook is not None
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
    app.config["AI_AGENT_MODE"] = base_settings.ai_agent_mode
    app.config["SHEET_GATEWAY"] = build_sheet_gateway(base_settings)
    conversation_ai = None
    if base_settings.gemini_agent_enabled and not using_runtime_overrides:
        provider = build_llm_provider(base_settings)
        if provider is not None:
            conversation_ai = GeminiAgent.from_settings(base_settings, provider)
    elif base_settings.ai_enabled and not using_runtime_overrides:
        conversation_ai = GeminiConversationAI(
            api_key=base_settings.gemini_api_key,
            model=base_settings.gemini_model,
            system_prompt=base_settings.agent_conversation_prompt,
        )
    app.config["SESSIONS"] = SessionFlowManager(
        human_handoff_phone=base_settings.human_handoff_phone,
        agent_persona_name=base_settings.agent_persona_name,
        website_url=base_settings.website_url,
        post_trip_handoff_enabled=base_settings.post_trip_handoff_enabled,
        handoff_keywords=base_settings.post_trip_handoff_keywords,
        post_trip_handoff_responsible_employee=base_settings.post_trip_handoff_responsible_employee,
        default_country_code=base_settings.default_country_code,
        conversation_ai=conversation_ai,
    )
    app.secret_key = base_settings.app_secret_key

    from services.ai_agent.ai_agent_app.web.api_routes import api_bp
    from services.ai_agent.ai_agent_app.web.auth import auth_bp, login_required

    if not getattr(api_bp, "_auth_guard_registered", False):
        @api_bp.before_request
        def check_api_auth():
            if not session.get("logged_in"):
                return jsonify({"error": "unauthorized"}), 401

        api_bp._auth_guard_registered = True  # type: ignore[attr-defined]

    app.register_blueprint(api_bp)
    app.register_blueprint(auth_bp)
    app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
    app.config.setdefault("SESSION_COOKIE_SECURE", base_settings.app_env == "production")
    app.config.setdefault("REMEMBER_COOKIE_HTTPONLY", True)
    app.config.setdefault("REMEMBER_COOKIE_SAMESITE", "Lax")
    app.config.setdefault("REMEMBER_COOKIE_SECURE", base_settings.app_env == "production")

    validation_errors = base_settings.validate()
    if validation_errors:
        raise RuntimeError("Configuration error: " + " | ".join(validation_errors))

    gateway: ExcelSheetGateway = app.config["SHEET_GATEWAY"]
    gateway.ensure_runtime_workbook(reset=base_settings.demo_reset_on_start)

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if base_settings.app_env == "production":
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'",
            )
        return response

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/chat")
    def chat():
        return redirect("/")

    @app.get("/api/health")
    def health():
        diagnostics = get_database_diagnostics() if get_database_diagnostics is not None else None
        return jsonify(
            {
                "status": "ok",
                "runtimeWorkbookExists": gateway.runtime_path.exists(),
                "sourceWorkbookExists": gateway.source_path.exists(),
                "activeDbPath": diagnostics["db_path"] if diagnostics else str(_system_db_path()),
                "counts": diagnostics["counts"] if diagnostics else {},
            }
        )

    @app.get("/api/bootstrap")
    def bootstrap():
        gateway.ensure_runtime_workbook()
        workbook_info = gateway.workbook_info()
        diagnostics = get_database_diagnostics() if get_database_diagnostics is not None else None
        return jsonify(
            {
                "runtimeWorkbook": workbook_info["runtimeWorkbook"],
                "sourceWorkbook": workbook_info["sourceWorkbook"],
                "sheetBackend": "crm-db" if diagnostics else app.config["SETTINGS"].sheet_backend,
                "stats": gateway.get_demo_stats(),
                "activeDbPath": diagnostics["db_path"] if diagnostics else str(_system_db_path()),
                "dbDiagnostics": diagnostics,
            }
        )

    @app.get("/api/agent-config")
    def agent_config():
        """Return non-sensitive agent configuration to the frontend."""
        s: Settings = app.config["SETTINGS"]
        return jsonify(
            {
                "agentPersonaName": s.agent_persona_name or "Rahvel Agent",
                "websiteUrl": s.website_url,
                "postTripHandoffEnabled": s.post_trip_handoff_enabled,
                "defaultCountryCode": s.default_country_code,
                "aiAgentMode": s.ai_agent_mode,
            }
        )
    
    @app.get("/webhook")
    def webhook_verify():
        settings: Settings = app.config["SETTINGS"]
        # TODO(production): bind verification to the real Meta app/page setup and rotate verify tokens through secrets management.
        return verify_webhook(settings.meta_verify_token)

    @app.post("/webhook")
    def webhook_received():
        # Wrap logic to use decorator with dynamic settings.
        # TODO(production): add route-level rate limiting, raw-body audit logging, durable retry queues,
        # and human-review routing before processing real Instagram messages.
        settings = app.config["SETTINGS"]
        @validate_meta_signature(settings.meta_app_secret)
        def process_request():
            data = request.get_json(force=True)
            webhook_logger.info(f"Received webhook event")
            events = parse_instagram_webhook(data if isinstance(data, dict) else {})
            service = get_system_service(settings)
            persisted = 0
            duplicates = 0
            replies_sent = 0
            replies_failed = 0
            meta_client = None
            if settings.meta_page_access_token:
                meta_client = MetaGraphClient(
                    MetaApiSettings(
                        page_access_token=settings.meta_page_access_token,
                        graph_api_version=settings.meta_graph_api_version or "v23.0",
                    )
                )

            if service is not None:
                for event in events:
                    attachments = [
                        {
                            "type": attachment.attachment_type,
                            "url": attachment.url,
                            "payload": attachment.payload,
                        }
                        for attachment in event.attachments
                    ]
                    result = service.record_inbound_channel_event(
                        channel="Instagram",
                        message_key=event.event_id,
                        sender_id=event.sender_id,
                        recipient_id=event.recipient_id,
                        text=event.text,
                        attachments=attachments,
                        timestamp=(
                            datetime.fromtimestamp(event.timestamp / 1000, tz=timezone.utc)
                            if event.timestamp
                            else None
                        ),
                        flow_key="instagram",
                        step_key="inbound_webhook",
                        outcome="received",
                    )
                    if result.get("created"):
                        persisted += 1
                        if meta_client is not None:
                            draft = build_instagram_reply(event)
                            send_result = meta_client.send_instagram_text_message(event.sender_id, draft.text)
                            if send_result.ok:
                                replies_sent += 1
                                try:
                                    service.create_interaction(
                                        timestamp=(
                                            datetime.fromtimestamp(event.timestamp / 1000, tz=timezone.utc)
                                            if event.timestamp
                                            else datetime.now(timezone.utc)
                                        ),
                                        channel="Instagram",
                                        customer_name=f"Instagram sender {event.sender_id}",
                                        raw_phone=event.sender_id,
                                        integrated_whatsapp="",
                                        phone_lookup_key="",
                                        traveler_id="",
                                        matched_row=None,
                                        status_snapshot="WEBHOOK_REPLIED",
                                        intent="outbound_message",
                                        trip_type="",
                                        suggested_trips="",
                                        action_taken="outbound_reply_sent",
                                        handoff_required=False,
                                        handoff_reason="",
                                        agent_notes=(
                                            f"Outbound reply sent after inbound webhook. "
                                            f"Reason: {draft.reason}. Reply: {draft.text}"
                                        ),
                                        flow_key="instagram",
                                        step_key="outbound_reply",
                                        message_key=f"{event.event_id}:reply",
                                        language="",
                                        outcome="sent",
                                    )
                                except Exception as exc:
                                    webhook_logger.warning(f"Could not record outbound Instagram reply: {exc}")
                            else:
                                replies_failed += 1
                                webhook_logger.warning(
                                    "Instagram reply send failed for %s: %s %s",
                                    event.sender_id,
                                    send_result.status_code,
                                    send_result.response_json,
                                )
                    else:
                        duplicates += 1

            return jsonify(
                {
                    "status": "received",
                    "events": len(events),
                    "persisted": persisted,
                    "duplicates": duplicates,
                    "repliesSent": replies_sent,
                    "repliesFailed": replies_failed,
                }
            )
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
        if session.stage in {"completed", "handed_off", "cancelled"}:
            return jsonify({"session": _serialize_session(gateway, session)})
        if session.booking_result is not None:
            session.booking_status = str(session.booking_result.get("booking_status") or session.booking_status or "Draft")
            session.handoff_state = "completed"
            session.stage = "completed"
            return jsonify({"session": _serialize_session(gateway, session)})
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
        session.booking_status = str(booking_result.get("booking_status") or "Draft")
        session.handoff_state = "completed"
        session.stage = "completed"
        session.messages.append(
            {
                "role": "assistant",
                "text": (
                    f"Booking draft {booking_result['booking_id']} created for {traveler_name} "
                    f"on {booking_result['trip_name']} ({booking_result['room_type']}). The session is now completed."
                ),
            }
        )
        return jsonify({"session": _serialize_session(gateway, session)})

    @app.post("/api/lead/<lead_id>/qualify")
    def qualify_lead(lead_id: str):
        return jsonify({"ok": gateway.qualify_lead(lead_id)})

    @app.post("/api/session/<session_id>/passport_attachment")
    def upload_passport_attachment(session_id: str):
        """Accept a passport image upload (metadata-first).

        File is saved to a local uploads directory under the project root.
        The database/session only stores a reference path — never raw bytes.
        Supported formats: JPG, PNG, PDF, HEIC, WEBP, GIF (max 10 MB).
        """
        sessions: SessionFlowManager = app.config["SESSIONS"]
        sess = sessions.get(session_id)
        if sess is None:
            return jsonify({"error": "session_not_found"}), 404

        if "file" not in request.files:
            return jsonify({"error": "no_file_provided"}), 400

        f = request.files["file"]
        if not f or not f.filename:
            return jsonify({"error": "empty_filename"}), 400

        if not _allowed_attachment(f.filename):
            return jsonify({"error": "unsupported_file_type"}), 415

        safe_name = secure_filename(f.filename)
        uploads_root = Path(os.environ.get("AI_AGENT_UPLOAD_ROOT", str(Path(app.root_path).parents[3] / "uploads")))
        uploads_dir = uploads_root / "passport" / session_id
        uploads_dir.mkdir(parents=True, exist_ok=True)
        dest = uploads_dir / safe_name

        # Check size before writing
        f.stream.seek(0, 2)
        size = f.stream.tell()
        f.stream.seek(0)
        if size > MAX_ATTACHMENT_BYTES:
            return jsonify({"error": "file_too_large", "maxBytes": MAX_ATTACHMENT_BYTES}), 413

        f.save(dest)
        ref = str(dest.relative_to(uploads_root)) if uploads_root in dest.parents else str(dest)
        sessions.handle_passport_attachment(sess, ref)
        traveler = (sess.final_result or {}).get("traveler") or {}
        traveler_id = str(traveler.get("traveler_id") or "").strip()
        passport_save = {}
        if traveler_id and hasattr(gateway, "save_traveler_passport"):
            passport_save = gateway.save_traveler_passport(
                traveler_id,
                passport_name=sess.passport_name,
                passport_number=sess.passport_number,
                passport_expiry=sess.passport_expiry,
                passport_nationality=sess.passport_nationality,
                passport_attachment_ref=ref,
                uploaded_by="ai-agent-web",
                attachment_file_name=safe_name,
                attachment_original_name=f.filename,
                attachment_mime_type=f.mimetype or "",
                attachment_size=size,
                notes=f"Uploaded from AI agent session {session_id}",
            )
        app_logger.info(f"Passport attachment saved: session={session_id} ref={ref}")
        return jsonify(
            {
                "ok": True,
                "ref": ref,
                "passportSave": passport_save,
                "session": _serialize_session(gateway, sess),
            }
        )

    @app.get("/api/visa/<destination>")
    def get_visa_requirement(destination: str):
        """Return visa requirement information for a destination (table-based, always includes disclaimer)."""
        nationality = str(request.args.get("nationality", "")).strip()
        result = gateway.get_visa_requirement(destination, nationality=nationality)
        if result.get("source") == "unknown":
            # Unknown destination: instruct to contact human
            result["handoff_recommended"] = True
            result["handoff_reason"] = "Visa information is not available for this destination. Please contact our team for guidance."
        else:
            result["handoff_recommended"] = False
        return jsonify(result)

    return app

if __name__ == "__main__":
    app = create_app()
    settings = app.config["SETTINGS"]
    debug_enabled = os.getenv("APP_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    use_reloader = os.getenv("APP_USE_RELOADER", "").strip().lower() in {"1", "true", "yes", "on"}
    app.run(
        host=settings.app_host,
        port=settings.app_port,
        debug=debug_enabled,
        use_reloader=use_reloader,
    )
