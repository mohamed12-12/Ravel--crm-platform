# Env Runtime Verification Report

Date: 2026-08-02
Worktree tested: `C:\tmp\rahma-port-write-safety`
Branch: `port-write-safety-idempotency`

## Scope

Used existing local env files to run and verify the latest product stack without printing or copying secrets into docs.

Safety constraints followed:

- No secret values printed.
- No `.env` files committed or copied.
- No push performed.
- Runtime reports mention only env file paths and variable presence/missing status.
- Safe demo mode remained active: `AGENT_TOOL_ROUTER_MODE=dry_run`, `AGENT_WRITE_TOOL_ENFORCEMENT=true`.

## Env Files Detected

Latest product worktree:

- `C:\tmp\rahma-port-write-safety\.env.example`
- `C:\tmp\rahma-port-write-safety\.env.local.example`
- `C:\tmp\rahma-port-write-safety\apps\api\.env.example`

Private/local Desktop checkout:

- `C:\Users\Mo\Desktop\nanovate tech\Projects\Rahma Traveler\.env`
- `C:\Users\Mo\Desktop\nanovate tech\Projects\Rahma Traveler\.env.example`
- `C:\Users\Mo\Desktop\nanovate tech\Projects\Rahma Traveler\rahma-traveler\.env.example`
- `C:\Users\Mo\Desktop\nanovate tech\Projects\Rahma Traveler\rahma-traveler\env.example`

## Env Files Used

| Service | Env input used | Notes |
| --- | --- | --- |
| AI agent | `C:\Users\Mo\Desktop\nanovate tech\Projects\Rahma Traveler\.env` plus process-local overrides | Latest worktree has no private `.env`; used the existing private local file without printing values. |
| CRM/API | `C:\Users\Mo\Desktop\nanovate tech\Projects\Rahma Traveler\.env` plus process-local DB/path overrides | Forced latest worktree DB path so old root app data paths were not used. |
| Admin web | Process env plus package defaults | No service-specific private env file found. Dev server loaded normally. |
| Middleware | Process env plus `PORT=3100`, `FLASK_API_URL=http://127.0.0.1:5000/api/crm` | No service-specific private env file found. |

## Required Variable Presence

Private local `.env` presence summary, values masked:

| Variable | Present in private `.env` | Runtime value source |
| --- | --- | --- |
| `AI_AGENT_MODE` | yes | Overridden to `tool_calling` for latest runtime. |
| `AI_PROVIDER` | yes | Private env. |
| `GEMINI_API_KEY` | yes | Private env; value not printed. |
| `GEMINI_MODEL` | yes | Private env. |
| `AGENT_TOOL_ROUTER_MODE` | no | Supplied as process override: `dry_run`. |
| `AGENT_WRITE_TOOL_ENFORCEMENT` | no | Supplied as process override: `true`. |
| `CRM_ACCESS_MODE` | no | Supplied as process override: `shared_service`. |
| `CRM_API_BASE_URL` | no | Supplied as process override for compatibility. |
| `DATABASE_URL` | yes | Overridden to latest worktree DB path. |
| `RAHMA_SYSTEM_DB_PATH` | yes | Overridden to latest worktree DB path. |
| `APP_SECRET_KEY` | yes | Private env; value not printed. |
| `SECRET_KEY` | yes | Private env; value not printed. |
| `CRM_API_TOKEN` | no | Not required for local `shared_service`; required if production/API integration mode is used. |
| `TRAVELER_UPLOAD_ROOT` | yes | Overridden to latest worktree uploads path. |
| `AI_AGENT_UPLOAD_ROOT` | yes | Overridden to latest worktree uploads path. |
| `APP_DEBUG` | yes | Overridden to `false`. |
| `APP_USE_RELOADER` | yes | Overridden to `false`. |
| `DEMO_RESET_ON_START` | yes | Overridden to `false`. |

## Services Started / Verified

The target ports were already listening from the latest-product dev stack, so the verification reused those processes rather than restarting unnecessarily.

| Service | URL | Result |
| --- | --- | --- |
| AI demo health | `http://127.0.0.1:5101/api/health` | 200 |
| AI demo UI | `http://127.0.0.1:5101/` | 200 |
| CRM dashboard/API | `http://127.0.0.1:5000/admin/dashboard` | 200 |
| Middleware health | `http://127.0.0.1:3100/api/health` | 200 |
| Admin web | `http://127.0.0.1:3101/index.html` | 200 |
| Gateway AI alias | `http://127.0.0.1:8080/p5001/api/health` | 200 |
| Gateway CRM alias | `http://127.0.0.1:8080/p3000/admin/dashboard` | 200 |

## Runtime Verification

Gemini/tool-calling initialization:

- Live session payload reported `runtime_mode=tool_calling`.
- Recent logs showed `Gemini response received` events.
- Recent logs showed tool-calling workflow markers.
- Recent logs showed controlled `Write audit` events.
- No secret values were printed from logs.

CRM/API connection:

- AI health returned active DB path under latest worktree.
- AI health returned nonzero counts for travelers, trips, bookings, and leads.
- Returning traveler lookup succeeded for a known local QA phone and reached `trip_type_required`.

## Live Smoke Scenarios

| Scenario | Result | Notes |
| --- | --- | --- |
| English greeting | pass | Stable phone-first response, no fallback/internal leak. |
| Arabic greeting | pass | Stable Arabic phone-first response, no fallback/internal leak. |
| `yes` too early | pass | No write; stayed identity-required. |
| `book` too early | pass | No write; stayed identity-required. |
| Returning traveler lookup | pass | Verified CRM profile and asked trip type. |
| Booking repeat safety | pass | Repeated confirmation did not create a duplicate booking. Current DB routed the chosen option to safe capacity review. |
| Human handoff request | pass | Handoff created safely. |
| Direct booking bypass | pass | Unauthenticated direct booking route returned 401. |

## Tests Run

Deployment-critical Python gate:

```powershell
python -m pytest tests/test_deployment_production_validation.py tests/test_port1_write_safety_idempotency.py tests/test_port2_write_result_response_gating.py tests/test_port3_response_guard.py tests/test_port4_state_tool_routing_audit.py tests/test_port4_7_write_enforcement_simulation.py tests/test_port4_8_write_only_enforcement_flag.py tests/test_phase2_gemini_tool_loop.py tests/test_phase3_booking_lifecycle.py tests/test_phase4_business_validation.py tests/test_phase5_gemini_write_tools.py tests/test_agent_conversation_reliability.py tests/test_security_hardening.py tests/test_data_authority.py tests/test_ui_layout_regressions.py -q
```

Result: `219 passed, 31 subtests passed`.

Node workspace typecheck:

```powershell
npm run typecheck --workspaces --if-present
```

Result: passed for `apps/admin-web` and `apps/middleware`.

## Remaining Blockers / Notes

- The latest product worktree does not contain a private `.env`; local QA depends on the private Desktop `.env` plus process overrides.
- The private `.env` does not include the new safety flags, so they must be added to the real local/deployment env before handoff: `AGENT_TOOL_ROUTER_MODE=dry_run`, `AGENT_WRITE_TOOL_ENFORCEMENT=true`.
- `CRM_API_TOKEN` is missing; this is acceptable for local `shared_service`, but required if production or integration API mode is enabled.
- Do not enable global `AGENT_TOOL_ROUTER_MODE=enforce`; read-tool enforcement remains not ready.
- The current runtime DB has been mutated by QA smoke flows. Do not commit DB/import-staging runtime artifacts unless intentionally refreshing fixtures.
- Any secret that was ever pasted into chat, terminal history, screenshots, or docs should be rotated before production.
