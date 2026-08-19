"""Built on write_result.py's outcome classification: the customer-facing
half of the write-status boundary. `gate_customer_write_reply` is what
stops the agent from telling a customer their booking/lead/handoff
succeeded when the backend actually returned failure -- and
`response_claims_write_success` catches the model claiming success in
its own generated wording even when the tool result said otherwise.
"""
from __future__ import annotations

import re
from typing import Any

from services.ai_agent.ai_agent_app.agent.write_result import (
    WriteOutcome,
    normalize_write_result,
)

_IDEMPOTENT_REPLAY_STATUSES = {"reused", "duplicate"}
_SUCCESS_WORDS = (
    "created",
    "saved",
    "recorded",
    "submitted",
    "confirmed",
    "transferring you",
    "transferred you",
    "connecting you",
    "connected you",
    "i've transferred",
    "i have transferred",
    "تم إنشاء",
    "تم تسجيل",
    "اتسجل",
    "تسجل",
    "حولتك",
    "تحويلك",
    "هحولك",
    "سأحولك",
    "بتحويلك",
    "جاري تحويلك",
    "تم تحويلك",
    "قمت بتحويلك",
)
_NEGATION_WORDS = (
    "not",
    "no ",
    "cannot",
    "can't",
    "couldn't",
    "could not",
    "didn't",
    "did not",
    "wasn't",
    "was not",
    "isn't",
    "is not",
    "won't",
    "will not",
    "لا",
    "لم",
    "ما",
    "مش",
)


def _contract_from(write_result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(write_result, dict):
        return {}
    if isinstance(write_result.get("write_result_contract"), dict) and write_result["write_result_contract"]:
        return dict(write_result["write_result_contract"])
    return dict(write_result)


def _record_id_from(write_result: dict[str, Any] | None, record_type: str) -> str:
    if not isinstance(write_result, dict):
        return ""
    contract = _contract_from(write_result)
    record_id = str(contract.get("record_id") or "").strip()
    if record_id:
        return record_id
    # "result_id" is the generic identifier every write executor (shared_service
    # and API mode alike) sets regardless of record type - see
    # GeminiWriteToolExecutor._result_id(). Record-type-specific keys below are
    # a fallback for callers that only populate those.
    generic_result_id = str(contract.get("result_id") or write_result.get("result_id") or "").strip()
    if generic_result_id:
        return generic_result_id
    if record_type == "booking":
        booking = write_result.get("booking_draft") if isinstance(write_result.get("booking_draft"), dict) else {}
        return str(write_result.get("booking_id") or booking.get("booking_id") or "").strip()
    if record_type == "lead":
        lead = write_result.get("lead_update") if isinstance(write_result.get("lead_update"), dict) else {}
        return str(write_result.get("lead_id") or lead.get("lead_id") or "").strip()
    if record_type == "handoff":
        handoff = write_result.get("handoff_case") if isinstance(write_result.get("handoff_case"), dict) else {}
        return str(write_result.get("handoff_id") or handoff.get("handoff_id") or "").strip()
    if record_type == "private_trip_request":
        private_request = write_result.get("private_trip_request") if isinstance(write_result.get("private_trip_request"), dict) else {}
        return str(write_result.get("request_id") or private_request.get("request_id") or "").strip()
    return ""


def write_result_allows_success(write_result: dict[str, Any] | None, record_type: str) -> bool:
    contract = _contract_from(write_result)
    record_id = str(contract.get("record_id") or _record_id_from(write_result, record_type)).strip()
    if not record_id:
        return False
    normalized = normalize_write_result(write_result, backend="")
    if not normalized.raw_status:
        # No formal write_result_contract at all -- a known legacy shape.
        # Its only success signal is "has a record id and wasn't marked
        # executed=False".
        return bool(contract.get("executed", True))
    # An UNKNOWN (unrecognized, never-before-seen) status is treated as
    # failure for safety -- normalize_write_result already logged it loudly
    # so this is never a silent misclassification.
    return normalized.outcome is WriteOutcome.SUCCESS


def customer_message_from_write_result(
    write_result: dict[str, Any] | None,
    record_type: str,
    language: str = "en",
    *,
    display_name: str = "",
) -> str:
    contract = _contract_from(write_result)
    normalized = normalize_write_result(write_result, backend="")
    status = normalized.raw_status
    record_id = str(contract.get("record_id") or _record_id_from(write_result, record_type)).strip()
    arabic = str(language or "").strip().lower().startswith("ar")

    if status in _IDEMPOTENT_REPLAY_STATUSES and record_id:
        if record_type == "private_trip_request":
            return f"Private trip request {record_id} is already recorded. The team will follow up within 24-48 hours."
        if record_type == "booking":
            return f"طلب الحجز {record_id} مسجل بالفعل، وسيتابعه فريق Ravel." if arabic else f"Booking request {record_id} is already recorded. The Ravel team will follow up."
        if record_type == "handoff":
            return f"طلب التواصل مع موظف {record_id} مسجل بالفعل، وسيتابعه الفريق." if arabic else f"Human handoff request {record_id} is already recorded. The team will follow up."
        if record_type == "lead":
            return f"طلبك {record_id} مسجل بالفعل، وسنتابعه معك." if arabic else f"Your request {record_id} is already recorded. We will follow up with you."

    if normalized.outcome is WriteOutcome.SUCCESS and record_id:
        if record_type == "private_trip_request":
            return f"Private trip request {record_id} has been saved. The team will follow up within 24-48 hours."
        if record_type == "booking":
            return f"تم تسجيل طلب الحجز {record_id}. فريق Ravel سيتابع معك الخطوة التالية." if arabic else f"Booking request {record_id} has been created. The Ravel team will follow up with the next step."
        if record_type == "handoff":
            return f"تم تسجيل طلب التواصل مع موظف {record_id}. سيتابع معك الفريق." if arabic else f"Human handoff request {record_id} has been created. The team will follow up with you."
        if record_type == "lead":
            name_part = f" لـ {display_name}" if display_name and arabic else (f" for {display_name}" if display_name else "")
            return f"تم تسجيل طلبك {record_id}{name_part}. سنتابعه معك." if arabic else f"Your request {record_id}{name_part} has been saved. We will follow up with you."

    if normalized.outcome is not WriteOutcome.SUCCESS or not record_id:
        error_code = str(contract.get("error_code") or "").strip().lower()
        if record_type == "private_trip_request":
            return "I could not save the private trip request right now. Please try again or contact us directly."
        if record_type == "booking" and error_code == "capacity_unavailable":
            return "لم أستطع إنشاء طلب الحجز لهذا الخيار لأن التوافر تغير. من فضلك اختر خيار غرفة آخر، أو يمكنني توصيلك بموظف بشري." if arabic else "I could not create the booking request for that option because availability changed. Please choose another room option, or I can connect you with a human agent."
        if record_type == "booking":
            return "لا أقدر أسجل طلب الحجز الآن. من فضلك راجع التفاصيل أو أكدها مرة أخرى." if arabic else "I could not create the booking request yet. Please review or confirm the details again."
        if record_type == "handoff":
            return "لم أتمكن من تسجيل طلب التواصل مع موظف الآن. من فضلك حاول مرة أخرى أو تواصل معنا مباشرة." if arabic else "I could not submit the human handoff request right now. Please try again or contact us directly."
        if record_type == "lead":
            return "لم أتمكن من تسجيل طلبك الآن. من فضلك حاول مرة أخرى." if arabic else "I could not save your request right now. Please try again."

    return "لم أتمكن من إكمال الطلب الآن. من فضلك حاول مرة أخرى." if arabic else "I could not complete the request right now. Please try again."


def response_claims_write_success(text: str) -> bool:
    """True if the text asserts a write succeeded.

    A success word (e.g. "created") only counts as a claim if it is not
    immediately negated ("no booking was created", "لم يتم إنشاء") -- an
    honest failure message describing what did NOT happen must not be
    mistaken for a false success claim.
    """
    normalized = str(text or "").casefold()
    for word in _SUCCESS_WORDS:
        word_cf = word.casefold()
        start = 0
        while True:
            idx = normalized.find(word_cf, start)
            if idx == -1:
                break
            window = normalized[max(0, idx - 25) : idx]
            if not any(negation in window for negation in _NEGATION_WORDS):
                return True
            start = idx + len(word_cf)
    return False


def gate_customer_write_reply(
    *,
    proposed_reply: str,
    write_result: dict[str, Any] | None,
    record_type: str,
    language: str = "en",
    display_name: str = "",
) -> str:
    if write_result_allows_success(write_result, record_type):
        if normalize_write_result(write_result, backend="").raw_status in _IDEMPOTENT_REPLAY_STATUSES:
            return customer_message_from_write_result(write_result, record_type, language, display_name=display_name)
        return proposed_reply or customer_message_from_write_result(write_result, record_type, language, display_name=display_name)
    if response_claims_write_success(proposed_reply) or not str(proposed_reply or "").strip():
        return customer_message_from_write_result(write_result, record_type, language, display_name=display_name)
    return proposed_reply


def detect_write_record_type(tool_name: str, write_result: dict[str, Any] | None) -> str:
    name = str(tool_name or "").strip()
    if "booking" in name:
        return "booking"
    if "handoff" in name:
        return "handoff"
    if "private_trip_request" in name or "private" in name:
        return "private_trip_request"
    if "lead" in name:
        return "lead"
    if isinstance(write_result, dict):
        if "booking_draft" in write_result or "booking_id" in write_result:
            return "booking"
        if "handoff_case" in write_result or "handoff_id" in write_result:
            return "handoff"
        if "lead_update" in write_result or "lead_id" in write_result:
            return "lead"
        if "private_trip_request" in write_result or "request_id" in write_result:
            return "private_trip_request"
    return "write"
