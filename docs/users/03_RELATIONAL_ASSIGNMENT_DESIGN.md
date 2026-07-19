# Relational Assignment Design

Leads and bookings now expose:

- `assigned_to_user_id`: nullable foreign-key-compatible employee ID
- `assigned_at`: assignment timestamp
- `assigned_by_user_id`: employee who made the assignment
- `assigned_user` and `assigned_by_user` relationships

`assigned_to` remains a legacy display/fallback field. The assignment service writes the verified employee display name to it for compatibility, but it never accepts arbitrary free text as a new assignment.

Assignment changes go through `app/services/assignments.py`. It validates that the employee exists, is active, and has an assignable role. It records the previous owner, new owner, actor, reason, request ID when available, and timestamp.

Backfill is deterministic only. A normalized legacy name must match exactly one username or full name. Otherwise the value is preserved and reported as unmatched or ambiguous.
