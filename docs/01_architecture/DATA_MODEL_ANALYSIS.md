# Data Model Analysis

Date: 2026-06-17

Scope: active SQLAlchemy models in `apps/api/app/models`, operational runtime schema patches in `UnifiedCRMService`, and aspirational Prisma schema in `database/schema/schema.prisma`.

## Current Data Architecture

The active data model is SQLAlchemy over SQLite, with additional direct `sqlite3` access by `UnifiedCRMService`. The schema is shaped by legacy workbook sheets. The Prisma/PostgreSQL schema is a future V2 idea and is not the active model.

Important architectural issue:

The system has more than one representation of product truth:

- SQLAlchemy models.
- Direct SQL in `UnifiedCRMService`.
- Excel/Google Sheets mappings.
- Prisma V2 schema.
- Test-created temporary schemas.

## Traveler Model

Model: `apps/api/app/models/traveler.py`

Table: `travelers`

### Current Fields

- `traveler_id` primary key.
- Status/name/profile:
  - `status`
  - `full_name`
  - `first_name`
  - `last_name`
  - `birthday`
  - `gender`
  - `nationality`
  - `residence`
  - `primary_language`
- Contact:
  - `phone_code`
  - `whatsapp_raw`
  - `email`
  - `community_whatsapp`
  - `integrated_whatsapp`
  - `normalized_whatsapp`
  - `phone_lookup_key`
- Travel/business metrics:
  - `local_trips_count`
  - `international_trips_count`
  - `total_trips`
  - `community_events_count`
  - `lifetime_revenue`
  - `rating`
  - `lead_source`
- Notes/preferences:
  - `notes`
  - `introduce_yourself`
  - `emergency_contact`
  - `emergency_phone`
  - `medical_notes`
  - `room_preference`
  - `agent_notes`
  - `data_audit`
- Flow links:
  - `current_flow_key`
  - `current_step`
  - `last_lead_id`
  - `last_booking_id`
  - `created_at`
  - `last_contacted_at`
- Passport:
  - `passport_name`
  - `passport_number`
  - `passport_expiry`
  - `passport_nationality`
  - `passport_attachment_ref`

Relationships:

- Trip bookings
- Community event bookings
- Leads
- Interactions

### Missing Fields

- Verified phone flag.
- Preferred contact channel.
- Consent/opt-in status.
- Do-not-contact flag.
- Customer owner/account manager.
- Segment/tier separate from status.
- Risk flags separate from status.
- Data retention/deletion metadata.
- Passport verification status.
- Attachment/document relationship.
- Audit metadata for profile changes.

### Dangerous Fields

- `passport_number`, `passport_attachment_ref`, emergency and medical fields: sensitive PII without access policy.
- `status`: overloaded for Active, VIP, Repeat, Blacklisted, Merged, risk states.
- `phone_lookup_key`: not unique, but used for duplicate logic.
- `lifetime_revenue` and trip counts: can be imported/synced and may not be reliable accounting truth.
- `current_flow_key` and `current_step`: session state stored on customer master but not actively controlled.

### Relationship Problems

- `last_lead_id` and `last_booking_id` are string fields, not formal foreign keys.
- No master account/contact grouping in active model.
- Phone/email are not normalized into separate contact methods.
- Attachments are not modeled.

## Lead Model

Model: `apps/api/app/models/lead.py`

Table: `leads`

### Current Fields

- `lead_id` primary key.
- Timestamps:
  - `created_at`
  - `updated_at`
- Customer:
  - `customer_name`
  - `raw_phone`
  - `integrated_whatsapp`
  - `phone_lookup_key`
- Relationship/status:
  - `traveler_id`
  - `traveler_status`
  - `customer_tier`
  - `match_status`
- Pipeline:
  - `lead_stage`
  - `lead_source`
  - `channel`
  - `preferred_trip_type`
  - `interested_trip_ids`
  - `suggested_trip_ids`
  - `priority`
  - `follow_up_status`
  - `follow_up_due_date`
- Operational:
  - `last_interaction_id`
  - `interaction_count`
  - `handoff_required`
  - `handoff_reason`
  - `notes`
- Automation:
  - `flow_key`
  - `current_step`
  - `language`
  - `trigger_keyword`
  - `waitlist_id`
  - `handoff_id`
  - `booking_id`
- Audit:
  - `source_sheet`
  - `source_row`

### Missing Fields

- Owner/assignee.
- Opportunity ID.
- Lead source campaign.
- Close/lost reason.
- Expected value.
- Probability.
- Next action task ID.
- Automation active flag.
- SLA fields.
- Consent/channel opt-in.

### Dangerous Fields

- `interested_trip_ids` and `suggested_trip_ids` are comma-separated text instead of relationships.
- `lead_stage` is uncontrolled string.
- `handoff_required` can be true without a valid `handoff_id`.
- `booking_id` is string and not a DB foreign key.
- `match_status` captures identity resolution but has no immutable audit.

### Relationship Problems

- One lead can be overwritten by new inquiries due to upsert logic.
- Lead-to-booking is weak.
- Lead-to-handoff is weak.
- Lead is overloaded as inquiry, opportunity, flow state, and follow-up task.

## Booking Model

Model: `apps/api/app/models/booking.py`

Table: `trip_bookings`

### Current Fields

- `booking_id` primary key.
- Keys:
  - `trip_id`
  - `trip_name`
  - `traveler_id`
  - `traveler_name`
- Details:
  - `room_type`
  - `flight_option`
  - `date_option`
  - `currency`
- Status/audit:
  - `booking_status`
  - `draft_created_at`
  - `booking_source`
  - `lead_id`
  - `interaction_id`
  - `alert_id`
  - `payment_status`
  - `booking_notes`

### Missing Fields

- Hold expiration.
- Confirmed at/by.
- Cancelled at/by/reason.
- Payment due amount.
- Deposit amount.
- Total price.
- Price breakdown.
- Payment transaction relationship.
- Passenger count.
- Room assignment.
- Passport/document completion status.
- Invoice/receipt references.
- Operations status.

### Dangerous Fields

- `booking_status` is manually editable and treated as confirmation.
- `payment_status` is manually editable with no ledger.
- `trip_name` and `traveler_name` duplicate linked record data.
- `lead_id` and `interaction_id` are not formal foreign keys in the SQLAlchemy model.
- Draft holds increment on booking create but no release logic exists.

### Relationship Problems

- Booking event trail references booking, but lifecycle rules are not enforced.
- Booking-to-payment does not exist.
- Booking-to-documents does not exist.
- Booking-to-inventory ledger does not exist.

## Trip Model

Model: `apps/api/app/models/trip.py`

Table: `trips`

### Current Fields

- `trip_id` primary key.
- Core:
  - `trip_name`
  - `type`
  - `year`
  - `trip_leader`
  - `start_date`
  - `end_date`
  - `sales_status`
  - `data_audit`
- Extensions:
  - `trip_window_status`
  - `trip_availability_note`
  - `next_reengage_date`
- Inventory/capacity:
  - `single_total`
  - `double_total`
  - `triple_total`
  - `single_remaining`
  - `double_remaining`
  - `triple_remaining`
  - `draft_holds_single`
  - `draft_holds_double`
  - `draft_holds_triple`
- Content:
  - `public_price`
  - `public_description`
  - `sales_notes`

### Missing Fields

- Destination/country/city.
- Supplier/DMC.
- Itinerary.
- Inclusions/exclusions.
- Structured price/currency.
- Structured discount.
- Flight included flag.
- Visa requirement metadata.
- Passport required flag.
- Cancellation policy.
- Operational status separate from sales status.
- Departure/product separation.

### Dangerous Fields

- `type` is generic and should be controlled.
- `public_price` is text.
- `sales_notes` may contain business rules/discounts in free text.
- Inventory counters can be manually edited without ledger.
- Draft holds have no timestamp or owner.

### Relationship Problems

- Trip acts as both product and departure.
- No inventory transaction table.
- No pricing relationship.
- No document requirement relationship.

## Handoff Model

Model: `apps/api/app/models/handoff.py`

Table: `handoff_queue`

### Current Fields

- `handoff_id` primary key.
- `created_at`
- Keys:
  - `lead_id`
  - `traveler_id`
  - `trip_id`
- Details:
  - `flow_key`
  - `reason`
  - `priority`
  - `channel`
  - `status`
  - `owner`
  - `assigned_to`
  - `notes`

### Missing Fields

- Automation lock ID.
- Conversation/session ID.
- Transcript snapshot.
- SLA due time.
- First response time.
- Resolved time.
- Resolution category.
- Escalation level.
- Assigned team.
- Created by/updated by.

### Dangerous Fields

- `status` is uncontrolled string.
- `reason` is free text/string code mixed.
- `priority` is string in Flask, integer in Prisma future schema.
- `owner` and `assigned_to` overlap.

### Relationship Problems

- Handoff is linked to lead/traveler/trip but no relationship to agent session or channel thread.
- No guarantee that handoff stops automation.
- No event history for assignment/status changes.

## Additional Models Worth Noting

### Interaction

Stores message/action audit fields but not full message transcript. It lacks direction/content fields in the active model even though UI references direction in places.

### Booking Event Trail

Good start for immutable booking/lead events. Needs broader use and stronger event taxonomy.

### Copy Library

Supports message copy by key/language. Useful for deterministic agent copy, but no workflow to manage approvals in production.

## Prisma Schema Status

`database/schema/schema.prisma` introduces:

- `MasterAccount`
- `Traveler`
- `Lead`
- `CopyLibrary`
- `FlowState`
- `HandoffQueue`
- `Interaction`
- `Booking`

This schema is much simpler than the active SQLAlchemy schema and does not cover current operational fields. It should be treated as a draft direction, not a migration-ready source of truth.

