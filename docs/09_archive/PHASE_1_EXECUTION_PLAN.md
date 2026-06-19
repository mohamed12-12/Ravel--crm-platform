# Phase 1 Execution Plan

Date: 2026-06-17

Scope:

1. Phone Normalization
2. CRM Identity Contract Cleanup
3. Booking Unsupported Media Type bug
4. Agent Session Premature Close Audit/Fix
5. Regression Protection Tests

This phase is intentionally narrow. It must not pull in trip redesign, lead redesign, passport expansion, attachment expansion, or Instagram work.

## Business Goal

Phase 1 exists to make the current demo and CRM identity layer safe enough to trust before any broader redesign begins.

The business outcome we want is simple:

- new and returning travelers are recognized correctly
- ambiguous phone numbers are handled safely
- existing Traveler IDs remain stable
- booking requests do not fail because of request payload/content-type drift
- the agent does not close a session prematurely during a valid flow
- regression coverage protects the corrected behavior

In plain terms, Phase 1 is the safety net that prevents wrong customer matching and broken demo interactions from contaminating later CRM work.

## Current Problems

### Phone Normalization

Actual issues in code:

- `UnifiedCRMService.normalize_phone()` still defaults to `20` in ways that can misclassify non-Egypt numbers.
- `apps/api/app/routes/leads.py` and `apps/api/app/routes/travelers.py` still have Egypt-first assumptions in manual paths.
- `services/ai_agent/ai_agent_app/agent/session_flow.py` can advance with incomplete phone metadata and depends on the current parser contract.
- The demo intake UI still hardcodes Egypt-flavored defaults in the form surface.

Observed business impact:

- `+966...` and similar numbers are more likely to be misread, causing false "new customer" or wrong-country outcomes.
- Existing Egyptian numbers need to continue to work exactly as they do now.

### CRM Identity Contract Cleanup

Actual issues in code:

- identity, phone parsing, lookup-key generation, and traveler matching are spread across the shared service and route layers
- the current contract is not explicit enough about raw input, normalized phone, lookup variants, country-code confidence, and confirmation-needed behavior
- some paths still assume Egypt as the default for lookup and intake

Observed business impact:

- duplicate safety and Traveler ID preservation are too easy to break when future work touches any phone-related path

### Booking Unsupported Media Type Bug

Actual issues in code:

- booking-related UI and API paths mix form posts and JSON expectations
- `apps/api/app/routes/bookings.py` uses form-based create/update behavior, while demo/session booking paths use JSON
- several frontend/server endpoints enforce content-type assumptions differently
- the system already has a real 415 response path for attachment uploads when file types are rejected, which is separate from booking itself but shows the same request-contract fragility

Observed business impact:

- booking actions can fail because the request shape is not consistently defined end to end
- operators can hit opaque client/server errors instead of a predictable validation response

### Agent Session Premature Close

Actual issues in code:

- `services/ai_agent/ai_agent_app/agent/session_flow.py` marks sessions `completed` in multiple branches
- a session can end after handoff, decline, or draft booking creation
- there is no separate durable distinction between clean completion, abandonment, handoff, draft created, and fatal error
- the current agent flow can appear finished even when the commercial journey is not actually complete

Observed business impact:

- staff may believe an interaction has fully completed when it is only a draft or partial handoff
- session close semantics are too coarse for client delivery

### Regression Protection

Actual issues in code:

- tests are already strong, but Phase 1 behavior is spread across normalization, identity, agent flow, and booking surfaces
- the current tests encode some safe behavior, but they are not yet grouped as a Phase 1 lock

Observed business impact:

- without a targeted regression suite, later trip/lead/passport work will quietly re-break the same safety rules

## Desired Result

After Phase 1:

- international phone numbers are recognized correctly and do not default to Egypt when they should not
- Egyptian local numbers still resolve safely and backward compatibility is preserved
- Traveler ID preservation remains intact
- duplicate travelers are not created from phone parsing mistakes
- booking create/update flows return predictable validation behavior instead of content-type confusion
- the agent no longer closes a session in a way that hides an incomplete commercial flow
- regression tests lock down the corrected behavior

## Affected Files

Exact files expected to change in Phase 1:

- `services/crm/system_services/phone_normalization.py`
- `services/crm/system_services/unified_service.py`
- `services/ai_agent/ai_agent_app/agent/session_flow.py`
- `services/ai_agent/ai_agent_app/server.py`
- `services/ai_agent/ai_agent_app/system_bridge.py`
- `services/ai_agent/ai_agent_app/sheets/excel_gateway.py`
- `services/ai_agent/ai_agent_app/web/static/app.js`
- `services/ai_agent/ai_agent_app/web/templates/index.html`
- `apps/api/app/routes/leads.py`
- `apps/api/app/routes/travelers.py`
- `apps/api/app/routes/bookings.py`
- `apps/api/app/templates/bookings/index.html`
- `apps/api/app/templates/bookings/detail.html`
- `tests/test_phone_normalization.py`
- `tests/test_phase1_readonly_agent.py`
- `tests/test_phase2_controlled_agent.py`
- `tests/test_phase3_demo_web.py`
- `tests/test_phase3_booking_write_through.py`
- `tests/test_phase4_traveler_management.py`
- `tests/test_phase5_manual_lead_agent_logic.py`
- `tests/test_phase8_demo_alignment.py`
- `tests/test_phase10_full_logic_lock.py`
- `tests/test_phase11_demo_features.py`

Files that should remain untouched in Phase 1:

- `database/schema/schema.prisma`
- `services/instagram/*`
- passport/attachment model expansion files beyond the existing minimal runtime behavior
- trip redesign files and trip inventory redesign surfaces

## Database Impact

Schema changes?

- No new functional schema is expected for Phase 1.
- Phase 1 should be contract cleanup and behavior stabilization only.

Migration required?

- No migration should be introduced as part of Phase 1.
- If a schema change starts appearing in the plan, that is a sign the phase has drifted.

Backward compatibility?

- Yes, absolutely required.
- Existing Egyptian phone records, legacy lookup keys, and current traveler IDs must keep working.
- The phase must not invalidate old traveler or lead rows that were created under current conventions.

## API Impact

Routes affected:

- `apps/api/app/routes/leads.py`
- `apps/api/app/routes/travelers.py`
- `apps/api/app/routes/bookings.py`
- `services/ai_agent/ai_agent_app/web/api_routes.py`
- `services/ai_agent/ai_agent_app/server.py`

Expected API behavior changes:

- phone normalization inputs should accept structured detection results consistently
- country-code ambiguity should produce a safe confirmation path
- booking endpoints should stop producing content-type confusion and should return deterministic validation errors
- session serialization should expose the phone normalization contract needed by the frontend

## CRM Impact

### Traveler Flow Changes

- traveler creation and update continue to preserve IDs
- traveler matching becomes safer for non-Egypt numbers
- manual traveler creation no longer silently forces Egypt assumptions where a better country code is already known

### Lead Flow Changes

- lead creation should continue to use identity-safe matching
- the lead contract itself is not being redesigned in Phase 1
- only the phone/identity handling feeding lead creation is being cleaned up

### Booking Flow Changes

- booking create/update behavior should become predictable with respect to content type and payload shape
- the visible result should be a proper validation/error response, not an opaque request failure

## Agent Impact

### Session Flow Changes

- the agent must no longer close sessions in a way that hides active commercial work
- session completion needs a clearer distinction between:
  - handoff
  - no-interest stop
  - booking draft created
  - error
  - true completion

### Identity Matching Changes

- the agent should pass structured phone metadata through the same shared normalization path as CRM routes
- ambiguous or non-Egypt numbers should not be collapsed into Egypt defaults
- returning traveler matching should remain stable

## Test Plan

Required tests for Phase 1:

- `tests/test_phone_normalization.py`
- `tests/test_phase1_readonly_agent.py`
- `tests/test_phase2_controlled_agent.py`
- `tests/test_phase3_demo_web.py`
- `tests/test_phase3_booking_write_through.py`
- `tests/test_phase4_traveler_management.py`
- `tests/test_phase5_manual_lead_agent_logic.py`
- `tests/test_phase8_demo_alignment.py`
- `tests/test_phase10_full_logic_lock.py`
- `tests/test_phase11_demo_features.py`

Expected test coverage:

- international phone parsing
- Egyptian local phone parsing
- ambiguous phone confirmation
- legacy lookup-key compatibility
- duplicate traveler safety
- blacklist safety
- booking request validation/content-type behavior
- agent session completion behavior
- no early session close during valid flow
- demo alignment and full logic lock regression

## Risks

### Technical Risks

- phone parsing changes can accidentally break historical Egyptian number behavior
- altering identity lookup variants can accidentally reduce duplicate safety
- changing session close logic can cause tests or UI assumptions to fail if completion states are not treated carefully
- fixing the booking request-contract issue could expose existing frontend payload drift in multiple places

### Business Risks

- if non-Egypt numbers are not handled correctly, the client will see misclassification and duplicate safety regressions immediately
- if sessions still close too early, the demo will look broken even when the DB writes succeeded
- if booking errors remain opaque, operators may lose trust in the CRM flow

## Rollback Plan

If Phase 1 fails, revert the affected behavior in this order:

1. Restore the previous phone-normalization contract.
2. Restore the prior agent session completion semantics.
3. Restore the previous booking request handling behavior.
4. Re-run the Phase 1 regression tests before any other changes are reintroduced.

Operational rollback rule:

- no downstream Phase 2/3 work should continue until Phase 1 is stable again
- do not keep partial phone or session changes if they break legacy matching

## Success Criteria

Phase 1 is successful only if all of the following are true:

- international phone numbers are recognized correctly
- existing Egyptian numbers still work
- ambiguous numbers request confirmation instead of being forced into the wrong country
- existing Traveler IDs are preserved
- no duplicate travelers are created by normalization mistakes
- booking status/update behavior works without unsupported-media-type confusion
- the agent no longer closes the session early during a valid commercial flow
- all Phase 1 regression tests pass

