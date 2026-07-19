# Current Employee Follow-up Audit

## Existing Statuses

Booking statuses: `Draft`, `Waiting Customer`, `Pending Confirmation`, `Confirmed`, `Payment Pending`, `Paid`, `Completed`, `Cancelled`.

Booking transitions come from `UnifiedCRMService.BOOKING_TRANSITIONS`:

| Status | Allowed next statuses | Customer contact | Payment | Inventory |
|---|---|---|---|---|
| Draft | Waiting Customer, Pending Confirmation, Cancelled | Often | No | Draft booking already owns holds where applicable |
| Waiting Customer | Pending Confirmation, Confirmed, Cancelled | Yes | No | No ordinary follow-up inventory change |
| Pending Confirmation | Confirmed, Cancelled | Maybe | No | No |
| Confirmed | Payment Pending, Cancelled | Maybe | Payment may be requested | No |
| Payment Pending | Paid, Cancelled | Yes | Required | No |
| Paid | Completed, Cancelled | Maybe | Complete or refundable | No |
| Completed | None | No | Complete | No |
| Cancelled | None | No | Refund may apply | Cancellation workflow owns inventory behavior |

Payment statuses: `Pending`, `Deposit Paid`, `Fully Paid`, `Refunded`.

Lead stages: `New Lead`, `Contacted`, `Qualified`, `Waiting Customer Reply`, `Proposal Sent`, `Booking Draft`, `Won`, `Lost`, `Handoff Needed`.

Lead transitions come from `PIPELINE_TRANSITIONS` in `apps/api/app/routes/leads.py`.

Handoff statuses: `Pending`/`New`, `In Progress`, `Resolved`.

Traveler states include active, inactive, archived, blocked/blacklisted style states from traveler records and service policy.

## Missing From Previous UI

Employees could change status and notes, but the pages did not clearly expose:

- assigned employee
- next action
- next follow-up
- overdue work
- last customer contact
- customer response state
- missing documents
- unified activity timeline
- safe stale-page detection

## Existing Fields Reused

Leads already had `priority`, `follow_up_status`, `follow_up_due_date`, `current_step`, `language`, `handoff_required`, `passport_status`, traveler/trip/booking links, interactions, and event trail.

Bookings already had status, payment status, passport status, notes, status history, and event trail.

## Fields Added

Added only the fields required to make follow-up actionable:

- `assigned_to`
- `last_contact_at`
- `customer_response_status`
- booking-only `priority`, `next_follow_up_at`, `next_action`
