# SQLite To PostgreSQL Data Repair Plan

Date: 2026-08-03

## Problem

The staging PostgreSQL migration failed because `booking_status_history` contains 17 legacy rows whose `booking_id` values do not exist in `trip_bookings`.

PostgreSQL correctly rejected those rows because `database/postgres/001_create_schema.sql` enforces:

```text
booking_status_history.booking_id -> trip_bookings.booking_id
```

SQLite must remain unchanged until a repair approach is approved.

## Repair Options

## Option A: Archive Orphan History Rows Into A Legacy Table

Create a PostgreSQL-only table such as `legacy_booking_status_history_orphans` and migrate orphan history rows there. Then migrate only valid rows into `booking_status_history`.

Example target shape:

```sql
CREATE TABLE IF NOT EXISTS legacy_booking_status_history_orphans (
    history_id BIGINT PRIMARY KEY,
    booking_id VARCHAR(50) NOT NULL,
    old_status VARCHAR(50),
    new_status VARCHAR(50),
    changed_at TIMESTAMPTZ,
    changed_by VARCHAR(50),
    change_source VARCHAR(50),
    notes TEXT,
    archived_reason TEXT NOT NULL DEFAULT 'missing_trip_booking_parent',
    archived_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Pros:

- Preserves all legacy audit rows.
- Keeps production PostgreSQL foreign keys strict.
- Does not invent bookings, trips, travelers, or customer facts.
- Makes the legacy data gap explicit and auditable.

Cons:

- Requires a small migration-script change.
- App screens expecting all history in one table would not see orphan rows unless explicitly wired later.

## Option B: Exclude Orphan Rows During Migration And Save CSV Report

Skip orphan `booking_status_history` rows during migration and retain an external CSV report.

Pros:

- Fastest path to a clean PostgreSQL migration.
- Keeps strict production FK.
- Avoids fake booking records.

Cons:

- Audit history leaves the database.
- CSV can be lost, mishandled, or accidentally committed.
- Future operational users cannot query the orphan history from PostgreSQL.

## Option C: Create Placeholder Legacy Booking Records Only If Business-Approved

Insert placeholder rows in `trip_bookings` for missing booking IDs, then migrate history normally.

Pros:

- Keeps status history in the normal table.
- Preserves FK consistency without a second archive table.

Cons:

- High hallucination/data-quality risk.
- Requires inventing or approving placeholder traveler/trip/customer fields.
- Could confuse booking dashboards, revenue reporting, payment status, and customer history.
- Not acceptable without explicit business approval and clear `legacy_placeholder` flags.

## Option D: Relax FK Temporarily For Staging Only

Remove or defer the `booking_status_history` FK in staging only to allow all rows through.

Pros:

- Quick diagnostic path.
- Useful only if testing app behavior with messy legacy data is the goal.

Cons:

- Not recommended for production.
- Allows data shape that the production schema is meant to prevent.
- Can hide real migration quality problems.
- Creates drift between staging and production expectations.

## Recommendation

Recommended option: `Option A`.

Archive orphan history rows into a PostgreSQL legacy table and keep `booking_status_history` strict.

This is the safest senior migration path because it:

- Preserves historical data.
- Avoids fake bookings.
- Keeps production referential integrity.
- Makes legacy inconsistencies visible and reviewable.
- Does not modify SQLite.

Fallback acceptable option: `Option B`, but only if the business confirms these 17 orphan history rows have no operational value.

Avoid unless explicitly approved:

- `Option C`, because placeholder bookings could pollute CRM truth.
- `Option D`, because relaxing the FK weakens the production data contract.

## Proposed Implementation If Option A Is Approved

1. Do not modify SQLite.
2. Reset or recreate staging schema `ravel`.
3. Add `legacy_booking_status_history_orphans` to PostgreSQL schema or a staging repair SQL.
4. Update `tools/migrate_sqlite_to_postgres.py` so:
   - Valid `booking_status_history` rows migrate into `booking_status_history`.
   - Orphan rows migrate into `legacy_booking_status_history_orphans`.
   - Summary logs show counts only.
5. Update `tools/validate_postgres_migration.py` so:
   - `booking_status_history` expected count equals valid source rows.
   - Orphan history count is validated in the legacy archive table.
   - No orphan rows remain in strict FK tables.
6. Rerun migration into clean staging.
7. Rerun validation.
8. Start app testing only after validation passes.

## Expected Counts With Option A

Based on the read-only audit:

| Target | Expected Rows |
| --- | ---: |
| `booking_status_history` | 48 |
| `legacy_booking_status_history_orphans` | 17 |
| Total preserved status history rows | 65 |

## Approval Needed

Before implementation, choose one:

```text
Option A: archive orphan history rows in PostgreSQL legacy table.
Option B: exclude orphan rows and keep CSV report only.
Option C: create placeholder bookings after business approval.
Option D: relax FK only for staging diagnostics.
```

No repair or migration rerun should happen until an option is approved.
