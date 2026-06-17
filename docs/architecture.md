# Architecture Overview

Rahma Travel OS is currently a lightweight monorepo with a Flask CRM, a spreadsheet-backed AI-agent demo, a TypeScript middleware prototype, and a React admin UI prototype.

## Current Runtime Layers

- `apps/admin-web/`: React/Vite operator UI. It currently talks to `apps/middleware`.
- `apps/api/`: database-backed Flask CRM. It owns SQLAlchemy models, Jinja admin pages, CRM routes, import scripts, and Flask-Migrate setup.
- `apps/middleware/`: TypeScript API layer intended to sit between admin UI, CRM core, and future channel integrations.
- `services/ai_agent/`: spreadsheet-backed demo Flask app and customer conversation flow. It owns the current `/`, `/api/*`, `/api/v1/*`, `/crm/*`, and `/webhook` MVP routes.
- `services/instagram/`: extracted webhook placeholder boundary for future Meta work.
- `services/crm/`: DB-first workflow service used by the spreadsheet demo to write into the CRM database and mirror back to Excel or Google Sheets.
- `database/`: migrations, Prisma schema, and future seeds.
- `scripts/`: workbook migration, audit, and phase automation utilities.
- `tests/`: regression coverage that locks the MVP workbook, CRM, booking, identity, sync, and agent behavior.

## Request Flow

1. The demo Flask app creates a `SessionFlowManager` and a sheet gateway.
2. The agent flow collects phone, profile, trip, and booking choices.
3. Spreadsheet gateways read/write Excel, Google Drive xlsx, or Google Sheets depending on settings.
4. When write-through is required, `services/ai_agent/ai_agent_app/system_bridge.py` calls `services/crm/system_services/UnifiedCRMService`.
5. The CRM service commits core state to SQLite first, then attempts workbook/sheet sync.
6. Failed sync operations are recorded for admin review instead of rolling back the database transaction.

## Integration Direction

The future Instagram/Meta path should enter through `services/instagram`, validate Meta signatures, verify webhook subscription challenges, enqueue inbound events, run deterministic workflow logic, rate limit outbound responses, and route risky or ambiguous cases into human review before any customer-visible response.

See [Instagram / Meta integration notes](./instagram-integration-plan.md) and [Production readiness checklist](./production-checklist.md).
