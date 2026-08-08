"""Thin wrapper around the tool registry (tool_registry.py) for
ProductionAgentCoordinator: `.describe()` produces the tool list shown in
the prompt context, `.registry`/`.get()` back the real safety-layer
validation against actually-allowed tool names.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.ai_agent.ai_agent_app.agent.tool_registry import ToolSpec


@dataclass(frozen=True)
class ManagedTool:
    name: str
    description: str
    parameters: dict[str, Any]
    permissions: str
    expected_output: str
    validation: str

    @classmethod
    def from_spec(cls, spec: ToolSpec) -> "ManagedTool":
        return cls(
            name=spec.name,
            description=spec.description,
            parameters=dict(spec.input_schema),
            permissions="write" if spec.allowed_write else "read",
            expected_output=str(spec.output_schema.get("type") or "object"),
            validation="Gemini-compatible schema",
        )


class ToolManager:
    def __init__(self, registry: dict[str, ToolSpec]) -> None:
        self.registry = dict(registry)

    def describe(self) -> list[ManagedTool]:
        return [ManagedTool.from_spec(spec) for spec in self.registry.values()]

    def get(self, name: str) -> ToolSpec | None:
        return self.registry.get(name)

