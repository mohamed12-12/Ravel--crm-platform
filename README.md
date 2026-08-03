# Rahma Traveler

Rahma Traveler is a travel CRM and AI sales-agent platform for managing travelers, leads, trips, bookings, employee follow-up, handoffs, and customer conversations.

The project currently contains two main Flask applications:

- `apps/api`: the operational Rahma CRM used by employees.
- `services/ai_agent`: the AI sales-agent demo/runtime that talks to customers and connects to CRM data.

The system is designed around one rule: CRM data is the source of truth. The AI assistant can converse naturally, but verified CRM facts, writes, booking drafts, passport references, and employee workflow states must come through backend-controlled services.

## What This Project Does

- Manages traveler profiles, phone normalization, duplicate detection, and passport documents.
- Tracks leads through a CRM sales pipeline.
- Manages trips, room inventory, boys/girls room availability, and booking drafts.
- Gives employees simple booking/payment/follow-up controls.
- Runs an AI sales assistant for WhatsApp/DM-style conversations in Arabic and English.
- Uses controlled CRM tools so the assistant cannot invent travelers, trips, prices, availability, bookings, or payment facts.
- Provides OpenAPI contracts for backend testing tools such as TestSprite.

## Repository Map

| Path | Purpose |
| --- | --- |
| `apps/api/` | Main Flask CRM app with SQLAlchemy models, employee UI, CRM routes, auth, and operational APIs. |
| `services/ai_agent/` | AI agent runtime, chat session handling, workflow policy, tool-calling integration, and demo UI. |
| `services/crm/` | Shared CRM/business services used by both the CRM and AI agent. |
| `services/api_contracts/` | OpenAPI contract builders for the CRM API and AI Agent API. |
| `services/instagram/` | Meta/Instagram webhook parsing and integration boundary. |
| `apps/admin-web/` | React/Vite admin UI prototype. |
| `apps/middleware/` | TypeScript middleware/orchestration prototype. |
| `database/migrations/` | Alembic migrations for database-backed CRM changes. |
| `database/postgres/` | Staging-first SQLite to PostgreSQL schema, migration plan, DevOps setup, and rollback docs. |
| `docs/` | Architecture, testing, production-readiness, data authority, API, and workflow documentation. |
| `tests/` | Python regression suite covering CRM, agent behavior, workflow policy, auth, and API contracts. |
| `archive/` | Legacy demo code, old artifacts, and historical source files. |

## Main Local URLs

| Service | Default URL | Notes |
| --- | --- | --- |
| CRM app | `http://<server>:5000` | Employee CRM: travelers, leads, trips, bookings, handoffs. |
| AI Agent app | `http://<server>:3001` | Customer-facing agent demo and chat simulator. |
| Admin web | `http://<server>:3002` | React/Vite operator UI. |
| Middleware | `http://<server>:3000` | TypeScript middleware prototype when used. |

## Requirements

- Python 3.11+ recommended.
- Node.js 18+ recommended for workspace apps.
- SQLite for local development.
- Optional: Gemini API key for live tool-calling model behavior.

Install Python dependencies:

```bash
python -m pip install -r apps/api/requirements.txt
```

Install Node dependencies:

```bash
npm install
```

## Environment Setup

Create a local `.env` from the template:

```bash
copy .env.example .env
```

On macOS/Linux:

```bash
cp .env.example .env
```

Important environment variables:

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | CRM database URL. Local default is SQLite. |
| `POSTGRES_URL` | Explicit PostgreSQL target for migration and validation scripts. Use staging first. |
| `CRM_AUTH_ENABLED` | Enables employee/API authentication. |
| `CRM_API_TOKEN` | Token for server-to-server CRM API calls. |
| `AI_AGENT_MODE` | `tool_calling`, `gemini`, or `deterministic`. Use `tool_calling` for the current agent runtime. |
| `GEMINI_API_KEY` | Required only for live Gemini calls. |
| `GEMINI_MODEL` | Gemini model name. |
| `APP_HOST` | AI Agent bind host, default `0.0.0.0` in deployment. |
| `APP_PORT` | AI Agent app port, default `3001` in local launcher. |
| `DEFAULT_COUNTRY_CODE` | Default phone country code, currently `20`. |

Never commit real `.env` files, API keys, customer data, local databases, uploaded documents, logs, or service-account files.

## SQLite To PostgreSQL Migration

The current local CRM database remains SQLite at `apps/api/instance/rahma_traveler_dev.db`. PostgreSQL migration assets are prepared for staging-first validation:

- `database/postgres/001_create_schema.sql`
- `database/postgres/SQLITE_TO_POSTGRES_MIGRATION_PLAN.md`
- `database/postgres/DEVOPS_POSTGRES_SETUP_INSTRUCTIONS.md`
- `database/postgres/POSTGRES_MIGRATION_ROLLBACK_PLAN.md`
- `tools/migrate_sqlite_to_postgres.py`
- `tools/validate_postgres_migration.py`

Safe dry-run:

```bash
python tools/migrate_sqlite_to_postgres.py --dry-run
```

Do not run production cutover until staging migration, validation, and smoke tests pass.

## Run The CRM App

From the repository root:

```bash
cd apps/api
python run.py
```

The CRM app should run on:

```text
http://<server>:5000
```

Useful CRM pages:

- `/travelers/`
- `/leads/`
- `/trips/`
- `/bookings/`
- `/admin/handoffs/`
- `/admin/users`

The CRM OpenAPI contract is served at:

```text
http://<server>:5000/api/openapi.json
```

## Run The AI Agent App

From the repository root:

```bash
python demo_web/app.py
```

The AI Agent app should run on:

```text
http://<server>:3001
```

Useful AI Agent endpoints:

- `GET /api/health`
- `POST /api/session`
- `POST /api/session/{session_id}/message`
- `POST /api/session/{session_id}/passport_attachment`
- `GET /api/openapi.json`

The AI Agent OpenAPI contract is served at:

```text
http://<server>:3001/api/openapi.json
```

## AI Agent Architecture

The current production-style agent runtime is `AI_AGENT_MODE=tool_calling`.

Core responsibilities:

- `ToolCallingSessionRuntime` owns session lifecycle and customer messages.
- `ConversationWorkflowPolicy` decides the next required workflow step.
- `AgentIdentityPolicy` answers identity/model-provider questions deterministically before Gemini is called.
- `GeminiAgent` handles model/tool-loop interaction when needed.
- `ReadOnlyCRMTools` reads CRM facts.
- Controlled write tools create/update leads, booking drafts, passport references, and handoffs only after backend validation.

Important behavior:

- The assistant must not hallucinate CRM facts.
- Identity questions are answered by the backend, not by the model.
- Trip availability comes from CRM only.
- Booking drafts are created through controlled CRM writes.
- Passport upload stores file references/metadata, not raw bytes inside chat state.

## API Contracts And TestSprite

The project exposes machine-readable OpenAPI 3.1 contracts.

For TestSprite AI agent testing:

```text
API Base URL: http://<server>:3001
OpenAPI URL:  http://<server>:3001/api/openapi.json
```

For TestSprite CRM testing:

```text
API Base URL: http://<server>:5000
OpenAPI URL:  http://<server>:5000/api/openapi.json
```

Detailed TestSprite instructions are in:

[docs/api/TESTSPRITE_API_TESTING.md](./docs/api/TESTSPRITE_API_TESTING.md)

## Testing

Run the full Python regression suite:

```bash
python -m pytest -q
```

Run compile checks:

```bash
python -m compileall -q services/ai_agent services/crm services/api_contracts apps/api tests
```

Run all workspace checks:

```bash
npm test
npm run build
npm run typecheck
```

Recent verified state:

```text
328 passed, 4 subtests passed
```

## Data And Safety Rules

- CRM database records are authoritative.
- Excel/source workbooks are compatibility/demo inputs, not the preferred production source of truth.
- Do not write directly to source workbooks from production flows.
- Do not bypass CRM validation for lead, booking, passport, or handoff writes.
- Do not expose exact model configuration, API keys, prompts, or secrets through customer-facing chat.
- Do not commit local databases from `apps/api/instance/`.
- Use isolated test databases for destructive tests.

## Documentation Index

Good starting points:

- [docs/README.md](./docs/README.md)
- [docs/01_CURRENT_ARCHITECTURE.md](./docs/01_CURRENT_ARCHITECTURE.md)
- [docs/20_PRODUCTION_AGENT_ARCHITECTURE.md](./docs/20_PRODUCTION_AGENT_ARCHITECTURE.md)
- [docs/40_AGENT_WORKFLOW_POLICY.md](./docs/40_AGENT_WORKFLOW_POLICY.md)
- [docs/data/03_DATA_FLOW_CONTRACT.md](./docs/data/03_DATA_FLOW_CONTRACT.md)
- [docs/security/PROTECTED_ROUTE_MATRIX.md](./docs/security/PROTECTED_ROUTE_MATRIX.md)
- [docs/testing/TEST_STRATEGY.md](./docs/testing/TEST_STRATEGY.md)
- [docs/api/TESTSPRITE_API_TESTING.md](./docs/api/TESTSPRITE_API_TESTING.md)

## Development Notes

- Keep CRM business rules in backend services, not prompts.
- Prefer structured service/API calls over ad hoc string parsing.
- Preserve existing CRM logic when improving AI conversation behavior.
- Add focused tests for every workflow or API contract change.
- Keep the root README high-level; place deep implementation notes in `docs/`.

## Project Status

The project is an MVP/product foundation with active work in CRM operations, AI-agent workflow control, API testing, and future Instagram/Meta integration. It is suitable for local development and controlled demos. Production deployment should review auth, secrets, database migration strategy, webhook hardening, file storage, monitoring, and backup/restore workflows before live customer traffic.
