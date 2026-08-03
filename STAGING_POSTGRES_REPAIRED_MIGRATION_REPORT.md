# Staging PostgreSQL Repaired Migration Report

Date: 2026-08-03

## Scope

This report covers the approved staging-only repaired SQLite to PostgreSQL migration using Option A:

```text
Archive orphan booking_status_history rows into a PostgreSQL legacy table while keeping the real booking_status_history FK strict.
```

## Safety Controls

- Target schema: `ravel`
- SSL: `enabled`
- Staging schema `ravel` was reset before rerun.
- Production was not touched.
- App configuration was not switched.
- SQLite was not modified.
- Full PostgreSQL URL and password were not written to this report.
- `booking_status_history` foreign key was not relaxed.

SQLite unchanged confirmation:

```text
SQLite file size before: 528384 bytes
SQLite file size after:  528384 bytes
SQLite SHA-256 unchanged: true
```

## Package Changes

Updated schema:

```text
database/postgres/001_create_schema.sql
```

Added archive table:

```text
legacy_booking_status_history_orphans
```

Archive table preserves:

- `history_id`
- `booking_id`
- `original_booking_id`
- `old_status`
- `new_status`
- `changed_at`
- `changed_by`
- `change_source`
- `notes`
- `source_table`
- `archived_reason`
- `archived_at`

Updated migration script:

```text
tools/migrate_sqlite_to_postgres.py
```

Updated validation script:

```text
tools/validate_postgres_migration.py
```

## Migration Result

Result: `PASS`

The full migration completed successfully.

| Table | SQLite Source Rows | PostgreSQL Target Rows | Status |
| --- | ---: | ---: | --- |
| `users` | 2 | 2 | pass |
| `travelers` | 540 | 540 | pass |
| `trips` | 47 | 47 | pass |
| `community_events` | 0 | 0 | pass |
| `dm_copy_library` | 0 | 0 | pass |
| `language_templates` | 0 | 0 | pass |
| `leads` | 0 | 0 | pass |
| `interactions` | 12 | 12 | pass |
| `trip_bookings` | 49 | 49 | pass |
| `ce_bookings` | 0 | 0 | pass |
| `booking_status_history` | 48 valid rows | 48 | pass |
| `legacy_booking_status_history_orphans` | 17 orphan rows | 17 | pass |
| `handoff_queue` | 3 | 3 | pass |
| `booking_event_trail` | 3 | 3 | pass |
| `traveler_documents` | 4 | 4 | pass |
| `trip_media` | 6 | 6 | pass |
| `assignment_history` | 3 | 3 | pass |
| `user_audit_log` | 5 | 5 | pass |
| `sync_queue` | 29 | 29 | pass |

## Booking History Repair

Result: `PASS`

| Metric | Count |
| --- | ---: |
| SQLite total `booking_status_history` rows | 65 |
| Valid rows migrated to strict `booking_status_history` | 48 |
| Orphan rows archived to `legacy_booking_status_history_orphans` | 17 |
| Total preserved history rows | 65 |

The strict FK table contains only valid booking history rows whose `booking_id` exists in `trip_bookings`.

The orphan rows were preserved in the legacy archive table with:

```text
archived_reason=missing_trip_booking_parent
source_table=booking_status_history
```

## Validation Result

Result: `PASS`

Validation checks passed:

- All table row counts matched expected counts.
- Primary key checks passed.
- `booking_status_history` FK consistency passed.
- Other FK-like checks passed.
- Duplicate checks passed.
- Required null/blank checks passed.
- Total booking history preservation check passed.

Duplicate checks:

| Check | Result | Duplicate Groups |
| --- | --- | ---: |
| `trip_bookings.idempotency_key` | pass | 0 |
| `leads.idempotency_key` | pass | 0 |
| `handoff_queue.idempotency_key` | pass | 0 |
| Active traveler/trip booking pair | pass | 0 |
| Open handoff pair | pass | 0 |

Critical tables:

| Table | Result |
| --- | --- |
| `travelers` | pass |
| `trips` | pass |
| `trip_bookings` | pass |
| `leads` | pass |
| `handoff_queue` | pass |
| `booking_status_history` | pass |
| `legacy_booking_status_history_orphans` | pass |

## Staging Readiness

Staging PostgreSQL is ready for application smoke testing.

Do not switch production or app configuration yet. Recommended next tests against staging:

1. Start the CRM app with staging `DATABASE_URL`.
2. Load admin dashboard.
3. Check travelers list/detail.
4. Check trips list/detail.
5. Check booking detail/status history behavior.
6. Check handoff queue.
7. Run AI-agent Arabic and English smoke flows against staging data.

## Final Status

Acceptance status:

- SQLite unchanged: `PASS`
- Production untouched: `PASS`
- Staging migration completes: `PASS`
- Valid history rows migrated to strict FK table: `PASS`
- Orphan history rows preserved in legacy archive table: `PASS`
- Validation passes: `PASS`
