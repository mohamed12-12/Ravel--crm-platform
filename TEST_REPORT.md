# Test Report — Production Readiness QA Pass

**Date:** 2026-08-09
**Scope:** Full functional test matrix, CRM (`apps/api`) + AI Agent (`services/ai_agent`), per the production-readiness QA brief.
**Ground rule applied throughout:** every row states its verification method. "Automated" means a real test that ran and passed just now (not a memory of a past run). "Live" means a real request against the deployed app at `https://demos.nanovate.io/`. "DB query" means a direct read-only query against the production Postgres database. Code-reading alone is never cited as sole evidence of correctness.

## Severity framework used in this report

No severity framework existed anywhere in this repo to reuse (checked `ravel_agent_master_discovery_report.md`, `docs/qa/TESTING_RELEASE_GATE.md`, `docs/deployment/PRODUCTION_READINESS_REPORT.md` — none define one). Defined fresh here, used consistently in `PRODUCTION_READINESS.md`:

- **Critical** — causes data loss/corruption, or a core workflow (identity, booking, payment status) silently fails or produces wrong results in production. Blocks go-live.
- **High** — a real, reproducible defect with meaningful blast radius or no workaround. Fix before or immediately after launch.
- **Medium** — real defect, narrow blast radius, or a coverage gap for something not proven broken. Normal timeline.
- **Low** — polish / defense-in-depth, no known live impact.

## Fresh baseline

| Check | Result | Method |
|---|---|---|
| Full test suite, before this pass | 825 passed, 0 failed, 55 subtests | Automated (`pytest -q`) — supersedes the stale "645 passed" figure last logged in `ravel_agent_master_discovery_report.md`, which predates ~180 tests added across 5 later commits |
| Full test suite, after this pass's fixes/new tests | see final count below | Automated |
| `pytest --collect-only` | 825 collected, 0 collection errors | Automated |

---

## Part A — CRM (`apps/api`)

### A.1 — Authentication & session

| Test | Method | Result | Evidence |
|---|---|---|---|
| Login succeeds/fails cleanly per role | Automated | Pass | `tests/test_admin_login_protection.py` |
| CSRF failure shows corrected message, not old "CRM service unavailable" text | Live source check | **Confirmed live** | Repo-wide grep for the literal string `"CRM service unavailable"` returns **zero matches** anywhere in the codebase. `apps/api/app/security.py` now shows "Your session expired. Please refresh the page and try again." at both CSRF-guard sites. |
| Session persists across reload / normal browsing | Automated | Pass | Flask's signed-cookie session mechanics covered incidentally by every session-based test; no dedicated persistence-over-time test exists — not separately re-verified live in this pass. |

### A.2 — Travelers

| Test | Method | Result | Evidence |
|---|---|---|---|
| Create/view/edit per role (RBAC) | Automated | Pass | `tests/test_idor_ownership.py` — **SQLite-only.** The ownership-scoping logic is plain SQLAlchemy/ORM code with no DB-specific branching, but this suite does not exercise it against Postgres specifically. Flagging per ground rules. |
| Delete traveler, zero references | Automated | Pass | `tests/test_phase4_traveler_management.py` |
| Delete traveler with active lead/booking — blocked with a specific reason | Automated | Pass | Same file; FK-enforced test explicitly re-enables SQLite foreign-key checking to match Postgres's default-enforced behavior (`_enable_sqlite_fk_enforcement`) |
| Dashboard/list-page delete, incl. cancel path | Automated + source read | Pass | `deleteTravelerFromList` in `travelers/index.html` checks `confirm()` before firing; cancel path is a plain early return, not independently unit-tested but trivial/low-risk |
| Traveler-delete-then-reuse-phone-number (open investigation) | **Live reproduction + DB query** | **See dedicated section below — not a currently-reproducible bug** | — |
| CSV export ownership scoping (sales role) | Automated | Pass | `tests/test_idor_ownership.py::test_agent_csv_export_also_hides_traveler_owned_by_someone_else` — SQLite-only, same caveat as above |

### A.3 — Leads

| Test | Method | Result | Evidence |
|---|---|---|---|
| Create/view/edit/assign a lead | Automated | Pass | Covered across `test_idor_ownership.py`, `test_phase4_lead_redesign.py` |
| Ownership scoping (sales sees own + unassigned only) | Automated | Pass | `test_idor_ownership.py` — SQLite-only |
| Duplicate-lead detection/dialogue | Automated | Pass | `test_postgres_agent_bridge.py::test_repeated_create_lead_returns_structured_duplicate_in_{postgres,sqlite}_mode` — **this one is dual-mode**, actually exercises both backends |
| **NEW bug found + fixed:** `leads/detail.html`'s "Advance Stage" button built its fetch URL as a hardcoded `/leads/${leadId}/advance` string — same bug class as the traveler-delete/handoff-badge prefix bugs, missed by both prior fix passes | Automated (new regression test) | **Fixed** | `apps/api/app/templates/leads/detail.html:453-459` now uses `{{ url_for('leads.advance_stage', ...) }}`; proven via new `tests/test_ui_layout_regressions.py::test_leads_and_handoffs_action_urls_respect_a_reverse_proxy_path_prefix` |

### A.4 — Bookings

| Test | Method | Result | Evidence |
|---|---|---|---|
| Create booking draft via agent flow, lands in CRM | Automated | Pass | `tests/test_golden_transcript_regressions.py` (booking-draft-write tests), cross-referenced against real Postgres writes (see Bug 7 note in that file: a real booking BK000002 was confirmed created against production Postgres during that investigation) |
| **Update booking status from CRM UI — the split-brain persistence fix** | Automated (SQLite) + source read + **live DB query** | **Code fix confirmed in place and unit-tested. NOT confirmed live in production — see flag below.** | `apps/api/app/routes/bookings.py:376-423` now mutates the SQLAlchemy `TripBooking`/`BookingStatusHistory` objects directly instead of routing through the legacy SQLite-backed `UnifiedCRMService`; `tests/test_employee_followup_workspace.py` (25 tests incl. this file + traveler-management, all pass) proves it end-to-end against a real HTTP route + SQLite DB. **Live DB query against production `booking_status_history` finds zero rows with `change_source='crm-ui'`, ever** — meaning no real admin session has exercised this exact fix in production since it shipped. Could not log in to test this myself: only a scrypt password hash is available in `.env` (`CRM_ADMIN_PASSWORD_HASH`), no plaintext credential. **Flagged as an open verification item requiring a human admin to click through once.** |
| Delete a booking, with/without dependent `booking_status_history` rows | Automated | Pass | `tests/test_phase4_traveler_management.py`, `tests/test_phase3_booking_lifecycle.py` |
| Assign/reassign booking to employee | Automated | Pass | `tests/test_employee_followup_workspace.py` |

### A.5 — Trips

| Test | Method | Result | Evidence |
|---|---|---|---|
| Create/view/edit a trip | Automated | Pass | `tests/test_trip_delete_and_sort.py` and related |
| Delete blocked with real bookings; cleans dangling refs otherwise | Automated | Pass | `tests/test_trip_delete_and_sort.py` — SQLite-only |
| Trip Inventory sorting (Open trips first) | Automated | Pass | Same file |

### A.6 — Employees (admin-only)

| Test | Method | Result | Evidence |
|---|---|---|---|
| Create employee per role, confirm each can log in | Automated | Pass | `tests/test_admin_login_protection.py::test_newly_created_employee_can_log_in_with_role_scoped_access` — this was a real, deep bug earlier this session (`_employee_login_match()` fallback fix); test exercises a real login POST, not just a DB row check. **Not independently re-verified with live production credentials** (would require creating a real employee account in production, which this pass did not do without explicit sign-off). |
| Delete employee — blocked for self-delete and active leads/bookings | Automated | Pass | Same file |

### A.7 — Handoff Queue

| Test | Method | Result | Evidence |
|---|---|---|---|
| Real handoff via agent visible across concurrent admin sessions (Redis/eventlet fix) | Automated (structural) + **live reproduction** | Redis/eventlet mechanics: pass (structural only, no real Redis server in test). **The exact "escalate → visible in a second concurrently-logged-in admin session" scenario has never been explicitly tested, automated or live, per the discovery report's own account.** Escalation-to-handoff itself: confirmed live (below). | `tests/test_redis_shared_state.py` (structural); ground-truth escalate test below |
| `escalate`/`esclate` and Arabic phrasings trigger real handoff | **Automated + live reproduction + DB query** | **Pass, confirmed three ways** | Automated: `tests/test_golden_transcript_regressions.py::test_escalate_request_during_empty_trip_results_reaches_manual_handoff`, parametrized over `["escalate me", "esclate me", "can you escalate this"]`. **Live**: sent `"esclate me please"` to a fresh session on the deployed agent (`https://demos.nanovate.io/rahma-agent/`) — response returned `toolsUsed: ["create_handoff"]`, `write_result_contract.status: "created"`, handoff id `H-00037953`, customer reply correctly named the "Operations Team" persona. **DB query**: `SELECT * FROM handoff_queue WHERE handoff_id='H-00037953'` confirms the row is genuinely persisted in production Postgres (`status='Pending'`, `priority='High'`, created_at matches). |
| Two consecutive escalate attempts don't collide on ID generation | Automated | Pass | `tests/test_postgres_agent_bridge.py::test_create_handoff_case_does_not_collide_with_the_exact_live_non_conforming_rows` — a literal repro of a real prior incident, confirmed fixed against Postgres |
| `/admin/handoffs/pending` resolves under `/rahma-crm/` prefix | **Live source check** | **Fixed, confirmed live** | Both `base.html:818` and `admin/handoffs.html:1440` now read `fetch('{{ url_for("handoffs.pending_count") }}')` — verified by direct grep of the live source, not assumed from the commit message |
| **NEW bugs found + fixed:** the SAME kanban board's three "update handoff status" fetch calls (`submitResolution`, `quickAssign`, the drag-and-drop handler) still built their URL as a hardcoded `` `/admin/handoffs/${handoffId}` `` string — missed by the `/admin/handoffs/pending` fix because that only touched the polling call, not these three | Automated (new regression test) | **Fixed** | `apps/api/app/templates/admin/handoffs.html` — added a `HANDOFF_UPDATE_URL_TEMPLATE` built via `url_for()` with a placeholder, substituted at runtime since `handoffId` is only known client-side; proven via the same new test as the leads fix above |

---

## Part B — AI Agent (`services/ai_agent`)

### B.1 — Core conversation flow

| Test | Method | Result | Evidence |
|---|---|---|---|
| New-traveler intake incl. validation rejections | Automated | Pass | `tests/test_golden_transcript_regressions.py` |
| Returning traveler skips known fields | Automated | Pass | Same file + `test_postgres_agent_bridge.py` |
| Minor/guardian branch, incl. real DB persistence | Automated | Pass | `tests/test_guardian_consent_workflow.py` — `test_set_guardian_consent_and_lead_flag_persist_to_real_db` and `test_record_guardian_consent_verifies_against_a_real_db_read` both re-read via a **fresh DB connection** after the write, not just trusting the chat reply. Age-boundary (17 vs 18), new-vs-on-file traveler, and unverified-write rejection all covered. |

### B.2 — Trip selection

| Test | Method | Result | Evidence |
|---|---|---|---|
| Local/international trip search, incl. empty-results honesty | Automated | Pass | `test_golden_transcript_regressions.py` |
| Exploratory/hypothetical phrasing doesn't corrupt session state | Automated (exact original transcript) | Pass | `tests/test_exploratory_question_safety_net.py` — 1st contradiction gets a grounding recovery reply, 2nd consecutive one auto-escalates, a clean reply in between resets the strike counter |

### B.3 — Booking flow

| Test | Method | Result | Evidence |
|---|---|---|---|
| Room/gender/headcount/flight branching | Automated | Pass | `test_golden_transcript_regressions.py` |
| Passport collection, international-only, expiry validation | Automated | Pass | Same file + `test_phase6_passport_attachments.py` |
| **Confirm-synonym coverage gap** — `_AFFIRMATIVE_REPLIES` defines 16 accepted synonyms, but only `"yes"` and `"confirm"` had ever been exercised by a test | **Automated — gap closed this pass** | **Fixed: all 16 now proven** | Added `tests/test_golden_transcript_regressions.py::test_every_affirmative_reply_synonym_confirms_the_booking`, parametrized over the 14 previously-untested synonyms (`y, ok, okay, sure, book it, go ahead, تمام, ماشي, موافق, ايوه, أيوه, نعم, اوكي, اوكى`). **14/14 passed on first run** — no code fix needed, the confirmation-handling logic already worked correctly for all of them; the gap was purely in test coverage, now closed. |

### B.4 — Escalation / human handoff

Covered in Part A.7 above (escalate/esclate live + DB confirmed; ID-collision automated).

### B.5 — Write-status integrity

| Test | Method | Result | Evidence |
|---|---|---|---|
| Honest failure messages, no fabricated success, for every write action | Automated | Pass | `tests/test_write_result_boundary.py`, `tests/test_write_tool_executor_verification.py`, `tests/test_dual_access_mode_dispatch.py` |
| Diagnostic logging on write failure | Automated + source read | Pass | `write_tool_executor.py`'s single `execute()` dispatch choke-point now logs real exceptions with detail (Path A/B fix, 2026-08-08). Confirmed **zero remaining direct `self.service.<method>()` bypasses** of this dispatch pattern anywhere in `services/ai_agent/ai_agent_app/agent/` — the one known historical bypass (guardian-consent) is already remediated and regression-tested. |

### B.6 — Identity/meta questions

| Test | Method | Result | Evidence |
|---|---|---|---|
| "Who made you" etc., English + Arabic, mid-flow | Automated | Pass | `tests/test_agent_identity_policy.py` — 9 tests incl. mixed-language, provider-question, prompt-injection resistance, and persistence through an in-progress booking. Live-transcript-pinned Arabic phrasings included. Not separately re-verified live in this pass; automated coverage is already thorough (asserts the model is never even called). |

### B.7 — Concurrency / load

| Test | Method | Result | Evidence |
|---|---|---|---|
| `scripts/concurrent_load_test.py` against live deployment | **Not run this pass** | **Open — flagged, not executed** | The script requires `--base-url` pointed at the live app to answer the real multi-worker question (its own docstring recommends running it twice — once local, once live — since worker/process behavior differs). Running 10 concurrent sessions × several messages against production wasn't done in this pass without checking rate limits first (`/api/session` is limited to 20/min, `/message` to 30/min) — running it as configured could trip those limits and produce a false failure signal rather than a true one. **Recommend running deliberately, sized under those limits, as a separate step — not silently assumed passing.** |
| Session state doesn't fragment across the (single-worker) agent process | Automated + source read | Pass | `rahma-agent` pm2 config confirmed still pinned to 1 worker; per-process in-memory `SessionFlowManager` is therefore safe by construction as long as that stays true |

---

## Traveler-delete-then-reuse-phone-number — dedicated section

This was investigated exhaustively earlier in this session, live against production, before this QA pass began. Summary (full detail in `ravel_agent_master_discovery_report.md`, this pass's entry):

- **Not currently reproducible.** A fresh, genuinely new session (confirmed via the appearance of the opening greeting, which can only come from the server's own response to a real `POST /api/session` — verified no `localStorage`/`sessionStorage`/cookie-based session caching exists anywhere in `services/ai_agent/ai_agent_app/web/static/app.js`) sent the user's exact originally-affected phone number to the live deployed agent: it correctly returned `"not_found"`.
- Direct read-only Postgres queries confirm zero rows for that phone number in `travelers`, `leads`, or `interactions` — the delete was clean.
- The traveler-delete route itself (`apps/api/app/routes/travelers.py:544-599`) was independently re-audited: every table with a real FK to `travelers.traveler_id` is covered, both delete-button JS paths surface real failures via `alert()`, and a live Postgres query found **zero duplicate traveler rows** sharing any phone-lookup column across all (then-541) travelers.
- **Most likely explanation**: the original report coincided with a ~17.5-hour window (2026-08-07 14:07 → 2026-08-08 07:45) during which the dashboard-list delete button was newly-added but still had a hardcoded path bug (fixed in `e3d7504`) layered on top of a separately-being-fixed FK/cascade gap (fixed in `cdda723`) — a genuinely ambiguous "did this actually delete" period. Both are now fixed.
- **A real, separate latent bug was found and left unfixed pending confirmation**: `services/ai_agent/ai_agent_app/agent/tool_calling_runtime.py:867-930` has a session-scoped identity cache that never re-checks (or actively discards a fresh `not_found` result) once a session has verified a traveler once — a genuine defect if a traveler is deleted *mid-conversation* in the same session, just not what explains the original report. **Recommend hardening as defense-in-depth, tracked separately from this specific investigation.**

---

## Cross-cutting checks (Part C)

| Check | Method | Result |
|---|---|---|
| Full test suite | Automated | See final count below |
| Remaining hardcoded `/static/` or unprefixed absolute paths | **Live grep, repo-wide** | Found and fixed 4 new instances (leads advance-stage, 3× handoffs update) — see Parts A.3/A.7 above. Re-grepped after fixing: no further `fetch('/...` / `fetch("/...` / `` fetch(`/... `` matches remain in any CRM template. |
| Remaining direct `self.service.<method>()` bypasses of `write_tool_executor.py`'s dispatch pattern | Source read (full file + directory grep) | **Zero found.** Every direct call is internal to a legitimate `_execute_<action>` handler body, only reachable via `execute()`. The one historical bypass (guardian consent) is already fixed and regression-tested. |
| `gunicorn`/`eventlet` pinning | Direct file read | Confirmed: `gunicorn==21.2.0` (exact pin, with an inline comment explaining why — 26.0.0 removed the eventlet worker entry point) and `eventlet>=0.36.0`, consistent in both `requirements.txt` and `apps/api/requirements.txt`. |
| Mojibake / garbled characters (reported via screenshot) | **Live investigation + fix + verification** | **Fixed.** Root cause: a UTF-8→misread-as-Windows-1252→re-saved-as-UTF-8 double-encoding, confined to exactly one file (`apps/api/app/templates/bookings/detail.html`), 26 corrupted instances across 21 lines, all four decorative characters (`←`, `→`, `—`, `·`), introduced in commit `672ee220` (2026-08-03). Same root-cause class as a previously-fixed incident in the AI agent's own Arabic prompt strings (commit `ae665552`), but that fix's mojibake guard only covers agent-generated chat text, not Jinja templates — this is why it wasn't caught. Fixed using precise Python-based codepoint replacement (never a shell heredoc, per this project's established caution around multi-byte text) and verified: no mojibake marker strings remain anywhere in the repo. |

---

## Final full-suite count

**840 passed, 0 failed, 55 subtests passed** (`pytest -q`, full run, 8m56s). This includes the 15 tests added during this pass (1 hardcoded-path-prefix regression test covering both the leads and handoffs fixes, 14 confirm-synonym parametrized cases) on top of the 825 that existed at the start of this pass. Zero regressions.
