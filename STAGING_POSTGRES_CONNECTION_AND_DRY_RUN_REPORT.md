# Staging PostgreSQL Connection And Dry-Run Report

Date: 2026-08-03

## Scope

This report covers staging PostgreSQL connection verification, schema status, schema application, and SQLite migration dry-run for Rahma Traveler CRM.

No actual row migration was run.

## Safety Controls

- PostgreSQL target treated as staging only.
- Password and full connection URL were not written to this report.
- SSL was required for the connection.
- `DB_SCHEMA=ravel` was used.
- SQLite was not modified.
- Production migration was not attempted.

## Connection Verification

Result: `PASS`

- Connection method: `POSTGRES_URL` environment variable.
- Database name: `postgres`
- Schema: `ravel`
- SSL: `enabled`
- Current schema after connection options: `ravel`

## Schema Status

Result: `PASS`

- Schema `ravel` existed before check: `false`
- Schema `ravel` created during staging setup: `true`
- Expected Rahma tables before schema application: `0`
- Schema SQL applied: `true`
- Expected Rahma tables after schema application: `18`

Schema file applied:

```text
database/postgres/001_create_schema.sql
```

## SQLite Source

Source database:

```text
apps/api/instance/rahma_traveler_dev.db
```

SQLite access mode:

```text
read-only dry-run
```

## Dry-Run Result

Result: `PASS`

Dry-run command:

```bash
python tools/migrate_sqlite_to_postgres.py --dry-run
```

Dry-run table summary:

| Table | Source Rows | Columns |
| --- | ---: | ---: |
| `users` | 2 | 10 |
| `travelers` | 540 | 44 |
| `trips` | 47 | 32 |
| `community_events` | 0 | 7 |
| `dm_copy_library` | 0 | 12 |
| `language_templates` | 0 | 8 |
| `leads` | 0 | 44 |
| `interactions` | 12 | 24 |
| `trip_bookings` | 49 | 34 |
| `ce_bookings` | 0 | 8 |
| `booking_status_history` | 65 | 8 |
| `handoff_queue` | 3 | 14 |
| `booking_event_trail` | 3 | 13 |
| `traveler_documents` | 4 | 18 |
| `trip_media` | 6 | 17 |
| `assignment_history` | 3 | 9 |
| `user_audit_log` | 5 | 6 |
| `sync_queue` | 29 | 6 |

Dry-run conclusion:

```text
Dry run complete. No PostgreSQL data writes were attempted.
```

## Stop Point

Per instruction, the actual migration is blocked pending explicit approval.

The next command must not be run until approved:

```bash
python tools/migrate_sqlite_to_postgres.py --postgres-url "$POSTGRES_URL"
```

After approved migration, run:

```bash
python tools/validate_postgres_migration.py --postgres-url "$POSTGRES_URL"
```

## Remaining Risks Before Actual Migration

- The staging schema is empty and ready, but row migration has not been executed.
- Legacy raw SQLite paths still need application smoke testing after staging migration.
- Validation requires the actual migration to complete first.
- The current target uses database `postgres`; a dedicated staging database is still recommended before production cutover.
