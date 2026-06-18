# Booking ID Recovery

Scope: analysis only. No booking IDs were generated in the CRM and no booking rows were imported.

Source inspected:

- Quarantine file: `.tmp-booking-import-populated/quarantine.json`
- Quarantine reason: `duplicate_or_placeholder_booking_id`
- Placeholder Booking ID: `0-L-000`
- Placeholder rows reviewed: 224

## Recovery Policy

A placeholder booking can receive a generated migration ID only if the row can be uniquely identified using:

- Traveler
- Trip
- Booking date
- Amount
- Room type

## Findings

All 224 placeholder rows are contiguous workbook rows `629` through `852`.

Every placeholder row has:

- `Booking ID`: `0-L-000`
- `Trip ID`: blank
- `Trip Name`: blank
- `Traveler ID`: blank
- `Traveler Name`: blank
- `Room Type`: blank
- `Currency`: blank
- `Amount`: blank
- `Date`: blank
- `Payment Method`: blank
- `Currency #2`: blank
- `Amount #2`: blank
- `Date #2`: blank
- `Payment Method #2`: blank

## Recovery Table

| Workbook Row(s) | Original Booking ID | Unique Fingerprint Available | Generated Booking ID | Decision | Reason |
|---|---|---|---|---|---|
| 629-852 | `0-L-000` | No | None | Not recoverable | All identity fields are blank. There is no traveler, trip, date, amount, or room type evidence to distinguish one placeholder row from another. |

## Result

- Placeholder bookings reviewed: 224
- Booking IDs safely generated: 0
- Placeholder rows remaining quarantined: 224

## CTO Decision

Do not generate `MIG-BOOK-00001` style IDs for these rows. Generating IDs here would create fake unique records without evidence.

