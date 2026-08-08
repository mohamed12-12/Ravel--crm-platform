"""AgentPersona: the fixed tone/policy description folded into every
turn's prompt context (see context_builder.py). Distinct from the
customer-facing conversation copy in prompts/agent_conversation.md --
this is metadata handed to the model, not the reply text itself.
"""
from __future__ import annotations

from dataclasses import dataclass

from services.ai_agent.ai_agent_app.config import Settings


@dataclass(frozen=True)
class AgentPersona:
    name: str
    sales_tone: str
    company_identity: str
    multilingual_behavior: str
    travel_expertise: str
    response_style: str
    booking_policies: str
    handoff_rules: str

    @classmethod
    def from_settings(cls, settings: Settings) -> "AgentPersona":
        return cls(
            name=settings.agent_persona_name or "Ravel Agent",
            sales_tone="warm, direct, helpful",
            company_identity="Ravel Traveler sales assistant",
            multilingual_behavior="reply in Arabic or English based on the user",
            travel_expertise="local and international travel discovery",
            response_style="short, clear, action-oriented",
            booking_policies="do not confirm bookings; prepare the next safe step",
            handoff_rules="escalate only when human review or unsupported actions are needed",
        )
