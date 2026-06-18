# Trip Reference Recovery

Scope: analysis only. No booking rows were imported and no trip references were changed.

Source inspected:

- Quarantine file: `.tmp-booking-import-populated/quarantine.json`
- CRM database used for analysis: `.tmp-booking-import-populated/crm.db`
- Quarantine reason: `invalid_booking_trip_reference`
- Invalid booking trip references reviewed: 89

## Recovery Policy

High confidence requires enough evidence to map a booking to exactly one CRM trip using trip name, destination/name, year, and dates.

The booking sheet contains `Trip ID` and `Trip Name`, but the invalid rows do not contain booking-level trip dates. Several workbook Trip IDs are duplicated or absent from the imported CRM trip table. Therefore no invalid trip reference is safe for automatic recovery in this sprint.

## Grouped Recovery Review

| Booking Row(s) | Workbook Trip Value | Proposed CRM Trip | Confidence | Reason |
|---|---|---|---|---|
| 74-80, 82-87 | `RT-INT-25-002` / Serbia & Bosnia | Blocked: duplicate trip resolution required | Medium | Trip name identifies one of the duplicate workbook rows, but that trip is not imported and final Trip ID is not confirmed. No dates/prices/leader exist for safe automated assignment. |
| 395-405, 407-408 | `RT-INT-25-002` / Zanzibar 2 | Blocked: duplicate trip resolution required | Medium | Trip name identifies the other duplicate workbook row, but that trip is not imported and final Trip ID is not confirmed. No dates/prices/leader exist for safe automated assignment. |
| 139-150, 153-155 | `RT-INT-25-003` / Oman | Blocked: duplicate trip resolution required | Medium | Trip name identifies one duplicate workbook row, but final CRM Trip ID is not confirmed. |
| 444-449, 451-452 | `RT-INT-25-003` / Lebanon 2 | Blocked: duplicate trip resolution required | Medium | Trip name identifies the other duplicate workbook row, but final CRM Trip ID is not confirmed. |
| 216-220 | `RT-INT-25-004` / Morocco | Blocked: duplicate trip resolution required | Medium | Trip name identifies one duplicate workbook row, but final CRM Trip ID is not confirmed. |
| 462-468 | `RT-INT-25-004` / Spain | Blocked: duplicate trip resolution required | Medium | Trip name identifies the other duplicate workbook row, but final CRM Trip ID is not confirmed. |
| 594 | `RT-INT-26-007` / Georgia & Armenia | Possible `RT-INT-26-005`, not import-ready | Medium | CRM has one `Georgia & Armenia` trip in 2026, but the workbook booking references a different missing Trip ID and has no booking-level date to prove it is the same departure. |
| 595 | `RT-INT-26-008` / Georgia & Armenia | Possible `RT-INT-26-005`, not import-ready | Medium | Same as above. Missing original Trip ID prevents automatic reassignment. |
| 599 | `RT-INT-26-007` / Maldives | None | Low | CRM has `Maldives` as `RT-INT-25-001`, but the booking reference is 2026. Year/Trip ID evidence conflicts. |
| 600 | `RT-INT-26-008` / Maldives | None | Low | CRM has `Maldives` as `RT-INT-25-001`, but the booking reference is 2026. Year/Trip ID evidence conflicts. |
| 601 | `RT-INT-26-009` / Maldives | None | Low | CRM has `Maldives` as `RT-INT-25-001`, but the booking reference is 2026. Year/Trip ID evidence conflicts. |
| 602 | `RT-INT-26-010` / Maldives | None | Low | CRM has `Maldives` as `RT-INT-25-001`, but the booking reference is 2026. Year/Trip ID evidence conflicts. |
| 603 | `RT-INT-26-011` / Maldives | None | Low | CRM has `Maldives` as `RT-INT-25-001`, but the booking reference is 2026. Year/Trip ID evidence conflicts. |
| 604 | `RT-INT-26-012` / Maldives | None | Low | CRM has `Maldives` as `RT-INT-25-001`, but the booking reference is 2026. Year/Trip ID evidence conflicts. |
| 607 | `RT-LOC-26-009` / Nuweiba | None | Low | No exact imported CRM trip with this Trip ID or exact trip name. |
| 608 | `RT-LOC-26-010` / Nuweiba | None | Low | No exact imported CRM trip with this Trip ID or exact trip name. |
| 609 | `RT-LOC-26-011` / Nuweiba | None | Low | No exact imported CRM trip with this Trip ID or exact trip name. |
| 610 | `RT-LOC-26-012` / Nuweiba | None | Low | No exact imported CRM trip with this Trip ID or exact trip name. |
| 611 | `RT-LOC-26-013` / Nuweiba | None | Low | No exact imported CRM trip with this Trip ID or exact trip name. |
| 612 | `RT-LOC-26-014` / Nuweiba | None | Low | No exact imported CRM trip with this Trip ID or exact trip name. |
| 613 | `RT-LOC-26-015` / Nuweiba | None | Low | No exact imported CRM trip with this Trip ID or exact trip name. |
| 614 | `RT-LOC-26-016` / Nuweiba | None | Low | No exact imported CRM trip with this Trip ID or exact trip name. |
| 615 | `RT-LOC-26-017` / Nuweiba | None | Low | No exact imported CRM trip with this Trip ID or exact trip name. |
| 616 | `RT-LOC-26-018` / Nuweiba | None | Low | No exact imported CRM trip with this Trip ID or exact trip name. |
| 617 | `RT-LOC-26-019` / Nuweiba | None | Low | No exact imported CRM trip with this Trip ID or exact trip name. |
| 618 | `RT-LOC-26-020` / Nuweiba | None | Low | No exact imported CRM trip with this Trip ID or exact trip name. |
| 620 | `RT-LOC-26-022` / Nuweiba | None | Low | No exact imported CRM trip with this Trip ID or exact trip name. |
| 621 | `RT-LOC-26-023` / Nuweiba | None | Low | No exact imported CRM trip with this Trip ID or exact trip name. |
| 622 | `RT-LOC-26-024` / Nuweiba | None | Low | No exact imported CRM trip with this Trip ID or exact trip name. |
| 623 | `RT-LOC-26-025` / Nuweiba | None | Low | No exact imported CRM trip with this Trip ID or exact trip name. |
| 624 | `RT-LOC-26-026` / Nuweiba | None | Low | No exact imported CRM trip with this Trip ID or exact trip name. |
| 625 | `RT-LOC-26-027` / Nuweiba | None | Low | No exact imported CRM trip with this Trip ID or exact trip name. |
| 627 | `RT-INT-26-007` / Sri Lanka | None | Low | No imported CRM trip with this Trip ID or exact trip name. |
| 628 | `RT-INT-26-008` / Sri Lanka | None | Low | No imported CRM trip with this Trip ID or exact trip name. |

## Result

- High confidence recoverable trip references: 0
- Medium confidence references requiring manual trip resolution first: 63
- Low confidence references remaining quarantined: 26

## CTO Decision

Do not proceed with automatic trip-reference recovery. The duplicate/missing trip records must be resolved manually before any booking import can use them.

