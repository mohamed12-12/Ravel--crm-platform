"""Enforces the runtime contract boundary every tool call must pass
through: validates a tool call's arguments against its ToolSpec's
JSON-schema `required`/type fields before execution, and normalizes
whatever a tool raises or returns into a safe, customer-presentable
result afterward (SAFE_TOOL_ERROR_MESSAGES) rather than ever surfacing a
raw exception. See services/ai_agent/TOOL_INVENTORY_AND_CONTRACTS.md's
"Contract Rules" section for the human-readable version of these rules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.ai_agent.ai_agent_app.agent.tool_registry import ToolSpec
from services.ai_agent.ai_agent_app.agent.write_result import WriteOutcome, normalize_write_result


SAFE_TOOL_ERROR_MESSAGES = {
    "read": "I could not verify that information right now. Please try again, or I can connect you with a human agent.",
    "lead": "I could not save your request right now. Please try again.",
    "booking": "I could not create the booking request yet. Please review or confirm the details again.",
    "handoff": "I could not submit the human handoff request right now. Please try again or contact us directly.",
    "document": "I could not save the document right now. Please try again.",
    "tool": "I could not complete that request right now. Please try again.",
}


@dataclass(frozen=True)
class ToolContractValidation:
    ok: bool
    reason_code: str = ""
    errors: list[str] = field(default_factory=list)


def validate_tool_input_contract(spec: ToolSpec, args: Any) -> ToolContractValidation:
    if not isinstance(args, dict):
        return ToolContractValidation(False, "invalid_tool_arguments", ["expected_object"])

    schema = spec.input_schema if isinstance(spec.input_schema, dict) else {}
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    for field in required:
        value = args.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            return ToolContractValidation(False, "missing_required_tool_argument", [str(field)])

    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    for key, value in args.items():
        if value is None:
            continue
        field_schema = properties.get(key) if isinstance(properties.get(key), dict) else {}
        expected = field_schema.get("type") if isinstance(field_schema, dict) else ""
        if _value_matches_json_type(value, str(expected or "")):
            continue
        return ToolContractValidation(False, "invalid_tool_argument_type", [str(key)])

    return ToolContractValidation(True)


def normalize_tool_result_contract(
    *,
    spec: ToolSpec,
    result: Any = None,
    exception: Exception | None = None,
    session_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record_type = _record_type_for_tool(spec.name)
    if exception is not None:
        return _safe_failed_result(spec=spec, record_type=record_type, reason_code="tool_execution_failed", session_context=session_context)
    if not isinstance(result, dict):
        return _safe_failed_result(spec=spec, record_type=record_type, reason_code="malformed_tool_result", session_context=session_context)

    normalized = dict(result)
    if spec.allowed_write:
        contract = normalized.get("write_result_contract")
        if not isinstance(contract, dict):
            nested = normalized.get("write_result") if isinstance(normalized.get("write_result"), dict) else {}
            contract = nested.get("write_result_contract") if isinstance(nested.get("write_result_contract"), dict) else None
        if not isinstance(contract, dict):
            return _safe_failed_result(spec=spec, record_type=record_type, reason_code="malformed_write_result", session_context=session_context)
        write_outcome = normalize_write_result({"write_result_contract": contract}, backend="").outcome
        record_id = str(contract.get("record_id") or normalized.get("result_id") or "").strip()
        if write_outcome is WriteOutcome.SUCCESS and not record_id:
            return _safe_failed_result(spec=spec, record_type=record_type, reason_code="missing_write_record_id", session_context=session_context)
        normalized.setdefault("executed", bool(contract.get("executed", write_outcome is WriteOutcome.SUCCESS)))
        normalized.setdefault("result_id", record_id)
        normalized["write_result_contract"] = dict(contract)
        normalized.setdefault("write_result", {"write_result_contract": dict(contract)})
    else:
        if str(normalized.get("status") or "").strip().lower() == "error":
            normalized["warnings"] = []
            normalized["errors"] = [{"code": "tool_execution_failed"}]
            normalized["safe_customer_message"] = SAFE_TOOL_ERROR_MESSAGES["read"]
        normalized.setdefault("executed", True)

    normalized.setdefault("contract_status", "validated")
    return normalized


def safe_tool_input_error_result(*, spec: ToolSpec, reason_code: str, errors: list[str], session_context: dict[str, Any] | None = None) -> dict[str, Any]:
    result = _safe_failed_result(
        spec=spec,
        record_type=_record_type_for_tool(spec.name),
        reason_code=reason_code,
        session_context=session_context,
    )
    result["contract_errors"] = list(errors)
    return result


def _safe_failed_result(
    *,
    spec: ToolSpec,
    record_type: str,
    reason_code: str,
    session_context: dict[str, Any] | None,
) -> dict[str, Any]:
    message = SAFE_TOOL_ERROR_MESSAGES.get(record_type, SAFE_TOOL_ERROR_MESSAGES["tool"])
    audit = {
        "session_id": str((session_context or {}).get("session_id") or ""),
        "tool_group": spec.metadata.get("tool_group", ""),
        "reason_code": reason_code,
    }
    base = {
        "status": "failed",
        "executed": False,
        "result_id": "",
        "errors": [{"code": reason_code}],
        "warnings": [],
        "assistant_message": message,
        "reply": message,
        "safe_customer_message": message,
        "audit": audit,
        "contract_status": "failed",
    }
    if spec.allowed_write:
        contract = {
            "status": "failed",
            "executed": False,
            "reused": False,
            "record_type": record_type,
            "record_id": "",
            "idempotency_key": "",
            "customer_confirmation_allowed": False,
            "error_code": reason_code,
            "safe_customer_message_key": f"{record_type}.{reason_code}",
            "audit": audit,
        }
        base["write_result_contract"] = contract
        base["write_result"] = {"write_result_contract": contract}
    return base


def _record_type_for_tool(tool_name: str) -> str:
    normalized = str(tool_name or "").strip().lower()
    if "booking" in normalized:
        return "booking"
    if "handoff" in normalized:
        return "handoff"
    if "lead" in normalized:
        return "lead"
    if "passport" in normalized or "document" in normalized:
        return "document"
    if normalized.startswith(("search_", "find_", "get_", "lookup_", "validate_")):
        return "read"
    return "tool"


def _value_matches_json_type(value: Any, expected: str) -> bool:
    if not expected:
        return isinstance(value, (str, int, float, bool, dict, list))
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    return True
