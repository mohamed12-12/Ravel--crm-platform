"""The three possible outcomes of ActionValidator.validate_action()
(action_validator.py) and the shape of its answer -- reasons, missing
info, and warnings, used to build both the audit trail and the
customer-facing "what's still needed" message.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


APPROVED = "APPROVED"
REJECTED = "REJECTED"
NEED_MORE_INFORMATION = "NEED_MORE_INFORMATION"


@dataclass(frozen=True)
class ValidationResult:
    action: str
    decision: str
    reasons: list[str] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    traveler_id: str = ""
    session_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "action": self.action,
            "decision": self.decision,
            "reasons": list(self.reasons),
            "missing_information": list(self.missing_information),
            "warnings": list(self.warnings),
        }
        if self.traveler_id:
            payload["traveler_id"] = self.traveler_id
        if self.session_id:
            payload["session_id"] = self.session_id
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload
