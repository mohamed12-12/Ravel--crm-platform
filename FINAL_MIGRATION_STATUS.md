# Final Migration Status

## Current Imported State

- Travelers imported: `571`
- Travelers quarantined: `191`
- Traveler merges applied from approved decisions: `11`
- Trips imported: `42`
- Trips quarantined: `6`
- Bookings imported: `48`
- Bookings quarantined: `802`
- Booking status history rows created: `48`

## Accepted Shared-Phone Exceptions

These remain as separate traveler records by design and should not be auto-merged:

- `20:1127311373`
  `TR00228` - Rania Ali +; `TR00901` - Rania's Child (5 years)
- `20:1097850042`
  `TR00450` - Mona Hamaki; `TR00904` - Mona's daughter^

## Quarantine Reasons

### Travelers

- Remaining duplicate phone lookup keys after import: `6`
- Accepted shared/family phone exceptions: `2`
- Manual review duplicate phone cases: `4`

Manual-review duplicate phone keys:

- `966:546052012`
- `20:1280003592`
- `20:1060035570`
- `20:1200644400`

### Trips

- `duplicate_trip_id_in_workbook`: `6`

### Bookings

- `unclear_booking_lifecycle`: `453`
- `duplicate_or_placeholder_booking_id`: `224`
- `invalid_booking_trip_reference`: `89`
- `invalid_booking_traveler_reference`: `36`

## What Is Safe To Show In CRM

- All `571` imported travelers
- All `42` imported trips
- All `48` imported bookings
- Booking lifecycle/status for the imported bookings:
  `Completed` 44, `Payment Pending` 2, `Paid` 2
- Booking history/timeline for the imported bookings
- Accepted shared-phone traveler exceptions, as separate records

## What Still Needs Manual Review

- The `4` unresolved duplicate-phone traveler cases listed above
- The `6` duplicate trip ID rows quarantined from the workbook
- The `802` quarantined booking rows
- All booking rows with placeholder ID `0-L-000`
- All booking rows missing a safe traveler or trip reference
- All booking rows with unclear lifecycle classification
- Imported trips with missing boys/girls split details
  `42` imported trips are flagged for manual room-split completion

## CRM Source Of Truth Decision

The CRM database can now be used as the operational source of truth for:

- imported travelers
- imported trips
- imported bookings
- agent lookups and CRM workflows that only rely on imported records

The CRM is not yet a complete source of truth for the entire legacy workbook because quarantined rows still exist outside the imported set. The agent should use CRM as the source of truth for live operations, while unresolved workbook rows stay in manual-review backlog until cleaned.

## Rollback Instructions

1. Stop any process using the target SQLite database.
2. Restore the database file from the backup taken before migration.
3. If no backup exists, do not attempt partial manual row deletion as a first response.
4. For test or dry-run environments, discard the migrated SQLite file and recreate it from the pre-migration copy.
5. Keep the quarantine JSON and migration reports so the import can be replayed safely after fixes.

## Next Recommended Cleanup Steps

1. Review the `4` unresolved duplicate-phone traveler groups and decide whether each is:
   separate people, shared family phone, or true duplicate.
2. Resolve the `6` duplicate trip ID workbook rows and choose the valid trip record for each ID.
3. Clean the `224` placeholder booking ID rows.
4. Resolve the `89` invalid trip references in booking rows.
5. Resolve the `36` invalid traveler references in booking rows.
6. Review the `453` lifecycle-unclear booking rows and assign a valid lifecycle only when evidence is sufficient.
7. Complete boys/girls room split data on imported trips where still missing.
8. After cleanup, rerun booking preview before importing any additional quarantined booking rows.
