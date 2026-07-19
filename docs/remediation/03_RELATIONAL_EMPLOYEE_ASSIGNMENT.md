# Relational Employee Assignment Remediation

## Scope

This remediation replaces unsafe free-text ownership for leads and bookings with verified employee IDs while preserving CRM business rules, status transitions, activity history, and legacy display data.

## Implementation

- Reused the existing `User` model.
- Added database-backed role lookup and permission checks.
- Added relational ownership and assignment metadata to leads/bookings.
- Added durable assignment history and user administration audit logs.
- Added admin employee management and manager/admin assignment controls.
- Added relational work queues and inactive-owner visibility.
- Kept exact-match compatibility backfill for existing text values.

## Operational migration

Apply Alembic revision `c61e4a2f9b10` after `b52e9d6a31f4`. The migration creates missing user/audit tables, adds ownership columns and indexes, and links only deterministic legacy values. Review unmatched or ambiguous legacy values before manually assigning them.

## Explicit non-goals

No deployment hardening, CRM data model redesign, booking rule redesign, or AI-agent conversation redesign is part of this change.
