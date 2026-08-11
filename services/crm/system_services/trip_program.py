from __future__ import annotations

import json
import re
from typing import Any


_DAY_RE = re.compile(r"^(?:day\s*)?(\d+)\s*[:.)-]\s*(.*)$", re.IGNORECASE)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def parse_text_list(value: Any) -> list[str]:
    raw = _clean(value)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, list):
        return [_clean(item) for item in parsed if _clean(item)]
    if isinstance(parsed, dict):
        return [_clean(item) for item in parsed.values() if _clean(item)]
    items = []
    for line in raw.splitlines():
        clean_line = re.sub(r"^\s*[-*•]\s*", "", line).strip()
        if clean_line:
            items.append(clean_line)
    return items


def parse_itinerary(value: Any) -> list[dict[str, Any]]:
    raw = _clean(value)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, list):
        days = []
        for index, item in enumerate(parsed, start=1):
            if isinstance(item, dict):
                title = _clean(item.get("title") or item.get("name") or item.get("day_title"))
                details = _clean(item.get("details") or item.get("description") or item.get("program") or item.get("text"))
                day_number = item.get("day") or item.get("day_number") or index
            else:
                title = ""
                details = _clean(item)
                day_number = index
            if title or details:
                days.append({"day": day_number, "title": title, "details": details})
        return days
    if isinstance(parsed, dict):
        return parse_itinerary(json.dumps(list(parsed.values()), ensure_ascii=False))

    days = []
    for index, line in enumerate([part.strip() for part in raw.splitlines() if part.strip()], start=1):
        match = _DAY_RE.match(line)
        if match:
            day_number = int(match.group(1))
            details = match.group(2).strip()
        else:
            day_number = index
            details = line
        title, _, detail_tail = details.partition(":")
        if detail_tail:
            days.append({"day": day_number, "title": title.strip(), "details": detail_tail.strip()})
        else:
            days.append({"day": day_number, "title": "", "details": details})
    return days


def build_trip_program(trip: dict[str, Any]) -> dict[str, Any]:
    itinerary = parse_itinerary(trip.get("itinerary") or trip.get("day_program"))
    inclusions = parse_text_list(trip.get("inclusions"))
    exclusions = parse_text_list(trip.get("exclusions"))
    missing_fields = [
        field
        for field, values in (
            ("itinerary", itinerary),
            ("inclusions", inclusions),
            ("exclusions", exclusions),
        )
        if not values
    ]
    return {
        "itinerary": itinerary,
        "day_program": itinerary,
        "inclusions": inclusions,
        "exclusions": exclusions,
        "missing_fields": missing_fields,
        "source": "crm_trip_record",
        "missing_policy": "If a requested itinerary/program detail is absent here, answer that it is unlisted in CRM.",
    }
