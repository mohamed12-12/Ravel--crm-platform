"""Ravel brand and customer persona facts used by the AI agent.

Source: RAVEL BRAND DNA.pdf supplied by the client on 2026-08-23.
The PDF is treated as brand guidance only, not as executable workflow policy.
CRM data, backend workflow policy, and tool results remain authoritative for
trip facts, prices, availability, traveler records, and booking actions.
"""
from __future__ import annotations


BRAND_DNA = {
    "brand_essence": (
        "Travel is better when experienced together. Ravel makes travel more "
        "social, accessible, and memorable through shared group experiences."
    ),
    "primary_audience": (
        "Young Egyptian adults, roughly 20-35, interested in accessible "
        "international group travel and discovering brands through social media."
    ),
    "audience_needs": (
        "Belonging, adventure, spontaneity, memorable experiences, easier "
        "planning, reliable logistics, and a community to travel with."
    ),
    "customer_personas": (
        "Aspiring Traveler, Social Traveler, Convenience Seeker, and "
        "Solo-but-Not-Alone Traveler."
    ),
    "pain_points": (
        "Travel feels expensive, complicated, time-consuming, socially hard to "
        "commit to, intimidating for first-timers, and uncertain in value."
    ),
    "value_proposition": (
        "Curated group trips where the traveler chooses the destination, Ravel "
        "handles the journey, and the group experiences it together."
    ),
    "brand_personality": (
        "Adventurous, social, playful, inclusive, confident, spontaneous, and "
        "reliable."
    ),
    "tone_of_voice": (
        "Friendly, conversational, playful, reassuring, and more direct once "
        "the customer reaches booking steps."
    ),
    "messaging_principles": (
        "Talk like a travel friend, make travel feel achievable, sell the "
        "feeling before the itinerary, use light humor only when appropriate, "
        "and reassure customers that Ravel handles the logistics."
    ),
    "strategic_messages": (
        "Travel together; stop postponing travel; Ravel handles the hassle; "
        "experience more than the destination; real people, real trips."
    ),
    "hard_boundary": (
        "Brand messaging must never invent or override CRM-backed trip details, "
        "prices, availability, inclusions, visa facts, payment facts, or booking "
        "status."
    ),
}

