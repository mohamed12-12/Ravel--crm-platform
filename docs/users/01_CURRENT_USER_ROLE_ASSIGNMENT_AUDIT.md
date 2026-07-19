# Current User, Role, and Assignment Audit

## Verified Current State

- CRM authentication uses `apps/api/app/models/user.py` and the `/login` route.
- The existing user table is reused; it now stores `username`, `full_name`, `email`, `password_hash`, `role`, `is_active`, timestamps, and `last_login_at`.
- Roles currently enforced by the CRM are `admin`, `manager`, `agent`, and `sales`.
- Browser sessions store the authenticated `user_id`. The backend loads the active user and role from the database on every request. A session role is not authoritative.
- API-token requests may identify a service actor and role through the existing API headers.
- Leads and bookings retain legacy `assigned_to` text for compatibility, but new assignment writes use `assigned_to_user_id`.
- Assignment history is stored in `assignment_history`; user administration changes are stored in `user_audit_log`.

## Previous Risks

- Free-text names could be misspelled, renamed, or impersonated.
- Session-only roles could become stale or be modified without matching database state.
- “Assigned to me” could not reliably identify work owned by a real employee.
- Deactivated employees had no relational impact on existing work.

## Compatibility Decision

The legacy text columns are not removed. Existing values are linked only when the normalized value matches exactly one username or full name. Ambiguous and unmatched values remain visible as legacy text and are not silently assigned. New forms submit verified employee IDs.

The formal migration is `database/migrations/versions/c61e4a2f9b10_relational_employee_assignment.py`. SQLite startup DDL remains as a compatibility bridge for older demo databases until migrations are applied consistently.
