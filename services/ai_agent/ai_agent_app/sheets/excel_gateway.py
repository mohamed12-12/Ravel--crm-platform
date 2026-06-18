from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, is_zipfile

from openpyxl import load_workbook

from services.ai_agent.ai_agent_app.config import Settings
from services.ai_agent.ai_agent_app.logger import sheet_logger
from services.ai_agent.ai_agent_app.system_bridge import check_traveler_completed_trips, create_system_booking, get_system_trip_result, qualify_system_lead, run_system_sales_cycle
from scripts.phase1_readonly_agent import build_agent_response


class ExcelSheetGateway:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.source_path = settings.excel_source_workbook
        self.runtime_path = settings.excel_runtime_workbook
        self._lock = threading.RLock()  # RLock: reentrant so Drive subclass can lock > super()

    def ensure_runtime_workbook(self, reset: bool = False) -> None:
        with self._lock:
            runtime_invalid = self.runtime_path.exists() and not is_zipfile(self.runtime_path)
            if not self.runtime_path.exists() or reset or runtime_invalid:
                if runtime_invalid:
                    sheet_logger.warning(
                        "Runtime workbook was corrupted or not a valid .xlsx archive. Restoring from source."
                    )
                sheet_logger.info(f"Ensuring runtime workbook: resetting={reset or runtime_invalid}")
                shutil.copy2(self.source_path, self.runtime_path)

    def _load_runtime_workbook(self, **kwargs):
        self.ensure_runtime_workbook()
        try:
            return _load_workbook_with_retry(self.runtime_path, **kwargs)
        except BadZipFile:
            sheet_logger.warning("Runtime workbook load failed with BadZipFile. Rebuilding runtime workbook from source.")
            self.ensure_runtime_workbook(reset=True)
            return _load_workbook_with_retry(self.runtime_path, **kwargs)

    def reset_runtime_workbook(self) -> None:
        sheet_logger.warning("Resetting runtime workbook from source.")
        self.ensure_runtime_workbook(reset=True)

    def workbook_info(self) -> dict[str, str]:
        return {
            "sourceWorkbook": self.source_path.name,
            "runtimeWorkbook": self.runtime_path.name,
        }

    def get_message_copy(self, message_key: str, language: str = "en", fallback: str = "") -> str:
        wb = self._load_runtime_workbook(data_only=True, read_only=True)
        try:
            if "DM Copy Library" not in wb.sheetnames:
                return fallback
            ws = wb["DM Copy Library"]
            headers = {
                _normalize_text(ws.cell(1, col).value): col
                for col in range(1, ws.max_column + 1)
                if _normalize_text(ws.cell(1, col).value)
            }
            key_col = headers.get("Message Key")
            ar_col = headers.get("Arabic Copy")
            en_col = headers.get("English Copy")
            active_col = headers.get("Active")
            if not key_col or not ar_col or not en_col:
                return fallback
            for row_idx in range(2, ws.max_row + 1):
                if _normalize_text(ws.cell(row_idx, key_col).value) != message_key:
                    continue
                active = _normalize_text(ws.cell(row_idx, active_col).value) if active_col else ""
                if active and active.lower() in {"no", "false", "0", "inactive"}:
                    continue
                value = ws.cell(row_idx, ar_col).value if language.lower().startswith("ar") else ws.cell(row_idx, en_col).value
                if value in (None, ""):
                    value = ws.cell(row_idx, en_col).value or ws.cell(row_idx, ar_col).value
                text = _normalize_text(value)
                return text or fallback
            return fallback
        finally:
            wb.close()

    def preview_customer(
        self,
        full_name: str,
        raw_phone: str,
        trip_type: str | None = None,
        country_code: str = "",
    ) -> dict[str, Any]:
        trip_result_override = get_system_trip_result(trip_type, self.settings)
        return build_agent_response(
            workbook_path=self.runtime_path,
            full_name=full_name,
            raw_phone=raw_phone,
            trip_type=trip_type,
            country_code=country_code,
            trip_result_override=trip_result_override,
        )

    def run_sales_cycle(
        self,
        *,
        full_name: str,
        raw_phone: str,
        country_code: str,
        trip_type: str | None,
        channel: str,
        source: str,
        agent_notes: str,
        birthday: str = "",
        gender: str = "",
        nationality: str = "",
        preferred_trip_id: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            return run_system_sales_cycle(
                self.settings,
                full_name=full_name,
                raw_phone=raw_phone,
                country_code=country_code,
                trip_type=trip_type,
                channel=channel,
                source=source,
                agent_notes=agent_notes,
                birthday=birthday,
                gender=gender,
                nationality=nationality,
                preferred_trip_id=preferred_trip_id,
            )

    def create_booking(
        self,
        *,
        traveler_id: str,
        traveler_name: str,
        trip_id: str,
        room_type: str,
        channel: str,
        lead_id: str,
        source: str,
        agent_notes: str,
        flight_option: str = "",
        date_option: str = "",
        currency: str = "",
    ) -> dict[str, Any]:
        sheet_logger.info(f"Creating booking draft: traveler={traveler_id}, trip={trip_id}")
        with self._lock:
            return create_system_booking(
                self.settings,
                traveler_id=traveler_id,
                traveler_name=traveler_name,
                trip_id=trip_id,
                room_type=room_type,
                channel=channel,
                lead_id=lead_id,
                source=source,
                agent_notes=agent_notes,
                flight_option=flight_option,
                date_option=date_option,
                currency=currency,
            )

    def get_demo_stats(self) -> dict[str, Any]:
        wb = self._load_runtime_workbook(data_only=True, read_only=True)
        stats = get_demo_stats_from_wb(wb)
        wb.close()
        return stats

    def crm_preview(self, limit: int = 15) -> list[dict[str, Any]]:
        wb = self._load_runtime_workbook(data_only=True, read_only=True)
        rows = crm_preview_from_wb(wb, limit)
        wb.close()
        return rows

    def qualify_lead(self, lead_id: str) -> bool:
        with self._lock:
            return qualify_system_lead(self.settings, lead_id)

    def traveler_has_completed_trips(self, traveler_id: str) -> bool:
        """Return True if the traveler has at least one past completed booking.

        Used by the post-trip handoff logic in session_flow.py. Safe to call on
        any traveler_id; returns False on any error.
        """
        return check_traveler_completed_trips(self.settings, traveler_id)

    def get_visa_requirement(self, destination: str) -> dict[str, Any]:
        """Look up visa requirement from the 'Visa Requirements' sheet in the runtime workbook.

        Returns a dict with:
          - required (bool | None): True if visa is required, False if not, None if unknown
          - destination (str): normalized destination name found in the table
          - notes (str): any free-text notes from the table row
          - disclaimer (str): always present - must be shown to customer
          - source (str): 'table' if found, 'unknown' if not
        """
        DISCLAIMER = (
            "Visa requirements change frequently. Always verify with the official embassy "
            "or consulate of your destination country before travel. Rahma Travel is not "
            "responsible for visa denials or entry refusals."
        )
        if not destination or not destination.strip():
            return {"required": None, "destination": "", "notes": "", "disclaimer": DISCLAIMER, "source": "unknown"}

        dest_clean = destination.strip().lower()
        try:
            wb = self._load_runtime_workbook(data_only=True, read_only=True)
            try:
                if "Visa Requirements" not in wb.sheetnames:
                    return {"required": None, "destination": destination, "notes": "", "disclaimer": DISCLAIMER, "source": "unknown"}
                ws = wb["Visa Requirements"]
                # Expected columns: Destination | Visa Required (Yes/No) | Notes
                headers: dict[str, int] = {}
                for col in range(1, ws.max_column + 1):
                    val = _normalize_text(ws.cell(1, col).value)
                    if val:
                        headers[val.lower()] = col
                dest_col = headers.get("destination") or headers.get("country") or 1
                req_col = headers.get("visa required") or headers.get("required") or 2
                notes_col = headers.get("notes") or headers.get("note") or 3
                for row_idx in range(2, ws.max_row + 1):
                    row_dest = _normalize_text(ws.cell(row_idx, dest_col).value).lower()
                    if not row_dest:
                        continue
                    if dest_clean in row_dest or row_dest in dest_clean:
                        req_raw = _normalize_text(ws.cell(row_idx, req_col).value).lower()
                        required: bool | None = None
                        if req_raw in {"yes", "y", "true", "1", "required"}:
                            required = True
                        elif req_raw in {"no", "n", "false", "0", "not required"}:
                            required = False
                        notes = _normalize_text(ws.cell(row_idx, notes_col).value)
                        return {
                            "required": required,
                            "destination": _normalize_text(ws.cell(row_idx, dest_col).value),
                            "notes": notes,
                            "disclaimer": DISCLAIMER,
                            "source": "table",
                        }
                return {"required": None, "destination": destination, "notes": "", "disclaimer": DISCLAIMER, "source": "unknown"}
            finally:
                wb.close()
        except Exception as exc:
            sheet_logger.error(f"get_visa_requirement error for {destination!r}: {exc}")
            return {"required": None, "destination": destination, "notes": "", "disclaimer": DISCLAIMER, "source": "unknown"}

    def get_trip_discount_notes(self, trip_id: str) -> str:
        """Return the trip notes field for a given trip_id from the Trips sheet.

        This allows the agent to read discount or special offer notes that staff
        have written in the CRM trip record, without any hardcoded discount logic.
        Returns an empty string if no notes are found.
        """
        if not trip_id:
            return ""
        try:
            wb = self._load_runtime_workbook(data_only=True, read_only=True)
            try:
                if "Trips" not in wb.sheetnames:
                    return ""
                ws = wb["Trips"]
                headers: dict[str, int] = {}
                for col in range(1, ws.max_column + 1):
                    val = _normalize_text(ws.cell(2, col).value)  # row 2 is headers in Trips
                    if val:
                        headers[val.lower()] = col
                id_col = headers.get("trip id") or 1
                notes_col = headers.get("notes") or headers.get("trip notes") or headers.get("agent notes")
                if not notes_col:
                    return ""
                for row_idx in range(3, ws.max_row + 1):
                    row_id = _normalize_text(ws.cell(row_idx, id_col).value)
                    if row_id == trip_id:
                        return _normalize_text(ws.cell(row_idx, notes_col).value)
                return ""
            finally:
                wb.close()
        except Exception as exc:
            sheet_logger.error(f"get_trip_discount_notes error for trip {trip_id!r}: {exc}")
            return ""


def get_demo_stats_from_wb(wb: Any) -> dict[str, Any]:
    travelers_ws = wb["Travelers"]
    interactions_ws = wb["Interactions"]
    trips_ws = wb["Trips"]

    traveler_count = _count_non_empty_rows(
        travelers_ws,
        start_row=2,
        key_col=2,
        max_empty_streak=600,
    )
    interaction_count = _count_non_empty_rows(
        interactions_ws,
        start_row=2,
        key_col=1,
        max_empty_streak=600,
    )

    trip_status_counts: dict[str, int] = {}
    empty_streak = 0
    for row in trips_ws.iter_rows(
        min_row=3,
        max_row=trips_ws.max_row,
        min_col=1,
        max_col=26,
        values_only=True,
    ):
        key = row[0] if len(row) >= 1 else None
        status = row[25] if len(row) >= 26 else None
        if key in (None, ""):
            empty_streak += 1
            if empty_streak >= 600:
                break
            continue
        empty_streak = 0
        status_key = str(status or "None")
        trip_status_counts[status_key] = trip_status_counts.get(status_key, 0) + 1

    lead_stats = _compute_lead_stats(wb)
    booking_stats = _compute_booking_stats(wb)

    stats = {
        "travelerCount": traveler_count,
        "interactionCount": interaction_count,
        "tripStatusCounts": trip_status_counts,
    }
    stats.update(lead_stats)
    stats.update(booking_stats)
    return stats

def crm_preview_from_wb(wb: Any, limit: int = 15) -> list[dict[str, Any]]:
    ws = wb["Travelers"]
    rows: list[dict[str, Any]] = []
    count = 0
    for row_idx in range(2, ws.max_row + 1):
        traveler_id = ws.cell(row_idx, 2).value
        full_name = ws.cell(row_idx, 3).value
        if not traveler_id or not full_name:
            continue
        rows.append(
            {
                "id": str(traveler_id),
                "name": str(full_name),
                "status": str(ws.cell(row_idx, 1).value or "Active"),
                "phone": str(ws.cell(row_idx, 10).value or "N/A"),
            }
        )
        count += 1
        if count >= limit:
            break
    return rows

def _count_non_empty_rows(ws, *, start_row: int, key_col: int, max_empty_streak: int) -> int:
    count = 0
    empty_streak = 0
    for (value,) in ws.iter_rows(
        min_row=start_row,
        max_row=ws.max_row,
        min_col=key_col,
        max_col=key_col,
        values_only=True,
    ):
        if value in (None, ""):
            empty_streak += 1
            if empty_streak >= max_empty_streak:
                break
            continue
        empty_streak = 0
        count += 1
    return count


def _normalize_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _load_workbook_with_retry(path: Path, **kwargs):
    last_error: Exception | None = None
    for _ in range(5):
        try:
            return load_workbook(path, **kwargs)
        except (BadZipFile, OSError) as exc:
            last_error = exc
            time.sleep(0.15)
    if last_error:
        raise last_error
    return load_workbook(path, **kwargs)


def _compute_lead_stats(wb) -> dict[str, Any]:
    if "Leads" not in wb.sheetnames:
        return {
            "leadCount": 0,
            "leadStageCounts": {},
            "priorityCounts": {},
            "followUpSummary": {"dueToday": 0, "overdue": 0, "urgent": 0},
            "pipeline": {
                "newCustomers": 0,
                "existingTravelers": 0,
                "qualified": 0,
                "followUpNeeded": 0,
                "needsReview": 0,
                "blocked": 0,
                "bookingDrafts": 0,
                "qualificationRate": 0.0,
            },
            "recentLeads": [],
        }

    ws = wb["Leads"]
    headers: dict[str, int] = {}
    header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
    for col_idx, cell in enumerate(header_row, start=1):
        if cell not in (None, ""):
            headers[str(cell).strip()] = col_idx

    required = ["Lead ID", "Lead Stage", "Priority", "Updated At", "Match Status", "Customer Name", "Customer Tier", "Follow Up Status", "Follow Up Due Date"]
    if any(name not in headers for name in required):
        return {
            "leadCount": 0,
            "leadStageCounts": {},
            "priorityCounts": {},
            "followUpSummary": {"dueToday": 0, "overdue": 0, "urgent": 0},
            "pipeline": {
                "newCustomers": 0,
                "existingTravelers": 0,
                "qualified": 0,
                "followUpNeeded": 0,
                "needsReview": 0,
                "blocked": 0,
                "bookingDrafts": 0,
                "qualificationRate": 0.0,
            },
            "recentLeads": [],
        }

    lead_stage_counts: dict[str, int] = {}
    priority_counts: dict[str, int] = {}
    due_today = 0
    overdue = 0
    urgent = 0

    lead_count = 0
    new_customers = 0
    existing_travelers = 0
    qualified = 0
    follow_up_needed = 0
    needs_review = 0
    blocked = 0
    booking_drafts = 0
    recent_rows: list[dict[str, Any]] = []

    empty_streak = 0
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, values_only=True):
        lead_id = _row_value(row, headers, "Lead ID")
        if not lead_id:
            empty_streak += 1
            if empty_streak >= 600:
                break
            continue
        empty_streak = 0
        lead_count += 1

        stage = _row_value(row, headers, "Lead Stage")
        priority = _row_value(row, headers, "Priority")
        follow_up_status = _row_value(row, headers, "Follow Up Status")
        due_date = _row_value(row, headers, "Follow Up Due Date")
        updated_at = _row_value(row, headers, "Updated At")
        match_status = _row_value(row, headers, "Match Status")
        customer_name = _row_value(row, headers, "Customer Name")
        customer_tier = _row_value(row, headers, "Customer Tier")

        lead_stage_counts[stage] = lead_stage_counts.get(stage, 0) + 1
        priority_counts[priority] = priority_counts.get(priority, 0) + 1

        if stage in {"Qualified", "VIP Priority", "Repeat Priority"}:
            qualified += 1
        if stage in {"Follow Up Needed", "VIP Follow Up", "Repeat Follow Up"}:
            follow_up_needed += 1
        if stage == "Needs Review":
            needs_review += 1
        if stage == "Blocked":
            blocked += 1
        if stage in {"Booking Draft Created", "VIP Booking Draft", "Repeat Booking Draft"}:
            booking_drafts += 1

        if match_status == "not_found":
            new_customers += 1
        if match_status == "single_match":
            existing_travelers += 1

        if follow_up_status == "Urgent":
            urgent += 1
        if due_date:
            due_date_text = due_date
            if isinstance(due_date, str):
                due_date_text = due_date
            else:
                due_date_text = str(due_date)
            try:
                due_obj = due_date_text.split("T", 1)[0]
                from datetime import date

                parsed = date.fromisoformat(due_obj)
                today = date.today()
                if parsed == today:
                    due_today += 1
                elif parsed < today:
                    overdue += 1
            except ValueError:
                pass

        recent_rows.append(
            {
                "leadId": lead_id,
                "customerName": customer_name,
                "leadStage": stage,
                "priority": priority,
                "customerTier": customer_tier,
                "updatedAt": updated_at,
                "followUpStatus": follow_up_status,
            }
        )

    recent_rows.sort(key=lambda item: item["updatedAt"], reverse=True)
    qualification_rate = round((qualified / lead_count) * 100, 1) if lead_count else 0.0

    return {
        "leadCount": lead_count,
        "leadStageCounts": lead_stage_counts,
        "priorityCounts": priority_counts,
        "followUpSummary": {"dueToday": due_today, "overdue": overdue, "urgent": urgent},
        "pipeline": {
            "newCustomers": new_customers,
            "existingTravelers": existing_travelers,
            "qualified": qualified,
            "followUpNeeded": follow_up_needed,
            "needsReview": needs_review,
            "blocked": blocked,
            "bookingDrafts": booking_drafts,
            "qualificationRate": qualification_rate,
        },
        "recentLeads": recent_rows[:5],
    }


def _compute_booking_stats(wb) -> dict[str, int]:
    booking_draft_count = 0
    payment_pending_count = 0
    booking_alert_count = 0

    if "Trip Bookings" in wb.sheetnames:
        ws = wb["Trip Bookings"]
        headers: dict[str, int] = {}
        header_row = next(ws.iter_rows(min_row=2, max_row=2, values_only=True), ())
        for col_idx, cell in enumerate(header_row, start=1):
            if cell not in (None, ""):
                headers[str(cell).strip()] = col_idx

        status_idx = headers.get("Booking Status")
        payment_idx = headers.get("Payment Status")

        empty_streak = 0
        for row in ws.iter_rows(min_row=3, max_row=ws.max_row, values_only=True):
            trip_id = row[1] if len(row) >= 2 else None
            if trip_id in (None, ""):
                empty_streak += 1
                if empty_streak >= 600:
                    break
                continue
            empty_streak = 0
            if status_idx and len(row) >= status_idx and str(row[status_idx - 1] or "").strip() == "Draft":
                booking_draft_count += 1
            if payment_idx and len(row) >= payment_idx and str(row[payment_idx - 1] or "").strip() == "Awaiting Deposit":
                payment_pending_count += 1

    if "Booking Alerts" in wb.sheetnames:
        ws = wb["Booking Alerts"]
        empty_streak = 0
        for (alert_id,) in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=1, max_col=1, values_only=True):
            if alert_id in (None, ""):
                empty_streak += 1
                if empty_streak >= 600:
                    break
                continue
            empty_streak = 0
            booking_alert_count += 1

    return {
        "bookingDraftCount": booking_draft_count,
        "paymentPendingCount": payment_pending_count,
        "bookingAlertCount": booking_alert_count,
    }


def _row_value(row: tuple[Any, ...], headers: dict[str, int], key: str) -> str:
    idx = headers[key] - 1
    if idx < 0 or idx >= len(row):
        return ""
    value = row[idx]
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()
