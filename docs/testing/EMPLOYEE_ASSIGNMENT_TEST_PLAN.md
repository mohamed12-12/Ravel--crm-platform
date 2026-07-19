# Employee Assignment Test Plan

## Automated checks

- Existing employee follow-up regression tests must continue to pass.
- Login loads role from the active database user.
- Inactive users cannot authenticate or receive new work.
- Agents/sales can update permitted follow-up fields but cannot assign work.
- Managers/admins can assign only active eligible users.
- Invalid IDs, inactive IDs, and arbitrary legacy names are rejected.
- Assignment history records previous owner, new owner, actor, reason, and timestamp.
- “Assigned to me”, unassigned, inactive-owner, and employee filters use relational IDs.
- User creation, role changes, activation, and password reset create audit records.
- Concurrent booking updates retain the existing conflict protection.

## Manual acceptance

1. Log in as an administrator and create an employee.
2. Open a lead or booking and assign it from the employee dropdown.
3. Confirm the owner and assignment history appear on the detail page.
4. Log in as the employee and select **Assigned to me**.
5. Deactivate the employee and confirm the work appears in **Inactive owner** for a manager/admin.
6. Confirm an agent cannot see assignment controls or change ownership by posting a crafted name/ID.
7. Confirm the existing booking status and payment transition options remain unchanged.

## Data review

Before production use, review the migration/backfill report for `leads_matched`, `bookings_matched`, `ambiguous`, and `unmatched`. Do not force ambiguous names into an employee account.
