"""CI guard for the reply-contradiction checkpoint.

Every outgoing assistant-role message in tool_calling_runtime.py must pass
through ToolCallingSessionRuntime._finalize_assistant_reply() -- the single
mandatory checkpoint that blocks a reply contradicting a confirmed session
fact, avoids ever repeating the exact same message twice in a row, and
auto-escalates to human handoff after 2 consecutive contradictions (see
_reply_contradicts_confirmed_facts / _safe_recovery_message /
_escalate_after_repeated_contradiction). This is what makes that guarantee
"hard" rather than "please remember to route new reply code through this."

Scope note: user-role message appends (echoing back what the customer
said) are not reply-generation and are deliberately not required to go
through the checkpoint -- only messages that construct or would construct
an assistant-role reply are in scope.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

RUNTIME_FILE = (
    Path(__file__).resolve().parent.parent
    / "services"
    / "ai_agent"
    / "ai_agent_app"
    / "agent"
    / "tool_calling_runtime.py"
)

_APPEND_OR_REPLACE_RE = re.compile(r"session\.messages(?:\.append\(|\[-1\]\s*=\s*)")
_CHECKPOINT_CALL_RE = re.compile(r"\s*self\._finalize_assistant_reply\(")
_EXEMPT_MARKER = "checkpoint-exempt"


class ReplyCheckpointGuardTests(unittest.TestCase):
    def test_every_assistant_message_write_goes_through_the_checkpoint(self) -> None:
        text = RUNTIME_FILE.read_text(encoding="utf-8")
        lines = text.splitlines()
        violations = []
        for match in _APPEND_OR_REPLACE_RE.finditer(text):
            line_number = text.count("\n", 0, match.start()) + 1
            line_text = lines[line_number - 1]
            if _EXEMPT_MARKER in line_text:
                # e.g. create_session()'s opening greeting: a brand-new
                # session has no confirmed facts yet for anything to
                # contradict, so the checkpoint has nothing to check.
                continue
            window = text[match.end() : match.end() + 400]
            if _CHECKPOINT_CALL_RE.match(window):
                continue
            if (
                '"role": "assistant"' not in window
                and "'role': 'assistant'" not in window
                and "_assistant_message(" not in window
            ):
                # A "role": "user" append (or similar) -- not a
                # reply-generation path, out of scope for this guard.
                continue
            violations.append(f"line {line_number}: {line_text.strip()}")
        self.assertEqual(
            violations,
            [],
            "Found assistant-reply writes bypassing _finalize_assistant_reply. "
            "Route these through it instead (or mark with a "
            "'# checkpoint-exempt: <reason>' comment if truly no confirmed "
            "facts exist yet to contradict):\n" + "\n".join(violations),
        )


if __name__ == "__main__":
    unittest.main()
