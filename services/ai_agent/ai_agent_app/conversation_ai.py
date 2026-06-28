from __future__ import annotations

import json
from typing import Any
from urllib import error, parse, request

from services.ai_agent.ai_agent_app.logger import agent_logger


DEFAULT_AGENT_CONVERSATION_PROMPT = (
    "You are the conversational layer for Rahma Traveler's sales agent. "
    "Rewrite the approved operational message so it sounds natural, warm, and human in chat, "
    "while preserving the exact business meaning. "
    "Never invent trips, prices, availability, IDs, dates, payment facts, or CRM facts. "
    "Never ask for typed passport details. For international trips, only ask for the passport attachment upload when required. "
    "Never ask the traveler whether they want to pay now or confirm a deposit. "
    "Do not imply flights are included by default; most trips are offered without flights unless the base message clearly says otherwise. "
    "Never invent VIP discounts, group discounts, or visa facts. "
    "If the customer asks what you mean or asks for clarification, explain the current request briefly in simple chat language and then ask for the same next step. "
    "If the customer says something off-track, answer briefly and steer back to the required next action. "
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
        self.model = model.strip()
        self.system_prompt = system_prompt.strip() or DEFAULT_AGENT_CONVERSATION_PROMPT
        self.timeout_seconds = timeout_seconds

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
                "Do not add or remove prices, dates, trip names, IDs, room counts, or payment facts.",
                "If passport upload is required, ask only for the attachment upload and do not ask for typed passport fields.",
                "Do not ask the traveler whether they want to pay now or confirm a deposit.",
                "Do not imply flights are included by default unless the base message explicitly says that.",
                "Do not invent any VIP discount, group discount, or visa requirement.",
                "If the user's message is asking for clarification, explain the current request briefly and then repeat the same required next action.",
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
                "temperature": 0.35,
                "topP": 0.9,
                "maxOutputTokens": 220,
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
