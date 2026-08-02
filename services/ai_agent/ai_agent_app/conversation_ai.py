from __future__ import annotations

import json
import re
from typing import Any
from urllib import error, parse, request

from services.ai_agent.ai_agent_app.logger import agent_logger


DEFAULT_AGENT_CONVERSATION_PROMPT = (
    "You are Ravel Agent, Ravel Traveler's warm, professional sales assistant. "
    "Rewrite the approved operational message into a natural chat reply with a human persona, "
    "while preserving the exact application flow and business meaning. "
    "Never invent trips, prices, availability, IDs, dates, payment facts, or CRM facts. "
    "Never ask for typed passport details. For international trips, only ask for the passport attachment upload when required. "
    "Never ask the traveler whether they want to pay now or confirm a deposit. "
    "Do not imply flights are included by default; most trips are offered without flights unless the base message clearly says otherwise. "
    "Never invent VIP discounts, group discounts, or visa facts. "
    "If the customer greets you, greet them briefly and continue the same required step. "
    "If the customer asks what you mean or asks for clarification, explain the current request briefly in simple chat language and then ask for the same next step. "
    "If the customer repeats the same clarification, do not repeat the exact same wording. Explain more simply and keep the same required step. "
    "If the customer has a privacy concern, explain that the information is used to check or create their Ravel Traveler profile safely. "
    "If the customer asks who created you or who built the assistant, say that you were created by nanovate.io for Ravel Traveler and do not attribute the assistant to any model provider. "
    "If the customer says something off-track, answer briefly and steer back to the required next action. "
    "Understand small typos and natural customer phrases only inside the current workflow step. "
    "Keep replies concise and suitable for WhatsApp-style chat. "
    "Return only the final assistant message."
)


class GeminiConversationAI:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        system_prompt: str = "",
        timeout_seconds: float = 2.5,
    ) -> None:
        self.api_key = api_key.strip()
        self.model = self._normalize_model_name(model)
        self.system_prompt = system_prompt.strip() or DEFAULT_AGENT_CONVERSATION_PROMPT
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _normalize_model_name(model: str) -> str:
        cleaned = (model or "").strip()
        if cleaned.startswith("models/"):
            cleaned = cleaned.split("/", 1)[1].strip()
        if " " in cleaned:
            cleaned = re.sub(r"\s+", "-", cleaned.casefold())
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
        if not self.api_key or not self.model or not base_text.strip():
            return None

        context = session_context or {}
        prompt = {
            "task": "Rewrite the base assistant message into a more natural chat reply without changing its meaning.",
            "message_key": message_key,
            "language": language,
            "required_action": required_action,
            "last_user_message": user_text,
            "session_context": context,
            "base_message": base_text,
            "rules": [
                "Preserve every factual detail already present in the base message.",
                "The base message and required_action are the source of truth.",
                "Do not change the workflow step.",
                "Do not add or remove prices, dates, trip names, IDs, room counts, or payment facts.",
                "If passport upload is required, ask only for the attachment upload and do not ask for typed passport fields.",
                "Do not ask the traveler whether they want to pay now or confirm a deposit.",
                "Do not imply flights are included by default unless the base message explicitly says that.",
                "Do not invent any VIP discount, group discount, or visa requirement.",
                "If the user's message is a greeting, greet them briefly and continue the required action.",
                "If the user's message is asking for clarification, explain the current request briefly and then repeat the same required next action.",
                "If persona_intent_repeat_count is greater than 1, do not repeat the previous wording.",
                "If the user's message is a privacy concern, explain the safety/profile reason briefly and continue the required action.",
                "If the user asks who created you or who built the assistant, say that you were created by nanovate.io for Ravel Traveler and do not attribute the assistant to any model provider.",
                "Understand small typos such as loca for local and intl for international, but do not invent options.",
                "Natural phrases such as okay I need it can mean interest in the single offered trip.",
                "Never select between multiple trips unless the operational layer has identified a single safe option.",
                "For any phone-number step, the final reply must include the phrase WhatsApp number.",
                "Return a complete sentence, not a fragment.",
                "Stay concise and conversational.",
            ],
        }
        payload = {
            "system_instruction": {
                "parts": [{"text": self.system_prompt}],
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": json.dumps(prompt, ensure_ascii=False)}],
                }
            ],
            "generationConfig": {
                "temperature": 0.25,
                "topP": 0.9,
                "maxOutputTokens": 512,
            },
        }

        encoded_model = parse.quote(self.model, safe="")
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{encoded_model}:generateContent"
            f"?key={parse.quote(self.api_key, safe='')}"
        )
        req = request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            agent_logger.warning("Gemini rewrite HTTP error for %s: %s %s", message_key, exc.code, body)
            return None
        except Exception as exc:
            agent_logger.warning("Gemini rewrite failed for %s: %s", message_key, exc)
            return None

        try:
            data = json.loads(raw)
            candidates = data.get("candidates") or []
            parts = (((candidates[0] or {}).get("content") or {}).get("parts") or []) if candidates else []
            text = "\n".join(str(part.get("text") or "").strip() for part in parts if part.get("text"))
            cleaned = text.strip()
            return cleaned or None
        except Exception as exc:
            agent_logger.warning("Gemini rewrite parse failed for %s: %s", message_key, exc)
            return None
