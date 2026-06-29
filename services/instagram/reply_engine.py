from __future__ import annotations

from dataclasses import dataclass

from services.instagram.payload_parser import InstagramInboundEvent


@dataclass(frozen=True)
class InstagramReplyDraft:
    text: str
    reason: str


def build_instagram_reply(event: InstagramInboundEvent) -> InstagramReplyDraft:
    """Build a safe outbound reply for an inbound Instagram DM.

    This is intentionally deterministic and conservative so we can ship a
    production-safe outbound path before layering richer AI behavior on top.
    """
    text = (event.text or "").strip().lower()
    has_attachment = bool(event.attachments)

    if not text and has_attachment:
        return InstagramReplyDraft(
            text=(
                "Thanks for sending the attachment. Please share your WhatsApp number "
                "so I can check your profile safely."
            ),
            reason="attachment_only",
        )

    if "passport" in text or "attachment" in text or has_attachment:
        return InstagramReplyDraft(
            text=(
                "Thanks for the passport attachment. Please share your WhatsApp number "
                "so I can check your profile safely."
            ),
            reason="passport_attachment",
        )

    if "visa" in text:
        return InstagramReplyDraft(
            text=(
                "I can help with visa guidance. Please share your destination and your WhatsApp number "
                "so I can check your profile safely."
            ),
            reason="visa_inquiry",
        )

    if any(keyword in text for keyword in {"trip", "tour", "booking", "reserve", "reservation"}):
        return InstagramReplyDraft(
            text=(
                "Thanks for reaching out to Rahma Traveler. Please share your WhatsApp number "
                "so I can check your profile safely."
            ),
            reason="travel_inquiry",
        )

    return InstagramReplyDraft(
        text=(
            "Thanks for reaching out to Rahma Traveler. Please share your WhatsApp number "
            "so I can check your profile safely."
        ),
        reason="default",
    )
