# Rahma Travel OS

Rahma Travel OS is the MVP foundation for Rahma Traveler's CRM, booking automation, AI-agent workflow, and future Instagram/Meta customer conversation platform.

The repo is now organized as a product foundation while preserving the current MVP behavior. Public routes, workflows, database behavior, and agent logic are intentionally unchanged in this cleanup phase.

## Main Areas

- `apps/admin-web/` - React/Vite operator admin UI prototype.
- `apps/api/` - database-backed Flask CRM with models, routes, services, and admin templates.
- `apps/middleware/` - TypeScript middleware/API layer for admin and future channel orchestration.
- `services/ai_agent/` - spreadsheet-backed Flask demo app, agent flow, and sheet gateways.
- `services/instagram/` - Instagram/Meta webhook placeholder boundary.
- `services/crm/` - shared CRM/business workflow services.
- `database/` - migrations, Prisma schema, and seed placeholders.
- `packages/` - shared package placeholders for future extracted code.
- `scripts/` - workbook cleanup, audit, and alignment scripts.
- `tests/` - Python regression tests for the MVP business behavior.
- `docs/` - active architecture and production-readiness documentation.
- `archive/` - deprecated demos, old phase artifacts, and source/demo workbooks.

## Documentation

- [Architecture overview](./docs/architecture.md)
- [Folder structure](./docs/folder-structure.md)
- [Setup instructions](./docs/SETUP.md)
- [Instagram / Meta integration notes](./docs/instagram-integration-plan.md)
- [Production readiness checklist](./docs/production-checklist.md)
- [Cleanup report](./CLEANUP_REPORT.md)

## Quick Start

```bash
npm install
python -m pip install -r apps/api/requirements.txt
cp .env.example .env
```

Run checks from the repository root:

```bash
npm test
npm run build
npm run typecheck
python -m pytest tests
python -m compileall .
```

## Security

Never commit real `.env` files, service-account JSON files, API keys, database credentials, certificates, local database files, build output, logs, or cache folders.
