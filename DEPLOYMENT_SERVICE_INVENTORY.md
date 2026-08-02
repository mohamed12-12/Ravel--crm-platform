# Deployment Service Inventory

Date: 2026-08-02
Branch: `port-write-safety-idempotency`
Worktree: `C:\tmp\rahma-port-write-safety`

## Required Services

| Service | Location | Purpose | Local URL | Production Notes |
| --- | --- | --- | --- | --- |
| CRM/API Flask service | `apps/api` | CRM dashboard, traveler/lead/booking/handoff APIs, auth/admin routes | `http://127.0.0.1:5000` | Run behind WSGI/ASGI production server, not Flask dev server. |
| AI Agent demo service | `services/ai_agent/ai_agent_app` | Latest AI demo UI, Gemini tool-calling runtime, controlled write tools | `http://127.0.0.1:5101` | Requires `AI_AGENT_MODE=tool_calling`; safe mode keeps global router enforce off. |
| Admin web frontend | `apps/admin-web` | React/Vite admin frontend | `http://127.0.0.1:3101/index.html` in dev | Build output: `apps/admin-web/dist`. Serve as static assets in production. |
| Middleware service | `apps/middleware` | Express middleware/API facade | `http://127.0.0.1:3100/api/health` | Build output: `apps/middleware/dist`; run with `npm start` after build. |
| Dev gateway | `scripts/dev_gateway.py` | Local route aliases for API/demo | `http://127.0.0.1:8080/p5001/`, `http://127.0.0.1:8080/p3000/` | Local-only convenience service, not required for production if real routing exists. |
| Database/storage | `apps/api/instance/rahma_traveler_dev.db` for local QA | SQLite CRM source during local QA | local file | Production should use managed DB path/URL, not demo/test DB. |
| Upload storage | `apps/api/instance/uploads` | Passport/document upload storage | local file path | Production must use durable storage with backups and access controls. |
| Excel compatibility | `tests/fixtures/operational_source_workbook.xlsx` for tests; `.tmp-run/live-runtime.xlsx` for local demo | Legacy/demo compatibility only | local files | Production should not write source workbook; DB/API must be source of truth. |
| Gemini provider | `services/ai_agent/llm` | LLM/tool-calling model provider | external | Requires real `GEMINI_API_KEY` and valid `GEMINI_MODEL`. |
| Meta/webhook integrations | `services/instagram`, env tokens | Optional production messaging integrations | external | Must remain disabled until tokens/webhook URL are configured. |

## Verified Local URLs

- AI demo UI: `http://127.0.0.1:5101/`
- AI health: `http://127.0.0.1:5101/api/health`
- CRM dashboard: `http://127.0.0.1:5000/admin/dashboard`
- Middleware health: `http://127.0.0.1:3100/api/health`
- Admin web: `http://127.0.0.1:3101/index.html`
- Gateway demo alias: `http://127.0.0.1:8080/p5001/api/health`
- Gateway CRM alias: `http://127.0.0.1:8080/p3000/admin/dashboard`

## Runtime Boundaries

- Customer/demo chat uses the AI agent service, not old root `app/` UI.
- CRM/admin routes are separate from agent tool policy.
- Global read/router enforcement remains disabled; write-only enforcement can be enabled by `AGENT_WRITE_TOOL_ENFORCEMENT=true`.
- Direct booking routes are guarded and cannot bypass confirmation/idempotency.
