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

**Timing re-check against the new client clarification (ask after trip selection/details, not during intake):** traced both live state machines end to end.
- Deterministic flow (`session_flow.py`): `awaiting_currency` is only entered from `_advance_after_flight_decision` (`services/ai_agent/ai_agent_app/agent/session_flow.py:1383-1400`), which itself only runs after passport handling (`services/ai_agent/ai_agent_app/agent/session_flow.py:766-792`) — i.e. after trip type, trip selection, room type, group size, and flight preference are already collected. The passport-upload branch also routes into `awaiting_currency` only once the passport step is satisfied (`services/ai_agent/ai_agent_app/agent/session_flow.py:776-779`). No code path sets `awaiting_currency` earlier than this.
- Tool-calling flow (`workflow_policy.py`): `currency_required`/`collect_payment_currency` is returned only after the group-nationality, flight-option, and passport checks all pass (`services/ai_agent/ai_agent_app/agent/workflow_policy.py:404-489`); the new-traveler onboarding decision path (`services/ai_agent/ai_agent_app/agent/workflow_policy.py:557-619`) never returns a currency step, so onboarding cannot reach it either.
- Conclusion: both implemented flows already ask for currency only after trip and room/group details are collected, immediately before booking-draft creation. The client's new timing clarification is already satisfied by the current code — **no ordering change is required for Phase 1.** The only remaining open item is the terminology question below.

## Confirmed requirements this phase must satisfy

From the provided task attachment: "Payment preference question (confirmed: ask every time)." The attachment also instructs to first determine whether "preferred payment method" is the same as the existing currency question or a genuinely separate question, and to flag genuine ambiguity instead of guessing.

**New client clarification (received after this document was first drafted):** the payment-method/currency question must be asked after the customer has chosen their trip and its details (room type, headcount, etc.), not during initial intake and not before trip selection. As shown in "Current state" above, this ordering is already how both `session_flow.py` and `workflow_policy.py` behave today, so this clarification requires no code change — it confirms the existing behavior rather than changing it.

## Working assumptions (for Phases 6 and 7 specifically)

Not applicable.

## Design approach

- No ordering change is needed: both `session_flow.py` and `workflow_policy.py` already gate the currency question behind trip type, trip selection, room type, group size, and flight/passport collection. Do not touch the state-machine ordering — doing so would be solving an already-solved problem and risks the currency-loop regressions covered by `tests/test_golden_transcript_regressions.py`.
- Remaining work is purely the terminology decision, still unconfirmed:
  - If the client means currency (EGP/USD): no schema or state-machine change is needed at all; this phase closes as "confirmed, already correct" once documented.
  - If the client means a separate payment method (cash, bank transfer, wallet, etc.): add a distinct `payment_method` concept to traveler/booking or booking-only data, define allowed values with the client, and collect it as its own step positioned the same way (after trip/room/group details, immediately before booking-draft creation) — reusing the existing `awaiting_currency`/`currency_required` placement pattern rather than the currency step itself.
  - In the payment-method case, update the agent prompt, workflow policy, session serialization, web chat hints, booking create/update paths, and tests for the new step; the existing currency step and its tests stay untouched either way.

## Dependencies on other phases

This phase should be resolved before Phase 2 and Phase 7 because both touch payment/pricing language and could otherwise encode the wrong meaning.

## Risks specific to this phase

The risk is semantic, not technical: changing the existing currency step when the client meant bank/cash/wallet would ship the wrong workflow. Now that the timing question is confirmed already-correct, the main remaining risk is scope creep — touching the working currency step/tests while adding a payment-method step. The current code also has regression coverage around currency loops (`tests/test_golden_transcript_regressions.py:58`, `tests/test_golden_transcript_regressions.py:106-124`, `tests/test_golden_transcript_regressions.py:156-185`), so any implementation must avoid reintroducing that loop.

## Tests

**Stage B, 2026-08-11:** ran the full `workflow_policy`/`session_flow`-adjacent suite (18 test files). Result: 424 passed, 24 subtests passed, 1 failed. The one failure (`test_tier2_field_validation.py::test_passport_country_mismatch_with_stated_nationality_does_not_block`) was confirmed via `git stash` to already fail identically before this session's changes, and is unrelated (this pass only touched the two `.md` prompt files, which have no Python import path into that test). Full detail in `ravel_agent_master_discovery_report.md`'s 2026-08-11 entry.

## Live verification

Not yet performed for the prompt polish itself (persona/wording changes, low technical risk). Held pending approval per Stage B's "report and pause" instruction, alongside the still-open live re-check for the unrelated Travel Summary trip-count fix (see the discovery report).

## Implementation status

**Stage B complete for the terminology/scope this execution round specified.** Changed `gemini_agent_system.md` and `agent_conversation.md` only: natural, varied wording for the currency question, EGP/USD dialect-variation recognition, and mid-flow "why do you need this" handling — no `workflow_policy.py`/`session_flow.py` changes, since the timing ordering was already correct (see "Current state" above) and this round's instructions scoped the work to prompt/persona polish. Holding for approval before Phase 2.

