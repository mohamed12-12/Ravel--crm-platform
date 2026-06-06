# Architecture

Rahma Travel OS is structured as a modular travel-operations platform.

## Core Layers

- Presentation: `v2-admin/` for the operator interface
- Business logic: `rahma-traveler/app/` for Flask routes, services, and models
- Middleware: `v2-middleware/` for deterministic orchestration and integration adapters
- Data and migrations: `rahma-traveler/migrations/` and `v2-middleware/prisma/`

## Design Principles

- Preserve deterministic business rules for critical workflow steps
- Keep sensitive external calls behind explicit service boundaries
- Prefer readable, auditable flows over deeply implicit abstractions
- Separate traveler lifecycle state from channel-specific behavior

## Workflow Shape

1. Data enters through web routes, imports, or middleware services.
2. The system normalizes the record and applies safety checks.
3. The workflow either automates the next step or queues it for human review.
4. Operational outputs are reflected in the CRM and admin surface.

## Release Considerations

- Keep environment variables out of version control
- Keep generated assets out of commits
- Run tests before publishing
- Ensure database migrations are reviewed before deploys
