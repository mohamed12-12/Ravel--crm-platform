from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.ai_agent.ai_agent_app.config import load_settings
from services.ai_agent.ai_agent_app.conversation_ai import GeminiConversationAI


FORBIDDEN_PHRASES = (
    "passport number",
    "passport expiry",
    "pay now",
    "deposit",
    "vip discount",
    "visa required",
)


def main() -> int:
    settings = load_settings()
    if not settings.gemini_api_key:
        print("FAIL: GEMINI_API_KEY is missing from .env.")
        return 1
    if not settings.agent_conversation_prompt:
        print("FAIL: agent conversation prompt is not loaded.")
        return 1

    ai = GeminiConversationAI(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        system_prompt=settings.agent_conversation_prompt,
        timeout_seconds=20,
    )

    base_text = (
        "Same reason, and I will keep it simple: the number lets me find the correct traveler profile "
        "before we talk about trips or reservations. Please send your WhatsApp number."
    )
    rewritten = ai.rewrite_message(
        message_key="session.explain_phone_request_repeat",
        base_text=base_text,
        language="en",
        user_text="why?",
        required_action="Ask for the WhatsApp number.",
        session_context={
            "stage": "awaiting_phone",
            "customer_message_intent": "clarification",
            "persona_intent_repeat_count": 2,
            "passport_required": False,
            "has_passport_attachment": False,
        },
    )

    if not rewritten:
        print("FAIL: Gemini returned no usable rewrite.")
        return 1

    lowered = rewritten.lower()
    if "whatsapp" not in lowered or "number" not in lowered:
        print("FAIL: Gemini reply did not preserve the required WhatsApp-number step.")
        print(f"Reply: {rewritten}")
        return 1
    for phrase in FORBIDDEN_PHRASES:
        if phrase in lowered:
            print(f"FAIL: Gemini reply included forbidden phrase: {phrase}")
            print(f"Reply: {rewritten}")
            return 1

    print("PASS: Gemini persona rewrite is working.")
    print(f"Model: {settings.gemini_model}")
    print(f"Reply: {rewritten}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
