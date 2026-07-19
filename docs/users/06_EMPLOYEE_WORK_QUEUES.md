# Employee Work Queues

Leads and bookings support:

- **Assigned to me**: relational owner equals the logged-in employee.
- **Unassigned**: relational owner is null.
- **Inactive owner**: work remains linked to a deactivated employee and is visible to managers/admins for reassignment.
- **Any employee**: manager/admin filter by a verified employee ID.

The dashboard also shows the current employee’s work and team workload. Counts are derived from relational IDs, not display names. Legacy-only assignments remain visible in record lists but are not counted as assigned-to-me until a safe backfill or explicit reassignment links them.
