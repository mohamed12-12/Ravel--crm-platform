# Database

Database-owned assets live here.

- `migrations/` contains Alembic/Flask-Migrate migrations for `apps/api`.
- `schema/` contains the Prisma schema used by `apps/middleware`.
- `seeds/` is reserved for future production-safe seed data.

The MVP still uses SQLite locally. Production work should migrate demo data to PostgreSQL with reviewed migrations and rollback procedures.
