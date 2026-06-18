# Migration Quarantine Summary

This is the short review sheet for `migration_quarantine.json`.

## Main Buckets

| Reason | Count | Default Decision |
|---|---:|---|
| `duplicate_or_placeholder_booking_id` | 224 | Reject unless a real unique booking ID can be proven |
| `missing_phone_and_name` | 167 | Reject |
| `invalid_booking_trip_reference` | 83 | Reject unless trip can be matched with certainty |
| `invalid_booking_traveler_reference` | 60 | Reject unless traveler can be matched with certainty |
| `duplicate_phone_identity_in_workbook` | 58 | Keep one clean traveler only if identity is obvious |
| `missing_name_for_new_record` | 23 | Reject unless name can be verified |
| `duplicate_trip_id_in_workbook` | 6 | Keep one real trip only after manual decision |
| `duplicate_traveler_id_in_workbook` | 4 | Keep one real traveler only after manual decision |

## What To Review First

1. `duplicate_or_placeholder_booking_id`
   These are booking rows with the repeated placeholder `0-L-000` or another unsafe duplicate pattern.

2. `invalid_booking_trip_reference`
   These booking rows do not point to a safe trip ID.

3. `invalid_booking_traveler_reference`
   These booking rows do not point to a safe traveler ID.

4. `duplicate_phone_identity_in_workbook`
   These are traveler rows sharing the same phone identity.

5. `missing_phone_and_name`
   These are not safe to import.

## Fast Rules

- If the row is a booking and the traveler or trip link is unclear, leave it quarantined.
- If the row is a traveler and the phone identity is duplicated, keep only the best single record when the name and ID clearly match.
- If you are not 100% sure, do not import.
- Do not invent Traveler IDs, Trip IDs, or Booking IDs.

## Practical Recommendation

For a first pass:

1. Accept only rows that are clearly valid without guessing.
2. Leave all booking-reference problems quarantined.
3. Merge duplicate traveler phone rows only when the correct record is obvious.
4. Leave duplicate trip IDs for manual cleanup.

This keeps CRM safe and avoids corrupting traveler identity or booking history.
