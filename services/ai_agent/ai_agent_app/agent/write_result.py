from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from services.ai_agent.ai_agent_app.logger import agent_logger


class WriteOutcome(Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    UNKNOWN = "unknown"  # backend returned a status not in _KNOWN_STATUS_MAP


# Every write_result_contract status string actually produced by either
# backend, audited directly from the source (unified_service.py for SQLite,
# agent_crm_bridge.py's PostgresAgentBridgeService for Postgres, and the
# ai-agent-side write_tool_executor.py wrapper). Do not add a fallback/
# default branch that guesses -- anything not listed here must resolve to
# UNKNOWN, never SUCCESS or FAILURE by assumption.
_KNOWN_STATUS_MAP: dict[str, WriteOutcome] = {
    "success": WriteOutcome.SUCCESS,
    "created": WriteOutcome.SUCCESS,
    "updated": WriteOutcome.SUCCESS,
    "reused": WriteOutcome.SUCCESS,  # idempotent replay of an existing record
    "duplicate": WriteOutcome.SUCCESS,  # idempotent replay of an existing record
    "failed": WriteOutcome.FAILURE,
    "blocked": WriteOutcome.FAILURE,  # e.g. confirmation_required path
}


@dataclass(frozen=True)
class NormalizedWriteResult:
    outcome: WriteOutcome
    raw_status: str
    record_id: str
    backend: str  # "sqlite" | "postgres" | other, for logging/debugging
    raw_result: dict


def _contract_from(write_result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(write_result, dict):
        return {}
    contract = write_result.get("write_result_contract")
    if isinstance(contract, dict) and contract:
        return contract
    return write_result


def normalize_write_result(write_result: dict[str, Any] | None, *, backend: str = "") -> NormalizedWriteResult:
    """The single mandatory entry point for interpreting a write's outcome.

    Accepts either a write_result_contract dict directly, or a full write
    result containing one under "write_result_contract" -- every write
    handler must call this on a backend's raw response before doing
    anything else with it. No code outside write_response_gating.py (which
    is itself built on this function) may inspect a raw status string
    directly.
    """
    raw_result = write_result if isinstance(write_result, dict) else {}
    contract = _contract_from(raw_result)
    raw_status = str(contract.get("status") or "").strip().lower()
    record_id = str(
        contract.get("record_id")
        or raw_result.get("booking_id")
        or raw_result.get("lead_id")
        or raw_result.get("traveler_id")
        or raw_result.get("result_id")
        or ""
    ).strip()

    if not raw_status:
        # No formal contract at all -- a known legacy shape (a bare write
        # result with no write_result_contract), not a new backend
        # convention, so this does not warrant the loud UNKNOWN log below.
        # Callers decide what "no status" means for them (e.g. falling back
        # to record_id + an executed flag); the boundary itself has no
        # record-type-aware context to decide that on their behalf.
        outcome = WriteOutcome.UNKNOWN
    else:
        outcome = _KNOWN_STATUS_MAP.get(raw_status, WriteOutcome.UNKNOWN)
        if outcome is WriteOutcome.UNKNOWN:
            agent_logger.error(
                "UNRECOGNIZED write status -- backend=%s raw_status=%r raw_result=%r. "
                "This status is not in _KNOWN_STATUS_MAP and must be added explicitly.",
                backend,
                raw_status,
                raw_result,
            )

    return NormalizedWriteResult(
        outcome=outcome,
        raw_status=raw_status,
        record_id=record_id,
        backend=backend,
        raw_result=raw_result,
    )
