# Run Guide

For hosting this on a server (AWS EC2), see [`deploy/README.md`](deploy/README.md)
instead - this doc covers local development only.

## Required Tools

- Node.js 18+
- npm
- Python 3.11+ (a real virtualenv is recommended; local dev here has been
  running against a global interpreter, which is fine for a single machine
  but not what you want to reproduce on a server - see `deploy/README.md`)
- SQLite (bundled with Python) for local development
- PostgreSQL client tools (`psql`) if you're working with the staging/production database
- PowerShell on Windows

## Setup

```bash
npm install
python -m pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env` locally. Do not commit real secrets - `.env` is gitignored.

## Services and Ports

| Service | Default port | Env var that controls it | Run from |
| --- | --- | --- | --- |
| CRM/API (Flask, `apps/api`) | 5000 | `CRM_API_PORT` | `apps/api` |
| AI agent (Flask, `services/ai_agent`) | 3001 | `APP_PORT` | repo root |
| Middleware (Express, `apps/middleware`) | 3000 | `PORT` | `apps/middleware` |
| Admin web (Vite/React, `apps/admin-web`) | 5173 (Vite default) | n/a (`vite --port` to change) | `apps/admin-web` |

`CRM_API_PORT` and `PORT` are deliberately separate variables even though
both services default to a nearby port - `apps/api/run.py` and
`apps/middleware/src/index.ts` used to both read the same generic `PORT`
var from this shared `.env`, which meant starting both from the same file
made them fight over the same port. `CRM_API_PORT` gives the CRM its own
setting; `PORT` still works as a fallback for the CRM if you're only ever
running it standalone.

## Database: SQLite vs PostgreSQL

Two valid local setups, controlled entirely by `.env`:

**SQLite (default for a fresh clone)** - `.env.example` ships with
`DATABASE_URL=sqlite:///rahma_traveler_dev.db` and `CRM_ACCESS_MODE=shared_service`.
Nothing else to configure; the CRM and agent both work directly against the
local SQLite file.

**PostgreSQL** - once you've migrated data (see
`database/postgres/SQLITE_TO_POSTGRES_MIGRATION_PLAN.md` and
`POSTGRES_STAGING_AGENT_PATH_SMOKE_REPORT.md` for how that was done here),
set:

```env
DATABASE_URL=<same value as POSTGRES_URL>
CRM_ACCESS_MODE=api
CRM_API_BASE_URL=http://127.0.0.1:5000
CRM_API_TOKEN=<a real generated token, shared between both services>
```

`CRM_ACCESS_MODE` matters here, not just `DATABASE_URL`: the agent's
`shared_service` mode (`services/crm/system_services/unified_service.py`)
opens SQLite directly and never reads `DATABASE_URL` at all. Leaving
`shared_service` set while pointing `DATABASE_URL` at Postgres gives you a
split-brain setup - the CRM app on Postgres, the agent silently still on
SQLite. `api` mode routes the agent through the CRM's HTTP bridge, which is
the code path that's actually Postgres-aware, and is also what production
requires.

## Run Commands

CRM/API (start this first if you're using `CRM_ACCESS_MODE=api`):

```bash
cd apps/api
python run.py
```

Opens on `http://127.0.0.1:5000` (or `CRM_API_PORT`/`PORT`).

AI agent:

```bash
python -m services.ai_agent.ai_agent_app.server
# or the compatibility wrapper:
python demo_web/app.py
```

Opens on `http://127.0.0.1:3001` (or `APP_PORT`). Note:
`demo_web/app.py` prefers a gitignored `archive/legacy_demo_web` package if
one happens to exist on your machine, falling back to
`services.ai_agent.ai_agent_app.server` otherwise - if you ever see
unexpected behavior only in `demo_web/app.py` and not the module form above,
that's why. Production uses `services/ai_agent/wsgi.py`, which always
imports the real module directly.

Middleware:

```bash
cd apps/middleware
npm run dev
```

Admin web:

```bash
cd apps/admin-web
npm run dev
```

## Build Commands

```bash
npm run build --workspaces --if-present   # admin-web + middleware
python -m compileall apps services scripts tests
```

## Test Commands

```bash
npm run typecheck --workspaces --if-present
python -m pytest tests -q
```

Deployment-critical subset (faster, what to run before every deploy):

```bash
python -m pytest tests/test_deployment_production_validation.py tests/test_port1_write_safety_idempotency.py \
    tests/test_port2_write_result_response_gating.py tests/test_port3_response_guard.py \
    tests/test_postgres_agent_bridge.py tests/test_instagram_webhook.py tests/test_data_authority.py -q
```

`api`-access-mode coverage (Phase 4 / test-production parity): running the
*entire* suite with `CRM_ACCESS_MODE=api` forced was tried and is **not**
currently a clean, ready-to-wire-into-CI command — several existing tests
construct a `ToolCallingSessionRuntime`/`ReadOnlyCRMTools` directly without
a `crm_api_base_url`, which is a hard requirement in `api` mode
(`CRMApiClient.__init__` raises `ValueError` without one), so they fail for
a fixture-configuration reason unrelated to any real backend divergence.
Making that command safe means auditing every such fixture across the
suite to either set `crm_api_base_url`/a working local server or an
explicit mode override — a real, separately-scoped follow-up, not done
here. Until then, use the targeted tests below instead, which exercise the
actual mechanism that broke for guardian consent without needing that
fixture-wide rework:

```bash
python -m pytest tests/test_dual_access_mode_dispatch.py \
    tests/test_write_tool_executor_verification.py::TestGuardianConsentDispatchByMode \
    tests/test_postgres_agent_bridge.py tests/test_dual_backend_write_parity.py -q
```

Production-path smoke test (Task B.3 — scripted conversation walk against
the real `PostgresAgentBridgeService` code path, on a disposable database,
never real production data):

```bash
python tools/production_parity_smoke_test.py
```

PostgreSQL migration (already run once against staging - see
`POSTGRES_STAGING_AGENT_PATH_SMOKE_REPORT.md` - safe to re-run, inserts use
`ON CONFLICT DO NOTHING`):

```bash
python tools/migrate_sqlite_to_postgres.py --dry-run
python tools/migrate_sqlite_to_postgres.py --postgres-url "$POSTGRES_URL"
python tools/validate_postgres_migration.py --postgres-url "$POSTGRES_URL"
```

Read `database/postgres/SQLITE_TO_POSTGRES_MIGRATION_PLAN.md`,
`DEVOPS_POSTGRES_SETUP_INSTRUCTIONS.md`, and `POSTGRES_MIGRATION_ROLLBACK_PLAN.md`
before running this against anything other than a disposable/staging target.

## Deploying / Hosting

Not covered here - see [`deploy/README.md`](deploy/README.md) for a full
EC2 (systemd + gunicorn + nginx) walkthrough, and `.env.production.example`
for the production environment template.

## Common Issues

- **`RuntimeError: The Werkzeug web server is not designed to run in
  production`** when running `apps/api/run.py`: this was a real bug (fixed)
  - `socketio.run()` needs `allow_unsafe_werkzeug=True` unless
  `FLASK_DEBUG=true`. If you see this again after a merge, check that fix
  is still in place.
- **CRM and middleware fighting over the same port**: see `CRM_API_PORT`
  above - make sure you're on a version of `.env`/`.env.example` that has it.
- **`Property 'env' does not exist on type 'ImportMeta'` in `apps/admin-web`
  typecheck**: fixed by adding `apps/admin-web/src/vite-env.d.ts`
  (`/// <reference types="vite/client" />`). If it reappears, that file was
  probably deleted.
- If CRM SQLite files remain locked on Windows, dispose the SQLAlchemy
  engine before cleanup:

  ```python
  db.session.remove()
  db.engine.dispose()
  ```

- `python -m compileall .` (no path filter) is noisy because it walks
  `node_modules` and temp folders - use the narrower command above.
- Admin-web and middleware `npm test` scripts are placeholders (no real
  tests exist yet for either).
- `npm audit --audit-level=high` currently reports esbuild/vite
  high-severity findings - review before treating `npm audit` as a hard gate.
