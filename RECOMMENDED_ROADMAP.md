# Recommended Roadmap

Date: 2026-06-17

This roadmap assumes the current baseline remains a demo/MVP until the critical data, security, and workflow issues are resolved.

## Phase 1: Stabilization

Goal: make the current demo baseline reliable and honest.

Deliverables:

- Freeze current behavior as a known demo baseline.
- Confirm which app is client-facing: Flask CRM, demo agent, React admin, or all.
- Fix encoding/mojibake issues in visible templates.
- Add environment/run documentation for each app.
- Remove or label prototype-only screens.
- Add smoke-test checklist for demo flows.
- Ensure `sync_queue` page works for expected failures.
- Document current limitations in client-facing demo notes.

Exit criteria:

- Team can run demo without guessing.
- No stakeholder mistakes demo capability for production readiness.
- Existing tests pass.

## Phase 2: CRM Logic Fixes

Goal: make CRM core concepts safer and closer to real sales operations.

Deliverables:

- Decide DB source of truth.
- Move runtime schema changes into migrations.
- Normalize lead/opportunity/task design.
- Add lead owner and next action.
- Add controlled enums for lead stage, handoff reason, booking status, payment status, traveler status/tier/risk.
- Add merge audit for duplicate travelers.
- Add admin action audit log.
- Add proper authentication/RBAC/CSRF plan and implementation.

Exit criteria:

- CRM can support multi-user internal use without corrupting core data.
- Identity resolution and merge are auditable.
- Lead lifecycle no longer overwrites distinct opportunities unexpectedly.

## Phase 3: Agent Improvements

Goal: turn demo state machine into a production-ready conversation workflow.

Deliverables:

- Persist sessions in DB or Redis.
- Add explicit state transition log.
- Add consent and identity verification states.
- Fix `awaiting_country_code` branch behavior.
- Validate flight and currency replies instead of defaulting.
- Add waitlist/follow-up flow.
- Add human takeover state and automation lock.
- Add transcript storage.
- Add configurable agent settings in CRM.
- Add stronger Arabic/English copy management.

Exit criteria:

- Agent can resume after restart.
- Handoff stops automation.
- Every state transition is auditable.
- Invalid replies are handled safely.

## Phase 4: Trip/Booking Improvements

Goal: make booking and inventory operationally trustworthy.

Deliverables:

- Add booking hold model with expiration.
- Add inventory transaction ledger.
- Add cancellation release logic.
- Add structured pricing/currency/deposit fields.
- Add payment transaction/proof model.
- Add booking confirmation workflow.
- Add passport/document requirement workflow for international trips.
- Add secure attachment/document model.
- Add rooming/passenger details.
- Add operations handoff checklist.

Exit criteria:

- A confirmed booking means payment and inventory are properly reconciled.
- Draft holds cannot silently block inventory forever.
- Passport and attachment collection is secure and traceable.

## Phase 5: Client Acceptance

Goal: validate the product with Rahma Travel employees before external launch.

Deliverables:

- Define acceptance scenarios:
  - New traveler
  - Returning traveler
  - Duplicate phone
  - Blacklist/protected traveler
  - Local booking
  - International booking with passport
  - Handoff
  - Payment confirmation
  - Cancellation
- Create seeded acceptance dataset.
- Train staff on CRM workflows.
- Run UAT sessions.
- Capture defects and process gaps.
- Sign off demo vs production scope separately.

Exit criteria:

- Client agrees on workflow behavior.
- No P0/P1 blockers remain for chosen launch scope.
- Staff can operate the CRM without developer intervention.

## Phase 6: Instagram Integration

Goal: connect real Meta/Instagram messaging safely.

Deliverables:

- Meta app/page setup.
- Webhook signature validation.
- Inbound event validation and idempotency.
- Message queue and retry worker.
- Outbound messaging API.
- Conversation identity mapping.
- Rate limiting and abuse protection.
- Human inbox/takeover integration.
- Monitoring and alerting.
- Message transcript storage.
- Privacy and retention controls.

Exit criteria:

- Real Instagram messages can be received, processed, handed off, and replied to safely.
- Duplicate webhook events do not duplicate leads/bookings.
- Human takeover is reliable.

## Phase 7: Production Hardening

Goal: make the system safe to operate with real customer data.

Deliverables:

- Move from SQLite to managed PostgreSQL or approved production DB.
- Centralize persistence layer.
- Add background job system.
- Add object storage for documents.
- Add encryption and secret management.
- Add structured logging, metrics, error tracking.
- Add backup/restore plan.
- Add deployment pipeline.
- Add load/concurrency testing.
- Add security review.
- Add data retention and deletion workflows.
- Add incident response runbooks.

Exit criteria:

- System can be monitored, backed up, restored, audited, and safely maintained.
- Security/compliance posture is acceptable for traveler and passport data.

