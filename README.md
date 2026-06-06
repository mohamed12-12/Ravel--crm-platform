# Rahma Travel OS

Rahma Travel OS is an AI-powered travel CRM, booking automation, sales intelligence, and traveler lifecycle platform for managing leads, itineraries, handoffs, and operational workflows across the customer journey.

## Features

- Traveler and lead management
- Sales intelligence and follow-up prioritization
- Booking automation and operational handoffs
- CRM workflows for lifecycle tracking
- Human-in-the-loop review for sensitive actions
- Multi-service architecture with web, middleware, and data-processing components

## Tech Stack

- Python 3 and Flask
- SQLAlchemy and Flask-Migrate
- Node.js and TypeScript
- React + Vite for the admin experience
- Prisma for database tooling in middleware services
- PostgreSQL-ready data modeling
- OpenAI / Gemini / Meta integration points
- Docker-ready release structure

## Architecture

The repository is organized as a lightweight monorepo:

- `rahma-traveler/` contains the Flask application, templates, routes, services, and data workflows.
- `v2-admin/` contains the Vite-based admin interface.
- `v2-middleware/` contains the TypeScript middleware layer and Prisma tooling.

The platform separates presentation, orchestration, and data-handling concerns so travel operations can scale without coupling business rules to a single surface.

See [ARCHITECTURE.md](./ARCHITECTURE.md) for a deeper breakdown.

## Business Workflow

1. Capture leads from inbound channels and sales channels.
2. Normalize traveler data and resolve duplicates.
3. Prioritize opportunities using operational and sales signals.
4. Automate booking and follow-up workflows where safe.
5. Escalate sensitive cases to a human operator.
6. Track the traveler lifecycle through conversion, booking, and service completion.

## Installation

### Prerequisites

- Node.js 18+
- Python 3.11+
- npm
- PostgreSQL if you want to run against a persistent database

### Setup

```bash
npm install
python -m pip install -r rahma-traveler/requirements.txt
```

## Environment Variables

Copy the example file and fill in real values locally:

```bash
cp rahma-traveler/.env.example rahma-traveler/.env
```

Common variables include:

- `SECRET_KEY`
- `DATABASE_URL`
- `EXCEL_FILE_PATH`
- `OPENAI_API_KEY`
- `GEMINI_API_KEY`
- `META_VERIFY_TOKEN`
- `META_PAGE_ACCESS_TOKEN`
- `META_APP_SECRET`

Never commit real secrets.

## Database Setup

The Flask app uses SQLAlchemy migrations.

```bash
cd rahma-traveler
flask db upgrade
```

If you are using the middleware Prisma layer:

```bash
cd v2-middleware
npx prisma generate
npx prisma migrate dev
```

## Testing

Run all available checks from the repository root:

```bash
npm test
```

Python tests are discovered from the `tests/` directory.

## Docker Usage

This repository is Docker-ready, but a production container image is not committed yet. The recommended pattern is:

- build the Python app into a container
- run the admin app as a separate build stage if needed
- connect both to the same backing database

## Roadmap

See [ROADMAP.md](./ROADMAP.md).

## Security Note

Secrets must live in local `.env` files or a secret manager. Do not commit API keys, database credentials, certificates, service-account JSON files, build output, or local cache directories.
