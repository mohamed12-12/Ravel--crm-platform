# Phase 1 - Payment preference question

## What this phase delivers

The booking flow asks the traveler for the confirmed payment preference every time, once the team confirms whether that means payment currency or a separate payment method such as cash, bank transfer, or wallet.

## Current state (investigated, not assumed)

- The agent prompt currently says to collect "preferred payment currency" for a new traveler, limited to EGP or USD (`services/ai_agent/ai_agent_app/prompts/gemini_agent_system.md:18`, `services/ai_agent/ai_agent_app/prompts/gemini_agent_system.md:56-57`, `services/ai_agent/ai_agent_app/prompts/gemini_agent_system.md:64`).
- The deterministic/session flow has an `awaiting_currency` stage and asks "What is your preferred currency for payment? 1. EGP 2. USD" (`services/ai_agent/ai_agent_app/agent/session_flow.py:776-777`, `services/ai_agent/ai_agent_app/agent/session_flow.py:1401-1405`).
- The tool-calling workflow has a `currency_required` state and asks "Which payment currency do you prefer?" with EGP/USD choices (`services/ai_agent/ai_agent_app/agent/workflow_policy.py:568-574`, `services/ai_agent/ai_agent_app/agent/workflow_policy.py:611-617`).
- The web chat placeholder for the currency stage says "Type EGP or USD..." (`services/ai_agent/ai_agent_app/web/static/app.js:244`).
- Traveler and booking models have currency fields but no separate payment-method field: traveler `preferred_currency` is present (`apps/api/app/models/traveler.py:34`), booking `currency` is present (`apps/api/app/models/booking.py:30`), and no `payment_method` field was found by repo search.

Finding: "preferred payment method" is not the same implemented concept as the current payment-currency question unless the client is using "method" loosely. The code supports currency preference, not method/channel preference.

## Confirmed requirements this phase must satisfy

From the provided task attachment: "Payment preference question (confirmed: ask every time)." The attachment also instructs to first determine whether "preferred payment method" is the same as the existing currency question or a genuinely separate question, and to flag genuine ambiguity instead of guessing.

## Working assumptions (for Phases 6 and 7 specifically)

Not applicable.

## Design approach

- Do not implement until the business wording is confirmed.
- If the client means currency: remove returning-traveler shortcuts that skip currency, then ensure every booking path reaches the existing currency-required step before booking draft creation.
- If the client means method: add a separate `payment_method` concept to traveler/booking or booking-only data, define allowed values with the client, and collect it separately from EGP/USD.
- In either case, update the agent prompt, workflow policy, session serialization, web chat hints, booking create/update paths, and tests around the affected required step.

## Dependencies on other phases

This phase should be resolved before Phase 2 and Phase 7 because both touch payment/pricing language and could otherwise encode the wrong meaning.

## Risks specific to this phase

The risk is semantic, not technical: changing the existing currency step when the client meant bank/cash/wallet would ship the wrong workflow. The current code also has regression coverage around currency loops (`tests/test_golden_transcript_regressions.py:58`, `tests/test_golden_transcript_regressions.py:106-124`, `tests/test_golden_transcript_regressions.py:156-185`), so any implementation must avoid reintroducing that loop.

## Tests

Not run. No code was changed in this documentation-only pass.

## Live verification

Not run. No deployed app behavior was changed or verified in this documentation-only pass.

