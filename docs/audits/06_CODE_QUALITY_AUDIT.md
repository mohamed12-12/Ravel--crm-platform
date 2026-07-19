# Code Quality Audit

| ID | Severity | Evidence | Recommended fix |
|---|---|---|---|
| CQ-01 | High | `unified_service.py` 3,695 lines | Split query, inventory, traveler, lead/booking and sync services. |
| CQ-02 | High | `session_flow.py` and `server.py` mix HTTP, state and persistence | Extract adapters without changing contracts. |
| CQ-03 | High | broad `except Exception` and silent `pass` in routes/services | Use typed exceptions, structured logging and explicit failures. |
| CQ-04 | Medium | DB, Excel, Sheets and Drive gateways coexist | Document authority and add parity/reconciliation tests. |
| CQ-05 | Medium | `demo_web` imports archived code | Make legacy selection explicit after approval. |
