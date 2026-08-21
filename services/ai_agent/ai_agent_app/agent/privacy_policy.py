"""Blocks the agent from answering a question about a DIFFERENT
traveler's data than the one currently verified in this session --
SENSITIVE_TOOLS lists which tool calls are gated by this check.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from services.ai_agent.validation.lexicon import OWN_PHONE_CORRECTION_TERMS


@dataclass(frozen=True)
class AgentPrivacyResponse:
    intent: str
    language: str
    text: str


class AgentPrivacyPolicy:
    """Backend privacy guard for customer-scoped CRM data access."""

    AR_RESPONSE = (
        "\u0644\u0627 \u0623\u0633\u062a\u0637\u064a\u0639 \u0645\u0634\u0627\u0631\u0643\u0629 \u0628\u064a\u0627\u0646\u0627\u062a \u0623\u064a "
        "\u0645\u0633\u0627\u0641\u0631 \u0622\u062e\u0631 \u0645\u0646 \u062e\u0644\u0627\u0644 \u0647\u0630\u0647 "
        "\u0627\u0644\u0645\u062d\u0627\u062f\u062b\u0629. \u0623\u0642\u062f\u0631 \u0623\u0633\u0627\u0639\u062f\u0643 "
        "\u0641\u064a \u062d\u062c\u0632\u0643 \u0623\u0648 \u0628\u064a\u0627\u0646\u0627\u062a\u0643 \u0623\u0646\u062a "
        "\u0641\u0642\u0637\u060c \u0648\u0644\u0648 \u062a\u062d\u062a\u0627\u062c \u0645\u0631\u0627\u062c\u0639\u0629 "
        "\u0628\u064a\u0627\u0646\u0627\u062a \u0639\u0645\u064a\u0644 \u0622\u062e\u0631 \u064a\u0631\u062c\u0649 "
        "\u0627\u0644\u0631\u062c\u0648\u0639 \u0644\u0645\u0648\u0638\u0641 \u0631\u062d\u0645\u0629 \u062a\u0631\u0627\u0641\u0644 "
        "\u062f\u0627\u062e\u0644 \u0646\u0638\u0627\u0645 CRM."
    )
    EN_RESPONSE = (
        "I can't share another traveler's details in this chat. "
        "I can help with your own booking or profile only. For another customer, please ask the Ravel Traveler team to review it with employee access."
    )

    SENSITIVE_TOOLS = {
        "search_traveler",
        "find_traveler_by_phone",
        "get_traveler_profile",
        "get_traveler_trip_history",
        "get_booking_status",
        "get_passport_status",
        "lookup_lead",
    }

    _OTHER_TRAVELER_AR = (
        "\u0628\u064a\u0627\u0646\u0627\u062a",
        "\u0645\u0639\u0644\u0648\u0645\u0627\u062a",
        "\u0628\u0631\u0648\u0641\u0627\u064a\u0644",
        "\u0645\u0644\u0641",
        "\u0631\u0642\u0645 \u062a\u0639\u0631\u064a\u0641",
        "\u062a\u0631\u0627\u0641\u0644\u0631",
    )
    # Bare English nouns like "details"/"profile"/"data" are far too common in
    # ordinary customer questions ("what details do you need?") to use alone as
    # a signal \u2014 require the phrase to actually reference a *different*
    # person's data, not just any mention of data/profile/details.
    _OTHER_TRAVELER_EN = (
        "another traveler",
        "other traveler",
        "different traveler",
        "another customer",
        "other customer",
        "different customer",
        "someone else",
        "somebody else",
        "another person",
        "other person",
        "my friend's",
        "his booking",
        "her booking",
        "their booking",
        "his profile",
        "her profile",
        "their profile",
        "his passport",
        "her passport",
        "their passport",
        "his details",
        "her details",
        "their details",
        "someone's",
    )
    # A first-person POSSESSIVE only. The bare pronouns "\u0627\u0646\u0627"/"\u0623\u0646\u0627" and "me"
    # used to be listed here, and since one self term anywhere in the message
    # cancels the whole cross-traveler check, they let the two most ordinary
    # sentence openings in either language through untouched:
    # "show me another traveler's booking" and "\u0627\u0646\u0627 \u0639\u0627\u064a\u0632 \u0628\u064a\u0627\u0646\u0627\u062a \u0645\u062d\u0645\u062f \u0627\u0634\u0631\u0641" were
    # both allowed. The Egyptian possessives below replace what "\u0627\u0646\u0627" was
    # really covering ("\u0627\u0644\u0645\u0644\u0641 \u0628\u062a\u0627\u0639\u064a"), without matching a sentence that is
    # about somebody else.
    _SELF_AR = (
        "\u0628\u064a\u0627\u0646\u0627\u062a\u064a",
        "\u0645\u0639\u0644\u0648\u0645\u0627\u062a\u064a",
        "\u0645\u0644\u0641\u064a",
        "\u062d\u062c\u0632\u064a",
        "\u0628\u062a\u0627\u0639\u064a",
        "\u0628\u062a\u0627\u0639\u062a\u064a",
        "\u062e\u0627\u0635\u062a\u064a",
        "\u0644\u064a\u0627 \u0627\u0646\u0627",
    )
    _SELF_EN = ("my profile", "my details", "my data", "my booking", "my own")

    @classmethod
    def evaluate_user_message(cls, user_text: str, session_context: dict[str, Any]) -> AgentPrivacyResponse | None:
        if not cls._session_is_bound(session_context):
            return None
        language = cls._detect_language(user_text)
        blocked = cls._looks_like_other_traveler_request(user_text)
        if not blocked and cls._contains_other_phone(user_text, session_context):
            # A phone number that differs from the session's is not by itself a
            # request for someone else's data -- it is usually the customer
            # giving or correcting their OWN number. Live 2026-08-20 transcript:
            # the first number found no profile, the customer sent
            # "اه بس الرقم ده 01240789320" (their real one) and got the
            # cross-traveler refusal, so the corrected number was never even
            # merged into the session.
            #
            # Checked in this order on purpose: a message that reads as a data
            # request about another person ("عايز بيانات الرقم ده ...") is
            # already blocked above and never reaches this allowance.
            blocked = not cls._phone_is_offered_as_own_identity(user_text, session_context)
        if blocked:
            return AgentPrivacyResponse(
                intent="other_traveler_data_request",
                language=language,
                text=cls.AR_RESPONSE if language == "ar" else cls.EN_RESPONSE,
            )
        return None

    @classmethod
    def _phone_is_offered_as_own_identity(cls, user_text: str, context: dict[str, Any]) -> bool:
        """True when the message explicitly frames the number as the sender's own.

        "اه بس الرقم ده 01240789320", "رقمي الصح ...", "my number is ...",
        "غير الرقم لـ ..." are identity inputs, not questions about anyone's
        data, so they must reach the identity lookup instead of the
        cross-traveler refusal. Honoured for a verified session too, so a
        traveler can correct the number on their own file.

        A BARE differing number is still blocked, verified or not, because that
        is what a phone-fishing attempt also looks like -- see
        test_different_phone_after_bound_session_is_blocked_before_model, which
        pins exactly that case.
        """

        normalized = cls._normalize(user_text)
        return any(
            cls._contains_phrase(normalized, cls._normalize(term))
            for term in OWN_PHONE_CORRECTION_TERMS
        )

    @classmethod
    def guard_tool_call(
        cls,
        tool_name: str,
        args: dict[str, Any],
        session_context: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        context = session_context or {}
        if tool_name not in cls.SENSITIVE_TOOLS or not cls._session_is_bound(context):
            return None
        if cls._tool_targets_current_session(args, context):
            return None
        return cls.blocked_tool_result(language=str(context.get("language") or "en"))

    @classmethod
    def blocked_tool_result(cls, *, language: str = "en") -> dict[str, Any]:
        text = cls.AR_RESPONSE if str(language or "").startswith("ar") else cls.EN_RESPONSE
        return {
            "status": "privacy_blocked",
            "traveler": None,
            "warnings": ["cross_traveler_data_access_blocked"],
            "assistant_message": text,
            "executed": False,
        }

    @classmethod
    def _session_is_bound(cls, context: dict[str, Any]) -> bool:
        workflow = context.get("workflow") if isinstance(context.get("workflow"), dict) else {}
        known = context.get("known_traveler") if isinstance(context.get("known_traveler"), dict) else {}
        return bool(
            workflow.get("identity_verified")
            or context.get("traveler_id")
            or context.get("lead_id")
            or context.get("booking_id")
            or known.get("traveler_id")
            or context.get("customer_name")
            or context.get("raw_phone")
        )

    @classmethod
    def _tool_targets_current_session(cls, args: dict[str, Any], context: dict[str, Any]) -> bool:
        requested_phone = str(args.get("raw_phone") or "").strip()
        requested_traveler_id = str(args.get("traveler_id") or "").strip()
        requested_lead_id = str(args.get("lead_id") or "").strip()
        requested_booking_id = str(args.get("booking_id") or "").strip()

        if not any((requested_phone, requested_traveler_id, requested_lead_id, requested_booking_id)):
            return True
        if requested_phone and not cls._phone_matches_session(requested_phone, context):
            return False
        if requested_traveler_id and not cls._traveler_matches_session(requested_traveler_id, context):
            return False
        if requested_lead_id and requested_lead_id != str(context.get("lead_id") or "").strip():
            return False
        if requested_booking_id and requested_booking_id != str(context.get("booking_id") or "").strip():
            return False
        return True

    @classmethod
    def _contains_other_phone(cls, user_text: str, context: dict[str, Any]) -> bool:
        if not cls._session_phone_keys(context):
            # Nothing to contrast against. A session that holds no phone number
            # at all cannot have "another traveler's" number in it -- whatever
            # arrives is simply the first one given. `_session_is_bound` counts
            # a customer_name on its own, so once a name is captured before the
            # phone step (which now happens for "اسمي خالد جاد ماجد"), the very
            # first number the customer sent was being refused.
            return False
        for phone in cls._phone_candidates(user_text):
            if not cls._phone_matches_session(phone, context):
                return True
        return False

    # A real phone number carries at least 9 digits (E.164 national numbers;
    # an Egyptian mobile is 11, 12 with the country code). The separator class
    # in the pattern below deliberately accepts "-" and "." because numbers are
    # written "010-1234-5678" -- which also made "2026-09-28" a perfect match.
    # ToolCallingSessionRuntime._extract_phone_candidate already applies both of
    # these guards; this module never learned them, so a DATE in any
    # dash/dot-separated form counted as "another traveler's phone number" and
    # earned the cross-traveler refusal -- including the exact ISO format the
    # agent itself asks for ("ممكن تبعت التاريخ بصيغة YYYY-MM-DD").
    _MIN_PHONE_DIGITS = 9
    _DATE_SHAPED_RE = re.compile(r"^\d{1,4}[-./]\d{1,2}[-./]\d{1,4}$")

    @classmethod
    def _phone_candidates(cls, text: str) -> list[str]:
        normalized = cls._arabic_digits_to_ascii(text)
        candidates: list[str] = []
        for match in re.finditer(r"(?:\+|00)?\d[\d\s().-]{7,}\d", normalized):
            candidate = match.group(0).strip()
            if cls._DATE_SHAPED_RE.match(candidate):
                continue
            if len(re.sub(r"\D+", "", candidate)) < cls._MIN_PHONE_DIGITS:
                continue
            candidates.append(candidate)
        return candidates

    @classmethod
    def _session_phone_keys(cls, context: dict[str, Any]) -> set[str]:
        """Every phone number already on record for this session, comparably keyed."""

        candidates = [context.get("raw_phone"), context.get("pending_raw_phone")]
        phone_normalization = context.get("phone_normalization") if isinstance(context.get("phone_normalization"), dict) else {}
        candidates.extend(
            [
                phone_normalization.get("normalized_e164"),
                phone_normalization.get("normalized_whatsapp"),
                phone_normalization.get("local_number"),
            ]
        )
        known = context.get("known_traveler") if isinstance(context.get("known_traveler"), dict) else {}
        candidates.extend(
            [
                known.get("raw_phone"),
                known.get("whatsapp_raw"),
                known.get("integrated_whatsapp"),
                known.get("normalized_whatsapp"),
            ]
        )
        keys = {cls._phone_key(str(candidate or "")) for candidate in candidates if candidate}
        return {key for key in keys if key}

    @classmethod
    def _phone_matches_session(cls, requested: str, context: dict[str, Any]) -> bool:
        requested_key = cls._phone_key(requested)
        if not requested_key:
            return True
        return requested_key in cls._session_phone_keys(context)

    @classmethod
    def _traveler_matches_session(cls, requested_traveler_id: str, context: dict[str, Any]) -> bool:
        requested = str(requested_traveler_id or "").strip().casefold()
        if not requested:
            return True
        known = context.get("known_traveler") if isinstance(context.get("known_traveler"), dict) else {}
        workflow = context.get("workflow") if isinstance(context.get("workflow"), dict) else {}
        verified = workflow.get("verified_traveler") if isinstance(workflow.get("verified_traveler"), dict) else {}
        candidates = [context.get("traveler_id"), known.get("traveler_id"), verified.get("traveler_id")]
        return any(requested == str(candidate or "").strip().casefold() for candidate in candidates if candidate)

    @classmethod
    def _looks_like_other_traveler_request(cls, text: str) -> bool:
        normalized = cls._normalize(text)
        if any(cls._contains_phrase(normalized, cls._normalize(token)) for token in (*cls._SELF_AR, *cls._SELF_EN)):
            return False
        if any(cls._contains_phrase(normalized, cls._normalize(token)) for token in cls._OTHER_TRAVELER_AR):
            return True
        if any(cls._contains_phrase(normalized, cls._normalize(token)) for token in cls._OTHER_TRAVELER_EN):
            return True
        return False

    @staticmethod
    def _contains_phrase(normalized_text: str, normalized_phrase: str) -> bool:
        if not normalized_text or not normalized_phrase:
            return False
        if " " in normalized_phrase:
            return normalized_phrase in normalized_text
        return re.search(rf"(?<!\w){re.escape(normalized_phrase)}(?!\w)", normalized_text) is not None

    @staticmethod
    def _arabic_digits_to_ascii(text: str) -> str:
        return str(text or "").translate(
            str.maketrans("\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669\u06F0\u06F1\u06F2\u06F3\u06F4\u06F5\u06F6\u06F7\u06F8\u06F9", "01234567890123456789")
        )

    @classmethod
    def _phone_key(cls, value: str) -> str:
        digits = re.sub(r"\D+", "", cls._arabic_digits_to_ascii(value))
        if digits.startswith("00"):
            digits = digits[2:]
        if digits.startswith("20") and len(digits) > 10:
            digits = digits[2:]
        return digits[-10:] if len(digits) >= 10 else digits

    @classmethod
    def _normalize(cls, text: str) -> str:
        normalized = cls._arabic_digits_to_ascii(text).casefold()
        normalized = re.sub(r"[\u064B-\u065F\u0670]", "", normalized)
        normalized = normalized.translate(
            str.maketrans({"\u0623": "\u0627", "\u0625": "\u0627", "\u0622": "\u0627", "\u0649": "\u064a", "\u0629": "\u0647", "\u0624": "\u0648", "\u0626": "\u064a"})
        )
        normalized = re.sub(r"[^\w\u0600-\u06FF]+", " ", normalized)
        return re.sub(r"\s+", " ", normalized).strip()

    @staticmethod
    def _detect_language(text: str) -> str:
        return "ar" if re.search(r"[\u0600-\u06FF]", str(text or "")) else "en"
