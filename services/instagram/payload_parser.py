from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class InstagramAttachment:
    attachment_type: str
    url: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InstagramInboundEvent:
    event_id: str
    sender_id: str
    recipient_id: str
    timestamp: int
    text: str = ""
    attachments: tuple[InstagramAttachment, ...] = ()
    raw_event: dict[str, Any] = field(default_factory=dict)


def parse_instagram_webhook(payload: Any) -> list[InstagramInboundEvent]:
    """Normalize Meta webhook payloads into inbound Instagram message events."""
    events: list[InstagramInboundEvent] = []
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return events
    if not isinstance(payload, dict):
        return events

    entries = payload.get("entry", []) or []
    if isinstance(entries, str):
        try:
            entries = json.loads(entries)
        except Exception:
            entries = []
    if isinstance(entries, dict):
        entries = [entries]
    elif not isinstance(entries, list):
        entries = []

    for entry in entries:
        try:
            if isinstance(entry, str):
                try:
                    entry = json.loads(entry)
                except Exception:
                    continue
            if not isinstance(entry, dict):
                continue

            messaging_list = entry.get("messaging", []) or []
            if isinstance(messaging_list, str):
                try:
                    messaging_list = json.loads(messaging_list)
                except Exception:
                    messaging_list = []
            if isinstance(messaging_list, dict):
                messaging_list = [messaging_list]
            elif not isinstance(messaging_list, list):
                messaging_list = []

            for messaging in messaging_list:
                if isinstance(messaging, str):
                    try:
                        messaging = json.loads(messaging)
                    except Exception:
                        continue
                if not isinstance(messaging, dict):
                    continue

                message = messaging.get("message")
                if not isinstance(message, dict):
                    message = {}
                sender = messaging.get("sender")
                if not isinstance(sender, dict):
                    sender = {}
                recipient = messaging.get("recipient")
                if not isinstance(recipient, dict):
                    recipient = {}

                message_id = str(message.get("mid") or messaging.get("mid") or "").strip()
                sender_id = str(sender.get("id") or "").strip()
                recipient_id = str(recipient.get("id") or "").strip()
                if not message_id or not sender_id:
                    continue

                attachments: list[InstagramAttachment] = []
                raw_attachments = message.get("attachments", []) or []
                if isinstance(raw_attachments, dict):
                    raw_attachments = [raw_attachments]
                elif not isinstance(raw_attachments, list):
                    raw_attachments = []

                for item in raw_attachments:
                    if not isinstance(item, dict):
                        continue
                    payload_block = item.get("payload") or {}
                    url_str = str(payload_block.get("url") or "").strip() if isinstance(payload_block, dict) else ""
                    attachments.append(
                        InstagramAttachment(
                            attachment_type=str(item.get("type") or "").strip(),
                            url=url_str,
                            payload=payload_block if isinstance(payload_block, dict) else {},
                        )
                    )

                events.append(
                    InstagramInboundEvent(
                        event_id=message_id,
                        sender_id=sender_id,
                        recipient_id=recipient_id,
                        timestamp=int(messaging.get("timestamp") or 0),
                        text=str(message.get("text") or "").strip(),
                        attachments=tuple(attachments),
                        raw_event=messaging,
                    )
                )
        except Exception:
            continue

    return events

