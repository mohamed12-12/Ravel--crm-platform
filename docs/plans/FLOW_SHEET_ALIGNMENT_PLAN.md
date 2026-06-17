# Rahma Traveler Flow-to-Sheet Alignment Plan

Date: 2026-05-12

## 1. Purpose

This plan maps the PDF workflow into the workbook so every sheet agrees with the others and the agent never has to guess.

The goal is to support the real DM flows with sheet-backed state, shared booking logic, bilingual copy, reminders, and handoff rules while avoiding hallucination.

## 2. Workbook Facts Confirmed

The current workbook `RT - Travelers Database.phase5.ready.xlsx` already contains these operational sheets:

- `Trips`
- `Travelers`
- `Leads`
- `Trip Bookings`
- `Interactions`
- `Booking Alerts`
- `Community Events`
- `CE Bookings`
- `Phase 0 Audit`
- `Needs update Read Me`
- `uncleaned data - reference`

The important existing relationships are:

- `Travelers` is the customer master table.
- `Trips` is the trip product table.
- `Leads` is the sales pipeline table.
- `Interactions` is the conversation audit table.
- `Trip Bookings` is the booking draft / booking record table.
- `Booking Alerts` is the internal notification table.

## 3. Core Principle

One source of truth per idea:

- Customer identity lives in `Travelers`.
- Trip inventory lives in `Trips`.
- Conversation state lives in `Leads` or a dedicated flow-state sheet.
- Every DM touch lives in `Interactions`.
- Every booking draft lives in `Trip Bookings`.
- Internal notifications live in `Booking Alerts`.

No sheet should repeat business logic in a different form unless it is a derived field.

## 4. What The PDF Flows Need

The PDF describes 6 flows:

1. Trigger keyword DM.
2. New follower welcome.
3. Booking confirmation and pre-trip drip.
4. Abandoned inquiry / no reply.
5. Custom / private trip inquiry.
6. Post-trip review / re-engagement.

These all share common pieces:

- Language choice.
- Trip teaser and trip details.
- Shared booking flow.
- Waitlist and follow-up reminders.
- Human handoff.

## 5. Recommended Sheet Architecture

Keep the current sheets and add only the missing control sheets.

### Keep As-Is

- `Travelers`
- `Trips`
- `Leads`
- `Interactions`
- `Trip Bookings`
- `Booking Alerts`
- `Community Events`
- `CE Bookings`

### Add New Control Sheets

- `DM Flow States`
- `DM Copy Library`
- `Waitlist`
- `Follow Ups`
- `Trip Drips`
- `Handoff Queue`
- `Language Templates`

These sheets are not for decoration. They are the missing parts that let the flows work without the agent making things up.

## 6. Sheet Roles

### `Travelers`

Purpose:

- One row per customer.

Must contain:

- Identity fields.
- WhatsApp fields.
- Status.
- Travel counts.
- Notes.
- Master language preference if known.

Used by:

- Flow 1.
- Flow 2.
- Flow 3.
- Flow 6.

### `Trips`

Purpose:

- One row per trip.

Must contain:

- Trip ID.
- Trip name.
- Type.
- Year.
- Start date.
- End date.
- Sales status.
- Public description.
- Public price.
- Remaining capacity.
- Draft holds.

Used by:

- Flow 1.
- Flow 2.
- Flow 3.
- Flow 4.
- Flow 6.

### `Leads`

Purpose:

- One row per active sales conversation or lead.

Must contain:

- Lead ID.
- Customer link.
- Trip interest.
- Current flow.
- Current step.
- Language.
- Waitlist flag.
- Follow-up schedule.
- Handoff status.

Used by:

- Flow 1.
- Flow 2.
- Flow 4.
- Flow 5.

### `Interactions`

Purpose:

- Full conversation log.

Must contain:

- Timestamp.
- Channel.
- Message summary.
- Trigger.
- Step entered.
- Step exited.
- Agent action.
- Handoff reason if any.

Used by:

- All flows.

### `Trip Bookings`

Purpose:

- Booking draft and booking record layer.

Must contain:

- Booking ID.
- Traveler ID.
- Trip ID.
- Room type.
- Flight choice.
- Date choice.
- Currency.
- Deposit status.
- Payment status.

Used by:

- Flow 1.
- Flow 2.
- Flow 3.

### `Booking Alerts`

Purpose:

- Internal team notifications.

Must contain:

- Alert ID.
- Trip ID.
- Lead ID.
- Booking ID.
- Alert type.
- Priority.
- Owner.
- Status.

Used by:

- Flow 3.
- Flow 5.

### `Waitlist`

Purpose:

- People who said "not now" or did not choose a trip yet.

Must contain:

- Contact reference.
- Trip interest.
- Language.
- Last touch.
- Re-engage time.
- Re-engagement reason.

Used by:

- Flow 1.
- Flow 2.
- Flow 4.

### `Trip Drips`

Purpose:

- Scheduled messages tied to trip dates.

Must contain:

- Trip ID.
- D-7 message.
- D-3 message.
- D-1 message.
- Day-of message.
- Post-trip message.

Used by:

- Flow 3.
- Flow 6.

### `Handoff Queue`

Purpose:

- Cases that need a human.

Must contain:

- Lead ID.
- Reason.
- Priority.
- Owner.
- Status.

Used by:

- Flow 1.
- Flow 2.
- Flow 4.
- Flow 5.

### `DM Copy Library`

Purpose:

- All approved message copy in both Arabic and English.

Must contain:

- Flow name.
- Step name.
- Message key.
- Arabic copy.
- English copy.
- Optional variables.

Used by:

- Every flow.

## 7. Flow To Sheet Mapping

### Flow 1 Trigger Keyword DM

Entry:

- Keyword trigger from comments or DM.

Sheet actions:

1. Create or update a `Lead`.
2. Create an `Interaction` row.
3. Store language choice.
4. Pull trip teaser text from `DM Copy Library`.
5. Pull trip details from `Trips`.
6. If interested, move through shared booking module.
7. If no or later, add to `Waitlist`.
8. Schedule re-engagement 48 hours before trip closes.

Important:

- The teaser, full details, and booking prompts must come from sheet copy, not from model memory.

### Flow 2 New Follower Welcome

Entry:

- New follower detected.

Sheet actions:

1. Create `Lead`.
2. Create `Interaction`.
3. Save language.
4. Show action choice from `DM Copy Library`.
5. Route into browse, book, or ask-question branch.
6. Merge all branches into the same shared booking module.

Important:

- Browse and book are not different booking systems. They are the same shared module with different entry points.

### Flow 3 Booking Confirmation + Pre-Trip Drip

Entry:

- Team marks deposit confirmed.

Sheet actions:

1. Update `Trip Bookings`.
2. Update `Leads` stage.
3. Create confirmation `Interaction`.
4. Queue upsell prompt.
5. Load pre-trip reminders from `Trip Drips`.
6. Trigger `Trip Drips` at D-7, D-3, D-1.
7. Trigger check-in message on trip day.

Important:

- These reminders should be scheduled from trip dates, not written as ad hoc text.

### Flow 4 Abandoned Inquiry / No Reply

Entry:

- No reply after a timeout.

Sheet actions:

1. Mark `Lead` as stalled or cold.
2. Add to `Follow Ups`.
3. Add soft nudge at 24h.
4. Add urgency nudge at 48h.
5. If still no reply, move to cold queue for next launch.

Important:

- The follow-up timing should be stored in the sheet so the agent never guesses the reminder window.

### Flow 5 Custom / Private Trip Inquiry

Entry:

- Keywords like `private trip`, `group trip`, `customized`.

Sheet actions:

1. Create `Lead`.
2. Record qualifying answers.
3. Store budget, destination, group size, and dates.
4. Add to `Handoff Queue`.
5. Create an internal note for manual quote.

Important:

- Do not auto-book custom trips.
- Do not invent pricing for private quotes.

### Flow 6 Post-Trip Review + Re-Engagement

Entry:

- Triggered by trip end date.

Sheet actions:

1. Pull traveler and booking details.
2. Send wrap-up DM from `DM Copy Library`.
3. Store star rating request.
4. Store UGC prompt.
5. Offer next-trip early-bird or referral prompt.

Important:

- This flow should be driven by booked trip dates, not by model memory.

## 8. Shared Booking Module

Build one shared booking module and let Flow 1 and Flow 2 call it.

Steps inside the module:

1. Personal info.
2. Room type.
3. Flights with or without.
4. Date selection if there are multiple options.
5. Currency.
6. Deposit amount.
7. Bank details.

Why this matters:

- It avoids duplicated logic.
- It reduces maintenance.
- It reduces the chance of one flow drifting away from the other.

## 9. Language Handling

Every message node must have an Arabic and English version.

Plan for each message node:

- `message_key`
- `ar_text`
- `en_text`
- optional `variables`
- optional `buttons`

Rules:

- Do not let the AI improvise the Arabic or English copy.
- The agent should select from approved copy only.
- If a node has no translation, mark it incomplete before release.

## 10. Hallucination Controls

The agent must never guess data that is supposed to come from sheets.

Hard rules:

- Trip names come from `Trips`.
- Dates come from `Trips` or `Trip Drips`.
- Capacity comes from `Trips`.
- Booking state comes from `Trip Bookings`.
- Follow-up timing comes from `Follow Ups`.
- Message copy comes from `DM Copy Library`.
- Handoff decisions come from sheet status and policy rules.

If data is missing:

- Return a human handoff.
- Do not invent a value.
- Do not summarize from memory.

## 11. Edit Order

To avoid system errors, edit in this order:

1. Add the missing control sheets.
2. Define headers and validation rules.
3. Populate copy library entries for both languages.
4. Link flows to lead and interaction logging.
5. Add shared booking module fields.
6. Add waitlist and follow-up schedules.
7. Add drip scheduling fields.
8. Add handoff queue rules.
9. Test each flow separately.
10. Test end-to-end with one real example per flow.

## 12. Safe Implementation Phases

### Phase A

- Lock the schema.
- Add the missing control sheets.
- Do not change the agent logic yet.

### Phase B

- Add bilingual copy tables.
- Make the agent read message text from sheets.

### Phase C

- Add waitlist, follow-up, and drip scheduling sheets.

### Phase D

- Connect Flow 1 and Flow 2 to the shared booking module.

### Phase E

- Connect booking confirmation and pre-trip drip.

### Phase F

- Connect post-trip and re-engagement.

## 13. Acceptance Criteria

The workbook is ready for this workflow when:

- Every flow has a clear sheet owner.
- Every customer touch is logged.
- Every prompt has Arabic and English copy.
- Booking logic is shared.
- Reminder timing comes from sheets.
- Handoff is automatic for missing or risky data.
- The agent can answer without inventing anything.

## 14. Immediate Recommendation

Before editing existing operational tabs, add the missing control sheets and populate the `DM Copy Library`.

That is the safest way to make all papers listen to each other without breaking the current system.
