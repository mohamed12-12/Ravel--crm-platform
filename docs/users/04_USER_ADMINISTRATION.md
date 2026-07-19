# Employee Administration

Administrators use **Employees** in the CRM navigation (`/admin/users`).

Supported actions:

- Create an employee with username, display name, email, role, and password.
- Edit display name, email, and role.
- Activate or deactivate an account.
- Reset a password without displaying the old or new password.

Rules:

- Passwords are stored only as Werkzeug password hashes.
- Usernames and emails are unique when provided.
- Roles are limited to the policy values in the permission matrix.
- An administrator cannot deactivate or demote the account currently being used.
- User changes are committed with an audit record in `user_audit_log`.
- Deactivation does not delete ownership history or silently reassign work.
