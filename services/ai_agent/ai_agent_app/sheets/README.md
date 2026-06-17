# Sheet Gateways

This folder contains workbook and sheet adapters used by the demo app.

- `excel_gateway.py` is the local workbook implementation.
- `google_drive_gateway.py` downloads/uploads xlsx files through Google Drive.
- `google_sheets_api_gateway.py` talks to Google Sheets rows directly.
- `sheets_adapter.py` adapts row-based sheets to the subset of workbook behavior used by scripts.
- `factory.py` chooses the backend from settings.

Production work should keep external API retries, rate limits, and failure queues outside customer-facing request latency where possible.
