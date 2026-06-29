from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.instagram.payload_parser import InstagramInboundEvent


@dataclass(frozen=True)
class InstagramConversationInput:
    sender_id: str
    message_text: str
    attachment_count: int
    event_id: str
    raw_event: dict[str, Any]


class InstagramChannelBridge:
    """Adapter layer between Meta webhook events and the Rahma AI session flow."""

    @staticmethod
    def build_input(event: InstagramInboundEvent) -> InstagramConversationInput:
        return InstagramConversationInput(
            sender_id=event.sender_id,
            message_text=event.text,
            attachment_count=len(event.attachments),
            event_id=event.event_id,
            raw_event=event.raw_event,
        )

    @staticmethod
    def build_reply_payload(reply_text: str) -> dict[str, Any]:
        return {"text": str(reply_text or "").strip()}

