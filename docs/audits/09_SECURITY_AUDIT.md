# Security Audit

No secret values are included.

| ID | Severity | Status | Evidence | Required remediation |
|---|---|---|---|---|
| SEC-01 | Critical | Resolved in batch | CRM state-changing routes are guarded when `CRM_AUTH_ENABLED=true` in production; API token/session and role checks added. Browser CSRF/login flow remains a next blocker. |
| SEC-02 | High | Needs manual verification | Agent debug-password fallback remains in existing development auth; production must use hashed secret configuration and never enable debug. |
| SEC-03 | High | Resolved in batch | webhook verification no longer logs the supplied token; regression test added. |
| SEC-04 | High | Resolved in batch | CRM document reads/writes are guarded when enabled; extension, MIME, size and path checks are covered. Agent uploads are session-bound and validated. |
| SEC-05 | High | Confirmed | Meta production TODOs remain | add replay, rate limits and sample-signature tests. |
| SEC-06 | Medium | Likely | SQLite/Excel hold PII | managed DB, backups, least privilege and encryption policy. |

Positive controls: ignored `.env`/certs, environment-loaded keys, HMAC `compare_digest`, basic response headers.
