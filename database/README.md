# Database

Database-owned assets live here.

- `migrations/` contains Alembic/Flask-Migrate migrations for `apps/api`.
- `postgres/` contains the reviewed PostgreSQL schema, staging migration plan, DevOps setup notes, rollback plan, and SQLite-to-PostgreSQL tooling.
- `schema/` contains the Prisma schema used by `apps/middleware`.
- `seeds/` is reserved for future production-safe seed data.

The MVP still uses SQLite locally. Production work should migrate data to PostgreSQL only after staging validation passes and rollback procedures are reviewed.
