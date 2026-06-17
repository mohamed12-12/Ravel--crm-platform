# Architecture

The active architecture documentation lives in [docs/architecture.md](./docs/architecture.md).

Quick map:

- `apps/admin-web/` is the React admin UI prototype.
- `apps/api/` is the database-backed Flask CRM.
- `apps/middleware/` is the TypeScript middleware/API layer.
- `services/ai_agent/` is the spreadsheet-backed demo Flask app and agent flow.
- `services/instagram/` is the future Instagram/Meta integration boundary.
- `services/crm/` is the DB-first business workflow boundary.
- `database/` owns migrations, schema, and future seeds.

See also [docs/folder-structure.md](./docs/folder-structure.md) and [docs/production-checklist.md](./docs/production-checklist.md).
