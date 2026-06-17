# Phase 5 Booking Automation

Date: 2026-05-11

## What Phase 5 Adds

Phase 5 adds the first booking automation layer for the demo:

- reads room availability from `Trips`
- checks draftable capacity by room type
- creates a booking draft
- appends a controlled row to `Trip Bookings`
- creates an internal alert in `Booking Alerts`
- updates the linked lead to a booking stage
- logs a booking interaction

## Workbook Additions

### Trips

New helper fields:

- `Draft Holds Single`
- `Draft Holds Double`
- `Draft Holds Triple`

### Trip Bookings

New helper fields:

- `Booking Status`
- `Draft Created At`
- `Booking Source`
- `Lead ID`
- `Interaction ID`
- `Alert ID`
- `Payment Status`
- `Booking Notes`

### New Sheet

- `Booking Alerts`

## Demo Inventory

Phase 5 seeds demo-only open trips in the Phase 5 workbook copy so the client can test booking drafts even though the real source data does not currently have confirmed `Open` future trips.

## Demo Behavior

- only `Open` trips can be drafted
- capacity is checked before a draft is created
- draft creation increases the room-type draft hold count
- payment is marked as `Awaiting Deposit`
- the lead stage moves to a booking-draft stage

## Main Files

- `scripts/phase5_booking_automation.py`
- `PHASE5_BOOKING_AUTOMATION_USAGE.md`
- `demo_web/app.py`
- `demo_web/static/app.js`
- `demo_web/templates/index.html`
