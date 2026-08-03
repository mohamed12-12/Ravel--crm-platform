# PostgreSQL Migration Rollback Plan

## Safety Position

The SQLite database remains the source of truth until staging migration, validation, and app smoke tests pass. The migration scripts open SQLite in read-only mode, so a failed staging migration should not damage the current SQLite database.

## If Staging Migration Fails

1. Stop the staging app process.
2. Leave `apps/api/instance/rahma_traveler_dev.db` untouched.
3. Set app environment back to SQLite:

```text
DATABASE_URL=sqlite:///rahma_traveler_dev.db
APP_ENV=development
FLASK_CONFIG=development
```

4. If the AI agent shared-service path is enabled, restore the SQLite path:

```text
RAHMA_SYSTEM_DB_PATH=apps/api/instance/rahma_traveler_dev.db
```

5. Restart the app and run the local smoke flow.
6. Drop or restore only the staging PostgreSQL database if needed.

## Restore PostgreSQL Staging From Backup

Create a staging backup before replays:

```bash
pg_dump --format=custom --file rahma_traveler_staging.before_migration.dump "$POSTGRES_URL"
```

Restore into a clean staging database:

```bash
dropdb rahma_traveler_staging_restore
createdb rahma_traveler_staging_restore
pg_restore --clean --if-exists --dbname rahma_traveler_staging_restore rahma_traveler_staging.before_migration.dump
```

Use a restore database first. Do not restore over production during troubleshooting.

## Production Rollback After Approved Cutover

Production cutover should happen only after staging passes. If production cutover later fails:

1. Freeze writes at the application/load balancer layer.
2. Capture the PostgreSQL error summary and current failed cutover timestamp.
3. Switch `DATABASE_URL` back to the final pre-cutover SQLite database.
4. Restart app services.
5. Run smoke tests for traveler lookup, trip search, booking draft, handoff queue, and admin dashboard.
6. Keep the failed PostgreSQL database intact for forensic comparison.

## Backup Restore Steps

SQLite:

1. Keep the final pre-cutover SQLite file copied to encrypted storage.
2. Restore by placing it back at the configured SQLite path.
3. Confirm file ownership and app read/write permissions.
4. Start the app with the SQLite `DATABASE_URL`.

PostgreSQL:

1. Restore a provider snapshot or `pg_restore` backup to a new database.
2. Validate row counts and key records.
3. Point staging to the restored database first.
4. Promote only after smoke tests pass.

## Rollback Exit Criteria

Rollback is complete when:

- The app starts cleanly.
- Admin dashboard loads.
- Traveler lookup works.
- Trip search works.
- No duplicate booking is created by repeated confirmation.
- Handoff queue can be viewed.
- No secrets or customer data were written into logs or commits.
