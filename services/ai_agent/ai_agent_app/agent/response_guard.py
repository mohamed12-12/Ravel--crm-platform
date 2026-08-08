"""Last-line-of-defense checks on the model's generated reply before it
reaches the customer: strips/rejects raw JSON keys, internal tool/state
names, and tracebacks that should never appear in customer-facing text
(_RAW_JSON_KEY_RE / _INTERNAL_TERM_RE), and catches a false claim of
write success via write_response_gating.py. Distinct from
response_format.py, which handles wording/style cleanup rather than
leak/safety detection.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from services.ai_agent.ai_agent_app.agent.response_format import format_agent_reply, response_completeness_issue
from services.ai_agent.ai_agent_app.agent.write_response_gating import (
    response_claims_write_success,
    write_result_allows_success,
)


_SUCCESS_RECORD_TYPES = {"booking", "lead", "handoff"}
_RAW_JSON_KEY_RE = re.compile(
    r"\b(?:assistant_message|write_result_contract|write_result|tool_result|workflow_policy|session_context|response_contract)\b",
    re.IGNORECASE,
)
_INTERNAL_TERM_RE = re.compile(
    r"\b(?:"
    r"create_booking_draft|create_booking|create_handoff|create_lead|update_lead_stage|"
    r"search_available_trips|get_trip_details|get_trip_media|find_traveler_by_phone|"
    r"booking_confirmation_required|handoff_pending|human_review|toolgroup|reason_code|"
    r"safe_customer_message_key|idempotency_key|workflow_policy|required_step|allowed_tools|"
    r"write_result_contract|response_contract|schema|system_prompt|prompt_id|model_config|"
    r"debug|traceback|runtimeerror|valueerror|validator reasons|router"
    r")\b",
    re.IGNORECASE,
)
_INVENTORY_COUNT_RE = re.compile(
    "(?:"
    r"\b(?:only\s+)?\d+\s+(?:(?:single|double|triple|quad|family|shared|private|available)\s+){0,3}(?:rooms?|places?|spots?|seats?)\s+(?:remaining|left|available)\b|"
    r"\b(?:remaining|available)\s+(?:rooms?|places?|spots?|seats?)\s*[:=]?\s*\d+\b|"
    r"\b(?:room inventory|inventory levels?|capacity)\s+(?:is|are)?\s*\d+\b|"
    "\\b\\d+\\s*[\\u0600-\\u06ff]{2,12}\\s*(?:\\u0645\\u062a\\u0627\\u062d\\u0629|\\u0645\\u062a\\u0627\\u062d|\\u0645\\u062a\\u0628\\u0642\\u064a\\u0629|\\u0645\\u062a\\u0628\\u0642\\u064a)\\b|"
    "\\b(?:\\u0627\\u0644\\u0645\\u062a\\u0627\\u062d|\\u0627\\u0644\\u0645\\u062a\\u0628\\u0642\\u064a|\\u0627\\u0644\\u0633\\u0639\\u0629)\\s*[:=]?\\s*\\d+\\b"
    ")",
    re.IGNORECASE,
)
_MOJIBAKE_RE = re.compile("[\\u00d8\\u00d9][\\x80-\\xffA-Za-z]*")
_INCOMPLETE_PHRASE_RE = re.compile(
    r"(?:\b(?:please\s+)?(?:send|share|provide|confirm|choose)\s+(?:your|the|a|an)?|"
    "(?:\\u0645\\u0646 \\u0641\\u0636\\u0644\\u0643\\s+|\\u0644\\u0648 \\u0633\\u0645\\u062d\\u062a\\s+)?(?:\\u0627\\u0631\\u0633\\u0644|\\u0627\\u0628\\u0639\\u062b|\\u0634\\u0627\\u0631\\u0643|\\u0627\\u062e\\u062a\\u0627\\u0631|\\u0623\\u0643\\u062f))\\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ResponseGuardResult:
    message: str
    language: str
    validation_status: str
    fallback_used: bool = False
    blocked_terms_found: list[str] = field(default_factory=list)
    reason_code: str = ""


def safe_fallback_message(language: str = "en", message_key: str = "general") -> str:
    arabic = str(language or "").strip().lower().startswith("ar")
    key = str(message_key or "general").strip().lower()
    if "booking" in key:
        if arabic:
            return "\u0644\u0645 \u0623\u062a\u0645\u0643\u0646 \u0645\u0646 \u062a\u0623\u0643\u064a\u062f \u0637\u0644\u0628 \u0627\u0644\u062d\u062c\u0632 \u0627\u0644\u0622\u0646. \u0645\u0646 \u0641\u0636\u0644\u0643 \u0631\u0627\u062c\u0639 \u0627\u0644\u062a\u0641\u0627\u0635\u064a\u0644 \u0623\u0648 \u0623\u0643\u062f\u0647\u0627 \u0645\u0631\u0629 \u0623\u062e\u0631\u0649."
        return "I could not confirm the booking request yet. Please review or confirm the details again."
    if "handoff" in key or "human" in key:
        if arabic:
            return "\u0644\u0645 \u0623\u062a\u0645\u0643\u0646 \u0645\u0646 \u0625\u0631\u0633\u0627\u0644 \u0637\u0644\u0628 \u0627\u0644\u062a\u0648\u0627\u0635\u0644 \u0645\u0639 \u0645\u0648\u0638\u0641 \u0627\u0644\u0622\u0646. \u0645\u0646 \u0641\u0636\u0644\u0643 \u062d\u0627\u0648\u0644 \u0645\u0631\u0629 \u0623\u062e\u0631\u0649 \u0623\u0648 \u062a\u0648\u0627\u0635\u0644 \u0645\u0639\u0646\u0627 \u0645\u0628\u0627\u0634\u0631\u0629."
        return "I could not submit the human handoff request right now. Please try again or contact us directly."
    if arabic:
        return "\u0645\u0639\u0644\u0634\u060c \u0644\u0645 \u0623\u062a\u0645\u0643\u0646 \u0645\u0646 \u062a\u062c\u0647\u064a\u0632 \u0627\u0644\u0631\u062f \u0628\u0634\u0643\u0644 \u0635\u062d\u064a\u062d. \u0645\u0645\u0643\u0646 \u062a\u0631\u0633\u0644 \u0637\u0644\u0628\u0643 \u0645\u0631\u0629 \u0623\u062e\u0631\u0649\u061f"
    return "Sorry, I couldn't prepare that response properly. Could you try that again?"


def _looks_like_raw_json(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    if _RAW_JSON_KEY_RE.search(value):
        return True
    if not ((value.startswith("{") and value.endswith("}")) or (value.startswith("[") and value.endswith("]"))):
        return False
    try:
        parsed = json.loads(value)
    except Exception:
        return True
    return isinstance(parsed, (dict, list))


def _internal_terms(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in _INTERNAL_TERM_RE.finditer(str(text or "")):
        term = match.group(0)
        folded = term.casefold()
        if folded not in seen:
            seen.add(folded)
            found.append(term)
    return found[:8]


def _record_type_from_text(text: str, explicit_record_type: str = "") -> str:
    requested = str(explicit_record_type or "").strip().lower()
    if requested in _SUCCESS_RECORD_TYPES:
        return requested
    normalized = str(text or "").casefold()
    if "booking" in normalized or "\u062d\u062c\u0632" in normalized:
        return "booking"
    if (
        "handoff" in normalized
        or "human" in normalized
        or "transfer" in normalized
        or "team member" in normalized
        or "\u0645\u0648\u0638\u0641" in normalized
        or "\u062a\u062d\u0648\u064a\u0644" in normalized
    ):
        return "handoff"
    if "lead" in normalized or "request" in normalized or "\u0637\u0644\u0628" in normalized:
        return "lead"
    return ""


def known_record_ids_from_context(session_context: dict[str, Any] | None) -> dict[str, str]:
    """Records this session already persisted, keyed by write record type.

    A truthful reference to a record saved on an *earlier* turn ("your request
    LD00001 is saved") carries no write result on the current turn, so the
    false-write-success check would otherwise reject the agent's own honest
    answer and replace it with a generic apology.
    """

    context = session_context if isinstance(session_context, dict) else {}
    known: dict[str, str] = {}
    for record_type, key in (("lead", "lead_id"), ("booking", "booking_id"), ("handoff", "handoff_id")):
        record_id = str(context.get(key) or "").strip()
        if record_id:
            known[record_type] = record_id
    return known


def response_guard_issue(
    text: str,
    *,
    write_result: dict[str, Any] | None = None,
    record_type: str = "",
    allow_inventory_counts: bool = False,
    known_record_ids: dict[str, str] | None = None,
) -> tuple[str, list[str]]:
    cleaned = format_agent_reply(text)
    if not cleaned:
        return "empty_response", []
    if _looks_like_raw_json(cleaned):
        return "raw_json_or_internal_payload", []
    if _MOJIBAKE_RE.search(cleaned):
        return "mojibake_text", []
    terms = _internal_terms(cleaned)
    if terms:
        return "internal_term_leak", terms
    if not allow_inventory_counts and _INVENTORY_COUNT_RE.search(cleaned):
        return "inventory_quantity_leak", []
    if _INCOMPLETE_PHRASE_RE.search(cleaned):
        return "obviously_incomplete_sentence", []
    success_record_type = _record_type_from_text(cleaned, record_type)
    if success_record_type and response_claims_write_success(cleaned) and not write_result_allows_success(write_result, success_record_type):
        already_saved = str((known_record_ids or {}).get(success_record_type) or "").strip()
        if not already_saved:
            return "false_write_success_claim", []
    completeness_issue = response_completeness_issue(cleaned)
    if completeness_issue:
        return completeness_issue, []
    return "", []


def guard_customer_response(
    text: str,
    *,
    language: str = "en",
    write_result: dict[str, Any] | None = None,
    record_type: str = "",
    fallback_message_key: str = "general",
    allow_inventory_counts: bool = False,
    known_record_ids: dict[str, str] | None = None,
) -> ResponseGuardResult:
    cleaned = format_agent_reply(text)
    issue, terms = response_guard_issue(
        cleaned,
        write_result=write_result,
        record_type=record_type,
        allow_inventory_counts=allow_inventory_counts,
        known_record_ids=known_record_ids,
    )
    if not issue:
        return ResponseGuardResult(
            message=cleaned,
            language=str(language or "en"),
            validation_status="passed",
        )
    return ResponseGuardResult(
        message=safe_fallback_message(language, fallback_message_key or record_type or "general"),
        language=str(language or "en"),
        validation_status="blocked",
        fallback_used=True,
        blocked_terms_found=terms,
        reason_code=issue,
    )
