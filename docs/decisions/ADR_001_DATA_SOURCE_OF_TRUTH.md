# ADR 001: CRM Database as Operational Source of Truth

- Status: Accepted for architecture; transitional implementation
- Date: 2026-07-16

## Context

Rahma Traveler had a CRM database, source and runtime Excel workbooks, optional Google Sheets, direct shared-service agent access, imports, exports, and demo reset behavior. Several paths could read or write the same entities from different stores. This created stale-read, overwrite, duplicate-ID, inventory, and demo-isolation risks.

## Decision

The CRM database is the sole operational authority for all business entities, IDs, statuses, inventory, history, notes, and attachment metadata.

Excel and Google Sheets are import/export channels only. Imports use validation, preview, conflict review, and explicit approval. Exports are generated from CRM. Production AI agents use authenticated CRM APIs and the tool-calling runtime. Demo workbooks and reset behavior remain isolated and disabled in production.

## Alternatives considered

### Excel as authority

Rejected. It cannot safely provide transactional booking/inventory behavior, concurrent writes, protected status transitions, relational integrity, or API audit guarantees.

### Google Sheets as authority

Rejected for the same operational reasons and because network/cache behavior adds stale-read and availability risks.

### Bidirectional CRM-sheet synchronization

Rejected. Without per-field ownership, versions, durable conflict records, and transactional reconciliation it creates two writers and ambiguous recovery.

### Shared database service for every deployment

Accepted only as a development transition. It is fast and preserves current tests, but it couples the agent to CRM storage and cannot be the production trust boundary.

## Consequences

Positive consequences:

- One answer exists for live traveler, trip, booking, status, and inventory facts.
- Agent statements and writes can be grounded in the same CRM authority.
- Spreadsheet edits cannot silently overwrite CRM.
- Demo reset is separated from operational state.
- Import/export direction, audit, and failure semantics are explicit.

Negative consequences:

- Operators cannot treat a workbook edit as an immediate CRM edit.
- Import approval and conflict-review tooling must be completed.
- Some legacy direct-service and ID-generation code needs later migration.
- CRM API availability becomes a required production dependency.

## Migration impact

Existing CRM IDs remain unchanged. Validated legacy IDs may be inserted only through approved migration. Existing spreadsheet adapters are retained but constrained. Environment configuration must select CRM authority and production API mode. Google integration remains disabled unless its direction is explicitly enabled.

## Rollback approach

Code rollback may re-enable the prior adapters only in an isolated development environment using backed-up data. Operational rollback must never restore uncontrolled dual writes. Preserve the CRM database, import preview/audit artifacts, and generated exports; disable the affected integration, revert application code, and reconcile through a fresh preview. If the authority decision itself must be reconsidered, create a new ADR rather than changing this record silently.

## Follow-up gates

Production remains no-go until durable import approval records, centralized ID generation, a non-SQLite agent adapter, managed attachment storage, and production inventory concurrency controls are implemented and tested.
