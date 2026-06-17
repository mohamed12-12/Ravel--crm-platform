# CRM Business Analysis

Date: 2026-06-17

This document analyzes the current CRM modules as business systems, not just code screens.

## Leads Section

### Purpose

Leads represent customer sales inquiries and automation outcomes. They hold pipeline stage, source/channel, trip interest, suggested trips, priority, follow-up status/due date, handoff flags, language, and links to traveler/booking.

### Current Logic

- Manual lead creation calls `UnifiedCRMService.record_agent_outcome`.
- Agent-confirmed trip interest also calls `record_agent_outcome`.
- Identity resolution determines whether to create traveler, reuse traveler, or create handoff.
- Lead stages are derived from traveler status and trip availability.
- Existing leads may be updated/upserted by phone lookup or traveler rather than always creating a fresh opportunity.
- Lead dashboard groups stages into high-level pipeline buckets.
- Leads can be manually updated, advanced, marked lost, and converted into handoff.

### Problems

- Lead is doing too much: inquiry, opportunity, automation session, and follow-up task.
- Upsert behavior can overwrite or mutate an existing lead when a new distinct opportunity should be created.
- Pipeline stages are string values with no controlled enum table.
- Lead ownership is missing.
- Sales SLA is missing.
- Follow-up tasks are fields, not a proper task/activity model.
- No explicit lost reason, closed-won date, or conversion source.
- Handoff is linked by string and not enforced as a lifecycle state machine.

### Missing Functionality

- Opportunity-per-trip or opportunity-per-sales-cycle model.
- Lead owner, team, queue, SLA, next action.
- Structured activity/task table.
- Lead score and qualification criteria.
- Lost reason and reactivation logic.
- Consent and channel opt-in status.
- Automation active/inactive flag in the active Flask model.
- Pipeline transition guardrails.

### ERP/CRM Best Practices

- Separate contact/traveler from lead/opportunity.
- Use controlled stage transitions.
- Store owner, source, campaign, product/trip interest, probability, expected value, next action, due date, and close date.
- Keep immutable activity history.
- Do not overwrite historical opportunities.
- Model handoff as assignment/escalation with SLA.

### Recommended Future Design

- Traveler/contact table is the customer master.
- Lead is an inquiry record.
- Opportunity is a sales cycle for a specific trip or package.
- Task/activity handles follow-up.
- Handoff is an escalation linked to lead/opportunity/session.
- Add clear statuses:
  - New
  - Contacted
  - Qualified
  - Proposal Sent
  - Booking Draft
  - Deposit Pending
  - Confirmed
  - Lost
  - Blocked
- Make stage transitions explicit and audited.

## Travelers Section

### Purpose

Travelers are customer master records. They store identity, profile, phone, travel counts, preferences, emergency/medical information, revenue, language, current flow state, last lead, last booking, and passport fields.

### Current Logic

- Travelers can be imported from sheets.
- New traveler IDs are sequential `TR00001` style.
- Create route prevents creating a traveler with a blacklisted duplicate phone.
- Create route redirects to existing profile on duplicate phone.
- Update route normalizes phones and prevents update to blacklisted phone.
- Delete marks traveler as inactive.
- Detail page shows related leads, bookings, community event bookings, interactions, handoffs, and booking event trail.
- Stats may be refreshed from sheets.
- Duplicate merge moves bookings/leads/interactions/handoffs to master and marks aliases `Merged`.

### Problems

- Traveler is both customer master and workflow state holder.
- Sensitive medical/emergency/passport fields have no access controls.
- Passport fields exist but no secure document model.
- Phone lookup is not uniquely constrained.
- Sequential IDs from max value are concurrency-risky.
- Status values mix lifecycle, segment, risk, blacklist, and merge status.
- Counts/revenue can be imported/synced rather than reliably computed.

### Missing Functionality

- Master account/contact grouping model in active DB.
- Consent records.
- Attachment/document model.
- Data retention and deletion workflow.
- Audit log for profile changes.
- Role-based visibility for sensitive fields.
- Duplicate merge audit history.
- Preferred channel and do-not-contact status.

### ERP/CRM Best Practices

- Maintain a stable customer master with immutable ID.
- Separate customer status, tier, risk flags, and lifecycle.
- Use normalized contact points table for phone/email/WhatsApp.
- Use document management for passports.
- Audit all changes to identity, phone, and sensitive fields.
- Enforce uniqueness or review workflow for verified identifiers.

### Recommended Future Design

- Add `customer_account` or `master_account`.
- Add `contact_method` table.
- Add `customer_flag` table for blacklist/payment risk/VIP.
- Add `document` and `attachment` tables.
- Add `profile_change_audit`.
- Keep `traveler_id` stable forever.

## Trips Section

### Purpose

Trips are sellable travel products. They store trip identity, type, dates, leader, sales status, inventory totals/remaining/draft holds, public price/description, and sales notes.

### Current Logic

- Trips can be listed, filtered, created, updated, cancelled.
- Trip ID can be user-entered or generated.
- Inventory can be updated through JSON route.
- Trip sync writes selected columns to sheets.
- Recommendation uses sales status, date, capacity, availability note, and trip type.
- Booking draft increments draft holds.

### Problems

- Product catalog fields are thin.
- Price is a string, not structured pricing.
- No package options, supplier/DMC, itinerary, inclusions/exclusions, cancellation policy, or currency rules.
- Inventory is room-type counts only.
- Draft holds do not expire.
- Cancellation does not release holds or notify linked leads/bookings.
- Flight support is only a text option.
- Discount support is free-text notes.

### Missing Functionality

- Structured pricing table.
- Date options/departures.
- Inventory ledger.
- Hold expiration and release.
- Supplier operations fields.
- Trip media/content publishing workflow.
- Booking rules per trip.
- Visa/passport requirements per destination.

### ERP/CRM Best Practices

- Product/trip master separate from departure/inventory.
- Inventory ledger instead of only counters.
- Pricing and discounts as structured entities.
- Sales status and operational status separated.
- Capacity changes audited.

### Recommended Future Design

- `trip` as product/package.
- `trip_departure` for dates.
- `trip_inventory` and `inventory_transaction`.
- `trip_price` and `trip_discount`.
- `trip_requirement` for passport/visa/flight needs.
- `trip_content` for public-facing copy.

## Bookings Section

### Purpose

Bookings represent reservations on trips. They currently support booking draft creation, status/payment updates, and event trail history.

### Current Logic

- Booking draft requires trip, traveler, room type.
- Trip must be open.
- Room capacity must be available after draft holds.
- Draft hold is incremented.
- Booking gets status `Draft`.
- Payment defaults to `Pending` or `Awaiting Deposit`.
- Booking status can be manually updated to Draft/Confirmed/Cancelled.
- Payment status can be manually updated.
- Event trail logs booking draft and payment follow-up.

### Problems

- Confirmation is just a status field update.
- No payment transaction, receipt, deposit amount, due amount, or currency conversion.
- No hold expiration.
- No cancellation release logic.
- No passenger count, room occupancy, roommate pairing, passport completion flag, or operational checklist.
- No invoice/receipt/customer communication.
- No immutable booking lifecycle state machine.

### Missing Functionality

- Booking quote vs reservation vs confirmed booking separation.
- Payment model.
- Hold expiry worker.
- Cancellation/refund workflow.
- Rooming list.
- Passenger documents.
- Operations handoff.
- Confirmation message and customer-visible proof.

### ERP/CRM Best Practices

- Booking lifecycle should be governed:
  - Draft
  - Hold
  - Deposit Pending
  - Confirmed
  - Fully Paid
  - Cancelled
  - Refunded
- Payments should be ledger entries.
- Inventory changes should be transactions, not counter edits.
- Every confirmation/cancellation should have actor/time/reason.

### Recommended Future Design

- Add `booking_status_history`.
- Add `payment_transaction`.
- Add `booking_hold` with expiration.
- Add `room_assignment`.
- Add `booking_document_requirement`.
- Add confirmation workflow and customer notification.

## Handoffs Section

### Purpose

Handoffs are requests for human review or takeover when automation should not continue.

### Current Logic

- Handoff queue stores handoff ID, lead, traveler, trip, flow, reason, priority, channel, status, owner/assigned_to, notes.
- Handoffs can be created by manual lead conflict/blacklist, API calls, or manual lead action.
- Board organizes Pending/In Progress/Resolved.
- Admin can assign or resolve.
- WebSocket event notifies new handoff.

### Problems

- Handoff does not guarantee automation is stopped.
- No assignment rules.
- No SLA/escalation.
- No reason taxonomy.
- No transcript/context package.
- No ownership audit.
- Some handoff recommendations, such as unknown visa info, do not create queue records.
- TypeScript middleware has a Prisma handoff service but it is not wired into the current running product.

### Missing Functionality

- Automation lock per traveler/channel/session.
- Human reply tracking.
- Handoff transcript.
- Agent resume policy.
- Queue ownership and escalation.
- Notifications outside the web page.
- Reporting on time-to-first-response and time-to-resolution.

### ERP/CRM Best Practices

- Treat handoff as a case/ticket.
- Store queue, owner, priority, SLA, reason, status, resolution type.
- Lock AI automation while case is active.
- Audit every owner/status change.
- Link to conversation transcript and customer profile.

### Recommended Future Design

- Rename or extend to `case` / `handoff_case`.
- Add `automation_lock`.
- Add `case_event`.
- Add `case_assignment`.
- Add SLA timers.
- Add escalation policy.
- Integrate with Instagram/WhatsApp conversation inbox.

