# Demo and Production Isolation

## Data classes

| Data class | Purpose | Production use |
|---|---|---|
| Operational CRM DB | Live records | Required |
| Test SQLite DB | Automated test isolation | Forbidden |
| Excel source workbook | Legacy/demo seed input | Not an operational production source |
| Excel runtime workbook | Disposable demo/output state | Forbidden as production authority |
| Import staging artifacts | Preview/audit input | Must move to durable protected storage before production |

## Enforced configuration

- `DATA_AUTHORITY` must be `crm`.
- Production requires `DATABASE_URL`.
- Production requires `CRM_ACCESS_MODE=api`, `CRM_API_BASE_URL`, and `CRM_API_TOKEN`.
- Production requires `AI_AGENT_MODE=tool_calling`.
- Production rejects `DEMO_DATA_MODE=true`.
- Production rejects `DIRECT_IMPORT_APPLY_ENABLED=true`.
- Source and runtime workbook paths must differ.
- Shared-service SQLite mode rejects different `DATABASE_URL` and `RAHMA_SYSTEM_DB_PATH` values.

## Reset behavior

Both agent reset endpoints return `409 demo_reset_disabled` unless `DEMO_DATA_MODE=true`. A demo reset copies only the configured source workbook to the configured runtime workbook. It must never modify the CRM database or source workbook.

## Test behavior

Tests set CRM authority, disable mirror/apply by default, and use temporary databases and workbooks for state-changing cases. `test_operational_db_protection.py` hashes the operational SQLite file before and after destructive schema operations against a temporary DB.

## Operational rules

- Never set a production database path in test fixtures.
- Never use the operational workbook as a runtime workbook.
- Label demo records and artifacts at the environment and storage level.
- Do not permit fallback from an unavailable production CRM API to demo Excel or Google data.
- Keep production credentials outside workbooks and generated exports.

## Remaining blocker

The current local-file attachment and import-staging directories need environment-specific managed storage, retention, backup, and access controls before production.
