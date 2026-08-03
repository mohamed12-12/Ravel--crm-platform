# Staging PostgreSQL Actual Migration Report

Date: 2026-08-03

## Scope

This report covers the approved actual SQLite to PostgreSQL migration attempt against staging PostgreSQL only.

## Safety Controls

- Target schema: `ravel`
- SSL: `enabled`
- Full PostgreSQL URL and password were not written to this report.
- App configuration was not switched to PostgreSQL.
- Production was not touched.
- SQLite source database was not modified.

SQLite unchanged confirmation:

```text
SQLite file size before: 528384 bytes
SQLite file size after:  528384 bytes
SQLite SHA-256 unchanged: true
```

## Target Confirmation

Result: `PASS`

- Connection target resolved to schema `ravel`.
- SSL was enabled.
- Expected schema tables existed before migration attempt: `18`

## Migration Result

Result: `FAILED`

The migration started and successfully inserted the first dependency-ordered tables, then stopped at `booking_status_history`.

Failure class:

```text
Foreign key violation
```

Reason:

```text
booking_status_history contains at least one legacy booking_id that is not present in trip_bookings.
```

No secrets were printed or written. The exact connection string and password remain excluded.

## Table Row Counts

| Table | SQLite Source Rows | PostgreSQL Rows After Attempt | Status |
| --- | ---: | ---: | --- |
| `users` | 2 | 2 | migrated |
| `travelers` | 540 | 540 | migrated |
| `trips` | 47 | 47 | migrated |
| `community_events` | 0 | 0 | migrated |
| `dm_copy_library` | 0 | 0 | migrated |
| `language_templates` | 0 | 0 | migrated |
| `leads` | 0 | 0 | migrated |
| `interactions` | 12 | 12 | migrated |
| `trip_bookings` | 49 | 49 | migrated |
| `ce_bookings` | 0 | 0 | migrated |
| `booking_status_history` | 65 | 0 | failed |
| `handoff_queue` | 3 | 0 | not reached |
| `booking_event_trail` | 3 | 0 | not reached |
| `traveler_documents` | 4 | 0 | not reached |
| `trip_media` | 6 | 0 | not reached |
| `assignment_history` | 3 | 0 | not reached |
| `user_audit_log` | 5 | 0 | not reached |
| `sync_queue` | 29 | 0 | not reached |

## Critical Table Checks

| Area | Result | Notes |
| --- | --- | --- |
| `travelers` | `PASS` | 540 of 540 rows present. |
| `trips` | `PASS` | 47 of 47 rows present. |
| `trip_bookings` | `PASS` | 49 of 49 rows present. |
| `leads` | `PASS` | 0 of 0 rows present. |
| `handoff_queue` | `FAIL` | Migration did not reach this table after FK failure. |
| `booking_status_history` | `FAIL` | Blocked by orphan booking history reference. |

## Validation Result

Result: `FAILED`

Validation summary:

- Row count/key checks passed for tables migrated before the failure.
- Row count/key checks failed for 8 tables not migrated after the failure.
- Primary key presence failed for the same 8 incomplete tables.
- Foreign key checks passed on the currently migrated subset.
- Duplicate checks passed on the currently migrated subset.
- Required null/blank checks passed on the currently migrated subset.

Failed row-count/key tables:

- `booking_status_history`
- `handoff_queue`
- `booking_event_trail`
- `traveler_documents`
- `trip_media`
- `assignment_history`
- `user_audit_log`
- `sync_queue`

Duplicate check result:

| Check | Result |
| --- | --- |
| `trip_bookings.idempotency_key` | `PASS` |
| `leads.idempotency_key` | `PASS` |
| `handoff_queue.idempotency_key` | `PASS` on current empty target table |
| Active traveler/trip booking pair | `PASS` |
| Open handoff pair | `PASS` on current empty target table |

Required null checks:

```text
PASS for all checks executed by validation script.
```

## Staging Readiness

Staging PostgreSQL is not ready for app testing yet.

Reason:

```text
The staged database is partially migrated and validation failed.
```

## Recommended Fix Options

Choose one before rerunning migration into a clean staging schema/database:

1. Data cleanup option: identify and exclude or repair orphan `booking_status_history` rows whose `booking_id` does not exist in `trip_bookings`.
2. Schema compatibility option: remove or relax the PostgreSQL foreign key from `booking_status_history.booking_id` to `trip_bookings.booking_id` so PostgreSQL matches the current legacy SQLite data shape.
3. Archive option: migrate orphan booking history into a separate legacy/audit table that preserves history without blocking normalized booking constraints.

Recommended senior path:

```text
Use option 3 if the history is valuable, otherwise option 1. Avoid silently dropping data without a business decision.
```

## Next Step

Do not switch app configuration yet.

Before another migration attempt:

1. Decide how to handle orphan booking status history.
2. Reset or recreate the staging `ravel` schema.
3. Reapply `001_create_schema.sql`.
4. Rerun actual migration.
5. Rerun validation.
