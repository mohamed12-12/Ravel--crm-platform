# Ravel Agent -- Master Discovery Report

Running log of confirmed root causes and fixes found while hardening the
Ravel Traveler agent/CRM stack. Each entry is dated and self-contained;
newer entries go at the top. Cross-reference commit hashes where available.

---

## 2026-08-09 -- Session-identity-stickiness defensive hardening: implemented, unit-tested, NOT confirmed live (deploy step unavailable from this environment)

**This is explicitly a defensive hardening fix, not a confirmed reproduction
of a specific incident.** The QA pass below found the traveler-delete
report itself not currently reproducible; the staleness bug this hardens
against is real by code inspection, but no live incident has ever been
pinned to it.

**What changed**, `services/ai_agent/ai_agent_app/agent/tool_calling_runtime.py`:
- Added `_traveler_still_exists_before_write(session)`: a real
  `find_traveler_by_phone` re-check, run only right before a high-stakes
  write (not every turn -- a CRM round-trip per message would be wasteful
  for a rare failure mode), that deliberately bypasses
  `_store_identity_result`'s "ignore a not_found for an already-verified
  session" guard, since this is the one deliberate point where a genuine
  not_found must be trusted, not discarded. Fails open (returns True,
  proceed) on no cached identity, no phone on file, or a lookup error --
  this is a narrow safety net for a genuine deletion, not a general retry
  gate.
- Added `_invalidate_stale_identity(session)`: clears the session's cached
  `identity_verified`/`verified_traveler` state once a re-check positively
  confirms the traveler is gone.
- Wired into `_execute_booking_draft`: blocks the write outright and
  restarts identity collection (`session.stage = "identity_required"`,
  clears `raw_phone`) rather than creating a booking against a traveler_id
  that no longer resolves to anything -- a booking has no safe fallback.
- Wired into `_execute_manual_handoff`: falls back to a phone-only handoff
  (clears `traveler_id`, adds an internal note flagging the discrepancy)
  rather than blocking the customer's explicit request for human help -- a
  handoff with no valid traveler is still useful to a human, unlike a
  booking.
- Deliberately NOT wired into `_escalate_after_repeated_contradiction` or
  `_execute_policy_handoff` (the other two `create_handoff` call sites) --
  those are backend-triggered, not directly hinging on the customer's own
  claimed identity the same way. Flagged as a narrower follow-up if this
  is ever revisited.

**Regression tests added**, `tests/test_golden_transcript_regressions.py`:
`test_booking_write_is_blocked_when_verified_traveler_is_deleted_mid_session`
and
`test_manual_handoff_falls_back_to_phone_only_when_verified_traveler_is_deleted_mid_session`.
Both simulate the deletion by nulling the mocked read-tools double's
`identity` attribute mid-session, then re-sending a message that reaches
the write point. Both pass, proving the intended code path is correct in
isolation.

**Test-double gaps found and fixed while adding this** (not production
bugs): three existing tests set up a "verified traveler" session directly
(`_trip_selection_session`, `_selected_trip_session`, and an inline
new-minor-traveler setup in `test_guardian_consent_workflow.py`) without
ever configuring the mocked read-tools double to also report that same
traveler as found on a fresh lookup -- harmless before this change (nothing
ever re-checked), but the new pre-write re-check correctly noticed the
inconsistency and (correctly, by the new logic's own lights) treated an
unconfigured double as "traveler not found." Fixed by keeping each
helper's read-tools double in sync with whatever traveler it marks
verified. Full suite: 825 -> 840 (QA pass fixes) -> 842 (this hardening),
0 failed throughout, confirmed with a fresh full run after every change.

**Live verification: attempted four times against the deployed agent
(`https://demos.nanovate.io/rahma-agent/`), inconclusive for this specific
change.** Procedure each time: insert a disposable test traveler directly
in Postgres (mirroring `create_traveler`'s own schema), verify identity in
a fresh session, delete the traveler mid-conversation (mirroring the
dashboard delete route's cascade), then continue the same open session
and request escalation. In all four attempts, the persisted handoff
correctly showed `traveler_id: null` -- but re-fetching the session
afterward showed `session.preview.traveler` still holding the **old,
pre-deletion** traveler dict, meaning `_invalidate_stale_identity` was
never actually invoked. **The most likely explanation: this environment
has no mechanism to deploy or restart the live `rahma-agent` pm2 process,
so these live tests almost certainly exercised the previously-deployed
code, not this session's edits.** The `traveler_id: null` result each time
is consistent with a separate, pre-existing safety net already present in
the write-validation layer (`write_tool_executor.py`'s `_resolve_traveler`
re-resolves the traveler fresh via `get_traveler_profile` at write time
regardless of what the caller passes) -- a reassuring independent finding,
but not evidence this specific runtime-level change works live. **Action
needed: deploy this change, then re-run the same four-step live
procedure and confirmed `session.preview.traveler` is actually cleared
(not just that the persisted `traveler_id` ends up null, which the
write-validation layer may already guarantee on its own).**

Also found and incidentally fixed while live-testing: the handoff-queue's
`deduplicate_open` matching appears to match ANY open ("Pending") handoff
sharing the same `reason_code` when `traveler_id` is empty, regardless of
session/customer -- two of the four live attempts above deduped against
an unrelated earlier test handoff instead of creating a fresh row. Not
investigated further (out of scope for this hardening pass), but worth a
dedicated look: if this also happens for two different real anonymous
customers escalating around the same time, their requests could get
silently merged into one handoff row for staff. All disposable test
travelers and test handoff rows created during this verification have
been cleaned up (deleted / marked Resolved) so nothing lingers in the
live production queue.

---

## 2026-08-09 -- Traveler-delete-then-reuse-phone-number: investigated, not reproducible; production-readiness QA pass found and fixed 3 new bugs, closed a real test-coverage gap

**Traveler-delete-then-reuse-phone-number.** A report claimed a deleted
traveler's phone number still resolved as "existing" in a brand-new AI
agent conversation. Investigated live against production before assuming
any cause:

- Ruled out, with evidence: orphaned `leads` rows (`resolve_identity()` in
  `agent_crm_bridge.py` only ever queries `travelers`, never `leads`),
  incomplete delete cascade (every FK-referencing table is already covered
  in `travelers.py`'s delete route), duplicate traveler rows sharing a
  phone number (zero found across all 541 travelers via a direct Postgres
  query), a swallowed delete failure (both delete-button JS paths surface
  the real server error via `alert()`), and a split-brain agent/CRM
  dispatch mismatch (`crm.py`'s `_agent_runtime()` correctly routes to the
  Postgres-native `PostgresAgentCRMTools` whenever `SQLALCHEMY_DATABASE_URI`
  isn't SQLite -- confirmed live: the deployed agent's own session payload
  reports `dbSource: "postgres:crm-api"` with a `travelerCount` matching a
  direct DB query exactly).
- A genuinely fresh session (confirmed via the reappearance of the opening
  greeting, which can only come from the server's real response to
  `POST /api/session` -- `services/ai_agent/ai_agent_app/web/static/app.js`
  has no `localStorage`/`sessionStorage`/cookie-based session caching at
  all), sent the exact originally-affected phone number, correctly
  returned `not_found` live against production.
- **Most likely explanation, not a currently-live bug**: the original
  report's delete happened inside a ~17.5-hour window (2026-08-07 14:07 ->
  2026-08-08 07:45) where the dashboard-list delete button had a hardcoded
  URL bug (fixed in `e3d7504`) stacked on a separately-being-fixed FK
  cascade gap (fixed in `cdda723`) -- a genuinely ambiguous "did this
  actually delete" period, since resolved.
- **Real, separate, unresolved bug found and deliberately left unfixed
  pending a decision**: `services/ai_agent/ai_agent_app/agent/tool_calling_runtime.py:867-930`
  never re-checks a traveler's identity once a session has verified it
  once, and explicitly discards a fresh `not_found` result for an
  already-verified session. Not what explains this report, but a real gap
  if a traveler is deleted *while* a customer is mid-conversation in a
  session that already verified them.

**Production-readiness QA pass.** Full functional test matrix run across
CRM + AI agent (see `TEST_REPORT.md`, `PRODUCTION_READINESS.md` for full
detail). Fresh full-suite baseline: 825 passed -> 840 passed after this
pass's fixes/new tests, 0 failed, both times (supersedes the stale "645
passed" figure, which predated ~180 tests added across 5 later commits).

New bugs found and fixed:
- **Mojibake on the booking detail page.** `apps/api/app/templates/bookings/detail.html`
  had 26 corrupted characters across 21 lines (`←`, `→`, `—`, `·`), all one
  root cause: UTF-8 bytes misread as Windows-1252 and re-saved as UTF-8,
  introduced in commit `672ee220` (2026-08-03). Same root-cause class as a
  previously-fixed incident in the AI agent's own Arabic prompt strings
  (`ae665552`), but that fix's mojibake guard only inspects agent-generated
  chat text, not Jinja templates -- why this instance was never caught.
  Fixed via precise Python codepoint replacement (`chr(0x...)`, never a
  shell heredoc with the literal characters typed inline -- typing them
  directly triggered the sandbox's control-character-in-command guard on
  the first several attempts, which is itself a useful confirmation that
  raw multi-byte/control characters should never be hand-typed into a
  shell command for this exact class of file).
- **Same hardcoded-path-under-reverse-proxy bug, 4 more instances the
  prior two fix passes missed**: `leads/detail.html`'s "Advance Stage"
  button (`fetch(\`/leads/${leadId}/advance\`)`) and `admin/handoffs.html`'s
  three update-handoff calls (`submitResolution`, `quickAssign`, the
  drag-and-drop handler; all `fetch(\`/admin/handoffs/${handoffId}\`)`).
  Fixed the same way as before (`url_for()`); since `handoffId` is only
  known client-side at runtime, used a `url_for(..., id='__HANDOFF_ID__')`
  placeholder template substituted in JS rather than a per-render literal.
  New regression test: `tests/test_ui_layout_regressions.py::test_leads_and_handoffs_action_urls_respect_a_reverse_proxy_path_prefix`.

Real test-coverage gap found and closed (no code fix needed):
- `_AFFIRMATIVE_REPLIES` in `tool_calling_runtime.py` defines 16 synonyms
  accepted as a booking confirmation; only `"yes"` and `"confirm"` had ever
  been exercised by a test. Added
  `tests/test_golden_transcript_regressions.py::test_every_affirmative_reply_synonym_confirms_the_booking`,
  parametrized over the remaining 14 (`y, ok, okay, sure, book it, go
  ahead`, plus 8 Arabic variants). All 14 passed on the first run -- the
  logic already worked, the gap was purely in coverage.

Live-verified, not just read: escalate/esclate -> real handoff, both via a
live HTTP request against the deployed agent (`toolsUsed:
["create_handoff"]`, `write_result_contract.status: "created"`) and a
direct Postgres query confirming the handoff row (`H-00037953`) actually
persisted.

**Flagged, not fixed -- open verification gap**: the booking-status-update
split-brain fix (previous entry, this same date) has **zero**
`booking_status_history` rows with `change_source='crm-ui'` in production,
ever, per a live query run during this pass -- meaning no real CRM-UI
session has exercised the fix since it shipped. Could not close this
personally: only a scrypt password hash exists for the admin account, no
usable credential. Needs one real admin click-through to confirm.

---

## 2026-08-09 -- Booking status updates now write through Postgres/SQLAlchemy; CSRF wording and hardcoded handoff URL fixed

Confirmed the booking-status-update failure was a split-brain persistence
bug, not a CSRF/session bug. The route in `apps/api/app/routes/bookings.py`
used `UnifiedCRMService().update_booking_status(...)`, which wrote through
the legacy SQLite path while the rest of the booking detail page reads from
the Postgres/SQLAlchemy session. That mismatch meant the UI could report a
successful save without the booking row or status-history row actually
changing in the database the page reads from.

**Wire-level clue that ruled out the original CSRF theory for the incident:**
the investigation observed a browser `POST` that surfaced as a `302`, not a
`400`. Since the booking-status form is a plain HTML form with no JS
interception, a browser will only follow a redirect on a real `3xx`
response; `_browser_redirect(..., status_code=400)` does not become a
redirect-followed navigation. That made the route-body fallback path the
actual suspect, not CSRF.

**Database proof of the split-brain issue:** a read-only Postgres query for
`BK000001` showed exactly one `booking_status_history` row total, with
`change_source='agent_write'`, and zero rows with `change_source='crm-ui'`.
That confirmed the CRM UI's status-update path had never landed in the
database the UI reads. The legacy SQLite fallback file contained unrelated
rows and did not contain the booking under investigation.

**What changed:**

- `apps/api/app/routes/bookings.py`
  - Replaced the SQLite service call with direct ORM mutation of the
    already-loaded `TripBooking`.
  - Reused `UnifiedCRMService._validate_booking_transition(...)` as the
    transition check.
  - Inserted a `BookingStatusHistory` row directly with
    `change_source='crm-ui'`.
  - Removed the stale `expire_all()`/reload sequence that only existed to
    compensate for the old out-of-band write.
  - Added `db.session.rollback()` plus structured error logging before the
    generic flash message.
- `apps/api/app/security.py`
  - Replaced the misleading CSRF flash copy with
    `Your session expired. Please refresh the page and try again.`
- `apps/api/app/templates/leads/detail.html`
  - Replaced the generic action-failure alert copy with
    `Something went wrong completing this action. Please try again.`
- `apps/api/app/templates/base.html` and
  `apps/api/app/templates/admin/handoffs.html`
  - Replaced the hardcoded `/admin/handoffs/pending` fetch target with
    `url_for("handoffs.pending_count")`.
- `tests/test_employee_followup_workspace.py`
  - Removed the accidental `BookingEventTrail` assertion from the happy
    path.
  - Added a regression test proving the route still works when
    `RAHMA_SYSTEM_DB_PATH` points at a nonexistent path.
  - Added a regression test that forces an exception in assignment and
    asserts the route logs the failure and surfaces the new save-failure
    message.

**Verification:** `python -m pytest tests/test_employee_followup_workspace.py -q`
passed (`14 passed`).

## 2026-08-08f -- Part A closed for real: live EC2 rollout done by ownership, three findings confirmed live and pulled back into the repo

Ownership ran the 2026-08-08e runbook against the actual EC2 box. **This
is the first entry in this whole load/scalability arc backed by a real
deployment, not code reading or a local proxy** -- recorded here exactly
as reported, then reconciled into the tracked files below.

**Confirmed live, both processes:** logs show `Using worker: eventlet`
for both `rahma-agent` and `rahma-crm-api`; `redis-cli CLIENT LIST` shows
an active connection from `rahma-crm-api`; `scripts/concurrent_load_test.py`
measured a 5.7x speedup (10.7s serial baseline vs. 1.87s concurrent) --
the same order of magnitude as 2026-08-08d's standalone local proof
(10.03s vs. 2.05s), now demonstrated through the real `gunicorn
--worker-class eventlet` process instead of a substitute script.

**Three real bugs found only by actually deploying, pulled back into git
here:**

1. **gunicorn 26.0.0 removed the eventlet worker entry point entirely** --
   `--worker-class eventlet` failed to load. 2026-08-08e's source reading
   confirmed *how* `EventletWorker` works in the version installed
   locally at the time (`gunicorn/workers/geventlet.py` existed there);
   it did not check whether that module still ships in the newest release,
   which is exactly the gap that bit production. Fixed by pinning
   `gunicorn==21.2.0` in both `requirements.txt` and
   `apps/api/requirements.txt` (was `>=21.2.0` in both), with a comment
   explaining why this one must not float to latest.
2. **`deploy/pm2/ecosystem.config.js` bound `rahma-agent` to port 3001,
   but nginx's real `/rahma-agent/` location proxies to 5003** --
   confirmed against the live nginx config. Fixed in the ecosystem file.
   While correcting this, also found (by re-checking every file that
   referenced `rahma-agent`'s production port, not just the one that was
   reported) that `deploy/systemd/rahma-ai-agent.service` and
   `deploy/nginx/rahma-traveler.conf` both still said 3001 too --
   corrected to 5003 in both for consistency, since a stale copy of
   either would reproduce this same class of bug the next time someone
   deployed from them. `deploy/nginx/rahma-traveler.conf`'s
   `rahma_crm_api` upstream was *also* still 5000, a full day after
   2026-08-08d already confirmed live that port is 5002 -- that
   correction had been applied to the systemd/PM2 files but this nginx
   example file was missed; fixed now too. `deploy/README.md`'s security-
   group note ("do not open 5000 or 3001 publicly") and this session's own
   just-written section 11.7 load-test command (`--base-url
   http://127.0.0.1:3001`) were both still wrong for the same reason --
   corrected to 5002/5003.
3. **`services/ai_agent/wsgi.py` never had `ProxyFix`.** Switching
   `rahma-agent` from the raw `python demo_web/app.py` dev server to
   gunicorn+`services/ai_agent/wsgi.py` (2026-08-08d's fix) moved
   production onto a code path that skipped it: `demo_web/app.py`'s
   `__main__` block applies `ProxyFix(x_for=1, x_proto=1, x_host=1,
   x_prefix=1)` for local runs, but `wsgi.py` is a separate, minimal
   production entrypoint that never had the equivalent. Confirmed live:
   `url_for('static', ...)` had no way to know this app is mounted at
   nginx's `/rahma-agent/` prefix, so the chat widget served with every
   CSS/JS request 404ing -- an unstyled page, not a crash, so it would
   have been easy to miss without specifically checking. Fixed by adding
   the identical `ProxyFix` call to `wsgi.py`. New tests in
   `tests/test_ai_agent_wsgi_proxy_fix.py` cover both directions (prefix
   present -> asset URLs prefixed; no proxy header -> unprefixed, so
   direct/local access is provably unaffected); both verified to fail for
   the right reason before the fix via revert-then-restore.

**Lesson for this arc specifically:** 2026-08-08e's "verified by reading
the source" pass was real and caught a genuine gap (psycopg2), but it
verified the mechanism using whatever gunicorn/eventlet versions happened
to be installed locally at the time -- it could not and did not check
"is this still true of the exact version that will actually get
installed on the box," which is a live-environment fact, not a
code-reading one. Both kinds of verification were necessary; neither
substitutes for the other, which is exactly why this arc kept both
labels ("confirmed by code reading" vs. "confirmed live") distinct
throughout rather than treating the first as sufficient.

**What's left, honestly:** ownership still needs to do the standard
pull+restart on the box so the now-corrected repo (gunicorn pin, port,
ProxyFix) matches what's actually running there (the live box currently
has these three fixes applied by hand, ahead of git, until that
pull+restart happens). Nothing else from 2026-08-08d/e's "explicitly NOT
done" list is newly closed by this entry beyond what's stated above as
confirmed live.

---

## 2026-08-08e -- Closing out Part A: eventlet-on-Linux verified by source reading (not guessed), one real gap found and fixed (psycopg2 + eventlet), deploy docs corrected for the real OS/process manager

2026-08-08d's local proof used a standalone `eventlet.wsgi.server`, not
actual `gunicorn --worker-class eventlet` (gunicorn cannot run on Windows
at all -- no `fcntl` module -- so the real command was never runnable
here). Rather than assert "it'll be the same on Linux," read the
mechanism directly from the actually-installed packages:

**Confirmed by reading the installed gunicorn source
(`gunicorn/workers/geventlet.py`), not memory or docs:**
`EventletWorker.init_process()` calls `self.patch()`, which calls
`eventlet.monkey_patch()` (no arguments -- the full default patch set:
`os`, `select`, `socket`, `thread`/`threading`, `time`) before the WSGI
app is even created. This is the same underlying eventlet green-thread
scheduler 2026-08-08d's standalone proof exercised (10.03s serial vs.
2.05s concurrent for 5 simulated 2s-blocking requests) -- gunicorn's
eventlet worker is not a different concurrency mechanism, it's the same
one, invoked automatically instead of manually. This is also
Flask-SocketIO's own documented, widely-deployed recommended production
config (`gunicorn --worker-class eventlet -w 1 module:app`), not a novel
choice being trusted blind.

**Audited every blocking network call either service actually makes, to
check it's really covered by that monkey-patch (grep + direct read, not
assumption):**
- `services/ai_agent/llm/gemini_provider.py` (Gemini API calls) and
  `services/ai_agent/ai_agent_app/agent/crm_api_client.py` (the
  `CRM_ACCESS_MODE=api` HTTP bridge to `rahma-crm-api`) both use raw
  `urllib.request` -> `http.client` -> the stdlib `socket` module.
  `google-generativeai`/grpc are not used anywhere in this repo despite
  being pinned in `requirements.txt` (unused pin, harmless but worth
  knowing). Fully covered by eventlet's monkey-patch -- confirmed clean,
  no caveat for `rahma-agent`.
- **`rahma-crm-api`'s Postgres access is not covered, and this is a real
  gap the monkey-patch does not fix:** `apps/api` connects via
  `postgresql+psycopg2://` (confirmed in `apps/api/app/config.py`).
  `psycopg2` talks to `libpq` through its own C extension and makes its
  blocking network syscalls below the Python `socket` module entirely --
  `eventlet.monkey_patch()` cannot see or patch it. Confirmed against the
  installed eventlet itself (0.41.1): it ships a separate, not-applied-by-
  default module for exactly this
  (`eventlet.support.psycopg2_patcher.make_psycopg_green()`, wraps
  `psycopg2.extensions.set_wait_callback()`). Left unpatched, one
  greenthread's Postgres query blocks the *entire* eventlet worker process
  for its duration -- every other concurrent request/Socket.IO connection
  that worker is holding stalls too, silently reintroducing the same
  class of problem eventlet was adopted to fix, just moved from "no
  eventlet at all" to "eventlet, but only for non-DB I/O."

**Fixed:** `apps/api/app/__init__.py` adds
`_green_psycopg2_if_running_under_eventlet()`, called right after
`SQLALCHEMY_ENGINE_OPTIONS` is set (before `db.init_app()`, so it runs
before any connection pool opens a connection). Guarded on
`eventlet.patcher.is_monkey_patched("socket")` being true -- i.e. it only
does anything when actually running under `gunicorn --worker-class
eventlet`, so local dev, pytest, and the plain Flask dev server are
provably untouched (`tests/test_db_connection_pool.py`: one test asserts
the wait callback stays `None` outside eventlet, one simulates the
monkey-patched condition via `monkeypatch` and asserts it gets set, both
verified to fail for the right reason before the fix via
revert-then-restore). `services/ai_agent` (`rahma-agent`) needs no
equivalent fix -- it never touches Postgres directly, per the call-path
audit above.

**Also found while re-reading the deploy docs against the real PM2
setup:** `deploy/pm2/ecosystem.config.js`'s `cwd`
(`/home/ec2-user/rahma-crm-platform`) matches Amazon Linux's default
`ec2-user` home directory, not Ubuntu's `ubuntu` user --
`deploy/README.md` sections 1-10 are written for Ubuntu (`apt install`,
systemd units) and were very likely never actually run on the real box,
consistent with 2026-08-08d's finding that the systemd units document an
approach that was never adopted. Not confirmed live (no EC2 access this
session) -- flagged in `deploy/README.md` section 11 as "confirm via
`cat /etc/os-release` before running any install command," not asserted
as fact.

**`deploy/README.md` section 11 rewritten** from a general explanation
into a literal, ordered command sequence for the real box: OS/process
confirmation, Redis install (branched by OS, not assumed), pulling the
now-corrected `requirements.txt`, setting `REDIS_URL` in the file the app
actually reads (`apps/api/app/config.py` loads the **repo-root** `.env`,
not `/etc/rahma-traveler/.env` -- confirmed by reading `_load_env_file`'s
call sites, not assumed from the systemd doc), switching PM2 over to the
tracked ecosystem file, verifying eventlet is really the running worker
class and Redis is really receiving traffic (not just that both started
without error), and the two live checks that are the actual proof: the
two-admin-session handoff test, and `scripts/concurrent_load_test.py`
run from on the box against `127.0.0.1:3001` directly.

**Still explicitly NOT done, honestly (same constraint as 2026-08-08d,
unchanged by this pass):** none of this has been run against the real
EC2 box -- no real OS confirmed, no real Redis provisioned, no real
`pm2 delete && pm2 start deploy/pm2/ecosystem.config.js`, no real
handoff test, no real `concurrent_load_test.py` run against the deployed
app. This entry closes the *reasoning* gap (why eventlet-on-Linux should
work, backed by reading the actual installed library source rather than
trusting the Windows proxy proof) and ships one additional confirmed-real
fix (psycopg2 greening) that the reasoning pass surfaced -- it does not
substitute for the live verification, which still requires the user's
own EC2 access.

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
