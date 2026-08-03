# PostgreSQL Staging App Smoke Report

Date: 2026-08-03

## Scope

Temporary local environment overrides were used for this smoke test only. No default repo configuration was switched permanently.

## Env Vars Used

- `DATABASE_URL`
- `POSTGRES_URL`
- `APP_ENV`
- `FLASK_CONFIG`
- `CRM_AUTH_ENABLED`
- `ADMIN_USERNAME`
- `ADMIN_PASSWORD`
- `CRM_API_TOKEN`
- `PORT`
- `APP_HOST`
- `APP_PORT`
- `AI_AGENT_MODE`
- `CRM_ACCESS_MODE`
- `CRM_API_BASE_URL`
- `AGENT_TOOL_ROUTER_MODE`
- `AGENT_WRITE_TOOL_ENFORCEMENT`
- `APP_PASSWORD`
- `DEMO_RESET_ON_START`

## Services Started

- CRM/API service on `http://127.0.0.1:5100`
- AI agent service on `http://127.0.0.1:3101`
- Admin web not started because CRM server-rendered pages covered dashboard/travelers/trips/bookings smoke checks.
- Middleware not started because the smoke scenarios were exercised directly against CRM and agent HTTP surfaces.

## URLs Checked

- `http://127.0.0.1:5100/login`
- `http://127.0.0.1:5100/admin/db-health`
- `http://127.0.0.1:5100/admin/dashboard`
- `http://127.0.0.1:5100/travelers/?q=...`
- `http://127.0.0.1:5100/trips/?q=...`
- `http://127.0.0.1:5100/bookings/`
- `http://127.0.0.1:5100/admin/handoffs/`
- `http://127.0.0.1:3101/api/health`
- `http://127.0.0.1:3101/login`
- `http://127.0.0.1:3101/api/v1/bookings/draft`
- `http://127.0.0.1:5100/api/crm/agent/read`

## Scenario Results

| Scenario | Result | Notes |
| --- | --- | --- |
| CRM login | PASS | Browser session established for CRM admin routes. |
| CRM connected to staging PostgreSQL | PASS | CRM reported a PostgreSQL SQLAlchemy URI for the active app connection. |
| Dashboard loads traveler/trip metrics from staging | PASS | Dashboard returned 200 and rendered expected PostgreSQL-backed traveler/open-trip counts (540/2). |
| Returning traveler lookup works | PASS | Traveler search returned the staging traveler TR00001. |
| Trip search works | PASS | Trip search returned the staging trip RT-INT-23-001. |
| Bookings page loads from staging PostgreSQL | PASS | Bookings index loaded successfully from the PostgreSQL-backed CRM app. |
| Handoff flow works | PASS | Admin handoff creation succeeded through the PostgreSQL-backed CRM app. |
| AI agent service starts with safe flags | PASS | Agent health endpoint responded successfully. |
| Direct booking bypass remains blocked | PASS | Unconfirmed direct booking draft request was blocked before any write executed. |
| Agent-backed traveler lookup on PostgreSQL | BLOCKED | Blocked by the current SQLite-only CRM agent adapter, not by staging data or connectivity. |
| Booking draft flow reaches confirmation safely on PostgreSQL | BLOCKED | The confirmed booking-draft path still depends on SQLite-only UnifiedCRMService/agent bridge code, so it was not safe to execute against staging PostgreSQL. |
| Repeated confirmation does not duplicate booking on PostgreSQL | BLOCKED | Could not be exercised safely against PostgreSQL staging because the duplicate-protection booking write path is still SQLite-only. |

## Write Verification

- `handoff_queue` count before/after: `4` -> `5`
- `user_audit_log` count before/after: `6` -> `7`
- `trip_bookings` count before/after: `49` -> `49`
- Writes went to PostgreSQL staging: `yes`

## SQLite Safety

- SQLite stayed unchanged: `yes`
- SQLite size before/after: `528384` -> `528384` bytes

## Relevant Tests

- No automated PostgreSQL-mode unit suite was run against staging because the available test set is mainly SQLite-isolated and several write-path tests create/drop local databases rather than safely target a shared staging schema.

## Remaining Blockers Before Production Cutover

- The CRM web app can read and write PostgreSQL staging for SQLAlchemy-backed routes.
- The AI/agent CRM bridge is still not PostgreSQL-compatible end to end.
- `/api/crm/agent/read` and `/api/crm/agent/write` currently reject non-SQLite CRM backends.
- `UnifiedCRMService` and `system_bridge` still open SQLite directly, so booking draft confirmation and duplicate-protection writes cannot yet be truthfully smoke-tested end to end on PostgreSQL.
- The agent `/api/health` diagnostics still report the operational SQLite path because that subsystem has not been migrated to PostgreSQL-aware diagnostics.
- Do not switch production or default local config yet.