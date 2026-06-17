# Production Readiness Checklist

## Security

- [ ] Move all secrets to a secret manager or deployment environment.
- [ ] Remove hardcoded demo login password and use real operator authentication.
- [ ] Enforce Meta webhook signature validation on raw request bodies.
- [ ] Implement Meta page access token storage, rotation, and revocation handling.
- [ ] Add request rate limiting for public routes.
- [ ] Add role-based access control for admin workflows.

## Reliability

- [ ] Replace in-request external writes with durable queues.
- [ ] Add retry policies and dead-letter queues for sheet sync and Meta sends.
- [ ] Add retry queue observability for webhook, sync, and outbound-message workers.
- [ ] Add idempotency keys for webhooks, agent writes, and booking drafts.
- [ ] Add structured error handling for integrations.
- [ ] Add monitoring and error tracking.

## Data

- [ ] Migrate demo SQLite data to PostgreSQL.
- [ ] Define the production database migration plan from demo workbook/SQLite data to PostgreSQL.
- [ ] Decide whether the tracked SQLite database should be removed from git history.
- [ ] Review all migrations before production deploy.
- [ ] Add audit logs for identity merges, handoffs, booking changes, and outbound messages.
- [ ] Define backup and restore procedures.

## Product Operations

- [ ] Formalize human review ownership, SLA, and assignment workflow.
- [ ] Design the human handoff workflow for Instagram conversations, CRM records, and operator notes.
- [ ] Build admin tools for unresolved webhook events and failed outbound messages.
- [ ] Add clear operator-visible status for automation paused, handoff required, and retry pending.
- [ ] Add production seed/import procedure separate from demo workbooks.

## Testing

- [ ] Add middleware route/service tests.
- [ ] Add admin UI component and API-contract tests.
- [ ] Add webhook signature and event-shape tests.
- [ ] Add queue retry/idempotency tests.
- [ ] Add migration smoke tests against PostgreSQL.
