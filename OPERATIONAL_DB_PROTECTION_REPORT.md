# Operational DB Protection Report

Date: 2026-06-19

## Current status

- Operational DB protection tests exist and are still in place.
- Temp-test isolation has been fixed for the known failing harnesses.
- Full pytest now passes without mutating the operational DB.

## Root causes found

1. Several tests were using `DATABASE_URL` before the temp DB path and CRM service path were both isolated.
2. Some tests passed Windows temp paths directly into SQLite setup code.
3. A few harnesses depended on import-time config assumptions instead of runtime `get_sqlalchemy_uri()` resolution.
4. The AI-agent passport upload test wrote to a hardcoded project-level uploads path outside the writable test sandbox.
5. The operational DB had drift from the expected baseline counts before restore.

## Observed operational DB counts

- travelers: 571
- trips: 42
- trip_bookings: 48
- booking_status_history: 48
- leads: 0

## Expected operational DB counts

- travelers: 571
- trips: 42
- trip_bookings: 48
- booking_status_history: 48
- leads: 0

## Notes

- SHA256 protection tests remain in place.
- The operational DB was restored once from the approved backup and stayed unchanged through the final test run.
- Remaining work is limited to the frontend build spawn issue (`npm run build` / Vite esbuild `spawn EPERM` on this machine).
