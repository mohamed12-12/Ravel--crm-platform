"""CI guard for the write-result-status normalization boundary.

Every write_result_contract's "status" field must be interpreted through
write_result.normalize_write_result() -- never checked directly. This is
what makes the boundary "hard" rather than "please remember to use this."

Scope note: "status" alone is far too overloaded a word to grep for --
traveler account status ("Active"/"Inactive"), identity-lookup status
("found"/"not_found"/"duplicate"), and tool_contracts.py's own separate
"contract_status" ("validated"/"failed") are all legitimate, unrelated
vocabularies that also use a "status" key. The pattern below was tuned
against a real audit of this codebase (see write_result.py's module
docstring / the discovery report) to catch specifically a write-result
CONTRACT's status being read off a contract-like variable -- not every
"status" field in the agent directory.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent / "services" / "ai_agent" / "ai_agent_app" / "agent"
ALLOWED_FILES = {"write_result.py"}

# Matches e.g. contract.get("status"), write_result_contract["status"],
# existing_contract.get('status'), etc. -- a "contract"-named receiver
# followed by a status lookup.
_RAW_CONTRACT_STATUS_PATTERN = re.compile(
    r'contract(_[a-z]+)?\s*(\.get\(\s*["\']status["\']|\[\s*["\']status["\']\])'
)


class NoRawContractStatusChecksOutsideBoundaryTests(unittest.TestCase):
    def test_no_direct_write_result_contract_status_checks_outside_the_boundary(self) -> None:
        violations = []
        for path in sorted(AGENT_DIR.glob("*.py")):
            if path.name in ALLOWED_FILES:
                continue
            text = path.read_text(encoding="utf-8")
            for match in _RAW_CONTRACT_STATUS_PATTERN.finditer(text):
                line_number = text.count("\n", 0, match.start()) + 1
                violations.append(f"{path.name}:{line_number}: {match.group(0)}")
        self.assertEqual(
            violations,
            [],
            "Found direct write_result_contract status reads outside write_result.py. "
            "Route these through normalize_write_result() instead:\n" + "\n".join(violations),
        )


if __name__ == "__main__":
    unittest.main()
