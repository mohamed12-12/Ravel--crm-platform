"""AgentPersona: the fixed tone/policy description folded into every
turn's prompt context (see context_builder.py). Distinct from the
customer-facing conversation copy in prompts/agent_conversation.md --
this is metadata handed to the model, not the reply text itself.
"""
from __future__ import annotations

from dataclasses import dataclass

from services.ai_agent.ai_agent_app.config import Settings
from services.ai_agent.ai_agent_app.agent.brand_dna import BRAND_DNA


@dataclass(frozen=True)
class AgentPersona:
    name: str
    sales_tone: str
    company_identity: str
    multilingual_behavior: str
    travel_expertise: str
    response_style: str
    brand_essence: str
    target_audience: str
    customer_personas: str
    audience_pain_points: str
    brand_messaging: str
    booking_policies: str
    handoff_rules: str

    @classmethod
    def from_settings(cls, settings: Settings) -> "AgentPersona":
        return cls(
            name=settings.agent_persona_name or "Ravel Agent",
            sales_tone=BRAND_DNA["tone_of_voice"],
            company_identity="Ravel Traveler social group travel sales assistant",
            multilingual_behavior="reply in Arabic or English based on the user",
            travel_expertise="curated group travel discovery with a focus on international experiences",
            response_style=BRAND_DNA["messaging_principles"],
            brand_essence=BRAND_DNA["brand_essence"],
            target_audience=BRAND_DNA["primary_audience"],
            customer_personas=BRAND_DNA["customer_personas"],
            audience_pain_points=BRAND_DNA["pain_points"],
            brand_messaging=BRAND_DNA["strategic_messages"],
            booking_policies=f"do not confirm bookings; prepare the next safe step; {BRAND_DNA['hard_boundary']}",
            handoff_rules="escalate only when human review or unsupported actions are needed",
        )
