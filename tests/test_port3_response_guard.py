from __future__ import annotations

from services.ai_agent.ai_agent_app.agent.gemini_agent import GeminiAgent
from services.ai_agent.ai_agent_app.agent.response_guard import guard_customer_response, response_guard_issue


def _booking_write_result(status: str = "success", record_id: str = "BK123") -> dict:
    return {
        "status": status,
        "executed": status == "success",
        "reused": status in {"reused", "duplicate"},
        "record_type": "booking",
        "record_id": record_id,
        "customer_confirmation_allowed": status in {"success", "reused", "duplicate"},
    }


def test_raw_json_response_is_blocked():
    guarded = guard_customer_response('{"assistant_message":"hello","required_step":"create_booking_draft"}')

    assert guarded.fallback_used is True
    assert guarded.reason_code == "raw_json_or_internal_payload"
    assert "assistant_message" not in guarded.message


def test_internal_tool_state_and_schema_terms_are_blocked():
    issue, terms = response_guard_issue(
        "The create_booking_draft tool is blocked in booking_confirmation_required by the schema."
    )

    assert issue == "internal_term_leak"
    assert "create_booking_draft" in terms


def test_fake_booking_success_without_write_result_is_blocked():
    guarded = guard_customer_response(
        "Booking request has been created successfully.",
        record_type="booking",
        fallback_message_key="booking",
    )

    assert guarded.fallback_used is True
    assert guarded.reason_code == "false_write_success_claim"
    assert "created successfully" not in guarded.message.lower()


def test_booking_success_with_valid_write_result_passes():
    message = "Booking request BK123 has been created. The Ravel team will follow up."
    guarded = guard_customer_response(
        message,
        write_result=_booking_write_result(),
        record_type="booking",
    )

    assert guarded.fallback_used is False
    assert guarded.message == message


def test_exact_inventory_count_is_hidden_by_fallback():
    guarded = guard_customer_response("There are 3 double rooms available for this trip.")

    assert guarded.fallback_used is True
    assert guarded.reason_code == "inventory_quantity_leak"
    assert "3 double rooms" not in guarded.message


def test_incomplete_english_and_arabic_are_blocked():
    english = guard_customer_response("Please send your")
    arabic = guard_customer_response("\u0645\u0646 \u0641\u0636\u0644\u0643 \u0627\u0631\u0633\u0644", language="ar")

    assert english.fallback_used is True
    assert english.reason_code == "obviously_incomplete_sentence"
    assert arabic.fallback_used is True
    assert arabic.reason_code == "obviously_incomplete_sentence"
    assert "\u0645\u0639\u0644\u0634" in arabic.message


def test_valid_arabic_and_english_messages_pass():
    ar = "\u062a\u0645\u0627\u0645\u060c \u0623\u0642\u062f\u0631 \u0623\u0633\u0627\u0639\u062f\u0643 \u0641\u064a \u0627\u062e\u062a\u064a\u0627\u0631 \u0627\u0644\u0631\u062d\u0644\u0629 \u0627\u0644\u0645\u0646\u0627\u0633\u0628\u0629."
    en = "Sure, I can help you compare the available trip options."

    assert guard_customer_response(ar, language="ar").message == ar
    assert guard_customer_response(en, language="en").message == en


def test_gemini_final_guard_blocks_internal_reply():
    guarded = GeminiAgent._guard_final_customer_reply(
        "workflow_policy says required_step=create_booking_draft",
        tool_events=[],
        language="en",
    )

    assert "workflow_policy" not in guarded
    assert "create_booking_draft" not in guarded
    assert "try that again" in guarded.lower()


def test_gemini_final_guard_allows_verified_booking_write_success():
    guarded = GeminiAgent._guard_final_customer_reply(
        "Booking request BK123 has been created. The Ravel team will follow up.",
        tool_events=[
            {
                "name": "create_booking_draft",
                "write": True,
                "result": {"write_result_contract": _booking_write_result()},
            }
        ],
        language="en",
    )

    assert guarded.startswith("Booking request BK123 has been created")
