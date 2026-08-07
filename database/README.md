# Database

Database-owned assets live here.

- `migrations/` contains Alembic/Flask-Migrate migrations for `apps/api`.
- `postgres/` contains the reviewed PostgreSQL schema, staging migration plan, DevOps setup notes, rollback plan, and SQLite-to-PostgreSQL tooling.
- `schema/` contains the Prisma schema used by `apps/middleware`.
- `seeds/` is reserved for future production-safe seed data.

The MVP still uses SQLite locally. Production work should migrate data to PostgreSQL only after staging validation passes and rollback procedures are reviewed.

## Schema drift: SQLAlchemy models vs. Alembic migrations

Several columns (`travelers`/`leads` passport & currency fields,
`trip_bookings`/`leads`/`handoff_queue.idempotency_key`) were added to the
SQLAlchemy models in `apps/api/app/models/` at some point without a
matching Alembic migration. Local SQLite dev databases never surfaced this,
because `apps/api/app/__init__.py` runs its own idempotent
`ALTER TABLE ... ADD COLUMN` backfills on every app start (SQLite only --
gated on `uri.startswith("sqlite")`), and `db.create_all()` in tests always
builds a schema matching the *current* models regardless of migration
history. Postgres has neither of those safety nets, so a database built
purely from `flask db upgrade` was missing these columns -- see
`migrations/versions/f4c8b21e9a3d_*` and `a3f6c9e2d817_*` for the
reconciliation migrations that closed each gap. If a "column does not
exist" error ever surfaces against Postgres for a column that works fine
locally, this is the first thing to check: does every model column added
recently actually have a migration behind it?

## RDS role scoping

`DEVOPS_POSTGRES_SETUP_INSTRUCTIONS.md` documents a least-privilege
`rahma_app` role (its own database, `SELECT`/`INSERT`/`UPDATE`/`DELETE`
only, no DDL) intended for the app's actual runtime credential. The shared
staging/production RDS instance this project's `.env` points at also hosts
other, unrelated projects' schemas on the same instance -- confirm which
credential is actually configured in `DATABASE_URL`/`POSTGRES_URL` before
assuming `rahma_app`'s scoping is what's really in effect.
