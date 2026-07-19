from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentMemory:
    short_term: dict[str, Any] = field(default_factory=dict)
    long_term: dict[str, Any] = field(default_factory=dict)
    observations: list[dict[str, Any]] = field(default_factory=list)

    def update_short_term(self, **fields: Any) -> None:
        self.short_term.update({k: v for k, v in fields.items() if v not in {None, ""}})

    def update_long_term(self, **fields: Any) -> None:
        self.long_term.update({k: v for k, v in fields.items() if v not in {None, ""}})

    def add_observation(self, observation: dict[str, Any]) -> None:
        if observation:
            self.observations.append(dict(observation))

    def snapshot(self) -> dict[str, Any]:
        return {
            "short_term": dict(self.short_term),
            "long_term": dict(self.long_term),
            "observations": [dict(item) for item in self.observations[-20:]],
        }

