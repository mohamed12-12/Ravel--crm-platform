# ID Authority

## Rule

The CRM owns all new operational IDs. Clients and spreadsheets may supply an existing legacy ID only during an approved, validated migration. IDs are immutable after creation except through a separately approved identity-merge process that preserves aliases and references.

## Current generators and target

| ID | Current generator | Collision or drift risk | Import rule | Target rule |
|---|---|---|---|---|
| Traveler ID | `UnifiedCRMService` scans CRM/workbook history for the next `TR` value | Workbook participation and concurrent max-plus-one creation | Preserve a valid, unique legacy `TR` ID; reject blank/conflicting ID | CRM database sequence/UUID-backed allocator with formatted public ID |
| Trip ID | Usually supplied by CRM administration or legacy import | Human naming collision | Preserve only after date/type/entity review | CRM-owned immutable ID |
| Lead ID | `_next_prefixed_id(..., "LD", 5)` | Max-plus-one concurrency on SQLite | Preserve only if unique and approved | CRM allocator |
| Booking ID | `_next_booking_id` based on traveler/trip context | Format collision under concurrent drafts | Blank imported IDs are invalid | CRM booking transaction allocates ID |
| Handoff ID | Service uses prefixed sequence; manual route can use UUID fragment | Two formats and generator paths | Preserve unique historical ID only | One CRM allocator |
| Interaction ID | Service uses daily `INT` ID; manual route uses `I-` UUID fragment | Two formats and ordering assumptions | Preserve unique historical ID only | One CRM allocator |

## Conflict rules

- Same ID and same material fields: Unchanged.
- Same ID with different phone, dates, status, inventory, or relationship: Conflict.
- Same normalized phone with different traveler ID: Duplicate identity conflict.
- Imported blank ID: Invalid; import must not generate it.
- Existing legacy ID: Preserve exactly when validated.
- New runtime record: Ignore client-proposed ID unless the endpoint is an approved migration endpoint.

## Required follow-up

Centralize interaction and handoff generation, remove workbook participation from new traveler allocation, and use database uniqueness plus retry semantics. This is migration work, not part of the current minimum enforcement batch.
