from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db


@dataclass
class ConversationPage:
    items: list[dict[str, Any]]
    page: int
    per_page: int
    total: int

    @property
    def pages(self) -> int:
        if self.total <= 0:
            return 1
        return ((self.total - 1) // self.per_page) + 1

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def prev_num(self) -> int:
        return max(1, self.page - 1)

    @property
    def next_num(self) -> int:
        return min(self.pages, self.page + 1)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    text_value = str(value).strip()
    if not text_value:
        return None
    if text_value.endswith("Z"):
        text_value = f"{text_value[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text_value)
    except ValueError:
        return None


def _format_datetime(value: datetime | None) -> str:
    if not value:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.strftime("%d %b %Y %H:%M")


def _load_payload(raw_payload: Any) -> dict[str, Any]:
    if isinstance(raw_payload, dict):
        return raw_payload
    try:
        payload = json.loads(raw_payload or "{}")
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _message_text(message: dict[str, Any]) -> str:
    for key in ("text", "message_text", "message", "content"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _message_direction(role: str) -> str:
    normalized = role.strip().lower()
    if normalized == "user":
        return "inbound"
    if normalized in {"assistant", "agent"}:
        return "outbound"
    return normalized or "system"


def _message_actor(role: str) -> str:
    direction = _message_direction(role)
    if direction == "inbound":
        return "Traveler"
    if direction == "outbound":
        return "Agent"
    return role.strip().title() or "System"


def _clean_identifier(value: Any) -> str:
    return str(value or "").strip()


def _row_to_conversation(row: dict[str, Any]) -> dict[str, Any]:
    payload = _load_payload(row.get("payload"))
    raw_messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    messages: list[dict[str, Any]] = []
    for index, raw_message in enumerate(raw_messages, start=1):
        if not isinstance(raw_message, dict):
            continue
        body = _message_text(raw_message)
        if not body:
            continue
        role = str(raw_message.get("role") or "").strip()
        timestamp = _parse_datetime(
            raw_message.get("timestamp")
            or raw_message.get("created_at")
            or raw_message.get("time")
            or raw_message.get("sent_at")
        )
        messages.append(
            {
                "turn": index,
                "role": role,
                "actor": _message_actor(role),
                "direction": _message_direction(role),
                "text": body,
                "timestamp": timestamp,
                "timestamp_label": _format_datetime(timestamp),
            }
        )

    created_at = _parse_datetime(row.get("created_at"))
    updated_at = _parse_datetime(row.get("updated_at"))
    last_message = messages[-1] if messages else {}
    traveler_id = _clean_identifier(row.get("traveler_id") or payload.get("traveler_id"))
    lead_id = _clean_identifier(row.get("lead_id") or payload.get("lead_id") or payload.get("_open_lead_id"))
    raw_phone = _clean_identifier(row.get("raw_phone") or payload.get("raw_phone"))
    stage = _clean_identifier(payload.get("stage"))
    outcome = _clean_identifier(payload.get("handoff_state") or payload.get("booking_status") or payload.get("lead_stage"))

    return {
        "session_id": _clean_identifier(row.get("session_id") or payload.get("id")),
        "traveler_id": traveler_id,
        "lead_id": lead_id,
        "raw_phone": raw_phone,
        "stage": stage,
        "outcome": outcome,
        "created_at": created_at,
        "updated_at": updated_at,
        "created_label": _format_datetime(created_at),
        "updated_label": _format_datetime(updated_at),
        "messages": messages,
        "turn_count": len(messages),
        "last_message": last_message,
        "last_message_text": str(last_message.get("text") or ""),
        "last_message_direction": str(last_message.get("direction") or ""),
        "last_activity": updated_at or created_at,
        "last_activity_label": _format_datetime(updated_at or created_at),
    }


def _session_rows(where_clause: str = "", params: dict[str, Any] | None = None, *, limit: int = 300) -> list[dict[str, Any]]:
    sql = """
        SELECT session_id, payload, created_at, updated_at, traveler_id, lead_id, raw_phone, last_message_key
        FROM ai_agent_sessions
    """
    if where_clause:
        sql += f" WHERE {where_clause}"
    sql += " ORDER BY COALESCE(updated_at, created_at) DESC, session_id DESC LIMIT :limit"
    query_params = dict(params or {})
    query_params["limit"] = int(limit)
    try:
        rows = db.session.execute(text(sql), query_params).mappings().all()
    except SQLAlchemyError:
        db.session.rollback()
        return []
    return [dict(row) for row in rows]


def get_conversation(session_id: str) -> dict[str, Any] | None:
    rows = _session_rows("session_id = :session_id", {"session_id": session_id}, limit=1)
    return _row_to_conversation(rows[0]) if rows else None


def get_conversations_for_traveler(traveler_id: str, *, limit: int = 8) -> list[dict[str, Any]]:
    traveler_id = _clean_identifier(traveler_id)
    if not traveler_id:
        return []
    return [
        _row_to_conversation(row)
        for row in _session_rows("traveler_id = :traveler_id", {"traveler_id": traveler_id}, limit=limit)
    ]


def get_conversations_for_lead(lead_id: str, traveler_id: str = "", *, limit: int = 8) -> list[dict[str, Any]]:
    lead_id = _clean_identifier(lead_id)
    traveler_id = _clean_identifier(traveler_id)
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if lead_id:
        clauses.append("lead_id = :lead_id")
        params["lead_id"] = lead_id
    if traveler_id:
        clauses.append("traveler_id = :traveler_id")
        params["traveler_id"] = traveler_id
    if not clauses:
        return []
    return [_row_to_conversation(row) for row in _session_rows(" OR ".join(clauses), params, limit=limit)]


def list_conversations(*, q: str = "", page: int = 1, per_page: int = 30) -> ConversationPage:
    page = max(1, int(page or 1))
    per_page = max(1, int(per_page or 30))
    rows = _session_rows(limit=1000)
    conversations = [_row_to_conversation(row) for row in rows]
    needle = q.strip().lower()
    if needle:
        conversations = [
            item for item in conversations
            if needle in " ".join(
                [
                    item.get("session_id", ""),
                    item.get("traveler_id", ""),
                    item.get("lead_id", ""),
                    item.get("raw_phone", ""),
                    item.get("stage", ""),
                    item.get("outcome", ""),
                    item.get("last_message_text", ""),
                ]
            ).lower()
        ]
    total = len(conversations)
    start = (page - 1) * per_page
    return ConversationPage(items=conversations[start:start + per_page], page=page, per_page=per_page, total=total)


def list_conversation_groups(*, q: str = "", page: int = 1, per_page: int = 30) -> ConversationPage:
    conversations = list_conversations(q=q, page=1, per_page=1000).items
    grouped: dict[str, dict[str, Any]] = {}
    for conversation in conversations:
        group_key = (
            conversation.get("traveler_id")
            or conversation.get("lead_id")
            or conversation.get("raw_phone")
            or conversation.get("session_id")
            or "unknown"
        )
        group = grouped.setdefault(
            group_key,
            {
                "key": group_key,
                "traveler_id": conversation.get("traveler_id", ""),
                "lead_id": conversation.get("lead_id", ""),
                "raw_phone": conversation.get("raw_phone", ""),
                "sessions": [],
                "session_count": 0,
                "turn_count": 0,
                "last_activity": None,
                "last_activity_label": "",
                "last_message_text": "",
                "last_message_direction": "",
                "stage": "",
                "outcome": "",
            },
        )
        group["sessions"].append(conversation)
        group["session_count"] += 1
        group["turn_count"] += int(conversation.get("turn_count") or 0)
        activity = conversation.get("last_activity")
        if group["last_activity"] is None or (activity and activity > group["last_activity"]):
            group["last_activity"] = activity
            group["last_activity_label"] = conversation.get("last_activity_label", "")
            group["last_message_text"] = conversation.get("last_message_text", "")
            group["last_message_direction"] = conversation.get("last_message_direction", "")
            group["stage"] = conversation.get("stage", "")
            group["outcome"] = conversation.get("outcome", "")
            group["traveler_id"] = conversation.get("traveler_id", "") or group["traveler_id"]
            group["lead_id"] = conversation.get("lead_id", "") or group["lead_id"]
            group["raw_phone"] = conversation.get("raw_phone", "") or group["raw_phone"]

    def sort_value(item: dict[str, Any]) -> float:
        value = item.get("last_activity")
        if not isinstance(value, datetime):
            return 0.0
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()

    groups = sorted(grouped.values(), key=sort_value, reverse=True)
    page = max(1, int(page or 1))
    per_page = max(1, int(per_page or 30))
    total = len(groups)
    start = (page - 1) * per_page
    return ConversationPage(items=groups[start:start + per_page], page=page, per_page=per_page, total=total)


def session_id_from_handoff_notes(raw_notes: str | None, idempotency_key: str | None = None) -> str:
    text_value = str(raw_notes or "").strip()
    candidates: list[dict[str, Any]] = []
    if text_value.startswith("{"):
        payload = _load_payload(text_value)
        if payload:
            candidates.append(payload)
    for line in text_value.splitlines():
        if line.startswith("handoff_context="):
            payload = _load_payload(line.split("=", 1)[1].strip())
            if payload:
                candidates.append(payload)
    for payload in candidates:
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        for value in (
            metadata.get("session_id"),
            payload.get("session_id"),
            payload.get("conversation_session_id"),
        ):
            session_id = _clean_identifier(value)
            if session_id:
                return session_id

    key = _clean_identifier(idempotency_key)
    if key.startswith("handoff:"):
        parts = key.split(":")
        if len(parts) >= 4:
            return parts[-1].strip()

    match = re.search(r"\bsession[_ -]?id['\"]?\s*[:=]\s*['\"]?([A-Za-z0-9_.:-]+)", text_value, re.IGNORECASE)
    return match.group(1).strip() if match else ""
