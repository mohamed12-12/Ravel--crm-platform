from __future__ import annotations

import re
from dataclasses import dataclass

from services.ai_agent.ai_agent_app.agent.session_flow import detect_language


@dataclass(frozen=True)
class AgentIdentityResponse:
    intent: str
    language: str
    text: str


class AgentIdentityPolicy:
    """Backend-owned answers for assistant identity and provider questions."""

    IDENTITY_AR = "أنا مساعد رحمة ترافل الذكي، تم تطويري وتخصيصي بواسطة nanovate.io."
    IDENTITY_EN = "I’m Ravel Traveler’s AI assistant, built and customized by nanovate.io."
    PROVIDER_AR = "تم تطوير هذا المساعد بواسطة nanovate.io ويعتمد على تقنيات الذكاء الاصطناعي."
    PROVIDER_EN = "This assistant was built and integrated by nanovate.io and is powered by an AI model."

    _IDENTITY_PATTERNS = (
        "مين عملك",
        "مين طورك",
        "انت تابع لمين",
        "انت من شركة ايه",
        "مين اللي عمل البرنامج ده",
        "who created you",
        "who made you",
        "who built you",
        "who developed you",
        "what company made you",
        "created you",
        "made you",
        "built you",
        "developed you",
        "company made you",
    )
    _PROVIDER_PATTERNS = (
        "what model do you use",
        "are you gemini",
        "are you openai",
        "are you google",
        "انت جيميناي",
        "انت Gemini",
        "انت openai",
        "انت اوبن ايه اي",
        "بتستخدم موديل ايه",
    )

    @classmethod
    def evaluate(cls, user_text: str) -> AgentIdentityResponse | None:
        normalized = cls._normalize(user_text)
        if not normalized:
            return None
        language = detect_language(user_text)
        if cls._matches(normalized, cls._IDENTITY_PATTERNS):
            return AgentIdentityResponse(
                intent="identity",
                language=language,
                text=cls.IDENTITY_AR if language == "ar" else cls.IDENTITY_EN,
            )
        if cls._matches(normalized, cls._PROVIDER_PATTERNS):
            return AgentIdentityResponse(
                intent="model_provider",
                language=language,
                text=cls.PROVIDER_AR if language == "ar" else cls.PROVIDER_EN,
            )
        return None

    @staticmethod
    def _normalize(text: str) -> str:
        normalized = str(text or "").casefold()
        normalized = re.sub(r"[\u064B-\u065F\u0670]", "", normalized)
        normalized = normalized.translate(
            str.maketrans(
                {
                    "أ": "ا",
                    "إ": "ا",
                    "آ": "ا",
                    "ى": "ي",
                    "ة": "ه",
                    "ؤ": "و",
                    "ئ": "ي",
                }
            )
        )
        normalized = re.sub(r"[^\w\u0600-\u06FF]+", " ", normalized)
        return re.sub(r"\s+", " ", normalized).strip()

    @classmethod
    def _matches(cls, normalized_text: str, patterns: tuple[str, ...]) -> bool:
        return any(cls._normalize(pattern) in normalized_text for pattern in patterns)
