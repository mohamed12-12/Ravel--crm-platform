# Final QA Deployment Checklist

Date: 2026-08-02

## Automated Gates

- [x] Production env validation tests pass.
- [x] Ports 1-4.8 safety tests pass in targeted gate.
- [x] Gemini write/runtime tests pass in targeted gate.
- [x] Booking lifecycle tests pass in targeted gate.
- [x] Response guard/completion tests pass in targeted gate.
- [x] Security/data authority/UI layout tests pass in targeted gate.
- [x] TypeScript typecheck passes for admin web and middleware.
- [x] Root build passes.
- [!] Broad `python -m pytest -q` has 8 classified legacy/outdated assertion failures.

## Local Runtime Smoke

- [x] AI demo UI loads at `http://127.0.0.1:5101/`.
- [x] CRM dashboard loads at `http://127.0.0.1:5000/admin/dashboard`.
- [x] Admin web loads at `http://127.0.0.1:3101/index.html`.
- [x] Middleware health returns 200 at `http://127.0.0.1:3100/api/health`.
- [x] English greeting produces stable phone-first response.
- [x] Arabic greeting produces stable Arabic phone-first response.
- [x] Returning traveler lookup reaches trip-type selection.
- [x] Unknown/new traveler flow asks for full name safely.
- [x] `yes` too early does not write.
- [x] `book` too early does not write.
- [x] Repeated booking confirmation does not duplicate booking.
- [x] Direct booking bypass route returns 401 without auth.
- [x] Human handoff request creates handoff safely.
- [x] Capacity-review handoff now persists.

## Safety Gates

- [x] No booking success without WriteResult/record id.
- [x] No duplicate booking from repeated confirmation in smoke.
- [x] Write-only enforcement is enabled in safe mode.
- [x] Global router enforce remains off.
- [x] Read tools are not globally enforced yet.
- [x] Customer responses did not expose tool/router/state/WriteResult internals in smoke.

## Before Real Production

- [ ] Rotate any pasted/local Gemini/admin secrets.
- [ ] Configure production secret manager.
- [ ] Point DB and upload storage at production-safe durable resources.
- [ ] Decide whether broad legacy tests should be marked/migrated before CI uses full `pytest`.
- [ ] Configure real webhook URLs/tokens only if Meta integration is going live.
- [ ] Run manual QA on a clean seeded production-like database.
