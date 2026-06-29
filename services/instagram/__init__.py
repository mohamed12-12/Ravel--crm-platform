"""Instagram and Meta integration primitives."""

from services.instagram.channel_bridge import InstagramChannelBridge, InstagramConversationInput
from services.instagram.meta_client import MetaApiSettings, MetaGraphClient, MetaSendResult
from services.instagram.payload_parser import InstagramAttachment, InstagramInboundEvent, parse_instagram_webhook
from services.instagram.reply_engine import InstagramReplyDraft, build_instagram_reply

__all__ = [
    "InstagramAttachment",
    "InstagramChannelBridge",
    "InstagramConversationInput",
    "InstagramInboundEvent",
    "InstagramReplyDraft",
    "MetaApiSettings",
    "MetaGraphClient",
    "MetaSendResult",
    "build_instagram_reply",
    "parse_instagram_webhook",
]
