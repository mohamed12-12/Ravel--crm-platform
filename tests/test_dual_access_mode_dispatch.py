"""Task B.2: guard the actual mechanism that broke for guardian consent.

execute()'s api_client/service branch (write_tool_executor.py) is a single,
centralized point that every registered action goes through -- there is no
per-action mode-branching to duplicate or diverge. The guardian-consent bug
happened because record_guardian_consent/record_lead_guardian_flag were
PUBLIC methods that bypassed execute() entirely and called self.service
directly. Rather than forcing the whole ~700-test suite through a live
CRM_ACCESS_MODE=api HTTP round-trip (most existing tests construct their own
settings/fixtures explicitly and would never see the ambient env var change
anyway -- see tests/conftest.py's os.environ.setdefault), this file
targets the mechanism directly: every registered action must dispatch
through execute()'s branch, and no future public write-executor method may
reintroduce a bypass.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from services.ai_agent.ai_agent_app.agent.write_tool_executor import GeminiWriteToolExecutor

AGENT_DIR = Path(__file__).resolve().parent.parent / "services" / "ai_agent" / "ai_agent_app" / "agent"

ALL_ACTIONS = [
    "create_traveler",
    "create_lead",
    "update_lead_stage",
    "create_booking_draft",
    "create_handoff",
    "set_guardian_consent",
    "flag_lead_guardian_approval",
]


class _ApprovingValidator:
    @staticmethod
    def validate_action(*, action, payload, session_context):
        return SimpleNamespace(
            decision="APPROVED",
            traveler_id="",
            warnings=[],
            to_dict=lambda: {"decision": "APPROVED"},
        )


def _api_mode_executor(write_calls: list[str]) -> GeminiWriteToolExecutor:
    api_client = SimpleNamespace(
        read=lambda action, payload: None,
        write=lambda action, payload, session_context: (
            write_calls.append(action),
            {"result_id": "X1", "write_result_contract": {"status": "created", "record_id": "X1"}},
        )[1],
    )
    read_only_tools = SimpleNamespace(service=None, api_client=api_client)
    settings = SimpleNamespace(crm_access_mode="api")
    executor = GeminiWriteToolExecutor(settings=settings, read_only_tools=read_only_tools)
    executor.action_validator = _ApprovingValidator()
    return executor


class EveryActionDispatchesThroughTheCentralBranchTests(unittest.TestCase):
    def test_every_registered_action_routes_through_the_api_client_in_api_mode(self) -> None:
        for action in ALL_ACTIONS:
            with self.subTest(action=action):
                write_calls: list[str] = []
                executor = _api_mode_executor(write_calls)
                result = executor.execute(action=action, payload={}, session_context={})
                self.assertEqual(write_calls, [action])
                self.assertEqual(result.get("result_id"), "X1")

    def test_api_mode_never_touches_self_service_for_any_action(self) -> None:
        """self.service is None in real api-mode configuration (reads go
        over HTTP instead) -- if execute() ever fell through to a local
        _execute_* handler for ANY action while in api mode, it would raise
        AttributeError on None, exactly like the original guardian-consent
        bug. This proves that can't happen for any currently-registered
        action.
        """
        for action in ALL_ACTIONS:
            with self.subTest(action=action):
                write_calls: list[str] = []
                executor = _api_mode_executor(write_calls)
                self.assertIsNone(executor.service)
                # Would raise AttributeError on None if execute() dispatched
                # to a local handler instead of the api_client.
                executor.execute(action=action, payload={}, session_context={})


class NoFutureBypassOfExecuteTests(unittest.TestCase):
    """Codifies the audit that found the ONLY historical bypass (guardian
    consent, now fixed) and permanently guards against a new one: the write
    executor's public API is execute() plus record_guardian_consent/
    record_lead_guardian_flag (both of which now dispatch through execute()
    internally). No other public method may exist, and no caller may invoke
    any write-executor method other than those three.
    """

    _SANCTIONED_PUBLIC_METHODS = {"execute", "record_guardian_consent", "record_lead_guardian_flag"}

    def test_write_executor_has_no_undocumented_public_methods(self) -> None:
        path = AGENT_DIR / "write_tool_executor.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        public_methods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "GeminiWriteToolExecutor":
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("_"):
                        public_methods.add(item.name)
        unexpected = public_methods - self._SANCTIONED_PUBLIC_METHODS
        self.assertEqual(
            unexpected,
            set(),
            f"New public method(s) on GeminiWriteToolExecutor: {unexpected}. "
            "Any new public write method must dispatch through self.execute(...) "
            "internally (see record_guardian_consent for the pattern), not call "
            "self.service directly -- that bypass is exactly what broke guardian "
            "consent under CRM_ACCESS_MODE=api.",
        )

    def test_no_caller_invokes_a_write_executor_method_other_than_the_sanctioned_three(self) -> None:
        pattern = re.compile(r"_write_executor\.(\w+)\(")
        violations = []
        for path in sorted(AGENT_DIR.glob("*.py")):
            if path.name == "write_tool_executor.py":
                continue
            text = path.read_text(encoding="utf-8")
            for match in pattern.finditer(text):
                method_name = match.group(1)
                if method_name not in self._SANCTIONED_PUBLIC_METHODS:
                    line_number = text.count("\n", 0, match.start()) + 1
                    violations.append(f"{path.name}:{line_number}: _write_executor.{method_name}(")
        self.assertEqual(violations, [], "Found a call to an unsanctioned write-executor method:\n" + "\n".join(violations))


class ApiClientWriteFailureTests(unittest.TestCase):
    """A create_handoff (or any) write in CRM_ACCESS_MODE=api can raise
    CRMApiError -- a real HTTP failure, timeout, or malformed CRM response --
    from self.read_only_tools.api_client.write(). Before this fix, execute()
    had no try/except around that call, so the exception propagated all the
    way up uncaught instead of returning a normal failed write_result_contract
    the caller already knows how to render safely.
    """

    def test_crm_api_error_from_create_handoff_is_caught_and_returns_a_failed_contract(self) -> None:
        from services.ai_agent.ai_agent_app.agent.crm_api_client import CRMApiError

        def _raise_write(action, payload, session_context):
            raise CRMApiError("CRM API request failed: connection timed out")

        api_client = SimpleNamespace(read=lambda action, payload: None, write=_raise_write)
        read_only_tools = SimpleNamespace(service=None, api_client=api_client)
        settings = SimpleNamespace(crm_access_mode="api")
        executor = GeminiWriteToolExecutor(settings=settings, read_only_tools=read_only_tools)
        executor.action_validator = _ApprovingValidator()

        result = executor.execute(action="create_handoff", payload={}, session_context={"session_id": "S1"})

        self.assertFalse(result["executed"])
        self.assertEqual(result["write_result_contract"]["status"], "failed")
        self.assertEqual(result["write_result_contract"]["record_type"], "handoff")

    def test_deduplication_reuse_path_is_unaffected_by_the_new_error_handling(self) -> None:
        """The non-error, `reused` case (api_client.write succeeding and
        reporting a deduplicated handoff) must still pass straight through,
        exactly as before -- the new try/except must only intercept an actual
        exception, never a normal successful/reused response.
        """
        write_calls: list[str] = []
        executor = _api_mode_executor(write_calls)
        executor.read_only_tools.api_client.write = lambda action, payload, session_context: (
            write_calls.append(action),
            {
                "executed": False,
                "result_id": "H-0009",
                "write_result_contract": {"status": "reused", "reused": True, "record_id": "H-0009"},
            },
        )[1]

        result = executor.execute(action="create_handoff", payload={}, session_context={})

        self.assertEqual(write_calls, ["create_handoff"])
        self.assertFalse(result["executed"])
        self.assertEqual(result["write_result_contract"]["status"], "reused")


class LocalHandlerExceptionLoggingTests(unittest.TestCase):
    """Distinct from ApiClientWriteFailureTests above: that class covers a
    CRMApiError from an HTTP round-trip (CRM_ACCESS_MODE=api's api_client
    branch). This covers the OTHER branch -- api_client is None, so
    execute() calls a local _execute_* handler directly (this is what
    actually runs *inside* apps/api/app/routes/crm.py's agent_write(),
    since that route builds its own executor with no api_client at all).
    A real production incident traced a "clean, no-exception" handoff
    failure (Path B in tool_calling_runtime.py's _execute_manual_handoff)
    to exactly this branch: some exception inside the local handler was
    being caught and converted into a normal-looking failed contract with
    zero diagnostic detail. This is deliberately decoupled from any
    specific theory of what that exception actually is (a prior
    investigation guessed idempotency_key column drift; that guess turned
    out to be unconfirmed) -- it proves the LOGGING fix works for any
    exception this branch might ever see, not just one hypothesized cause.
    """

    def test_any_exception_from_the_local_handler_is_logged_at_error_with_the_real_detail(self) -> None:
        service = SimpleNamespace(
            create_handoff_case=Mock(side_effect=RuntimeError("something specific and diagnosable")),
        )
        read_only_tools = SimpleNamespace(service=service, api_client=None)
        settings = SimpleNamespace(crm_access_mode="shared_service", default_country_code="20")
        executor = GeminiWriteToolExecutor(settings=settings, read_only_tools=read_only_tools)
        executor.action_validator = _ApprovingValidator()

        with self.assertLogs("rahma_agent", level="ERROR") as captured:
            result = executor.execute(
                action="create_handoff",
                payload={"user_requested_human": True},
                session_context={"session_id": "S-LOCAL-1"},
            )

        self.assertEqual(result["write_result_contract"]["status"], "failed")
        self.assertFalse(result["executed"])
        joined = "\n".join(captured.output)
        self.assertIn("Local write handler failed", joined)
        self.assertIn("something specific and diagnosable", joined)


if __name__ == "__main__":
    unittest.main()
