"""Drives one Gemini tool-calling round-trip: sends the conversation +
available tools to the model, executes whatever tool calls it requests
(via ReadOnlyCRMTools for reads, GeminiWriteToolExecutor for writes),
feeds results back, and repeats up to `max_tool_calls` times. Used both
as the conversation engine inside `ToolCallingSessionRuntime`
(tool_calling_runtime.py) and, standalone, as the simpler non-tool-calling
Gemini mode server.py's `create_app()` can select instead.
"""
from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from typing import Any

from services.ai_agent.ai_agent_app.agent.prompt_builder import PromptBuilder
from services.ai_agent.ai_agent_app.agent.response_format import format_agent_reply, response_completeness_issue
from services.ai_agent.ai_agent_app.agent.response_guard import (
    guard_customer_response,
    known_record_ids_from_context,
)
from services.ai_agent.ai_agent_app.agent.privacy_policy import AgentPrivacyPolicy
from services.ai_agent.ai_agent_app.agent.read_only_tools import ReadOnlyCRMTools
from services.ai_agent.ai_agent_app.agent.safety import AgentSafetyLayer
from services.ai_agent.ai_agent_app.agent.tool_contracts import (
    normalize_tool_result_contract,
    safe_tool_input_error_result,
    validate_tool_input_contract,
)
from services.ai_agent.ai_agent_app.agent.tool_registry import ToolSpec, build_agent_tool_registry
from services.ai_agent.ai_agent_app.agent.tool_routing_audit import evaluate_tool_route, should_enforce_tool_route
from services.ai_agent.ai_agent_app.agent.workflow_policy import ConversationWorkflowPolicy
from services.ai_agent.ai_agent_app.agent.write_tool_executor import GeminiWriteToolExecutor
from services.ai_agent.ai_agent_app.agent.write_response_gating import detect_write_record_type, gate_customer_write_reply
from services.ai_agent.ai_agent_app.agent.write_result import WriteOutcome, normalize_write_result
from services.ai_agent.ai_agent_app.config import Settings
from services.ai_agent.ai_agent_app.logger import agent_logger
from services.ai_agent.validation import ActionValidator
from services.ai_agent.llm.gemini_provider import GeminiProvider, GeminiProviderError


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
        action_validator: ActionValidator | None = None,
        prompt_builder: PromptBuilder | None = None,
        tool_registry: dict[str, ToolSpec] | None = None,
        write_tools_enabled: bool = False,
        max_tool_calls: int = 4,
        safety_layer: AgentSafetyLayer | None = None,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.read_only_tools = read_only_tools or ReadOnlyCRMTools(settings)
        self.action_validator = action_validator or ActionValidator(settings, read_only_tools=self.read_only_tools)
        self.write_tools_enabled = bool(write_tools_enabled)
        self.read_only_tool_registry = build_agent_tool_registry(
            include_write_tools=False,
            include_validation_tool=False,
        )
        self.tool_registry = tool_registry or build_agent_tool_registry(include_write_tools=self.write_tools_enabled)
        self.prompt_builder = prompt_builder or PromptBuilder(
            system_prompt=settings.ai_agent_system_prompt,
            tool_registry=self.tool_registry,
        )
        self.safety_layer = safety_layer or AgentSafetyLayer()
        self.workflow_policy = ConversationWorkflowPolicy()
        self.privacy_policy = AgentPrivacyPolicy()
        self.rewrite_prompt_builder = PromptBuilder(
            system_prompt=settings.ai_agent_system_prompt,
            tool_registry=self.read_only_tool_registry,
        )
        self.write_executor = (
            GeminiWriteToolExecutor(settings=settings, read_only_tools=self.read_only_tools, action_validator=self.action_validator)
            if self.write_tools_enabled
            else None
        )
        self.max_tool_calls = max(1, int(max_tool_calls))
        self._memory: dict[str, list[dict[str, Any]]] = {}

    @classmethod
    def from_settings(cls, settings: Settings, provider: GeminiProvider, *, write_tools_enabled: bool = False) -> "GeminiAgent":
        return cls(settings=settings, provider=provider, write_tools_enabled=write_tools_enabled)

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
            crm_context["lead_lookup"] = self.read_only_tools.lookup_lead(
                raw_phone=raw_phone,
                country_code=country_code,
            )
            crm_context["passport_status"] = self.read_only_tools.get_passport_status(
                raw_phone=raw_phone,
                country_code=country_code,
            )
        if traveler_id:
            crm_context["traveler_profile"] = self.read_only_tools.get_traveler_profile(traveler_id=traveler_id)
            crm_context.setdefault("lead_lookup", self.read_only_tools.lookup_lead(traveler_id=traveler_id))
            crm_context.setdefault("passport_status", self.read_only_tools.get_passport_status(traveler_id=traveler_id))
        if lead_id:
            crm_context["lead_lookup"] = self.read_only_tools.lookup_lead(lead_id=lead_id, traveler_id=traveler_id)
        workflow_policy = session_context.get("workflow_policy") if isinstance(session_context.get("workflow_policy"), dict) else {}
        if trip_type and bool(workflow_policy.get("trip_search_allowed")):
            crm_context["trip_search"] = self.read_only_tools.search_trips(
                trip_type=trip_type,
                query=str(session_context.get("trip_query") or "").strip(),
            )
        if selected_trip_id:
            crm_context["trip_details"] = self.read_only_tools.get_trip_details(trip_id=selected_trip_id)
        if booking_id:
            crm_context["booking_lookup"] = self.read_only_tools.get_booking_status(booking_id=booking_id)
        traveler_record = {}
        for key in ("traveler_profile", "traveler_lookup"):
            payload = crm_context.get(key)
            if isinstance(payload, dict) and isinstance(payload.get("traveler"), dict):
                traveler_record = dict(payload.get("traveler") or {})
                if traveler_record:
                    break
        if not traveler_record and isinstance(session_context.get("known_traveler"), dict):
            traveler_record = dict(session_context.get("known_traveler") or {})
        if traveler_record:
            status = str(traveler_record.get("status") or "Active").strip() or "Active"
            local_trips = int(traveler_record.get("local_trips_count") or traveler_record.get("local_trips") or 0)
            international_trips = int(traveler_record.get("international_trips_count") or traveler_record.get("international_trips") or 0)
            total_trips = int(traveler_record.get("total_trips") or (local_trips + international_trips))
            crm_context["traveler_summary"] = {
                "full_name": str(traveler_record.get("full_name") or "").strip(),
                "traveler_id": str(traveler_record.get("traveler_id") or "").strip(),
                "status": status,
                "local_trips": local_trips,
                "international_trips": international_trips,
                "total_trips": total_trips,
                "vip_status": status.upper() == "VIP",
            }
        return crm_context

    @staticmethod
    def _safe_refusal() -> str:
        return "I cannot save changes automatically in this step. I can only check whether the action is allowed."

    def _workflow_fallback_reply(self, session_context: dict[str, Any] | None) -> str:
        workflow = (session_context or {}).get("workflow_policy")
        workflow = workflow if isinstance(workflow, dict) else {}
        return self.sanitize_reply(str(workflow.get("assistant_message") or "").strip())

    def _workflow_block_result(self, tool_name: str, session_context: dict[str, Any] | None) -> dict[str, Any] | None:
        context_policy = (session_context or {}).get("workflow_policy")
        context_policy = context_policy if isinstance(context_policy, dict) else {}
        allowed_tools = set(context_policy.get("allowed_tools") or [])
        if not allowed_tools or tool_name in allowed_tools:
            return None
        decision = self.workflow_policy.evaluate(session_context or {})
        return self.workflow_policy.block_tool_result(tool_name, decision)

    def _route_tool_call(self, tool_name: str, session_context: dict[str, Any] | None) -> dict[str, Any]:
        decision = evaluate_tool_route(
            requested_tool_name=tool_name,
            session_context=session_context or {},
            mode=getattr(self.settings, "agent_tool_router_mode", "dry_run"),
        )
        payload = decision.to_dict()
        agent_logger.info(
            "Tool router audit mode=%s state=%s tool=%s group=%s allowed=%s would_block=%s reason=%s",
            decision.mode,
            decision.state,
            decision.requested_tool,
            decision.tool_group,
            decision.allowed,
            decision.would_block,
            decision.reason_code,
        )
        if decision.would_block:
            agent_logger.warning(
                "Tool router would block mode=%s state=%s tool=%s group=%s reason=%s",
                decision.mode,
                decision.state,
                decision.requested_tool,
                decision.tool_group,
                decision.reason_code,
            )
        return payload

    @staticmethod
    def _record_type_for_tool_name(tool_name: str) -> str:
        normalized = str(tool_name or "").strip().lower()
        if "passport" in normalized or "document" in normalized:
            return "document"
        return detect_write_record_type(normalized, {})

    @staticmethod
    def _route_decision_is_write_tool(route_decision: dict[str, Any]) -> bool:
        audit = route_decision.get("audit") if isinstance(route_decision.get("audit"), dict) else {}
        if isinstance(audit.get("write_tool_group"), bool):
            return bool(audit.get("write_tool_group"))
        return str(route_decision.get("tool_group") or "") in {
            "lead_write",
            "booking_write",
            "handoff_write",
            "passport_write",
        }

    def _write_tool_enforcement_enabled(self) -> bool:
        return bool(getattr(self.settings, "agent_write_tool_enforcement", False))

    def _should_block_write_tool_runtime(self, route_decision: dict[str, Any]) -> bool:
        if not self._write_tool_enforcement_enabled():
            return False
        if not self._route_decision_is_write_tool(route_decision):
            return False
        return bool(route_decision.get("would_block"))

    def _router_block_result(self, route_decision: dict[str, Any], *, tool_name: str = "") -> dict[str, Any]:
        customer_message_key = str(route_decision.get("safe_customer_message_key") or "request_not_ready")
        record_type = self._record_type_for_tool_name(tool_name)
        contract = {
            "status": "blocked",
            "executed": False,
            "reused": False,
            "record_type": record_type,
            "record_id": "",
            "idempotency_key": "",
            "customer_confirmation_allowed": False,
            "error_code": str(route_decision.get("reason_code") or "request_not_ready"),
            "safe_customer_message_key": customer_message_key,
            "audit": {
                "router_mode": str(route_decision.get("mode") or ""),
                "tool_group": str(route_decision.get("tool_group") or ""),
            },
        }
        return {
            "status": "router_blocked",
            "executed": False,
            "assistant_message": "I cannot save this yet. I need to confirm the required details first.",
            "customer_message_key": customer_message_key,
            "write_result": {"write_result_contract": contract},
            "write_result_contract": contract,
        }

    @staticmethod
    def _tool_events_contain_verified_crm_fact(tool_events: list[dict[str, Any]]) -> bool:
        for event in tool_events:
            result = event.get("result") if isinstance(event, dict) else {}
            if not isinstance(result, dict):
                continue
            traveler = result.get("traveler") if isinstance(result.get("traveler"), dict) else {}
            if traveler.get("traveler_id") and traveler.get("status"):
                return True
            if result.get("trip") or result.get("trips") or result.get("open_trips") or result.get("bookings"):
                return True
        return False

    @staticmethod
    def _tool_events_contain_failed_contract(tool_events: list[dict[str, Any]]) -> bool:
        for event in tool_events:
            result = event.get("result") if isinstance(event, dict) else {}
            if not isinstance(result, dict):
                continue
            if str(result.get("contract_status") or "").strip().lower() == "failed":
                return True
            if str(result.get("status") or "").strip().lower() in {"failed", "error", "router_blocked", "workflow_blocked"}:
                return True
            if isinstance(result.get("write_result_contract"), dict) and result["write_result_contract"]:
                normalized = normalize_write_result(result, backend="")
                # A missing/empty status (a known legacy contract shape) is
                # not itself a failure signal here -- only a status that IS
                # present but resolves to non-success (an explicit failure,
                # or a genuinely new, not-yet-recognized status) counts.
                if normalized.raw_status and normalized.outcome is not WriteOutcome.SUCCESS:
                    return True
        return False

    @staticmethod
    def _grounded_fact_text(session_context: dict[str, Any], tool_events: list[dict[str, Any]]) -> str:
        payload = {
            "session_context": session_context,
            "tool_results": [event.get("result") for event in tool_events if isinstance(event, dict)],
        }
        try:
            return json.dumps(payload, ensure_ascii=False, sort_keys=True).casefold()
        except Exception:
            return str(payload).casefold()

    @staticmethod
    def _collect_trip_dicts(value: Any, found: list[dict[str, Any]]) -> None:
        """Recursively collect trip-shaped dicts (have a trip_id plus a trip fact) from a nested structure."""
        if isinstance(value, dict):
            if str(value.get("trip_id") or "").strip() and any(
                key in value for key in ("trip_name", "public_price", "price", "start_date", "end_date")
            ):
                found.append(value)
            for nested in value.values():
                GeminiAgent._collect_trip_dicts(nested, found)
        elif isinstance(value, list):
            for item in value:
                GeminiAgent._collect_trip_dicts(item, found)

    @classmethod
    def _trip_fact_texts_by_scope(
        cls,
        session_context: dict[str, Any],
        tool_events: list[dict[str, Any]],
        selected_trip_id: str,
    ) -> tuple[str, str]:
        """Split every trip fact in context into (selected trip text, other trips text).

        _grounded_fact_text flattens every trip ever shown this session into one
        blob, so a price that is real for trip A also "grounds" that same price
        misattributed to trip B. Splitting by trip_id lets _ground_reply catch a
        sensitive token that is only ever real for a *different* trip.
        """
        found: list[dict[str, Any]] = []
        cls._collect_trip_dicts(session_context, found)
        for event in tool_events:
            result = event.get("result") if isinstance(event, dict) else None
            cls._collect_trip_dicts(result, found)
        selected = [trip for trip in found if str(trip.get("trip_id") or "").strip() == selected_trip_id]
        other = [trip for trip in found if str(trip.get("trip_id") or "").strip() != selected_trip_id]

        def _dump(trips: list[dict[str, Any]]) -> str:
            if not trips:
                return ""
            try:
                return json.dumps(trips, ensure_ascii=False, sort_keys=True).casefold()
            except Exception:
                return ""

        return _dump(selected), _dump(other)

    @staticmethod
    def _sensitive_fact_tokens(reply: str) -> list[str]:
        text = str(reply or "")
        patterns = (
            r"\b(?:TR|RT|BK|LD|LEAD|BOOKING|HANDOFF|HF|HND)[-_]?[A-Z0-9]{2,}\b",
            r"(?:\$|USD\s*|EGP\s*)\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s*(?:USD|EGP)\b",
            r"\b\d{4}-\d{2}-\d{2}\b",
            r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2}\b",
        )
        tokens: list[str] = []
        seen: set[str] = set()
        for pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                token = match.group(0).strip()
                folded = token.casefold()
                if folded not in seen:
                    seen.add(folded)
                    tokens.append(token)
        return tokens

    @staticmethod
    def _is_price_or_date_token(token: str) -> bool:
        return bool(re.match(r"^(?:\$|USD|EGP|\d)", token.strip(), re.IGNORECASE)) and not re.match(
            r"^(?:TR|RT|BK|LD|LEAD|BOOKING|HANDOFF|HF|HND)[-_]", token.strip(), re.IGNORECASE
        )

    @staticmethod
    def _contains_unverified_crm_claim(reply: str) -> bool:
        text = str(reply or "").casefold()
        patterns = (
            r"\bvip\b",
            r"\bactive\b",
            r"\bblocked\b",
            r"\bfound\b",
            r"\bconfirmed\b",
            r"\bavailable\b",
            r"\bbooking\b",
            r"\btraveler id\b",
            r"\bstatus\b",
            r"\bprice\b",
            r"\bplaces?\b",
            r"\btr\d{3,}\b",
            r"\brt-[a-z0-9-]+\b",
            r"\$",
        )
        return any(re.search(pattern, text) for pattern in patterns)

    @staticmethod
    def _reply_is_trusted_tool_message(reply: str, tool_events: list[dict[str, Any]]) -> bool:
        cleaned = str(reply or "").strip()
        if not cleaned:
            return False
        for event in tool_events:
            if not isinstance(event, dict):
                continue
            candidate = str(event.get("assistant_message") or "").strip()
            if candidate and candidate == cleaned:
                return True
        return False

    def _ground_reply(
        self,
        *,
        reply: str,
        session_context: dict[str, Any],
        tool_events: list[dict[str, Any]],
    ) -> str:
        for event in tool_events:
            result = event.get("result") if isinstance(event, dict) else {}
            if isinstance(result, dict) and result.get("status") == "privacy_blocked":
                return str(result.get("assistant_message") or AgentPrivacyPolicy.EN_RESPONSE).strip()
        workflow = session_context.get("workflow_policy") if isinstance(session_context.get("workflow_policy"), dict) else {}
        grounded_text = self._grounded_fact_text(session_context, tool_events)
        sensitive_tokens = self._sensitive_fact_tokens(reply)
        ungrounded_tokens = [token for token in sensitive_tokens if token.casefold() not in grounded_text]

        selected_trip_id = str(session_context.get("selected_trip_id") or "").strip()
        if selected_trip_id and not ungrounded_tokens:
            selected_trip_text, other_trip_text = self._trip_fact_texts_by_scope(
                session_context, tool_events, selected_trip_id
            )
            if other_trip_text:
                for token in sensitive_tokens:
                    if not self._is_price_or_date_token(token):
                        continue
                    folded = token.casefold()
                    # Grounded overall, but only via a different trip's own
                    # data — the customer's selected trip never actually said
                    # this, so a wrong-trip price/date must not slip through.
                    if folded in other_trip_text and folded not in selected_trip_text:
                        ungrounded_tokens.append(token)

        if ungrounded_tokens:
            return str(
                workflow.get("assistant_message")
                or "I need to verify that information in the CRM before I can confirm it. Please share the missing detail, or I can connect you with a human agent."
            ).strip()
        if self._reply_is_trusted_tool_message(reply, tool_events):
            # The reply is a tool executor's own deterministic message (e.g. "I
            # still need: WhatsApp number or traveler ID."), not model output -
            # words like "traveler id" inside it are a request, not a claim, so
            # the unverified-CRM-claim heuristic below must not override it.
            return reply
        if self._tool_events_contain_failed_contract(tool_events) and self._contains_unverified_crm_claim(reply):
            return str(
                workflow.get("assistant_message")
                or "I could not verify that information right now. Please try again, or I can connect you with a human agent."
            ).strip()
        if not workflow or workflow.get("identity_verified"):
            return reply
        if self._tool_events_contain_verified_crm_fact(tool_events):
            return reply
        if not self._contains_unverified_crm_claim(reply):
            return reply
        return str(
            workflow.get("assistant_message")
            or "Please share your WhatsApp number first so I can check your Ravel Traveler profile safely."
        ).strip()

    @staticmethod
    def sanitize_reply(text: str) -> str:
        cleaned = re.sub(r"\{[a-zA-Z0-9_]+\}", "", str(text or ""))
        return format_agent_reply(cleaned)

    @staticmethod
    def _contains_internal_instruction_leak(reply: str) -> bool:
        """Reject backend/prompt fragments before they reach a customer."""

        normalized = str(reply or "").casefold()
        markers = (
            "assistant_message",
            "workflow_policy",
            "required_step",
            "customer_message_key",
            "allowed_tools",
            "workflow_blocked",
            "tool_result",
            "validator reasons",
            "do not improvise around",
            "let's look at the",
        )
        return any(marker in normalized for marker in markers)

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
    def _finish_reason(raw_response: dict[str, Any], fallback: str = "") -> str:
        candidates = raw_response.get("candidates") or []
        if candidates and isinstance(candidates[0], dict):
            reason = str(candidates[0].get("finishReason") or candidates[0].get("finish_reason") or "").strip()
            if reason:
                return reason
        return str(fallback or "").strip()

    @staticmethod
    def _token_limit_finish(reason: str) -> bool:
        return str(reason or "").strip().upper() in {"MAX_TOKENS", "LENGTH"}

    @staticmethod
    def _completion_fallback(language: str) -> str:
        if str(language or "").startswith("ar"):
            return "\u0645\u0639\u0644\u0634\u060c \u0645\u0642\u062f\u0631\u062a\u0634 \u0623\u062c\u0647\u0632 \u0627\u0644\u0631\u062f \u0628\u0634\u0643\u0644 \u0635\u062d\u064a\u062d. \u0645\u0645\u0643\u0646 \u062a\u0628\u0639\u062a \u0637\u0644\u0628\u0643 \u0645\u0631\u0629 \u062a\u0627\u0646\u064a\u0629\u061f"
        return "Sorry, I couldn\u2019t prepare that response properly. Could you try that again?"

    def _regenerate_complete_reply(
        self,
        *,
        package,
        session_context: dict[str, Any],
        user_message: str,
        partial_reply: str,
        reason: str,
    ) -> tuple[str, str, str]:
        language = str(session_context.get("language") or _detect_language(user_message))
        payload = {
            "task": "replace_incomplete_customer_reply",
            "language": language,
            "user_message": user_message,
            "session_context": session_context,
            "partial_incomplete_reply": partial_reply,
            "failure_reason": reason,
            "instructions": [
                "Return one complete concise replacement response.",
                "Do not append to the partial reply.",
                "Do not repeat duplicate content from the partial reply.",
                "Use only the supplied session context and tool results.",
                "Plain text only. No Markdown emphasis.",
                "Complete every sentence, question, and list item.",
            ],
        }
        try:
            response = self.provider.generate(
                system_prompt=package.system_prompt,
                messages=[{"role": "user", "parts": [{"text": json.dumps(payload, ensure_ascii=False)}]}],
                tools=[],
                # No tools are offered on this call, so there is no function-call
                # thought signature to preserve (unlike _run_tool_loop's shared
                # config). Turning thinking off here means the whole budget goes
                # to the visible reply, instead of hidden reasoning tokens
                # silently eating it and hitting MAX_TOKENS a second time in a
                # row with an even smaller cap than the call it was recovering
                # from.
                generation_config={
                    "temperature": 0.2,
                    "topP": 0.9,
                    "maxOutputTokens": 512,
                    "thinkingConfig": {"thinkingBudget": 0},
                },
                request_id=f"{package.prompt_id}-completion-retry",
            )
        except GeminiProviderError as exc:
            agent_logger.warning("Final response regeneration unavailable prompt_id=%s reason=%s error=%s", package.prompt_id, reason, exc)
            return self._completion_fallback(language), "", "regeneration_error"
        replacement = self.sanitize_reply(self._extract_reply(response.text))
        finish_reason = self._finish_reason(response.raw, response.finish_reason)
        issue = "token_limit_finish" if self._token_limit_finish(finish_reason) else response_completeness_issue(replacement)
        if issue:
            agent_logger.warning(
                "Final response regeneration rejected prompt_id=%s reason=%s retry_finish_reason=%s",
                package.prompt_id,
                issue,
                finish_reason,
            )
            return self._completion_fallback(language), response.response_id, issue
        return replacement, response.response_id, ""

    def _ensure_complete_final_reply(
        self,
        *,
        package,
        session_context: dict[str, Any],
        user_message: str,
        reply_text: str,
        response,
    ) -> tuple[str, str, str]:
        finish_reason = self._finish_reason(response.raw, getattr(response, "finish_reason", ""))
        issue = "token_limit_finish" if self._token_limit_finish(finish_reason) else response_completeness_issue(reply_text)
        if not issue:
            return reply_text, getattr(response, "response_id", ""), ""
        agent_logger.warning(
            "Final response validation failed prompt_id=%s response_id=%s reason=%s finish_reason=%s",
            package.prompt_id,
            getattr(response, "response_id", ""),
            issue,
            finish_reason,
        )
        replacement, response_id, retry_issue = self._regenerate_complete_reply(
            package=package,
            session_context=session_context,
            user_message=user_message,
            partial_reply=reply_text,
            reason=issue,
        )
        return replacement, response_id or getattr(response, "response_id", ""), retry_issue or issue

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
            "call_id": str(call.get("id") or call.get("callId") or "").strip(),
            "thought_signature_present": bool(part.get("thoughtSignature") or part.get("thought_signature")),
            "thought_signature_preserved": bool(part.get("thoughtSignature") or part.get("thought_signature")),
            "raw": call,
            "raw_part": deepcopy(part),
        }

    def _validate_tool_call(self, name: str, args: dict[str, Any]) -> ToolSpec:
        spec = self.tool_registry.get(name)
        if spec is None:
            raise GeminiToolLoopError(f"Unsupported tool requested: {name}")

        validation = validate_tool_input_contract(spec, args)
        if not validation.ok:
            raise GeminiToolLoopError(f"Invalid tool input for {name}: {validation.reason_code}.")
        return spec

    def _safe_execute_tool(
        self,
        spec: ToolSpec,
        args: dict[str, Any],
        session_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        validation = validate_tool_input_contract(spec, args)
        if not validation.ok:
            return safe_tool_input_error_result(
                spec=spec,
                reason_code=validation.reason_code,
                errors=validation.errors,
                session_context=session_context,
            )
        try:
            raw_result = self._execute_tool(spec.name, args, session_context)
        except Exception as exc:
            agent_logger.warning("Tool execution failed tool=%s reason=%s", spec.name, type(exc).__name__)
            return normalize_tool_result_contract(
                spec=spec,
                exception=exc,
                session_context=session_context,
            )
        return normalize_tool_result_contract(
            spec=spec,
            result=raw_result,
            session_context=session_context,
        )

    def _execute_tool(self, name: str, args: dict[str, Any], session_context: dict[str, Any] | None = None) -> dict[str, Any]:
        privacy_block = self.privacy_policy.guard_tool_call(name, args, session_context or {})
        if privacy_block is not None:
            return privacy_block
        if name == "search_traveler":
            return self.read_only_tools.search_traveler(
                raw_phone=str(args.get("raw_phone") or ""),
                country_code=str(args.get("country_code") or self.settings.default_country_code or ""),
            )
        if name == "find_traveler_by_phone":
            return self.read_only_tools.find_traveler_by_phone(
                raw_phone=str(args.get("raw_phone") or ""),
                country_code=str(args.get("country_code") or self.settings.default_country_code or ""),
            )
        if name == "get_traveler_profile":
            return self.read_only_tools.get_traveler_profile_safe(
                traveler_id=str(args.get("traveler_id") or ""),
                raw_phone=str(args.get("raw_phone") or ""),
                country_code=str(args.get("country_code") or self.settings.default_country_code or ""),
            )
        if name == "get_traveler_trip_history":
            return self.read_only_tools.get_traveler_trip_history(
                traveler_id=str(args.get("traveler_id") or ""),
                raw_phone=str(args.get("raw_phone") or ""),
                country_code=str(args.get("country_code") or self.settings.default_country_code or ""),
            )
        if name == "search_trips":
            return self.read_only_tools.search_trips(
                trip_type=str(args.get("trip_type") or ""),
                query=str(args.get("query") or ""),
            )
        if name == "search_available_trips":
            return self.read_only_tools.search_available_trips(
                trip_type=str(args.get("trip_type") or ""),
                destination=str(args.get("destination") or ""),
                query=str(args.get("query") or ""),
                preferred_date=str(args.get("preferred_date") or ""),
                travelers=str(args.get("travelers") or args.get("group_size") or ""),
                flight_option=str(args.get("flight_option") or ""),
                room_type=str(args.get("room_type") or ""),
            )
        if name == "get_trip_details":
            return self.read_only_tools.get_trip_details(trip_id=str(args.get("trip_id") or ""))
        if name == "get_trip_media":
            return self.read_only_tools.get_trip_media(trip_id=str(args.get("trip_id") or ""))
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
        if name == "lookup_lead":
            return self.read_only_tools.lookup_lead(
                lead_id=str(args.get("lead_id") or ""),
                traveler_id=str(args.get("traveler_id") or ""),
                raw_phone=str(args.get("raw_phone") or ""),
                country_code=str(args.get("country_code") or self.settings.default_country_code or ""),
            )
        if name == "validate_business_action":
            action = str(args.get("action") or "").strip()
            return self.action_validator.validate_action(
                action=action,
                payload=args,
                session_context=session_context or {},
            ).to_dict()
        if self.write_executor and name in {"create_lead", "update_lead_stage", "create_booking_draft", "create_handoff"}:
            return self.write_executor.execute(
                action=name,
                payload=args,
                session_context=session_context or {},
            )
        raise GeminiToolLoopError(f"Unsupported tool requested: {name}")

    @staticmethod
    def _last_write_result_for_guard(tool_events: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
        for event in reversed(tool_events):
            if not isinstance(event, dict) or not event.get("write"):
                continue
            result = event.get("result") if isinstance(event.get("result"), dict) else {}
            write_result = result.get("write_result_contract") if isinstance(result.get("write_result_contract"), dict) and result.get("write_result_contract") else None
            if write_result is None and isinstance(result.get("write_result"), dict):
                write_result = result["write_result"]
            if write_result is None:
                write_result = result
            record_type = detect_write_record_type(str(event.get("name") or ""), write_result)
            if record_type != "write":
                return write_result, record_type
        return None, ""

    @staticmethod
    def _guard_final_customer_reply(
        reply_text: str,
        *,
        tool_events: list[dict[str, Any]],
        language: str,
        fallback_message_key: str = "general",
        allow_inventory_counts: bool = False,
        session_context: dict[str, Any] | None = None,
    ) -> str:
        write_result, record_type = GeminiAgent._last_write_result_for_guard(tool_events)
        guarded = guard_customer_response(
            reply_text,
            language=language,
            write_result=write_result,
            record_type=record_type,
            fallback_message_key=fallback_message_key or record_type or "general",
            allow_inventory_counts=allow_inventory_counts,
            known_record_ids=known_record_ids_from_context(session_context),
        )
        if guarded.fallback_used:
            agent_logger.warning(
                "Gemini response guard fallback reason=%s terms=%s",
                guarded.reason_code,
                ",".join(guarded.blocked_terms_found),
            )
        return guarded.message

    @staticmethod
    def _gate_reply_with_write_results(reply_text: str, tool_events: list[dict[str, Any]], language: str) -> str:
        gated = str(reply_text or "")
        for event in reversed(tool_events):
            if not isinstance(event, dict) or not event.get("write"):
                continue
            result = event.get("result") if isinstance(event.get("result"), dict) else {}
            write_result = result.get("write_result_contract") if isinstance(result.get("write_result_contract"), dict) and result.get("write_result_contract") else None
            if write_result is None and isinstance(result.get("write_result"), dict):
                write_result = result["write_result"]
            if write_result is None:
                write_result = result
            record_type = detect_write_record_type(str(event.get("name") or ""), write_result)
            if record_type == "write":
                continue
            return gate_customer_write_reply(
                proposed_reply=gated,
                write_result=write_result,
                record_type=record_type,
                language=language,
            )
        return gated

    @staticmethod
    def _tool_result_summary(result: dict[str, Any]) -> dict[str, Any]:
        summary: dict[str, Any] = {"type": type(result).__name__}
        if isinstance(result, dict):
            summary["keys"] = sorted(result.keys())
            for key in (
                "match_status",
                "handoff_required",
                "status",
                "workflow_state",
                "required_step",
                "traveler",
                "trips",
                "open_trips",
                "bookings",
                "leads",
                "documents",
                "decision",
                "executed",
                "result_id",
                "lead_id",
                "booking_id",
                "handoff_id",
            ):
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
    def _tool_response_part(name: str, result: dict[str, Any], call_id: str = "") -> dict[str, Any]:
        response: dict[str, Any] = {"name": name, "response": result}
        if call_id:
            response["id"] = call_id
        return {"functionResponse": response}

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
                if not reply_text and tool_events:
                    for event in reversed(tool_events):
                        reply_candidate = str(event.get("assistant_message") or event.get("reply") or "").strip()
                        if reply_candidate:
                            reply_text = reply_candidate
                            break
                if not reply_text:
                    workflow = session_context.get("workflow_policy") if isinstance(session_context.get("workflow_policy"), dict) else {}
                    reply_text = str(workflow.get("assistant_message") or "").strip()
                language = str(session_context.get("language") or _detect_language(user_message) or "en")
                reply_text = self.sanitize_reply(reply_text)
                reply_text = self._gate_reply_with_write_results(
                    reply_text,
                    tool_events,
                    language,
                )
                if self._contains_internal_instruction_leak(reply_text):
                    reply_text = self._workflow_fallback_reply(session_context)
                fallback_message_key = (
                    str((session_context.get("workflow_policy") or {}).get("message_key") or "general")
                    if isinstance(session_context.get("workflow_policy"), dict)
                    else "general"
                )
                allow_inventory_counts = self._tool_events_contain_verified_crm_fact(tool_events)
                reply_text = self._guard_final_customer_reply(
                    reply_text,
                    tool_events=tool_events,
                    language=language,
                    fallback_message_key=fallback_message_key,
                    allow_inventory_counts=allow_inventory_counts,
                    session_context=session_context,
                )
                if self._contains_internal_instruction_leak(reply_text):
                    reply_text = self._workflow_fallback_reply(session_context)
                if not reply_text:
                    reply_text = self._safe_refusal()
                reply_text, final_response_id, completion_issue = self._ensure_complete_final_reply(
                    package=package,
                    session_context=session_context,
                    user_message=user_message,
                    reply_text=reply_text,
                    response=response,
                )
                reply_text = self._guard_final_customer_reply(
                    reply_text,
                    tool_events=tool_events,
                    language=language,
                    fallback_message_key=fallback_message_key,
                    allow_inventory_counts=allow_inventory_counts,
                    session_context=session_context,
                )
                memory_key = self._memory_key(session_context)
                self._memory.setdefault(memory_key, []).extend(
                    [
                        {"role": "user", "text": user_message},
                        {"role": "assistant", "text": reply_text},
                    ]
                )
                agent_logger.info(
                    "Gemini final answer prompt_id=%s response_id=%s tool_calls=%s final_answer_len=%s",
                    package.prompt_id,
                    final_response_id or response.response_id,
                    tool_call_count,
                    len(reply_text),
                )
                return {
                    "reply": reply_text,
                    "tool_requests": tool_events,
                    "write_results": [event for event in tool_events if event.get("write")],
                    "mode": self.settings.ai_agent_mode,
                    "prompt_id": package.prompt_id,
                    "response_id": final_response_id or response.response_id,
                    "elapsed_ms": None,
                    "usage_metadata": response.usage_metadata or {},
                    "completion_issue": completion_issue,
                    "finish_reason": self._finish_reason(response.raw, response.finish_reason),
                }

            for call in function_calls:
                tool_call_count += 1
                if tool_call_count > self.max_tool_calls:
                    raise GeminiToolLoopError("Maximum Gemini tool-call limit reached.")
                safety_ok, safety_error = self.safety_layer.validate_tool_args(
                    call["name"],
                    call.get("args") or {},
                    allowed_tools=set(self.tool_registry.keys()),
                )
                if not safety_ok:
                    raise GeminiToolLoopError(safety_error)
                spec = self._validate_tool_call(call["name"], call.get("args") or {})
                if not bool(call.get("thought_signature_present")):
                    raise GeminiToolLoopError(f"Missing thought signature for function call: {spec.name}")

                tool_input = call.get("args") or {}
                thought_signature_present = bool(call.get("thought_signature_present"))
                call_id = str(call.get("call_id") or "").strip()
                agent_logger.info(
                    "Gemini tool requested prompt_id=%s model=%s round=%s tool=%s call_id_present=%s thought_signature_present=%s input=%s",
                    package.prompt_id,
                    getattr(self.settings, "gemini_model", ""),
                    tool_call_count,
                    spec.name,
                    bool(call_id),
                    thought_signature_present,
                    json.dumps(tool_input, ensure_ascii=False, sort_keys=True),
                )
                route_decision = self._route_tool_call(spec.name, session_context)
                result = None
                if self._should_block_write_tool_runtime(route_decision):
                    agent_logger.warning(
                        "Runtime write-only enforcement blocked tool=%s state=%s group=%s reason=%s",
                        spec.name,
                        route_decision.get("state"),
                        route_decision.get("tool_group"),
                        route_decision.get("reason_code"),
                    )
                    result = self._router_block_result(route_decision, tool_name=spec.name)
                if result is None and should_enforce_tool_route(
                    evaluate_tool_route(
                        requested_tool_name=spec.name,
                        session_context=session_context,
                        mode=route_decision.get("mode"),
                    )
                ):
                    result = self._router_block_result(route_decision, tool_name=spec.name)
                if result is None:
                    result = self._workflow_block_result(spec.name, session_context)
                if result is None:
                    result = self._safe_execute_tool(spec, tool_input, session_context)
                else:
                    result = normalize_tool_result_contract(
                        spec=spec,
                        result=result,
                        session_context=session_context,
                    )
                summary = self._tool_result_summary(result)
                agent_logger.info(
                    "Gemini tool result prompt_id=%s model=%s round=%s tool=%s call_id_present=%s thought_signature_preserved=%s summary=%s",
                    package.prompt_id,
                    getattr(self.settings, "gemini_model", ""),
                    tool_call_count,
                    spec.name,
                    bool(call_id),
                    thought_signature_present,
                    json.dumps(summary, ensure_ascii=False, sort_keys=True),
                )
                tool_events.append(
                    {
                        "name": spec.name,
                        "input": deepcopy(tool_input),
                        "summary": summary,
                        "write": bool(spec.allowed_write),
                        "result": deepcopy(result),
                        "tool_route": deepcopy(route_decision),
                        "assistant_message": str(result.get("assistant_message") or "").strip(),
                        "executed": bool(result.get("executed", spec.allowed_write is False)),
                        "session_update": deepcopy(result.get("session_update")) if isinstance(result.get("session_update"), dict) else None,
                        "thought_signature_present": thought_signature_present,
                        "thought_signature_preserved": thought_signature_present,
                        "call_id": call_id,
                        "raw_model_part": deepcopy(call.get("raw_part") or {}),
                    }
                )
                contents.append({"role": "model", "parts": deepcopy(parts)})
                contents.append({"role": "user", "parts": [self._tool_response_part(spec.name, result, call_id=call_id)]})

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
        session_context = session_context or {}
        crm_context = self._collect_crm_context(session_context)
        history = self._conversation_history(
            session_context=session_context,
            provided_history=session_context.get("conversation_history") if isinstance(session_context.get("conversation_history"), list) else None,
        )
        package = self.rewrite_prompt_builder.build_rewrite_prompt(
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
        reply = self.sanitize_reply(str(result.get("reply") or "").strip())
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
            fallback_reply = ""
            if "Maximum Gemini tool-call limit reached" in str(exc):
                fallback_reply = self._workflow_fallback_reply(session_context)
            return {
                "reply": fallback_reply or self._safe_refusal(),
                "tool_requests": [],
                "mode": self.settings.ai_agent_mode,
                "error": "" if fallback_reply else "tool_request_failed",
                "warning": "tool_request_failed" if fallback_reply else "",
            }
        except GeminiProviderError as exc:
            agent_logger.warning("Gemini chat unavailable prompt_id=%s error=%s", package.prompt_id, exc)
            return {
                "reply": self._safe_refusal(),
                "tool_requests": [],
                "mode": self.settings.ai_agent_mode,
                "error": "provider_unavailable",
            }

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        reply = self.sanitize_reply(str(result.get("reply") or "").strip()) or self._safe_refusal()
        reply = self._ground_reply(
            reply=reply,
            session_context=session_context,
            tool_events=list(result.get("tool_requests", []) or []),
        )
        final_payload = {
            "reply": reply,
            "tool_requests": result.get("tool_requests", []),
            "write_results": result.get("write_results", []),
            "mode": self.settings.ai_agent_mode,
            "prompt_id": result.get("prompt_id"),
            "response_id": result.get("response_id"),
            "elapsed_ms": elapsed_ms,
            "usage_metadata": result.get("usage_metadata") or {},
        }
        agent_logger.info(
            "Gemini chat final prompt_id=%s response_id=%s elapsed_ms=%s final_answer_len=%s",
            result.get("prompt_id") or "",
            result.get("response_id") or "",
            elapsed_ms,
            len(reply),
        )
        return final_payload
