# PostgreSQL DevOps Setup Instructions

## Scope

These instructions prepare a staging PostgreSQL database for Rahma Traveler CRM migration testing. Do not use production first, do not copy production secrets into this repository, and do not commit database dumps.

## Database Names

- Staging: `rahma_traveler_staging`
- Production later: `rahma_traveler_production`

## App User

Create a dedicated application role. Example name:

```sql
CREATE ROLE rahma_app LOGIN PASSWORD '<set-outside-git>';
CREATE DATABASE rahma_traveler_staging OWNER rahma_app;
```

Use a password manager or AWS Secrets Manager. Do not paste the real password into docs, shell history, commits, screenshots, or support tickets.

## Minimum Permissions

For staging, the app role needs:

- `CONNECT` on the database.
- `USAGE` on the application schema, normally `public`.
- `SELECT`, `INSERT`, `UPDATE`, and `DELETE` on application tables.
- `USAGE` and `SELECT` on sequences created by `BIGSERIAL` columns.

Example after schema creation:

```sql
GRANT CONNECT ON DATABASE rahma_traveler_staging TO rahma_app;
GRANT USAGE ON SCHEMA public TO rahma_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO rahma_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO rahma_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO rahma_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO rahma_app;
```

## Required Extensions

No PostgreSQL extension is required by `001_create_schema.sql`.

Optional future extension:

- `pgcrypto` if UUID generation moves into PostgreSQL later.

## Environment Variables

Use staging values first:

```text
DATABASE_URL=postgresql+psycopg://rahma_app:<password>@<host>:5432/rahma_traveler_staging?sslmode=require
POSTGRES_URL=postgresql://rahma_app:<password>@<host>:5432/rahma_traveler_staging?sslmode=require
FLASK_CONFIG=production
APP_ENV=staging
```

Notes:

- `DATABASE_URL` is used by the Flask/SQLAlchemy app.
- `POSTGRES_URL` can be used by the migration and validation scripts.
- Keep SQLite-specific paths such as `RAHMA_SYSTEM_DB_PATH` only for local fallback or explicit rollback testing.

## Schema Creation

Create schema on an empty staging database:

```bash
psql "$POSTGRES_URL" -f database/postgres/001_create_schema.sql
```

Then run a dry-run before any write:

```bash
python tools/migrate_sqlite_to_postgres.py --dry-run
```

Run the staged migration:

```bash
python tools/migrate_sqlite_to_postgres.py --postgres-url "$POSTGRES_URL"
```

Validate before app cutover:

```bash
python tools/validate_postgres_migration.py --postgres-url "$POSTGRES_URL"
```

## Backup Policy

Minimum staging policy:

- Take a `pg_dump --format=custom` backup before every migration replay.
- Keep at least 7 daily staging backups while migration testing is active.
- Store backups outside the EC2 instance disk, preferably S3 with encryption enabled.

Minimum production policy before cutover:

- Enable automated RDS backups or equivalent managed PostgreSQL point-in-time recovery.
- Take a manual snapshot immediately before production cutover.
- Keep a verified SQLite backup from the final pre-cutover state.
- Test restore on staging before trusting the process.

## SSL Requirement

Require SSL for all non-local PostgreSQL connections.

Use:

```text
sslmode=require
```

If the provider supports certificate validation, prefer `sslmode=verify-full` with the provider CA certificate.

## Network And Firewall Access

For AWS EC2/RDS:

- Do not expose PostgreSQL to `0.0.0.0/0`.
- Allow inbound PostgreSQL only from the EC2 app security group, a bastion/VPN, or a tightly controlled admin IP.
- Keep database and app in the same VPC/private subnets when possible.
- Restrict outbound access from the app host to required services only.

## Known Unresolved Risk: Shared RDS Instance

Migrated from `POSTGRES_PRODUCTION_CUTOVER_PLAN.md` (deleted as a
superseded cutover checklist -- cutover already happened, see
`ravel_agent_master_discovery_report.md` -- but this specific risk was
never confirmed resolved and shouldn't be lost with the rest of that
file):

- Staging used a shared RDS instance; as of this project's last live
  check, production's `.env` still points `DATABASE_URL`/`POSTGRES_URL`
  directly at that same shared instance (host `clinic-isac...rds.amazonaws.com`,
  db `postgres`, `search_path=ravel`), shared with other apps'
  schemas (Mem0/OlivsDashboard/safaria). The least-privilege `rahma_app`
  role documented above in "App User"/"Minimum Permissions" addresses the
  *permissions* half of this; a fully isolated instance (not just a
  scoped role on a shared one) has not been provisioned.
- A routine CI smoke job for the Postgres agent-path (matching the
  one-off staging smoke test already run by hand once against this
  schema -- see `RUN_GUIDE.md`'s PostgreSQL section) is still
  recommended, not yet automated.
- Production secrets and service accounts should be owned by DevOps
  (Secrets Manager/SSM), not hand-edited `.env` files -- see
  `deploy/README.md`'s "Secret Handling" pointer, not yet wired up.

## Staging First, Production Later

Required order:

1. Create staging PostgreSQL.
2. Apply `001_create_schema.sql`.
3. Run migration dry-run.
4. Run staged migration.
5. Run validation script.
6. Run app smoke tests against staging.
7. Review rollback plan.
8. Only then schedule production cutover.

Do not run destructive production migration commands from this repository.
