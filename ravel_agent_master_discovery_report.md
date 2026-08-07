# Ravel Agent -- Master Discovery Report

Running log of confirmed root causes and fixes found while hardening the
Ravel Traveler agent/CRM stack. Each entry is dated and self-contained;
newer entries go at the top. Cross-reference commit hashes where available.

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
