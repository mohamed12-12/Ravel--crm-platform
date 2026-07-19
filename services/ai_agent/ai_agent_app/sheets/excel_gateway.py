from __future__ import annotations

import re
import shutil
import threading
import time
import sqlite3
from pathlib import Path
from typing import Any
from urllib import parse, request
from zipfile import BadZipFile, is_zipfile

from openpyxl import load_workbook

from services.ai_agent.ai_agent_app.config import Settings
from services.ai_agent.ai_agent_app.logger import sheet_logger
from services.ai_agent.ai_agent_app.system_bridge import check_traveler_completed_trips, create_system_booking, create_system_handoff, get_system_preview_result, get_system_trip_result, qualify_system_lead, run_system_sales_cycle, save_system_traveler_passport, sync_system_live_agent_lead
from services.crm.system_services.config import resolve_system_db_path
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
        system_preview = get_system_preview_result(full_name, raw_phone, trip_type, country_code, self.settings)
        if system_preview is not None:
            return system_preview
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
        passport_required: bool = False,
        passport_status: str = "",
        group_size: int | str = 1,
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
                passport_required=passport_required,
                passport_status=passport_status,
                group_size=group_size,
            )

    def get_demo_stats(self) -> dict[str, Any]:
        db_stats = self._get_demo_stats_from_db()
        if db_stats is not None:
            return db_stats
        wb = self._load_runtime_workbook(data_only=True, read_only=True)
        stats = get_demo_stats_from_wb(wb)
        wb.close()
        return stats

    def crm_preview(self, limit: int = 15) -> list[dict[str, Any]]:
        db_rows = self._crm_preview_from_db(limit)
        if db_rows is not None:
            return db_rows
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

    def save_traveler_passport(
        self,
        traveler_id: str,
        *,
        passport_name: str = "",
        passport_number: str = "",
        passport_expiry: str = "",
        passport_nationality: str = "",
        passport_attachment_ref: str = "",
        uploaded_by: str = "ai-agent",
        attachment_file_name: str = "",
        attachment_original_name: str = "",
        attachment_mime_type: str = "",
        attachment_size: int | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            try:
                return save_system_traveler_passport(
                    self.settings,
                    traveler_id,
                    passport_name=passport_name,
                    passport_number=passport_number,
                    passport_expiry=passport_expiry,
                    passport_nationality=passport_nationality,
                    passport_attachment_ref=passport_attachment_ref,
                    uploaded_by=uploaded_by,
                    attachment_file_name=attachment_file_name,
                    attachment_original_name=attachment_original_name,
                    attachment_mime_type=attachment_mime_type,
                    attachment_size=attachment_size,
                    notes=notes,
                )
            except Exception as exc:
                sheet_logger.warning(
                    "Traveler passport persistence failed for %s: %s",
                    traveler_id,
                    exc,
                )
                return {}

    def sync_live_agent_lead(self, lead_id: str, **payload: Any) -> dict[str, Any]:
        with self._lock:
            return sync_system_live_agent_lead(self.settings, lead_id, **payload)

    def create_handoff_case(self, **payload: Any) -> dict[str, Any]:
        with self._lock:
            return create_system_handoff(self.settings, **payload)

    def get_visa_requirement(self, destination: str, nationality: str = "") -> dict[str, Any]:
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
        nationality = str(nationality or "").strip()
        if not destination or not destination.strip():
            return {
                "required": None,
                "destination": "",
                "nationality": nationality,
                "notes": "",
                "summary": "",
                "disclaimer": DISCLAIMER,
                "source": "unknown",
                "source_mode": "unknown",
                "sources": [],
            }

        dest_clean = destination.strip().lower()
        try:
            wb = self._load_runtime_workbook(data_only=True, read_only=True)
            try:
                if "Visa Requirements" not in wb.sheetnames:
                    table_result = None
                else:
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
                    table_result = None
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
                            table_result = {
                                "required": required,
                                "destination": _normalize_text(ws.cell(row_idx, dest_col).value),
                                "nationality": nationality,
                                "notes": notes,
                                "summary": notes or ("Visa required." if required else "Visa not required." if required is False else ""),
                                "disclaimer": DISCLAIMER,
                                "source": "table",
                                "source_mode": "internal",
                                "sources": [],
                            }
                            break
                if table_result:
                    return table_result
            finally:
                wb.close()
        except Exception as exc:
            sheet_logger.error(f"get_visa_requirement error for {destination!r}: {exc}")
            return {
                "required": None,
                "destination": destination,
                "nationality": nationality,
                "notes": "",
                "summary": "",
                "disclaimer": DISCLAIMER,
                "source": "unknown",
                "source_mode": "unknown",
                "sources": [],
            }

        if not nationality:
            return {
                "required": None,
                "destination": destination,
                "nationality": "",
                "notes": "",
                "summary": "I need the traveler's nationality or passport country to check visa rules accurately.",
                "disclaimer": DISCLAIMER,
                "source": "unknown",
                "source_mode": "unknown",
                "sources": [],
                "needs_nationality": True,
            }

        web_result = self._lookup_visa_requirement_on_web(destination, nationality, disclaimer=DISCLAIMER)
        if web_result:
            return web_result
        return {
            "required": None,
            "destination": destination,
            "nationality": nationality,
            "notes": "",
            "summary": "I could not verify a reliable visa requirement from the available sources.",
            "disclaimer": DISCLAIMER,
            "source": "unknown",
            "source_mode": "unknown",
            "sources": [],
        }

    def _lookup_visa_requirement_on_web(self, destination: str, nationality: str, *, disclaimer: str) -> dict[str, Any] | None:
        search_url = self._build_visa_search_url(destination, nationality)
        if not search_url:
            return None
        for url in self._extract_candidate_visa_links(self._fetch_url_text(search_url)):
            page_text = self._fetch_url_text(url)
            parsed = self._parse_visa_requirement_from_text(page_text)
            if not parsed:
                continue
            return {
                "required": parsed["required"],
                "destination": destination,
                "nationality": nationality,
                "notes": parsed["summary"],
                "summary": parsed["summary"],
                "disclaimer": disclaimer,
                "source": "web",
                "source_mode": "web",
                "sources": [{"url": url, "label": self._source_label(url)}],
            }
        return None

    @staticmethod
    def _build_visa_search_url(destination: str, nationality: str) -> str:
        query = f"official visa requirements {nationality} passport {destination}"
        return "https://duckduckgo.com/html/?" + parse.urlencode({"q": query})

    @staticmethod
    def _fetch_url_text(url: str) -> str:
        req = request.Request(url, headers={"User-Agent": "Mozilla/5.0 RahmaTravelerVisaLookup/1.0"})
        with request.urlopen(req, timeout=6) as response:
            return response.read().decode("utf-8", errors="replace")

    @staticmethod
    def _extract_candidate_visa_links(html: str) -> list[str]:
        if not html:
            return []
        links = re.findall(r'href="(https?://[^"]+)"', html, flags=re.IGNORECASE)
        official = []
        for link in links:
            lowered = link.lower()
            if any(bit in lowered for bit in (".gov", ".gc.ca", ".gob.", "embassy", "consulate", "gov.uk", "state.gov")):
                official.append(link)
        return list(dict.fromkeys(official))[:5]

    @staticmethod
    def _parse_visa_requirement_from_text(text: str) -> dict[str, Any] | None:
        normalized = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()
        lowered = normalized.lower()
        for required, phrases in (
            (False, ("visa-free", "no visa required", "visa not required", "visa on arrival")),
            (True, ("visa required", "must obtain a visa", "need a visa")),
        ):
            for phrase in phrases:
                index = lowered.find(phrase)
                if index >= 0:
                    snippet = normalized[max(0, index - 80): index + len(phrase) + 120].strip()
                    return {"required": required, "summary": snippet}
        return None

    @staticmethod
    def _source_label(url: str) -> str:
        parsed = parse.urlparse(url)
        return parsed.netloc or url

    def get_trip_discount_notes(self, trip_id: str) -> str:
        """Return the trip notes field for a given trip_id from the Trips sheet.

        This allows the agent to read discount or special offer notes that staff
        have written in the CRM trip record, without any hardcoded discount logic.
        Returns an empty string if no notes are found.
        """
        if not trip_id:
            return ""
        try:
            from services.crm.system_services import UnifiedCRMService

            commercial_context = UnifiedCRMService.resolve_commercial_context(
                traveler=None,
                trip_type=None,
                requested_group_size=1,
                trip_id=trip_id,
            )
            note_parts: list[str] = []
            vip_offer = str((commercial_context.get("vip") or {}).get("offer_text") or "").strip()
            group_offer = str((commercial_context.get("group") or {}).get("offer_text") or "").strip()
            if vip_offer:
                note_parts.append(f"VIP discount: {vip_offer}")
            if group_offer:
                threshold = int((commercial_context.get("group") or {}).get("threshold") or 0)
                if threshold > 0:
                    note_parts.append(f"Group discount for {threshold}+ travelers: {group_offer}")
                else:
                    note_parts.append(f"Group discount: {group_offer}")
            if note_parts:
                return " | ".join(note_parts)
        except Exception:
            pass
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

    def _get_demo_stats_from_db(self) -> dict[str, Any] | None:
        db_path = resolve_system_db_path()
        if not db_path.exists():
            return None
        try:
            connection = sqlite3.connect(db_path)
            connection.row_factory = sqlite3.Row
            cursor = connection.cursor()

            traveler_count = cursor.execute("SELECT COUNT(*) FROM travelers").fetchone()[0]
            interaction_count = cursor.execute("SELECT COUNT(*) FROM interactions").fetchone()[0] if self._table_exists(cursor, "interactions") else 0
            lead_count = cursor.execute("SELECT COUNT(*) FROM leads").fetchone()[0] if self._table_exists(cursor, "leads") else 0
            booking_draft_count = cursor.execute("SELECT COUNT(*) FROM trip_bookings").fetchone()[0] if self._table_exists(cursor, "trip_bookings") else 0
            payment_pending_count = cursor.execute("SELECT COUNT(*) FROM trip_bookings WHERE booking_status = 'Payment Pending'").fetchone()[0] if self._table_exists(cursor, "trip_bookings") else 0
            booking_alert_count = cursor.execute("SELECT COUNT(*) FROM handoff_queue").fetchone()[0] if self._table_exists(cursor, "handoff_queue") else 0
            trip_status_counts = {}
            if self._table_exists(cursor, "trips"):
                for status, count in cursor.execute("SELECT COALESCE(sales_status, 'None') AS status, COUNT(*) FROM trips GROUP BY COALESCE(sales_status, 'None')"):
                    trip_status_counts[str(status)] = int(count)
            lead_stage_counts = {}
            if self._table_exists(cursor, "leads"):
                for stage, count in cursor.execute("SELECT COALESCE(lead_stage, 'Blank') AS stage, COUNT(*) FROM leads GROUP BY COALESCE(lead_stage, 'Blank')"):
                    lead_stage_counts[str(stage)] = int(count)
            recent_leads = []
            if self._table_exists(cursor, "leads"):
                for row in cursor.execute(
                    "SELECT lead_id, customer_name, lead_stage FROM leads ORDER BY created_at DESC LIMIT 8"
                ):
                    recent_leads.append(
                        {
                            "leadId": row["lead_id"],
                            "customerName": row["customer_name"],
                            "leadStage": row["lead_stage"],
                        }
                    )
            return {
                "travelerCount": int(traveler_count),
                "interactionCount": int(interaction_count),
                "tripStatusCounts": trip_status_counts,
                "leadCount": int(lead_count),
                "leadStageCounts": lead_stage_counts,
                "recentLeads": recent_leads,
                "bookingDraftCount": int(booking_draft_count),
                "paymentPendingCount": int(payment_pending_count),
                "bookingAlertCount": int(booking_alert_count),
                "qualificationRate": 0,
                "followUpSummary": {"urgent": 0, "dueToday": 0},
                "dbSource": str(db_path),
            }
        except Exception as exc:
            sheet_logger.warning(f"CRM DB stats unavailable; falling back to workbook stats: {exc}")
            return None
        finally:
            try:
                connection.close()
            except Exception:
                pass

    def _crm_preview_from_db(self, limit: int) -> list[dict[str, Any]] | None:
        db_path = resolve_system_db_path()
        if not db_path.exists():
            return None
        try:
            connection = sqlite3.connect(db_path)
            connection.row_factory = sqlite3.Row
            cursor = connection.cursor()
            if not self._table_exists(cursor, "travelers"):
                return None
            rows = []
            for row in cursor.execute(
                "SELECT traveler_id, full_name, status, COALESCE(integrated_whatsapp, whatsapp_raw, '') AS phone "
                "FROM travelers WHERE traveler_id IS NOT NULL ORDER BY traveler_id LIMIT ?",
                (limit,),
            ):
                rows.append(
                    {
                        "id": str(row["traveler_id"]),
                        "name": str(row["full_name"] or ""),
                        "status": str(row["status"] or "Active"),
                        "phone": str(row["phone"] or "N/A"),
                    }
                )
            return rows
        except Exception as exc:
            sheet_logger.warning(f"CRM DB preview unavailable; falling back to workbook preview: {exc}")
            return None
        finally:
            try:
                connection.close()
            except Exception:
                pass

    @staticmethod
    def _table_exists(cursor: sqlite3.Cursor, table_name: str) -> bool:
        row = cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone()
        return row is not None


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
