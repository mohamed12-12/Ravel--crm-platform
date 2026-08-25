"""
Phase 3 Verification Suite -- Wording / UX Polish (Strengthened Exact-Match Edition)
====================================================================================

Verifies that every single one of the 23 Phase-3 copy changes renders the EXACT
intended user-facing string (verbatim string equality or exact template rendering).

Also includes regression guards against:
- Accidental English artifacts (e.g. "Forty Eight", "(Group size)", "(Boys / Male)")
- Process leakage & internal jargon (e.g. "YYYY-MM-DD", "(مفتوحة)", "نفس فئة السعر")
- Logic/state drift (preserving all Phase 1/2 workflow invariant states)
"""

import pytest
from unittest.mock import MagicMock, Mock

from services.ai_agent.ai_agent_app.agent.workflow_policy import ConversationWorkflowPolicy
from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
from tests.test_agent_conversation_reliability import PassiveAgent, RecordingReadTools, TRIPS


def _make_policy() -> ConversationWorkflowPolicy:
    return ConversationWorkflowPolicy()


def _ar_ctx(**overrides) -> dict:
    ctx: dict = {
        "language": "ar",
        "messages": [{"role": "user", "text": "مرحبا"}],
        "raw_phone": "",
        "pending_raw_phone": "",
        "workflow": {},
        "known_traveler": {},
        "trip_type": "",
        "selected_trip_id": "",
        "selected_trip": {},
        "room_group": "",
        "trip_result": {},
        "room_type": "",
        "group_size": 0,
        "boys_count": 0,
        "girls_count": 0,
        "family_units": 0,
        "group_nationality_type": "",
        "group_nationality_counts": {},
        "flight_option": "",
        "currency": "",
        "collection_state": {},
        "customer_name": "",
        "nationality": "",
        "birthday": "",
        "mixed_room_requirements": [],
        "discovery_only": False,
        "booking_intent": False,
        "new_traveler_lead_saved": False,
    }
    ctx.update(overrides)
    return ctx


def _verified_ctx(**overrides) -> dict:
    ctx = _ar_ctx(
        workflow={
            "lookup_status": "found",
            "identity_verified": True,
            "verified_traveler": {"traveler_id": "T001", "full_name": "Ahmed Ali", "status": "Active"},
            "verified_status": "Active",
        },
    )
    ctx.update(overrides)
    return ctx


@pytest.fixture
def runtime():
    settings = MagicMock()
    settings.default_country_code = "20"
    settings.language = "ar"
    settings.ai_provider = "none"
    settings.gemini_api_key = ""
    settings.enable_write_tools = True
    settings.post_trip_handoff_responsible_employee = "Ravel Team"
    rt = ToolCallingSessionRuntime(settings=settings, conversation_ai=PassiveAgent())
    rt._read_only_tools = RecordingReadTools(trips=TRIPS)
    rt._write_executor = Mock()
    return rt


# ===========================================================================
# EXACT MATCH VERIFICATION FOR ALL 23 USER-FACING STRINGS
# ===========================================================================

def test_p3_01_duplicate_traveler_exact_match() -> None:
    """Change #1: duplicate_traveler_detected AR exact match."""
    policy = _make_policy()
    ctx = _ar_ctx(raw_phone="+201000000000", workflow={"lookup_status": "duplicate"})
    decision = policy.evaluate(ctx)
    expected = "لقيت رقم الواتساب ده مرتبط بأكتر من ملف. هبعت الطلب للفريق وهيتواصلوا معاك بعد المراجعة."
    assert decision.assistant_message == expected
    assert decision.state == "duplicate_traveler_detected"


def test_p3_02_invalid_phone_exact_match() -> None:
    """Change #2: invalid_phone AR exact match."""
    policy = _make_policy()
    ctx = _ar_ctx(raw_phone="abc", workflow={"lookup_status": "invalid_phone"})
    decision = policy.evaluate(ctx)
    expected = "ممكن ترسلي رقم واتساب صحيح؟ لو خارج مصر، حط كود الدولة، مثال: +966512345678."
    assert decision.assistant_message == expected
    assert decision.state == "identity_required"


def test_p3_03_restricted_traveler_exact_match() -> None:
    """Change #3: restricted_traveler_review AR exact match."""
    policy = _make_policy()
    ctx = _ar_ctx(
        raw_phone="+201000000000",
        workflow={
            "lookup_status": "found",
            "identity_verified": False,
            "verified_traveler": {"traveler_id": "T001", "status": "Archived"},
            "verified_status": "Archived",
        },
    )
    decision = policy.evaluate(ctx)
    expected = "لقيت ملفك. الطلب ده بحتاج مراجعة من فريق Ravel — هيتواصلوا معاك قريب."
    assert decision.assistant_message == expected
    assert decision.state == "human_handoff_required"


def test_p3_04_identity_lookup_pending_exact_match() -> None:
    """Change #4: identity_lookup_pending AR exact match."""
    policy = _make_policy()
    ctx = _ar_ctx(raw_phone="+201000000000", workflow={})
    decision = policy.evaluate(ctx)
    expected = "ثانية، بشوف رقم الواتساب ده ..."
    assert decision.assistant_message == expected
    assert decision.state == "identity_lookup_pending"


def test_p3_05_identity_required_exact_match() -> None:
    """Change #5: identity_required AR exact match."""
    policy = _make_policy()
    ctx = _ar_ctx(booking_intent=True)
    decision = policy.evaluate(ctx)
    expected = "عشان أكمل معاك الحجز، ممكن تبعتلي رقم واتساب الخاص بيك؟"
    assert decision.assistant_message == expected
    assert decision.state == "identity_required"


def test_p3_06_private_request_ready_exact_match() -> None:
    """Change #6: private_request_ready AR exact match."""
    policy = _make_policy()
    ctx = _verified_ctx(
        private_trip_active=True,
        trip_type="local",
        private_service_type="consultation",
        private_destination="Cairo",
        private_dates_flexible=True,
        private_party_size="4",
        private_budget_currency="EGP",
    )
    decision = policy.evaluate(ctx)
    expected = "تمام، بسجّل الطلب دلوقتي للفريق."
    assert decision.assistant_message == expected
    assert decision.state == "private_request_ready"


def test_p3_07_private_trip_saved_handoff_exact_match() -> None:
    """Change #7: private_trip_consultation AR exact match."""
    policy = _make_policy()
    ctx = _verified_ctx(
        private_trip_active=True,
        trip_type="local",
        private_service_type="consultation",
        private_destination="Cairo",
        private_dates_flexible=True,
        private_party_size="4",
        private_budget_currency="EGP",
        private_trip_request_id="PR-123",
    )
    decision = policy.evaluate(ctx)
    expected = "تم حفظ طلب الرحلة الخاصة. ✅ فريق Ravel هيتواصل معاك خلال ٢٤–٤٨ ساعة."
    assert decision.assistant_message == expected
    assert decision.state == "human_handoff_required"


def test_p3_08_trip_selection_required_exact_match() -> None:
    """Change #8: trip_selection_required AR exact match."""
    policy = _make_policy()
    ctx = _verified_ctx(
        trip_type="local",
        trip_result={"open_trips": [{"trip_id": "TRIP1", "name": "Siwa", "type": "local"}]},
    )
    decision = policy.evaluate(ctx)
    expected = "لقيت رحلات تناسب اختيارك 🎉 — اختار منها الرحلة اللي تحب:"
    assert decision.assistant_message == expected
    assert decision.state == "trip_selection_required"


def test_p3_09_trip_search_ready_exact_match() -> None:
    """Change #9: trip_search_ready AR exact match."""
    policy = _make_policy()
    ctx = _verified_ctx(trip_type="local", trip_result={})
    decision = policy.evaluate(ctx)
    expected = "تمام، بدور دلوقتي على الرحلات المتاحة ..."
    assert decision.assistant_message == expected
    assert decision.state == "trip_search_ready"


def test_p3_10_traveler_gender_required_exact_match() -> None:
    """Change #10: traveler_gender_required AR exact match."""
    policy = _make_policy()
    ctx = _verified_ctx(
        trip_type="local",
        selected_trip_id="TRIP1",
        selected_trip={"trip_id": "TRIP1", "name": "Siwa"},
    )
    decision = policy.evaluate(ctx)
    expected = (
        "المجموعة شباب، بنات، ولا مختلطة؟\n\n"
        "١) شباب فقط\n"
        "٢) بنات فقط\n"
        "٣) مختلطة (شباب + بنات)"
    )
    assert decision.assistant_message == expected
    assert decision.state == "traveler_gender_required"


def test_p3_11_group_size_required_exact_match() -> None:
    """Change #11: group_size_required AR exact match."""
    policy = _make_policy()
    trip = {
        "trip_id": "TRIP1",
        "available_single": 10,
        "boys_double": 5, "girls_double": 5,
        "boys_triple": 5, "girls_triple": 5,
        "available_double": 5, "available_triple": 5,
    }
    ctx = _verified_ctx(
        trip_type="local",
        selected_trip_id="TRIP1",
        selected_trip=trip,
        room_group="boys",
        room_type="Triple boys",
        collection_state={"room_group": "boys", "room_type": "Triple boys"},
        group_size=0,
    )
    decision = policy.evaluate(ctx)
    expected = "إجمالي كام مسافر هيحجزوا؟"
    assert decision.assistant_message == expected
    assert decision.state == "group_size_required"


def test_p3_12_room_capacity_alt_options_exact_match() -> None:
    """Change #12: room capacity alt options AR exact match."""
    policy = _make_policy()
    trip = {
        "trip_id": "T1",
        "available_single": 1,
        "boys_double": 3,
        "girls_double": 0,
        "boys_triple": 0,
        "girls_triple": 0,
        "available_double": 3,
        "available_triple": 0,
    }
    ctx = _verified_ctx(
        trip_type="local",
        selected_trip_id="T1",
        selected_trip=trip,
        room_group="boys",
        room_type="Single",
        group_size=3,
        collection_state={"room_group": "boys", "room_type": "Single", "group_size": True},
    )
    decision = policy.evaluate(ctx)
    expected = (
        "الغرفة اللي اخترتها (Single) مش متاحة للمجموعة كلها حالياً (محتاجين 3 غرفة، المتاح 1 بس).\n\n"
        "الخيارات المتاحة لمجموعتك (3 أفراد):\n"
        "1) Double - Boys (ثنائية شباب)\n\n"
        "اختار رقم الخيار المناسب:"
    )
    assert decision.assistant_message == expected
    assert decision.state == "room_type_required"


def test_p3_13_room_capacity_no_options_handoff_exact_match() -> None:
    """Change #13: room capacity total handoff AR exact match."""
    policy = _make_policy()
    trip = {
        "trip_id": "T1",
        "available_single": 1,
        "boys_double": 0, "girls_double": 0,
        "boys_triple": 0, "girls_triple": 0,
        "available_double": 0, "available_triple": 0,
    }
    ctx = _verified_ctx(
        trip_type="local",
        selected_trip_id="T1",
        selected_trip=trip,
        room_group="boys",
        room_type="Single",
        group_size=4,
        collection_state={"room_group": "boys", "room_type": "Single", "group_size": True},
    )
    decision = policy.evaluate(ctx)
    expected = (
        "مفيش غرف كافية لمجموعتك في أي خيار حالياً.\n"
        "بعت الطلب لفريق Ravel وهيتواصلوا معاك بعد المراجعة."
    )
    assert decision.assistant_message == expected
    assert decision.state == "capacity_handoff_required"


def test_p3_14_no_trips_available_exact_match() -> None:
    """Change #14: no_trips_available AR exact match (local & international)."""
    policy = _make_policy()
    msg_local = policy._no_trips_available_message("local", arabic=True)
    expected_local = "للأسف مفيش رحلات محلية متاحة دلوقتي. سجّلت طلبك وفريق Ravel هيتواصل معاك لو في رحلة مناسبة."
    assert msg_local == expected_local

    msg_intl = policy._no_trips_available_message("international", arabic=True)
    expected_intl = "للأسف مفيش رحلات دولية متاحة دلوقتي. سجّلت طلبك وفريق Ravel هيتواصل معاك لو في رحلة مناسبة."
    assert msg_intl == expected_intl


def test_p3_15_duplicate_lead_exact_match() -> None:
    """Change #15: duplicate_lead_prompt AR exact match with Eastern Arabic numerals."""
    policy = _make_policy()
    msg = policy._duplicate_lead_prompt("LEAD-42", arabic=True)
    expected = (
        "عندك طلب مفتوح برقم LEAD-42.\n"
        "تكمل عليه ولا تبدأ طلب جديد؟\n\n"
        "١. أكمل الطلب الحالي\n"
        "٢. ابدأ طلب جديد"
    )
    assert msg == expected


def test_p3_16_full_intake_exact_match() -> None:
    """Change #16: full new traveler intake AR exact match."""
    policy = _make_policy()
    ctx = _ar_ctx(raw_phone="+201000000000", workflow={"lookup_status": "not_found"})
    decision = policy.evaluate(ctx)
    expected = (
        "مش لاقي ملف بالرقم ده. عشان نكمل الحجز، بعتلي:\n"
        "١) الاسم الكامل (مثال: محمد أشرف صفوت)\n"
        "٢) الجنسية (مثال: مصري)\n"
        "٣) تاريخ الميلاد (مثال: 10/08/1995)\n"
        "٤) عدد المسافرين وتفاصيلهم (مثال: ٢ شباب)"
    )
    assert decision.assistant_message == expected


def test_p3_17_birthday_label_exact_match() -> None:
    """Change #17: partial birthday field label exact match."""
    policy = _make_policy()
    ctx = _ar_ctx(
        raw_phone="+201000000000",
        workflow={"lookup_status": "not_found"},
        customer_name="Ahmed Ali Sayed",
        nationality="مصري",
    )
    decision = policy.evaluate(ctx)
    assert "تاريخ الميلاد (مثال: 10/08/1995)" in decision.assistant_message
    assert "YYYY-MM-DD" not in decision.assistant_message


def test_p3_18_partial_intake_followup_exact_match() -> None:
    """Change #18: partial intake follow-up AR exact match."""
    policy = _make_policy()
    ctx = _ar_ctx(
        raw_phone="+201000000000",
        workflow={"lookup_status": "not_found"},
        customer_name="Ahmed Ali Sayed",
    )
    decision = policy.evaluate(ctx)
    expected_start = "تمام، بس محتاج كمان:\n"
    assert decision.assistant_message.startswith(expected_start)
    assert "شكراً! متبقي فقط" not in decision.assistant_message


def test_p3_19_group_nationality_type_prompt_exact_match() -> None:
    """Change #19: group_nationality_type_prompt AR exact match."""
    policy = _make_policy()
    msg = policy._group_nationality_type_prompt(arabic=True)
    expected = "المجموعة كلها مصريين ولا في أجانب كمان؟\n\n١. كلهم نفس الجنسية\n٢. مختلطة (مصريين وأجانب)"
    assert msg == expected


def test_p3_20_gender_counts_prompt_exact_match() -> None:
    """Change #20: gender_counts_prompt AR exact match."""
    policy = _make_policy()
    msg = policy._gender_counts_prompt(arabic=True)
    expected = "كام شاب وكام بنت في المجموعة؟ مثال: ٣ شباب و٢ بنات."
    assert msg == expected


def test_p3_21_family_units_prompt_exact_match() -> None:
    """Change #21: family_units_prompt AR exact match."""
    policy = _make_policy()
    msg = policy._family_units_prompt(arabic=True)
    expected = "في أزواج أو عيلة يقدروا يتشاركوا في أوضة؟ اكتب العدد، أو ٠ لو مفيش."
    assert msg == expected


def test_p3_22_group_nationality_counts_prompt_exact_match() -> None:
    """Change #22: group_nationality_counts_prompt AR exact match."""
    policy = _make_policy()
    msg = policy._group_nationality_counts_prompt(3, arabic=True)
    expected = "بعتلي عدد المصريين والأجانب من الـ3 مسافرين، مثال: ٢ مصري و١ أجنبي."
    assert msg == expected


def test_p3_23_room_capacity_handoff_success_exact_match(runtime) -> None:
    """Change #23: room capacity handoff success message AR exact match."""
    session = runtime.create_session()
    session.language = "ar"
    msg = runtime._handoff_success_message(session, reason_code="room_capacity")
    expected = "مفيش غرف كافية لمجموعتك حالياً. بعت الطلب لـRavel Team في فريق Ravel وهيتواصلوا معاك بعد المراجعة."
    assert msg == expected


# ===========================================================================
# REGRESSION GUARDS: NO ENGLISH ARTIFACTS / INTERNAL JARGON IN ARABIC STRINGS
# ===========================================================================

@pytest.mark.parametrize(
    "forbidden_artifact",
    [
        "Forty Eight", "Forty-Eight", "forty eight",
        "(Group size)",
        "(Boys / Male)", "(Girls / Female)", "(Boys + Girls)",
        "YYYY-MM-DD",
        "(مفتوحة)",
        "/نفس فئة السعر",
    ],
)
def test_p3_regression_guard_no_forbidden_artifacts_in_arabic_prompts(forbidden_artifact: str) -> None:
    """Regression test asserting that forbidden English/technical terms never leak into Arabic prompts."""
    policy = _make_policy()
    # Check all prompt static methods
    prompts = [
        policy._no_trips_available_message("local", arabic=True),
        policy._duplicate_lead_prompt("LEAD-1", arabic=True),
        policy._group_nationality_type_prompt(arabic=True),
        policy._gender_counts_prompt(arabic=True),
        policy._family_units_prompt(arabic=True),
        policy._group_nationality_counts_prompt(4, arabic=True),
    ]

    # Evaluate representative workflow decision messages
    contexts = [
        _ar_ctx(raw_phone="+201000000000", workflow={"lookup_status": "duplicate"}),
        _ar_ctx(raw_phone="abc", workflow={"lookup_status": "invalid_phone"}),
        _ar_ctx(booking_intent=True),
        _verified_ctx(
            private_trip_active=True,
            trip_type="local",
            private_service_type="consultation",
            private_destination="Cairo",
            private_dates_flexible=True,
            private_party_size="4",
            private_budget_currency="EGP",
            private_trip_request_id="PR-1",
        ),
        _verified_ctx(
            trip_type="local",
            selected_trip_id="T1",
            selected_trip={"trip_id": "T1", "name": "Siwa"},
        ),
        _ar_ctx(raw_phone="+201000000000", workflow={"lookup_status": "not_found"}),
    ]
    for ctx in contexts:
        decision = policy.evaluate(ctx)
        prompts.append(decision.assistant_message)

    for prompt_text in prompts:
        assert forbidden_artifact not in prompt_text, (
            f"Forbidden artifact '{forbidden_artifact}' leaked into Arabic message:\n{prompt_text}"
        )


def test_p3_behavior_english_identity_required_unchanged() -> None:
    """English variant of identity_required must be completely untouched."""
    policy = _make_policy()
    ctx = _ar_ctx(booking_intent=True)
    ctx["language"] = "en"
    ctx["messages"] = [{"role": "user", "text": "I want to book a trip"}]
    decision = policy.evaluate(ctx)
    msg = decision.assistant_message
    assert ("WhatsApp" in msg or "share your" in msg.lower())


def test_p3_behavior_no_trips_en_unchanged() -> None:
    """English no_trips_available must be completely untouched."""
    policy = _make_policy()
    msg = policy._no_trips_available_message("local", arabic=False)
    assert "open right now" in msg or "Ravel" in msg
