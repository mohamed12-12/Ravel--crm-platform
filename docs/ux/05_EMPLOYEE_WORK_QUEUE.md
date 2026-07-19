# Employee Work Queue

The dashboard now includes an operational queue built from CRM data.

## Sections

- Overdue Follow-ups: leads with `follow_up_due_date` before today.
- Due Today: leads due today.
- New Unassigned Leads: new lead stages with empty `assigned_to`.
- Waiting Customer: waiting-reply lead stages.
- Deposit Follow-ups: bookings with payment pending signals.
- Missing Documents: bookings with pending passport status.

## Source

The queue is generated from `Lead`, `TripBooking`, and related CRM fields. It is not generated from AI text.

## Remaining Improvements

Future work should add employee/user relationships and saved personal views such as "assigned to me".
