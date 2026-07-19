# New Traveler Agent Flow

Date: 2026-07-15

When `find_traveler_by_phone` returns `not_found`, the workflow enters `traveler_not_found`.

## Current Phase 1 Behavior

- The agent explains that no profile was found.
- The agent may collect required profile details conversationally.
- No traveler is created automatically.
- No broad CRM write access was added.

## Deferred Work

Traveler creation requires a later controlled-write phase with:

- Required CRM schema fields.
- Duplicate-phone check immediately before creation.
- Explicit customer confirmation.
- Idempotency protection.
- Official traveler ID returned by the CRM API/service.
