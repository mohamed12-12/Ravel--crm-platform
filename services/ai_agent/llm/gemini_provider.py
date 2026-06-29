from __future__ import annotations

import json
import re
import time
import uuid
import socket
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request

from services.ai_agent.ai_agent_app.logger import agent_logger


@dataclass(frozen=True)
class GeminiProviderResponse:
    text: str
    response_id: str
    raw: dict[str, Any]
    usage_metadata: dict[str, Any] | None = None


class GeminiProviderError(RuntimeError):
    pass


class GeminiProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 20.0,
        retries: int = 1,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.model = self._normalize_model_name(model)
        self.timeout_seconds = max(float(timeout_seconds), 1.0)
        self.retries = max(int(retries), 0)
        self.base_url = base_url.rstrip("/")

    @staticmethod
    def _normalize_model_name(model: str) -> str:
        cleaned = (model or "").strip()
        if cleaned.startswith("models/"):
            cleaned = cleaned.split("/", 1)[1].strip()
        cleaned = re.sub(r"\s+", "-", cleaned)
        return cleaned

    def is_ready(self) -> bool:
        return bool(self.api_key and self.model)

    def generate(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        generation_config: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> GeminiProviderResponse:
        if not self.is_ready():
            raise GeminiProviderError("Gemini provider is not configured.")

        payload: dict[str, Any] = {
            "system_instruction": {"parts": [{"text": system_prompt or ""}]},
            "contents": messages,
            "generationConfig": {
                "temperature": 0.25,
                "topP": 0.9,
                "maxOutputTokens": 512,
            },
        }
        if generation_config:
            payload["generationConfig"].update(generation_config)
        if tools:
            payload["tools"] = tools

        encoded_model = parse.quote(self.model, safe="")
        encoded_key = parse.quote(self.api_key, safe="")
        url = f"{self.base_url}/models/{encoded_model}:generateContent?key={encoded_key}"
        req = request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            started = time.perf_counter()
            try:
                with request.urlopen(req, timeout=self.timeout_seconds) as response:
                    raw_text = response.read().decode("utf-8")
                    payload_json = json.loads(raw_text)
                    text = self._extract_text(payload_json)
                    response_id = str(payload_json.get("responseId") or payload_json.get("id") or uuid.uuid4().hex)
                    usage_metadata = payload_json.get("usageMetadata")
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    agent_logger.info(
                        "Gemini response received prompt_id=%s response_id=%s elapsed_ms=%s",
                        request_id or "",
                        response_id,
                        elapsed_ms,
                    )
                    return GeminiProviderResponse(
                        text=text,
                        response_id=response_id,
                        raw=payload_json,
                        usage_metadata=usage_metadata if isinstance(usage_metadata, dict) else None,
                    )
            except error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                last_error = exc
                retryable = exc.code in {408, 429, 500, 502, 503, 504}
                agent_logger.warning(
                    "Gemini HTTP error prompt_id=%s attempt=%s status=%s body=%s",
                    request_id or "",
                    attempt + 1,
                    exc.code,
                    body,
                )
                if not retryable or attempt >= self.retries:
                    break
            except (TimeoutError, socket.timeout) as exc:
                last_error = exc
                agent_logger.warning(
                    "Gemini timeout prompt_id=%s attempt=%s timeout=%.2f",
                    request_id or "",
                    attempt + 1,
                    self.timeout_seconds,
                )
                if attempt >= self.retries:
                    break
            except Exception as exc:
                last_error = exc
                agent_logger.warning(
                    "Gemini request failed prompt_id=%s attempt=%s error=%s",
                    request_id or "",
                    attempt + 1,
                    exc,
                )
                if attempt >= self.retries:
                    break

        raise GeminiProviderError(f"Gemini generation failed: {last_error}")

    @staticmethod
    def _extract_text(payload_json: dict[str, Any]) -> str:
        candidates = payload_json.get("candidates") or []
        if not candidates:
            return ""
        content = (candidates[0] or {}).get("content") or {}
        parts = content.get("parts") or []
        pieces: list[str] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            text = str(part.get("text") or "").strip()
            if text:
                pieces.append(text)
        return "\n".join(pieces).strip()
