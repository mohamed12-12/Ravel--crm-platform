# Test Inventory

Active suite: 36 Python files and 284 collected tests under `tests/`. Coverage includes CRM, bookings, leads, handoffs, Excel migration, Google adapters, workflow policy, tool loops, mocked Gemini, passport flows, demo APIs, data safeguards and operational DB isolation.

Missing verified coverage: browser E2E, concurrency/load, authorization/CSRF, attachment malware/retention, rate limiting, Meta replay samples, secret-gated live Gemini, backup restore, and full DB/Excel/Google reconciliation.

Finding TINV-01 | Medium | Confirmed | archive test discovery breaks the default command. Constrain collection to `tests/`.
