# Phase 4 - Auto-assign new leads to available Sales

## What this phase delivers

New leads are assigned automatically to an available Sales employee using round-robin logic, while Admin/Manager can still manually reassign leads afterward.

## Current state (investigated, not assumed)

- `Lead` already has assignment fields: `assigned_to`, `assigned_to_user_id`, `assigned_at`, and `assigned_by_user_id` (`apps/api/app/models/lead.py:43-46`).
- User roles include a `sales` role (`apps/api/app/models/user.py:18`, `apps/api/app/routes/admin.py:26`).
- `active_assignees()` returns active users in assignable roles `admin`, `manager`, `agent`, and `sales`, ordered by name/username (`apps/api/app/services/assignments.py:16`, `apps/api/app/services/assignments.py:27-33`).
- `apply_assignment()` updates assignment fields and writes `AssignmentHistory` (`apps/api/app/services/assignments.py:73-109`).
- Manual lead creation only applies an assignment if `assigned_to_user_id` was provided (`apps/api/app/routes/leads.py:413-426`).
- Lead update supports manual reassignment and blocks it for callers without `assign_work` (`apps/api/app/routes/leads.py:498-512`, `apps/api/app/routes/leads.py:570-577`).
- Admin/Manager have `assign_work`; Agent/Sales do not (`apps/api/app/security.py:24-28`).
- No round-robin state, sales-only auto-assignment function, or automatic assignment call was found in the assignment service or lead creation path.

## Confirmed requirements this phase must satisfy

From the provided task attachment: add round-robin assignment logic using the already agreed working default, auto-assign first, and keep manual reassignment for Admin/Manager. The task says to layer on top of existing manual-assignment code, not replace it.

## Working assumptions (for Phases 6 and 7 specifically)

Not applicable.

## Design approach

- Add a sales-only active assignee query for automatic lead assignment.
- Add deterministic round-robin state. Options to verify before implementation: store last-assigned sales user in a small settings/audit table, or infer from recent `AssignmentHistory` for leads.
- Call auto-assignment after lead creation when no explicit manual assignment was submitted.
- Use `apply_assignment()` so assignment history remains consistent.
- Preserve manual reassignment controls and `assign_work` checks for Admin/Manager.
- Add concurrency/idempotency tests so repeated create retries do not double-rotate or assign different Sales users for the same created lead.

## Dependencies on other phases

No strict dependency. It should be implemented before broad rollout testing in Phase 8.

## Risks specific to this phase

Round-robin can become unfair or inconsistent under concurrent lead creation unless the state update is transactional. Existing manual assignment history should remain the source of assignment audit, so auto-assignment should reuse it.

## Tests

Not run. No code was changed in this documentation-only pass.

## Live verification

Not run. No deployed app behavior was changed or verified in this documentation-only pass.

