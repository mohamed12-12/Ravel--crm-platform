# Implementation Plan

Date: 2026-06-16
Scope: planning only. No implementation has been performed in this phase.

## Phase 1: Safe Changes

Goal: prepare configurable behavior without changing current defaults or business outcomes.

Recommended work:

- Confirm the requirement decisions listed in `CLIENT_REQUIREMENTS_CONFIRMATION.md`.
- Add explicit acceptance criteria for each approved requirement before code changes.
- Add tests that lock current behavior first, then update expected behavior only for approved changes.
- Introduce persona configuration behind current default copy, so existing responses remain unchanged until the client persona is enabled.
- Add website-link response only after the official URL and copy are confirmed.
- Standardize numbered prompt helpers for future use, initially preserving current prompt text unless the client approves updated copy.
- Add documentation for Traveler ID immutability and Trip ID change risks.
- Update `RUN_GUIDE.md`, `../00_status/FINAL_SUMMARY.md`, and `../06_testing/QA_REPORT.md` later to reconcile their pre-fix status with `../06_testing/FIX_REPORT.md`.

Exit criteria:

- No business behavior changes unless explicitly approved.
- `python -m pytest tests`, `npm test`, `npm run build`, `npm run typecheck`, and `python -m compileall apps services scripts tests` remain green.
- Requirements have signed-off acceptance criteria.

## Phase 2: Business Logic Changes

Goal: implement approved CRM and agent behavior changes that affect workflows.

Recommended work:

- Apply approved persona behavior to agent responses with language-specific tests.
- Convert approved multi-option prompts to numbered options and numeric parsing.
- Update the flights prompt/copy so it accurately reflects the client's business model that most trips are without flights.
- Implement post-trip human support only after confirming the "trip ended" rule and support-intent triggers.
- Implement VIP and group discount logic only after receiving exact formulas, approval rules, and audit requirements.
- Implement international-trip passport data collection after confirming required fields and privacy policy.
- Define Trip ID modification/regeneration behavior, including relationship updates, aliasing, and rollback.
- Extend CRM tests for lead, booking, handoff, discount, passport, and ID invariants.

Exit criteria:

- All changed behavior has regression tests.
- Discount and passport behavior has product/client approval.
- No existing Traveler IDs are mutated.
- Trip ID changes preserve booking/lead/trip referential integrity.

## Phase 3: Instagram/Meta Production Integration

Goal: connect the real customer channel only after core business contracts and security controls are ready.

Recommended work:

- Implement production Meta webhook verification using configured app/page values.
- Enforce `X-Hub-Signature-256` validation on raw request bodies with tests.
- Store Meta page access tokens in a secret manager, with rotation/revocation procedures.
- Add inbound event idempotency by Meta event/message ID.
- Add durable inbound and outbound queues with retry and dead-letter handling.
- Connect Instagram inbound messages to the approved agent workflow.
- Add outbound Meta Graph API send handling with rate-limit awareness.
- Route ambiguous, blocked, post-trip, or human-requested conversations into the CRM handoff queue.
- Persist message, attachment, handoff, and operator-response audit trails.
- Add monitoring for webhook failures, queue latency, outbound send failures, and handoff backlog.

Exit criteria:

- Staging Meta webhook receives and validates real test events.
- Signature, token, retry, idempotency, and rate-limit paths are tested.
- Human handoff is observable and reversible by operators.
- No live account is connected until staging evidence is accepted.

## Phase 4: Production Hardening

Goal: make the MVP operable and supportable for real customers.

Recommended work:

- Migrate from demo SQLite/workbook workflows to a reviewed PostgreSQL production plan.
- Add production audit logs for identity changes, bookings, discounts, messages, attachments, handoffs, and admin actions.
- Add authentication/authorization for admin, CRM, attachment, and handoff actions.
- Add application rate limiting for public/API/webhook endpoints.
- Add structured logging, error tracking, metrics, and alerting.
- Add real frontend and middleware tests; replace placeholder test scripts.
- Add lint/format/static analysis and CI checks.
- Resolve dependency audit findings through controlled upgrades.
- Add backup, retention, privacy, and incident-response procedures.
- Add attachment storage scanning, signed URLs, access controls, and deletion policy if attachments are approved.

Exit criteria:

- CI runs build, typecheck, pytest, frontend tests, middleware tests, lint, secret scan, and dependency audit.
- Production environment has secrets, monitoring, backups, and rollback procedures.
- Data privacy handling is documented and reviewed.

## Recommendation Summary

- Ready to implement now: Phase 1 documentation, acceptance criteria, test-contract preparation, and non-invasive configuration scaffolding that keeps current defaults.
- Needs client confirmation first: Phase 2 business behavior, especially persona content, discount formulas, passport fields, post-trip handoff rules, numbered-choice scope, website URL, and Trip ID policy.
- Must not implement yet: Phase 3 real Instagram/Meta production integration and live visa web lookup until security, compliance, source authority, monitoring, and handoff controls are confirmed.
