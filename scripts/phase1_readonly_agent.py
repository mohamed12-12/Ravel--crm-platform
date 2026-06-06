from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.phase0_cleanup import (  # noqa: E402
    TODAY,
    canonicalize_status,
    ensure_headers,
    excel_date_to_date,
    normalize_phone_fields,
)


TRAVELERS_SHEET = "Travelers"
TRIPS_SHEET = "Trips"

TRAVELERS_EXPECTED_HEADERS = [
    "Status",
    "Traveler ID",
    "Full Name",
    "Code",
    "WhatsApp",
    "Loc. Trips",
    "Int. Trips",
    "Total trips",
    "Integrated WhatsApp",
    "Phone Lookup Key",
    "Data Audit",
]

TRIPS_EXPECTED_HEADERS = [
    "Trip ID",
    "Trip Name",
    "Type",
    "Year",
    "Start Date",
    "End Date",
    "Sales Status",
    "Data Audit",
]

BLOCKED_STATUSES = {"Blacklisted"}
REVIEW_STATUSES = {"Payment Risk", "High Maintenance"}
SUPPORTED_TRIP_TYPES = {"Local", "International"}


@dataclass
class TravelerRecord:
    row: int
    traveler_id: str
    full_name: str
    status: str
    code: str
    whatsapp: str
    integrated_whatsapp: str
    phone_lookup_key: str
    loc_trips: int | None
    int_trips: int | None
    total_trips: int | None
    data_audit: str


@dataclass
class TripRecord:
    row: int
    trip_id: str
    trip_name: str
    trip_type: str
    year: int | None
    start_date: str | None
    end_date: str | None
    sales_status: str
    data_audit: str
    remaining_single: int | None = None
    remaining_double: int | None = None
    remaining_triple: int | None = None
    draft_holds_single: int = 0
    draft_holds_double: int = 0
    draft_holds_triple: int = 0
    available_single: int | None = None
    available_double: int | None = None
    available_triple: int | None = None
    remaining_places: int | None = None
    availability_status: str = "unknown"


def normalize_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    return None


def format_date(value: object) -> str | None:
    parsed = excel_date_to_date(value)
    if parsed:
        return parsed.isoformat()
    text = str(value).strip() if value is not None else ""
    return text or None


def normalize_trip_type(raw_type: str) -> str | None:
    lowered = raw_type.strip().lower()
    if lowered in {"local", "inside egypt", "egypt", "egypt trips"}:
        return "Local"
    if lowered in {"international", "abroad", "outside egypt"}:
        return "International"
    return None


def normalize_name(value: str) -> str:
    lowered = value.casefold().strip()
    lowered = re.sub(r"[^0-9a-z\u0600-\u06ff\s]+", " ", lowered)
    return " ".join(lowered.split())


def tokenize_name(value: str) -> list[str]:
    return [token for token in normalize_name(value).split() if token]


def names_are_compatible(submitted_name: str, existing_name: str) -> bool:
    submitted_normalized = normalize_name(submitted_name)
    existing_normalized = normalize_name(existing_name)
    if not submitted_normalized or not existing_normalized:
        return False

    if submitted_normalized == existing_normalized:
        return True

    submitted_tokens = tokenize_name(submitted_name)
    existing_tokens = tokenize_name(existing_name)
    if not submitted_tokens or not existing_tokens:
        return False

    # REQUIRE at least 2 tokens on both sides for auto-matching
    if len(submitted_tokens) < 2 or len(existing_tokens) < 2:
        return False

    # Strict first/last name check
    if len(submitted_tokens) >= 2 and len(existing_tokens) >= 2:
        if submitted_tokens[0] == existing_tokens[0] and submitted_tokens[-1] == existing_tokens[-1]:
            return True
        first_name_ratio = difflib.SequenceMatcher(None, submitted_tokens[0], existing_tokens[0]).ratio()
        last_name_ratio = difflib.SequenceMatcher(None, submitted_tokens[-1], existing_tokens[-1]).ratio()
        if first_name_ratio >= 0.95 and last_name_ratio >= 0.90:
            return True

    # High shared token ratio requirement
    shared_tokens = set(submitted_tokens) & set(existing_tokens)
    shorter_size = min(len(set(submitted_tokens)), len(set(existing_tokens)))
    if shorter_size and len(shared_tokens) >= 2 and (len(shared_tokens) / shorter_size) >= 0.80:
        return True

    # Final fuzzy fallback must be very high and include multiple shared tokens
    ratio = difflib.SequenceMatcher(None, submitted_normalized, existing_normalized).ratio()
    return ratio >= 0.92 and len(shared_tokens) >= 2


def load_headers(workbook_path: Path) -> tuple[Any, Any, dict[str, int], dict[str, int]]:
    wb = load_workbook(workbook_path, data_only=True, read_only=False)
    travelers_ws = wb[TRAVELERS_SHEET]
    trips_ws = wb[TRIPS_SHEET]
    traveler_headers = ensure_headers(
        travelers_ws,
        header_row=1,
        expected_headers=TRAVELERS_EXPECTED_HEADERS,
        helper_headers=[],
    )
    trip_headers = ensure_headers(
        trips_ws,
        header_row=2,
        expected_headers=TRIPS_EXPECTED_HEADERS,
        helper_headers=[],
    )
    return wb, travelers_ws, trips_ws, traveler_headers, trip_headers


def collect_travelers(travelers_ws, header_map: dict[str, int]) -> list[TravelerRecord]:
    results: list[TravelerRecord] = []
    for row_idx in range(2, travelers_ws.max_row + 1):
        traveler_id = travelers_ws.cell(row_idx, header_map["Traveler ID"]).value
        full_name = travelers_ws.cell(row_idx, header_map["Full Name"]).value
        code = travelers_ws.cell(row_idx, header_map["Code"]).value
        whatsapp = travelers_ws.cell(row_idx, header_map["WhatsApp"]).value
        integrated = travelers_ws.cell(row_idx, header_map["Integrated WhatsApp"]).value
        lookup_key = travelers_ws.cell(row_idx, header_map["Phone Lookup Key"]).value

        if not any([traveler_id, full_name, code, whatsapp, integrated, lookup_key]):
            continue

        results.append(
            TravelerRecord(
                row=row_idx,
                traveler_id=str(traveler_id).strip() if traveler_id else "",
                full_name=str(full_name).strip() if full_name else "",
                status=canonicalize_status(travelers_ws.cell(row_idx, header_map["Status"]).value),
                code=str(code).strip() if code else "",
                whatsapp=str(whatsapp).strip() if whatsapp else "",
                integrated_whatsapp=str(integrated).strip() if integrated else "",
                phone_lookup_key=str(lookup_key).strip() if lookup_key else "",
                loc_trips=normalize_int(travelers_ws.cell(row_idx, header_map["Loc. Trips"]).value),
                int_trips=normalize_int(travelers_ws.cell(row_idx, header_map["Int. Trips"]).value),
                total_trips=normalize_int(travelers_ws.cell(row_idx, header_map["Total trips"]).value),
                data_audit=str(travelers_ws.cell(row_idx, header_map["Data Audit"]).value or "").strip(),
            )
        )
    return results


def collect_trips(trips_ws, header_map: dict[str, int]) -> list[TripRecord]:
    results: list[TripRecord] = []
    for row_idx in range(3, trips_ws.max_row + 1):
        trip_id = trips_ws.cell(row_idx, header_map["Trip ID"]).value
        trip_name = trips_ws.cell(row_idx, header_map["Trip Name"]).value
        if not trip_id and not trip_name:
            continue

        remaining_single = normalize_int(trips_ws.cell(row_idx, 11).value)
        remaining_double = normalize_int(trips_ws.cell(row_idx, 12).value)
        remaining_triple = normalize_int(trips_ws.cell(row_idx, 13).value)
        draft_holds_single = (normalize_int(trips_ws.cell(row_idx, header_map.get("Draft Holds Single", 0)).value) or 0) if header_map.get("Draft Holds Single") else 0
        draft_holds_double = (normalize_int(trips_ws.cell(row_idx, header_map.get("Draft Holds Double", 0)).value) or 0) if header_map.get("Draft Holds Double") else 0
        draft_holds_triple = (normalize_int(trips_ws.cell(row_idx, header_map.get("Draft Holds Triple", 0)).value) or 0) if header_map.get("Draft Holds Triple") else 0
        available_single = max(remaining_single - draft_holds_single, 0) if remaining_single is not None else None
        available_double = max(remaining_double - draft_holds_double, 0) if remaining_double is not None else None
        available_triple = max(remaining_triple - draft_holds_triple, 0) if remaining_triple is not None else None
        available_values = [value for value in (available_single, available_double, available_triple) if value is not None]
        remaining_places = sum(available_values) if available_values else None
        if remaining_places is None:
            availability_status = "capacity_unknown"
        elif remaining_places > 0:
            availability_status = "available"
        else:
            availability_status = "full"

        results.append(
            TripRecord(
                row=row_idx,
                trip_id=str(trip_id).strip() if trip_id else "",
                trip_name=str(trip_name).strip() if trip_name else "",
                trip_type=str(trips_ws.cell(row_idx, header_map["Type"]).value or "").strip(),
                year=normalize_int(trips_ws.cell(row_idx, header_map["Year"]).value),
                start_date=format_date(trips_ws.cell(row_idx, header_map["Start Date"]).value),
                end_date=format_date(trips_ws.cell(row_idx, header_map["End Date"]).value),
                # Treat blank Sales Status as "Open" so newly added rows appear immediately
                sales_status=str(trips_ws.cell(row_idx, header_map["Sales Status"]).value or "Open").strip() or "Open",
                data_audit=str(trips_ws.cell(row_idx, header_map["Data Audit"]).value or "").strip(),
                remaining_single=remaining_single,
                remaining_double=remaining_double,
                remaining_triple=remaining_triple,
                draft_holds_single=draft_holds_single,
                draft_holds_double=draft_holds_double,
                draft_holds_triple=draft_holds_triple,
                available_single=available_single,
                available_double=available_double,
                available_triple=available_triple,
                remaining_places=remaining_places,
                availability_status=availability_status,
            )
        )
    return results


def find_traveler_matches(
    travelers: list[TravelerRecord],
    raw_phone: str,
    country_code: str = "",
) -> tuple[dict[str, Any], list[TravelerRecord]]:
    normalized = normalize_phone_fields(country_code, raw_phone)
    matches: list[TravelerRecord] = []

    if normalized.lookup_key:
        matches = [record for record in travelers if record.phone_lookup_key == normalized.lookup_key]

    if not matches and normalized.normalized_whatsapp:
        matches = [record for record in travelers if record.integrated_whatsapp == normalized.normalized_whatsapp]

    if not matches and normalized.local_number:
        matches = [record for record in travelers if record.whatsapp == normalized.local_number]

    return asdict(normalized), matches


def recommend_trips(
    trips: list[TripRecord],
    desired_type: str,
    today: date = TODAY,
) -> dict[str, list[dict[str, Any]]]:
    normalized_type = normalize_trip_type(desired_type)
    if normalized_type is None:
        raise ValueError(f"Unsupported trip type {desired_type!r}")

    open_trips: list[dict[str, Any]] = []
    tbd_trips: list[dict[str, Any]] = []

    for trip in trips:
        # 1. Filter by trip type (Local vs International)
        if trip.trip_type != normalized_type:
            continue

        # 2. Exclude cancelled, closed, or archived trips (only Open or Date TBD allowed)
        if trip.sales_status not in {"Open", "Date TBD"}:
            continue

        # 3. Handle 'Open' trips with specific dates
        if trip.sales_status == "Open" and trip.start_date:
            # Only show trips with future Start Date
            if trip.start_date >= today.isoformat() and trip.availability_status != "full":
                open_trips.append(asdict(trip))
            continue

        # 4. Handle trips without dates (Date TBD or Open without dates)
        # Offer as follow-up, not a confirmed trip
        if trip.sales_status == "Date TBD" or (trip.sales_status == "Open" and not trip.start_date):
            if trip.availability_status != "full":
                tbd_trips.append(asdict(trip))

    # Sort open trips by departure date, TBD trips by year
    open_trips.sort(key=lambda item: (item["start_date"] or "", item["trip_name"]))
    tbd_trips.sort(key=lambda item: ((item["year"] or 0), item["trip_name"]))
    return {"open_trips": open_trips, "date_tbd_trips": tbd_trips}


def build_agent_response_from_wb(
    wb: Any,  # FakeWorkbook or real openpyxl Workbook
    full_name: str,
    raw_phone: str,
    trip_type: str | None,
    country_code: str = "",
    trip_result_override: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Same as build_agent_response() but accepts a pre-loaded workbook."""
    travelers_ws = wb[TRAVELERS_SHEET]
    trips_ws = wb[TRIPS_SHEET]
    traveler_headers = ensure_headers(
        travelers_ws,
        header_row=1,
        expected_headers=TRAVELERS_EXPECTED_HEADERS,
        helper_headers=[],
    )
    trip_headers = ensure_headers(
        trips_ws,
        header_row=2,
        expected_headers=TRIPS_EXPECTED_HEADERS,
        helper_headers=[],
    )
    travelers = collect_travelers(travelers_ws, traveler_headers)
    trips = collect_trips(trips_ws, trip_headers)
    normalized_phone, matches = find_traveler_matches(travelers, raw_phone, country_code)

    result: dict[str, Any] = {
        "customer_name": full_name,
        "lookup_phone": normalized_phone,
        "match_status": "",
        "name_match_status": "",
        "traveler": None,
        "handoff_required": False,
        "handoff_reason": "",
        "actions": [],
        "trip_result": None,
    }

    if not matches:
        result["match_status"] = "not_found"
        result["actions"].append("collect_new_traveler_data")
    elif len(matches) > 1:
        result["match_status"] = "multiple_matches"
        result["handoff_required"] = True
        result["handoff_reason"] = "duplicate_phone_match"
        result["actions"].append("human_review_duplicate_phone")
        result["traveler"] = [asdict(record) for record in matches]
    else:
        traveler = matches[0]
        result["match_status"] = "single_match"
        result["traveler"] = asdict(traveler)
        if traveler.status in BLOCKED_STATUSES:
            result["name_match_status"] = "blocked_status"
            result["handoff_required"] = True
            result["handoff_reason"] = "blacklisted_customer"
            result["actions"].append("block_sales_flow")
        elif not normalize_name(full_name):
            result["name_match_status"] = "phone_only"
            result["actions"].append("continue_sales_flow")
        elif not names_are_compatible(full_name, traveler.full_name):
            result["name_match_status"] = "conflict"
            result["handoff_required"] = True
            result["handoff_reason"] = "phone_name_conflict"
            result["actions"].append("human_review_identity_conflict")
        elif traveler.status in REVIEW_STATUSES:
            result["name_match_status"] = "matched"
            result["handoff_required"] = True
            result["handoff_reason"] = traveler.status.lower().replace(" ", "_")
            result["actions"].append("allow_conversation_but_require_human_review")
        else:
            result["name_match_status"] = "matched"
            result["actions"].append("continue_sales_flow")

    if trip_type and not result["handoff_required"]:
        trip_result = trip_result_override if trip_result_override is not None else recommend_trips(trips, trip_type, today=TODAY)
        result["trip_result"] = trip_result
        if trip_result["open_trips"]:
            result["actions"].append("show_open_trips")
        elif trip_result["date_tbd_trips"]:
            result["actions"].append("offer_date_tbd_follow_up")
        else:
            result["actions"].append("no_trip_available")

    return result


def build_agent_response(
    workbook_path: Path,
    full_name: str,
    raw_phone: str,
    trip_type: str | None,
    country_code: str = "",
    trip_result_override: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    wb, travelers_ws, trips_ws, traveler_headers, trip_headers = load_headers(workbook_path)
    result = build_agent_response_from_wb(
        wb=wb,
        full_name=full_name,
        raw_phone=raw_phone,
        trip_type=trip_type,
        country_code=country_code,
        trip_result_override=trip_result_override,
    )
    wb.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 1 read-only sales agent prototype.")
    parser.add_argument("--workbook", required=True, type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--phone", required=True)
    parser.add_argument("--country-code", default="")
    parser.add_argument("--trip-type", default=None)
    args = parser.parse_args()

    result = build_agent_response(
        workbook_path=args.workbook,
        full_name=args.name,
        raw_phone=args.phone,
        trip_type=args.trip_type,
        country_code=args.country_code,
    )
    print(json.dumps(result, indent=2, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
