"""Regression tests for a live 2026-08-21 Arabic intake that corrupted a lead.

The transcript, in order:

    customer: هلا
    agent:    ... send your WhatsApp number
    customer: موسي عويس سامي                       <- a name, before the number
    agent:    أهلاً بك يا أستاذ موسى ... ابعتلي رقم الواتساب
    customer: لا اسمي خالد جاد ماجد                 <- corrects the name
    agent:    أهلاً بك يا أستاذ خالد ... ابعتلي رقم الواتساب
    customer: 01290786340
    agent:    لم أجد ملفاً ... هل الاسم الثلاثي بالكامل هو خالد جاد ماجد؟
    customer: اه بس الرقم ده 01240789320            <- corrects the number
    agent:    لا أستطيع مشاركة بيانات أي مسافر آخر  <- privacy refusal (!)
    customer: اه ده رقمي
    agent:    ما جنسيتك؟                            <- captured as the NAME (!)
    customer: العراقيه
    agent:    مش قادر أتعرف على الجنسية دي           <- article not recognised
    customer: عراقي
    ...
    agent:    شكرا اه ده رقمي. تم حفظ بياناتك وطلبك برقم LD00003

Lead LD00003 was created in production with customer_name = "اه ده رقمي".

Four independent defects, one per section below:

1. Nothing stored a name the customer volunteered before the phone step, so
   the workflow asked for it a third time after the lookup failed.
2. The privacy guard treated the customer's own corrected number as a request
   for another traveler's data, so the correction was never even merged.
3. _validate_name_tokens accepted "اه ده رقمي" -- three Arabic tokens of 2+
   characters that are not all identical passed every check it had.
4. resolve_nationality did not recognise a nationality with the definite
   article ("العراقيه").
"""
from __future__ import annotations

import pytest

from services.ai_agent.ai_agent_app.agent.privacy_policy import AgentPrivacyPolicy
from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
from tests.test_phase12_booking_state import _send, runtime as runtime  # noqa: F401


# ---------------------------------------------------------------------------
# 3. A non-answer must never be stored as a name.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "اه ده رقمي",
        "اه ده رقمي الجديد",
        "لا ده رقمي",
        "نعم ماشي تمام",
        "yes this is me",
        "ok my number is right",
        "ايوه الرقم ده",
    ],
)
def test_an_answer_to_another_question_is_never_captured_as_a_name(text: str) -> None:
    name, reason = ToolCallingSessionRuntime._validate_name_tokens(text)
    assert name == ""
    # No structural reason on purpose: this is not a malformed name, it is a
    # reply to something else, so it stays routed to the off-script handling
    # instead of earning a "please send three parts" correction.
    assert reason == ""


@pytest.mark.parametrize(
    "text",
    [
        # Real names that merely CONTAIN a rejected token as a fragment. The
        # check is exact-token, never substring, precisely so these still pass.
        "بسمه محمد علي",
        "نعمه سيد ابراهيم",
        "اسماء كمال حسن",
        "رقيه جمال سعيد",
        "دينا احمد فؤاد",
        "هدي مصطفي كامل",
        "Mohamed Ashraf Safwat",
        "موسي عويس سامي",
        "خالد جاد ماجد",
    ],
)
def test_real_names_are_still_accepted(text: str) -> None:
    name, reason = ToolCallingSessionRuntime._validate_name_tokens(text)
    assert reason == ""
    assert name


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("لا اسمي خالد جاد ماجد", "خالد جاد ماجد"),
        ("اسمي محمد اشرف صفوت", "محمد اشرف صفوت"),
        ("انا اسمي محمد اشرف صفوت", "محمد اشرف صفوت"),
        ("my name is Khaled Gad Maged", "Khaled Gad Maged"),
        ("My Name Is Mohamed Ashraf Safwat", "Mohamed Ashraf Safwat"),
    ],
)
def test_an_explicit_declaration_yields_only_the_name(text: str, expected: str) -> None:
    """Without stripping the declaration, "لا اسمي خالد جاد ماجد" passed every
    structural check as a five-token Arabic name and would have been stored
    verbatim as the customer's legal name."""

    assert ToolCallingSessionRuntime._validate_name_tokens(text)[0] == expected
    assert ToolCallingSessionRuntime._declared_full_name(text) == expected


@pytest.mark.parametrize("text", ["خالد جاد ماجد", "Mohamed Ashraf Safwat", "اه ده رقمي", "اسمي خالد"])
def test_declared_name_requires_both_a_declaration_and_a_valid_name(text: str) -> None:
    """A bare name is a valid name but NOT a declaration (only a declaration may
    overwrite a name captured earlier), and a declaration wrapped around
    something that is not a name stays rejected."""

    assert ToolCallingSessionRuntime._declared_full_name(text) == ""


# ---------------------------------------------------------------------------
# 2. The customer's own corrected number is an identity input, not a request
#    for another traveler's data.
# ---------------------------------------------------------------------------

_UNVERIFIED = {
    "customer_name": "Khaled Gad Maged",
    "raw_phone": "01290786340",
    "pending_raw_phone": "01290786340",
    "workflow": {"identity_verified": False, "lookup_status": "not_found"},
}
_VERIFIED = {
    "customer_name": "Mohamed Ashraf",
    "raw_phone": "01554158741",
    "traveler_id": "TR00586",
    "workflow": {"identity_verified": True, "verified_traveler": {"traveler_id": "TR00586"}},
}


@pytest.mark.parametrize(
    ("context", "text"),
    [
        # The exact live message that was refused.
        (_UNVERIFIED, "اه بس الرقم ده 01240789320"),
        (_UNVERIFIED, "ده رقمي 01240789320"),
        (_UNVERIFIED, "رقمي الصح 01240789320"),
        # Honoured for a verified traveler too, so they can correct the number
        # on their own file.
        (_VERIFIED, "رقمي الجديد 01240789320"),
        (_VERIFIED, "غير الرقم لـ 01240789320"),
        (_VERIFIED, "my number is 01240789320"),
        (_VERIFIED, "please use this number 01240789320"),
    ],
)
def test_a_self_framed_phone_correction_is_not_a_privacy_event(context: dict, text: str) -> None:
    assert AgentPrivacyPolicy.evaluate_user_message(text, context) is None


@pytest.mark.parametrize(
    ("context", "text"),
    [
        # A bare differing number stays blocked -- that is also what phone
        # fishing looks like, and test_agent_privacy_policy.py pins it.
        (_UNVERIFIED, "01554158741"),
        (_VERIFIED, "01111111111"),
        # A data request about someone else is blocked even when it is wrapped
        # in phrasing that would otherwise read as self-framing.
        (_VERIFIED, "عايز بيانات الرقم ده 01111111111"),
        (_VERIFIED, "عايز بيانات محمد اشرف"),
        # Pre-existing holes closed in the same pass: one bare pronoun anywhere
        # in the message used to cancel the whole cross-traveler check, and
        # "show me"/"انا" are the most ordinary sentence openings there are.
        (_VERIFIED, "show me another traveler's booking"),
        (_VERIFIED, "tell me his booking details"),
        (_VERIFIED, "give me someone else's profile"),
        (_VERIFIED, "انا عايز بيانات محمد اشرف"),
    ],
)
def test_requests_about_another_traveler_are_still_blocked(context: dict, text: str) -> None:
    response = AgentPrivacyPolicy.evaluate_user_message(text, context)
    assert response is not None
    assert response.intent == "other_traveler_data_request"


@pytest.mark.parametrize(
    "text",
    [
        "check my profile",
        "what are my details",
        "عايز اعرف بياناتي",
        "ابعتلي ملفي",
        "انا عايز اعرف الملف بتاعي",
        "الحجز بتاعي فين",
    ],
)
def test_questions_about_the_customers_own_record_are_still_allowed(text: str) -> None:
    """Dropping the bare pronouns must not start refusing self-questions -- the
    Egyptian possessives ("بتاعي") cover what "انا" was really carrying."""

    assert AgentPrivacyPolicy.evaluate_user_message(text, _VERIFIED) is None


# ---------------------------------------------------------------------------
# 1 + 3 end to end: replay the transcript.
# ---------------------------------------------------------------------------

def test_a_name_declared_before_the_phone_is_not_asked_for_again(runtime: ToolCallingSessionRuntime) -> None:
    session = runtime.create_session()

    session = _send(runtime, "هلا", session)
    session = _send(runtime, "لا اسمي خالد جاد ماجد", session)
    assert session.customer_name == "خالد جاد ماجد"

    # The number still has to be asked for -- capturing the name must not skip
    # the phone-first identity step.
    assert session.stage in {"identity_required", "starting"}

    session = _send(runtime, "01290786340", session)
    # No profile for this number, and the name is already known, so the intake
    # moves straight on instead of asking for the name a third time.
    assert session.stage == "nationality_required"
    assert session.customer_name == "خالد جاد ماجد"


def test_a_later_declaration_corrects_an_earlier_one(runtime: ToolCallingSessionRuntime) -> None:
    session = runtime.create_session()
    session = _send(runtime, "اسمي موسي عويس سامي", session)
    assert session.customer_name == "موسي عويس سامي"

    session = _send(runtime, "لا اسمي خالد جاد ماجد", session)
    assert session.customer_name == "خالد جاد ماجد"


def test_a_bare_three_word_message_before_the_phone_is_not_taken_as_a_name(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Only an explicit declaration is honoured this early. A bare three-word
    message while the phone is being asked for is far more likely to be
    something else, and mis-capturing it is exactly the failure being fixed."""

    session = runtime.create_session()
    session = _send(runtime, "عايزك تساعدني بسرعة", session)
    assert session.customer_name == ""


def test_the_transcript_no_longer_stores_a_non_answer_as_the_name(
    runtime: ToolCallingSessionRuntime,
) -> None:
    session = runtime.create_session()
    for text in ("01290786340", "خالد جاد ماجد"):
        session = _send(runtime, text, session)
    assert session.stage == "nationality_required"

    # The message that became the customer's name in production.
    session = _send(runtime, "اه ده رقمي", session)
    assert session.customer_name == "خالد جاد ماجد"
    assert session.nationality == ""

    # And the nationality answer that was rejected in production.
    session = _send(runtime, "العراقيه", session)
    assert session.nationality == "Iraqi"
    assert session.stage == "birthday_required"
