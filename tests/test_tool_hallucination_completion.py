from __future__ import annotations

from types import SimpleNamespace

from services.ai_agent.ai_agent_app.agent.gemini_agent import GeminiAgent
from services.ai_agent.ai_agent_app.agent.tool_registry import build_agent_tool_registry
from test_phase2_gemini_tool_loop import LoopProviderStub, function_call_response, text_response


def _settings(*, enforcement: bool = True, router_mode: str = "dry_run") -> SimpleNamespace:
    return SimpleNamespace(
        ai_agent_mode="gemini",
        agent_tool_router_mode=router_mode,
        agent_write_tool_enforcement=enforcement,
        default_country_code="20",
        ai_agent_system_prompt="You are Ravel Agent.",
        gemini_model="test-model",
    )


class FakeReadOnlyTools:
    service = object()
    api_client = None

    def __init__(self, *, malformed: bool = False, fail: bool = False) -> None:
        self.malformed = malformed
        self.fail = fail
        self.write_count = 0

    def search_available_trips(self, **kwargs):
        if self.fail:
            raise RuntimeError("sqlite internal table failure")
        if self.malformed:
            return ["not", "a", "contract"]
        return {
            "status": "found",
            "trips": [{"trip_id": "RT-GROUNDED", "trip_name": "Grounded Trip", "public_price": "500 USD"}],
            "open_trips": [{"trip_id": "RT-GROUNDED", "trip_name": "Grounded Trip", "public_price": "500 USD"}],
        }


class FakeWriteExecutor:
    def __init__(self, result: dict | None = None) -> None:
        self.calls = []
        self.result = result or {}

    def execute(self, *, action, payload, session_context):
        self.calls.append({"action": action, "payload": payload, "session_context": session_context})
        return dict(self.result)


def _agent(provider, read_tools=None, *, write_tools: bool = False, enforcement: bool = True) -> GeminiAgent:
    agent = GeminiAgent(
        settings=_settings(enforcement=enforcement),
        provider=provider,
        read_only_tools=read_tools or FakeReadOnlyTools(),
        action_validator=SimpleNamespace(),
        tool_registry=build_agent_tool_registry(include_write_tools=write_tools, include_validation_tool=False),
        write_tools_enabled=write_tools,
        max_tool_calls=2,
    )
    return agent


def test_write_tool_blocked_from_wrong_state_when_write_enforcement_enabled():
    fake_write = FakeWriteExecutor(
        {
            "write_result_contract": {
                "status": "success",
                "executed": True,
                "record_type": "booking",
                "record_id": "BK-SHOULD-NOT-RUN",
            }
        }
    )
    provider = LoopProviderStub(
        [
            function_call_response("create_booking_draft", {"traveler_id": "TR1", "trip_id": "RT1", "room_type": "Double"}),
            text_response("Booking request BK-SHOULD-NOT-RUN has been created."),
        ]
    )
    agent = _agent(provider, write_tools=True, enforcement=True)
    agent.write_executor = fake_write

    result = agent.respond(
        user_message="book it",
        session_context={"session_id": "wrong-state", "stage": "identity_required", "language": "en"},
    )

    assert fake_write.calls == []
    event = result["tool_requests"][0]
    assert event["result"]["write_result_contract"]["status"] == "blocked"
    assert event["executed"] is False
    assert "BK-SHOULD-NOT-RUN" not in result["reply"]


def test_read_tool_does_not_create_write_event():
    provider = LoopProviderStub(
        [
            function_call_response("search_available_trips", {"trip_type": "international"}),
            text_response("I found the trip options returned by CRM."),
        ]
    )
    agent = _agent(provider, read_tools=FakeReadOnlyTools())

    result = agent.respond(
        user_message="show international trips",
        session_context={"session_id": "read-only", "workflow_policy": {"identity_verified": True}, "language": "en"},
    )

    assert result["write_results"] == []
    assert result["tool_requests"][0]["write"] is False


def test_unknown_tool_is_blocked_without_customer_internal_leak():
    provider = LoopProviderStub([function_call_response("magic_write", {"booking_id": "BK-FAKE"})])
    agent = _agent(provider)

    result = agent.respond(user_message="do magic", session_context={"session_id": "unknown-tool", "language": "en"})

    assert result["error"] == "tool_request_failed"
    assert "magic_write" not in result["reply"]
    assert "magic_write" not in result["error"]


def test_invalid_tool_args_are_safe_and_do_not_leak_tool_name_to_customer():
    provider = LoopProviderStub([function_call_response("get_trip_details", {})])
    agent = _agent(provider)

    result = agent.respond(user_message="show trip", session_context={"session_id": "bad-args", "language": "en"})

    assert result["error"] == "tool_request_failed"
    assert "get_trip_details" not in result["reply"]


def test_malformed_read_tool_result_blocks_ungrounded_trip_price_and_id():
    provider = LoopProviderStub(
        [
            function_call_response("search_available_trips", {"trip_type": "international"}),
            text_response("Trip RT-FAKE is available for 999 USD on 2026-10-01."),
        ]
    )
    agent = _agent(provider, read_tools=FakeReadOnlyTools(malformed=True))

    result = agent.respond(
        user_message="show international trips",
        session_context={"session_id": "malformed-read", "workflow_policy": {"identity_verified": True}, "language": "en"},
    )

    event = result["tool_requests"][0]
    assert event["result"]["contract_status"] == "failed"
    assert "RT-FAKE" not in result["reply"]
    assert "999 USD" not in result["reply"]
    assert "2026-10-01" not in result["reply"]


def test_tool_failure_does_not_leak_internal_exception_details():
    provider = LoopProviderStub(
        [
            function_call_response("search_available_trips", {"trip_type": "international"}),
            text_response("RuntimeError sqlite internal table failure"),
        ]
    )
    agent = _agent(provider, read_tools=FakeReadOnlyTools(fail=True))

    result = agent.respond(
        user_message="show international trips",
        session_context={"session_id": "read-fail", "workflow_policy": {"identity_verified": True}, "language": "en"},
    )

    event = result["tool_requests"][0]
    assert event["result"]["errors"] == [{"code": "tool_execution_failed"}]
    assert "sqlite" not in result["reply"].lower()
    assert "runtimeerror" not in result["reply"].lower()


def test_malformed_write_result_does_not_produce_customer_success():
    fake_write = FakeWriteExecutor({"assistant_message": "Booking request BK-FAKE has been created."})
    provider = LoopProviderStub(
        [
            function_call_response("create_booking_draft", {"traveler_id": "TR1", "trip_id": "RT1", "room_type": "Double"}),
            text_response("Booking request BK-FAKE has been created."),
        ]
    )
    agent = _agent(provider, write_tools=True, enforcement=False)
    agent.write_executor = fake_write

    result = agent.respond(
        user_message="book it",
        session_context={
            "session_id": "malformed-write",
            "stage": "booking_confirmation_required",
            "workflow_policy": {"identity_verified": True},
            "language": "en",
        },
    )

    event = result["tool_requests"][0]
    assert event["result"]["write_result_contract"]["status"] == "failed"
    assert "BK-FAKE" not in result["reply"]
    assert "created" not in result["reply"].lower()
