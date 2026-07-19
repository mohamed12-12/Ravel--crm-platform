from __future__ import annotations

import json
from typing import Any
from urllib import error, request


class CRMApiError(RuntimeError):
    pass


class CRMApiClient:
    """Authenticated client for the CRM-owned agent tool endpoints."""

    def __init__(self, *, base_url: str, token: str, timeout_seconds: float = 15.0) -> None:
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.token = str(token or "").strip()
        self.timeout_seconds = float(timeout_seconds)
        if not self.base_url:
            raise ValueError("CRM_API_BASE_URL is required when CRM_ACCESS_MODE=api.")
        if not self.token:
            raise ValueError("CRM_API_TOKEN is required when CRM_ACCESS_MODE=api.")

    def read(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/api/crm/agent/read", {"action": action, "payload": payload})

    def write(
        self,
        action: str,
        payload: dict[str, Any],
        session_context: dict[str, Any],
    ) -> dict[str, Any]:
        return self._post(
            "/api/crm/agent/write",
            {"action": action, "payload": payload, "session_context": session_context},
        )

    def save_passport(self, traveler_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post(
            "/api/crm/agent/passport",
            {"traveler_id": traveler_id, "payload": payload},
        )

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        req = request.Request(
            f"{self.base_url}{path}",
            data=encoded,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-CRM-Role": "agent",
            },
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise CRMApiError(f"CRM API returned HTTP {exc.code}: {detail[:500]}") from exc
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise CRMApiError(f"CRM API request failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise CRMApiError("CRM API returned a non-object response.")
        if payload.get("error"):
            raise CRMApiError(str(payload["error"]))
        result = payload.get("result")
        return dict(result) if isinstance(result, dict) else payload
