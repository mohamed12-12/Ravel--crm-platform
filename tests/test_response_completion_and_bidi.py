from __future__ import annotations

from pathlib import Path

from services.ai_agent.ai_agent_app.agent.response_format import response_completeness_issue


ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "services" / "ai_agent" / "ai_agent_app" / "web" / "static" / "app.js"
STYLES_CSS = ROOT / "services" / "ai_agent" / "ai_agent_app" / "web" / "static" / "styles.css"


def test_complete_arabic_response_passes_validation() -> None:
    text = "يمكنني مساعدتك في الاستفسار عن رحلة Today Demo. هل تريد متابعة الحجز الحالي؟"

    assert response_completeness_issue(text) == ""


def test_complete_english_response_passes_validation() -> None:
    assert response_completeness_issue("I can help with Today Demo. Would you like to see the booking status?") == ""


def test_arabic_connector_ending_is_rejected() -> None:
    assert response_completeness_issue("يمكنني مساعدتك في الاستفسار عن رحلة Today Demo أو") == "suspicious_incomplete_ending"


def test_english_connector_ending_is_rejected() -> None:
    assert response_completeness_issue("I can help with the booking and") == "suspicious_incomplete_ending"


def test_valid_short_arabic_reply_is_not_rejected() -> None:
    assert response_completeness_issue("تم.") == ""


def test_multiline_numbered_arabic_list_passes_validation() -> None:
    text = "\n".join(
        [
            "يمكنني مساعدتك في:",
            "1) متابعة حجز رحلة Today Demo.",
            "2) عرض الصور الرسمية.",
            "3) تحويلك إلى موظف خدمة العملاء.",
            "هل تريد المتابعة؟",
        ]
    )

    assert response_completeness_issue(text) == ""


def test_empty_numbered_list_item_is_rejected() -> None:
    assert response_completeness_issue("يمكنني مساعدتك في:\n1) متابعة الحجز.\n2) ") == "empty_list_item"


def test_chat_renderer_sets_direction_per_message_and_uses_bidi_isolation() -> None:
    source = APP_JS.read_text(encoding="utf-8")

    assert "function detectTextDirection" in source
    assert "div.dir = dir" in source
    assert "document.createElement(\"bdi\")" in source
    assert "bdi.textContent = token" in source
    assert "document.createElement(\"ol\")" in source
    assert "document.createElement(\"li\")" in source


def test_chat_renderer_does_not_inject_message_text_as_html() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    render_body = source.split("function renderMessages", 1)[1].split("function renderSession", 1)[0]

    assert "message.text).replaceAll" not in render_body
    assert "div.innerHTML" not in render_body
    assert "appendBidiText(div, message.text" not in render_body


def test_chat_css_contains_directional_rules() -> None:
    source = STYLES_CSS.read_text(encoding="utf-8")

    assert ".chat-bubble.dir-rtl" in source
    assert ".chat-bubble.dir-ltr" in source
    assert "unicode-bidi: isolate" in source
    assert ".chat-list[dir=\"rtl\"]" in source
