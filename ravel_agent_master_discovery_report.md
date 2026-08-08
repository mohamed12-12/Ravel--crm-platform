# Ravel Agent -- Master Discovery Report

Running log of confirmed root causes and fixes found while hardening the
Ravel Traveler agent/CRM stack. Each entry is dated and self-contained;
newer entries go at the top. Cross-reference commit hashes where available.

---

## 2026-08-08d -- Load/scalability audit: production process config had silently drifted from every doc, one of the gaps is a live bug

Triggered by ownership asking, directly: what actually happens under real
concurrent load? Investigated rather than reassured, per this project's
whole-session practice. Split into what was confirmed by direct
inspection/live data (below) versus what still needs the user's own EC2
access to finish (see "Explicitly NOT done").

**Confirmed via a live `pm2 describe` on both processes (the user ran
this; I have no EC2 access):** the actual production launch commands
disagreed with `deploy/systemd/*.service` and `deploy/README.md` in every
dimension checked. Those docs describe a deployment approach that was
apparently never actually adopted -- production is a hand-run PM2 setup
with no tracked ecosystem config anywhere in the repo, so it had drifted
silently with nothing to catch it:

- `rahma-agent`: `venv/bin/python demo_web/app.py` -- the raw Flask
  **development** server, not gunicorn+`services/ai_agent/wsgi.py` as
  documented (that file does exist and is correct, it just isn't what's
  running). One process (PM2 fork mode, no cluster count).
- `rahma-crm-api`: `gunicorn -w 2 -b 127.0.0.1:5002 run:app` -- **plain
  sync workers, no `--worker-class eventlet`, no `REDIS_URL`** -- directly
  contradicting the single-eventlet-worker constraint
  `deploy/systemd/rahma-crm-api.service`'s own comment already documented.
  Port (5002 live vs. 5000 documented) was a third, harmless-but-confirms-
  the-pattern drift.

**This second one is not a future risk -- it is a currently-active bug.**
Flask-SocketIO's connection state (who is connected to which handoff
room) lives in one process's memory with no `message_queue` configured.
With 2 separate OS processes and no shared state, a handoff notification
fired on the process that isn't holding the target admin's websocket
connection is silently dropped, right now, roughly half the time.

**Confirmed by direct local reproduction (not assumption) that the dev-
server finding is real and that the proposed fix actually works:** built
a minimal throwaway Flask app with one endpoint that sleeps 2s (standing
in for the 1-6s Gemini calls seen throughout this session's production
logs), and fired 5 concurrent requests at it two ways --
  - Plain `app.run()` (`threaded=False`, the real default, matching
    `demo_web/app.py` exactly): **5 concurrent 2s requests took 10.03s
    total** -- precisely serial, each request starting exactly when the
    previous one finished.
  - The same app served via `eventlet.wsgi.server` (monkey-patched):
    **5 concurrent 2s requests took 2.05s total** -- genuinely concurrent.

**Also found, not in the original theory:** `ToolCallingSessionRuntime`'s
session state (`services/ai_agent/ai_agent_app/agent/tool_calling_runtime.py:171`,
a plain `dict`) and the rate limiter's counters (`storage_uri="memory://"`)
are both created fresh per `create_app()` call -- i.e. one independent
copy per worker **process**, with no shared store and no sticky routing
anywhere in the stack. The in-memory session dict is currently safe only
because there's exactly one `rahma-agent` process today, by accident, not
by design -- naively adding more gunicorn workers to fix the blocking
problem above would have traded "slow under load" for "silently resets a
customer's conversation mid-way," which is worse. The correct fix is the
same one already proven for `rahma-crm-api`: an eventlet worker class,
kept at **1** worker, not more OS processes -- eventlet's cooperative
concurrency lets that single worker serve many customers concurrently
during I/O waits without needing a second process at all.

**Also found:** `POST /rahma-agent/webhook`'s rate limit (60/min) is
keyed by remote address, but every Instagram customer's message arrives
via Meta's own calling infrastructure, not the customer's own IP -- so
it's one ceiling shared across every customer combined, not per-customer.
A handful of people chatting concurrently could plausibly reach it. No
`SQLALCHEMY_ENGINE_OPTIONS` existed anywhere either -- relying on
SQLAlchemy's small defaults regardless of worker count.

**Fixed (code-side, all locally tested, none of it deployed):**
- `apps/api/app/extensions.py`: both the Socket.IO `message_queue` and the
  rate limiter's `storage_uri` now read `REDIS_URL` when set, falling back
  to today's exact in-memory behavior when it isn't -- zero risk to local
  dev/tests/the current single-worker deployment.
- `deploy/systemd/rahma-ai-agent.service`: corrected from "2-3 plain sync
  workers" (actively dangerous given the session-state finding above) to
  `--worker-class eventlet --workers 1`.
- `deploy/systemd/rahma-crm-api.service`: `--worker-class eventlet`
  restored, port corrected to 5002, comment updated with the live-bug
  finding.
- `deploy/pm2/ecosystem.config.js` (new): the actual PM2 launch commands,
  now tracked in git for the first time, matching the corrected config
  above -- fixes the root cause of the doc/reality drift itself, not just
  this one instance of it.
- `services/ai_agent/ai_agent_app/server.py`: webhook rate limit raised
  60/min -> 300/min (default) and made adjustable via `WEBHOOK_RATE_LIMIT`
  without a code change, given real traffic volume isn't known yet.
- `apps/api/app/__init__.py`: `SQLALCHEMY_ENGINE_OPTIONS` (pool_size=5,
  max_overflow=10, pool_pre_ping, pool_recycle=280s) applied when
  `DATABASE_URL` is Postgres, adjustable via `DB_POOL_SIZE`/
  `DB_MAX_OVERFLOW`/`DB_POOL_RECYCLE_SECONDS`; left unset for SQLite,
  which doesn't support these options at all.
- `scripts/concurrent_load_test.py` (new): fires N simulated concurrent
  customers (configurable) at a real running instance, each sending a few
  paced messages, and checks for dropped/errored requests and session
  cross-contamination -- the actual test for "will it fall over," not a
  configuration review alone.

**What is explicitly NOT done, honestly (no EC2/PM2/Redis-server access
in this environment):**
- None of this is deployed. The corrected PM2 commands are written down
  in `deploy/pm2/ecosystem.config.js` but nobody has run
  `pm2 delete rahma-agent rahma-crm-api && pm2 start deploy/pm2/ecosystem.config.js`
  against the real box.
- `REDIS_URL` requires a real Redis instance provisioned on the EC2 box
  (or reachable from it) -- not done. The redis-backed wiring was only
  verified structurally (the right manager class gets constructed) against
  an address with nothing listening, since Flask-Limiter/Flask-SocketIO
  both connect lazily -- never verified against a real Redis server.
- The two-admin-session live handoff notification test (open two admin
  sessions, trigger a handoff from one, confirm it appears in the other)
  has not been run against the real deployed app.
- `scripts/concurrent_load_test.py` has not been run against the real
  deployed app, only sanity-checked as a script (gunicorn itself cannot
  run in this Windows dev environment at all -- no `fcntl` module -- so
  even the corrected worker config could only be proven via a standalone
  eventlet reproduction, not the actual `gunicorn` command that will run
  in production).
- Whether `redis-server` or an equivalent is already available on the EC2
  instance, or needs installing, is unknown.

---

## 2026-08-08c -- Issue 1, closed: `_next_prefixed_id()`'s lexicographic-sort ID collision

**This closes the Issue-1 investigation arc.** Two prior hypotheses in this
log (`idempotency_key` schema drift; a generic "local handler exception"
diagnosis) were real, defensible fixes given what was known at each point,
but neither was the confirmed root cause of the specific "esclate me"
collision. This one is confirmed directly by code logic, independent of
any live-database claim:

**Root cause:** `apps/api/app/services/agent_crm_bridge.py`'s
`_next_prefixed_id()` computed the "last" row via
`ORDER BY <column> DESC LIMIT 1` -- a **lexicographic (text) sort**, not a
numeric one. A non-sequential ID in that column (a UUID/hex-style row from
an old script, manual insert, or different code path -- e.g.
`H-B3AE7949`) sorts *above* every real sequential ID (`'B' > '0'`
character-by-character), so it gets picked as "last" every time such a row
exists. The function then blindly concatenates whatever digits happen to
appear in that string (`"B3AE7949"` -> digits `"37949"`) and computes
`next_number = 37949 + 1 = 37950` -- which can collide with a real,
already-existing sequential row (`H-00037950`), causing a
`UniqueViolation` on every subsequent handoff-creation attempt, not a rare
one. Verified independently via plain Python arithmetic (no database
needed): `"".join(ch for ch in "H-B3AE7949" if ch.isdigit())` does produce
`"37949"`, confirming the mechanism exactly.

The same shared function is used for `travelers`, `leads`, and
`trip_bookings` IDs too -- identically exposed the moment any
non-conforming row exists in those tables, even though none had visibly
triggered it yet.

**Notably, `services/crm/system_services/unified_service.py`'s own
`_next_prefixed_id` (the SQLite backend) never had this bug** -- it already
fetched every matching row and computed the numeric max in Python via
`re.fullmatch(prefix + r"(\d+)", value)`, ignoring anything that didn't
match. This is the textbook shape of this project's recurring dual-backend
drift: one backend's implementation was corrected or written more
carefully at some point, and the other silently kept the flawed version.

**Fixed:** rewrote the Postgres-side `_next_prefixed_id` to match its
already-correct SQLite sibling's approach -- fetch every row matching the
prefix, keep only ones that are strictly `prefix + digits`, take the
numeric max among those. Applies to all four call sites automatically
since it's the one shared function.

**Verified:**
- `test_next_prefixed_id_ignores_non_conforming_rows_and_sorts_numerically`
  -- seeds the exact three IDs from the live report, confirms the next ID
  is `H-00037951`.
- `test_create_handoff_case_does_not_collide_with_the_exact_live_non_conforming_rows`
  -- same seed, but through the real `create_handoff_case()` write path
  over the real `/api/crm/agent/write` route.
- `test_next_prefixed_id_starts_from_one_when_every_row_is_non_conforming`
  -- edge case, confirms it still starts from 1 with zero conforming rows.
- Confirmed each test actually fails against the pre-fix code (temporarily
  reverted, re-ran, restored) before trusting them as real regression
  guards -- not just written and assumed correct.

**What is explicitly NOT confirmed or done, honestly (no EC2/production/RDS
access available in this environment):**
- The exact production rows (`H-B3AE7949`, `H-914C4F11`) were never
  queried or inspected by me -- I cannot run
  `SELECT * FROM handoff_queue WHERE handoff_id IN (...)` against real
  production, so their actual origin (old script, manual test insert, a
  different code path) remains unknown and still needs investigation by
  someone with real DB access.
- The four-table non-conforming-ID sweep
  (`SELECT ... WHERE ... !~ '^PREFIX[0-9]{N}$'`) was never run against
  real production for the same reason.
- Nothing was deployed to EC2, no PM2 process was restarted, and no live
  browser reproduction was performed. The fix is committed and tested
  locally only; live verification (two consecutive "esclate me" requests
  producing distinct, correctly-incrementing handoff IDs, confirmed by
  querying the real database afterward) still needs to happen on the real
  deployed system before this can be called fully closed end-to-end.

---

## 2026-08-08b -- The real Path A vs Path B distinction in `_execute_manual_handoff`

Direct follow-up to Issue 1 below, after a live production check disproved
that entry's root-cause claim. `_execute_manual_handoff()`
(`tool_calling_runtime.py`) has two structurally different failure paths,
and confusing them costs real investigation time:

- **Path A** (~line 3138): `self._write_executor.execute(...)` itself
  *raises*, caught right there. Fixed correctly in the previous round --
  logs at ERROR with `exc_info=True`. Not touched in this entry.
- **Path B** (~line 3176): `execute()` returns *normally* -- no exception
  visible to `_execute_manual_handoff` at all -- but the returned
  `write_result_contract` says `status="failed"`. Confirmed live,
  repeatedly, as the path that's actually firing for "esclate me". The log
  line here only ever re-prints the already-sanitized contract; it has no
  way to show the real underlying reason because that reason was never
  captured in the first place.

**Traced the real decision point:** in production (`CRM_ACCESS_MODE=api`),
the agent process's `execute()` forwards the write over HTTP and never
reaches Path A/B's own exception handling itself -- it's
`apps/api/app/routes/crm.py`'s `agent_write()` that builds a *second*,
independent `GeminiWriteToolExecutor` and calls `execute()` again, locally,
inside the `rahma-crm-api` process. That second call's own local-handler
`except Exception` (same file, same class, different process) is what was
actually catching something and converting it into a clean-looking failed
contract with zero diagnostic detail -- a Path-A-shaped bug hiding one
layer deeper than where the previous round's logging reached. Confirmed by
building a real two-process repro (an actual CRM API Flask app served over
a real HTTP socket, an actual agent-side executor configured with
`CRM_ACCESS_MODE=api` making a real network call to it): the exception
fired inside the CRM-API process, and the agent side got back exactly the
"clean" `status="failed"` contract described above.

**What's fixed:** that local-handler `except Exception` in
`write_tool_executor.py` now logs the real exception at ERROR with a full
traceback -- this is the exact spot everything upstream already succeeds
or is approved, so anything reaching it IS the real, specific reason, not
a re-derivation of one.

**What's still NOT confirmed, honestly:** the *specific* exception that's
actually firing in real production. I have no SSH/credentialed access to
the EC2 box, the real RDS, or live PM2 logs from this environment -- every
check above was against a local/scratch repro that forces the same code
path, not the real deployed process. This fix makes the real reason
*visible* the next time "esclate me" is reproduced live; it does not, by
itself, tell us what that reason is. That requires deploying this logging
change and reading the actual `rahma-crm-api` log output after a live
reproduction -- which needs to be done by someone with EC2 access.

**Cross-process log gotcha (documented inline at the log call too):** this
log line lives in a module (`services/ai_agent/...`) that both PM2
processes import, and its logger is named `"rahma_agent"` -- neither fact
determines which process's log captures it. Python logging state is
per-process; whichever process's interpreter actually executes the line
is the one whose stdout/log file gets it. For the `CRM_ACCESS_MODE=api`
production path, that's `rahma-crm-api`, not `rahma-agent`, despite the
logger's name.

---

## 2026-08-08 -- Five-issue live-bug pass (escalate write failure, employee login/delete, traveler delete)

### Issue 1 -- "escalate"/"esclate" still failed after the intent-detection fix (commit `62c7cbe`)

**Initial hypothesis (later found unconfirmed -- see the 2026-08-08b entry
below): `idempotency_key` schema drift.** `idempotency_key` was added to
the `TripBooking`, `Lead`, and `HandoffQueue` SQLAlchemy models (used by
`agent_crm_bridge.py`'s `_handoff_by_idempotency`/`_booking_by_idempotency`
raw-SQL dedup lookups) but no Alembic migration ever created the column --
confirmed by grepping every migration file for `idempotency_key` (zero
matches). **This was verified only against the repo's migration history and
a local/scratch database -- NOT against the real production RDS.** A
follow-up live check against the real production RDS
(`information_schema.columns` on `ravel.handoff_queue`) found the column
already exists there, contradicting the "missing in production" claim this
entry originally made. The migration added below is still safe to keep
(idempotent, matches the established reconciliation pattern) but should
not be treated as the confirmed fix for the live failure -- see the
2026-08-08b entry for the actual traced mechanism and what's still
unconfirmed about it.

**Fixed (kept, but not the confirmed root cause of the live failure):**
- `database/migrations/versions/a3f6c9e2d817_*` -- reconciliation migration
  adding `idempotency_key` to all three tables (same pattern as the earlier
  passport/currency reconciliation in `f4c8b21e9a3d`), in case the column is
  ever genuinely missing on some environment.
- `write_tool_executor.py`'s local-handler `except Exception` now also logs
  the real exception at ERROR with a traceback, not just the sanitized
  audit code -- this logging change turned out to be the actually load-
  bearing part of this entry; see 2026-08-08b.

**Lesson:** "confirmed by grepping the repo" and "confirmed against live
production" are not the same claim, and conflating them in this entry's
original wording is exactly the mistake to not repeat.

### Issue 2 -- newly created employees could not log in

**Confirmed root cause:** `auth.py`'s `login()` only ever validated against
`_credentials_match()`, which checks the single env-var-configured admin
identity (`ADMIN_USERNAME`/`ADMIN_PASSWORD`). It never queried the `users`
table at all -- so no employee created via `/admin/users/create`, however
correctly stored (their password IS hashed correctly there), could ever
authenticate. This is a distinct, more fundamental bug than the previously
suspected "hardcoded role" theory -- that provisioning-time hardcode is
correct and intentional for the one admin identity it applies to.

**Fixed:** added `_employee_login_match()`, checked as a fallback when the
env-var admin check fails, validating the submitted password against the
real stored `password_hash` for an active user and using their actual
stored role.

### Issue 3 -- no way to delete an employee

Added `/admin/users/<id>/delete`: blocks self-delete, blocks deleting an
employee with any current lead/booking assignment (explicit choice per this
round's spec -- block rather than silently reassign), detaches (nulls,
doesn't delete) every historical FK reference that isn't a current
assignment (`UserAuditLog`, `AssignmentHistory`, `Lead`/`TripBooking`
`assigned_by_user_id`, `TripMedia.uploaded_by_user_id`), and logs the
deletion to `UserAuditLog`.

### Issue 4 -- traveler delete failed; investigated two hypotheses, one was wrong

First hypothesis (TravelerDocument rows never explicitly deleted) turned
out to be **incorrect** -- `Traveler.documents` already has
`cascade='all, delete-orphan'`, so the ORM already deletes those rows. This
was only caught by writing a foreign-key-enforced test (SQLite doesn't
enforce FKs by default, which is why nothing caught this class of gap
locally before) and watching it pass even with the "fix" reverted --
proof the premise was wrong, not proof the code was fine.

**Confirmed real root cause:** `booking_status_history.booking_id` is
`NOT NULL` with a foreign key to `trip_bookings.booking_id`, and neither
model declares a cascade relationship. `delete()`'s bulk
`TripBooking.query...delete()` had nothing deleting `BookingStatusHistory`
rows first, so any traveler whose booking ever had a status change logged
(the normal case for a real booking) could not be deleted on a backend that
enforces foreign keys (Postgres in production).

**Fixed:** explicit `BookingStatusHistory` bulk delete before the
`TripBooking` delete, keyed off the already-computed `booking_ids`. Also
added an `IntegrityError` catch around the final commit that returns a
clear, honest error to the admin UI instead of a raw 500, in case any other
FK gap surfaces in the future. The list/dashboard delete action (added in
the previous round) was already present and working -- confirmed via a
real HTTP CSRF+auth+FK-enforced live check rather than re-added.

**Lesson carried forward:** every delete-cascade fix in this project needs
a foreign-key-*enforced* test, not just a SQLite-default test, or a broken
cascade (or a non-existent one that was never actually broken) is
indistinguishable from a correct one locally.
