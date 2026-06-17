# QA Report

Date: 2026-06-16  
Role: Senior QA Engineer + Senior Software Architect  
Scope: frontend, middleware, Flask CRM, AI-agent demo, database/migrations, environment, tests, and cleanup candidates.  
Rule followed: no business logic or feature behavior changes.

## Executive Result

The project is not production-ready. Build and TypeScript checks pass, and basic smoke routes respond, but the Python regression suite fails with 19 failures. There is no real lint command, no real frontend/middleware automated tests, npm audit reports high-severity vulnerabilities, and Instagram integration remains placeholder-only.

## Passed Tests

- `npm install`: passed.
- `npm run build`: passed.
- `npm run typecheck`: passed.
- `python -m compileall .`: passed.
- AI-agent Flask smoke:
  - `GET /api/health`: 200
  - `GET /api/bootstrap`: 200
  - `POST /api/session`: 200
  - `GET /webhook` without Meta params: 404, current placeholder behavior
- CRM Flask smoke with isolated SQLite:
  - `GET /`: 302 to `/admin/dashboard`
  - `GET /admin/`: 302 to `/admin/dashboard`
  - `GET /travelers/`: 200
  - `GET /api/crm/duplicates`: 200 with empty duplicate list
- Middleware HTTP smoke:
  - `GET /api/health`: 200
- Admin web production preview smoke:
  - `GET /`: 200 and served React root

## Failed Tests

- `npm test`: failed because `python -m pytest tests` failed.
- `python -m pytest tests`: failed with 19 failed, 29 passed, 38 warnings.
- `npm run lint`: failed because no `lint` script exists.
- `npm audit --audit-level=high`: failed with 2 high-severity vulnerabilities.

## Bugs And Findings

### Critical: Regression Suite Failing

Severity: Critical  
Impact: Cannot certify existing business behavior before production.

Reproduction:

```bash
python -m pytest tests
```

Observed:

- 19 failed, 29 passed.

Recommended fix:

- Fix behavior/test drift in a dedicated stabilization phase.
- Do not proceed to production integration until this suite is green or intentionally rebaselined with product approval.

### Critical: `SystemServiceSettings` Test/Constructor Drift

Severity: Critical  
Affected tests:

- `tests/test_phase10_full_logic_lock.py`: 14 failures
- `tests/test_phase9_sync_safety.py`: 1 failure

Reproduction:

```bash
python -m pytest tests/test_phase10_full_logic_lock.py tests/test_phase9_sync_safety.py
```

Observed:

- `TypeError: SystemServiceSettings.__init__() missing 2 required positional arguments: 'google_sheet_id' and 'google_application_credentials'`

Recommended fix:

- Decide whether those settings should have safe defaults or whether all tests/callers must pass explicit values.
- Keep behavior unchanged until ownership confirms the intended config contract.

### High: Phase 1 Agent Trip Availability Mismatch

Severity: High  
Affected test:

- `tests/test_phase1_readonly_agent.py::Phase1ReadonlyAgentTests::test_build_agent_response_handles_phase1_cases`

Reproduction:

```bash
python -m pytest tests/test_phase1_readonly_agent.py
```

Observed:

- Expected one open local trip for a new customer.
- Actual open trip count was zero.

Recommended fix:

- Review seeded workbook expectations against current trip filtering/date logic.
- Confirm whether test fixture or behavior is authoritative.

### High: Manual Lead Conflict And Blacklist Logic Mismatch

Severity: High  
Affected tests:

- `test_manual_lead_conflict_creates_handoff`
- `test_manual_lead_blacklist_creates_handoff_and_blocks`

Reproduction:

```bash
python -m pytest tests/test_phase5_manual_lead_agent_logic.py
```

Observed:

- Name conflict creates an extra traveler where test expects no new traveler.
- Blacklisted traveler lead does not set `handoff_required`.

Recommended fix:

- Review manual lead route behavior against the desired handoff/identity policy.
- Add route-level regression coverage after the contract is clarified.

### High: Demo Session Stage Contract Mismatch

Severity: High  
Affected test:

- `tests/test_phase8_demo_alignment.py::Phase8DemoAlignmentTests::test_demo_web_calls_crm_services_safely_and_creates_records`

Reproduction:

```bash
python -m pytest tests/test_phase8_demo_alignment.py
```

Observed:

- Expected `awaiting_intake`.
- Actual `awaiting_phone`.

Recommended fix:

- Confirm intended first session stage in the current agent UX.
- Update test or logic only after product decision.

### High: npm High-Severity Vulnerabilities

Severity: High  
Reproduction:

```bash
npm audit --audit-level=high
```

Observed:

- `esbuild <=0.28.0` via `vite <=6.4.2`
- Fix requires `npm audit fix --force`, which would install `vite@8.0.16` and may be breaking.

Recommended fix:

- Plan a dependency-upgrade task with frontend smoke and build verification.
- Do not force-upgrade inside a cleanup-only phase.

### Medium: No Lint Script

Severity: Medium  
Reproduction:

```bash
npm run lint
```

Observed:

- npm reports missing script `lint`.

Recommended fix:

- Add lint tooling in a separate standards phase after agreeing on ESLint/Prettier/Python lint stack.

### Medium: Real Frontend/Middleware Tests Missing

Severity: Medium  
Observed:

- `apps/admin-web` and `apps/middleware` test scripts only print placeholders.

Recommended fix:

- Add admin component tests, route/service tests, and API contract tests.

### Medium: Compile Command Walks Ignored Folders

Severity: Medium  
Observed:

- `python -m compileall .` traverses `.history`, `.tmp-test-workdirs`, and `node_modules`, creating noisy output and extra bytecode.

Recommended fix:

- Add a narrower script such as `python -m compileall apps services scripts tests demo_web`.

### Medium: Temporary SQLite Cleanup Lock On Windows

Severity: Medium  
Observed:

- First CRM smoke using `TemporaryDirectory()` failed cleanup because SQLite DB remained locked.

Recommended fix:

- Ensure tests/smokes call `db.session.remove()` and `db.engine.dispose()` before deleting temp SQLite files on Windows.

### High: Production Instagram Integration Not Ready

Severity: High  
Observed:

- Webhook verify/signature helpers exist, but real Instagram handling is not implemented.
- No durable queues, idempotency, token rotation, audit logs, monitoring, or human handoff workflow.

Recommended fix:

- Complete the production-readiness work listed in `docs/instagram-integration-plan.md` before connecting a real account.

## Command Log

| Command | Result |
| --- | --- |
| `git branch --show-current` | Passed: `cleanup/mvp-to-product-structure` |
| `git status --short --untracked-files=all` | Passed: large in-progress restructure status |
| `rg --files ...` inventory commands | Passed |
| `npm install` | Passed, with 2 high-severity audit findings |
| `npm run build` | Passed |
| `npm run typecheck` | Passed |
| `npm test` | Failed: pytest 19 failed, 29 passed |
| `python -m pytest tests` | Failed: 19 failed, 29 passed |
| `npm audit --audit-level=high` | Failed: 2 high-severity vulnerabilities |
| `npm run lint` | Failed: missing script |
| `python -m compileall .` | Passed |
| AI-agent Flask smoke test client | Passed core smoke routes |
| CRM Flask smoke test client | Passed after explicit DB cleanup |
| Middleware `GET /api/health` via local server | Passed |
| Admin web Vite preview smoke | Passed |
| Cleanup candidate `rg` scans | Passed |
| Generated artifact cleanup command | Passed: removed 31 generated folders |

## Assumptions

- The current branch state is intentionally mid-restructure and should not be reverted.
- Failing Python tests reflect existing behavior/test drift, not the QA documentation cleanup.
- Placeholder Instagram integration should remain placeholder-only in this phase.
- No claim is made that full business workflows are production-ready because the regression suite is failing.
