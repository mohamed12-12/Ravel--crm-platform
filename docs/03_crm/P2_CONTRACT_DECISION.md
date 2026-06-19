# P2 Contract Decision

Date: 2026-06-16
Branch: `cleanup/mvp-to-product-structure`

## Decision

Both remaining failures were caused by outdated test expectations, not broken product behavior.

## Decisions By Test

### `tests/test_phase1_readonly_agent.py::Phase1ReadonlyAgentTests::test_build_agent_response_handles_phase1_cases`

Decision: outdated test expectation.

Reason:

- The implementation filters open trips by `TODAY` and only surfaces future departures.
- The test fixture used a trip dated `2026-06-01`, which is already in the past relative to this run (`2026-06-16`).
- The correct way to keep the test meaningful is to seed a future trip date, not weaken the trip filter.

### `tests/test_phase8_demo_alignment.py::Phase8DemoAlignmentTests::test_demo_web_calls_crm_services_safely_and_creates_records`

Decision: outdated test expectation.

Reason:

- The demo session currently starts at `awaiting_phone`.
- The existing demo flow asks for the WhatsApp number first, then moves to intake.
- The test was asserting `awaiting_intake` immediately after session creation, which does not match the current product flow.

## Safe Fix Applied

- Updated the phase 1 fixture to use a future trip date.
- Updated the phase 8 test to follow the actual phone-first demo flow.

## Notes

- No production logic was changed for these two mismatches.
- This keeps the tests aligned with the current user experience and avoids silently changing the workflow contract.
