# Status Authority

## Rule

The CRM owns status values and transitions. External data may propose a change, but only CRM validation may approve and apply it. Generic spreadsheet import treats every different existing status as a conflict.

## Status owners

| Entity | CRM field | Authority and validation |
|---|---|---|
| Traveler | `travelers.status` | CRM traveler rules; blocked, blacklisted, archived, and inactive states affect eligibility |
| Trip | `trips.sales_status`, window fields | CRM trip administration and availability rules |
| Lead | `leads.lead_stage`, follow-up fields | CRM lead workflow and controlled agent action |
| Booking | `trip_bookings.booking_status`, payment/passport fields | CRM booking transition service with status history |
| Handoff | `handoff_queue.status` | CRM handoff workflow |
| Passport document | verification status | CRM document review workflow |

## Protected behavior

- An agent cannot claim a write occurred unless the CRM endpoint returns execution success.
- Booking and inventory state change in the same CRM-controlled operation.
- Spreadsheet status differences are not applied by generic import.
- Deleted or missing external rows do not close, cancel, or delete CRM records.
- Unknown status text is invalid, not mapped by guesswork.

## Transition expectations

Booking transitions must retain old status, new status, actor, source, time, and notes. Lead and handoff transitions should reach the same level of durable history in a future schema update. Until then, their current CRM timestamps, interaction/event trails, and agent audit records remain the available evidence.
