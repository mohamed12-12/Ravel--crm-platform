# PostgreSQL Staging Agent-Path Smoke Report

Date: 2026-08-03

## Scope

This smoke test exercised the **agent path** specifically: the `/api/crm/agent/read` and
`/api/crm/agent/write` endpoints in `apps/api/app/routes/crm.py`, backed by
`PostgresAgentCRMTools` / `PostgresAgentBridgeService`
(`apps/api/app/services/agent_crm_bridge.py`), against a **staging PostgreSQL** database.

- Database: staging RDS instance, schema **`ravel`** only. This RDS instance is shared with an
  unrelated application (a separate, differently-named workload on the same server); every
  connection used in this smoke test explicitly ran `SET search_path TO ravel` (or connected via
  a DSN with `options=-csearch_path=ravel`) before issuing any query, and no query ever referenced
  another schema. See **Remaining Blockers** below re: sharing this instance long-term.
- Temporary local environment overrides only. `.env`'s default `DATABASE_URL` (SQLite) was never
  changed; `POSTGRES_URL` was added to `.env` by the user ahead of this run and was only read, never
  printed or modified by this process.
- Safe flags held for the entire run: `AGENT_TOOL_ROUTER_MODE=dry_run`,
  `AGENT_WRITE_TOOL_ENFORCEMENT=true`. The global router was never switched to `enforce`.
- SQLite (`apps/api/instance/rahma_traveler_dev.db`) was never opened for writes by this test.

## Env Vars Used (names only)

- `DATABASE_URL` (set to the staging `POSTGRES_URL` value, subprocess-scoped only)
- `POSTGRES_URL`
- `APP_ENV`, `FLASK_CONFIG`
- `CRM_AUTH_ENABLED`, `ADMIN_USERNAME`, `ADMIN_PASSWORD` (ephemeral, generated per run)
- `CRM_API_TOKEN` (ephemeral, generated per run)
- `PORT`, `APP_HOST`, `APP_PORT`
- `AI_AGENT_MODE`, `CRM_ACCESS_MODE`, `CRM_API_BASE_URL`
- `AGENT_TOOL_ROUTER_MODE`, `AGENT_WRITE_TOOL_ENFORCEMENT`
- `APP_PASSWORD` (ephemeral, generated per run)
- `DEMO_RESET_ON_START` (`false`)

## Services Started

- CRM/API service (Flask) on an ephemeral local port, `DATABASE_URL` pointed at staging Postgres
  (`ravel` schema).
- AI agent service (`demo_web/app.py` → `services/ai_agent`) on an ephemeral local port, in
  `tool_calling` mode with `CRM_ACCESS_MODE=api`, `AGENT_TOOL_ROUTER_MODE=dry_run`,
  `AGENT_WRITE_TOOL_ENFORCEMENT=true`.
- Both services were stopped at the end of the run; no orphaned processes remained.

## Scenario Results

| Scenario | Result | Notes |
| --- | --- | --- |
| CRM login | PASS | Browser session established against staging-backed CRM. |
| CRM connected to staging PostgreSQL | PASS | `/admin/db-health` reported a `postgresql+psycopg2://` SQLAlchemy URI. |
| CRM dashboard loads | PASS | Dashboard rendered 200 OK from staging PostgreSQL data. |
| AI agent service starts with safe flags | PASS | Agent `/api/health` responded `ok` with `AGENT_TOOL_ROUTER_MODE=dry_run`. |
| Direct booking bypass remains blocked | PASS | Unconfirmed `/api/v1/bookings/draft` request returned 409 `blocked` before any write (regression guard). |
| Returning traveler lookup through agent path | PASS | `agent/read` `find_traveler_by_phone` resolved a real staging traveler (`TR00001`). |
| Trip search through agent path | PASS | `agent/read` `search_trips` returned the sampled staging trip. |
| Trip details through agent path | PASS | `agent/read` `get_trip_details` returned the expected trip record. |
| Lead create/reuse through agent path | PASS | First run in this session created lead `LD00001` via `agent/write` `create_lead`; this run's repeat calls were correctly rejected as duplicates referencing the same `LD00001`, with **zero** new `leads` rows. |
| Booking draft confirmation through agent path | PASS | Booking `BK000001` was created via `agent/write` `create_booking_draft` (`booking_confirmed: true`) in an earlier run this session; write bridge and idempotency verified end-to-end on Postgres. |
| Repeated booking confirmation does not duplicate | PASS | Repeated `create_booking_draft` calls (same and across process restarts) consistently return the same `booking_id` with contract status `duplicate`/`reused`, **zero** new `trip_bookings` rows. |
| Handoff create/reuse through agent path | PASS | First call created handoff `H-00037950`; the immediate repeat call returned `deduplicated: true` for the same `handoff_id`, zero new `handoff_queue` rows. |
| Booking status read through agent path | PASS | `agent/read` `get_booking_status` returned the booking created above. |

All 13 scenarios passed, including the required agent-path lead/booking/handoff create+reuse and
booking-status-read scenarios. Create-vs-reuse semantics were verified **both within a single run
and across separate process restarts in this session** (lead/booking/handoff created in an earlier
run were correctly recognized and deduplicated in the final run), which is a stronger signal than
in-process-only idempotency.

## Write Verification (final run)

| Table | Before | After | Delta |
| --- | --- | --- | --- |
| `ravel.travelers` | 540 | 540 | 0 |
| `ravel.trips` | 47 | 47 | 0 |
| `ravel.leads` | 1 | 1 | 0 (already created by an earlier run this session) |
| `ravel.trip_bookings` | 50 | 50 | 0 (already created by an earlier run this session) |
| `ravel.handoff_queue` | 6 | 6 | 0 (already created by an earlier run this session) |
| `ravel.user_audit_log` | 9 | 10 | +1 (CRM employee login audit row) |

Across the full session (first create run + this reuse-verification run), exactly **one** new lead,
**one** new booking, and **one** new handoff were created in `ravel` — no duplicates at any point.
Every connection used in this test set `search_path` to `ravel` only; no other schema was queried
or written.

## SQLite Safety

- SQLite file: `apps/api/instance/rahma_traveler_dev.db`
- Size before/after: `528384` → `528384` bytes (unchanged)
- SHA-256 before/after: identical (`48e8e194907edd59132a140e88ed645c601722d74c6eb85da1f09f08f8500e`)
- **SQLite stayed byte-for-byte unchanged.**

## Tests Run

```
pytest tests/test_postgres_agent_bridge.py tests/test_data_authority.py \
       tests/test_port1_write_safety_idempotency.py \
       tests/test_port2_write_result_response_gating.py \
       tests/test_port3_response_guard.py -q
```

Result: **45 passed**, 0 failed. These suites are SQLite-fixture-isolated (they simulate the
Postgres code path by overriding `SQLALCHEMY_DATABASE_URI` on an otherwise-SQLite-backed test app,
per `tests/test_postgres_agent_bridge.py`), so they did not touch the staging database; they were
run to confirm the write-safety/idempotency/response-guard contracts that the live staging run
above also exercised end-to-end.

## Acceptance Checklist

- [x] Agent path works against PostgreSQL staging (`/api/crm/agent/read` and `/api/crm/agent/write`
      both routed to `PostgresAgentCRMTools`/`PostgresAgentBridgeService`).
- [x] Dashboard/app still works (CRM login, dashboard, and `/admin/db-health` all functioned against
      staging Postgres).
- [x] SQLite unchanged (byte-for-byte, confirmed via SHA-256).
- [x] No duplicate booking (verified in-run and across process restarts).
- [x] No unsafe write (`AGENT_TOOL_ROUTER_MODE=dry_run`, `AGENT_WRITE_TOOL_ENFORCEMENT=true` held
      throughout; direct-booking-bypass regression guard still blocks unconfirmed writes).
- [x] No internal leaks (no secrets, connection strings, or tokens were printed or committed; all
      credentials used were ephemeral and generated per run).

## Remaining Blockers Before Production Cutover

- **Shared RDS instance**: the staging Postgres server used here also hosts an unrelated
  application/workload under a different schema. This smoke test scoped every operation to `ravel`
  via `search_path`, but running a shared instance long-term carries operational risk (noisy
  neighbor, accidental cross-schema access by future scripts, blast radius of credential leakage).
  Recommend a dedicated staging instance (or at minimum a dedicated, least-privilege role scoped to
  `ravel` only) before treating this as the permanent staging environment.
- **No repo-tracked staging bootstrap**: there is no `docker-compose`/local Postgres harness and no
  committed `.env.staging`; the staging `POSTGRES_URL` currently only exists in one engineer's local
  `.env`. This should move to a proper secrets store before more people need to run this smoke test.
- **Duplicate-detection responses surface as `decision: REJECTED`**: for `create_lead` in
  particular, the repeated-call response has `result_id: ""` at the top level (the existing lead ID
  only appears inside `validation.warnings` text, e.g. `"Existing lead IDs: LD00001"`) rather than a
  structured `write_result_contract` the way `create_booking_draft`'s duplicate path does. This is
  functionally safe (no duplicate row is created) but is an inconsistent contract across write
  actions that downstream/UI consumers should be aware of.
- **No CI job runs this smoke test automatically**: this was a manual, human-triggered run. Consider
  wiring a scheduled or pre-cutover CI job that runs this scenario set against staging routinely.
- **Ephemeral per-run credentials**: `ADMIN_USERNAME`/`ADMIN_PASSWORD`/`CRM_API_TOKEN` were
  generated fresh for this run and discarded; there is no persisted staging service-account story
  yet for routine (non-smoke-test) agent-path usage.
- Do not switch production or the default local config — this run left `.env`'s default
  `DATABASE_URL` (SQLite) untouched.
