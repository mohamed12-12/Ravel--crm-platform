from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentObservation:
    label: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "details": dict(self.details)}


def summarize_observation(tool_name: str, result: dict[str, Any]) -> AgentObservation:
    label_map = {
        "search_available_trips": "Trip matched",
        "find_traveler_by_phone": "Traveler found",
        "get_traveler_profile": "Traveler profile loaded",
        "get_traveler_trip_history": "Trip history loaded",
        "get_trip_details": "Trip details loaded",
        "get_trip_media": "Verified trip media loaded",
    }
    return AgentObservation(
        label=label_map.get(tool_name, "Tool executed"),
        details={"tool": tool_name, "keys": sorted(result.keys()) if isinstance(result, dict) else []},
    )
