# Application Entrypoints

| Application | Command | Port | Data | Status / risk |
|---|---|---:|---|---|
| CRM | `python apps/api/run.py` | 5000 | SQLAlchemy/SQLite by default | `debug=True` is hardcoded. |
| AI demo | `python services/ai_agent/ai_agent_app/server.py` | 5001 | CRM bridge plus Excel/Google | Active integration/demo; `/api/health`. |
| Legacy wrapper | `python demo_web/app.py` | configured | archived legacy app | Compatibility only. |
| Middleware | package scripts under `apps/middleware` | 3000 configured | CRM API | Auxiliary/incomplete. |
| Admin web | package scripts under `apps/admin-web` | Vite default | frontend | Auxiliary. |
| Scripts | migration/import utilities | n/a | DB/workbook/Google | Operational; explicit target required. |

Dependency map: browser -> agent demo -> CRM/system bridge -> SQLite and optional Excel/Google. CRM UI -> SQLAlchemy -> SQLite. A deployment must explicitly define the authoritative store per entity.

Finding ENT-01 | High | Confirmed | `apps/api/run.py:21` uses `debug=True`. Use a production server and a production-safe environment setting.
