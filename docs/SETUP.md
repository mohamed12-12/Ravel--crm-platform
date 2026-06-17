# Setup

## Prerequisites

- Node.js 18+
- npm
- Python 3.11+
- SQLite for MVP local development
- PostgreSQL for production-oriented work

## Install

From the repository root:

```bash
npm install
python -m pip install -r apps/api/requirements.txt
```

## Environment

Copy the root example and fill real values only in local files:

```bash
cp .env.example .env
```

The database-backed Flask app also reads `apps/api/.env` if present. Do not commit either `.env` file.

## Run The Spreadsheet Demo

```bash
python -m services.ai_agent.ai_agent_app.server
```

Default URL: `http://127.0.0.1:5001`.

The legacy wrapper is still available:

```bash
python demo_web/app.py
```

## Run The CRM Flask App

```bash
cd apps/api
flask db upgrade
python run.py
```

## Run The Middleware

```bash
cd apps/middleware
npm run dev
```

Default URL: `http://localhost:3000`.

## Run The Admin UI

```bash
cd apps/admin-web
npm run dev
```

Default URL: `http://localhost:3001`.

## Checks

From the repository root:

```bash
npm test
npm run build
npm run typecheck
python -m pytest tests
python -m compileall .
```
