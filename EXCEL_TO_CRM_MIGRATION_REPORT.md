# Excel to CRM Migration Report

## Implementation Summary

Implemented a safe Excel-to-CRM migration utility:

`scripts/migrate_excel_to_crm.py`

The utility reads `RT - Travelers Database.xlsx`, validates records, applies CRM identity rules, and writes a quarantine/report output. It is dry-run by default and only commits CRM writes when `--execute` is explicitly passed.

## Command Examples

Dry-run against the default workbook:

```powershell
python scripts/migrate_excel_to_crm.py --dry-run --workbook "RT - Travelers Database.xlsx" --quarantine-output migration_quarantine.json --report-output migration_report.md
```

Execute against an explicit database:

```powershell
python scripts/migrate_excel_to_crm.py --execute --workbook "RT - Travelers Database.xlsx" --db "apps/api/instance/rahma_traveler_dev.db" --quarantine-output migration_quarantine.json --report-output migration_report.md
```

## Safety Guarantees

- Dry-run is the default behavior.
- `--execute` is required before any CRM data is committed.
- Existing CRM records are not overwritten blindly.
- Existing traveler IDs are preserved.
- Phone normalization uses the approved CRM phone normalization service.
- Unknown numbers are not forced to Egyptian country code `20`.
- Duplicate traveler IDs are quarantined.
- Duplicate phone identities are quarantined.
- Duplicate or placeholder booking IDs are quarantined.
- Bookings are imported only when traveler and trip references are valid.
- Excel remains import/reporting input only; the CRM database remains operational source of truth.

## Dry-Run Behavior

Dry-run opens the workbook, validates rows, stages ORM changes in a database session, writes report/quarantine files, then rolls back the session.

No database records are committed in dry-run mode.

## Execute Behavior

Execute mode commits only records that pass validation.

Records that are ambiguous, duplicated, or missing required references are written to quarantine instead of being forced into CRM tables.

## Quarantine Behavior

Quarantine output is JSON and includes:

- source sheet
- source row
- reason
- key
- raw row values

Current quarantine reasons include:

- `missing_phone_and_name`
- `duplicate_traveler_id_in_workbook`
- `duplicate_phone_identity_in_workbook`
- `missing_identity_key`
- `missing_traveler_id_for_new_record`
- `missing_name_for_new_record`
- `duplicate_trip_id_in_workbook`
- `duplicate_or_placeholder_booking_id`
- `invalid_booking_traveler_reference`
- `invalid_booking_trip_reference`
- `invalid_room_type`

## Real Workbook Dry-Run Result

Command run:

```powershell
python scripts/migrate_excel_to_crm.py --dry-run --workbook "RT - Travelers Database.xlsx" --quarantine-output migration_quarantine.json --report-output migration_report.md
```

Result:

| Sheet | Scanned | Created | Updated | Skipped | Quarantined | Errors |
|---|---:|---:|---:|---:|---:|---:|
| Travelers | 773 | 521 | 0 | 0 | 252 | 0 |
| Trips | 48 | 42 | 0 | 0 | 6 | 0 |
| Trip Bookings | 850 | 483 | 0 | 0 | 367 | 0 |
| Leads | 0 | 0 | 0 | 1 | 0 | 0 |

Total quarantined rows:

`625`

Errors:

`0`

## Tests Added

Added:

`tests/test_excel_to_crm_migration.py`

Coverage includes:

- dry-run does not write records
- Traveler ID preservation
- existing traveler safe update
- duplicate phone prevention
- phone normalization during import
- non-Egyptian phone import
- ambiguous rows quarantined
- invalid booking/trip references quarantined
- execute writes expected safe records

## Test Results

Full verification:

```powershell
python -m pytest tests
npm test
npm run build
npm run typecheck
python -m compileall apps services scripts tests
```

Results:

- `python -m pytest tests`: `111 passed`
- `npm test`: passed; includes workspace test placeholders and `111 passed`
- `npm run build`: passed
- `npm run typecheck`: passed
- `python -m compileall apps services scripts tests`: passed

Focused migration tests:

```powershell
python -m pytest tests/test_excel_to_crm_migration.py -q
```

Result:

`6 passed`

## Remaining Limitations

- The migration does not create CRM leads unless a clean `Leads` sheet exists.
- Boys/girls room split is not invented from Excel when missing.
- Repeated placeholder booking ID `0-L-000` is quarantined, not auto-converted.
- Rows missing traveler or trip references are quarantined unless a safe direct reference exists.
- Existing records are filled only where fields are blank; broad overwrite/merge behavior is intentionally not implemented.
- Full production execution should be done only after reviewing `migration_quarantine.json` and backing up the CRM database.
