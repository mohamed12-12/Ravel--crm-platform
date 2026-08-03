# Deployment Environment Checklist

Date: 2026-08-02

## Safe Demo / Controlled Client Demo

Use these values for controlled demo mode:

```env
APP_ENV=development
AI_AGENT_MODE=tool_calling
AI_PROVIDER=gemini
GEMINI_API_KEY=<secret from secret manager or private local .env>
GEMINI_MODEL=<approved Gemini model>
AGENT_TOOL_ROUTER_MODE=dry_run
AGENT_WRITE_TOOL_ENFORCEMENT=true
CRM_ACCESS_MODE=shared_service
DEMO_RESET_ON_START=false
APP_DEBUG=false
APP_USE_RELOADER=false
```

## Production Required Variables

| Variable | Required | Rule |
| --- | --- | --- |
| `APP_ENV` | yes | `production` in production. |
| `FLASK_ENV` / `FLASK_CONFIG` | yes | Production config must not enable debug. |
| `APP_SECRET_KEY` | yes | Strong, non-default, from secret manager. |
| `SECRET_KEY` | yes | Strong, non-default, from secret manager. |
| `CRM_AUTH_ENABLED` | yes | Must not be false in production. |
| `CRM_API_TOKEN` | required for integration/API mode | Strong token from secret manager; never log. |
| `AI_AGENT_MODE` | yes | Must be `tool_calling` in production. |
| `AI_PROVIDER` | yes | `gemini` for current live LLM path. |
| `GEMINI_API_KEY` | yes for Gemini | Secret manager only. Do not commit. |
| `GEMINI_MODEL` | yes for Gemini | Use the approved deployed model name. |
| `AGENT_TOOL_ROUTER_MODE` | yes | Keep `dry_run`; do not enable `enforce` yet. |
| `AGENT_WRITE_TOOL_ENFORCEMENT` | yes | Recommended `true`. Required by production validation. |
| `DATABASE_URL` | yes | Production DB URL/path. Must not point at demo/test DB. |
| `RAHMA_SYSTEM_DB_PATH` | if SQLite/shared service | Production DB file path if SQLite is still used. |
| `TRAVELER_UPLOAD_ROOT` | yes | Durable upload storage. |
| `AI_AGENT_UPLOAD_ROOT` | yes | Durable upload storage for agent uploads. |
| `DEMO_RESET_ON_START` | yes | Must be `false` in production. |
| `SHEET_BACKEND` | if Excel/Sheets compatibility is active | DB/API source of truth preferred. Source workbook writes are disallowed. |
| `META_VERIFY_TOKEN` | if webhook enabled | Secret manager. |
| `META_PAGE_ACCESS_TOKEN` | if webhook enabled | Secret manager. |
| `META_APP_SECRET` | if webhook enabled | Secret manager. |
| `PUBLIC_WEBHOOK_URL` | if webhook enabled | Public HTTPS URL. |

## Startup Validation Added/Verified

- AI service production validation rejects weak/default `APP_SECRET_KEY`.
- AI service production validation rejects `AI_AGENT_MODE` values other than `tool_calling`.
- AI service production validation requires Gemini key/model when `AI_PROVIDER=gemini`.
- AI service production validation requires `AGENT_WRITE_TOOL_ENFORCEMENT=true`.
- AI service production validation rejects demo reset/debug/reloader in production.
- CRM/API production validation rejects weak/default `SECRET_KEY`, disabled auth, and debug.

## Secret Handling

- Do not commit `.env`.
- Do not print tokens in logs or reports.
- Rotate any key that was pasted into chat or terminal history before real production use.
- Use a deployment secret manager for Gemini, CRM API, session, admin, and webhook secrets.
