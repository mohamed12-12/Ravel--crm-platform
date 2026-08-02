# Deployment Runbook

Date: 2026-08-02

## Install

```powershell
cd C:\tmp\rahma-port-write-safety
npm install
python -m pip install -r apps/api/requirements.txt
```

If Python dependencies are managed globally/venv in your environment, activate that environment before running tests or services.

## Local Safe Demo Start

Preferred local launcher:

```powershell
cd C:\tmp\rahma-port-write-safety
$env:AGENT_TOOL_ROUTER_MODE="dry_run"
$env:AGENT_WRITE_TOOL_ENFORCEMENT="true"
$env:AI_AGENT_MODE="tool_calling"
.\scripts\start-rahma-dev.ps1 -Python C:\Python314\python.exe -ApiPort 5000 -DemoPort 5101 -GatewayPort 8080 -NoNgrok
```

Start admin web and middleware separately when needed:

```powershell
cd C:\tmp\rahma-port-write-safety\apps\admin-web
npm run dev -- --host 127.0.0.1 --port 3101

cd C:\tmp\rahma-port-write-safety\apps\middleware
npm run dev
```

## Build

```powershell
cd C:\tmp\rahma-port-write-safety
npm run typecheck --workspaces --if-present
npm run build
```

Note: `npm run build` currently runs `python -m compileall .`, which walks `node_modules` and temp folders. It passes, but it is noisy. A later cleanup can scope compileall to `apps`, `services`, `scripts`, and `tests`.

## Health Checks

```powershell
Invoke-WebRequest http://127.0.0.1:5101/api/health
Invoke-WebRequest http://127.0.0.1:5101/
Invoke-WebRequest http://127.0.0.1:5000/admin/dashboard
Invoke-WebRequest http://127.0.0.1:3100/api/health
Invoke-WebRequest http://127.0.0.1:3101/index.html
```

## Critical Regression Gate

```powershell
python -m pytest tests/test_deployment_production_validation.py tests/test_port1_write_safety_idempotency.py tests/test_port2_write_result_response_gating.py tests/test_port3_response_guard.py tests/test_port4_state_tool_routing_audit.py tests/test_port4_7_write_enforcement_simulation.py tests/test_port4_8_write_only_enforcement_flag.py tests/test_phase2_gemini_tool_loop.py tests/test_phase3_booking_lifecycle.py tests/test_phase4_business_validation.py tests/test_phase5_gemini_write_tools.py tests/test_agent_conversation_reliability.py tests/test_security_hardening.py tests/test_data_authority.py tests/test_ui_layout_regressions.py -q
```

Latest result: `219 passed, 31 subtests passed`.

## Broad Suite

```powershell
python -m pytest -q
```

Latest result: `526 passed, 8 failed, 31 subtests passed`.

The 8 failures are classified in `PRODUCTION_READINESS_REPORT.md` as legacy/outdated assertion failures around old fallback text, coordinator call expectations, and mojibake fixtures. They did not reproduce as live runtime failures.

## Backup Before Deploy

- Snapshot production DB.
- Backup upload directory or object-storage bucket.
- Backup current deployed environment variables through the secret manager/versioned deployment platform.
- Export current deployment version/commit SHA.

## Production Start Notes

- Do not use Flask dev server in production.
- Serve CRM/API behind a production server and reverse proxy.
- Serve admin web static `dist` behind the frontend host/CDN.
- Run middleware from compiled `apps/middleware/dist/index.js`.
- Keep `AGENT_TOOL_ROUTER_MODE=dry_run` until read-tool enforcement is separately audited.
- Keep `AGENT_WRITE_TOOL_ENFORCEMENT=true`.
