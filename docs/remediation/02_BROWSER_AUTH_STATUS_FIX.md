# Browser Auth Status Fix

Date: 2026-07-16

## Root Cause

The booking detail page used a traditional HTML form:

`apps/api/app/templates/bookings/detail.html -> POST /bookings/<booking_id>/status`

The global `crm_request_guard()` in `apps/api/app/security.py` protects CRM writes. Before this fix it returned JSON for every unauthenticated write:

`{"error":"authentication_required"}`

Because the booking form was a browser navigation, the user was taken away from the booking detail page and saw the raw JSON response.

## Request Path Before

Browser form POST -> `crm_request_guard()` -> missing `session["logged_in"]` -> JSON 401 -> browser displays raw JSON.

No CSRF token was submitted. API key auth existed, but browser code must not use `X-CRM-API-Key`.

## Request Path After

Employee login -> server-side Flask session -> HttpOnly session cookie -> CSRF token in form/header -> role check -> booking route -> `UnifiedCRMService.update_booking_status()` -> status history/event trail -> flash message -> redirect to booking detail.

API clients still receive JSON errors when using JSON requests or API paths.

## Authentication And CSRF

Browser employees use `/login`, the `users` table, or a configured `CRM_ADMIN_USERNAME` plus `CRM_ADMIN_PASSWORD_HASH`. The session stores `logged_in`, `username`, `role`, and `csrf_token`.

Cookies remain HttpOnly, SameSite Lax, and Secure in production. State-changing browser requests include `csrf_token` form input or `X-CSRF-Token` header.

API keys remain service-to-service only and are not exposed in frontend JavaScript.

## Role Rules

Write roles: `admin`, `manager`, `agent`, `sales`.

Admin-sensitive routes remain `admin` or `manager`.

Assignment changes require `admin` or `manager`.

## Files Changed

- `apps/api/app/security.py`
- `apps/api/app/routes/auth.py`
- `apps/api/app/templates/auth/login.html`
- `apps/api/app/__init__.py`
- `apps/api/app/routes/bookings.py`
- `apps/api/app/routes/leads.py`
- `apps/api/app/routes/admin.py`
- `apps/api/app/models/booking.py`
- `apps/api/app/models/lead.py`
- `database/migrations/versions/b52e9d6a31f4_add_employee_followup_fields.py`
- CRM templates under `apps/api/app/templates/...`
- `tests/test_employee_followup_workspace.py`

## UI Changes

Booking and lead detail pages now show an employee follow-up summary, next action, owner, priority, customer response, missing documents, and activity timeline.

The booking status form saves status, payment status, note, assignment, priority, next action, next follow-up, and contact marker. It redirects back with friendly messages.

## Database Changes

Additive fields:

- Leads: `assigned_to`, `last_contact_at`, `customer_response_status`
- Bookings: `assigned_to`, `priority`, `next_follow_up_at`, `next_action`, `last_contact_at`, `customer_response_status`

SQLite startup backfill and Alembic migration are included.

## Tests

Commands run:

`python -m pytest -q tests/test_employee_followup_workspace.py`

Result: 9 passed.

`python -m pytest -q tests/test_security_hardening.py tests/test_phase3_booking_lifecycle.py tests/test_phase4_lead_redesign.py`

Result: 17 passed.

`python -m pytest -q`

Result: 316 passed, 4 subtests passed.

## Manual Checklist

1. Open a booking detail page.
2. Log in if redirected.
3. Save valid booking/payment status changes.
4. Confirm the page redirects back, not to raw JSON.
5. Confirm flash message appears.
6. Confirm status history and activity timeline show the update.
7. Try stale page save and confirm conflict message.
8. Try assignment change as non-manager and confirm rejection.

## Remaining Limitations

The app still has a lightweight role model. It stores role in session rather than a dedicated user-role table. Follow-up ownership is a text field, not a relational employee assignment.
