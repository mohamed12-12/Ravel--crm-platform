# Recommended Source of Truth

## Decision

The CRM database is the sole operational source of truth for travelers, trips, leads, bookings, interactions, handoffs, inventory, statuses, notes, attachment metadata, passport metadata, and business IDs.

Excel and Google Sheets are integration channels only. They may propose imports through validation and review, or receive generated exports. They do not own live values and cannot silently overwrite CRM records.

Production AI-agent access is:

`Agent -> authenticated CRM API -> CRM validation/service -> CRM database`

The CRM frontend follows the same ownership boundary through CRM routes and services.

## Why the repository supports this decision

- SQLAlchemy models already represent the complete operational entity set.
- CRM routes and `UnifiedCRMService` already contain identity, booking, inventory, status, and workflow behavior.
- Agent read and controlled write tools can be exposed through authenticated CRM endpoints.
- Existing workbooks are useful as migration, demo, reporting, and business-integration artifacts without acting as databases.
- Existing validated migration logic can preserve legacy IDs while quarantining unsafe rows.

## Transitional model

Development may use `CRM_ACCESS_MODE=shared_service` while the CRM and agent run in one trusted workspace against the same SQLite path. Startup validation rejects a different `DATABASE_URL` and `RAHMA_SYSTEM_DB_PATH` in this mode.

Production must use `CRM_ACCESS_MODE=api` and `AI_AGENT_MODE=tool_calling`. The current CRM agent API adapter is SQLite-specific, so PostgreSQL support requires replacing its local adapter with a repository/service implementation that uses the configured SQLAlchemy connection.

## Non-authorities

- The source workbook is not writable by operational flows.
- The runtime workbook is not an import authority.
- Google Sheets is not a live fallback for CRM reads.
- The agent does not generate authoritative IDs or statuses.
- Demo reset cannot run unless `DEMO_DATA_MODE=true`.

## Production blockers

1. Import staging is persisted as immutable fingerprinted JSON preview artifacts, not durable relational sync-run/conflict tables with an approval UI.
2. Generic import schema manifests and version compatibility rules are not yet complete.
3. Interaction and handoff ID generation remains split across service and manual routes.
4. The CRM agent endpoint adapter currently supports SQLite only.
5. Passport binaries use local file storage; only metadata is CRM-owned and API-routed.
6. SQLite does not provide the desired production concurrency and row-locking behavior for inventory.

The architecture decision is valid now, but deployment hardening is **no-go** until these blockers are resolved in a later approved batch.
