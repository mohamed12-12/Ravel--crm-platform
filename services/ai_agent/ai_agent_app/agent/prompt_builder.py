"""Used by GeminiAgent (gemini_agent.py) to build a one-off prompt asking
Gemini to rephrase a canned/base reply naturally, in the customer's own
language and conversation context -- not the main tool-calling prompt
loop itself, just the rewrite step for otherwise-robotic canned text.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from services.ai_agent.ai_agent_app.agent.tool_registry import ToolSpec


@dataclass(frozen=True)
class PromptPackage:
    prompt_id: str
    system_prompt: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    generation_config: dict[str, Any]


class PromptBuilder:
    def __init__(self, *, system_prompt: str, tool_registry: dict[str, ToolSpec]) -> None:
        self.system_prompt = system_prompt.strip()
        self.tool_registry = tool_registry

    def build_rewrite_prompt(
        self,
        *,
        message_key: str,
        base_text: str,
        language: str,
        required_action: str,
        session_context: dict[str, Any] | None = None,
        conversation_history: list[dict[str, Any]] | None = None,
        crm_context: dict[str, Any] | None = None,
        user_text: str = "",
    ) -> PromptPackage:
        prompt_id = uuid.uuid4().hex
        payload = {
            "task": "rewrite_base_message",
            "message_key": message_key,
            "language": language,
            "required_action": required_action,
            "user_text": user_text,
            "session_context": session_context or {},
            "conversation_history": conversation_history or [],
            "crm_context": crm_context or {},
            "base_message": base_text,
            "grounding_policy": {
                "crm_is_source_of_truth": True,
                "workflow_policy_is_authoritative": True,
                "never_infer_traveler_status_from_model_text": True,
                "only_state_trip_facts_from_crm_context_or_tool_results": True,
                "only_quote_itinerary_inclusions_exclusions_from_trip_program_fields": True,
                "if_program_detail_missing_say_unlisted_in_crm": True,
                "only_quote_room_price_for_selected_room_type_and_currency": True,
                "do_not_dump_full_room_pricing_matrix_unless_customer_asks": True,
                "do_not_mix_customer_preferences_with_verified_crm_facts": True,
            },
            "available_tools": list(self.tool_registry.keys()),
            "output_contract": {
                "type": "plain_text",
                "rules": [
                    "Preserve the meaning of the base_message.",
                    "Do not invent facts.",
                    "Do not change the workflow step.",
                    "Keep the reply concise and conversational.",
                    "Use plain text only: no Markdown emphasis, asterisks, backticks, or code fences.",
                    "Put each list item on its own line and use 1), 2), 3) numbering.",
                    "Ask one question per turn.",
                    "Complete every sentence, question, and list item.",
                    "For Arabic replies, use Arabic wording except exact names, IDs, dates, prices, phones, and URLs.",
                    "Match the active language exactly; do not switch to English unless the user explicitly asks for English.",
                ],
            },
        }
        return PromptPackage(
            prompt_id=prompt_id,
            system_prompt=self.system_prompt,
            messages=[{"role": "user", "parts": [{"text": json.dumps(payload, ensure_ascii=False)}]}],
            tools=[self._tool_schema(spec) for spec in self.tool_registry.values()],
            generation_config={"temperature": 0.25, "topP": 0.9, "maxOutputTokens": 1024},
        )

    def build_chat_prompt(
        self,
        *,
        user_message: str,
        language: str,
        session_context: dict[str, Any] | None = None,
        conversation_history: list[dict[str, Any]] | None = None,
        crm_context: dict[str, Any] | None = None,
    ) -> PromptPackage:
        prompt_id = uuid.uuid4().hex
        payload = {
            "task": "chat_response",
            "language": language,
            "user_message": user_message,
            "session_context": session_context or {},
            "conversation_history": conversation_history or [],
            "crm_context": crm_context or {},
            "grounding_policy": {
                "crm_is_source_of_truth": True,
                "workflow_policy_is_authoritative": True,
                "never_infer_traveler_status_from_model_text": True,
                "only_state_trip_facts_from_crm_context_or_tool_results": True,
                "only_quote_itinerary_inclusions_exclusions_from_trip_program_fields": True,
                "if_program_detail_missing_say_unlisted_in_crm": True,
                "only_quote_room_price_for_selected_room_type_and_currency": True,
                "do_not_dump_full_room_pricing_matrix_unless_customer_asks": True,
                "do_not_mix_customer_preferences_with_verified_crm_facts": True,
                "if_workflow_blocks_a_tool_use_the_blocked_result_message": True,
                "plain_text_customer_format": True,
                "numbered_lists_use_parenthesis": True,
                "one_question_per_turn": True,
                "complete_final_response_required": True,
                "arabic_replies_use_arabic_except_exact_dynamic_values": True,
                "maintain_active_language_for_clarifications": True,
            },
            "available_tools": list(self.tool_registry.keys()),
            "output_contract": {
                "type": "json",
                "properties": {
                    "reply": {"type": "string"},
                    "tool_requests": {"type": "array"},
                },
            },
        }
        return PromptPackage(
            prompt_id=prompt_id,
            system_prompt=self.system_prompt,
            messages=[{"role": "user", "parts": [{"text": json.dumps(payload, ensure_ascii=False)}]}],
            tools=[self._tool_schema(spec) for spec in self.tool_registry.values()],
            generation_config={"temperature": 0.35, "topP": 0.9, "maxOutputTokens": 1536},
        )

    @staticmethod
    def _tool_schema(spec: ToolSpec) -> dict[str, Any]:
        return {
            "functionDeclarations": [
                {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.input_schema,
                }
            ]
        }
