# Employee Follow-up Test Plan

## Automated Coverage Added

`tests/test_employee_followup_workspace.py`

Coverage:

1. Unauthenticated browser booking status update redirects to login, not raw JSON.
2. API clients still receive structured JSON auth errors.
3. CSRF failure is rejected for browser sessions.
4. Authorized employee updates booking status/payment/note/follow-up.
5. Unauthorized assignment change is rejected.
6. Invalid booking transition is rejected.
7. Invalid payment transition is rejected.
8. Concurrent booking update conflict is detected.
9. Lead follow-up summary and overdue state render.
10. Quick actions use backend-verified transition and record an event.

## Regression Commands

`python -m pytest -q tests/test_employee_followup_workspace.py`

`python -m pytest -q tests/test_security_hardening.py tests/test_phase3_booking_lifecycle.py tests/test_phase4_lead_redesign.py`

`python -m pytest -q`

Full result on 2026-07-16: 316 passed, 4 subtests passed.

## Manual Tests

1. Log out and submit a booking status form. Expect login page.
2. Log in and submit without CSRF using dev tools. Expect friendly rejection.
3. Update booking status through valid path. Expect redirect and flash.
4. Try invalid booking/payment transitions. Expect rejection.
5. Assign a booking as manager. Expect saved owner.
6. Try assignment as agent. Expect permission message.
7. Open lead detail. Expect follow-up summary and quick actions.
8. Confirm dashboard work queue is based on real lead/booking records.
