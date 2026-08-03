# PostgreSQL Production Cutover Plan

Date: 2026-08-03

## Current Status

Staging migration, CRM smoke, and agent-path smoke have passed against PostgreSQL schema `ravel`.

Safe agent flags remain the required production posture for cutover:

- `AGENT_TOOL_ROUTER_MODE=dry_run`
- `AGENT_WRITE_TOOL_ENFORCEMENT=true`

Do not enable global router enforce during the database cutover.

## Production Readiness Gates

Complete these before switching production traffic:

- Provision a dedicated production PostgreSQL database or a strictly isolated production schema.
- Create a least-privilege app role scoped only to the production schema.
- Require SSL for all app database connections.
- Store `DATABASE_URL` / `POSTGRES_URL` only in the production secrets manager.
- Confirm the production schema is created from `database/postgres/001_create_schema.sql`.
- Run migration first into a fresh staging clone of production SQLite.
- Run `tools/validate_postgres_migration.py` with zero critical mismatches.
- Run CRM smoke and agent-path smoke against the production-like staging database.
- Capture a fresh SQLite backup before cutover.
- Define a cutover window and freeze writes during final export/import.

## Pre-Cutover Backup

1. Stop or pause user-facing write traffic.
2. Create a filesystem backup of the current SQLite database.
3. Record SQLite file size and SHA-256.
4. Export a logical PostgreSQL backup after migration completes.
5. Store backups outside the application host.

No backup artifact should be committed to the repo.

## Final Migration Steps

1. Confirm production write freeze is active.
2. Run final SQLite to PostgreSQL migration with the production PostgreSQL URL from secrets.
3. Use the production schema only.
4. Preserve orphan `booking_status_history` rows in `legacy_booking_status_history_orphans`.
5. Run validation immediately:

```bash
python tools/validate_postgres_migration.py --sqlite apps/api/instance/rahma_traveler_dev.db
```

Use the environment-provided PostgreSQL connection string; do not print secrets.

## App Cutover Steps

1. Set production `DATABASE_URL` to the PostgreSQL connection string.
2. Set the schema/search path to the production schema.
3. Keep `CRM_ACCESS_MODE=api` for the AI agent.
4. Keep `AGENT_TOOL_ROUTER_MODE=dry_run`.
5. Keep `AGENT_WRITE_TOOL_ENFORCEMENT=true`.
6. Restart CRM/API service.
7. Restart AI agent service.
8. Start admin web/middleware if deployed separately.
9. Do not change UI/UX as part of this cutover.

## Post-Cutover Smoke

Run these immediately after service restart:

- CRM login
- `/admin/db-health` reports PostgreSQL
- dashboard loads traveler/trip/booking metrics
- traveler lookup
- trip search
- booking page loads
- agent `/api/health`
- agent-path `find_traveler_by_phone`
- agent-path `search_trips`
- agent-path `get_trip_details`
- agent-path `create_lead` create/reuse
- agent-path `create_booking_draft` confirmation/repeat
- agent-path `create_handoff` create/reuse
- direct booking bypass remains blocked
- SQLite file SHA-256 remains unchanged after cutover smoke

## Acceptance Criteria

- Production app reads from PostgreSQL.
- Production agent path reads/writes PostgreSQL.
- No SQLite writes occur after cutover.
- No duplicate lead, booking, or handoff rows are created by repeated requests.
- No fake success message appears for failed/blocked/duplicate writes.
- No secrets appear in logs, reports, shell output, or commits.

## Rollback Plan

Use rollback only if production validation fails or critical app behavior is broken.

1. Stop write traffic.
2. Restore the previous `DATABASE_URL` pointing at SQLite.
3. Restart CRM/API and AI agent services.
4. Verify dashboard and agent-path reads are back on SQLite.
5. Compare SQLite SHA-256 against the pre-cutover snapshot.
6. Keep the failed PostgreSQL database unchanged for forensic inspection.
7. Document the failed validation or smoke scenario before another attempt.

If any writes occurred in PostgreSQL before rollback, reconcile them manually before the next cutover attempt.

## Remaining Operational Risks

- Staging currently used a shared RDS instance. Production should not rely on shared credentials or broad schema access.
- A routine CI smoke job for PostgreSQL agent-path checks is still recommended.
- Production secrets and service accounts need to be owned by DevOps, not local `.env` files.
- Router enforce should remain out of scope until database cutover is stable.
