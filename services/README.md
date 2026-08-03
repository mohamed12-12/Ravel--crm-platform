# Services

Domain services and integration boundaries live here.

- `ai_agent/` contains the spreadsheet-backed MVP agent app.
- `crm/` contains shared CRM workflow services.
- `instagram/` contains the future Meta/Instagram webhook boundary.
- `notifications/` is reserved for outbound alert adapters.

This folder should hold reusable service logic, not application entrypoints.

## Database Migration Note

Some shared CRM services still use direct SQLite access while the main CRM app uses SQLAlchemy. Before production-only PostgreSQL cutover, review `database/postgres/SQLITE_TO_POSTGRES_MIGRATION_PLAN.md` and smoke-test these service paths against staging PostgreSQL.
