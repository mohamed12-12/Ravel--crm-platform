# Database Audit Report

Scope: analysis only. No code or data was modified.

## Executive Summary

The CRM dashboard is not reading the same SQLite database that the migration populated.

- The dashboard and AI agent default to `apps/api/instance/rahma_traveler_dev.db`.
- The migration populated `.tmp-booking-import-populated/crm.db`.
- Those are different files with different contents.
- The dashboard DB currently contains only a minimal demo subset: `1` traveler, `1` trip, `0` bookings, and `1` lead.
- The migration DB contains the recovered migration dataset: `571` travelers, `42` trips, `48` bookings, and `0` leads.

That is why the dashboard shows only `1` traveler and `1` trip even though the migration reports `571` travelers, `42` trips, and `48` bookings.

## SQLite Files Found

Persistent SQLite files found in the repo:

| Path | Role |
|---|---|
| `apps/api/instance/rahma_traveler_dev.db` | Active Flask CRM / agent default database |
| `instance/rahma_traveler_dev.db` | Empty or unused SQLite file at repo root |
| `.tmp-booking-import-populated/crm.db` | Populated migration target database |
| `.tmp-booking-preview-populated/crm.db` | Preview-only migration database |
| `.tmp-traveler-validation/crm.db` | Traveler-validation-only database |

There are also many temporary SQLite files under `.tmp-test-workdirs/`; those are test fixtures and are not the live dashboard database.

## Counts Per Database

| DB file path | travelers | trips | trip_bookings | leads |
|---|---:|---:|---:|---:|
| `apps/api/instance/rahma_traveler_dev.db` | 1 | 1 | 0 | 1 |
| `instance/rahma_traveler_dev.db` | unavailable | unavailable | unavailable | unavailable |
| `.tmp-booking-import-populated/crm.db` | 571 | 42 | 48 | 0 |
| `.tmp-booking-preview-populated/crm.db` | 571 | 42 | 0 | 0 |
| `.tmp-traveler-validation/crm.db` | 571 | 0 | 0 | 0 |

Notes:

- `instance/rahma_traveler_dev.db` is a zero-byte file, so SQLite queries fail with `OperationalError`.
- The migration DB is the only file in this audit with the `571 / 42 / 48` counts.

## Which DB Is Used By Each Component

### Flask CRM Dashboard

Code path:

- `apps/api/run.py`
- `apps/api/app/config.py`
- `apps/api/app/__init__.py`
- `apps/api/app/routes/admin.py`

Observed behavior:

- `apps/api/run.py` boots `create_app(os.getenv('FLASK_CONFIG', 'default'))`.
- `DevelopmentConfig` in `apps/api/app/config.py` defaults to `sqlite:///rahma_traveler_dev.db` when `DATABASE_URL` is not set.
- Flask resolves that relative SQLite path into the app instance database.

Active dashboard DB path:

- `apps/api/instance/rahma_traveler_dev.db`

Why the dashboard shows only `1` traveler and `1` trip:

- That database only contains a tiny demo subset.
- The dashboard counts `Traveler.query.count()` and `Trip.query.filter_by(sales_status='Open').count()` from this file, not from the migration DB.

### Migration Script

Code path:

- `scripts/migrate_excel_to_crm.py`

Observed behavior:

- It reads `DATABASE_URL` if set.
- It also supports `--db`, which sets `DATABASE_URL` to the provided path.
- The migration run that produced the populated dataset used:

` .tmp-booking-import-populated/crm.db`

### AI Agent

Code path:

- `services/ai_agent/ai_agent_app/system_bridge.py`
- `services/crm/system_services/config.py`

Observed behavior:

- The agent’s system bridge uses `RAHMA_SYSTEM_DB_PATH` if set.
- If not set, it falls back to `apps/api/instance/rahma_traveler_dev.db`.

Default agent DB path:

- `apps/api/instance/rahma_traveler_dev.db`

### Tests

Observed behavior:

- Tests create temporary SQLite files in `.tmp-test-workdirs/`.
- Many tests set `DATABASE_URL` and/or `RAHMA_SYSTEM_DB_PATH` to isolated temp DBs.
- The test suite does not rely on the live dashboard DB.

## Root Cause

The migration populated a different SQLite file than the dashboard reads.

In practice:

1. Migration work was executed against `.tmp-booking-import-populated/crm.db`.
2. The dashboard still points at `apps/api/instance/rahma_traveler_dev.db`.
3. Those two files are not synchronized.
4. The dashboard therefore shows the minimal demo rows already present in its own instance DB.

## Recommended Fix

Do not guess or copy data blindly.

The safest fix is:

1. Make the dashboard and agent explicitly point to the same approved CRM database path.
2. Treat that path as the single operational source of truth.
3. Preserve the migration DB as a controlled import target or replace the dashboard DB with the approved populated database after a backup and validation step.
4. Add a startup check that logs the resolved DB path so this mismatch is visible immediately.

## Audit Conclusion

The dashboard is not broken because the data disappeared. It is reading the wrong SQLite file for the migrated dataset.

The populated migration dataset exists and is intact in `.tmp-booking-import-populated/crm.db`, but the dashboard is still bound to `apps/api/instance/rahma_traveler_dev.db`.

