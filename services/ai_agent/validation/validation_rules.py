from __future__ import annotations

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
    "flight included": "With Flight",
    "include flights": "With Flight",
    "yes": "With Flight",
    "without flight": "Without Flight",
    "without flights": "Without Flight",
    "no flight": "Without Flight",
    "no flights": "Without Flight",
    "no": "Without Flight",
    "exclude flights": "Without Flight",
}
HANDOFF_CONFIDENCE_THRESHOLD = 0.5
HANDOFF_VALIDATION_FAILURE_THRESHOLD = 2
ALLOWED_TRAVELER_UPDATE_FIELDS = {
    "full_name",
    "birthday",
    "gender",
    "nationality",
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
    lowered = str(value or "").strip().casefold()
    if not lowered:
        return ""
    if lowered in VALID_FLIGHT_OPTION_MAP:
        return VALID_FLIGHT_OPTION_MAP[lowered]
    return ""
