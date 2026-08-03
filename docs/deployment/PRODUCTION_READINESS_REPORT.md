# Production Readiness Report

Date: 2026-08-02
Worktree: `C:\tmp\rahma-port-write-safety`
Branch: `port-write-safety-idempotency`

## Final Status

`READY_EXCEPT_EXTERNAL_SECRETS`

The latest product stack is ready for a controlled deployment rehearsal and controlled client demo after production secrets/env values are supplied through a secret manager. It is not a blind production go-live until secrets are rotated/configured, production DB/storage are selected, and broad legacy tests are either migrated or excluded from CI.

## What Changed In This Readiness Pass

- Added production startup validation for AI agent config in `services/ai_agent/ai_agent_app/config.py`.
- Added production startup validation for CRM/API config in `apps/api/app/config.py`.
- Added `tests/test_deployment_production_validation.py`.
- Fixed Gemini tool argument validation so schema-declared `object`/`array` fields such as `room_requirements` are accepted.
- Wrapped controlled write-tool execution failures into safe failed WriteResult responses instead of surfacing temporary runtime errors.
- Added capacity-specific safe booking failure messaging.
- Added controlled capacity/duplicate/policy handoff approval in `services/ai_agent/validation/action_validator.py`.
- Isolated UI layout test workbook paths to tracked fixtures.
- Created deployment docs/runbooks/checklists.

## Environment Used For QA

```env
APP_ENV=development
AI_AGENT_MODE=tool_calling
AI_PROVIDER=gemini
AGENT_TOOL_ROUTER_MODE=dry_run
AGENT_WRITE_TOOL_ENFORCEMENT=true
CRM_ACCESS_MODE=shared_service
DEMO_RESET_ON_START=false
APP_DEBUG=false
APP_USE_RELOADER=false
```

Secrets were loaded only from a private local environment for live Gemini smoke and were not printed or committed.

## Services Verified

| URL | Result |
| --- | --- |
| `http://127.0.0.1:5101/api/health` | 200 |
| `http://127.0.0.1:5101/` | 200 |
| `http://127.0.0.1:5000/admin/dashboard` | 200 |
| `http://127.0.0.1:3100/api/health` | 200 |
| `http://127.0.0.1:3101/index.html` | 200 |
| `http://127.0.0.1:8080/p5001/api/health` | 200 |
| `http://127.0.0.1:8080/p3000/admin/dashboard` | 200 |

## Live Runtime Smoke Results

| Scenario | Result | Notes |
| --- | --- | --- |
| New session loads | Pass | Latest AI demo UI/API healthy. |
| English greeting | Pass | Stable phone-first response, no fallback. |
| Arabic greeting | Pass | Natural Arabic phone-first response, no fallback. |
| Returning traveler phone lookup | Pass | Reached trip type selection. |
| Unknown/new traveler flow | Pass | Asked for full three-part name safely. |
| `yes` too early | Pass | No write. |
| `book` too early | Pass | No write. |
| Trip search/selection | Pass | Local demo trip selectable. |
| Booking confirmation/retry | Pass | Repeated confirmation produced no duplicate booking. |
| Direct booking bypass | Pass | Unauthenticated direct route returned 401. |
| Human handoff request | Pass | Handoff created safely. |
| Capacity review | Pass after fix | Controlled handoff persisted for unavailable room path. |

## Automated Test Results

### Deployment-Critical Python Gate

Command:

```powershell
python -m pytest tests/test_deployment_production_validation.py tests/test_port1_write_safety_idempotency.py tests/test_port2_write_result_response_gating.py tests/test_port3_response_guard.py tests/test_port4_state_tool_routing_audit.py tests/test_port4_7_write_enforcement_simulation.py tests/test_port4_8_write_only_enforcement_flag.py tests/test_phase2_gemini_tool_loop.py tests/test_phase3_booking_lifecycle.py tests/test_phase4_business_validation.py tests/test_phase5_gemini_write_tools.py tests/test_agent_conversation_reliability.py tests/test_security_hardening.py tests/test_data_authority.py tests/test_ui_layout_regressions.py -q
```

Result: `219 passed, 31 subtests passed`.

### TypeScript / Build

Commands:

```powershell
npm run typecheck --workspaces --if-present
npm run build
```

Results:

- Typecheck: passed for admin web and middleware.
- Root build: passed, including admin web Vite build, middleware TypeScript build, and Python compileall.

### Broad Python Suite

Command:

```powershell
python -m pytest -q
```

Result: `526 passed, 8 failed, 31 subtests passed`.

Failure classification:

| Failure Area | Classification | Deployment Impact |
| --- | --- | --- |
| Old unknown traveler expected text | Legacy/outdated assertion | Current runtime intentionally asks for new traveler name safely. |
| Old invalid Gemini fallback wording | Legacy/outdated assertion | Response guard now uses safer completion fallback. |
| Old coordinator call-count expectation | Legacy/outdated architecture assertion | Current phone-first path can answer before coordinator call. |
| Mojibake Arabic fixture tests | Legacy/test-data issue | Live Arabic greeting passed with real Arabic. |
| Test expecting customer-visible `CRM` term | Legacy/outdated assertion | Response guard sanitizes internal/customer-hostile terms. |

Recommendation: create an active release marker/gate for latest product tests and migrate or mark these legacy assertions before requiring full-suite zero failures in CI.

## Known Runtime Notes

- Global router enforce remains off and must stay off.
- Read-tool enforcement is not ready.
- Safe mode for controlled demo should keep `AGENT_WRITE_TOOL_ENFORCEMENT=true`.
- The current local demo DB was mutated by live QA booking/handoff smoke. Do not commit runtime DB/import-staging changes unless intentionally refreshed.
- `npm run build` is valid but noisy because `compileall .` walks temp folders and `node_modules`; a later cleanup should scope it.

## Folder / Commit Hygiene

Keep for source PR:

- Safety/runtime source changes under `services/ai_agent`, `services/crm`, and `apps/api`.
- Targeted safety tests under `tests/`.
- Deployment docs in the repo root.

Do not commit local QA artifacts unless explicitly intended:

- `apps/api/instance/rahma_traveler_dev.db`
- `apps/api/instance/import-staging/*.json`
- `.tmp-run/*`
- `.tmp-test-*`
- `.pytest_cache`
- `apps/admin-web/dist`
- `apps/middleware/dist`
- `node_modules`
- any private `.env`

## Remaining Blockers Before Real Production

1. Provide production secrets through a secret manager and rotate any key pasted into chat/terminal history.
2. Select production DB/storage and confirm no demo/test DB paths are used.
3. Decide whether broad legacy tests should be migrated or excluded from production CI.
4. Run a final clean-database manual QA pass with production-like seed data.
5. Configure Meta/webhook tokens only if those integrations are in launch scope.

## Go / No-Go

- Controlled client demo on latest product branch: Go, with safe mode and private secrets.
- Production deployment rehearsal: Go, once production env values are supplied.
- Real production launch: Conditional Go after external secrets, DB/storage, backups, and CI test policy are finalized.
