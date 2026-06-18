# Operational Database Promotion Report

Scope: safe operational promotion of the approved migration database into the CRM dashboard instance database. No migration logic was changed and no quarantined data was imported.

## Promotion Summary

- Old dashboard DB path: `apps/api/instance/rahma_traveler_dev.db`
- Backup path: `apps/api/instance/backups/rahma_traveler_dev.20260618-231454.db.bak`
- Promoted DB path: `.tmp-booking-import-populated/crm.db`
- Operational dashboard DB after promotion: `apps/api/instance/rahma_traveler_dev.db`

## Final Counts

| Table | Count |
|---|---:|
| travelers | 571 |
| trips | 42 |
| trip_bookings | 48 |
| booking_status_history | 48 |
| leads | 0 |

## Validation Results

- `python -m pytest tests`: passed, 127 passed
- `npm test`: passed
- `npm run build`: passed
- `npm run typecheck`: passed
- `python -m compileall apps services scripts tests`: passed

Additional validation:

- CRM database diagnostics route now exposes the active DB path and counts.
- AI agent health payload now exposes the active DB path and counts.
- `resolve_system_db_path()` resolves to `apps/api/instance/rahma_traveler_dev.db` when `RAHMA_SYSTEM_DB_PATH` is set to that path.
- The promoted dashboard database count check matches the approved migration database counts.

## Rollback Instructions

If rollback is required:

1. Stop the Flask CRM and AI agent processes.
2. Restore the backup file back into `apps/api/instance/rahma_traveler_dev.db`:
   - `apps/api/instance/backups/rahma_traveler_dev.20260618-231454.db.bak`
3. Restart the CRM and AI agent.
4. Verify counts on the restored database before resuming use.

## Notes

- Quarantine reports were preserved.
- No quarantined bookings were imported.
- The promotion only replaced the operational dashboard DB with the already approved migrated dataset.

