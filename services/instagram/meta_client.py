from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import error, request


@dataclass(frozen=True)
class MetaApiSettings:
    page_access_token: str
    graph_api_version: str = "v23.0"


@dataclass(frozen=True)
class MetaSendResult:
    ok: bool
    status_code: int
    response_json: dict[str, Any]


class MetaGraphClient:
    """Thin Graph API client for Instagram messaging send operations."""

    def __init__(self, settings: MetaApiSettings) -> None:
        self.settings = settings

    def send_instagram_text_message(self, recipient_id: str, text: str) -> MetaSendResult:
        payload = {
            "recipient": {"id": recipient_id},
            "messaging_type": "RESPONSE",
            "message": {"text": text},
        }
        return self._post_json("me/messages", payload)

    def _post_json(self, path: str, payload: dict[str, Any]) -> MetaSendResult:
        url = (
            f"https://graph.facebook.com/{self.settings.graph_api_version}/{path}"
            f"?access_token={self.settings.page_access_token}"
        )
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=20) as response:
                raw = response.read().decode("utf-8")
                return MetaSendResult(
                    ok=200 <= response.status < 300,
                    status_code=response.status,
                    response_json=json.loads(raw) if raw else {},
                )
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            return MetaSendResult(
                ok=False,
                status_code=exc.code,
                response_json=json.loads(raw) if raw else {},
            )

