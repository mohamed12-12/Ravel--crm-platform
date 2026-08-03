# Rollback Plan

Date: 2026-08-02

## Rollback Triggers

- AI runtime cannot initialize after deployment.
- CRM lookup/write paths return repeated 5xx errors.
- Booking duplicate prevention fails.
- Customer-facing responses leak internals or claim false success.
- Admin dashboard cannot load or authenticate.
- Middleware health fails after restart.

## Immediate Actions

1. Disable external traffic to AI chat/webhook entrypoints if customer impact is active.
2. Keep `AGENT_TOOL_ROUTER_MODE=dry_run`; do not switch to global enforce as a mitigation.
3. If write behavior is suspicious, set `AGENT_WRITE_TOOL_ENFORCEMENT=true` and disable direct integration write routes at the edge/API gateway.
4. Restore the previous known-good deployment artifact/commit.
5. Restore DB from the pre-deploy backup only if data corruption occurred; otherwise avoid data rollback and reconcile forward.

## Data Rollback

- Take a fresh incident snapshot before restoring anything.
- Restore database backup to a staging clone first and verify counts/checksums.
- Restore upload storage only if document data was corrupted or misplaced.
- Never run demo reset against production.

## Verification After Rollback

- CRM dashboard loads.
- AI health endpoint returns 200.
- Direct booking bypass remains blocked.
- Existing bookings/leads/handoffs are visible.
- No new duplicate booking was created during incident window.

## Communication

- Document incident start/end time.
- Record deployed commit before and after rollback.
- Record affected sessions/bookings/leads/handoffs.
- Rotate any exposed tokens immediately.
