# Rahma Traveler Agent and Sheet Logic Alignment Plan

Date: 2026-05-13

## 1. Goal

This plan is the next senior round for aligning the web demo agent with `RT - Travelers Database.phase5.ready.xlsx` and the live Google Sheet.

The project is already producing useful results: the agent can collect intake data, check the CRM by WhatsApp, preview future trips, and write leads. The next work is to harden the logic so the agent never invents IDs, never overwrites the wrong traveler, and never says trip or CRM data that is not backed by the workbook.

## 2. Non-Negotiable Rules

1. The `Travelers` sheet is the only source of truth for `Traveler ID`.
2. The `Trips` sheet is the only source of truth for trip availability, dates, and trip type.
3. The `Leads` sheet is the sales pipeline, not the customer master.
4. The `Interactions` sheet is an audit log, not the source for customer identity.
5. The agent must not create or reuse a `Traveler ID` from memory, random generation, visible Google Sheet rows, `Leads`, or `Interactions`.
6. Any uncertain match must go to handoff instead of being auto-written.
7. Empty dates, missing capacity, missing prices, and conflicting phone/name data must be stated as missing or sent to follow-up; the agent must not fill gaps with guesses.

## 3. Correct New Client ID Logic

When the intake form is submitted, the agent should use this decision path:

1. Normalize the WhatsApp number into:
   - `Code`
   - local `WhatsApp`
   - `Integrated WhatsApp`
   - `Phone Lookup Key`
2. Search the full `Travelers` sheet by normalized phone fields.
3. If one traveler matches:
   - Use the existing `Traveler ID`.
   - Check `Status`.
   - If blacklisted, stop and hand off.
   - If the submitted name conflicts with the saved name, stop and hand off.
   - If safe, update only missing profile fields.
4. If multiple travelers match the same phone:
   - Stop automation.
   - Send to handoff.
   - Do not create a new traveler.
5. If no traveler matches:
   - Read every non-empty `Traveler ID` cell in the full `Travelers` sheet.
   - Include rows that are hidden or filtered in Google Sheets.
   - Parse only valid IDs matching `TR` plus digits, for example `TR00522`.
   - Find the highest numeric ID.
   - Create `next_id = highest + 1` with the same five-digit padding.
   - Re-check that `next_id` does not already exist.
   - Write the new traveler row with the intake data.
   - Then create or update the lead.

Example:

If the full `Travelers` sheet contains `TR00522`, the next new customer must be `TR00523`, even if Google Sheets is currently filtered and the last visible row looks like `TR00499`.

## 4. Why The Screenshot Looked Wrong

The browser screenshot showed `TR00499` as the last visible traveler, but the workbook can be filtered. A filtered Google Sheet view can hide existing rows, so the visible bottom row is not a safe source for new IDs.

The agent logic must use workbook data, not what is visible on screen. For safety, we should add an audit panel or debug output showing:

- max traveler ID found in `Travelers`
- row number where it was found
- next proposed traveler ID
- whether filters or blank row gaps exist
- whether duplicate traveler IDs exist
- whether duplicate phone lookup keys exist

## 5. Phase Plan

### Phase A - Sheet Integrity Audit

Purpose: prove the workbook is safe before adding more automation.

Tasks:

- Scan `Travelers` for all valid `TRxxxxx` IDs.
- Report true max `Traveler ID`.
- Detect duplicate traveler IDs.
- Detect duplicate phone lookup keys.
- Detect rows where `Traveler ID` exists but name or phone is missing.
- Detect rows where phone exists but lookup key is missing.
- Detect filtered/hidden rows and warn that visible Google rows are not authoritative.
- Write results to `Phase 0 Audit` or a new `CRM Integrity Audit` section.

Done when:

- We can explain why the next ID is what it is.
- The agent and workbook agree on the same next ID.

### Phase B - Identity Resolver Hardening

Purpose: decide existing vs new customer without corrupting CRM identity.

Tasks:

- Treat phone lookup as the primary identity key.
- Treat name as a conflict check, not the primary ID key.
- Tighten name matching so one common first name does not auto-match a different person.
- If same phone appears under a different full name, create handoff instead of updating.
- If multiple rows share the same phone, create handoff instead of choosing one.
- If a traveler is blacklisted, stop the sales flow and do not write a lead as qualified.

Done when:

- Existing safe customers continue.
- New customers receive a new sequential traveler ID.
- Conflicting customers are paused for manual review.

### Phase C - Controlled Traveler Creation

Purpose: create new customers like an experienced Excel CRM operator.

Tasks:

- Generate the new ID from the true max `Traveler ID` in `Travelers`.
- Preserve formatting and formulas when inserting into the first safe blank row.
- Do not write into partially blank historical rows unless all key identity fields are empty.
- Save intake fields:
  - full name
  - birthday
  - gender
  - nationality
  - code
  - WhatsApp
  - integrated WhatsApp
  - phone lookup key
  - lead source
  - created at
  - last contacted at
  - agent notes
- Log the created row and ID in `Interactions`.

Done when:

- New client creation is repeatable.
- No ID conflict can happen from random or stale values.

### Phase D - Lead and Traveler Synchronization

Purpose: make `Travelers`, `Leads`, and `Interactions` listen to each other.

Tasks:

- A confirmed interested customer creates or updates a `Leads` row.
- `Leads.Traveler ID` must always point to `Travelers.Traveler ID`.
- `Interactions.Traveler ID` must match the resolved traveler.
- If a lead is created for a new traveler, both rows must be linked.
- If existing traveler confirms a lead, do not create a duplicate traveler.
- Update trip counters only after a real booking or confirmed completed trip, not merely a lead.

Done when:

- One customer has one CRM profile.
- Many leads/interactions can link to the same traveler safely.

### Phase E - Trip Availability Logic

Purpose: prevent trip hallucination.

Tasks:

- Filter trips by user response: `Local` or `International`.
- Only show trips with future `Start Date` and `End Date`.
- If dates are empty, mark as `Date TBD` and offer follow-up, not a confirmed trip.
- If remaining capacity is configured, show exact remaining places.
- If remaining capacity is empty, say capacity is not configured.
- If the trip is old, cancelled, closed, or full, do not offer it as available.

Done when:

- Every trip suggestion can be traced to a real row in `Trips`.

### Phase F - Chat Agent State Machine

Purpose: make the chat flow match the sheet.

Tasks:

- Start with a clean intake form:
  - full name
  - birthday
  - gender
  - nationality
  - WhatsApp country code
  - WhatsApp number
  - preferred language
- After intake, do CRM lookup before asking for trip type.
- Ask local/international only after CRM check passes.
- Show trip options from the sheet.
- Write Lead/Traveler only after the user confirms interest.
- If user says no/later, create waitlist/follow-up only when that phase is enabled.
- Add clear UI state for:
  - new traveler created
  - existing traveler updated
  - lead saved
  - handoff required
  - no write performed

Done when:

- The demo conversation can be replayed and every write is explainable.

### Phase G - Flow PDF Alignment

Purpose: map the six PDF flows without duplicating logic.

Tasks:

- Build one shared booking module:
  - personal info
  - room type
  - flight option
  - date option
  - currency
  - deposit details
- Let Flow 1 and Flow 2 call the same shared module.
- Add `DM Copy Library` rows for every message key in English and Arabic.
- Add `Waitlist`, `Follow Ups`, `Trip Drips`, and `Handoff Queue` only if the workbook needs durable state for them.
- Do not hardcode copy in the agent when the sheet should own it.

Done when:

- The agent can follow Flow 1 and Flow 2 in demo mode and has sheet structure ready for Flows 3-6.

### Phase H - Tests and Demo Protection

Purpose: keep the working result stable.

Tasks:

- Add tests for:
  - true max traveler ID from hidden/filtered-style row gaps
  - duplicate traveler IDs
  - duplicate phone lookup keys
  - new customer gets max + 1
  - existing customer reuses existing ID
  - phone/name conflict triggers handoff
  - blacklisted traveler is stopped
  - missing trip dates are not offered as confirmed
- Add a demo reset button that clearly resets the runtime workbook from the source workbook.
- Make the UI display whether it is writing to local XLSX or Google Sheets.

Done when:

- We can test safely before showing the client.

### Phase I - Production Readiness

Purpose: prepare for Instagram/Messenger integration later.

Tasks:

- Keep secrets in `.env`.
- Required env keys should include:
  - `SHEET_BACKEND`
  - `GOOGLE_SHEET_ID`
  - `GOOGLE_APPLICATION_CREDENTIALS`
  - `OPENAI_API_KEY`
  - `HUMAN_HANDOFF_PHONE`
  - `META_PAGE_ACCESS_TOKEN`
  - `META_VERIFY_TOKEN`
  - `META_APP_SECRET`
  - `PUBLIC_WEBHOOK_URL`
  - `DEFAULT_COUNTRY_CODE`
  - `DEMO_WRITE_MODE`
- Add webhook input validation before connecting Meta.
- Add logging for every external message and sheet write.
- Add rate limiting and retry rules for Google Sheets writes.

Done when:

- Demo logic is stable enough to connect to a real DM channel without corrupting CRM data.

## 6. Immediate Recommendation

Do Phase A next before adding more conversational features.

The highest-risk problem is not the chat wording. The highest-risk problem is CRM identity integrity:

- true max traveler ID
- duplicate IDs
- duplicate phones
- phone/name conflicts
- source workbook vs runtime workbook confusion

Once that is clean, the next safest implementation step is Phase B and Phase C together.

## 7. Senior Acceptance Criteria

The project is ready to move beyond demo only when these are true:

- A new customer always receives `max(Travelers.Traveler ID) + 1`.
- An existing phone never creates a duplicate traveler.
- A phone/name conflict never updates the old traveler automatically.
- A blacklisted traveler never enters the sales flow.
- Every offered trip exists in `Trips` and is future-safe.
- Every write is logged in `Interactions`.
- `Leads`, `Travelers`, and `Interactions` all reference the same resolved `Traveler ID`.
- Missing sheet data leads to follow-up or handoff, not invented answers.
