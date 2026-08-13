# Phase 9 Production Reliability Report

Date: 2026-08-13

## Scope

Phase 9 audited and fixed only the concrete production reliability blockers found in Phase 8:

1. Tool-calling sessions were process-memory only.
2. Same-session HTTP turns could run concurrently against stale mutable state.
3. Webhook duplicate protection was sequential only, with no database-level uniqueness.

No AI classifier categories, prompts, fuzzy matching, provider calls, workflow policy, ActionValidator behavior, or booking/business rules were changed.

## Production Changes

### Durable Agent Sessions

Added `ai_agent_sessions` as the durable session snapshot table. `ToolCallingSessionRuntime` now persists `SessionState` and `AgentState` snapshots when sessions are created and after each HTTP tool-calling turn.

Durable state contains explicit dataclass fields only. Runtime objects such as LLM providers, tool registries, executors, gateways, locks, and service objects are not serialized.

Session database URL resolution:

- `AI_AGENT_SESSION_DATABASE_URL`, if set.
- Else `DATABASE_URL`.
- Else the existing local SQLite operational DB path.

### Cross-Process Same-Session Locking

Added a database lease around `handle_message_by_id(session_id, text, gateway)`. The HTTP message route now processes tool-calling turns through this boundary.

Behavior:

- Loads the latest durable session at the start of the turn.
- Serializes same-session requests with a DB lock.
- Persists the updated session after the existing `handle_message()` logic completes.
- Returns HTTP `409 session_busy` if the lock cannot be acquired before the configured wait.

### Webhook Idempotency

Added database-level uniqueness for non-empty `(channel, message_key)` interaction rows.

Webhook behavior:

- Existing sequential dedupe still returns the existing interaction.
- Concurrent duplicate inserts now hit the unique index.
- `record_inbound_channel_event()` catches that unique collision and re-queries the existing row, returning `created=False`.
- Migrations refuse to add the index if duplicate historical message keys already exist, avoiding silent data deletion or mutation.

Local dev DB check on 2026-08-13 found no duplicate non-empty `(channel, message_key)` rows.

## Files Changed

Production files:

- `services/ai_agent/ai_agent_app/agent/session_store.py`
- `services/ai_agent/ai_agent_app/agent/tool_calling_runtime.py`
- `services/ai_agent/ai_agent_app/server.py`
- `apps/api/app/models/interaction.py`
- `services/crm/system_services/unified_service.py`
- `database/postgres/001_create_schema.sql`
- `database/migrations/versions/e7b2c4d9a1f0_add_interaction_message_key_unique_index.py`

Tests changed/added:

- `tests/test_phase9_production_reliability.py`
- `tests/test_phase39_workflow_policy.py`
- `tests/test_agent_identity_policy.py`
- `tests/test_phase11_demo_features.py`

## Reliability Findings

### Persistence and Recovery

Verified:

- Same session can recover after a fresh runtime instance.
- Latest durable state is loaded on the HTTP turn boundary.
- Agent state persists alongside session state.
- Passport attachment session mutations are persisted after upload handling.

Remaining debt:

- Session payloads are JSON snapshots, not event-sourced state. This is sufficient for the demonstrated blocker and avoids redesign.

### Concurrency

Verified:

- Two runtimes cannot process the same session concurrently while a lock is held.
- Two same-session HTTP messages are serialized and both resulting field captures persist.
- Version checks reject stale saves after a locked turn.

Remaining debt:

- Lock TTL defaults are environment-configurable but not integrated with a metrics/alerting system.

### Partial Mutation Recovery

Verified:

- If CRM booking write succeeds but durable session persistence fails, retrying the same confirmation re-enters from the last durable pre-write state.
- Existing write executor idempotency returns the previous booking instead of creating a duplicate.
- The final persisted state moves to `post_booking_support` with the correct booking id.

### Webhook Retry/Deduplication

Verified:

- Duplicate webhook message id returns `created=False`.
- Raw duplicate interaction insert fails with `sqlite3.IntegrityError`.
- PostgreSQL schema and Alembic migration now define the same uniqueness invariant.

### Configuration

Production still requires:

- `APP_ENV=production`
- `AI_AGENT_MODE=tool_calling`

New optional configuration:

- `AI_AGENT_SESSION_DATABASE_URL`: overrides the session store DB when session state should be separated from the CRM database.
- `AI_AGENT_SESSION_LOCK_TTL_SECONDS`: default `30`.
- `AI_AGENT_SESSION_LOCK_WAIT_SECONDS`: default `10`.

If `AI_AGENT_SESSION_DATABASE_URL` is omitted, production must provide a valid `DATABASE_URL`.

## Verification Results

Collection:

- `983 tests collected`

Required order:

- New Phase 9 tests: `5 passed`
- Golden transcript regressions: `190 passed`
- Phase 3A-8 target suites: `144 passed`, `1 pre-existing failure`
- Persistence/database/webhook sweep: `89 passed`
- Broader production-agent sweep: `165 passed`
- Full `pytest tests --durations=25`: timed out after 15 minutes before pytest summary

Additional split-suite evidence after full-suite timeout:

- Admin/identity/privacy/API/followup/migration/security-adjacent batch: `120 passed`
- Phase 0-2/10/11 batch: `109 passed`
- Phase 3-6/8/9/A/H batch: `104 passed`, `2 pre-existing/non-Phase-9 failures`
- Production/write-safety batch: `173 passed`
- Remaining general batch: `102 passed`, `1 pre-existing failure`

Known/pre-existing failures observed:

- `tests/test_tier2_field_validation.py::test_passport_country_mismatch_with_stated_nationality_does_not_block`
- `tests/test_phase6_passport_attachments.py::PassportAttachmentTests::test_payment_screenshot_upload_does_not_update_passport_fields`
- `tests/test_phase4_traveler_management.py::Phase4TravelerManagementTests::test_detail_shows_related_records_and_update_syncs_back`

Warnings:

- SQLAlchemy SQLite datetime adapter deprecation warnings.
- Eventlet deprecation warning.

## Recommendation

GO FOR PHASE 10.

The three Phase 9 production reliability blockers are resolved and covered by regression tests. The remaining failures are outside the Phase 9 reliability changes and were not fixed under this phase's constraints.

Stop after Phase 9. Do not automatically begin Phase 10.
