# Production Readiness & Instagram Webhook Deploy Report

Date: 2026-08-03

## Addendum: Postgres cutover + additional bugs found (same day, later pass)

After this report, `.env` was switched to use PostgreSQL (the already-migrated
`ravel` schema) as the local default instead of SQLite - `DATABASE_URL` now
matches `POSTGRES_URL`, and `CRM_ACCESS_MODE` was changed from
`shared_service` to `api` so the AI agent talks to the CRM over HTTP (its
`shared_service` mode opens SQLite directly via
`services/crm/system_services/unified_service.py` and never reads
`DATABASE_URL` at all - leaving it set would have silently kept the agent on
SQLite while the CRM ran on Postgres). Verified end-to-end with both real
service entrypoints (not the smoke-test harness): CRM dashboard,
`/admin/db-health`, and the agent's `/api/crm/agent/read` bridge all
correctly report live `ravel` data. SQLite file confirmed byte-identical
before/after.

Four more real, pre-existing bugs were found and fixed while verifying that:

1. **`apps/api/run.py` crashed on every plain `python run.py`** (any
   database backend) - Flask-SocketIO refuses its dev server without
   `allow_unsafe_werkzeug=True`, which was never passed. Fixed.
2. **`/admin/db-health` always read SQLite directly**
   (`services/crm/system_services/config.py`), regardless of which database
   was actually configured - so it would have kept reporting stale SQLite
   counts even with Postgres correctly wired up everywhere else. Fixed in
   `apps/api/app/routes/admin.py` to query the live backend.
3. **`apps/api` and `apps/middleware` both read the same generic `PORT`
   env var** from the shared root `.env`, so running both from that file
   made them fight over the same port. Added a dedicated `CRM_API_PORT` for
   the CRM/API app (`apps/api/run.py`, falls back to `PORT` for
   compatibility); `apps/middleware` keeps using `PORT` as before.
4. **`apps/admin-web` TypeScript build was broken**
   (`Property 'env' does not exist on type 'ImportMeta'` in `src/api.ts`,
   which uses `import.meta.env`) - missing `vite-env.d.ts`. Added
   `apps/admin-web/src/vite-env.d.ts`. `npm run typecheck` and
   `npm run build` both now pass cleanly across all workspaces.

`RUN_GUIDE.md` was rewritten to reflect current, accurate local-run
instructions (it previously had stale claims, e.g. "Instagram integration
not implemented" and a stale test count) and points to `deploy/README.md`
for hosting. No changes were needed in `deploy/README.md`/the systemd
units - they already hardcode explicit ports per service via gunicorn's
`--bind`, so the `PORT` collision never affected them.

## Scope

This pass covered three things, scoped per explicit decisions made before starting:

1. Fix concrete production-deploy blockers in the code/config (not a full
   Docker/K8s rebuild - target is a single AWS EC2 instance running both
   Flask services under gunicorn + systemd + nginx).
2. Close all 3 flagged gaps in the existing Instagram/Meta webhook
   implementation (`services/instagram/`).
3. Produce a `.env` set ready for production, and a cleanup candidate list
   (not yet deleted - awaiting a separate go-ahead).

No production system was touched. No secrets were printed, committed, or
invented - every credential placeholder in the new `.env.production.example`
is empty and marked `# SECRET`.

## What Changed

### Deploy-blocking fixes

- **`.github/workflows/ci.yml`**: fixed `python -m pip install -r
  rahma-traveler/requirements.txt` (path never existed - CI's install step
  was silently broken) to point at the new root `requirements.txt`.
- **`requirements.txt`** (new, repo root): consolidated, accurate dependency
  list derived from actual imports across `apps/api` and `services/ai_agent`
  (previous `apps/api/requirements.txt` was missing `google-generativeai`,
  `gspread`, `google-auth`, `google-api-python-client`, `gunicorn`, and
  listed `phonenumbers` despite it not being used anywhere).
- **`apps/api/requirements.txt`**: fixed a real driver bug -
  `psycopg[binary]>=3.2.0` (psycopg v3) was declared, but the app connects
  via SQLAlchemy's `postgresql+psycopg2://` dialect, which needs `psycopg2`,
  not `psycopg`. A fresh install from this file would have failed to
  connect to Postgres at all. Changed to `psycopg2-binary>=2.9.9`.
- **`.env.example`** (root): added 9 vars that were read via `os.getenv` in
  code but never documented (drift): `RAHMA_SYSTEM_DB_PATH`,
  `AI_AGENT_SYSTEM_PROMPT`, `AGENT_TOOL_ROUTER_MODE`,
  `AGENT_WRITE_TOOL_ENFORCEMENT` (required `true` in production - was
  undocumented entirely), `AGENT_PERSONA_NAME`, `WEBSITE_URL`,
  `POST_TRIP_HANDOFF_ENABLED/KEYWORDS/RESPONSIBLE_EMPLOYEE`,
  `TRAVELER_UPLOAD_ROOT`, `AI_AGENT_UPLOAD_ROOT`, `META_PAGE_ID` (new, see
  below).
- **`.env.production.example`** (new, repo root): every variable set to the
  value production `validate()`/`validate_config()` checks actually require
  (`apps/api/app/config.py`, `services/ai_agent/ai_agent_app/config.py`,
  `services/data_authority.py`), cross-checked against
  `docs/deployment/DEPLOYMENT_ENV_CHECKLIST.md` so it doesn't contradict
  that existing doc. Every secret is blank with a generation command or
  source noted in a comment.
- **`deploy/`** (new directory): `systemd/rahma-crm-api.service`,
  `systemd/rahma-ai-agent.service`, `nginx/rahma-traveler.conf`, `README.md`
  - full EC2 install-to-running walkthrough. See "Gunicorn note" below for
    why the CRM/API unit is pinned to 1 eventlet worker.
- **`services/ai_agent/wsgi.py`** (new): production WSGI entrypoint for the
  agent service. `demo_web/app.py` (the existing entrypoint) only builds its
  Flask `app` inside `if __name__ == "__main__"`, and prefers importing
  `create_app` from a gitignored `archive/legacy_demo_web` package if it
  happens to exist on disk - not safe to depend on for what code actually
  runs in production. The new `wsgi.py` imports directly from
  `services.ai_agent.ai_agent_app.server`, always. `apps/api/run.py` already
  worked as a gunicorn target as-is (`run:app`) - no new file needed there.

**Gunicorn note**: the CRM/API service uses Flask-SocketIO for live handoff
notifications (`apps/api/app/extensions.py`), which needs an eventlet/gevent
worker under gunicorn (plain sync workers can't serve WebSocket upgrades
correctly). Added `eventlet` to both requirements files and set the systemd
unit to `--worker-class eventlet --workers 1`. Single worker is a real
constraint: Socket.IO state is per-process, so a second worker wouldn't see
notifications emitted by the first. Scaling past 1 worker needs a Redis
`message_queue` - documented as a follow-up in the systemd unit's comments,
not implemented (would be scope creep for a first EC2 deploy).

### Instagram/Meta webhook - all 3 flagged gaps closed

The implementation in `services/instagram/` (verify handshake, HMAC
signature check, payload parsing, outbound Graph API sender) was already
real and working before this pass. Closed:

1. **No tests against realistic Meta payloads** → `tests/test_instagram_webhook.py`
   (new, 16 tests, all passing): valid/missing/malformed/wrong/tampered
   signature handling, unconfigured-secret handling, verify handshake
   (success/wrong-token/missing-params), page-id filtering, and payload
   parsing against a realistic Instagram Messaging webhook payload shape
   (verified against Meta's documented `entry[].messaging[]` format).
2. **No persistence of rejected/invalid signature attempts** →
   `services/instagram/webhooks.py` now logs every rejection (missing
   signature, malformed format, mismatch, verify-token mismatch, wrong page
   id) as a structured `webhook_rejected reason=... remote_addr=... path=...`
   warning line. There's no dedicated audit table for webhook traffic (would
   be a bigger, separate feature), so this is the durable record - point
   your CloudWatch Logs alarm (or equivalent) at the `webhook_rejected`
   marker.
3. **Page-access-token handling incomplete** → added `META_PAGE_ID` (new env
   var, `services/ai_agent/ai_agent_app/config.py`) and
   `filter_entries_for_page()` (`services/instagram/webhooks.py`), wired
   into the `/webhook` POST handler in `server.py`. Inbound webhook entries
   whose `entry.id` doesn't match the configured page are dropped and
   logged rather than processed - defense in depth in case the app is ever
   subscribed to more than one page, or a misdelivered callback arrives.
   Production `validate()` now requires `META_PAGE_ID` once Meta credentials
   are configured.

**Bug found and fixed along the way**: `services/instagram/webhooks.py`
imported `webhook_logger` from `services.ai_agent.ai_agent_app.logger` at
module load time. That package's `__init__.py` imports the entire
`server.py` - so anything importing `services.instagram.webhooks` *first*
(as the new test module does) hit a circular import and crashed. Made the
logger import lazy (`_webhook_logger()`, resolved on first use) to fix it.
This was a latent fragility in the existing code, not something introduced
today, but it's a real bug: the same crash could have hit any future script
or worker process that imports the webhook module before the ai_agent
package is fully initialized.

### File cleanup - proposed, not yet deleted

Per your answer to "show me the list first": here are the 9 root-level
report files that look superseded and were never git-committed (so deleting
them is permanent, no history to recover from). None of them are referenced
by any other currently-kept doc (checked via grep):

| File | Why it looks superseded |
| --- | --- |
| `STAGING_POSTGRES_CONNECTION_AND_DRY_RUN_REPORT.md` | Early step in the Postgres migration chain; superseded by `POSTGRES_STAGING_AGENT_PATH_SMOKE_REPORT.md` + `POSTGRES_PRODUCTION_CUTOVER_PLAN.md`. |
| `STAGING_POSTGRES_ACTUAL_MIGRATION_REPORT.md` | Same chain, intermediate step. |
| `STAGING_POSTGRES_REPAIRED_MIGRATION_REPORT.md` | Same chain, intermediate step. |
| `POSTGRES_STAGING_APP_SMOKE_REPORT.md` | Predates the Postgres agent-bridge fix; explicitly flags the agent path as BLOCKED, which is no longer true - superseded by `POSTGRES_STAGING_AGENT_PATH_SMOKE_REPORT.md`. |
| `POSTGRES_AGENT_BRIDGE_FIX_REPORT.md` | Describes the bridge fix that's now verified end-to-end in the newer smoke report. |
| `SQLITE_DATA_INTEGRITY_AUDIT_REPORT.md` | Input to the Postgres migration planning; migration is past that stage now. |
| `SQLITE_TO_POSTGRES_DATA_REPAIR_PLAN.md` | Same - planning doc for a step already executed. |
| `TOOL_INVENTORY_AND_CONTRACTS.md` | Unrelated one-off QA artifact from earlier the same day; no other doc references it. |
| `TOOL_HALLUCINATION_COMPLETION_REPORT.md` | Same - standalone QA artifact, nothing currently depends on it. |

**I have not deleted these.** Tell me "delete them", "archive them instead"
(move to a local gitignored folder), or point at specific ones to keep and
I'll action it.

## Test Results

New tests, all passing:

```
pytest tests/test_instagram_webhook.py -q
16 passed
```

Previously-verified suite (Postgres bridge, data authority, write-safety/
idempotency, response guard) - re-ran after today's changes, still clean:

```
pytest tests/test_postgres_agent_bridge.py tests/test_data_authority.py \
       tests/test_port1_write_safety_idempotency.py \
       tests/test_port2_write_result_response_gating.py \
       tests/test_port3_response_guard.py -q
48 passed
```

Project's existing "Critical Regression Gate" (from
`docs/deployment/DEPLOYMENT_RUNBOOK.md`) plus the new Postgres/Instagram
suites:

```
239 passed, 31 subtests passed, 7 failed
```

**All 7 failures are pre-existing and unrelated to today's changes** -
confirmed via `git diff`: 6 are in `tests/test_phase5_gemini_write_tools.py`
and fail because the *assertion wording* wasn't updated after an earlier,
separate change to `write_tool_executor.py`/`gemini_agent.py` (104 and 137
uncommitted lines respectively, made before this session started - e.g. a
test expects the reply to contain `"open lead"` but the app now correctly
says `"Your request LD00001 is already recorded"` after the recent
duplicate-lead-contract fix). The 7th
(`test_ai_agent_production_rejects_weak_or_unsafe_env`) fails only in this
local environment because a real `GEMINI_API_KEY` is already set in this
machine's `.env` and `load_settings()` re-populates any env var
`monkeypatch.delenv` unset from the real `.env` file - it would pass in CI
where no `.env` exists. I did not touch any of the files responsible for
either failure class. Recommend someone who has context on the recent
duplicate-lead-contract change reconciles those 6 assertions.

The broader full suite (`pytest tests -q`, 605 tests) additionally shows 39
failures total (the 7 above plus 32 more, e.g. UI layout login redirects,
employee followup workspace, trip redesign) - all in files that were already
modified and uncommitted before this session began (`auth.py`,
`bookings.py`, `travelers.py`, `base.html`, etc.), confirmed via `git diff`/
`git status`. Not something today's Instagram/deploy work touched or should
try to fix blind.

## What You Still Need To Do (can't be done from here)

1. **Provision the EC2 instance** and point a domain at it - see
   `deploy/README.md` step-by-step, starting from "provision the instance."
2. **Create the production PostgreSQL database** (RDS recommended) and run
   the schema/migration per `database/postgres/DEVOPS_POSTGRES_SETUP_INSTRUCTIONS.md`
   - do not reuse the shared staging RDS instance from the earlier smoke
     test for real production data.
3. **Fill in every `# SECRET` value** in `.env.production.example` on the
   server - `SECRET_KEY`, `APP_SECRET_KEY`, `ADMIN_PASSWORD`, `CRM_API_TOKEN`,
   `DATABASE_URL`, `GEMINI_API_KEY`, and the four `META_*` values.
4. **Create the Meta App** and Instagram/Messenger product in the Meta App
   Dashboard, subscribe to the `messages` webhook field, and fill in
   `META_VERIFY_TOKEN` / `META_PAGE_ACCESS_TOKEN` / `META_APP_SECRET` /
   `META_PAGE_ID` - `deploy/README.md` section 8 has the exact clicks.
5. **Decide on the file cleanup list above** and the 6 non-staged
   `docs/deployment/*.md` files I did not touch (they're gitignored and
   weren't part of what you asked me to list).
6. **Reconcile or accept** the 6 stale `test_phase5_gemini_write_tools.py`
   assertions and the 32 other pre-existing failures - not blocking for a
   first deploy, but worth a decision before treating `pytest tests -q`
   green as a release gate.
