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
            "available_tools": list(self.tool_registry.keys()),
            "output_contract": {
                "type": "plain_text",
                "rules": [
                    "Preserve the meaning of the base_message.",
                    "Do not invent facts.",
                    "Do not change the workflow step.",
                    "Keep the reply concise and conversational.",
                ],
            },
        }
        return PromptPackage(
            prompt_id=prompt_id,
            system_prompt=self.system_prompt,
            messages=[{"role": "user", "parts": [{"text": json.dumps(payload, ensure_ascii=False)}]}],
            tools=[self._tool_schema(spec) for spec in self.tool_registry.values()],
            generation_config={"temperature": 0.25, "topP": 0.9, "maxOutputTokens": 512},
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
            generation_config={"temperature": 0.35, "topP": 0.9, "maxOutputTokens": 768},
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
