from __future__ import annotations

from datetime import date
from typing import Any

from services.ai_agent.ai_agent_app.config import Settings
from services.ai_agent.ai_agent_app.system_bridge import get_system_service


class ReadOnlyCRMTools:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.service = get_system_service(settings)
        if self.service is None:
            raise RuntimeError("CRM service is unavailable.")

    @staticmethod
    def _normalize_text(value: str | None) -> str:
        return str(value or "").strip()

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        if row is None:
            return {}
        if isinstance(row, dict):
            return dict(row)
        try:
            return dict(row)
        except Exception:
            return {key: row[key] for key in row.keys()}

    def search_traveler(self, *, raw_phone: str, country_code: str = "") -> dict[str, Any]:
        resolution = self.service.resolve_identity("", raw_phone, country_code or self.settings.default_country_code)
        return {
            "lookup_phone": resolution.lookup_phone,
            "match_status": resolution.match_status,
            "handoff_required": resolution.handoff_required,
            "handoff_reason": resolution.handoff_reason,
            "name_match_status": resolution.name_match_status,
            "actions": list(resolution.actions),
            "traveler": resolution.traveler,
        }

    def search_traveler_by_phone(self, *, raw_phone: str, country_code: str = "") -> dict[str, Any]:
        return self.search_traveler(raw_phone=raw_phone, country_code=country_code)

    def get_traveler_profile(
        self,
        *,
        traveler_id: str = "",
        raw_phone: str = "",
        country_code: str = "",
    ) -> dict[str, Any]:
        service = self.service
        if traveler_id:
            with service.connect() as connection:
                row = connection.execute(
                    """
                    SELECT *
                    FROM travelers
                    WHERE traveler_id = ?
                    """,
                    (traveler_id,),
                ).fetchone()
            return {"traveler": self._row_to_dict(row), "lookup_mode": "traveler_id"}

        if raw_phone:
            search = self.search_traveler(raw_phone=raw_phone, country_code=country_code)
            traveler = search.get("traveler")
            return {"traveler": traveler, "lookup_mode": "phone", **search}

        return {"traveler": None, "lookup_mode": "empty"}

    def search_trips(self, *, trip_type: str = "", query: str = "") -> dict[str, Any]:
        trip_result = self.service.build_trip_result(trip_type or None, today=date.today())
        normalized_query = self._normalize_text(query).casefold()
        if normalized_query:
            for bucket in ("open_trips", "date_tbd_trips"):
                trip_result[bucket] = [
                    trip
                    for trip in trip_result[bucket]
                    if normalized_query in str(trip.get("trip_name", "")).casefold()
                    or normalized_query in str(trip.get("trip_id", "")).casefold()
                ]
        return {
            "trip_type": trip_type or "",
            "query": query or "",
            **trip_result,
            "trips": [*trip_result["open_trips"], *trip_result["date_tbd_trips"]],
        }

    def get_trip_details(self, *, trip_id: str) -> dict[str, Any]:
        if not trip_id:
            return {"trip": None}
        with self.service.connect() as connection:
            row = connection.execute("SELECT * FROM trips WHERE trip_id = ?", (trip_id,)).fetchone()
        return {"trip": self._row_to_dict(row)}

    def get_booking_status(
        self,
        *,
        booking_id: str = "",
        traveler_id: str = "",
        lead_id: str = "",
    ) -> dict[str, Any]:
        query = "SELECT * FROM trip_bookings WHERE 1=1"
        params: list[Any] = []
        if booking_id:
            query += " AND booking_id = ?"
            params.append(booking_id)
        if traveler_id:
            query += " AND traveler_id = ?"
            params.append(traveler_id)
        if lead_id:
            query += " AND lead_id = ?"
            params.append(lead_id)
        query += " ORDER BY draft_created_at DESC, booking_id DESC"
        with self.service.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return {"bookings": [self._row_to_dict(row) for row in rows]}

    def lookup_booking(
        self,
        *,
        booking_id: str = "",
        traveler_id: str = "",
        lead_id: str = "",
        ) -> dict[str, Any]:
        return self.get_booking_status(booking_id=booking_id, traveler_id=traveler_id, lead_id=lead_id)

    def lookup_lead(
        self,
        *,
        lead_id: str = "",
        traveler_id: str = "",
        raw_phone: str = "",
        country_code: str = "",
    ) -> dict[str, Any]:
        query = "SELECT * FROM leads WHERE 1=1"
        params: list[Any] = []
        if lead_id:
            query += " AND lead_id = ?"
            params.append(lead_id)
        if traveler_id:
            query += " AND traveler_id = ?"
            params.append(traveler_id)
        if raw_phone:
            phone = self.service.normalize_phone(raw_phone, country_code or self.settings.default_country_code)
            lookup_keys = self.service.lookup_key_variants(phone)
            phone_clauses = ["integrated_whatsapp = ?", "raw_phone = ?", "phone_lookup_key = ?"]
            params.extend([phone["normalized_whatsapp"], phone["local_number"] or raw_phone, phone["lookup_key"]])
            if lookup_keys:
                placeholders = ", ".join("?" for _ in lookup_keys)
                phone_clauses.append(f"phone_lookup_key IN ({placeholders})")
                params.extend(lookup_keys)
            query += " AND (" + " OR ".join(phone_clauses) + ")"
        query += " ORDER BY created_at DESC, lead_id DESC"
        with self.service.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return {"leads": [self._row_to_dict(row) for row in rows]}

    def get_passport_status(
        self,
        *,
        traveler_id: str = "",
        raw_phone: str = "",
        country_code: str = "",
    ) -> dict[str, Any]:
        traveler = None
        if traveler_id:
            with self.service.connect() as connection:
                traveler = connection.execute(
                    """
                    SELECT traveler_id, full_name, status, passport_name, passport_number,
                           passport_expiry, passport_nationality, passport_attachment_ref
                    FROM travelers
                    WHERE traveler_id = ?
                    """,
                    (traveler_id,),
                ).fetchone()
        elif raw_phone:
            resolved = self.search_traveler_by_phone(raw_phone=raw_phone, country_code=country_code)
            traveler = resolved.get("traveler")
        traveler_dict = self._row_to_dict(traveler)
        if not traveler_dict.get("traveler_id"):
            return {"traveler": None, "documents": [], "passport_required": False}

        with self.service.connect() as connection:
            documents = connection.execute(
                """
                SELECT document_id, file_name, category, file_ref, verification_status,
                       passport_full_name, passport_number, passport_nationality, passport_expiry, uploaded_at
                FROM traveler_documents
                WHERE traveler_id = ?
                ORDER BY document_id DESC
                """,
                (traveler_dict["traveler_id"],),
            ).fetchall()

        has_passport_fields = any(
            traveler_dict.get(field)
            for field in ("passport_name", "passport_number", "passport_expiry", "passport_nationality", "passport_attachment_ref")
        )
        return {
            "traveler": traveler_dict,
            "documents": [self._row_to_dict(row) for row in documents],
            "passport_required": False,
            "passport_missing": not has_passport_fields,
            "has_passport_data": has_passport_fields,
        }
