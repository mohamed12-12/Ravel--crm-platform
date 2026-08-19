"""Business-rule constants and normalizers ActionValidator (action_validator.py)
checks against -- allowed fields, status sets, thresholds -- imported
directly from unified_service.py's own status constants where applicable
so the two never define an overlapping status list independently and
drift apart.
"""
from __future__ import annotations

import re

from services.ai_agent.validation.lexicon import TRIP_TYPE_TERMS
from services.crm.system_services.unified_service import (
    ARCHIVED_TRAVELER_STATUSES,
    BLOCKED_STATUSES,
    BOOKING_ACTIVE_STATUSES,
    BOOKING_LIFECYCLE_STATUSES,
)


SUPPORTED_VALIDATION_ACTIONS = {
    "create_traveler",
    "update_traveler",
    "create_lead",
    "update_lead_stage",
    "create_booking_draft",
    "update_booking",
    "upload_passport",
    "create_handoff",
    "create_private_trip_request",
    "set_guardian_consent",
    "flag_lead_guardian_approval",
}

CLOSED_LEAD_STAGES = {"Won", "Lost", "Blocked", "Cancelled", "Closed"}
REJECTED_TRAVELER_STATUSES = {
    *BLOCKED_STATUSES,
    *ARCHIVED_TRAVELER_STATUSES,
}
ACTIVE_BOOKING_STATUSES = {status for status in BOOKING_ACTIVE_STATUSES}
VALID_ROOM_TYPES = {"Single", "Double", "Triple"}
VALID_FLIGHT_OPTION_MAP = {
    "with flight": "With Flight",
    "with flights": "With Flight",
    "withflight": "With Flight",
    "withflights": "With Flight",
    "flight included": "With Flight",
    "include flights": "With Flight",
    "include flight": "With Flight",
    "with": "With Flight",
    "yes flight": "With Flight",
    "yes flights": "With Flight",
    "yes": "With Flight",
    "مع طيران": "With Flight",
    "شامل طيران": "With Flight",
    "اريد طيران": "With Flight",
    "اريد الرحلة شاملة الطيران": "With Flight",
    "أريد طيران": "With Flight",
    "أريد الرحلة شاملة الطيران": "With Flight",
    "without flight": "Without Flight",
    "without flights": "Without Flight",
    "withoutflight": "Without Flight",
    "withoutflights": "Without Flight",
    "without": "Without Flight",
    "witout": "Without Flight",
    "withot": "Without Flight",
    "whitout": "Without Flight",
    "wihout": "Without Flight",
    "wthout": "Without Flight",
    "with out": "Without Flight",
    "w out": "Without Flight",
    "w/out": "Without Flight",
    "no flight": "Without Flight",
    "no flights": "Without Flight",
    "noflight": "Without Flight",
    "noflights": "Without Flight",
    "no need flight": "Without Flight",
    "no need flights": "Without Flight",
    "no": "Without Flight",
    "not applicable": "Not Applicable",
    "not needed": "Not Applicable",
    "not available": "Not Applicable",
    "لا": "Without Flight",
    "exclude flights": "Without Flight",
    "بدون طيران": "Without Flight",
    "من غير طيران": "Without Flight",
    "لا اريد طيران": "Without Flight",
    "لا اريد الرحلة شاملة الطيران": "Without Flight",
    "لا أريد طيران": "Without Flight",
    "لا أريد الرحلة شاملة الطيران": "Without Flight",
    "مش عايز طيران": "Without Flight",
    "مش عايزة طيران": "Without Flight",
}
# Canonical source moved to lexicon.py's TRIP_TYPE_TERMS -- re-exported
# under this name for backward compatibility.
TRIP_TYPE_ALIASES = TRIP_TYPE_TERMS
HANDOFF_CONFIDENCE_THRESHOLD = 0.5
HANDOFF_VALIDATION_FAILURE_THRESHOLD = 2
ALLOWED_TRAVELER_UPDATE_FIELDS = {
    "full_name",
    "birthday",
    "gender",
    "nationality",
    "preferred_currency",
    "country_code",
    "raw_phone",
    "passport_attachment_ref",
}
ALLOWED_BOOKING_UPDATE_FIELDS = {
    "requested_stage",
    "payment_status",
    "room_type",
    "flight_option",
    "currency",
    "booking_notes",
}
VALID_BOOKING_STATUSES = set(BOOKING_LIFECYCLE_STATUSES)


def normalize_flight_option(value: str | None) -> str:
    lowered = re.sub(r"\s+", " ", str(value or "").strip().casefold())
    if not lowered:
        return ""
    if lowered in VALID_FLIGHT_OPTION_MAP:
        return VALID_FLIGHT_OPTION_MAP[lowered]
    compact = re.sub(r"[^0-9a-z\u0600-\u06ff]+", "", lowered)
    if compact in VALID_FLIGHT_OPTION_MAP:
        return VALID_FLIGHT_OPTION_MAP[compact]
    return ""


def normalize_trip_type(value: str | None) -> str:
    """Map clear Arabic/English trip-type intent to the CRM values."""

    normalized = re.sub(r"[\u064b-\u065f\u0670]", "", str(value or "").casefold())
    normalized = normalized.translate(str.maketrans({"\u0623": "\u0627", "\u0625": "\u0627", "\u0622": "\u0627", "\u0649": "\u064a", "\u0629": "\u0647"}))
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return ""
    if normalized in TRIP_TYPE_ALIASES:
        return TRIP_TYPE_ALIASES[normalized]

    # The Arabic definite article "ال" attaches directly to the noun with no
    # space ("المحلية" = "ال" + "محلية"), so a plain \w word-boundary check
    # never matches it -- "خلينا في المحلية" ("let's stay with the local
    # one") would otherwise silently fail to normalize at all. Also try the
    # same search with a leading "ال" stripped from every word.
    dearticled = re.sub(r"(?<!\S)ال", "", normalized)
    for candidate in (normalized, dearticled):
        for alias, trip_type in TRIP_TYPE_ALIASES.items():
            if len(alias) < 3:
                continue
            if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", candidate):
                return trip_type
    return ""
