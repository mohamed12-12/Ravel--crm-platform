# Spreadsheet Demo Flask App

This package contains the current spreadsheet-backed demo Flask application.

- `server.py` wires routes, sheet gateways, sessions, and webhook placeholders.
- `agent/` owns the in-memory MVP conversation flow.
- `sheets/` owns Excel, Google Drive xlsx, and Google Sheets adapters.
- `web/` owns demo templates, static files, auth, and `/api/v1` routes.
- `system_bridge.py` connects demo actions to the database-backed CRM service in `services/crm/system_services/`.

Keep public routes and imports stable unless tests and downstream launch commands are migrated together.
