from __future__ import annotations

from typing import Any

from services.ai_agent.ai_agent_app.config import Settings
from services.ai_agent.llm.gemini_provider import GeminiProvider


def build_llm_provider(settings: Settings | dict[str, Any]) -> GeminiProvider | None:
    if isinstance(settings, dict):
        mode = str(settings.get("ai_agent_mode") or "deterministic").strip().lower()
        api_key = str(settings.get("gemini_api_key") or "").strip()
        model = str(settings.get("gemini_model") or "gemini-3.5-flash").strip()
    else:
        mode = settings.ai_agent_mode
        api_key = settings.gemini_api_key
        model = settings.gemini_model

    if mode not in {"gemini", "tool_calling"} or not api_key:
        return None
    return GeminiProvider(api_key=api_key, model=model, timeout_seconds=20.0, retries=1)
