# API Audit

CRM routes cover travelers, trips, bookings, leads, interactions, handoffs, admin, copy and identity. Agent routes cover health, bootstrap, sessions, messages, intake, booking, passport, visa, webhook and `/api/v1` compatibility APIs.

| ID | Severity | Evidence | Recommended fix |
|---|---|---|---|
| API-01 | Critical | write/upload endpoints lack demonstrated route-level authorization | Add roles and deny-by-default protection before deployment. |
| API-02 | High | duplicate-style preview/reset/booking APIs span legacy/new flows | publish a versioned contract and deprecate aliases. |
| API-03 | High | raw JSON payloads and raw gateway results | add request schemas, bounded validation and idempotency. |
| API-04 | Medium | UI list routes do not show a consistent pagination policy | add stable filter/pagination contracts. |
| API-05 | Medium | webhook TODOs remain | add signature/replay/durable processing controls. |
