from __future__ import annotations

import json
import re
import time
from typing import Any

from services.ai_agent.ai_agent_app.agent.prompt_builder import PromptBuilder
from services.ai_agent.ai_agent_app.agent.read_only_tools import ReadOnlyCRMTools
from services.ai_agent.ai_agent_app.agent.tool_registry import build_read_only_tool_registry
from services.ai_agent.ai_agent_app.config import Settings
from services.ai_agent.ai_agent_app.logger import agent_logger
from services.ai_agent.llm.gemini_provider import GeminiProvider, GeminiProviderError


_WRITE_INTENT_PATTERN = re.compile(
    r"\b(create|update|delete|remove|book|reserve|modify|change|insert|write|draft|handoff)\b",
    re.IGNORECASE,
)


def _detect_language(text: str) -> str:
    if re.search(r"[\u0600-\u06FF]", text or ""):
        return "ar"
    return "en"


class GeminiAgent:
    def __init__(
        self,
        *,
        settings: Settings,
        provider: GeminiProvider,
        read_only_tools: ReadOnlyCRMTools | None = None,
        prompt_builder: PromptBuilder | None = None,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.read_only_tools = read_only_tools or ReadOnlyCRMTools(settings)
        self.tool_registry = build_read_only_tool_registry()
        self.prompt_builder = prompt_builder or PromptBuilder(
            system_prompt=settings.ai_agent_system_prompt,
            tool_registry=self.tool_registry,
        )
        self._memory: dict[str, list[dict[str, Any]]] = {}

    @classmethod
    def from_settings(cls, settings: Settings, provider: GeminiProvider) -> "GeminiAgent":
        return cls(settings=settings, provider=provider)

    def _memory_key(self, session_context: dict[str, Any] | None) -> str:
        if not session_context:
            return "global"
        return str(session_context.get("session_id") or session_context.get("id") or "global")

    def _conversation_history(
        self,
        *,
        session_context: dict[str, Any] | None,
        provided_history: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        if provided_history:
            return provided_history[-12:]
        memory_key = self._memory_key(session_context)
        return self._memory.get(memory_key, [])[-12:]

    def _collect_crm_context(self, session_context: dict[str, Any] | None) -> dict[str, Any]:
        session_context = session_context or {}
        crm_context: dict[str, Any] = {}
        raw_phone = str(session_context.get("raw_phone") or session_context.get("pending_raw_phone") or "").strip()
        country_code = str(session_context.get("country_code") or self.settings.default_country_code or "").strip()
        traveler_id = str(session_context.get("traveler_id") or "").strip()
        trip_type = str(session_context.get("trip_type") or "").strip()
        selected_trip_id = str(session_context.get("selected_trip_id") or "").strip()
        lead_id = str(session_context.get("lead_id") or "").strip()
        booking_id = str(session_context.get("booking_id") or "").strip()

        if raw_phone:
            crm_context["traveler_lookup"] = self.read_only_tools.search_traveler_by_phone(
                raw_phone=raw_phone,
                country_code=country_code,
            )
            crm_context["passport_status"] = self.read_only_tools.get_passport_status(
                raw_phone=raw_phone,
                country_code=country_code,
            )
        if traveler_id:
            crm_context["traveler_profile"] = self.read_only_tools.get_traveler_profile(traveler_id=traveler_id)
            crm_context.setdefault("passport_status", self.read_only_tools.get_passport_status(traveler_id=traveler_id))
        if trip_type:
            crm_context["trip_search"] = self.read_only_tools.search_trips(
                trip_type=trip_type,
                query=str(session_context.get("trip_query") or "").strip(),
            )
        if selected_trip_id:
            crm_context["trip_details"] = self.read_only_tools.get_trip_details(trip_id=selected_trip_id)
        if lead_id:
            crm_context["lead_lookup"] = self.read_only_tools.lookup_lead(lead_id=lead_id)
        if booking_id:
            crm_context["booking_lookup"] = self.read_only_tools.lookup_booking(booking_id=booking_id)
        return crm_context

    @staticmethod
    def _looks_like_write_request(text: str) -> bool:
        return bool(_WRITE_INTENT_PATTERN.search(text or ""))

    @staticmethod
    def _safe_refusal() -> str:
        return "Write operations are disabled in Phase 1."

    @staticmethod
    def _extract_reply(text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        try:
            payload = json.loads(cleaned)
            if isinstance(payload, dict):
                reply = str(payload.get("reply") or payload.get("text") or "").strip()
                if reply:
                    return reply
        except Exception:
            return cleaned
        return cleaned

    def rewrite_message(
        self,
        *,
        message_key: str,
        base_text: str,
        language: str,
        session_context: dict[str, Any] | None = None,
        user_text: str = "",
        required_action: str = "",
    ) -> str | None:
        if self._looks_like_write_request(user_text):
            return self._safe_refusal()
        session_context = session_context or {}
        crm_context = self._collect_crm_context(session_context)
        history = self._conversation_history(
            session_context=session_context,
            provided_history=session_context.get("conversation_history") if isinstance(session_context.get("conversation_history"), list) else None,
        )
        package = self.prompt_builder.build_rewrite_prompt(
            message_key=message_key,
            base_text=base_text,
            language=language,
            required_action=required_action,
            session_context=session_context,
            conversation_history=history,
            crm_context=crm_context,
            user_text=user_text,
        )
        started = time.perf_counter()
        try:
            response = self.provider.generate(
                system_prompt=package.system_prompt,
                messages=package.messages,
                tools=package.tools,
                generation_config=package.generation_config,
                request_id=package.prompt_id,
            )
        except GeminiProviderError as exc:
            agent_logger.warning("Gemini rewrite unavailable prompt_id=%s error=%s", package.prompt_id, exc)
            return None
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        reply = self._extract_reply(response.text)
        if not reply:
            return None
        agent_logger.info(
            "Gemini rewrite prompt_id=%s response_id=%s elapsed_ms=%s tool_requests=%s token_usage=%s",
            package.prompt_id,
            response.response_id,
            elapsed_ms,
            ",".join(self.tool_registry.keys()),
            response.usage_metadata or {},
        )
        return reply

    def respond(
        self,
        *,
        user_message: str,
        session_context: dict[str, Any] | None = None,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        session_context = session_context or {}
        if self._looks_like_write_request(user_message):
            return {
                "reply": self._safe_refusal(),
                "tool_requests": [],
                "mode": self.settings.ai_agent_mode,
            }

        language = session_context.get("language") or _detect_language(user_message)
        crm_context = self._collect_crm_context(session_context)
        history = self._conversation_history(session_context=session_context, provided_history=conversation_history)
        package = self.prompt_builder.build_chat_prompt(
            user_message=user_message,
            language=language,
            session_context=session_context,
            conversation_history=history,
            crm_context=crm_context,
        )
        started = time.perf_counter()
        try:
            response = self.provider.generate(
                system_prompt=package.system_prompt,
                messages=package.messages,
                tools=package.tools,
                generation_config=package.generation_config,
                request_id=package.prompt_id,
            )
        except GeminiProviderError as exc:
            agent_logger.warning("Gemini chat unavailable prompt_id=%s error=%s", package.prompt_id, exc)
            return {
                "reply": self._safe_refusal(),
                "tool_requests": [],
                "mode": self.settings.ai_agent_mode,
            }

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        reply = self._extract_reply(response.text)
        if not reply:
            reply = self._safe_refusal()
        memory_key = self._memory_key(session_context)
        self._memory.setdefault(memory_key, []).extend(
            [
                {"role": "user", "text": user_message},
                {"role": "assistant", "text": reply},
            ]
        )
        agent_logger.info(
            "Gemini chat prompt_id=%s response_id=%s elapsed_ms=%s tool_requests=%s token_usage=%s",
            package.prompt_id,
            response.response_id,
            elapsed_ms,
            ",".join(self.tool_registry.keys()),
            response.usage_metadata or {},
        )
        return {
            "reply": reply,
            "tool_requests": list(self.tool_registry.keys()),
            "mode": self.settings.ai_agent_mode,
            "prompt_id": package.prompt_id,
            "response_id": response.response_id,
            "elapsed_ms": elapsed_ms,
            "usage_metadata": response.usage_metadata or {},
        }
