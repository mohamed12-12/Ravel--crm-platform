# Protected Route Matrix

| Method | Path group | Resource | Auth | Authorization / validation | Tests |
|---|---|---|---|---|---|
| POST/PUT/PATCH/DELETE | `/travelers/*` | traveler writes/documents | CRM session or API token | authenticated; extension, MIME, 10MB, secure path | `test_security_hardening`, traveler tests |
| POST/PUT/PATCH/DELETE | `/leads/*` | lead writes | same | authenticated; admin-sensitive handoff/merge policy | security + lead tests |
| POST/PUT/PATCH/DELETE | `/trips/*` | trip/inventory writes | same | authenticated; inventory validation | security + trip tests |
| POST/PUT/PATCH/DELETE | `/bookings/*` | booking/status writes | same | authenticated; admin role where configured; business validation | security + booking tests |
| POST | `/interactions/` | interaction write | same | authenticated | security test |
| POST/PUT | `/admin/handoffs/*` | handoff write | same | authenticated | handoff tests |
| POST | `/api/crm/resolve-identity` | duplicate merge | same | `admin`/`manager` | security test |
| POST | `/admin/import`, `/admin/sync-issues/retry` | import/sync | same | `admin`/`manager`; upload validation | admin tests |
| POST | `/api/reset` | maintenance reset | agent session in production | authenticated/admin operational control | reset tests |
| POST | `/api/session/*/passport_attachment` | customer passport upload | valid session-bound ID | extension/MIME/10MB/secure session path | passport tests |
| POST | `/webhook` | Meta event ingress | Meta HMAC signature | configured secret; no token logging | webhook tests |

Read-only health/bootstrap, public trip discovery, and customer session creation remain intentionally public and must not expose secrets or operational file paths.
