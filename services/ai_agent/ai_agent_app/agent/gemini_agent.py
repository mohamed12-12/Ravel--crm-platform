from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from typing import Any

from services.ai_agent.ai_agent_app.agent.prompt_builder import PromptBuilder
from services.ai_agent.ai_agent_app.agent.read_only_tools import ReadOnlyCRMTools
from services.ai_agent.ai_agent_app.agent.tool_registry import ToolSpec, build_read_only_tool_registry
from services.ai_agent.ai_agent_app.config import Settings
from services.ai_agent.ai_agent_app.logger import agent_logger
from services.ai_agent.llm.gemini_provider import GeminiProvider, GeminiProviderError


_WRITE_INTENT_PATTERN = re.compile(
    r"\b(create|update|delete|remove|modify|insert|write|draft|handoff|cancel)\b",
    re.IGNORECASE,
)


def _detect_language(text: str) -> str:
    if re.search(r"[\u0600-\u06FF]", text or ""):
        return "ar"
    return "en"


class GeminiToolLoopError(RuntimeError):
    pass


class GeminiAgent:
    def __init__(
        self,
        *,
        settings: Settings,
        provider: GeminiProvider,
        read_only_tools: ReadOnlyCRMTools | None = None,
        prompt_builder: PromptBuilder | None = None,
        max_tool_calls: int = 4,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.read_only_tools = read_only_tools or ReadOnlyCRMTools(settings)
        self.tool_registry = build_read_only_tool_registry()
        self.prompt_builder = prompt_builder or PromptBuilder(
            system_prompt=settings.ai_agent_system_prompt,
            tool_registry=self.tool_registry,
        )
        self.max_tool_calls = max(1, int(max_tool_calls))
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
            return [deepcopy(message) for message in provided_history[-12:]]
        memory_key = self._memory_key(session_context)
        return [deepcopy(message) for message in self._memory.get(memory_key, [])[-12:]]

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
            crm_context["traveler_lookup"] = self.read_only_tools.search_traveler(
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
            crm_context["booking_lookup"] = self.read_only_tools.get_booking_status(booking_id=booking_id)
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

    @staticmethod
    def _extract_candidate_parts(raw_response: dict[str, Any]) -> list[dict[str, Any]]:
        candidates = raw_response.get("candidates") or []
        if not candidates:
            return []
        content = (candidates[0] or {}).get("content") or {}
        parts = content.get("parts") or []
        return [part for part in parts if isinstance(part, dict)]

    @staticmethod
    def _part_is_function_call(part: dict[str, Any]) -> bool:
        return "functionCall" in part or "function_call" in part

    @staticmethod
    def _normalize_function_call(part: dict[str, Any]) -> dict[str, Any]:
        call = part.get("functionCall") or part.get("function_call") or {}
        if not isinstance(call, dict):
            return {}
        name = str(call.get("name") or "").strip()
        args = call.get("args")
        if args is None:
            args = call.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {"value": args}
        if args is None:
            args = {}
        if not isinstance(args, dict):
            args = {"value": args}
        return {
            "name": name,
            "args": args,
            "id": str(call.get("id") or call.get("callId") or "").strip(),
            "raw": call,
        }

    def _validate_tool_call(self, name: str, args: dict[str, Any]) -> ToolSpec:
        spec = self.tool_registry.get(name)
        if spec is None:
            raise GeminiToolLoopError(f"Unsupported tool requested: {name}")

        required = spec.input_schema.get("required") or []
        if not isinstance(args, dict):
            raise GeminiToolLoopError(f"Invalid tool input for {name}: expected object.")
        for field in required:
            value = args.get(field)
            if not isinstance(value, str) or not value.strip():
                raise GeminiToolLoopError(f"Invalid tool input for {name}: missing {field}.")

        for key, value in args.items():
            if value is None:
                continue
            if isinstance(value, str):
                continue
            if isinstance(value, (int, float, bool)):
                continue
            raise GeminiToolLoopError(f"Invalid tool input for {name}: unsupported field {key}.")
        return spec

    def _execute_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "search_traveler":
            return self.read_only_tools.search_traveler(
                raw_phone=str(args.get("raw_phone") or ""),
                country_code=str(args.get("country_code") or self.settings.default_country_code or ""),
            )
        if name == "get_traveler_profile":
            return self.read_only_tools.get_traveler_profile(
                traveler_id=str(args.get("traveler_id") or ""),
                raw_phone=str(args.get("raw_phone") or ""),
                country_code=str(args.get("country_code") or self.settings.default_country_code or ""),
            )
        if name == "search_trips":
            return self.read_only_tools.search_trips(
                trip_type=str(args.get("trip_type") or ""),
                query=str(args.get("query") or ""),
            )
        if name == "get_trip_details":
            return self.read_only_tools.get_trip_details(trip_id=str(args.get("trip_id") or ""))
        if name == "get_booking_status":
            return self.read_only_tools.get_booking_status(
                booking_id=str(args.get("booking_id") or ""),
                traveler_id=str(args.get("traveler_id") or ""),
                lead_id=str(args.get("lead_id") or ""),
            )
        if name == "get_passport_status":
            return self.read_only_tools.get_passport_status(
                traveler_id=str(args.get("traveler_id") or ""),
                raw_phone=str(args.get("raw_phone") or ""),
                country_code=str(args.get("country_code") or self.settings.default_country_code or ""),
            )
        raise GeminiToolLoopError(f"Unsupported tool requested: {name}")

    @staticmethod
    def _tool_result_summary(result: dict[str, Any]) -> dict[str, Any]:
        summary: dict[str, Any] = {"type": type(result).__name__}
        if isinstance(result, dict):
            summary["keys"] = sorted(result.keys())
            for key in ("match_status", "handoff_required", "traveler", "trips", "open_trips", "bookings", "documents"):
                value = result.get(key)
                if value is None:
                    continue
                if isinstance(value, list):
                    summary[f"{key}_count"] = len(value)
                elif isinstance(value, dict):
                    summary[f"{key}_keys"] = sorted(value.keys())
                else:
                    summary[key] = value
        return summary

    @staticmethod
    def _tool_response_part(name: str, result: dict[str, Any]) -> dict[str, Any]:
        return {"functionResponse": {"name": name, "response": result}}

    def _run_tool_loop(
        self,
        *,
        package,
        session_context: dict[str, Any],
        user_message: str,
    ) -> dict[str, Any]:
        history = self._conversation_history(
            session_context=session_context,
            provided_history=session_context.get("conversation_history") if isinstance(session_context.get("conversation_history"), list) else None,
        )
        contents: list[dict[str, Any]] = [*history, *deepcopy(package.messages)]
        tool_events: list[dict[str, Any]] = []
        tool_call_count = 0

        while True:
            response = self.provider.generate(
                system_prompt=package.system_prompt,
                messages=contents,
                tools=package.tools,
                generation_config=package.generation_config,
                request_id=package.prompt_id,
            )
            parts = self._extract_candidate_parts(response.raw)
            function_calls = [self._normalize_function_call(part) for part in parts if self._part_is_function_call(part)]
            function_calls = [call for call in function_calls if call.get("name")]

            if not function_calls:
                reply_text = self._extract_reply(response.text)
                if not reply_text:
                    reply_text = self._extract_reply(
                        "\n".join(str(part.get("text") or "").strip() for part in parts if isinstance(part, dict) and part.get("text"))
                    )
                if not reply_text:
                    reply_text = self._safe_refusal()
                memory_key = self._memory_key(session_context)
                self._memory.setdefault(memory_key, []).extend(
                    [
                        {"role": "user", "text": user_message},
                        {"role": "assistant", "text": reply_text},
                    ]
                )
                agent_logger.info(
                    "Gemini final answer prompt_id=%s response_id=%s tool_calls=%s final_answer=%s",
                    package.prompt_id,
                    response.response_id,
                    tool_call_count,
                    reply_text,
                )
                return {
                    "reply": reply_text,
                    "tool_requests": tool_events,
                    "mode": self.settings.ai_agent_mode,
                    "prompt_id": package.prompt_id,
                    "response_id": response.response_id,
                    "elapsed_ms": None,
                    "usage_metadata": response.usage_metadata or {},
                }

            for call in function_calls:
                tool_call_count += 1
                if tool_call_count > self.max_tool_calls:
                    raise GeminiToolLoopError("Maximum Gemini tool-call limit reached.")
                spec = self._validate_tool_call(call["name"], call.get("args") or {})
                if self._looks_like_write_request(call["name"]):
                    raise GeminiToolLoopError("Write-like tool requests are not allowed in Phase 2.")

                tool_input = call.get("args") or {}
                agent_logger.info(
                    "Gemini tool requested prompt_id=%s tool=%s input=%s",
                    package.prompt_id,
                    spec.name,
                    json.dumps(tool_input, ensure_ascii=False, sort_keys=True),
                )
                result = self._execute_tool(spec.name, tool_input)
                summary = self._tool_result_summary(result)
                agent_logger.info(
                    "Gemini tool result prompt_id=%s tool=%s summary=%s",
                    package.prompt_id,
                    spec.name,
                    json.dumps(summary, ensure_ascii=False, sort_keys=True),
                )
                tool_events.append(
                    {
                        "name": spec.name,
                        "input": deepcopy(tool_input),
                        "summary": summary,
                    }
                )
                contents.append({"role": "model", "parts": deepcopy(parts)})
                contents.append({"role": "user", "parts": [self._tool_response_part(spec.name, result)]})

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
            result = self._run_tool_loop(package=package, session_context=session_context, user_message=user_text or base_text)
        except GeminiToolLoopError as exc:
            agent_logger.warning("Gemini rewrite rejected prompt_id=%s error=%s", package.prompt_id, exc)
            return None
        except GeminiProviderError as exc:
            agent_logger.warning("Gemini rewrite unavailable prompt_id=%s error=%s", package.prompt_id, exc)
            return None
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        reply = str(result.get("reply") or "").strip()
        if not reply:
            return None
        agent_logger.info(
            "Gemini rewrite prompt_id=%s response_id=%s elapsed_ms=%s tool_requests=%s token_usage=%s",
            package.prompt_id,
            result.get("response_id") or "",
            elapsed_ms,
            ",".join(event["name"] for event in result.get("tool_requests", [])),
            result.get("usage_metadata") or {},
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
                "error": "write_request_rejected",
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
            result = self._run_tool_loop(package=package, session_context=session_context, user_message=user_message)
        except GeminiToolLoopError as exc:
            agent_logger.warning("Gemini chat rejected prompt_id=%s error=%s", package.prompt_id, exc)
            return {
                "reply": self._safe_refusal(),
                "tool_requests": [],
                "mode": self.settings.ai_agent_mode,
                "error": str(exc),
            }
        except GeminiProviderError as exc:
            agent_logger.warning("Gemini chat unavailable prompt_id=%s error=%s", package.prompt_id, exc)
            return {
                "reply": self._safe_refusal(),
                "tool_requests": [],
                "mode": self.settings.ai_agent_mode,
                "error": str(exc),
            }

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        reply = str(result.get("reply") or "").strip() or self._safe_refusal()
        final_payload = {
            "reply": reply,
            "tool_requests": result.get("tool_requests", []),
            "mode": self.settings.ai_agent_mode,
            "prompt_id": result.get("prompt_id"),
            "response_id": result.get("response_id"),
            "elapsed_ms": elapsed_ms,
            "usage_metadata": result.get("usage_metadata") or {},
        }
        agent_logger.info(
            "Gemini chat final prompt_id=%s response_id=%s elapsed_ms=%s final_answer=%s",
            result.get("prompt_id") or "",
            result.get("response_id") or "",
            elapsed_ms,
            reply,
        )
        return final_payload
