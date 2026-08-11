# Phase 2 - Refund statuses and amount

## What this phase delivers

Bookings can show Full Refund or Partial Refund payment statuses, track the refund amount, restrict payment-status changes to Admin/Manager, allow Admin/Manager/Sales notes, and let the AI agent report verified refund status and amount when asked.

## Current state (investigated, not assumed)

- `TripBooking` has `payment_status` and `booking_notes`, but no refund amount field (`apps/api/app/models/booking.py:40-43`).
- Current payment statuses are `Pending`, `Deposit Paid`, `Fully Paid`, and `Refunded` (`apps/api/app/routes/bookings.py:38`).
- Payment transitions currently allow moving to generic `Refunded`, not separate full/partial refund statuses (`apps/api/app/routes/bookings.py:39-44`).
- Booking status update accepts `payment_status` and notes from the same handler (`apps/api/app/routes/bookings.py:312-318`), validates payment status against `PAYMENT_STATUSES` (`apps/api/app/routes/bookings.py:337-339`), applies payment status directly (`apps/api/app/routes/bookings.py:395-396`), and appends notes (`apps/api/app/routes/bookings.py:413`).
- RBAC currently gives Admin/Manager `assign_work` and `view_all`; Agent/Sales only have `update_followup` (`apps/api/app/security.py:24-28`). There is no dedicated payment-status permission in the current permission map.
- Booking detail template renders the payment status menu but no refund amount input (`apps/api/app/templates/bookings/detail.html:202-204`).

## Confirmed requirements this phase must satisfy

From the provided task attachment: add payment status values "Full Refund" and "Partial Refund", add a refund-amount field, split permissions so Admin/Manager can change status and all three roles can add a note, and have the AI agent surface refund status/amount when asked. The task says to extend existing payment-status enum and RBAC patterns, not replace them.

## Working assumptions (for Phases 6 and 7 specifically)

Not applicable.

## Design approach

- Add a nullable refund amount column to `trip_bookings` through the active migration path.
- Extend payment status constants/transitions with `Full Refund` and `Partial Refund`. Decide whether to keep legacy `Refunded` for old records and filters.
- Add a focused permission such as `change_payment_status` for Admin/Manager while keeping `update_followup` for note-only updates by Sales.
- Split the booking update handler behavior so Sales can submit notes without changing payment status.
- Add the refund amount to booking detail/index UI where payment status is shown.
- Extend read-only booking lookup/tool output so the agent can report only stored refund status/amount.

## Dependencies on other phases

Depends on Phase 1 only for payment terminology consistency. It can otherwise be implemented independently.

## Risks specific to this phase

This touches the same status write path that already has validation and history protections (`apps/api/app/routes/bookings.py:323-346`, `apps/api/app/routes/bookings.py:377-396`). The implementation must not bypass transition validation or the verified write-result boundary documented in the agent code (`services/ai_agent/ai_agent_app/agent/write_tool_executor.py:1082-1124`).

## Tests

Not run. No code was changed in this documentation-only pass.

## Live verification

Not run. No deployed app behavior was changed or verified in this documentation-only pass.

