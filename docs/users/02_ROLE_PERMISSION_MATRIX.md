# CRM Role and Permission Matrix

The backend policy is centralized in `apps/api/app/security.py`. Frontend controls only reflect this policy; hiding a control is never the authorization mechanism.

| Permission | Admin | Manager | Agent | Sales |
|---|---:|---:|---:|---:|
| View all work and team workload | Yes | Yes | No | No |
| Manage employee accounts | Yes | No | No | No |
| Assign or reassign leads/bookings | Yes | Yes | No | No |
| Update permitted follow-up/status fields | Yes | Yes | Yes | Yes |
| Manage handoffs | Yes | Yes | No | No |

All roles must be active database users. An inactive user cannot authenticate and is not eligible as a new assignee. Existing work remains linked to the inactive user and appears in the manager/admin inactive-owner queue.

The existing CRM status transition rules and CSRF/API authentication rules remain the authority for booking and lead business actions.
