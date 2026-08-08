"""Real, enforced validation on whatever tool Gemini actually decided to
call (unlike planner.py's decision, which is informational only) --
tool_calling_runtime.py calls `validate_tool_args` before executing any
model-requested tool call.
"""
from __future__ import annotations

from typing import Any


class AgentSafetyLayer:
    def validate_tool_args(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        allowed_tools: set[str] | None = None,
    ) -> tuple[bool, str]:
        if allowed_tools is not None and tool_name not in allowed_tools:
            return False, f"Unsupported tool requested: {tool_name}"
        if not isinstance(args, dict):
            return False, f"Invalid arguments for {tool_name}"
        if tool_name in {"search_available_trips", "search_trips"}:
            trip_type = str(args.get("trip_type") or "").strip().lower()
            if trip_type and trip_type not in {"local", "international"}:
                return False, f"Unsupported trip type: {trip_type}"
        phone = str(args.get("raw_phone") or "").strip()
        if phone and len("".join(ch for ch in phone if ch.isdigit())) < 8:
            return False, f"Malformed phone number for {tool_name}"
        return True, ""

    def allows_write(self, tool_name: str) -> bool:
        return tool_name.startswith(("create_", "update_", "delete_"))
