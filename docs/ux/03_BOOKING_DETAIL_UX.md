# Booking Detail UX

The booking detail page now keeps the existing CRM detail layout and adds an employee follow-up panel near the top.

## Visible Follow-up Fields

- booking status
- payment status
- traveler and trip
- assigned employee
- priority
- next action
- next follow-up
- last customer contact
- customer response
- missing documents

## Save Behavior

The page uses Post/Redirect/Get:

`POST /bookings/<id>/status -> validate -> write -> flash -> redirect detail`

The route calls `UnifiedCRMService.update_booking_status()` only for actual lifecycle changes or new notes, so follow-up-only edits do not create false status-history rows.

## Timeline

The activity timeline uses `booking_event_trail`. Status history remains visible separately for lifecycle auditing.
