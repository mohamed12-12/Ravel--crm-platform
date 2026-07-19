from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PlannerDecision:
    action: str
    tool_name: str = ""
    ask_user: bool = False
    reason: str = ""
    pending_tasks: list[str] = field(default_factory=list)


class AgentPlanner:
    def plan(self, *, goal: str, context: dict[str, Any], tools: list[str]) -> PlannerDecision:
        text = str(context.get("last_user_message") or "").lower()
        workflow = context.get("workflow_policy") if isinstance(context.get("workflow_policy"), dict) else {}
        allowed_tools = set(workflow.get("allowed_tools") or tools)

        if workflow.get("state") == "identity_required":
            return PlannerDecision(action="ask", ask_user=True, reason="WhatsApp number required before CRM workflow")
        if workflow.get("state") == "identity_lookup_pending" and "find_traveler_by_phone" in allowed_tools:
            return PlannerDecision(action="tool", tool_name="find_traveler_by_phone", reason="Phone collected; CRM lookup required")
        if (
            any(token in text for token in ("trip", "travel", "available"))
            and "search_available_trips" in tools
            and "search_available_trips" in allowed_tools
        ):
            return PlannerDecision(action="tool", tool_name="search_available_trips", reason="Trip search requested")
        if (
            any(token in text for token in ("profile", "whatsapp", "phone", "crm"))
            and "find_traveler_by_phone" in allowed_tools
        ):
            return PlannerDecision(action="tool", tool_name="find_traveler_by_phone", reason="Identity lookup requested")
        return PlannerDecision(action="ask", ask_user=True, reason="Need more detail")
