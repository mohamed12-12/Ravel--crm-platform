# Services

Domain services and integration boundaries live here.

- `ai_agent/` contains the spreadsheet-backed MVP agent app.
- `crm/` contains shared CRM workflow services.
- `instagram/` contains the future Meta/Instagram webhook boundary.
- `notifications/` is reserved for outbound alert adapters.

This folder should hold reusable service logic, not application entrypoints.
