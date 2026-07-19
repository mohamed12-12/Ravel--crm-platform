# Employee Follow-up Workflow

## Daily Work Queue

Employees start on the dashboard work queue:

- overdue follow-ups
- follow-ups due today
- new unassigned leads
- customers waiting for response
- deposit follow-ups
- missing documents

All rows link back to verified CRM lead or booking records.

## Booking Workflow

1. Open booking detail.
2. Review current status, payment status, traveler, trip, documents, and activity.
3. Set status/payment only through allowed transitions.
4. Add internal note if needed.
5. Set assigned employee, priority, next action, and next follow-up.
6. Save.
7. CRM writes status history and event trail before the page claims success.

## Lead Workflow

1. Open lead detail.
2. Review pipeline stage, priority, follow-up due date, customer response, documents, interactions, and activity.
3. Use quick actions for common backend-verified moves.
4. Edit lead details for owner, next action, follow-up date, or stage.
5. CRM validates stage transition and records activity.

## Conflict Handling

Booking detail submits expected status-history count.

Lead detail submits expected `updated_at`.

If the record changed after page load, the save is rejected with a friendly conflict message.
