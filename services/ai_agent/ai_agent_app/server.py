from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
from typing import Any
from werkzeug.utils import secure_filename

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, session

from services.ai_agent.ai_agent_app.agent import GeminiAgent, SessionFlowManager
from services.ai_agent.ai_agent_app.agent.response_format import response_completeness_issue
from services.ai_agent.ai_agent_app.agent.response_guard import guard_customer_response
from services.ai_agent.ai_agent_app.agent.write_response_gating import detect_write_record_type
from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
from services.ai_agent.ai_agent_app.agent.session_flow import detect_language
from services.ai_agent.ai_agent_app.config import Settings, load_settings
from services.ai_agent.ai_agent_app.conversation_ai import GeminiConversationAI
from services.ai_agent.ai_agent_app.sheets import ExcelSheetGateway, build_sheet_gateway
from services.ai_agent.ai_agent_app.system_bridge import get_system_service
from services.ai_agent.llm import build_llm_provider
from services.instagram import MetaApiSettings, MetaGraphClient, build_instagram_reply
from services.instagram.payload_parser import parse_instagram_webhook
from services.instagram.webhooks import verify_webhook, validate_meta_signature
from services.ai_agent.ai_agent_app.logger import app_logger, webhook_logger
from services.ai_agent.validation.validation_rules import normalize_flight_option, normalize_trip_type
from services.api_contracts import build_agent_openapi_contract
from services.crm.system_services.unified_service import UnifiedCRMService

try:
    from services.crm.system_services.config import get_database_diagnostics
except Exception:  # pragma: no cover - diagnostics should never block startup
    get_database_diagnostics = None  # type: ignore[assignment]

ALLOWED_ATTACHMENT_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "pdf", "heic", "webp"}
ALLOWED_ATTACHMENT_MIME_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/heic", "image/webp", "application/pdf"
}
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB
_PLACEHOLDER_PATTERN = re.compile(r"\{[a-zA-Z0-9_]+\}")
_PHONE_CANDIDATE_PATTERN = re.compile(r"(?:\+?\d[\d\s\-\(\)]{7,}\d)")
_TRIP_MEDIA_URL_PATTERN = re.compile(r"(?:https?://[^\s<>()]+)?/trips/media/[A-Za-z0-9._~:-]+")
_DIGIT_TRANSLATION = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def _allowed_attachment(filename: str, mimetype: str = "") -> bool:
    extension = filename.rsplit(".", 1)[1].lower() if "." in filename else ""
    if extension not in ALLOWED_ATTACHMENT_EXTENSIONS:
        return False
    return not mimetype or mimetype.lower() in ALLOWED_ATTACHMENT_MIME_TYPES


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").strip().split())

def _repair_mojibake_for_analysis(value: str) -> str:
    """Repair common double-encoded Arabic for classification only."""

    candidate = str(value or "")
    markers = ("\u00c3", "\u00d8", "\u00d9", "\u00e2", "\u00c5", "\u0178")
    for _ in range(2):
        if not any(marker in candidate for marker in markers):
            break
        try:
            repaired = candidate.encode("latin1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if repaired == candidate:
            break
        candidate = repaired
    return candidate


def _sanitize_gemini_reply(text: str, language: str) -> str:
    cleaned = _normalize_text(_PLACEHOLDER_PATTERN.sub("", str(text or "")))
    if cleaned:
        return cleaned
    if str(language or "").strip().lower().startswith("ar"):
        return "\u0645\u062d\u062a\u0627\u062c \u0623\u0639\u0631\u0641 \u062a\u0641\u0635\u064a\u0644\u0629 \u0625\u0636\u0627\u0641\u064a\u0629 \u0639\u0644\u0634\u0627\u0646 \u0623\u0642\u062f\u0631 \u0623\u0633\u0627\u0639\u062f\u0643 \u0628\u0634\u0643\u0644 \u0635\u062d\u064a\u062d."
    if str(language or "").strip().lower().startswith("ar"):
        return "أحتاج توضيحًا بسيطًا حتى أساعدك بشكل صحيح."
    return "I need one more detail so I can help you correctly."


def _strip_trip_media_urls(text: str) -> str:
    cleaned = _TRIP_MEDIA_URL_PATTERN.sub("", str(text or ""))
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _latest_write_result_from_agent_result(result: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    tool_requests = result.get("tool_requests") if isinstance(result, dict) else []
    if not isinstance(tool_requests, list):
        return None, ""
    for event in reversed(tool_requests):
        if not isinstance(event, dict) or not event.get("write"):
            continue
        tool_result = event.get("result") if isinstance(event.get("result"), dict) else {}
        write_result = tool_result.get("write_result_contract") if isinstance(tool_result.get("write_result_contract"), dict) and tool_result.get("write_result_contract") else None
        if write_result is None and isinstance(tool_result.get("write_result"), dict):
            write_result = tool_result["write_result"]
        if write_result is None:
            write_result = tool_result
        record_type = detect_write_record_type(str(event.get("name") or ""), write_result)
        if record_type != "write":
            return write_result, record_type
    return None, ""


def _extract_trip_media_from_agent_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    media: list[dict[str, Any]] = []
    tool_requests = result.get("tool_requests") if isinstance(result, dict) else []
    if not isinstance(tool_requests, list):
        return media
    for event in tool_requests:
        if not isinstance(event, dict) or str(event.get("name") or "") != "get_trip_media":
            continue
        tool_result = event.get("result") if isinstance(event.get("result"), dict) else {}
        items = tool_result.get("media") if isinstance(tool_result.get("media"), list) else []
        for item in items:
            if isinstance(item, dict) and (item.get("public_url") or item.get("url")):
                media.append(dict(item))
    return media


def _assistant_message_with_media(reply: str, media: list[dict[str, Any]], language: str) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "text": reply, "state": "completed"}
    safe_media = [dict(item) for item in media if isinstance(item, dict) and (item.get("public_url") or item.get("url"))]
    if safe_media:
        message["text"] = _strip_trip_media_urls(reply) or (
            "هذه الصورة الرسمية المؤكدة من CRM."
            if str(language or "").strip().lower().startswith("ar")
            else "Here is the verified official CRM trip image."
        )
        message["media"] = safe_media
    return message


def _trip_media_storage_root() -> Path:
    configured = os.environ.get("TRIP_MEDIA_ROOT", "").strip()
    if configured:
        return Path(configured).resolve()
    return (Path(__file__).resolve().parents[3] / "apps" / "api" / "instance" / "uploads" / "trips").resolve()


def _resolve_trip_media_storage_path(storage_key: str) -> Path | None:
    root = _trip_media_storage_root()
    parts = Path(str(storage_key or "").replace("\\", "/"))
    if parts.is_absolute() or ".." in parts.parts:
        return None
    path = (root / parts).resolve()
    if root != path and root not in path.parents:
        return None
    return path


def _infer_trip_type(text: str) -> str:
    normalized_trip_type = normalize_trip_type(text)
    if normalized_trip_type:
        return normalized_trip_type
    lowered = str(text or "").strip().lower()
    if not lowered:
        return ""
    if lowered == "1":
        return "local"
    if lowered == "2":
        return "international"
    if any(token in lowered for token in ("عمرة", "umrah", "حج", "hajj")):
        return "international"
    if any(token in lowered for token in ("داخل", "داخلي", "داخلية", "داخليه", "محلي", "محلية", "محليه", "local", "loca", "domestic")):
        return "local"
    if any(token in lowered for token in ("international", "خارجي", "خارجية", "خارجيه", "دولي", "دولية", "دوليه", "abroad", "overseas")):
        return "international"
    normalized = UnifiedCRMService.normalize_trip_type(text)
    return str(normalized or "").strip().lower()


def _infer_flight_option(text: str) -> str:
    lowered = str(text or "").strip().lower()
    if any(token in lowered for token in ("no flight", "no flights", "without flight", "without flights", "بدون طيران", "من غير طيران", "no flights")):
        return "Without Flight"
    if any(token in lowered for token in ("with flight", "with flights", "include flight", "include flights", "مع طيران", "شامل طيران")):
        return "With Flight"
    return ""


def _infer_currency(text: str) -> str:
    lowered = str(text or "").strip().lower()
    if any(token in lowered for token in ("egp", "جنيه", "مصري")):
        return "EGP"
    if any(token in lowered for token in ("usd", "dollar", "دولار")):
        return "USD"
    return ""


def _infer_human_request(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    keywords = (
        "human agent",
        "human",
        "real person",
        "someone from your team",
        "customer service",
        "support agent",
        "speak to agent",
        "عايز موظف",
        "عايز حد",
        "عايز انسان",
        "محتاج خدمة عملاء",
        "اكلم حد",
        "موظف",
        "انسان",
    )
    return any(token in lowered for token in keywords)


def _infer_passport_signal(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    return any(token in lowered for token in ("passport", "باسبور", "جواز", "معايا باسبور", "عندي باسبور"))


def _infer_room_type(text: str) -> str:
    lowered = str(text or "").strip().lower()
    if any(token in lowered for token in ("single", "سنجل", "فردي", "غرفة فردية")):
        return "Single"
    if any(token in lowered for token in ("double", "دبل", "ثنائي", "غرفة ثنائية")):
        return "Double"
    if any(token in lowered for token in ("triple", "تريبل", "ثلاثي", "غرفة ثلاثية")):
        return "Triple"
    return ""


def _infer_preferred_date(text: str) -> str:
    raw = _normalize_text(text)
    lowered = raw.lower()
    if re.search(r"\b\d{4}-\d{1,2}-\d{1,2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b", raw):
        return raw
    date_tokens = (
        "next month",
        "this month",
        "tomorrow",
        "weekend",
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
        "الشهر الجاي",
        "الشهر القادم",
        "الشهر ده",
        "بكره",
        "بكرة",
        "ويك اند",
        "تاريخ",
    )
    return raw if any(token in lowered for token in date_tokens) else ""


def _infer_trip_search_request(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    keywords = (
        "show me trips",
        "available trips",
        "what trips",
        "what do you have",
        "trip options",
        "travel options",
        "عايز رحلة",
        "عايزه رحلة",
        "ايه الرحلات",
        "إيه الرحلات",
        "الرحلات المتاحة",
        "رحلات ايه",
    )
    return any(token in lowered for token in keywords)


def _infer_language_question(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    return any(
        token in lowered
        for token in (
            "speak arabic",
            "can you speak arabic",
            "arabic?",
            "بتحكي عربي",
            "بتتكلم عربي",
            "تتكلم عربي",
            "عربي؟",
            "عربي",
        )
    )


def _infer_crm_identity_action(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    keywords = (
        "check my profile",
        "traveler profile",
        "my booking",
        "my reservation",
        "create lead",
        "save lead",
        "book",
        "booking",
        "reserve",
        "reservation",
        "احجز",
        "حجز",
        "ملفي",
        "بروفايل",
        "بياناتي",
    )
    return any(token in lowered for token in keywords)


def _infer_cancellation(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    return any(token == lowered or token in lowered for token in ("cancel", "stop", "not now", "الغاء", "إلغاء", "وقف", "مش دلوقتي"))


def _extract_phone_candidate(text: str) -> str:
    match = _PHONE_CANDIDATE_PATTERN.search(str(text or ""))
    if not match:
        return ""
    candidate = match.group(0).strip()
    digits = re.sub(r"\D", "", candidate)
    return candidate if len(digits) >= 8 else ""


def _explicit_english_request(text: str) -> bool:
    normalized = re.sub(r"[^\w\u0600-\u06ff]+", " ", str(text or "").casefold())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return any(
        phrase in normalized
        for phrase in (
            "speak english",
            "english please",
            "continue in english",
            "reply in english",
            "بالانجليزي",
            "بالانجليزى",
        )
    )


def _extract_gemini_message_hints(text: str, default_country_code: str) -> dict[str, Any]:
    analysis_text = _repair_mojibake_for_analysis(text)
    phone_candidate = _extract_phone_candidate(analysis_text)
    return {
        "candidate_trip_type": _infer_trip_type(analysis_text),
        "candidate_room_type": _infer_room_type(analysis_text),
        "candidate_preferred_date": _infer_preferred_date(analysis_text),
        "candidate_flight_option": normalize_flight_option(analysis_text) or _infer_flight_option(analysis_text),
        "candidate_currency": _infer_currency(analysis_text),
        "candidate_trip_search": _infer_trip_search_request(analysis_text),
        "candidate_language_question": _infer_language_question(analysis_text),
        "candidate_requires_whatsapp_for_crm": _infer_crm_identity_action(analysis_text),
        "candidate_human_request": _infer_human_request(analysis_text),
        "candidate_has_passport": _infer_passport_signal(analysis_text),
        "candidate_cancel": _infer_cancellation(analysis_text),
        "candidate_raw_phone": phone_candidate,
        "candidate_country_code": default_country_code if phone_candidate else "",
    }


def _merge_gemini_hints_into_session(session, hints: dict[str, Any]) -> None:
    raw_phone = str(hints.get("candidate_raw_phone") or "").strip()
    if raw_phone and not session.raw_phone:
        session.raw_phone = raw_phone
        session.pending_raw_phone = raw_phone
        if not session.country_code:
            session.country_code = str(hints.get("candidate_country_code") or session.country_code or "")
    trip_type = str(hints.get("candidate_trip_type") or "").strip()
    if trip_type and not session.trip_type:
        session.trip_type = trip_type
    room_type = str(hints.get("candidate_room_type") or "").strip()
    if room_type and not session.room_type:
        session.room_type = room_type
    preferred_date = str(hints.get("candidate_preferred_date") or "").strip()
    if preferred_date and not getattr(session, "preferred_date", ""):
        session.preferred_date = preferred_date
    flight_option = str(hints.get("candidate_flight_option") or "").strip()
    if flight_option and not session.flight_option:
        session.flight_option = flight_option
    currency = str(hints.get("candidate_currency") or "").strip()
    if currency and not session.currency:
        session.currency = currency


def _room_choice_label(room_type: str, room_group: str = "") -> str:
    if room_type == "Single":
        return "Single room"
    if room_group == "boys":
        return f"{room_type} boys room"
    if room_group == "girls":
        return f"{room_type} girls room"
    return f"{room_type} room"


def _serialize_session(gateway: ExcelSheetGateway, session) -> dict[str, Any]:
    runtime_mode = str(getattr(session, "agent_mode", "deterministic") or "deterministic").strip().lower() or "deterministic"
    chat_enabled = runtime_mode in {"tool_calling", "gemini"}
    requires_intake = runtime_mode == "deterministic" and session.stage == "awaiting_intake"
    safe_status_map = {
        "awaiting_phone": "Ready",
        "identity_required": "Waiting for WhatsApp number",
        "identity_lookup_pending": "Checking CRM",
        "traveler_found": "Traveler found",
        "traveler_not_found": "New traveler details required",
        "traveler_profile_incomplete": "New traveler details required",
        "traveler_creation_confirmation_required": "Profile confirmation required",
        "traveler_verified": "Traveler verified",
        "trip_type_required": "Ready",
        "public_trip_details": "Ready",
        "trip_discovery": "Trip preferences being collected",
        "trip_preferences_incomplete": "Trip preferences being collected",
        "trip_search_ready": "Searching trips",
        "trip_selection_required": "Waiting for customer response",
        "trip_results_available": "Searching trips",
        "room_type_required": "Waiting for customer response",
        "traveler_gender_required": "Waiting for customer response",
        "capacity_handoff_required": "Human review required",
        "group_size_required": "Waiting for customer response",
        "flight_option_required": "Waiting for customer response",
        "nationality_required": "Waiting for customer response",
        "birthday_required": "Waiting for customer response",
        "currency_required": "Waiting for customer response",
        "booking_ready": "Ready",
        "booking_confirmation_required": "Waiting for customer response",
        "no_trip_match": "Trip preferences being collected",
        "duplicate_traveler_detected": "Human review required",
        "human_handoff_required": "Human review required",
        "unable_to_continue": "Unable to continue",
        "awaiting_country_code": "Checking CRM",
        "awaiting_intake": "Understanding request",
        "awaiting_trip_type": "Checking CRM",
        "awaiting_confirmation": "Searching trips",
        "awaiting_passport_upload": "Waiting for customer response",
        "awaiting_group_size": "Understanding request",
        "awaiting_room_type": "Waiting for customer response",
        "awaiting_flight": "Waiting for customer response",
        "awaiting_currency": "Waiting for customer response",
        "awaiting_clarification": "Waiting for customer response",
        "gemini_conversation": "Understanding request",
        "collecting_context": "Understanding request",
        "waiting": "Waiting for customer response",
        "checking_crm": "Checking CRM",
        "searching_trips": "Searching trips",
        "ready": "Ready",
        "done": "Done",
        "completed": "Done",
        "handed_off": "Unable to complete request",
        "cancelled": "Unable to complete request",
        "error": "Unable to complete request",
    }
    return {
        "id": session.id,
        "runtime_mode": runtime_mode,
        "customer_status": safe_status_map.get(session.stage, "Understanding request"),
        "chat_enabled": chat_enabled,
        "requires_intake": requires_intake,
        "stage": session.stage,
        "uiStatus": safe_status_map.get(session.stage, "Understanding request"),
        "messages": session.messages,
        "agentMode": runtime_mode,
        "toolsUsed": list(getattr(session, "tools_used", []) or []),
        "fallbackUsed": bool(getattr(session, "fallback_used", False)),
        "customerName": session.customer_name,
        "birthday": session.birthday,
        "gender": session.gender,
        "nationality": session.nationality,
        "rawPhone": session.raw_phone,
        "countryCode": session.country_code,
        "phoneNormalization": session.phone_normalization,
        "pendingRawPhone": session.pending_raw_phone,
        "tripType": session.trip_type,
        "tripQuery": getattr(session, "trip_query", ""),
        "selectedTripId": session.selected_trip_id,
        "selectedTripName": session.selected_trip_name,
        "roomType": session.room_type,
        "roomGroup": session.room_group,
        "roomRequirements": getattr(session, "room_requirements", {}),
        "groupSize": session.group_size,
        "flightOption": session.flight_option,
        "preferredDate": getattr(session, "preferred_date", ""),
        "roomChoiceLabel": _room_choice_label(session.room_type, session.room_group) if session.room_type else "",
        "leadStatus": session.lead_status,
        "bookingStatus": session.booking_status,
        "handoffState": session.handoff_state,
        "preview": session.preview,
        "finalResult": session.final_result,
        "bookingResult": session.booking_result,
        "bookingCompleted": bool(getattr(session, "booking_completed", False)),
        "previousBookingResult": getattr(session, "previous_booking_result", {}),
        "passportName": session.passport_name,
        "passportNumber": session.passport_number,
        "passportExpiry": session.passport_expiry,
        "passportNationality": session.passport_nationality,
        "passportAttachmentRef": session.passport_attachment_ref,
        "passportRequired": str(session.trip_type or "").strip().lower() == "international",
        "passportUploadEnabled": runtime_mode in {"deterministic", "tool_calling", "gemini"},
        "stats": gateway.get_demo_stats(),
    }


def _safe_gemini_fallback_message(language: str) -> str:
    if str(language or "").strip().lower().startswith("ar"):
        return "\u0623\u0648\u0627\u062c\u0647 \u0645\u0634\u0643\u0644\u0629 \u0645\u0624\u0642\u062a\u0629 \u0641\u064a \u0627\u0644\u0648\u0635\u0648\u0644 \u0625\u0644\u0649 \u0627\u0644\u0645\u0633\u0627\u0639\u062f \u0627\u0644\u0622\u0646. \u062d\u0627\u0648\u0644 \u0645\u0631\u0629 \u0623\u062e\u0631\u0649 \u0628\u0639\u062f \u0642\u0644\u064a\u0644."
    if str(language or "").strip().lower().startswith("ar"):
        return "أواجه مشكلة مؤقتة في الوصول إلى المساعد الآن. حاول مرة أخرى بعد قليل."
    return "I'm having trouble accessing the assistant right now. Please try again in a moment."


def _safe_customer_runtime_error(language: str) -> str:
    if str(language or "").strip().lower().startswith("ar"):
        return "\u062d\u062f\u062b\u062a \u0645\u0634\u0643\u0644\u0629 \u0645\u0624\u0642\u062a\u0629 \u0623\u062b\u0646\u0627\u0621 \u0645\u062a\u0627\u0628\u0639\u0629 \u0627\u0644\u0637\u0644\u0628. \u0623\u0633\u062a\u0637\u064a\u0639 \u0627\u0644\u0645\u062a\u0627\u0628\u0639\u0629 \u0645\u0639\u0643 \u0627\u0644\u0622\u0646\u060c \u0623\u0648 \u064a\u0645\u0643\u0646\u0646\u064a \u062a\u062d\u0648\u064a\u0644\u0643 \u0625\u0644\u0649 \u0645\u0648\u0638\u0641 \u0625\u0630\u0627 \u0623\u062d\u0628\u0628\u062a."
    if str(language or "").strip().lower().startswith("ar"):
        return "حدثت مشكلة مؤقتة أثناء متابعة الطلب. أستطيع المتابعة معك الآن، أو يمكنني تحويلك إلى موظف إذا أحببت."
    return "I hit a temporary issue while handling that request. I can continue from here, or connect you with a human agent if you prefer."


def _gemini_opening_message(agent_name: str) -> str:
    name = str(agent_name or "").strip() or "Ravel Agent"
    return (
        f"Hello, I'm {name}, Ravel Traveler's travel assistant. "
        "Tell me what kind of trip you're thinking about, and I'll help you find the right option."
    )


def _extract_session_linked_ids(session) -> dict[str, str]:
    linked_ids: dict[str, str] = {
        "traveler_id": "",
        "lead_id": "",
        "booking_id": "",
    }
    final_result = session.final_result if isinstance(session.final_result, dict) else {}
    booking_result = session.booking_result if isinstance(session.booking_result, dict) else {}
    preview = session.preview if isinstance(session.preview, dict) else {}

    traveler = final_result.get("traveler") if isinstance(final_result.get("traveler"), dict) else {}
    write_result = final_result.get("write_result") if isinstance(final_result.get("write_result"), dict) else {}
    created_traveler = (
        write_result.get("created_traveler") if isinstance(write_result.get("created_traveler"), dict) else {}
    )
    lead_update = write_result.get("lead_update") if isinstance(write_result.get("lead_update"), dict) else {}
    preview_traveler = preview.get("traveler") if isinstance(preview.get("traveler"), dict) else {}

    linked_ids["traveler_id"] = str(
        created_traveler.get("traveler_id")
        or traveler.get("traveler_id")
        or preview_traveler.get("traveler_id")
        or ""
    ).strip()
    linked_ids["lead_id"] = str(lead_update.get("lead_id") or final_result.get("lead_id") or "").strip()
    linked_ids["booking_id"] = str(booking_result.get("booking_id") or "").strip()
    return linked_ids


def _preferred_traveler_record(session) -> dict[str, Any]:
    preview = session.preview if isinstance(session.preview, dict) else {}
    final_result = session.final_result if isinstance(session.final_result, dict) else {}
    write_result = final_result.get("write_result") if isinstance(final_result.get("write_result"), dict) else {}
    candidates = (
        preview.get("traveler"),
        final_result.get("traveler"),
        write_result.get("created_traveler"),
    )
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}


def _session_trip_result(session) -> dict[str, Any]:
    preview = session.preview if isinstance(session.preview, dict) else {}
    final_result = session.final_result if isinstance(session.final_result, dict) else {}
    for container in (preview, final_result):
        trip_result = container.get("trip_result") if isinstance(container.get("trip_result"), dict) else {}
        if trip_result:
            return trip_result
    return {}


def _selected_trip_snapshot(session) -> dict[str, Any]:
    trip_id = str(getattr(session, "selected_trip_id", "") or "").strip()
    if not trip_id:
        return {}
    trip_result = _session_trip_result(session)
    candidates = [*list(trip_result.get("open_trips") or []), *list(trip_result.get("date_tbd_trips") or [])]
    for trip in candidates:
        if str(trip.get("trip_id") or "").strip() == trip_id:
            return dict(trip)
    return {}


def _effective_trip_type(session) -> str:
    selected_trip = _selected_trip_snapshot(session)
    selected_type = str(selected_trip.get("type") or selected_trip.get("trip_type") or "").strip().lower()
    if selected_type in {"local", "international"}:
        return selected_type
    trip_type = str(getattr(session, "trip_type", "") or "").strip().lower()
    if trip_type in {"local", "international"}:
        return trip_type
    return trip_type


def _sync_session_lead_snapshot(gateway, session) -> dict[str, Any]:
    sync = getattr(gateway, "sync_live_agent_lead", None)
    if not callable(sync):
        return {}

    linked_ids = _extract_session_linked_ids(session)
    lead_id = linked_ids["lead_id"]
    if not lead_id:
        return {}

    traveler = _preferred_traveler_record(session)
    trip_result = _session_trip_result(session)
    selected_trip = _selected_trip_snapshot(session)
    suggested_trip_ids = [
        str(item.get("trip_id") or "").strip()
        for item in [*list(trip_result.get("open_trips") or []), *list(trip_result.get("date_tbd_trips") or [])]
        if str(item.get("trip_id") or "").strip()
    ]
    passport_ref = str(getattr(session, "passport_attachment_ref", "") or "").strip()
    trip_type = _effective_trip_type(session)
    passport_status = ""
    if trip_type == "international":
        passport_status = "received" if passport_ref else "pending"

    try:
        return sync(
            lead_id,
            customer_name=str(getattr(session, "customer_name", "") or traveler.get("full_name") or "").strip(),
            raw_phone=str(getattr(session, "raw_phone", "") or getattr(session, "pending_raw_phone", "") or "").strip(),
            traveler_id=linked_ids["traveler_id"] or str(traveler.get("traveler_id") or "").strip(),
            preferred_trip_type=trip_type,
            interested_trip_ids=str(getattr(session, "selected_trip_id", "") or "").strip(),
            suggested_trip_ids=", ".join(dict.fromkeys(suggested_trip_ids)),
            group_size=getattr(session, "group_size", 1) or 1,
            room_type=str(getattr(session, "room_type", "") or "").strip(),
            room_group=str(getattr(session, "room_group", "") or "").strip(),
            flight_option=str(getattr(session, "flight_option", "") or "").strip(),
            preferred_date=str(getattr(session, "preferred_date", "") or "").strip(),
            current_step=str(getattr(session, "stage", "") or "").strip(),
            language=str(getattr(session, "language", "") or "").strip(),
            booking_id=linked_ids["booking_id"],
            passport_attachment_ref=passport_ref,
            passport_status=passport_status,
            channel="web",
            lead_source="Rahma Sales AI",
            handoff_required=True if str(getattr(session, "handoff_state", "") or "").strip().lower() == "handed_off" else None,
            handoff_reason=str(
                ((session.final_result or {}).get("handoff_reason") if isinstance(session.final_result, dict) else "")
                or ""
            ).strip(),
        )
    except Exception as exc:
        app_logger.warning("Lead sync from session failed session=%s lead=%s error=%s", getattr(session, "id", ""), lead_id, exc)
        return {}


def _traveler_summary(record: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record, dict) or not record:
        return {}
    status = str(record.get("status") or "Active").strip() or "Active"
    local_trips = int(record.get("local_trips_count") or record.get("local_trips") or 0)
    international_trips = int(record.get("international_trips_count") or record.get("international_trips") or 0)
    total_trips = int(record.get("total_trips") or (local_trips + international_trips))
    return {
        "full_name": str(record.get("full_name") or "").strip(),
        "traveler_id": str(record.get("traveler_id") or "").strip(),
        "status": status,
        "local_trips": local_trips,
        "international_trips": international_trips,
        "total_trips": total_trips,
        "vip_status": status.upper() == "VIP",
    }


def _conversation_memory_snapshot(session, user_text: str = "") -> dict[str, Any]:
    """Expose bounded conversational memory while keeping the full session server-side."""

    user_messages = [
        str(message.get("text") or "").strip()[:240]
        for message in getattr(session, "messages", [])
        if isinstance(message, dict) and message.get("role") == "user"
    ]
    current_message = str(user_text or "").strip()[:240]
    if current_message and (not user_messages or user_messages[-1] != current_message):
        user_messages.append(current_message)
    return {
        "turn_count": len(user_messages),
        "pending_stage": str(getattr(session, "stage", "") or ""),
        "pending_question": str(getattr(session, "stage", "") or ""),
        "confirmed_facts": {
            "customer_name": str(getattr(session, "customer_name", "") or ""),
            "raw_phone": str(getattr(session, "raw_phone", "") or ""),
            "nationality": str(getattr(session, "nationality", "") or ""),
            "birthday": str(getattr(session, "birthday", "") or ""),
            "currency": str(getattr(session, "currency", "") or ""),
            "trip_type": str(getattr(session, "trip_type", "") or ""),
            "selected_trip_id": str(getattr(session, "selected_trip_id", "") or ""),
            "selected_trip_name": str(getattr(session, "selected_trip_name", "") or ""),
            "room_group": str(getattr(session, "room_group", "") or ""),
            "room_type": str(getattr(session, "room_type", "") or ""),
            "group_size": getattr(session, "group_size", 0) or 0,
            "flight_option": str(getattr(session, "flight_option", "") or ""),
        },
        "user_messages": user_messages[-20:],
    }

def _build_gemini_session_context(session, user_text: str) -> dict[str, Any]:
    linked_ids = _extract_session_linked_ids(session)
    traveler_snapshot = _traveler_summary(_preferred_traveler_record(session))
    hints = _extract_gemini_message_hints(user_text, session.country_code or "20")
    effective_raw_phone = str(session.raw_phone or session.pending_raw_phone or hints.get("candidate_raw_phone") or "").strip()
    effective_country_code = str(session.country_code or hints.get("candidate_country_code") or "").strip()
    effective_trip_type = str(session.trip_type or hints.get("candidate_trip_type") or "").strip()
    effective_room_type = str(session.room_type or hints.get("candidate_room_type") or "").strip()
    effective_preferred_date = str(getattr(session, "preferred_date", "") or hints.get("candidate_preferred_date") or "").strip()
    effective_flight_option = str(session.flight_option or hints.get("candidate_flight_option") or "").strip()
    effective_currency = str(session.currency or hints.get("candidate_currency") or "").strip()
    effective_stage = str(session.stage or "").strip() or "gemini_conversation"
    if effective_stage not in {"completed", "handed_off", "cancelled"}:
        effective_stage = "gemini_conversation"
    return {
        "session_id": session.id,
        "stage": effective_stage,
        "workflow_stage": session.stage,
        "language": session.language,
        "customer_name": session.customer_name,
        "birthday": session.birthday,
        "gender": session.gender,
        "nationality": session.nationality,
        "raw_phone": effective_raw_phone,
        "pending_raw_phone": session.pending_raw_phone or effective_raw_phone,
        "country_code": effective_country_code,
        "trip_type": effective_trip_type,
        "trip_query": getattr(session, "trip_query", ""),
        "selected_trip_id": session.selected_trip_id,
        "selected_trip_name": session.selected_trip_name,
        "room_type": effective_room_type,
        "room_group": session.room_group,
        "room_requirements": getattr(session, "room_requirements", {}),
        "group_size": session.group_size,
        "preferred_date": effective_preferred_date,
        "flight_option": effective_flight_option,
        "currency": effective_currency,
        "lead_status": session.lead_status,
        "booking_status": session.booking_status,
        "handoff_state": session.handoff_state,
        "passport_attachment_ref": session.passport_attachment_ref,
        "traveler_id": linked_ids["traveler_id"],
        "lead_id": linked_ids["lead_id"],
        "booking_id": linked_ids["booking_id"],
        "known_traveler": traveler_snapshot,
        "preview": session.preview,
        "final_result": session.final_result,
        "booking_result": session.booking_result,
        "last_user_message": user_text,
        "conversation_history": list(session.messages[-12:]),
        "conversation_memory": _conversation_memory_snapshot(session, user_text),
        **hints,
    }

def _post_booking_terms(text: str) -> dict[str, bool]:
    lowered = " ".join(str(text or "").strip().casefold().split())
    return {
        "book": any(term in lowered for term in (
            "\u0639\u0627\u064a\u0632 \u0627\u062d\u062c\u0632",
            "\u0639\u0627\u064a\u0632 \u0623\u062d\u062c\u0632",
            "\u062d\u062c\u0632 \u062c\u062f\u064a\u062f",
            "\u062d\u062c\u0632 \u062a\u0627\u0646\u064a",
            "i want to book",
            "book again",
            "another booking",
            "start a new booking",
            "another trip",
        )),
        "explicit_new": any(term in lowered for term in ("\u062a\u0627\u0646\u064a", "\u062c\u062f\u064a\u062f", "again", "another", "new")),
        "status": any(term in lowered for term in ("\u062d\u0627\u0644\u0629 \u0627\u0644\u062d\u062c\u0632", "\u062d\u062c\u0632\u064a", "\u0627\u062a\u0633\u062c\u0644", "booking status", "was my booking created")),
        "ack": lowered in {"\u062a\u0645\u0627\u0645", "\u0645\u0627\u0634\u064a", "\u0627\u0648\u0643\u064a", "\u0634\u0643\u0631\u0627", "ok", "okay", "thanks", "thank you", "understood"},
        "negative": lowered in {"\u0644\u0627", "\u0645\u0641\u064a\u0634", "no", "nothing"},
    }


def _handle_gemini_post_booking_message(session, text: str) -> bool:
    has_context = bool(
        getattr(session, "booking_completed", False)
        or isinstance(getattr(session, "booking_result", None), dict)
        or str(getattr(session, "stage", "") or "") in {"completed", "booking_created", "handoff_created", "post_booking_support", "new_booking_intent"}
    )
    if not has_context:
        return False
    terms = _post_booking_terms(text)
    if not any(terms.values()) and str(getattr(session, "stage", "") or "") not in {"completed", "post_booking_support", "new_booking_intent"}:
        return False
    session.messages.append({"role": "user", "text": text})
    arabic = str(getattr(session, "language", "") or "").startswith("ar")
    if terms["status"]:
        booking = session.booking_result if isinstance(session.booking_result, dict) else {}
        booking_id = str(booking.get("booking_id") or "booking").strip()
        status = str(booking.get("booking_status") or session.booking_status or "Draft").strip()
        reply = f"\u0623\u064a\u0648\u0647\u060c {booking_id} \u0645\u062a\u0633\u062c\u0644 \u0648\u062d\u0627\u0644\u062a\u0647 {status}. \u0641\u0631\u064a\u0642 Ravel \u0647\u064a\u062a\u0648\u0627\u0635\u0644 \u0645\u0639\u0627\u0643." if arabic else f"Yes, {booking_id} is created and its status is {status}. The Ravel team will follow up with you."
    elif terms["ack"]:
        reply = "\u062a\u0645\u0627\u0645\u060c \u0623\u0646\u0627 \u0645\u0639\u0627\u0643 \u0644\u0648 \u0627\u062d\u062a\u062c\u062a \u0623\u064a \u0645\u0633\u0627\u0639\u062f\u0629." if arabic else "All good. I’m here if you need anything else."
    elif terms["negative"]:
        reply = "\u0647\u0644 \u062a\u0642\u0635\u062f \u0623\u0646\u0643 \u0644\u0627 \u062a\u0631\u064a\u062f \u0628\u062f\u0621 \u062d\u062c\u0632 \u062c\u062f\u064a\u062f\u061f" if arabic else "Do you mean you don’t want to start a new booking?"
    elif terms["book"] and terms["explicit_new"]:
        if isinstance(session.booking_result, dict) and session.booking_result:
            session.previous_booking_result = dict(session.booking_result)
        session.trip_type = ""
        session.trip_query = ""
        session.selected_trip_id = ""
        session.selected_trip_name = ""
        session.room_type = ""
        session.room_group = ""
        session.room_requirements = {}
        session.group_size = 1
        session.flight_option = ""
        session.currency = ""
        session.stage = "new_booking_intent"
        reply = "\u0628\u0627\u0644\u062a\u0623\u0643\u064a\u062f\u060c \u064a\u0645\u0643\u0646\u0646\u0627 \u0628\u062f\u0621 \u062d\u062c\u0632 \u062c\u062f\u064a\u062f. \u0647\u0644 \u062a\u0631\u064a\u062f \u0627\u0644\u062d\u062c\u0632 \u0641\u064a \u0646\u0641\u0633 \u0627\u0644\u0631\u062d\u0644\u0629 \u0623\u0645 \u062a\u0628\u062d\u062b \u0639\u0646 \u0631\u062d\u0644\u0629 \u0623\u062e\u0631\u0649\u061f" if arabic else "Of course. We can start a new booking. Would you like the same trip, or are you looking for another trip?"
    elif terms["book"]:
        session.stage = "post_booking_support"
        reply = "\u0647\u0644 \u062a\u0631\u064a\u062f \u0628\u062f\u0621 \u062d\u062c\u0632 \u062c\u062f\u064a\u062f\u060c \u0623\u0645 \u062a\u0631\u064a\u062f \u0645\u062a\u0627\u0628\u0639\u0629 \u0627\u0644\u062d\u062c\u0632 \u0627\u0644\u062d\u0627\u0644\u064a\u061f" if arabic else "Do you want to start a new booking, or follow up on the current booking?"
    else:
        reply = "\u0645\u062d\u062a\u0627\u062c \u0623\u0639\u0631\u0641 \u062a\u0641\u0635\u064a\u0644\u0629 \u0625\u0636\u0627\u0641\u064a\u0629 \u0639\u0644\u0634\u0627\u0646 \u0623\u0642\u062f\u0631 \u0623\u0633\u0627\u0639\u062f\u0643 \u0628\u0634\u0643\u0644 \u0635\u062d\u064a\u062d." if arabic else "I need one more detail so I can help you correctly."
    if session.stage != "new_booking_intent":
        session.stage = "post_booking_support"
    session.booking_completed = True
    session.messages.append({"role": "assistant", "text": reply, "state": "completed"})
    session.tools_used = []
    session.fallback_used = False
    return True


def _gemini_option_number(text: str) -> int:
    normalized = str(text or "").translate(_DIGIT_TRANSLATION).strip()
    match = re.fullmatch(r"([1-9])[\s.)_\-]*", normalized)
    return int(match.group(1)) if match else 0


def _gemini_preview(session) -> dict[str, Any]:
    preview = getattr(session, "preview", None)
    if isinstance(preview, dict):
        return dict(preview)
    return {}


def _gemini_collection_state(session) -> dict[str, bool]:
    preview = _gemini_preview(session)
    state = preview.get("collection_state") if isinstance(preview.get("collection_state"), dict) else {}
    return dict(state)


def _gemini_update_collection_state(session, **updates: bool) -> None:
    preview = _gemini_preview(session)
    state = dict(preview.get("collection_state") if isinstance(preview.get("collection_state"), dict) else {})
    for key, value in updates.items():
        state[key] = bool(value)
    preview["collection_state"] = state
    session.preview = preview


def _gemini_verified_traveler(session) -> dict[str, Any]:
    preview = _gemini_preview(session)
    traveler = preview.get("traveler") if isinstance(preview.get("traveler"), dict) else {}
    if traveler:
        return dict(traveler)
    final_result = getattr(session, "final_result", None)
    if isinstance(final_result, dict) and isinstance(final_result.get("traveler"), dict):
        return dict(final_result["traveler"])
    return {}


def _gemini_append_verified_reply(session, text: str, *, stage: str) -> None:
    session.stage = stage
    session.messages.append(_assistant_message_with_media(text, [], session.language))
    session.tools_used = []
    session.fallback_used = False
    session.agent_mode = "gemini"


def _gemini_trip_type_prompt(language: str) -> str:
    if str(language or "").startswith("ar"):
        return "هل تبحث عن رحلة محلية داخل مصر أم رحلة دولية خارج مصر؟\n\n1. محلية\n2. دولية"
    return "Are you looking for a local trip or an international trip?\n\n1. Local trip\n2. International trip"


def _gemini_explicit_trip_type_choice(text: str) -> str:
    option = _gemini_option_number(text)
    if option == 1:
        return "local"
    if option == 2:
        return "international"
    normalized = _normalize_text(text).casefold()
    if not normalized:
        return ""
    compact = re.sub(r"[^\w\u0600-\u06ff]+", " ", normalized).strip()
    local_terms = {
        "local",
        "local trip",
        "domestic",
        "domestic trip",
        "inside egypt",
        "\u0645\u062d\u0644\u064a",
        "\u0645\u062d\u0644\u064a\u0629",
        "\u0645\u062d\u0644\u064a\u0647",
        "\u062f\u0627\u062e\u0644 \u0645\u0635\u0631",
    }
    international_terms = {
        "international",
        "international trip",
        "abroad",
        "outside egypt",
        "\u062f\u0648\u0644\u064a",
        "\u062f\u0648\u0644\u064a\u0629",
        "\u062f\u0648\u0644\u064a\u0647",
        "\u062e\u0627\u0631\u062c \u0645\u0635\u0631",
    }
    if compact in local_terms:
        return "local"
    if compact in international_terms:
        return "international"
    return ""


def _gemini_trip_list_reply(session) -> str:
    trip_type = str(getattr(session, "trip_type", "") or "").strip().lower()
    trip_result = _session_trip_result(session)
    trips = [*list(trip_result.get("open_trips") or []), *list(trip_result.get("date_tbd_trips") or [])]
    arabic = str(getattr(session, "language", "") or "").startswith("ar")
    if not trips:
        if arabic:
            return "\u0644\u0627 \u062a\u0648\u062c\u062f \u0631\u062d\u0644\u0627\u062a \u0645\u062a\u0627\u062d\u0629 \u0644\u0647\u0630\u0627 \u0627\u0644\u0646\u0648\u0639 \u062d\u0627\u0644\u064a\u0627 \u0641\u064a CRM. \u0644\u0646 \u0623\u0639\u0631\u0636 \u0646\u0648\u0639 \u0631\u062d\u0644\u0629 \u0622\u062e\u0631 \u0644\u0623\u0646 \u0637\u0644\u0628\u0643 \u0645\u062d\u062f\u062f. \u0633\u0628\u0628 \u0627\u0644\u062a\u062d\u0648\u064a\u0644: \u0644\u0627 \u062a\u0648\u062c\u062f \u0631\u062d\u0644\u0627\u062a \u0646\u0634\u0637\u0629 \u0645\u062a\u0627\u062d\u0629 \u0644\u0647\u0630\u0627 \u0627\u0644\u0646\u0648\u0639 \u0627\u0644\u0622\u0646."
        return f"I do not have any available {trip_type or 'requested'} trips in CRM right now. I will not show another trip type because you asked for this one. Reason for escalation: no active inventory is available for the requested trip type."
    label = trip_type if trip_type in {"local", "international"} else "available"
    if arabic:
        type_label = "\u0645\u062d\u0644\u064a\u0629" if trip_type == "local" else "\u062f\u0648\u0644\u064a\u0629" if trip_type == "international" else "\u0645\u062a\u0627\u062d\u0629"
        lines = [f"\u062a\u0645 \u0627\u062e\u062a\u064a\u0627\u0631 \u0631\u062d\u0644\u0629 {type_label}. \u0647\u0630\u0647 \u0627\u0644\u0631\u062d\u0644\u0627\u062a \u0627\u0644\u0645\u062a\u0627\u062d\u0629 \u062d\u0627\u0644\u064a\u0627:", ""]
        for index, trip in enumerate(trips, start=1):
            lines.append(f"{index}. {str(trip.get('trip_name') or trip.get('trip_id') or 'Trip').strip()}")
            start = str(trip.get("start_date") or "").strip()
            end = str(trip.get("end_date") or "").strip()
            if start and end:
                lines.append(f"\u0627\u0644\u062a\u0627\u0631\u064a\u062e: {start} \u0625\u0644\u0649 {end}")
            price = str(trip.get("public_price") or "").strip()
            if price:
                lines.append(f"\u0627\u0644\u0633\u0639\u0631: {price}")
            lines.append("")
        lines.append("\u0645\u0646 \u0641\u0636\u0644\u0643 \u0627\u0631\u062f\u062f \u0628\u0631\u0642\u0645 \u0627\u0644\u0631\u062d\u0644\u0629 \u0623\u0648 \u0627\u0633\u0645\u0647\u0627 \u0628\u0634\u0643\u0644 \u0648\u0627\u0636\u062d.")
        return "\n".join(lines).strip()
    lines = [f"Here are the {label} trips currently available:", ""]
    for index, trip in enumerate(trips, start=1):
        lines.append(f"{index}. {str(trip.get('trip_name') or trip.get('trip_id') or 'Trip').strip()}")
        start = str(trip.get("start_date") or "").strip()
        end = str(trip.get("end_date") or "").strip()
        if start and end:
            lines.append(f"Dates: {start} to {end}")
        price = str(trip.get("public_price") or "").strip()
        if price:
            lines.append(f"Price: {price}")
        lines.append("")
    lines.append("Please reply with the trip number or exact trip name.")
    return "\n".join(lines).strip()


def _gemini_selected_trip(session) -> dict[str, Any]:
    return _selected_trip_snapshot(session)


def _gemini_available_room_types(session) -> list[str]:
    trip = _gemini_selected_trip(session)
    if not trip:
        return []

    def available(key: str) -> int:
        try:
            return max(0, int(trip.get(key) or 0))
        except (TypeError, ValueError):
            return 0

    options: list[str] = []
    if available("available_single"):
        options.append("Single")
    group = str(getattr(session, "room_group", "") or "").strip().lower()
    double_key = f"{group}_double" if group in {"boys", "girls"} else "available_double"
    triple_key = f"{group}_triple" if group in {"boys", "girls"} else "available_triple"
    if available(double_key) or (double_key not in trip and available("available_double")):
        options.append("Double")
    if available(triple_key) or (triple_key not in trip and available("available_triple")):
        options.append("Triple")
    return options


def _gemini_gender_prompt(session) -> str:
    trip_name = str(getattr(session, "selected_trip_name", "") or "").strip()
    if str(getattr(session, "language", "") or "").startswith("ar"):
        trip_part = f" \u0644\u0631\u062d\u0644\u0629 {trip_name}" if trip_name else ""
        return (
            f"\u0644\u0645\u0631\u0627\u062c\u0639\u0629 \u0627\u0644\u063a\u0631\u0641 \u0627\u0644\u0645\u062a\u0627\u062d\u0629{trip_part}\u060c \u0647\u0644 \u0627\u0644\u0645\u0633\u0627\u0641\u0631\u0648\u0646 \u0634\u0628\u0627\u0628 \u0623\u0645 \u0628\u0646\u0627\u062a\u061f\n\n"
            "1. \u0634\u0628\u0627\u0628\n"
            "2. \u0628\u0646\u0627\u062a"
        )
    trip_part = f" for {trip_name}" if trip_name else ""
    return (
        f"To check the right room availability{trip_part}, are the travelers boys/male or girls/female?\n\n"
        "1. Boys / Male\n"
        "2. Girls / Female"
    )


def _gemini_room_prompt(session) -> str:
    options = _gemini_available_room_types(session) or ["Single"]
    group = str(getattr(session, "room_group", "") or "").strip().lower()
    if str(getattr(session, "language", "") or "").startswith("ar"):
        room_labels = {"Single": "\u0633\u0646\u062c\u0644", "Double": "\u062f\u0628\u0644", "Triple": "\u062a\u0631\u064a\u0628\u0644"}
        lines = ["\u0645\u0646 \u0641\u0636\u0644\u0643 \u0627\u062e\u062a\u0631 \u0646\u0648\u0639 \u0627\u0644\u063a\u0631\u0641\u0629:", "", "\u0627\u0644\u062e\u064a\u0627\u0631\u0627\u062a \u0627\u0644\u0645\u062a\u0627\u062d\u0629:"]
        for index, room_type in enumerate(options, start=1):
            lines.append(f"{index}. {room_labels.get(room_type, room_type)}")
        lines.append("\u0627\u0631\u062f\u062f \u0628\u0627\u0633\u0645 \u0627\u0644\u063a\u0631\u0641\u0629 \u0623\u0648 \u0631\u0642\u0645\u0647\u0627.")
        return "\n".join(lines)
    lines = ["Please choose your preferred room option:", "", "Available room options:"]
    for index, room_type in enumerate(options, start=1):
        suffix = ""
        if room_type != "Single" and group in {"boys", "girls"}:
            suffix = f" {group}"
        lines.append(f"{index}. {room_type}{suffix}")
    lines.append("Reply with the room name or its number.")
    return "\n".join(lines)


def _gemini_booking_confirmation_summary(session) -> str:
    trip_name = str(getattr(session, "selected_trip_name", "") or getattr(session, "selected_trip_id", "") or "the selected trip").strip()
    room = _room_choice_label(str(getattr(session, "room_type", "") or "").strip(), str(getattr(session, "room_group", "") or "").strip())
    flight = str(getattr(session, "flight_option", "") or "Not Applicable").strip()
    return (
        "Before I create the booking draft, please confirm these details:\n"
        f"Trip: {trip_name}\n"
        f"Travelers: {getattr(session, 'group_size', 1) or 1}\n"
        f"Room: {room}\n"
        f"Flight option: {flight}\n"
        "Do you confirm creating the booking draft?"
    )


def _gemini_trip_supports_flights(session) -> bool:
    trip = _gemini_selected_trip(session)
    trip_type = str(trip.get("type") or trip.get("trip_type") or getattr(session, "trip_type", "") or "").strip().lower()
    if trip_type in {"local", "domestic"}:
        return False
    if "supports_flights" in trip:
        return bool(trip.get("supports_flights"))
    return trip_type == "international"


def _gemini_lookup_or_load_trips(session, gemini_agent: GeminiAgent) -> None:
    if isinstance(_gemini_preview(session).get("trip_result"), dict):
        return
    tools = getattr(gemini_agent, "read_only_tools", None)
    if tools is None:
        return
    search_trips = getattr(tools, "search_trips", None)
    if not callable(search_trips):
        return
    try:
        result = search_trips(trip_type=str(getattr(session, "trip_type", "") or "").strip())
    except Exception as exc:
        app_logger.warning("Gemini deterministic trip lookup failed session=%s error=%s", getattr(session, "id", ""), exc)
        return
    if isinstance(result, dict):
        _set_preview_trip_result(session, result)


def _gemini_select_trip_from_text(session, text: str) -> bool:
    trip_result = _session_trip_result(session)
    trips = [*list(trip_result.get("open_trips") or []), *list(trip_result.get("date_tbd_trips") or [])]
    if not trips:
        return False
    option = _gemini_option_number(text)
    selected: dict[str, Any] = {}
    if option and option <= len(trips):
        selected = dict(trips[option - 1])
    else:
        normalized = _normalize_text(text).casefold()
        for trip in trips:
            trip_name = str(trip.get("trip_name") or "").strip().casefold()
            trip_id = str(trip.get("trip_id") or "").strip().casefold()
            if normalized and (normalized == trip_name or normalized == trip_id):
                selected = dict(trip)
                break
    if not selected:
        return False
    session.selected_trip_id = str(selected.get("trip_id") or "").strip()
    session.selected_trip_name = str(selected.get("trip_name") or session.selected_trip_id).strip()
    selected_type = str(selected.get("type") or selected.get("trip_type") or "").strip().lower()
    if selected_type in {"local", "international"}:
        session.trip_type = selected_type
    _gemini_update_collection_state(session, selected_trip=True, room_group=False, room_type=False, group_size=False, flight_option=False)
    return True


def _gemini_capture_room_group(session, text: str) -> bool:
    option = _gemini_option_number(text)
    lowered = _normalize_text(text).casefold()
    if option == 1 or any(token in lowered for token in ("boys", "boy", "male", "men", "man")):
        session.room_group = "boys"
    elif option == 2 or any(token in lowered for token in ("girls", "girl", "female", "women", "woman")):
        session.room_group = "girls"
    elif any(token in lowered for token in ("mix", "mixed", "boy and girl", "boys and girls")):
        session.room_group = "mixed"
    else:
        return False
    _gemini_update_collection_state(session, room_group=True, room_type=False, group_size=False, flight_option=False)
    return True


def _gemini_capture_room_type(session, text: str) -> bool:
    option = _gemini_option_number(text)
    options = _gemini_available_room_types(session)
    room_type = ""
    if option and option <= len(options):
        room_type = options[option - 1]
    else:
        inferred = _infer_room_type(text)
        if inferred in options:
            room_type = inferred
    if not room_type:
        return False
    session.room_type = room_type
    _gemini_update_collection_state(session, room_type=True, group_size=False, flight_option=False)
    return True


def _gemini_ensure_room_requirements_for_group(session) -> None:
    if isinstance(getattr(session, "room_requirements", None), dict) and session.room_requirements.get("requirements"):
        return
    room_type = str(getattr(session, "room_type", "") or "").strip().title()
    room_group = str(getattr(session, "room_group", "") or "").strip().lower()
    if room_type not in {"Single", "Double", "Triple"} or room_group not in {"boys", "girls"}:
        return
    occupancy = {"Single": 1, "Double": 2, "Triple": 3}.get(room_type, 1)
    group_size = max(1, int(getattr(session, "group_size", 1) or 1))
    rooms = (group_size + occupancy - 1) // occupancy
    session.room_requirements = {
        "requirements": [{"room_type": room_type, "room_group": room_group, "rooms": rooms}],
        "boys_rooms_requested": rooms if room_group == "boys" else 0,
        "girls_rooms_requested": rooms if room_group == "girls" else 0,
    }


def _gemini_capture_group_size(session, text: str) -> bool:
    option = _gemini_option_number(text)
    if not option:
        match = re.search(r"\b([1-9][0-9]?)\b", str(text or "").translate(_DIGIT_TRANSLATION))
        option = int(match.group(1)) if match else 0
    if not option:
        lowered = _normalize_text(text).casefold()
        words = {
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9,
            "\u0648\u0627\u062d\u062f": 1, "\u0648\u0627\u062d\u062f\u0629": 1,
            "\u0627\u062a\u0646\u064a\u0646": 2, "\u0627\u062b\u0646\u064a\u0646": 2,
            "\u0627\u062b\u0646\u0627\u0646": 2, "\u062b\u0646\u064a\u0646": 2,
            "\u062b\u0644\u0627\u062b\u0629": 3, "\u062a\u0644\u0627\u062a\u0629": 3,
            "\u0623\u0631\u0628\u0639\u0629": 4, "\u0627\u0631\u0628\u0639\u0629": 4,
            "\u062e\u0645\u0633\u0629": 5,
        }
        option = next(
            (value for token, value in words.items() if re.search(rf"(?<!\w){re.escape(token)}(?!\w)", lowered)),
            0,
        )
    if not option:
        return False
    session.group_size = option
    _gemini_update_collection_state(session, group_size=True)
    _gemini_ensure_room_requirements_for_group(session)
    return True



def _gemini_is_explanation_request(text: str) -> bool:
    normalized = " ".join(_normalize_text(text).casefold().split())
    return normalized in {
        "what do i need",
        "what is needed",
        "\u064a\u0639\u0646\u064a \u0627\u064a\u0647",
        "\u064a\u0639\u0646\u064a \u0625\u064a\u0647",
        "\u0648\u0636\u062d",
        "\u0648\u0636\u062d\u0644\u064a",
    }


def _gemini_is_booking_intent(text: str) -> bool:
    normalized = _normalize_text(text).casefold()
    return any(
        phrase in normalized
        for phrase in (
            "i want to book",
            "i want a booking",
            "\u0627\u0646\u0627 \u0639\u0627\u064a\u0632 \u0627\u062d\u062c\u0632",
            "\u0627\u0646\u0627 \u0639\u0627\u064a\u0632 \u0627\u062d\u062c\u0632 \u0644\u0648\u0642\u062a\u064a",
        )
    )


def _gemini_is_negative_trip_selection(text: str) -> bool:
    normalized = " ".join(_normalize_text(text).casefold().split())
    return normalized in {"no", "no thanks", "\u0644\u0627", "\u0644\u0627 \u0634\u0643\u0631\u0627"}

def _gemini_capture_flight_option(session, text: str) -> bool:
    option = _gemini_option_number(text)
    flight = {1: "With Flight", 2: "Without Flight"}.get(option) or normalize_flight_option(text) or _infer_flight_option(text)
    if not flight:
        return False
    session.flight_option = flight
    _gemini_update_collection_state(session, flight_option=True)
    return True


def _handle_gemini_deterministic_workflow(session, text: str, gemini_agent: GeminiAgent) -> bool:
    """Own numbered booking steps in Gemini mode so valid inputs never become model fallbacks."""

    traveler = _gemini_verified_traveler(session)
    if not traveler:
        return False

    clean_text = str(text or "").strip()
    option = _gemini_option_number(clean_text)
    collection = _gemini_collection_state(session)
    candidate_trip_type = _gemini_explicit_trip_type_choice(clean_text)
    if not getattr(session, "selected_trip_id", "") and not collection.get("trip_type") and candidate_trip_type:
        session.messages.append({"role": "user", "text": clean_text})
        session.trip_type = candidate_trip_type
        _gemini_update_collection_state(session, trip_type=True)
        _gemini_lookup_or_load_trips(session, gemini_agent)
        _gemini_append_verified_reply(session, _gemini_trip_list_reply(session), stage="trip_selection_required")
        return True

    if not getattr(session, "trip_type", ""):
        trip_type = _gemini_explicit_trip_type_choice(clean_text)
        if trip_type:
            session.messages.append({"role": "user", "text": clean_text})
            session.trip_type = trip_type
            _gemini_update_collection_state(session, trip_type=True)
            _gemini_lookup_or_load_trips(session, gemini_agent)
            _gemini_append_verified_reply(session, _gemini_trip_list_reply(session), stage="trip_selection_required")
            return True
        return False

    if not getattr(session, "selected_trip_id", "") and not collection.get("trip_type"):
        return False

    if not getattr(session, "selected_trip_id", ""):
        _gemini_lookup_or_load_trips(session, gemini_agent)
        if _gemini_is_negative_trip_selection(clean_text):
            session.messages.append({"role": "user", "text": clean_text})
            reply = (
                "\u062a\u0645\u0627\u0645\u060c \u0644\u0646 \u0623\u0643\u0645\u0644 \u062d\u062c\u0632 \u0647\u0630\u0647 \u0627\u0644\u0631\u062d\u0644\u0629. \u0625\u0630\u0627 \u063a\u064a\u0651\u0631\u062a \u0631\u0623\u064a\u0643\u060c \u0627\u0643\u062a\u0628 \u0631\u0642\u0645 \u0627\u0644\u0631\u062d\u0644\u0629 \u0623\u0648 \u0627\u0633\u0645\u0647\u0627 \u0627\u0644\u062f\u0642\u064a\u0642."
                if str(getattr(session, "language", "") or "").startswith("ar")
                else "No problem. I will not continue with this trip. If you change your mind, send the trip number or exact trip name."
            )
            _gemini_append_verified_reply(session, reply, stage="trip_selection_required")
            return True
        if _gemini_is_explanation_request(clean_text):
            session.messages.append({"role": "user", "text": clean_text})
            reply = (
                "\u0623\u062d\u062a\u0627\u062c \u0631\u0642\u0645 \u0627\u0644\u0631\u062d\u0644\u0629 \u0623\u0648 \u0627\u0633\u0645\u0647\u0627 \u0627\u0644\u062f\u0642\u064a\u0642 \u0645\u0646 \u0627\u0644\u0642\u0627\u0626\u0645\u0629. \u0644\u0627 \u062a\u062d\u062a\u0627\u062c \u0625\u0644\u0649 \u0625\u0631\u0633\u0627\u0644 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0645\u0631\u0629 \u0623\u062e\u0631\u0649."
                if str(getattr(session, "language", "") or "").startswith("ar")
                else "I need the number or exact name of the trip you want. You do not need to resend the whole list."
            )
            _gemini_append_verified_reply(session, reply, stage="trip_selection_required")
            return True
        if _gemini_select_trip_from_text(session, clean_text):
            session.messages.append({"role": "user", "text": clean_text})
            _gemini_append_verified_reply(session, _gemini_gender_prompt(session), stage="traveler_gender_required")
            return True
        if _session_trip_result(session):
            session.messages.append({"role": "user", "text": clean_text})
            reply = (
                (
                    "\u0645\u0646 \u0641\u0636\u0644\u0643 \u0627\u062e\u062a\u0631 \u0631\u062d\u0644\u0629 \u062f\u0648\u0644\u064a\u0629 \u0645\u0646 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0628\u0627\u0644\u0631\u0642\u0645 \u0623\u0648 \u0628\u0627\u0644\u0627\u0633\u0645 \u0627\u0644\u0648\u0627\u0636\u062d."
                    if str(getattr(session, "trip_type", "") or "").strip().lower() == "international"
                    else "\u0645\u0646 \u0641\u0636\u0644\u0643 \u0627\u062e\u062a\u0631 \u0631\u062d\u0644\u0629 \u0645\u0646 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0628\u0627\u0644\u0631\u0642\u0645 \u0623\u0648 \u0628\u0627\u0644\u0627\u0633\u0645 \u0627\u0644\u0648\u0627\u0636\u062d."
                )
                if str(getattr(session, "language", "") or "").startswith("ar")
                else "Please choose one of the listed trips by number or exact trip name."
            )
            _gemini_append_verified_reply(session, reply, stage="trip_selection_required")
            return True
        return False

    collection = _gemini_collection_state(session)
    room_group_collected = bool(collection.get("room_group") or getattr(session, "room_group", ""))
    room_type_collected = bool(collection.get("room_type") or getattr(session, "room_type", ""))
    group_size_collected = bool(collection.get("group_size"))
    flight_collected = bool(collection.get("flight_option") or getattr(session, "flight_option", ""))

    if not room_group_collected:
        if _gemini_capture_room_group(session, clean_text):
            session.messages.append({"role": "user", "text": clean_text})
            _gemini_append_verified_reply(session, _gemini_room_prompt(session), stage="room_type_required")
            return True
        return False

    if not room_type_collected:
        if _gemini_capture_room_type(session, clean_text):
            session.messages.append({"role": "user", "text": clean_text})
            reply = (
                "\u0643\u0645 \u0639\u062f\u062f \u0627\u0644\u0645\u0633\u0627\u0641\u0631\u064a\u0646 \u0641\u064a \u0637\u0644\u0628 \u0627\u0644\u062d\u062c\u0632\u061f"
                if str(getattr(session, "language", "") or "").startswith("ar")
                else "How many travelers should I put on this booking request?"
            )
            _gemini_append_verified_reply(session, reply, stage="group_size_required")
            return True
        session.messages.append({"role": "user", "text": clean_text})
        _gemini_append_verified_reply(session, _gemini_room_prompt(session), stage="room_type_required")
        return True

    if not group_size_collected:
        if _gemini_is_explanation_request(clean_text) or _gemini_is_booking_intent(clean_text):
            session.messages.append({"role": "user", "text": clean_text})
            reply = (
                "\u0623\u0642\u0635\u062f \u0639\u062f\u062f \u0627\u0644\u0645\u0633\u0627\u0641\u0631\u064a\u0646 \u0641\u064a \u0637\u0644\u0628 \u0627\u0644\u062d\u062c\u0632\u060c \u0648\u0644\u064a\u0633 \u0639\u062f\u062f \u0627\u0644\u063a\u0631\u0641. \u0627\u0643\u062a\u0628 \u0631\u0642\u0645\u064b\u0627 \u0645\u062b\u0644 2."
                if str(getattr(session, "language", "") or "").startswith("ar")
                else "I mean the number of travelers for this booking, not the number of rooms. Please reply with a number, for example 2."
            )
            _gemini_append_verified_reply(session, reply, stage="group_size_required")
            return True
        if _gemini_capture_group_size(session, clean_text):
            session.messages.append({"role": "user", "text": clean_text})
            if not _gemini_trip_supports_flights(session):
                session.flight_option = "Not Applicable"
                _gemini_update_collection_state(session, flight_option=True)
                session.booking_confirmation_requested = True
                _gemini_append_verified_reply(session, _gemini_booking_confirmation_summary(session), stage="booking_confirmation_required")
                return True
            reply = (
                "\u0647\u0644 \u062a\u0631\u064a\u062f \u0627\u0644\u0631\u062d\u0644\u0629 \u0645\u0639 \u0637\u064a\u0631\u0627\u0646 \u0623\u0645 \u0628\u062f\u0648\u0646 \u0637\u064a\u0631\u0627\u0646\u061f\n\n1. \u0645\u0639 \u0637\u064a\u0631\u0627\u0646\n2. \u0628\u062f\u0648\u0646 \u0637\u064a\u0631\u0627\u0646"
                if str(getattr(session, "language", "") or "").startswith("ar")
                else "Do you want this trip with flights or without flights?\n\n1. With flights\n2. Without flights"
            )
            _gemini_append_verified_reply(session, reply, stage="flight_option_required")
            return True
        session.messages.append({"role": "user", "text": clean_text})
        reply = (
            "\u0645\u0646 \u0641\u0636\u0644\u0643 \u0627\u0631\u0633\u0644 \u0639\u062f\u062f \u0627\u0644\u0645\u0633\u0627\u0641\u0631\u064a\u0646\u060c \u0645\u062b\u0644 1 \u0623\u0648 2 \u0623\u0648 3."
            if str(getattr(session, "language", "") or "").startswith("ar")
            else "Please send the number of travelers, for example 1, 2, or 3."
        )
        _gemini_append_verified_reply(session, reply, stage="group_size_required")
        return True

    if not flight_collected:
        if _gemini_capture_flight_option(session, clean_text):
            session.messages.append({"role": "user", "text": clean_text})
            session.booking_confirmation_requested = True
            _gemini_append_verified_reply(session, _gemini_booking_confirmation_summary(session), stage="booking_confirmation_required")
            return True
        session.messages.append({"role": "user", "text": clean_text})
        reply = (
            "\u0645\u0646 \u0641\u0636\u0644\u0643 \u0627\u062e\u062a\u0631 \u062e\u064a\u0627\u0631 \u0627\u0644\u0637\u064a\u0631\u0627\u0646:\n\n1. \u0645\u0639 \u0637\u064a\u0631\u0627\u0646\n2. \u0628\u062f\u0648\u0646 \u0637\u064a\u0631\u0627\u0646"
            if str(getattr(session, "language", "") or "").startswith("ar")
            else "Please choose the flight option:\n\n1. With flights\n2. Without flights"
        )
        _gemini_append_verified_reply(session, reply, stage="flight_option_required")
        return True

    return False


def _route_live_message_with_gemini(session, text: str, gemini_agent: GeminiAgent) -> None:
    detected = detect_language(_repair_mojibake_for_analysis(text))
    if detected == "ar":
        session.language = "ar"
    elif session.language == "ar" and _explicit_english_request(text):
        session.language = "en"
    elif session.language != "ar":
        session.language = "en"
    if _handle_gemini_post_booking_message(session, text):
        return
    if session.stage not in {"completed", "handed_off", "cancelled"}:
        session.stage = "gemini_conversation"

    hints = _extract_gemini_message_hints(text, session.country_code or "20")
    _merge_gemini_hints_into_session(session, hints)
    session_context = _build_gemini_session_context(session, text)
    _refresh_gemini_preview_from_context(session, session_context, gemini_agent)
    if _handle_gemini_deterministic_workflow(session, text, gemini_agent):
        return
    session_context = _build_gemini_session_context(session, text)
    result = gemini_agent.respond(
        user_message=text,
        session_context=session_context,
        conversation_history=session.messages[-12:],
    )

    reply = str(result.get("reply") or "").strip()
    error = str(result.get("error") or "").strip()
    if error and error != "write_request_rejected":
        reply = _safe_gemini_fallback_message(session.language)
        fallback_used = True
        tools_used: list[str] = []
    else:
        if not reply:
            reply = _safe_gemini_fallback_message(session.language)
            fallback_used = True
        else:
            fallback_used = False
        tools_used = [
            str(event.get("name") or "").strip()
            for event in result.get("tool_requests", [])
            if isinstance(event, dict) and str(event.get("name") or "").strip()
        ]
    reply = _sanitize_gemini_reply(reply, session.language)
    write_result, record_type = _latest_write_result_from_agent_result(result)
    guarded = guard_customer_response(
        reply,
        language=session.language,
        write_result=write_result,
        record_type=record_type,
        fallback_message_key=record_type or "general",
    )
    if guarded.fallback_used:
        app_logger.warning(
            "Gemini response guard fallback session=%s reason=%s terms=%s",
            session.id,
            guarded.reason_code,
            ",".join(guarded.blocked_terms_found),
        )
        reply = guarded.message
        fallback_used = True
    else:
        reply = guarded.message
    completion_issue = response_completeness_issue(reply)
    if completion_issue:
        app_logger.warning(
            "Gemini response validation failed session=%s reason=%s text=%r",
            session.id,
            completion_issue,
            reply[:160],
        )
        reply = _safe_gemini_fallback_message(session.language)
        fallback_used = True
    media = _extract_trip_media_from_agent_result(result)

    session.agent_mode = "gemini"
    session.tools_used = tools_used
    session.fallback_used = fallback_used
    session.messages.append({"role": "user", "text": text})
    session.messages.append(_assistant_message_with_media(reply, media, session.language))
    _apply_gemini_tool_results(session, result, gemini_agent=gemini_agent, session_context=session_context)


def _session_runs_gemini(app: Flask, sessions: SessionFlowManager, session_obj) -> bool:
    requested_mode = str(app.config.get("AI_AGENT_MODE") or "").strip().lower()
    if requested_mode != "gemini":
        return False
    if not isinstance(getattr(sessions, "conversation_ai", None), GeminiAgent):
        return False
    return str(getattr(session_obj, "agent_mode", "") or "").strip().lower() == "gemini"
    app_logger.info(
        "Live Gemini session %s mode=%s tools=%s fallback=%s",
        session.id,
        session.agent_mode,
        ",".join(session.tools_used),
        session.fallback_used,
    )


def _set_preview_trip_result(session, trip_result: dict[str, Any]) -> None:
    preview = dict(session.preview or {})
    preview["trip_result"] = {
        "open_trips": list(trip_result.get("open_trips") or []),
        "date_tbd_trips": list(trip_result.get("date_tbd_trips") or []),
    }
    session.preview = preview


def _set_preview_traveler(session, traveler: dict[str, Any]) -> None:
    preview = dict(session.preview or {})
    preview["traveler"] = dict(traveler)
    session.preview = preview


def _apply_gemini_read_context(session, result: dict[str, Any], tool_name: str, tool_input: dict[str, Any]) -> None:
    if tool_name in {"search_traveler", "get_traveler_profile"}:
        traveler = result.get("traveler")
        if isinstance(traveler, dict) and traveler:
            _set_preview_traveler(session, traveler)
            session.final_result = {**dict(session.final_result or {}), "traveler": dict(traveler)}
        raw_phone = str(tool_input.get("raw_phone") or "").strip()
        if raw_phone and not session.raw_phone:
            session.raw_phone = raw_phone
            session.pending_raw_phone = raw_phone
        country_code = str(tool_input.get("country_code") or "").strip()
        if country_code and not session.country_code:
            session.country_code = country_code
    elif tool_name == "search_trips":
        trip_type = str(result.get("trip_type") or tool_input.get("trip_type") or "").strip().lower()
        if trip_type:
            session.trip_type = trip_type
        _set_preview_trip_result(session, result)
    elif tool_name == "get_trip_details":
        trip = result.get("trip")
        if isinstance(trip, dict) and trip:
            session.selected_trip_id = str(trip.get("trip_id") or session.selected_trip_id or "")
            session.selected_trip_name = str(trip.get("trip_name") or session.selected_trip_name or "")
            _set_preview_trip_result(session, {"open_trips": [trip], "date_tbd_trips": []})
    elif tool_name == "get_passport_status":
        traveler = result.get("traveler")
        if isinstance(traveler, dict) and traveler:
            session.passport_name = str(traveler.get("passport_name") or session.passport_name or "")
            session.passport_number = str(traveler.get("passport_number") or session.passport_number or "")
            session.passport_expiry = str(traveler.get("passport_expiry") or session.passport_expiry or "")
            session.passport_nationality = str(traveler.get("passport_nationality") or session.passport_nationality or "")
            session.passport_attachment_ref = str(traveler.get("passport_attachment_ref") or session.passport_attachment_ref or "")
            _set_preview_traveler(session, traveler)
    elif tool_name == "get_booking_status":
        bookings = result.get("bookings") or []
        if isinstance(bookings, list) and bookings:
            session.booking_result = dict(bookings[0])
            session.booking_status = str(bookings[0].get("booking_status") or session.booking_status or "")
    elif tool_name == "lookup_lead":
        leads = result.get("leads") or []
        if isinstance(leads, list) and leads:
            lead = dict(leads[0])
            session.lead_status = str(lead.get("lead_stage") or session.lead_status or "")
            final_result = dict(session.final_result or {})
            final_result["lead_id"] = str(lead.get("lead_id") or final_result.get("lead_id") or "")
            session.final_result = final_result


def _refresh_gemini_preview_from_context(session, session_context: dict[str, Any], gemini_agent: GeminiAgent) -> None:
    tools = getattr(gemini_agent, "read_only_tools", None)
    if tools is None:
        return
    raw_phone = str(session_context.get("raw_phone") or "").strip()
    country_code = str(session_context.get("country_code") or "").strip()
    trip_type = str(session_context.get("trip_type") or "").strip()
    if raw_phone and not isinstance((session.preview or {}).get("traveler"), dict):
        traveler_lookup = tools.search_traveler(raw_phone=raw_phone, country_code=country_code)
        traveler = traveler_lookup.get("traveler")
        if isinstance(traveler, dict) and traveler:
            _set_preview_traveler(session, traveler)
    if trip_type and not isinstance((session.preview or {}).get("trip_result"), dict):
        trip_result = tools.search_trips(trip_type=trip_type)
        _set_preview_trip_result(session, trip_result)


def _apply_gemini_tool_results(session, result: dict[str, Any], *, gemini_agent: GeminiAgent, session_context: dict[str, Any]) -> None:
    tool_requests = result.get("tool_requests") if isinstance(result, dict) else []
    if isinstance(tool_requests, list):
        for event in tool_requests:
            if not isinstance(event, dict):
                continue
            tool_name = str(event.get("name") or "").strip()
            tool_input = event.get("input") if isinstance(event.get("input"), dict) else {}
            tool_result = event.get("result") if isinstance(event.get("result"), dict) else {}
            if not tool_name or not tool_result:
                continue
            _apply_gemini_read_context(session, tool_result, tool_name, tool_input)

    write_results = result.get("write_results") if isinstance(result, dict) else []
    if not isinstance(write_results, list):
        _refresh_gemini_preview_from_context(session, session_context, gemini_agent)
        return
    for event in write_results:
        if not isinstance(event, dict) or not event.get("executed"):
            continue
        session_update = event.get("session_update")
        if not isinstance(session_update, dict):
            tool_result = event.get("result")
            session_update = tool_result.get("session_update") if isinstance(tool_result, dict) else None
        if not isinstance(session_update, dict):
            continue
        if "lead_status" in session_update:
            session.lead_status = str(session_update.get("lead_status") or "")
        if "booking_status" in session_update:
            session.booking_status = str(session_update.get("booking_status") or "")
        if "handoff_state" in session_update:
            session.handoff_state = str(session_update.get("handoff_state") or session.handoff_state or "")
        if "stage" in session_update:
            session.stage = str(session_update.get("stage") or session.stage or "")
        if "booking_result" in session_update and isinstance(session_update.get("booking_result"), dict):
            session.booking_result = dict(session_update["booking_result"])
        if "final_result" in session_update and isinstance(session_update.get("final_result"), dict):
            session.final_result = dict(session_update["final_result"])
    _refresh_gemini_preview_from_context(session, session_context, gemini_agent)


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
    mode = str(base_settings.ai_agent_mode or "deterministic").strip().lower()
    conversation_ai = None
    session_runtime: Any
    if mode == "tool_calling":
        session_runtime = ToolCallingSessionRuntime(settings=base_settings)
    else:
        if base_settings.gemini_agent_enabled and not using_runtime_overrides:
            provider = build_llm_provider(base_settings)
            if provider is not None:
                conversation_ai = GeminiAgent.from_settings(base_settings, provider, write_tools_enabled=True)
        elif base_settings.ai_enabled and not using_runtime_overrides:
            conversation_ai = GeminiConversationAI(
                api_key=base_settings.gemini_api_key,
                model=base_settings.gemini_model,
                system_prompt=base_settings.agent_conversation_prompt,
            )
        session_runtime = SessionFlowManager(
            human_handoff_phone=base_settings.human_handoff_phone,
            agent_persona_name=base_settings.agent_persona_name,
            website_url=base_settings.website_url,
            post_trip_handoff_enabled=base_settings.post_trip_handoff_enabled,
            handoff_keywords=base_settings.post_trip_handoff_keywords,
            post_trip_handoff_responsible_employee=base_settings.post_trip_handoff_responsible_employee,
            default_country_code=base_settings.default_country_code,
            conversation_ai=conversation_ai,
        )
    app.config["SESSIONS"] = session_runtime
    app.secret_key = base_settings.app_secret_key

    from services.ai_agent.ai_agent_app.web.api_routes import api_bp
    from services.ai_agent.ai_agent_app.web.auth import auth_bp, login_required

    @app.before_request
    def check_api_auth():
        if request.blueprint == api_bp.name and not session.get("logged_in"):
            return jsonify({"error": "unauthorized"}), 401

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
                "agentPersonaName": s.agent_persona_name or "Ravel Agent",
                "websiteUrl": s.website_url,
                "postTripHandoffEnabled": s.post_trip_handoff_enabled,
                "defaultCountryCode": s.default_country_code,
                "aiAgentMode": s.ai_agent_mode,
            }
        )

    @app.get("/api/openapi.json")
    def openapi_contract():
        return jsonify(build_agent_openapi_contract(base_url=request.host_url.rstrip("/")))

    @app.get("/trips/media/<public_id>")
    def public_trip_media(public_id: str):
        """Serve verified CRM trip media for the agent chat preview."""
        if not re.fullmatch(r"[A-Za-z0-9._~:-]{8,128}", str(public_id or "")):
            abort(404)
        service = get_system_service(base_settings)
        if service is None:
            abort(404)
        try:
            with service.connect() as connection:
                row = connection.execute(
                    """
                    SELECT storage_key, mime_type
                    FROM trip_media
                    WHERE public_id = ?
                      AND is_active = 1
                      AND verification_status = 'verified'
                    """,
                    (public_id,),
                ).fetchone()
        except Exception:
            app_logger.warning("Could not read trip media public_id=%s", public_id, exc_info=True)
            abort(404)
        if row is None:
            abort(404)
        path = _resolve_trip_media_storage_path(row["storage_key"])
        if path is None or not path.exists() or not path.is_file():
            abort(404)
        return send_file(path, mimetype=row["mime_type"] or "image/jpeg", as_attachment=False, conditional=True, max_age=3600)

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
        if not base_settings.demo_data_mode:
            return jsonify({"error": "demo_reset_disabled"}), 409
        if base_settings.app_env == "production" and not session.get("logged_in"):
            return jsonify({"error": "authentication_required"}), 401
        gateway.reset_runtime_workbook()
        sessions: SessionFlowManager = app.config["SESSIONS"]
        sessions.clear()
        return jsonify({"ok": True, "stats": gateway.get_demo_stats()})

    @app.post("/api/session")
    def create_session_route():
        sessions: SessionFlowManager = app.config["SESSIONS"]
        session = sessions.create_session(gateway)
        current_mode = str(app.config.get("AI_AGENT_MODE") or "deterministic").strip().lower()
        session.agent_mode = "tool_calling" if current_mode == "tool_calling" else (
            "gemini" if current_mode == "gemini" and isinstance(getattr(sessions, "conversation_ai", None), GeminiAgent) else "deterministic"
        )
        if session.agent_mode == "gemini" and session.messages:
            session.stage = "gemini_conversation"
            session.messages[0]["text"] = _gemini_opening_message(sessions.agent_persona_name)
        session.tools_used = []
        session.fallback_used = False
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

            requested_mode = str(app.config.get("AI_AGENT_MODE") or "deterministic").strip().lower()
            session_mode = str(getattr(session, "agent_mode", "") or "").strip().lower()
            if session_mode == "tool_calling" and requested_mode != "tool_calling":
                return jsonify({"error": "session_mode_mismatch", "session": _serialize_session(gateway, session)}), 409

            if session_mode == "tool_calling":
                sessions.handle_message(session, text, gateway)
                session.tools_used = list(getattr(session, "tools_used", []) or [])
                session.fallback_used = bool(getattr(session, "fallback_used", False))
            elif session_mode == "gemini" or requested_mode == "gemini":
                gemini_agent = getattr(sessions, "conversation_ai", None)
                if isinstance(gemini_agent, GeminiAgent):
                    _route_live_message_with_gemini(session, text, gemini_agent)
                else:
                    sessions.handle_message(session, text, gateway)
                    session.agent_mode = "gemini"
                    session.tools_used = []
                    session.fallback_used = True
            else:
                sessions.handle_message(session, text, gateway)
                session.agent_mode = "deterministic"
                session.tools_used = []
                session.fallback_used = False
            _sync_session_lead_snapshot(gateway, session)
            return jsonify({"session": _serialize_session(gateway, session)})
        except Exception as e:
            app_logger.error(f"Error handling message for session {session_id}: {e}", exc_info=True)
            try:
                session.messages.append(
                    {
                        "role": "assistant",
                        "text": _safe_customer_runtime_error(getattr(session, "language", "en")),
                        "state": "completed",
                    }
                )
                session.stage = "waiting"
                session.tools_used = []
                session.fallback_used = True
                return jsonify({"session": _serialize_session(gateway, session)})
            except Exception:
                app_logger.error("Could not serialize recovery response for session %s", session_id, exc_info=True)
                return jsonify({"error": "agent_message_failed", "message": _safe_customer_runtime_error("en")}), 500

    @app.post("/api/session/<session_id>/intake")
    def submit_intake(session_id: str):
        sessions: SessionFlowManager = app.config["SESSIONS"]
        session = sessions.get(session_id)
        if session is None:
            return jsonify({"error": "session_not_found"}), 404
        if str(getattr(session, "agent_mode", "") or "").strip().lower() in {"tool_calling", "gemini"}:
            return jsonify(
                {
                    "error": "gemini_mode_chat_owned",
                    "message": "This session is controlled by the live agent runtime and collects details through chat.",
                    "session": _serialize_session(gateway, session),
                }
            ), 409

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
        if str(getattr(session, "agent_mode", "") or "").strip().lower() in {"tool_calling", "gemini"}:
            return jsonify(
                {
                    "error": "gemini_mode_chat_owned",
                    "message": "This session is controlled by the live agent runtime and handles booking requests through chat.",
                    "session": _serialize_session(gateway, session),
                }
            ), 409
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
        room_group = str(payload.get("roomGroup", "") or getattr(session, "room_group", "") or "").strip()
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
            room_group=room_group,
            room_requirements=getattr(session, "room_requirements", {}),
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

        if not _allowed_attachment(f.filename, f.mimetype or ""):
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
        lead_sync = _sync_session_lead_snapshot(gateway, sess)
        app_logger.info(f"Passport attachment saved: session={session_id} ref={ref}")
        return jsonify(
            {
                "ok": True,
                "ref": ref,
                "passportSave": passport_save,
                "leadSync": lead_sync,
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
