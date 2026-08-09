# Production Readiness — Assessment

**Date:** 2026-08-09
**Companion document:** `TEST_REPORT.md` (per-item evidence for every claim below)

## Go/No-Go recommendation

**Conditional Go.** No Critical or High-severity defect was found actively broken in production during this pass. The full test suite is green (840 passed, 0 failed) and several real gaps found during this pass were fixed and proven, not just logged. The one condition: **a human admin needs to click through the booking-status-update fix once in the real CRM UI** before fully trusting it in production — see the Medium item below for why this specific gap couldn't be closed by this pass alone.

## Confirmed-fixed items (this pass)

| Item | Evidence pointer |
|---|---|
| Mojibake/garbled characters on the booking detail page (`←`, `→`, `—`, `·` all corrupted) | `TEST_REPORT.md` → Cross-cutting checks |
| "CRM service unavailable" misleading message, all locations | `TEST_REPORT.md` → A.1 |
| `/admin/handoffs/pending` badge polling under the `/rahma-crm/` prefix | `TEST_REPORT.md` → A.7 |
| **New**: leads "Advance Stage" button hardcoded-path bug | `TEST_REPORT.md` → A.3 |
| **New**: handoffs kanban board's 3 update-status calls, same hardcoded-path bug class | `TEST_REPORT.md` → A.7 |
| **New**: confirm-synonym test-coverage gap (14 of 16 accepted synonyms had never been tested) — closed with new tests, no code fix needed since it already worked | `TEST_REPORT.md` → B.3 |
| Escalate/esclate → real human handoff | `TEST_REPORT.md` → A.7 (live + DB confirmed) |
| Booking-status-update split-brain persistence bug — **code fix confirmed in place, unit-tested** | `TEST_REPORT.md` → A.4 (see Medium item below for the live-verification gap) |

## Confirmed-working, pre-existing (re-verified this pass, not newly fixed)

RBAC matrix, CSV export scoping, duplicate-lead detection, trip delete cascade, employee creation/login/delete blocking, guardian-consent workflow + real-DB persistence, identity/meta-question canned replies, write-status integrity logging, `_next_prefixed_id` collision fix, gunicorn/eventlet pinning, zero remaining write-dispatch bypasses. All green in the fresh 840-test run; see `TEST_REPORT.md` for per-item method/evidence.

## Still-open items

### High

None found this pass.

### Medium

1. **Booking-status-update fix has never been exercised by a real CRM-UI session in production.** The code fix is in place (`apps/api/app/routes/bookings.py`) and passes 25 automated tests against a real HTTP route — but a live Postgres query shows **zero** `booking_status_history` rows with `change_source='crm-ui'`, ever. This investigation had no way to log in as admin to close the loop (only a password *hash* is available, not a usable credential) and did not create a live admin account without sign-off. **Action needed: one admin, one real status change, then re-run the same DB query to confirm a `crm-ui` row appears.** Low risk given the automated coverage, but per this project's own stated history of "fixes that were correct but for the wrong reason," this should be confirmed rather than assumed.
2. **`scripts/concurrent_load_test.py` was not run against the live deployment this pass.** Rate limits (`/api/session`: 20/min, `/message`: 30/min) mean it needs to be sized deliberately rather than run with defaults against production. The eventlet/multi-worker speedup this script exists to measure has not been re-confirmed since the most recent restarts.
3. **The two-concurrently-logged-in-admin-sessions handoff-visibility scenario has never been explicitly tested**, automated or live — only the underlying Redis/eventlet mechanics have been confirmed live (per the discovery report). The scenario itself (admin A creates a handoff, admin B — logged in separately, at the same time — sees it appear) is untested.
4. **A real, latent identity-cache staleness bug exists** in `services/ai_agent/ai_agent_app/agent/tool_calling_runtime.py:867-930`: a session that has already verified a traveler will never re-check, and will actively discard a fresh `not_found` result, for the rest of that session's lifetime. Not proven to have caused any observed incident (the specific traveler-delete-then-reuse-phone case this was investigated for turned out not to be reproducible, and most plausibly stemmed from an unrelated, now-fixed window of bugs — see `TEST_REPORT.md`). Still a real gap: if a traveler is deleted *while* a customer is mid-conversation in a session that already verified them, that session will keep treating them as existing for its remaining lifetime. Recommend hardening as defense-in-depth.
5. **Several CRM-side test suites** (`test_idor_ownership.py` for RBAC/CSV scoping, `test_trip_delete_and_sort.py`, `test_admin_login_protection.py`) **run against SQLite only**, not production's actual Postgres + `CRM_ACCESS_MODE=api` configuration. The logic under test is plain ORM code with no DB-specific branching, so this is a lower-confidence gap than the ones above — but per this project's own repeatedly-confirmed pattern (SQLite-path vs. Postgres-path silent divergence, 3 confirmed occurrences this session alone), it should not be assumed equivalent without eventually adding Postgres-mode coverage the way `test_postgres_agent_bridge.py` and `test_dual_backend_write_parity.py` already do for other areas.

### Low

1. Session persistence across reload/normal browsing has no dedicated test (implicitly covered, not explicitly asserted).
2. Employee creation/login-per-role automated coverage was not independently re-run against live production credentials (would require creating a real employee account; not done without sign-off).
3. "Who made you" identity-question canned reply was not spot-checked live in this pass — automated coverage is already thorough enough (asserts the model is never invoked) that this is low-value to repeat.

## Known limitations — acceptable to ship with

- **Single EC2 instance, no redundancy.** Already discussed and understood as a deliberate, cost-driven tradeoff, not a defect.
- **`rahma-agent` pinned to 1 worker by design**, to avoid the exact per-process in-memory session-state fragmentation this app would otherwise hit with multiple workers. This is a real constraint on horizontal scaling, not a bug — documented and intentional.
- **Postgres migration for `services/crm/system_services` (`UnifiedCRMService`) is not complete** — it still exists as a legacy, SQLite-native class used by some other routes/services (`trips.py`, `travelers.py`, `leads.py`, `admin.py`, `crm.py`, and `bookings.py`'s own `create()` route). The booking-status-update route no longer depends on it, but these other call sites were explicitly out of scope for this pass and may carry the same class of risk — flagged as a follow-up investigation, not confirmed broken.

## What blocks production readiness vs. what doesn't

**Nothing found this pass blocks go-live.** The Medium items above are real, worth closing, but none is a confirmed-broken core workflow — they're verification gaps (booking-status live click-through, load test, two-admin-session handoff visibility) or a narrow latent edge case (identity-cache staleness) with no confirmed live incident behind it. The recommendation is **Conditional Go**: ship, but close Medium item #1 (the one real "this specific fix has never actually been used for real" gap) with a single real admin click-through in the first business day after go-live, and track the rest as fast-follow.
