# Travel Business Logic Feature Plan

Date: 2026-06-24

## 1. Goal

This plan defines a safe implementation path for four business capabilities in Rahma Traveler without breaking current CRM, booking, agent, or sheet-sync logic:

1. Travel company business logic for trips with and without flights
2. VIP and group discount handling
3. Human handoff workflow hardening
4. Web-based visa information lookup

The plan is written against the current codebase and must preserve the existing non-hallucination rules, deterministic CRM writes, and protected-traveler safeguards.

## 2. Current Project Reality

The current project already contains partial support for these areas:

- `flight_option` already exists in booking flow and booking models.
- International trips already require passport attachment before room selection continues.
- VIP logic already exists in traveler status, lead stages, and some analytics paths.
- Handoff queue already exists in database, Flask routes, admin UI, and lead flow.
- Visa lookup already exists as a workbook/table-based endpoint: `GET /api/visa/<destination>`.

The current gaps are:

- The agent does not clearly explain the company rule that most trips are sold without flights.
- Discount behavior is not modeled as controlled business logic.
- Group booking is not a first-class workflow yet.
- Handoff is present, but not every trigger produces a complete handoff package.
- Visa answers are currently static/table-based only and do not support controlled web search.

## 3. Non-Negotiable Rules

1. The agent must never invent trip availability, discount values, visa rules, handoff outcomes, or CRM facts.
2. Deterministic operational flow must remain the source of truth. Gemini may rewrite tone only, not business facts.
3. Traveler, lead, booking, and handoff writes must stay traceable to existing service-layer logic.
4. Existing identity, blacklist, duplicate-phone, and passport protections must remain intact.
5. If a rule is uncertain, unavailable, or not configured, the agent must say so and route to follow-up or handoff instead of guessing.

## 4. Feature A: Travel Company Business Logic for Flights

### Business Rule

- Trips may be sold `With Flight` or `Without Flight`.
- Most trips are currently sold without flights.
- The agent should communicate this naturally and accurately.

### Required Behavior

1. When a trip is offered, the agent should not imply that flights are included by default.
2. When the customer asks about flights, the agent should explain that most current offers are without flights unless the package or booking option explicitly includes flights.
3. In the booking step, the agent may still ask whether the customer wants flight support, but the wording should reflect the business model:
   - default expectation: trip is sold without flights
   - optional upgrade/support path: customer can request flights if available
4. If the trip record does not explicitly support flight inclusion rules, the agent should avoid claiming flight pricing or flight availability.

### Recommended Data/Logic Changes

- Keep using existing `trip_bookings.flight_option`.
- Add optional trip-level configuration fields later if needed:
  - `flight_policy`: `without_flights_default`, `with_flights_included`, `flight_optional`, `manual_quote`
  - `flight_notes`
- Until trip-level fields exist, default agent phrasing should assume:
  - base trip offer is without flights
  - customer may request flight assistance separately

### Agent Copy Rule

Preferred operational meaning:

- “This trip is currently offered without flights by default. If you want, I can note that you need a flight option and our team can confirm availability.”

The agent must not say:

- “Flights are included” unless supported by configured trip data.
- “We can definitely add flights” unless flight support is explicitly enabled.

## 5. Feature B: VIP and Group Discounts

### Business Rule

The agent should recognize and apply special discounts for:

- VIP customers
- Group bookings

### Current State

- Traveler status already supports values such as `VIP`.
- Lead stages already include VIP-specific variants such as `VIP Priority`, `VIP Follow Up`, and `VIP Booking Draft`.
- There is no controlled discount engine yet.

### Required Behavior

1. VIP travelers should be recognized from CRM status or tier, not from chat claims alone.
2. Group-booking discounts should be triggered by explicit passenger count or a configured group threshold.
3. The agent must not invent a discount amount if no configured discount exists.
4. If a discount exists, the agent may communicate it.
5. If the customer may qualify but the discount is not configured, the agent should say the request will be checked by the team.

### Recommended Discount Model

Introduce a controlled discount-policy layer rather than hardcoding reply text.

Suggested policy object:

- `discount_type`
- `discount_label`
- `eligibility_basis`
- `value_type`: `percentage`, `fixed_amount`, `manual_quote`
- `value_amount`
- `currency`
- `applies_to`: `trip`, `booking`, `traveler`, `group`
- `stacking_rule`: `exclusive`, `best_only`, `stackable`
- `requires_human_approval`
- `notes`

### VIP Rules

1. VIP eligibility should be derived from traveler record:
   - preferred source: `Traveler.status`
   - optional future source: `Lead.customer_tier`
2. VIP discount application should happen only if a configured VIP discount rule exists.
3. If VIP traveler exists but no discount rule is configured:
   - preserve VIP lead path
   - do not fake a discount
   - optionally create a follow-up note or handoff for manual commercial handling

### Group Booking Rules

1. Group logic should start only when passenger count is known.
2. Minimum group threshold must be configurable.
3. Initial implementation should treat group booking as commercial logic, not as a full passenger manifest system.
4. If the project is still single-traveler-booking oriented, create the group request at lead/handoff level first.

Suggested initial fields:

- `group_size_requested`
- `group_discount_eligible`
- `group_discount_rule_id`
- `group_quote_required`

### Safe Rollout Recommendation

Phase 1:

- Recognize VIP traveler
- Recognize “group booking intent”
- Do not auto-price discounts yet
- Create structured notes/handoff when needed

Phase 2:

- Add configured discount rules
- Show discount in agent flow only when rule exists

Phase 3:

- Support group quote workflow and linked travelers/passengers

## 6. Feature C: Human Handoff Workflow

### Current State

Current handoff support already exists in:

- `handoff_queue` model
- lead manual creation conflict flow
- admin handoff board
- some agent and service-level handoff reasons

### Current Problems

- Not every escalation produces a full handoff package.
- No single contract guarantees that human agents receive all needed context.
- Some “recommend handoff” states do not create durable queue records.

### Required Behavior

Handoff should be seamless, structured, and predictable.

Every required handoff should create or update a durable record containing:

- traveler ID if known
- lead ID if known
- trip ID if known
- handoff reason
- priority
- current flow key/stage
- latest customer message summary
- latest agent action summary
- phone/channel
- passport status if relevant
- visa question context if relevant
- assigned owner/status metadata

### Required Handoff Triggers

Existing triggers that must remain:

- duplicate phone match
- phone/name conflict
- blacklisted customer
- protected traveler review states

New recommended triggers:

- VIP commercial discount approval needed
- group booking quote needed
- visa lookup unavailable or conflicting
- flight inclusion requires manual commercial confirmation
- missing trip configuration for a requested commitment

### Recommended Implementation Direction

1. Centralize handoff creation in service layer, not scattered route logic.
2. Add a helper such as `create_handoff_case(...)` in `UnifiedCRMService`.
3. Make all channels use the same payload contract.
4. Ensure lead and handoff stay linked through `lead.handoff_id`.
5. Log event-trail entries whenever a handoff is created.

### Suggested Handoff Payload Contract

- `handoff_id`
- `traveler_id`
- `lead_id`
- `trip_id`
- `channel`
- `flow_key`
- `reason_code`
- `reason_text`
- `priority`
- `status`
- `customer_summary`
- `agent_summary`
- `commercial_context_json`
- `assigned_to`
- `owner`
- `notes`

## 7. Feature D: Visa Information Web Lookup

### Current State

- Current endpoint: `/api/visa/<destination>`
- Current source: workbook sheet `Visa Requirements`
- Current behavior: if destination is unknown, recommend human follow-up

### Business Need

Customers may ask whether they need a visa for a specific country, and the agent should be able to search the web and answer carefully.

### Non-Negotiable Safety Rule

Visa requirements are time-sensitive. The system must treat them as unstable information and must not rely on memory or static prompt text alone.

### Recommended Response Policy

1. First use the internal table if the destination exists and the data is trusted.
2. If no trusted internal record exists, perform controlled web lookup.
3. Prefer official sources:
   - embassy / consulate sites
   - destination government immigration sites
   - airline/IATA-style official travel advisories if available
4. Return a short answer plus disclaimer.
5. Include source links in the UI/API response.
6. If sources conflict or are unclear, recommend human follow-up and optionally create a handoff.

### Important Scope Rule

The answer must be framed for the traveler's nationality/passport context. A visa answer without nationality context is incomplete.

The system should ask:

- destination country
- traveler nationality or passport country if not already known

If nationality is unknown and cannot be inferred safely, the agent should say it needs that detail before giving a visa answer.

### Recommended Technical Design

Introduce a resolver abstraction:

- `VisaInfoProvider`
  - `lookup_from_internal_table(destination, nationality)`
  - `lookup_from_web(destination, nationality)`
  - `merge_and_rank_results(...)`

Suggested API response contract:

- `destination`
- `nationality`
- `required`
- `summary`
- `notes`
- `disclaimer`
- `sources`
- `source_mode`: `internal`, `web`, `mixed`, `unknown`
- `handoff_recommended`
- `handoff_reason`
- `last_verified_at`

### UI/Agent Behavior

- If a traveler profile already has nationality, reuse it.
- If not, ask one direct question for nationality/passport country.
- Keep the answer concise.
- Never promise legal certainty.

### Example Safe Answer Style

- “Based on the latest official guidance I found for Egyptian passport holders traveling to Turkey, a visa is not required for short tourist visits. Please still verify with the official embassy or consulate before travel.”

## 8. Files and Layers Likely Affected

### Agent Layer

- `services/ai_agent/ai_agent_app/agent/session_flow.py`
- `services/ai_agent/ai_agent_app/prompts/agent_conversation.md`
- `services/ai_agent/ai_agent_app/conversation_ai.py`

### Service Layer

- `services/crm/system_services/unified_service.py`
- `services/ai_agent/ai_agent_app/system_bridge.py`
- `services/ai_agent/ai_agent_app/sheets/excel_gateway.py`

### API/UI Layer

- `services/ai_agent/ai_agent_app/server.py`
- `apps/api/app/routes/leads.py`
- `apps/api/app/routes/handoffs.py`
- `apps/api/app/routes/bookings.py`
- handoff/admin templates if richer operator context is added

### Models

- `apps/api/app/models/lead.py`
- `apps/api/app/models/handoff.py`
- `apps/api/app/models/booking.py`
- optional future discount-policy model

## 9. Suggested Implementation Phases

### Phase 1: Documentation and Prompt Alignment

- Align agent prompt with:
  - most trips are without flights
  - discounts must be configured, not invented
  - visa answers require nationality context
  - unclear visa/discount/flight commitments should hand off

### Phase 2: Deterministic Business Logic

- Add service helpers for:
  - `resolve_flight_policy`
  - `resolve_discount_eligibility`
  - `create_handoff_case`

### Phase 3: Visa Resolver

- Keep current workbook lookup as first source
- Add controlled web lookup as fallback
- Return sources and disclaimer

### Phase 4: UI and Operator Visibility

- Show discount eligibility and handoff reasons in lead/traveler detail
- Show visa question context on handoff board when relevant

### Phase 5: Test Lock

- Add regression tests for all supported paths

## 10. Required Tests

### Flight Logic

- Agent states that flights are not included by default unless configured
- Invalid flight commitments are never made
- Booking still preserves `flight_option`

### VIP / Group Discounts

- VIP traveler recognized from CRM
- VIP discount not stated when no rule exists
- Group intent recognized
- Group case can trigger manual quote/handoff

### Handoff

- Every required handoff creates linked durable record
- Lead and handoff IDs remain connected
- Reason codes and priority are preserved

### Visa

- Internal table lookup still works
- Web fallback only runs when internal data is missing or stale
- Nationality is required for final visa answer
- Source links are included
- Unknown/conflicting results trigger handoff recommendation

## 11. Recommended Delivery Strategy

Safest delivery order:

1. Ship the `.md` plan and confirm rules
2. Align prompt and conversational wording
3. Add deterministic service-layer helpers
4. Add visa web lookup with strict source policy
5. Add tests before expanding discount automation

## 12. Final Recommendation

Do not implement these four features as one large direct code change.

Best path:

1. Keep the current deterministic booking and identity core
2. Add controlled business-rule helpers
3. Add structured handoff packaging
4. Add visa web lookup as a separate, source-aware module
5. Treat discounts as configured commercial policy, not free-form agent creativity

This keeps the system professional, predictable, and safe across all customer cases.
