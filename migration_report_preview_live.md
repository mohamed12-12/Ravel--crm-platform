# Excel to CRM Migration Run Report

- Mode: dry-run
- Scope: bookings_preview
- Workbook: `C:\Users\Mo\Desktop\nanovate tech\Projects\Rahma Traveler\RT - Travelers Database.xlsx`
- Database URI: `sqlite:///C:/Users/Mo/Desktop/nanovate tech/Projects/Rahma Traveler/apps/api/instance/rahma_traveler_dev.db`
- Quarantined rows: 850

## Booking Preview

- Matched bookings: 0
- Quarantined bookings: 850
- Safe to execute: False

## Summary

| Sheet | Scanned | Created | Updated | Skipped | Quarantined | Errors |
|---|---:|---:|---:|---:|---:|---:|
| Trip Bookings | 850 | 0 | 0 | 0 | 850 | 0 |

## Quarantine Reasons

- invalid_booking_traveler_reference: 626
- duplicate_or_placeholder_booking_id: 224

## Validation

- bookings_preview: {'matched': 0, 'quarantined': 850, 'lifecycle_counts': {}, 'safe_to_execute': False}
