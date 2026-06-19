# Run Guide

## Required Tools

- Node.js 18+
- npm
- Python 3.11+ recommended; current QA run used Python 3.14
- SQLite for MVP local development
- PowerShell on Windows

## Setup

```bash
npm install
python -m pip install -r apps/api/requirements.txt
cp .env.example .env
```

Fill `.env` locally. Do not commit real secrets.

## Important Environment Variables

Core:

```text
APP_ENV=development
APP_HOST=127.0.0.1
APP_PORT=5001
APP_SECRET_KEY=
FLASK_CONFIG=development
SECRET_KEY=
DATABASE_URL=sqlite:///rahma_traveler_dev.db
```

Sheets:

```text
SHEET_BACKEND=excel
EXCEL_SOURCE_WORKBOOK=archive/source-artifacts/RT - Travelers Database.phase5.ready.xlsx
EXCEL_RUNTIME_WORKBOOK=archive/source-artifacts/RT - Travelers Database.phase5.demo.xlsx
GOOGLE_SHEET_ID=
GOOGLE_APPLICATION_CREDENTIALS=
```

Middleware:

```text
PORT=3000
FLASK_API_URL=http://localhost:5000/api/crm
```

Meta placeholders:

```text
META_VERIFY_TOKEN=
META_PAGE_ACCESS_TOKEN=
META_APP_SECRET=
META_GRAPH_API_VERSION=
PUBLIC_WEBHOOK_URL=
```

## Run Commands

AI-agent demo Flask app:

```bash
python -m services.ai_agent.ai_agent_app.server
```

Compatibility wrapper:

```bash
python demo_web/app.py
```

Database-backed Flask CRM:

```bash
cd apps/api
python run.py
```

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

All workspaces and Python compile:

```bash
npm run build
```

Frontend only:

```bash
cd apps/admin-web
npm run build
```

Middleware only:

```bash
cd apps/middleware
npm run build
```

## Test Commands

All configured tests:

```bash
npm test
```

Python tests:

```bash
python -m pytest tests
```

TypeScript checks:

```bash
npm run typecheck
```

Python compile:

```bash
python -m compileall .
```

Dependency audit:

```bash
npm audit --audit-level=high
```

## Current Known Results

- `npm run build`: passes.
- `npm run typecheck`: passes.
- `python -m compileall apps services scripts tests`: passes.
- `npm test`: passes.
- `python -m pytest tests`: 135 passed.
- `npm run lint`: unavailable; no lint script exists.
- `npm audit --audit-level=high`: fails with esbuild/vite high-severity findings.

## Common Issues

- If CRM SQLite files remain locked on Windows, dispose the SQLAlchemy engine before cleanup:

```python
db.session.remove()
db.engine.dispose()
```

- `python -m compileall .` is noisy because it walks ignored/generated folders. Prefer a narrower command in CI after a script is added.
- Admin and middleware test scripts are placeholders and do not validate real behavior.
- Real Instagram/Meta integration is not implemented yet; do not connect a production account.
- Build outputs are not committed. Re-run `npm run build` when needed.
