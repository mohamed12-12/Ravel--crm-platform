# Legacy Booking Lifecycle Analysis

Scope: analysis only. No lifecycle statuses were assigned and no bookings were imported.

Source inspected:

- Quarantine file: `.tmp-booking-import-populated/quarantine.json`
- Quarantine reason: `unclear_booking_lifecycle`
- Rows reviewed: 453

## Required Evidence

Lifecycle recovery requires at least one reliable signal from:

- Payment amount
- Deposit amount
- Booking date
- Trip date
- Booking status notes

## Evidence Profile

All 453 unclear lifecycle rows share the same evidence profile:

| Payment Evidence | Date Evidence | Status/Notes Evidence | Rows |
|---|---|---|---:|
| Missing | Missing | Missing | 453 |

The booking rows contain valid-looking booking IDs, traveler references, and trip references in many cases, but no payment/date/status evidence. Because the imported trip records also often lack historical start/end dates, the migration cannot safely infer `Completed`, `Paid`, `Payment Pending`, or `Confirmed`.

## Row Ranges Reviewed

| Workbook Row Range | Decision | Reason |
|---|---|---|
| 3-73 | Not Recoverable | No payment, date, or status-note evidence in booking row. |
| 88-103 | Not Recoverable | No payment, date, or status-note evidence in booking row. |
| 106-121 | Not Recoverable | No payment, date, or status-note evidence in booking row. |
| 123-138 | Not Recoverable | No payment, date, or status-note evidence in booking row. |
| 156-177 | Not Recoverable | No payment, date, or status-note evidence in booking row. |
| 179-192 | Not Recoverable | No payment, date, or status-note evidence in booking row. |
| 195-213 | Not Recoverable | No payment, date, or status-note evidence in booking row. |
| 221-272 | Not Recoverable | No payment, date, or status-note evidence in booking row. |
| 275-277 | Not Recoverable | No payment, date, or status-note evidence in booking row. |
| 279-342 | Not Recoverable | No payment, date, or status-note evidence in booking row. |
| 344-345 | Not Recoverable | No payment, date, or status-note evidence in booking row. |
| 347-394 | Not Recoverable | No payment, date, or status-note evidence in booking row. |
| 409 | Not Recoverable | No payment, date, or status-note evidence in booking row. |
| 411 | Not Recoverable | No payment, date, or status-note evidence in booking row. |
| 413-434 | Not Recoverable | No payment, date, or status-note evidence in booking row. |
| 436-443 | Not Recoverable | No payment, date, or status-note evidence in booking row. |
| 454-461 | Not Recoverable | No payment, date, or status-note evidence in booking row. |
| 469-475 | Not Recoverable | No payment, date, or status-note evidence in booking row. |
| 477 | Not Recoverable | No payment, date, or status-note evidence in booking row. |
| 479-483 | Not Recoverable | No payment, date, or status-note evidence in booking row. |
| 485-493 | Not Recoverable | No payment, date, or status-note evidence in booking row. |
| 495 | Not Recoverable | No payment, date, or status-note evidence in booking row. |
| 497 | Not Recoverable | No payment, date, or status-note evidence in booking row. |
| 499-502 | Not Recoverable | No payment, date, or status-note evidence in booking row. |
| 504-519 | Not Recoverable | No payment, date, or status-note evidence in booking row. |
| 521-540 | Not Recoverable | No payment, date, or status-note evidence in booking row. |
| 556-590 | Not Recoverable | No payment, date, or status-note evidence in booking row. |

## Largest Affected Trip Groups

| Trip ID | Trip Name | Rows |
|---|---|---:|
| `RT-LOC-24-012` | Siwa | 42 |
| `RT-LOC-25-004` | Island hopping 3 | 27 |
| `RT-LOC-24-004` | BELLY DANCING FAYOUM | 24 |
| `RT-LOC-24-005` | ISLAND HOPPING | 24 |
| `RT-LOC-24-006` | MARSA ALAM | 24 |
| `RT-LOC-25-001` | Fayoum Rally | 22 |
| `RT-LOC-24-007` | Island hopping 2 | 21 |
| `RT-LOC-26-002` | BD Retreat 3 | 21 |
| `RT-LOC-24-011` | BD Retreat | 20 |
| `RT-LOC-24-009` | Ras Sudr Yoga Retreat | 18 |

## Result

- Recoverable by evidence in current workbook: 0
- Not recoverable without manual business confirmation: 453

## CTO Decision

Do not assign lifecycle automatically. If the business wants these imported later, create a manual legacy-status decision file first, for example:

- `booking_id`
- `confirmed_final_status`
- `status_evidence`
- `approved_by`
- `approved_at`

