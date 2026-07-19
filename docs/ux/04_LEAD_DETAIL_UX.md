# Lead Detail UX

The lead detail page now shows a follow-up summary using existing lead data plus additive ownership/contact fields.

## Visible Follow-up Fields

- pipeline stage
- next action from `current_step`
- assigned employee
- priority
- follow-up date
- last customer contact
- customer response
- missing documents
- channel/language
- handoff status

## Quick Actions

Quick actions post to `/leads/<id>/quick-action` with CSRF and JSON.

Supported actions:

- Mark Customer Contacted
- Waiting for Customer
- Request Documents
- Deposit Requested

Each action validates the transition server-side and records a `booking_event_trail` event.

## Edit Behavior

Lead edits continue to use the existing lead route and stage transition map. Assignment changes require manager/admin role.
