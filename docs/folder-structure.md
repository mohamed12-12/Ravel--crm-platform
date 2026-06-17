# Folder Structure

## Product Surfaces

- `apps/admin-web/` - React/Vite operator admin UI prototype.
- `apps/api/` - Database-backed Flask CRM application.
- `apps/middleware/` - Express/TypeScript middleware/API layer.
- `demo_web/` - Backward-compatible wrapper for older `demo_web.app` imports.

## Services

- `services/ai_agent/` - Spreadsheet-backed demo Flask app, in-memory agent flow, web templates, auth, and sheet gateways.
- `services/instagram/` - Instagram/Meta webhook placeholder boundary.
- `services/crm/` - Shared CRM workflow services, including DB-first identity, lead, booking, and sync coordination.
- `services/notifications/` - Placeholder for future alert delivery adapters.

## Data And Shared Code

- `database/migrations/` - Alembic/Flask-Migrate migrations used by `apps/api`.
- `database/schema/` - Prisma schema used by `apps/middleware`.
- `database/seeds/` - Placeholder for future seed data.
- `packages/shared/` - Placeholder for cross-runtime contracts and utilities.
- `packages/ui/` - Placeholder for reusable admin UI components.

## Supporting Folders

- `scripts/` - Workbook preparation, audits, migration helpers, and credential checks.
- `tests/` - Python regression tests that lock MVP behavior.
- `docs/` - Active documentation.
- `archive/` - Deprecated demos, archived phase material, and local source/demo artifacts.

## Local Generated Folders

The following should stay out of commits:

- `node_modules/`
- `dist/`
- `logs/`
- `.tmp-test-workdirs/`
- `.pytest_cache/`
- `.history/`
- `pytest-cache-files-*`
- local `.db`, `.sqlite`, `.xlsx`, and `.pdf` artifacts

One legacy SQLite database fixture currently exists at `apps/api/instance/rahma_traveler_dev.db`; it is documented as a manual-review item in the cleanup report.
