from __future__ import annotations

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


def parse_instagram_webhook(payload: dict[str, Any]) -> list[InstagramInboundEvent]:
    """Normalize Meta webhook payloads into inbound Instagram message events."""
    events: list[InstagramInboundEvent] = []
    for entry in payload.get("entry", []) or []:
        for messaging in entry.get("messaging", []) or []:
            message = messaging.get("message") or {}
            sender = messaging.get("sender") or {}
            recipient = messaging.get("recipient") or {}

            message_id = str(message.get("mid") or messaging.get("mid") or "").strip()
            sender_id = str(sender.get("id") or "").strip()
            recipient_id = str(recipient.get("id") or "").strip()
            if not message_id or not sender_id:
                continue

            attachments: list[InstagramAttachment] = []
            for item in message.get("attachments", []) or []:
                payload_block = item.get("payload") or {}
                attachments.append(
                    InstagramAttachment(
                        attachment_type=str(item.get("type") or "").strip(),
                        url=str(payload_block.get("url") or "").strip(),
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
    return events

