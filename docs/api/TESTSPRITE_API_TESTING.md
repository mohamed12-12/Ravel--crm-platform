# Rahma Traveler API Testing Contract

This document explains which API contract to use for backend testing tools such as TestSprite.

## Recommended TestSprite Setup

Use one API surface per TestSprite run.

### AI Sales Agent API

Use this when testing the customer-facing agent chat flow.

- API Base URL: `http://127.0.0.1:5001`
- OpenAPI docs URL: `http://127.0.0.1:5001/api/openapi.json`
- Main flow:
  - `POST /api/session`
  - `POST /api/session/{session_id}/message`
  - `POST /api/session/{session_id}/passport_attachment`
  - `GET /api/session/{session_id}`

Suggested extra testing instructions:

```text
Focus on AI agent session behavior. Create a session, send Arabic and English messages, verify identity questions are answered by nanovate.io backend policy, verify empty messages return 400, verify unknown sessions return 404, and verify passport upload validation rejects unsupported files. Do not expect payment confirmation or exact model-provider disclosure.
```

### CRM Operations API

Use this when testing employee, CRM, lead, booking, and agent CRM bridge behavior.

- API Base URL: `https://unmethodising-feckly-moriah.ngrok-free.dev/admin/dashboard`
- OpenAPI docs URL: `https://unmethodising-feckly-moriah.ngrok-free.dev/admin/dashboard/api/openapi.json`
- If CRM auth is enabled, send one of:
  - `X-CRM-API-Key: <CRM_API_TOKEN>`
  - `Authorization: Bearer <CRM_API_TOKEN>`
- For role-sensitive API-token requests, also send:
  - `X-CRM-Role: admin`
  - `X-CRM-Actor: testsprite`

Suggested extra testing instructions:

```text
Focus on CRM business rules and error handling. Test the AI CRM bridge read/write allowlist, invalid actions returning 422, booking status/payment transition validation, lead quick actions, duplicate resolution validation, and document upload validation. Do not change CRM business logic. Avoid destructive traveler deletion unless using an isolated test database.
```

## Served Contracts

Both Flask apps now serve machine-readable OpenAPI 3.1 contracts:

- AI Agent contract: `GET /api/openapi.json` on the AI agent app.
- CRM contract: `GET /api/openapi.json` on the CRM app.

These contracts are generated from `services/api_contracts/rahma_traveler.py` so tests and external tools share the same API description.

## Notes

- Browser pages such as `/leads/`, `/bookings/`, and `/travelers/` are intentionally not the main target of the backend API contract.
- Some legacy CRM routes return redirects for browser form flows. The OpenAPI contract focuses on stable JSON endpoints and important write actions.
- File upload endpoints use `multipart/form-data`.
- Production deployments should expose the same contracts behind the deployment domain.
