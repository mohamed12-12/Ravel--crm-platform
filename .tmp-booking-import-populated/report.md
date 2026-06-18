# Excel to CRM Migration Run Report

- Mode: execute
- Scope: bookings
- Workbook: `RT - Travelers Database.xlsx`
- Database URI: `sqlite:///C:/Users/Mo/Desktop/nanovate tech/Projects/Rahma Traveler/.tmp-booking-import-populated/crm.db`
- Quarantined rows: 802

## Booking Preview

- Matched bookings: 48
- Imported bookings: 48
- Quarantined bookings: 802
- Safe to execute: False

## Booking Lifecycle Counts

- Completed: 44
- Paid: 2
- Payment Pending: 2

## Summary

| Sheet | Scanned | Created | Updated | Skipped | Quarantined | Errors |
|---|---:|---:|---:|---:|---:|---:|
| Trip Bookings | 850 | 48 | 0 | 0 | 802 | 0 |

## Quarantine Reasons

- unclear_booking_lifecycle: 453
- duplicate_or_placeholder_booking_id: 224
- invalid_booking_trip_reference: 89
- invalid_booking_traveler_reference: 36

## Validation

- bookings_import: {'matched': 48, 'imported': 48, 'quarantined': 802, 'status_distribution': {'Completed': 44, 'Payment Pending': 2, 'Paid': 2}, 'invalid_traveler_refs': 36, 'invalid_trip_refs': 89, 'placeholder_booking_ids': 224, 'lifecycle_unclear_count': 453, 'safe_to_execute': False}
