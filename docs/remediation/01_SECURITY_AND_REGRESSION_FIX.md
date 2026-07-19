# Security And Regression Fix

Date: 2026-07-16. This batch addresses security hardening, active regression alignment, and pytest collection only.

The five failures were caused by four legacy deterministic tests inheriting the repository `.env` tool-calling mode, and one invalid Gemini function-call fixture without `thoughtSignature`. The four tests now explicitly select deterministic mode; the Gemini fixture now includes a function-call ID and thought signature. Production validation remains strict and the negative missing-signature test remains.

CRM state-changing routes are protected by default whenever the application runs. Authentication accepts the existing Flask session or a configured `CRM_API_TOKEN` via `X-CRM-API-Key`/Bearer header. Admin-sensitive writes require `admin` or `manager` role. The test harness explicitly opts out for legacy data-flow fixtures, while security tests enable the guard.

The CRM entrypoint now defaults `FLASK_DEBUG=false`; reload is also explicit. Agent reset is authentication-protected in production. Passport uploads validate extension, MIME type, size and secure session-scoped storage; CRM traveler uploads use the same extension/MIME checks and path validation. Webhook verification no longer logs supplied tokens.

Archived `archive/manual-review/scratch-root/test_live_sheets_flow.py` is excluded by `pytest.ini` with `testpaths = tests` and narrow archive/cache exclusions. It was not deleted.

Files changed: `apps/api/app/security.py`, `apps/api/app/__init__.py`, `apps/api/app/config.py`, `apps/api/app/routes/travelers.py`, `apps/api/run.py`, `services/instagram/webhooks.py`, `services/ai_agent/ai_agent_app/server.py`, `.env.example`, four legacy test files, `tests/test_phase4_business_validation.py`, `tests/test_security_hardening.py`, `pytest.ini`.

Verification: `python -m pytest --collect-only -q` collected 289 tests with no errors. Controlled full suite: 289 passed, 0 failed, 0 skipped. Focused security suite: 5 passed. Focused former-failure suite: 17 passed.

Next blockers: end-to-end authorization/login UX, CSRF/trusted-origin enforcement for browser CRM forms, upload content scanning/retention, source-of-truth decision, and deployment hardening.
