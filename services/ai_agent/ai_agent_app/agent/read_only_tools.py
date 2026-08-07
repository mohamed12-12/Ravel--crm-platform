from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any
from urllib.parse import urljoin

from services.ai_agent.ai_agent_app.config import Settings
from services.ai_agent.ai_agent_app.agent.crm_api_client import CRMApiClient
from services.ai_agent.ai_agent_app.system_bridge import get_system_service


class ReadOnlyCRMTools:
    # crm_access_mode picks how every read/write in this class actually
    # reaches the CRM: "shared_service" calls get_system_service()'s
    # UnifiedCRMService in-process against SQLite; "api" instead calls the
    # CRM Flask app's /api/crm/agent/read and /agent/write over HTTP
    # (crm_api_client.py), which on that side dispatches to either
    # UnifiedCRMService or apps/api's PostgresAgentBridgeService depending
    # on ITS configured database -- see services/ai_agent/README.md and
    # services/crm/README.md for the full picture. self.service and
    # self.api_client are mutually exclusive in practice: an explicit
    # `service` (tests, or a caller that already resolved one) always wins,
    # and only when none is given does crm_access_mode decide which path to
    # build.
    def __init__(self, settings: Settings, *, service=None, api_client: CRMApiClient | None = None) -> None:
        self.settings = settings
        self.api_client = api_client
        access_mode = str(getattr(settings, "crm_access_mode", "shared_service") or "shared_service").strip().lower()
        if self.api_client is None and access_mode == "api":
            self.api_client = CRMApiClient(
                base_url=getattr(settings, "crm_api_base_url", ""),
                token=getattr(settings, "crm_api_token", ""),
            )
        self.service = service if service is not None else (None if self.api_client else get_system_service(settings))
        if self.service is None and self.api_client is None:
            raise RuntimeError("CRM service is unavailable.")

    def _api_read(self, action: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        if self.api_client is None:
            return None
        return self.api_client.read(action, payload)

    @staticmethod
    def _normalize_text(value: str | None) -> str:
        return str(value or "").strip()

    @classmethod
    def _trip_matches_query(cls, trip: dict[str, Any], query: str) -> bool:
        normalized_query = cls._normalize_text(query).casefold()
        if not normalized_query:
            return True
        haystack = " ".join(
            str(trip.get(key) or "")
            for key in (
                "trip_id",
                "trip_name",
                "destination",
                "country",
                "city",
                "location",
                "public_description",
                "description",
                "program",
                "notes",
            )
        ).casefold()
        stop_words = {
            "a",
            "an",
            "country",
            "destination",
            "for",
            "from",
            "go",
            "in",
            "interested",
            "me",
            "need",
            "please",
            "show",
            "the",
            "to",
            "travel",
            "trip",
            "trips",
            "visit",
            "visiting",
            "want",
            "we",
        }
        tokens = [token for token in normalized_query.split() if token and token not in stop_words]
        if not tokens:
            return True
        return all(token in haystack for token in tokens)

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

    @staticmethod
    def _field_state(value: Any) -> dict[str, Any]:
        if value is None:
            return {"value": None, "state": "null"}
        if value == "":
            return {"value": "", "state": "missing"}
        return {"value": value, "state": "present"}

    @staticmethod
    def _safe_traveler_fields(traveler: dict[str, Any] | None) -> dict[str, Any]:
        traveler = dict(traveler or {})
        return {
            "traveler_id": traveler.get("traveler_id") or "",
            "full_name": traveler.get("full_name") or "",
            "status": traveler.get("status") or "",
            "raw_phone": traveler.get("raw_phone") or traveler.get("whatsapp_raw") or "",
            "integrated_whatsapp": traveler.get("integrated_whatsapp") or "",
            "normalized_whatsapp": traveler.get("normalized_whatsapp") or "",
        }

    def _absolute_media_url(self, public_url: str) -> str:
        value = str(public_url or "").strip()
        if not value:
            return ""
        if value.startswith("http://") or value.startswith("https://"):
            return value
        base_url = str(getattr(self.settings, "crm_api_base_url", "") or "").strip()
        if not base_url:
            return value
        return urljoin(base_url.rstrip("/") + "/", value.lstrip("/"))

    def search_traveler(self, *, raw_phone: str, country_code: str = "") -> dict[str, Any]:
        remote = self._api_read("search_traveler", {"raw_phone": raw_phone, "country_code": country_code})
        if remote is not None:
            return remote
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

    def find_traveler_by_phone(self, *, raw_phone: str, country_code: str = "") -> dict[str, Any]:
        remote = self._api_read("find_traveler_by_phone", {"raw_phone": raw_phone, "country_code": country_code})
        if remote is not None:
            return remote
        try:
            resolution = self.service.resolve_identity("", raw_phone, country_code or self.settings.default_country_code)
            traveler = resolution.traveler if isinstance(resolution.traveler, dict) else None
            if resolution.match_status == "not_found":
                return {
                    "status": "not_found",
                    "traveler": None,
                    "warnings": [],
                }
            if resolution.match_status == "multiple_matches":
                return {
                    "status": "duplicate",
                    "traveler": None,
                    "warnings": [resolution.handoff_reason or "duplicate_phone_match"],
                }
            safe = self._safe_traveler_fields(traveler)
            required_missing = [key for key in ("traveler_id", "full_name", "status") if not safe.get(key)]
            if required_missing:
                return {
                    "status": "incomplete",
                    "traveler": safe,
                    "warnings": [f"Missing fields: {', '.join(required_missing)}"],
                }
            return {
                "status": "found",
                "traveler": safe,
                "warnings": list(resolution.actions or []),
            }
        except Exception as exc:
            return {
                "status": "error",
                "traveler": None,
                "warnings": [str(exc)],
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
        remote = self._api_read(
            "get_traveler_profile",
            {"traveler_id": traveler_id, "raw_phone": raw_phone, "country_code": country_code},
        )
        if remote is not None:
            return remote
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

    def get_traveler_profile_safe(
        self,
        *,
        traveler_id: str = "",
        raw_phone: str = "",
        country_code: str = "",
    ) -> dict[str, Any]:
        remote = self._api_read(
            "get_traveler_profile_safe",
            {"traveler_id": traveler_id, "raw_phone": raw_phone, "country_code": country_code},
        )
        if remote is not None:
            return remote
        profile = self.get_traveler_profile(traveler_id=traveler_id, raw_phone=raw_phone, country_code=country_code)
        traveler = profile.get("traveler") if isinstance(profile.get("traveler"), dict) else {}
        fields = {
            "traveler_id": self._field_state(traveler.get("traveler_id")),
            "full_name": self._field_state(traveler.get("full_name")),
            "status": self._field_state(traveler.get("status")),
            "local_trips_count": self._field_state(traveler.get("local_trips_count")),
            "international_trips_count": self._field_state(traveler.get("international_trips_count")),
            "total_trips": self._field_state(traveler.get("total_trips")),
            "passport_name": self._field_state(traveler.get("passport_name")),
            "passport_number": self._field_state(traveler.get("passport_number")),
            "passport_expiry": self._field_state(traveler.get("passport_expiry")),
            "passport_nationality": self._field_state(traveler.get("passport_nationality")),
            "passport_attachment_ref": self._field_state(traveler.get("passport_attachment_ref")),
        }
        profile["field_states"] = fields
        profile["traveler"] = self._safe_traveler_fields(traveler)
        return profile

    def get_traveler_trip_history(
        self,
        *,
        traveler_id: str = "",
        raw_phone: str = "",
        country_code: str = "",
    ) -> dict[str, Any]:
        remote = self._api_read(
            "get_traveler_trip_history",
            {"traveler_id": traveler_id, "raw_phone": raw_phone, "country_code": country_code},
        )
        if remote is not None:
            return remote
        profile = self.get_traveler_profile_safe(traveler_id=traveler_id, raw_phone=raw_phone, country_code=country_code)
        traveler = profile.get("traveler") if isinstance(profile.get("traveler"), dict) else {}
        traveler_id = str(traveler.get("traveler_id") or traveler_id or "").strip()
        if not traveler_id:
            return {"status": "not_found", "summary": {}, "history": []}
        summary = {
            "local_trips": 0,
            "international_trips": 0,
            "total_trips": 0,
            "source": "computed",
        }
        history: list[dict[str, Any]] = []
        try:
            with self.service.connect() as connection:
                rows = connection.execute(
                    """
                    SELECT b.booking_id, b.trip_id, b.booking_status, b.draft_created_at,
                           t.trip_name, t.type, t.start_date, t.end_date
                    FROM trip_bookings b
                    LEFT JOIN trips t ON TRIM(b.trip_id) = TRIM(t.trip_id)
                    WHERE TRIM(b.traveler_id) = ?
                    ORDER BY COALESCE(b.draft_created_at, '') DESC, b.booking_id DESC
                    """,
                    (traveler_id,),
                ).fetchall()
            for row in rows:
                item = self._row_to_dict(row)
                history.append(
                    {
                        "booking_id": item.get("booking_id") or "",
                        "trip_id": item.get("trip_id") or "",
                        "trip_name": item.get("trip_name") or "",
                        "trip_type": item.get("type") or "",
                        "booking_status": item.get("booking_status") or "",
                        "start_date": item.get("start_date") or None,
                        "end_date": item.get("end_date") or None,
                    }
                )
            local = sum(1 for item in history if str(item.get("trip_type") or "").strip().lower() == "local" and str(item.get("booking_status") or "").strip().lower() != "cancelled")
            intl = sum(1 for item in history if str(item.get("trip_type") or "").strip().lower() == "international" and str(item.get("booking_status") or "").strip().lower() != "cancelled")
            summary.update({"local_trips": local, "international_trips": intl, "total_trips": local + intl})
        except Exception as exc:
            return {"status": "error", "summary": summary, "history": history, "warnings": [str(exc)]}
        return {"status": "found", "summary": summary, "history": history, "traveler": traveler}

    def search_trips(self, *, trip_type: str = "", query: str = "") -> dict[str, Any]:
        remote = self._api_read("search_trips", {"trip_type": trip_type, "query": query})
        if remote is not None:
            return remote
        trip_result = self.service.build_trip_result(trip_type or None, today=date.today())
        normalized_query = self._normalize_text(query).casefold()
        if normalized_query:
            for bucket in ("open_trips", "date_tbd_trips"):
                trip_result[bucket] = [
                    trip
                    for trip in trip_result[bucket]
                    if self._trip_matches_query(trip, normalized_query)
                ]
        return {
            "trip_type": trip_type or "",
            "query": query or "",
            **trip_result,
            "trips": [*trip_result["open_trips"], *trip_result["date_tbd_trips"]],
        }

    def search_available_trips(
        self,
        *,
        trip_type: str = "",
        destination: str = "",
        query: str = "",
        preferred_date: str = "",
        travelers: str = "",
        flight_option: str = "",
        room_type: str = "",
    ) -> dict[str, Any]:
        remote = self._api_read(
            "search_available_trips",
            {
                "trip_type": trip_type,
                "destination": destination,
                "query": query,
                "preferred_date": preferred_date,
                "travelers": travelers,
                "flight_option": flight_option,
                "room_type": room_type,
            },
        )
        if remote is not None:
            return remote
        result = self.search_trips(trip_type=trip_type, query=query or destination)
        trips = list(result.get("trips") or [])
        normalized_destination = str(destination or "").strip().casefold()
        normalized_query = str(query or "").strip().casefold()
        normalized_date = str(preferred_date or "").strip()
        normalized_room = str(room_type or "").strip().casefold()
        if normalized_destination:
            trips = [
                trip
                for trip in trips
                if self._trip_matches_query(trip, normalized_destination)
            ]
        if normalized_query and normalized_query != normalized_destination:
            trips = [
                trip
                for trip in trips
                if self._trip_matches_query(trip, normalized_query)
            ]
        if normalized_date:
            trips = [
                trip
                for trip in trips
                if str(trip.get("start_date") or "").startswith(normalized_date[:10])
                or str(trip.get("end_date") or "").startswith(normalized_date[:10])
            ]
        if normalized_room:
            key = f"available_{normalized_room}".replace(" ", "_")
            trips = [trip for trip in trips if trip.get(key) is None or int(trip.get(key) or 0) > 0]
        return {
            "status": "found" if trips else "not_found",
            "trip_type": trip_type or "",
            "destination": destination or "",
            "preferred_date": preferred_date or "",
            "travelers": travelers or "",
            "flight_option": flight_option or "",
            "room_type": room_type or "",
            "trips": trips,
            "open_trips": [trip for trip in trips if trip in result.get("open_trips", [])],
            "date_tbd_trips": [trip for trip in trips if trip in result.get("date_tbd_trips", [])],
        }

    def get_trip_details(self, *, trip_id: str) -> dict[str, Any]:
        remote = self._api_read("get_trip_details", {"trip_id": trip_id})
        if remote is not None:
            return remote
        if not trip_id:
            return {"trip": None}
        with self.service.connect() as connection:
            row = connection.execute("SELECT * FROM trips WHERE trip_id = ?", (trip_id,)).fetchone()
        trip = self._row_to_dict(row)
        if not trip:
            return {"trip": None, "status": "not_found"}
        return {"trip": trip, "status": "found"}

    def get_trip_media(self, *, trip_id: str) -> dict[str, Any]:
        remote = self._api_read("get_trip_media", {"trip_id": trip_id})
        if remote is not None:
            return remote
        trip_id = self._normalize_text(trip_id)
        if not trip_id:
            return {
                "status": "missing_trip",
                "trip_id": "",
                "media": [],
                "message": "A trip must be selected before sharing official trip images.",
            }
        rows = []
        try:
            with self.service.connect() as connection:
                rows = connection.execute(
                    """
                    SELECT media_id, public_id, trip_id, public_url, image_type, alt_text,
                           display_order, mime_type, created_at
                    FROM trip_media
                    WHERE trip_id = ?
                      AND is_active = 1
                      AND verification_status = 'verified'
                    ORDER BY CASE WHEN image_type = 'cover' THEN 0 ELSE 1 END,
                             display_order ASC,
                             media_id ASC
                    """,
                    (trip_id,),
                ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        media = []
        for row in rows:
            item = self._row_to_dict(row)
            public_url = self._absolute_media_url(item.get("public_url") or "")
            if not public_url:
                continue
            media.append(
                {
                    "media_id": item.get("media_id"),
                    "public_id": item.get("public_id") or "",
                    "trip_id": item.get("trip_id") or trip_id,
                    "url": public_url,
                    "public_url": public_url,
                    "image_type": item.get("image_type") or "gallery",
                    "alt_text": item.get("alt_text") or "Official trip image",
                    "display_order": item.get("display_order") or 0,
                    "mime_type": item.get("mime_type") or "",
                    "created_at": item.get("created_at"),
                    "source": "rahma_crm_verified_trip_media",
                }
            )
        if not media:
            return {
                "status": "not_found",
                "trip_id": trip_id,
                "cover": None,
                "gallery": [],
                "media": [],
                "message": "No verified CRM trip image is available for this trip.",
            }
        cover = next((item for item in media if item.get("image_type") == "cover"), media[0])
        return {
            "status": "found",
            "trip_id": trip_id,
            "cover": cover,
            "gallery": media,
            "media": media,
            "message": "Verified CRM trip media retrieved.",
        }

    def get_booking_status(
        self,
        *,
        booking_id: str = "",
        traveler_id: str = "",
        lead_id: str = "",
    ) -> dict[str, Any]:
        remote = self._api_read(
            "get_booking_status",
            {"booking_id": booking_id, "traveler_id": traveler_id, "lead_id": lead_id},
        )
        if remote is not None:
            return remote
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
        remote = self._api_read(
            "lookup_lead",
            {
                "lead_id": lead_id,
                "traveler_id": traveler_id,
                "raw_phone": raw_phone,
                "country_code": country_code,
            },
        )
        if remote is not None:
            return remote
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
        remote = self._api_read(
            "get_passport_status",
            {"traveler_id": traveler_id, "raw_phone": raw_phone, "country_code": country_code},
        )
        if remote is not None:
            return remote
        traveler = None
        if traveler_id:
            try:
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
            except sqlite3.OperationalError:
                with self.service.connect() as connection:
                    traveler = connection.execute(
                        """
                        SELECT traveler_id, full_name, status
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

        try:
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
        except sqlite3.OperationalError:
            documents = []

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
