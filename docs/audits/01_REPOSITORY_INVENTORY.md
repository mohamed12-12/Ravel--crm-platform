# Repository Inventory

Audit date: 2026-07-15. Read-only scope.

| Area | Responsibility | Status / action |
|---|---|---|
| `apps/api` | Flask CRM, port 5000 | Active; keep. |
| `services/ai_agent` | Gemini/tool runtime and demo portal | Active; keep. |
| `services/crm/system_services` | CRM/Excel/sync domain service | Active; split later. |
| `services/instagram` | Meta webhook bridge | Incomplete for production. |
| `demo_web` | Imports legacy archived demo | Compatibility only. |
| `scripts` | migrations and phase utilities | Keep; classify before use. |
| `tests` | 284 active tests | Keep; repair failures. |
| `archive` | historical artifacts | Exclude from pytest collection. |
| `docs` | 92 docs files, 161 Markdown repository-wide | Restructure after approval. |

Finding INV-01 | High | Confirmed: default pytest collection includes `archive/manual-review/scratch-root/test_live_sheets_flow.py`, which fails because it imports obsolete `app`. Configure `testpaths = tests`; do not alter archive.

Finding INV-02 | High | Confirmed: `unified_service.py` is 3,695 lines, `session_flow.py` 1,880 lines, `server.py` 1,312 lines. Extract bounded domains only after contract tests exist.
