# SQLite Data Integrity Audit Report

Date: 2026-08-03

## Scope

This audit inspected the current SQLite database in read-only mode to identify rows that could violate PostgreSQL foreign keys during migration.

SQLite source:

```text
apps/api/instance/rahma_traveler_dev.db
```

## Safety Confirmation

- SQLite opened with `mode=ro`.
- SQLite file hash was unchanged after inspection.
- Staging PostgreSQL was not modified.
- Production was not touched.
- No secrets were used or printed.

## Table Counts

| Table | Rows |
| --- | ---: |
| `users` | 2 |
| `travelers` | 540 |
| `trips` | 47 |
| `community_events` | 0 |
| `dm_copy_library` | 0 |
| `language_templates` | 0 |
| `leads` | 0 |
| `interactions` | 12 |
| `trip_bookings` | 49 |
| `ce_bookings` | 0 |
| `booking_status_history` | 65 |
| `handoff_queue` | 3 |
| `booking_event_trail` | 3 |
| `traveler_documents` | 4 |
| `trip_media` | 6 |
| `assignment_history` | 3 |
| `user_audit_log` | 5 |
| `sync_queue` | 29 |

## Orphan Booking Status History

Result: `FOUND`

`booking_status_history` has 17 rows across 10 `booking_id` values that do not exist in `trip_bookings`.

| Booking ID | Rows | Old Status Values | New Status Values | First Changed | Last Changed | Changed By | Source |
| --- | ---: | --- | --- | --- | --- | --- | --- |
| `586-I26DEM-001` | 1 | `Draft` | `Cancelled` | `2026-07-30T09:57:02+00:00` | `2026-07-30T09:57:02+00:00` | `admin` | `crm-ui` |
| `586-L26DEM-001` | 1 | `Draft` | `Cancelled` | `2026-07-17T17:28:24+00:00` | `2026-07-17T17:28:24+00:00` | `admin` | `crm-ui` |
| `589-I26DEM-003` | 2 | `Draft`, `Pending Confirmation` | `Pending Confirmation`, `Confirmed` | `2026-07-17T14:48:14+00:00` | `2026-07-17T14:48:42+00:00` | `admin` | `crm-ui` |
| `589-L26TES-001` | 2 | `Draft`, `Pending Confirmation` | `Pending Confirmation`, `Confirmed` | `2026-07-17T14:58:25+00:00` | `2026-07-17T14:58:32+00:00` | `admin` | `crm-ui` |
| `591-I26DEM-002` | 1 | `Draft` | `Pending Confirmation` | `2026-07-16T12:34:40+00:00` | `2026-07-16T12:34:40+00:00` | `admin` | `crm-ui` |
| `593-I26DEM-004` | 2 | `Draft`, `Pending Confirmation` | `Pending Confirmation`, `Confirmed` | `2026-07-17T14:46:32+00:00` | `2026-07-17T14:46:48+00:00` | `admin` | `crm-ui` |
| `595-I26DEM-005` | 5 | `Draft`, `Waiting Customer`, `Confirmed`, `Payment Pending`, `Paid` | `Waiting Customer`, `Confirmed`, `Payment Pending`, `Paid`, `Completed` | `2026-07-19T07:02:53+00:00` | `2026-07-19T07:04:51+00:00` | `admin` | `crm-ui` |
| `608-I26DEM-002` | 1 | `Draft` | `Waiting Customer` | `2026-07-30T09:14:07+00:00` | `2026-07-30T09:14:07+00:00` | `admin` | `crm-ui` |
| `609-L26DEM-006` | 1 | `Draft` | `Waiting Customer` | `2026-07-30T08:56:49+00:00` | `2026-07-30T08:56:49+00:00` | `admin` | `crm-ui` |
| `615-L26DEM-008` | 1 | `Draft` | `Completed` | `2026-08-02T12:41:28+00:00` | `2026-08-02T12:41:28+00:00` | `admin` | `crm-ui` |

Detailed row-level CSV was generated locally for engineering review:

```text
.tmp-run/sqlite_integrity_audit/orphan_booking_status_history.csv
```

This CSV is a temporary audit artifact and should not be committed if considered operational data.

## Useful Context

The orphan booking IDs appear to be legacy/demo-style booking identifiers. A read-only context check found:

- No matching `trip_bookings.booking_id` rows.
- No matching inferred `trips.trip_id` rows for the embedded trip-code portion.
- No matching inferred traveler rows for the leading numeric portion.

This means creating placeholder booking records would require business assumptions, not simple technical reconstruction.

## Other FK-Like Orphan Risks

Result: `PASS`

No orphan rows were found for these relationship checks:

- `trip_bookings.traveler_id` to `travelers.traveler_id`
- `trip_bookings.trip_id` to `trips.trip_id`
- `trip_bookings.lead_id` to `leads.lead_id`
- `trip_bookings.interaction_id` to `interactions.interaction_id`
- `leads.traveler_id` to `travelers.traveler_id`
- `interactions.traveler_id` to `travelers.traveler_id`
- `handoff_queue.lead_id` to `leads.lead_id`
- `handoff_queue.traveler_id` to `travelers.traveler_id`
- `handoff_queue.trip_id` to `trips.trip_id`
- `traveler_documents.traveler_id` to `travelers.traveler_id`
- `trip_media.trip_id` to `trips.trip_id`
- `booking_event_trail.traveler_id` to `travelers.traveler_id`
- `booking_event_trail.lead_id` to `leads.lead_id`
- `booking_event_trail.booking_id` to `trip_bookings.booking_id`
- `booking_event_trail.trip_id` to `trips.trip_id`
- `booking_event_trail.interaction_id` to `interactions.interaction_id`
- `ce_bookings.traveler_id` to `travelers.traveler_id`
- `ce_bookings.event_id` to `community_events.event_id`
- `trip_media.uploaded_by_user_id` to `users.id`
- `assignment_history.previous_user_id` to `users.id`
- `assignment_history.new_user_id` to `users.id`
- `assignment_history.assigned_by_user_id` to `users.id`
- `user_audit_log.actor_user_id` to `users.id`
- `user_audit_log.target_user_id` to `users.id`

## Duplicate Risk Checks

Result: `PASS`

No duplicate groups were found for:

- `trip_bookings.idempotency_key`
- `leads.idempotency_key`
- `handoff_queue.idempotency_key`
- Active `trip_bookings` grouped by `traveler_id` and `trip_id`

## Conclusion

The known migration blocker is isolated to orphan `booking_status_history` rows. The safest repair should preserve those rows as legacy audit data without weakening the production booking foreign key.
