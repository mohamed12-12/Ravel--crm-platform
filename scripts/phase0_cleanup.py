from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from copy import copy
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from openpyxl import Workbook, load_workbook
from openpyxl.styles import PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from openpyxl.worksheet.datavalidation import DataValidation


TODAY = date.today()

CANONICAL_STATUSES = [
    "Active",
    "Repeat",
    "VIP",
    "Cancelled",
    "Inactive",
    "High Maintenance",
    "Payment Risk",
    "Blacklisted",
]

STATUS_ALIASES = {
    "": "",
    "active": "Active",
    "active traveler": "Active",
    "repeat": "Repeat",
    "repeat traveler": "Repeat",
    "vip": "VIP",
    "vip traveler": "VIP",
    "cancelled": "Cancelled",
    "cancelled booking": "Cancelled",
    "inactive": "Inactive",
    "inactive traveler": "Inactive",
    "high maintenance": "High Maintenance",
    "payment risk": "Payment Risk",
    "blacklisted": "Blacklisted",
    "blackllisted": "Blacklisted",
}

TRAVELERS_SHEET = "Travelers"
TRIPS_SHEET = "Trips"
AUDIT_SHEET = "Phase 0 Audit"
INTERACTIONS_SHEET = "Interactions"

TRAVELERS_HEADERS = [
    "Status",
    "Traveler ID",
    "Full Name",
    "First Name",
    "Last Name",
    "Birthday",
    "Gender",
    "Nationality",
    "Code",
    "WhatsApp",
    "Email",
    "Community Whatsapp",
    "Residence",
    "Loc. Trips",
    "Int. Trips",
    "Total trips",
    "Comm. Events",
    "Lifetime Revenue",
    "Notes",
    "Introduce yourself",
    "Emergency Contact",
    "Emergency Phone",
    "Medical Notes",
    "Room Preference",
    "⭐ Rating (1–5)",
]

TRAVELERS_HELPER_HEADERS = [
    "Integrated WhatsApp",
    "Normalized WhatsApp",
    "Phone Lookup Key",
    "Lead Source",
    "Created At",
    "Last Contacted At",
    "Agent Notes",
    "Data Audit",
]

TRIPS_HELPER_HEADERS = [
    "Sales Status",
    "Public Description",
    "Public Price",
    "Sales Notes",
    "Data Audit",
]


@dataclass
class PhoneNormalization:
    code: str
    local_number: str
    normalized_whatsapp: str
    lookup_key: str
    flags: list[str]


def normalize_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def digits_only(value: object) -> str:
    text = normalize_text(value)
    return "".join(ch for ch in text if ch.isdigit())


def canonicalize_status(value: object) -> str:
    raw = normalize_text(value)
    lowered = " ".join(raw.split()).lower()
    if lowered in STATUS_ALIASES:
        return STATUS_ALIASES[lowered]
    return raw


def normalize_phone_fields(code_value: object, phone_value: object) -> PhoneNormalization:
    code = digits_only(code_value)
    phone = digits_only(phone_value)
    flags: list[str] = []

    if phone.startswith("00"):
        phone = phone[2:]
        flags.append("INTL_PREFIX_IN_PHONE")

    if phone.startswith("+"):
        phone = phone[1:]
        flags.append("PLUS_PREFIX_IN_PHONE")

    if not code and phone.startswith("20") and len(phone) == 12:
        code = "20"
        phone = phone[2:]
        flags.append("CODE_DERIVED_FROM_PHONE")

    if not code and phone.startswith("01") and len(phone) == 11:
        code = "20"
        phone = phone[1:]
        flags.append("CODE_DERIVED_FROM_EGYPT_LOCAL")

    if not code and phone.startswith("1") and len(phone) == 10:
        code = "20"
        flags.append("CODE_DERIVED_FROM_EGYPT_LOCAL")

    if code and phone.startswith(code) and len(phone) > 10:
        phone = phone[len(code) :]
        flags.append("PHONE_INCLUDED_COUNTRY_CODE")

    if phone.startswith("0") and len(phone) in (11, 12):
        phone = phone[1:]
        flags.append("LEADING_ZERO_REMOVED")

    if not code:
        flags.append("MISSING_CODE")
    if not phone:
        flags.append("MISSING_WHATSAPP")

    if phone and len(phone) < 8:
        flags.append("SHORT_PHONE")
    if phone and len(phone) > 12:
        flags.append("LONG_PHONE")

    normalized_whatsapp = f"+{code}{phone}" if code and phone else ""
    lookup_key = f"{code}:{phone}" if code and phone else phone

    return PhoneNormalization(
        code=code,
        local_number=phone,
        normalized_whatsapp=normalized_whatsapp,
        lookup_key=lookup_key,
        flags=sorted(set(flags)),
    )


def row_has_values(values: Iterable[object]) -> bool:
    return any(value not in (None, "") for value in values)


def is_meaningful_traveler_row(ws: Worksheet, header_map: dict[str, int], row_idx: int) -> bool:
    key_headers = ["Status", "Traveler ID", "Full Name", "Code", "WhatsApp", "Email", "Notes"]
    values = [ws.cell(row_idx, header_map[header]).value for header in key_headers]
    return row_has_values(values)


def is_meaningful_trip_row(ws: Worksheet, header_map: dict[str, int], row_idx: int) -> bool:
    key_headers = ["Trip ID", "Trip Name", "Type", "Year", "Start Date", "End Date"]
    values = [ws.cell(row_idx, header_map[header]).value for header in key_headers]
    return row_has_values(values)


def clone_style(source, target) -> None:
    if not all(hasattr(source, attr) and hasattr(target, attr) for attr in ("font", "fill", "border", "alignment", "number_format", "protection")):
        return
    target.font = copy(source.font)
    target.fill = copy(source.fill)
    target.border = copy(source.border)
    target.alignment = copy(source.alignment)
    target.number_format = source.number_format
    target.protection = copy(source.protection)


def ensure_headers(
    ws: Worksheet,
    header_row: int,
    expected_headers: list[str],
    helper_headers: list[str],
) -> dict[str, int]:
    header_map: dict[str, int] = {}
    max_col = ws.max_column

    for col in range(1, max_col + 1):
        header = normalize_text(ws.cell(header_row, col).value)
        if header:
            header_map[header] = col

    reference_style_col = max_col
    for header in helper_headers:
        if header in header_map:
            continue
        max_col += 1
        ws.cell(header_row, max_col).value = header
        clone_style(ws.cell(header_row, reference_style_col), ws.cell(header_row, max_col))
        if header_row == 2:
            ws.cell(1, max_col).value = None
            clone_style(ws.cell(1, reference_style_col), ws.cell(1, max_col))
        header_map[header] = max_col

    for header in expected_headers:
        if header not in header_map:
            raise ValueError(f"Missing required header {header!r} in sheet {ws.title}")

    return header_map


def apply_travelers_status_validation(ws: Worksheet, header_map: dict[str, int]) -> None:
    status_col = get_column_letter(header_map["Status"])
    target_range = f"{status_col}2:{status_col}{ws.max_row}"
    formula = '"' + ",".join(CANONICAL_STATUSES) + '"'

    remaining: list[DataValidation] = []
    for dv in ws.data_validations.dataValidation:
        if str(dv.sqref) == target_range and dv.type == "list":
            continue
        remaining.append(dv)

    ws.data_validations.dataValidation = remaining
    status_dv = DataValidation(type="list", formula1=formula, allow_blank=True)
    status_dv.add(target_range)
    ws.add_data_validation(status_dv)


def ensure_interactions_sheet(wb: Workbook) -> None:
    if INTERACTIONS_SHEET in wb.sheetnames:
        ws = wb[INTERACTIONS_SHEET]
        ws.delete_rows(1, ws.max_row)
    else:
        ws = wb.create_sheet(INTERACTIONS_SHEET)

    headers = [
        "Interaction ID",
        "Timestamp",
        "Channel",
        "Customer Name",
        "Raw Phone",
        "Integrated WhatsApp",
        "Phone Lookup Key",
        "Traveler ID",
        "Matched Row",
        "Status Snapshot",
        "Intent",
        "Trip Type",
        "Suggested Trips",
        "Action Taken",
        "Handoff Required",
        "Handoff Reason",
        "Agent Notes",
    ]

    for col_idx, header in enumerate(headers, start=1):
        ws.cell(1, col_idx).value = header

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"


def build_travelers_audit(
    ws: Worksheet,
    header_map: dict[str, int],
    audit_rows: list[list[object]],
) -> dict[str, int]:
    max_row = ws.max_row
    helper_fills = {
        "issue": PatternFill("solid", fgColor="FDE9D9"),
        "ok": PatternFill("solid", fgColor="E2F0D9"),
    }

    phone_counts: Counter[str] = Counter()
    per_row_normalized: dict[int, PhoneNormalization] = {}
    processed_rows = 0

    for row_idx in range(2, max_row + 1):
        if not is_meaningful_traveler_row(ws, header_map, row_idx):
            continue

        processed_rows += 1
        normalized = normalize_phone_fields(
            ws.cell(row_idx, header_map["Code"]).value,
            ws.cell(row_idx, header_map["WhatsApp"]).value,
        )
        per_row_normalized[row_idx] = normalized
        if normalized.lookup_key:
            phone_counts[normalized.lookup_key] += 1

    status_counts: Counter[str] = Counter()
    summary = Counter()
    for row_idx in range(2, max_row + 1):
        if not is_meaningful_traveler_row(ws, header_map, row_idx):
            continue

        status_cell = ws.cell(row_idx, header_map["Status"])
        traveler_id = normalize_text(ws.cell(row_idx, header_map["Traveler ID"]).value)
        full_name = normalize_text(ws.cell(row_idx, header_map["Full Name"]).value)
        normalized = per_row_normalized[row_idx]

        canonical_status = canonicalize_status(status_cell.value)
        if normalize_text(status_cell.value) != canonical_status:
            summary["status_standardized"] += 1
        status_cell.value = canonical_status
        status_counts[canonical_status] += 1

        code_cell = ws.cell(row_idx, header_map["Code"])
        whatsapp_cell = ws.cell(row_idx, header_map["WhatsApp"])
        code_cell.value = normalized.code or None
        whatsapp_cell.value = normalized.local_number or None
        code_cell.number_format = "@"
        whatsapp_cell.number_format = "@"

        ws.cell(row_idx, header_map["Integrated WhatsApp"]).value = normalized.normalized_whatsapp or None
        ws.cell(row_idx, header_map["Normalized WhatsApp"]).value = normalized.normalized_whatsapp or None
        ws.cell(row_idx, header_map["Phone Lookup Key"]).value = normalized.lookup_key or None

        audit_flags = list(normalized.flags)
        if canonical_status not in ("", *CANONICAL_STATUSES):
            audit_flags.append("UNKNOWN_STATUS")
            summary["unknown_status_rows"] += 1

        if normalized.lookup_key and phone_counts[normalized.lookup_key] > 1:
            audit_flags.append("DUPLICATE_PHONE")
            summary["duplicate_phone_rows"] += 1

        if not traveler_id:
            audit_flags.append("MISSING_TRAVELER_ID")
            summary["missing_traveler_id_rows"] += 1

        if not full_name:
            audit_flags.append("MISSING_FULL_NAME")
            summary["missing_full_name_rows"] += 1

        deduped_flags = sorted(set(audit_flags))
        audit_text = "; ".join(deduped_flags)
        audit_cell = ws.cell(row_idx, header_map["Data Audit"])
        audit_cell.value = audit_text or None
        audit_cell.fill = helper_fills["issue"] if deduped_flags else helper_fills["ok"]

        if deduped_flags:
            for flag in deduped_flags:
                audit_rows.append(
                    [
                        TRAVELERS_SHEET,
                        row_idx,
                        traveler_id,
                        full_name,
                        flag,
                        normalized.lookup_key or "",
                    ]
                )
                summary["traveler_issue_rows"] += 1

    last_col = get_column_letter(ws.max_column)
    ws.auto_filter.ref = f"A1:{last_col}{max_row}"
    summary["traveler_rows_processed"] = processed_rows
    summary["traveler_unique_phone_keys"] = len([key for key in phone_counts if key])
    summary["traveler_duplicate_phone_keys"] = len([key for key, count in phone_counts.items() if count > 1])
    summary["traveler_status_breakdown"] = dict(status_counts)
    return summary


def build_trips_audit(
    ws: Worksheet,
    header_map: dict[str, int],
    audit_rows: list[list[object]],
) -> dict[str, int]:
    summary = Counter()
    max_row = ws.max_row
    helper_fills = {
        "issue": PatternFill("solid", fgColor="FDE9D9"),
        "ok": PatternFill("solid", fgColor="E2F0D9"),
    }

    for row_idx in range(3, max_row + 1):
        if not is_meaningful_trip_row(ws, header_map, row_idx):
            continue

        trip_id = normalize_text(ws.cell(row_idx, header_map["Trip ID"]).value)
        trip_name = normalize_text(ws.cell(row_idx, header_map["Trip Name"]).value)
        trip_type = normalize_text(ws.cell(row_idx, header_map["Type"]).value)
        trip_year = parse_year(ws.cell(row_idx, header_map["Year"]).value)
        start_value = ws.cell(row_idx, header_map["Start Date"]).value
        end_value = ws.cell(row_idx, header_map["End Date"]).value

        flags: list[str] = []
        if not start_value:
            flags.append("MISSING_START_DATE")
            summary["trip_missing_start_date_rows"] += 1
        elif not excel_date_to_date(start_value):
            flags.append("INVALID_START_DATE")
            summary["trip_invalid_start_date_rows"] += 1
        if not end_value:
            flags.append("MISSING_END_DATE")
            summary["trip_missing_end_date_rows"] += 1
        elif not excel_date_to_date(end_value):
            flags.append("INVALID_END_DATE")
            summary["trip_invalid_end_date_rows"] += 1
        if not trip_type:
            flags.append("MISSING_TYPE")
            summary["trip_missing_type_rows"] += 1

        start_date = excel_date_to_date(start_value)
        end_date = excel_date_to_date(end_value)

        sales_status_cell = ws.cell(row_idx, header_map["Sales Status"])
        existing_status = normalize_text(sales_status_cell.value)
        if not existing_status:
            if start_date and start_date >= TODAY:
                sales_status_cell.value = "Open"
                summary["trip_sales_status_set_open"] += 1
            elif end_date and end_date < TODAY:
                sales_status_cell.value = "Closed"
                summary["trip_sales_status_set_closed"] += 1
            elif "INVALID_START_DATE" in flags or "INVALID_END_DATE" in flags:
                sales_status_cell.value = "Date Fix Needed"
                summary["trip_sales_status_set_date_fix_needed"] += 1
            elif not start_value and not end_value:
                if trip_year is not None and trip_year >= TODAY.year:
                    sales_status_cell.value = "Date TBD"
                    summary["trip_sales_status_set_date_tbd"] += 1
                elif trip_year is not None and trip_year < TODAY.year:
                    sales_status_cell.value = "Archived"
                    summary["trip_sales_status_set_archived"] += 1
            elif not start_date or not end_date:
                sales_status_cell.value = "Date Fix Needed"
                summary["trip_sales_status_set_date_fix_needed"] += 1

        if start_date and end_date and end_date < start_date:
            flags.append("END_BEFORE_START")
            summary["trip_end_before_start_rows"] += 1

        if start_date and start_date < TODAY:
            summary["trip_past_departure_rows"] += 1
        if start_date and start_date >= TODAY:
            summary["trip_future_departure_rows"] += 1

        audit_text = "; ".join(sorted(set(flags)))
        audit_cell = ws.cell(row_idx, header_map["Data Audit"])
        audit_cell.value = audit_text or None
        audit_cell.fill = helper_fills["issue"] if audit_text else helper_fills["ok"]

        if flags:
            for flag in sorted(set(flags)):
                audit_rows.append([TRIPS_SHEET, row_idx, trip_id, trip_name, flag, trip_type])
                summary["trip_issue_rows"] += 1

        summary["trip_rows_processed"] += 1

    ws.auto_filter.ref = f"A2:{get_column_letter(ws.max_column)}{max_row}"
    return summary


def excel_date_to_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None or value == "":
        return None
    # Parse text date strings entered manually in Google Sheets
    # Supports: "2026-09-11", "6-2-2026", "6/2/2026", "2026/09/11"
    text = str(value).strip()
    # Try ISO format first (most reliable)
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    # Try common D-M-YYYY, M-D-YYYY, D/M/YYYY, M/D/YYYY patterns
    for sep in ("-", "/"):
        parts = text.split(sep)
        if len(parts) == 3:
            a, b, c = parts
            # YYYY-MM-DD or YYYY/MM/DD already handled by fromisoformat above
            # Try D-M-YYYY or M-D-YYYY (ambiguous; assume D-M-YYYY for Arabic locale)
            if len(c) == 4 and c.isdigit():
                try:
                    # Treat as D-M-YYYY
                    return date(int(c), int(b), int(a))
                except ValueError:
                    try:
                        # Fallback: M-D-YYYY
                        return date(int(c), int(a), int(b))
                    except ValueError:
                        pass
    return None


def parse_year(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    text = normalize_text(value)
    if text.isdigit():
        return int(text)
    return None


def replace_audit_sheet(wb: Workbook, audit_rows: list[list[object]], summary: dict[str, object]) -> None:
    if AUDIT_SHEET in wb.sheetnames:
        del wb[AUDIT_SHEET]
    ws = wb.create_sheet(AUDIT_SHEET)

    ws["A1"] = "Phase 0 Cleanup Summary"
    row_idx = 3
    for key, value in summary.items():
        ws.cell(row_idx, 1).value = key
        if isinstance(value, dict):
            ws.cell(row_idx, 2).value = json.dumps(value, ensure_ascii=True, sort_keys=True)
        else:
            ws.cell(row_idx, 2).value = value
        row_idx += 1

    row_idx += 1
    headers = ["Sheet", "Row", "Key", "Name", "Issue", "Details"]
    for col_idx, header in enumerate(headers, start=1):
        ws.cell(row_idx, col_idx).value = header
    for issue in audit_rows:
        row_idx += 1
        for col_idx, value in enumerate(issue, start=1):
            ws.cell(row_idx, col_idx).value = value

    ws.auto_filter.ref = f"A{row_idx - len(audit_rows)}:F{row_idx}"


def write_markdown_report(report_path: Path, summary: dict[str, object], audit_rows: list[list[object]]) -> None:
    status_breakdown = summary.get("traveler_status_breakdown", {})
    lines = [
        "# Phase 0 Cleanup Report",
        "",
        f"Date: {TODAY.isoformat()}",
        "",
        "## Summary",
        "",
        f"- Traveler rows processed: {summary.get('traveler_rows_processed', 0)}",
        f"- Trip rows processed: {summary.get('trip_rows_processed', 0)}",
        f"- Status values standardized: {summary.get('status_standardized', 0)}",
        f"- Duplicate phone keys: {summary.get('traveler_duplicate_phone_keys', 0)}",
        f"- Traveler rows with issues: {summary.get('traveler_issue_rows', 0)}",
        f"- Trip rows with issues: {summary.get('trip_issue_rows', 0)}",
        f"- Trips missing start date: {summary.get('trip_missing_start_date_rows', 0)}",
        f"- Trips missing end date: {summary.get('trip_missing_end_date_rows', 0)}",
        f"- Trips marked `Date TBD`: {summary.get('trip_sales_status_set_date_tbd', 0)}",
        f"- Trips marked `Date Fix Needed`: {summary.get('trip_sales_status_set_date_fix_needed', 0)}",
        f"- Interactions sheet ready: {summary.get('interactions_sheet_ready', 0)}",
        "",
        "## Status Breakdown",
        "",
    ]

    for key in sorted(status_breakdown):
        lines.append(f"- {key or '[blank]'}: {status_breakdown[key]}")

    lines.extend(
        [
            "",
            "## Remaining Manual Work",
            "",
            "- Fill real future `Start Date` and `End Date` values in `Trips` for rows marked `Date TBD` or `Date Fix Needed`.",
            "- Review duplicate phone rows before live CRM usage.",
            "- Review rows still marked with missing traveler IDs or missing WhatsApp data.",
            "- Confirm whether blank-status contacts should remain blank or be converted to `Active`.",
            "- Use `Integrated WhatsApp` plus `Phone Lookup Key` as the standard CRM lookup fields in Phase 1.",
            "",
            "## Audit Sample",
            "",
        ]
    )

    for issue in audit_rows[:25]:
        sheet, row_idx, key, name, issue_code, details = issue
        lines.append(f"- `{sheet}` row `{row_idx}`: `{issue_code}` for `{key}` `{name}` `{details}`")

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_phase0_cleanup(input_path: Path, output_path: Path, report_path: Path) -> dict[str, object]:
    wb = load_workbook(input_path)
    travelers_ws = wb[TRAVELERS_SHEET]
    trips_ws = wb[TRIPS_SHEET]

    traveler_header_map = ensure_headers(
        travelers_ws,
        header_row=1,
        expected_headers=TRAVELERS_HEADERS,
        helper_headers=TRAVELERS_HELPER_HEADERS,
    )
    trips_header_map = ensure_headers(
        trips_ws,
        header_row=2,
        expected_headers=["Trip ID", "Trip Name", "Type", "Year", "Start Date", "End Date"],
        helper_headers=TRIPS_HELPER_HEADERS,
    )

    # Normalize a single trailing-space header in-memory for easier lookup.
    trips_ws.cell(2, trips_header_map["Trip ID"]).value = "Trip ID"

    audit_rows: list[list[object]] = []
    summary: dict[str, object] = {}
    summary.update(build_travelers_audit(travelers_ws, traveler_header_map, audit_rows))
    summary.update(build_trips_audit(trips_ws, trips_header_map, audit_rows))
    apply_travelers_status_validation(travelers_ws, traveler_header_map)
    ensure_interactions_sheet(wb)
    summary["interactions_sheet_ready"] = 1

    replace_audit_sheet(wb, audit_rows, summary)
    wb.save(output_path)
    write_markdown_report(report_path, summary, audit_rows)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Phase 0 cleanup on the Rahma Traveler workbook.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()

    summary = run_phase0_cleanup(args.input, args.output, args.report)
    print(json.dumps(summary, indent=2, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
