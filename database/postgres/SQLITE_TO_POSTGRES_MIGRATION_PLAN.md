# SQLite To PostgreSQL Migration Plan

## Executive Summary

The current Rahma CRM database is SQLite at:

```text
apps/api/instance/rahma_traveler_dev.db
```

The active application layer is Flask with SQLAlchemy models in `apps/api/app/models/`. The project also has legacy/shared raw SQLite usage in `services/crm/system_services/unified_service.py` and read-only AI-agent access paths, so PostgreSQL cutover must be staged and smoke-tested before production.

No SQLite data should be modified during migration preparation. The migration script opens SQLite with `mode=ro`.

## Inspected Assets

- SQLite database: `apps/api/instance/rahma_traveler_dev.db`
- Flask app database config: `apps/api/app/__init__.py`
- SQLAlchemy extension: `apps/api/app/extensions.py`
- SQLAlchemy models: `apps/api/app/models/`
- Alembic migrations: `database/migrations/versions/`
- Raw SQLite services: `services/crm/system_services/unified_service.py`
- AI-agent database config: `services/ai_agent/ai_agent_app/config.py`
- Middleware Prisma schema: `database/schema/schema.prisma`

## Tables

Application tables inventoried from the live SQLite catalog:

| Table | Primary Key | Notes |
| --- | --- | --- |
| `users` | `id` | Employee/admin users. |
| `travelers` | `traveler_id` | Traveler profile, passport metadata, phone lookup fields. |
| `trips` | `trip_id` | Trip inventory, room availability, prices as display text. |
| `community_events` | `event_id` | Community event catalog. |
| `dm_copy_library` | `message_key` | Arabic/English message templates. |
| `language_templates` | `id` | Approved language templates. |
| `leads` | `lead_id` | CRM leads, assignment, follow-up, handoff, passport state. |
| `interactions` | `interaction_id` | Customer conversation interaction trail. |
| `trip_bookings` | `booking_id` | Booking drafts and operational booking state. |
| `ce_bookings` | `booking_id` | Community event bookings. |
| `booking_status_history` | `history_id` | Booking status audit trail. |
| `handoff_queue` | `handoff_id` | Employee handoff queue. |
| `booking_event_trail` | `event_id` | Booking/lead/traveler event audit trail. |
| `traveler_documents` | `document_id` | Uploaded document metadata and storage paths. |
| `trip_media` | `media_id` | Trip image metadata and public URLs. |
| `assignment_history` | `id` | Employee assignment audit records. |
| `user_audit_log` | `id` | User/admin action audit log. |
| `sync_queue` | `mapping_name`, `record_id` | Composite-key sync queue. |

## Type Mapping

| SQLite Pattern | PostgreSQL Type |
| --- | --- |
| `INTEGER` auto primary key | `BIGSERIAL` |
| `INTEGER` counters | `INTEGER` |
| `VARCHAR(n)` | `VARCHAR(n)` |
| `TEXT` | `TEXT` |
| `FLOAT` | `DOUBLE PRECISION` |
| `BOOLEAN` stored as 0/1 | `BOOLEAN` |
| `DATE` | `DATE` |
| `DATETIME` | `TIMESTAMPTZ` |

JSON-like fields remain `TEXT` for this migration because the application currently stores and reads them as text:

- `trip_bookings.room_requirements_json`
- `booking_event_trail.metadata_json`
- Template button/variable text fields
- Trip suggestion ID lists

File/path fields remain text/varchar metadata and do not include binary files:

- `travelers.passport_attachment_ref`
- `traveler_documents.storage_path`
- `traveler_documents.file_name`
- `trip_media.storage_key`
- `trip_media.public_url`
- `trip_media.original_filename`

## Constraints And Indexes

`database/postgres/001_create_schema.sql` includes:

- Primary keys for all tables.
- Unique constraints for `users.username`, `users.email`, `trip_media.public_id`, and `trip_media.storage_key`.
- Foreign keys between travelers, trips, leads, interactions, bookings, documents, media, users, and audit records.
- Indexes for phone lookup, assignments, idempotency keys, handoff queue status, booking active-pair lookup, media filtering, and audit lookup.
- `idempotency_key` indexes for lead, booking, handoff, and document write-safety paths.

## Migration Order

The scripts migrate in dependency-safe order:

1. `users`
2. `travelers`
3. `trips`
4. `community_events`
5. `dm_copy_library`
6. `language_templates`
7. `leads`
8. `interactions`
9. `trip_bookings`
10. `ce_bookings`
11. `booking_status_history`
12. `handoff_queue`
13. `booking_event_trail`
14. `traveler_documents`
15. `trip_media`
16. `assignment_history`
17. `user_audit_log`
18. `sync_queue`

## Dry Run

Dry-run reads SQLite only and does not require PostgreSQL:

```bash
python tools/migrate_sqlite_to_postgres.py --dry-run
```

Table subset:

```bash
python tools/migrate_sqlite_to_postgres.py --dry-run --tables travelers,trips,trip_bookings
```

## Staging Migration

Create schema:

```bash
psql "$POSTGRES_URL" -f database/postgres/001_create_schema.sql
```

Run migration:

```bash
python tools/migrate_sqlite_to_postgres.py --postgres-url "$POSTGRES_URL"
```

Resume or rerun behavior:

- Inserts use primary-key `ON CONFLICT DO NOTHING`.
- Existing records are skipped instead of duplicated.
- Serial sequences are reset after serial-key tables are migrated.

## Validation

Run:

```bash
python tools/validate_postgres_migration.py --postgres-url "$POSTGRES_URL"
```

Validation checks:

- Table row counts.
- Primary key presence by table.
- Foreign key consistency.
- Duplicate lead/booking/handoff idempotency groups.
- Duplicate active traveler/trip bookings.
- Duplicate open handoff groups.
- Required-field null or blank values.

The validator prints summaries only and does not print customer records.

## Application Cutover Checklist

Staging first:

1. Confirm PostgreSQL SSL connection.
2. Apply schema to empty staging database.
3. Run migration dry-run.
4. Run migration to staging.
5. Run validation.
6. Start the Flask CRM against staging `DATABASE_URL`.
7. Run admin dashboard smoke test.
8. Run traveler lookup smoke test.
9. Run trip search smoke test.
10. Run booking draft smoke test.
11. Run handoff queue smoke test.
12. Run AI-agent Arabic and English flow smoke tests.

Production later:

1. Schedule a write freeze.
2. Take SQLite backup.
3. Take PostgreSQL snapshot.
4. Replay migration into production PostgreSQL.
5. Validate.
6. Switch `DATABASE_URL`.
7. Run smoke tests.
8. Keep SQLite backup untouched through the rollback window.

## Remaining Engineering Follow-Ups

- Replace legacy direct `sqlite3` service paths with SQLAlchemy/PostgreSQL-compatible data access before production-only PostgreSQL operation.
- Confirm whether JSON-like `TEXT` fields should become `JSONB` after the first safe migration.
- Add PostgreSQL CI coverage once a test container or managed staging database is available.
- Review Alembic autogeneration after the first schema lands so future changes stay migration-managed.
