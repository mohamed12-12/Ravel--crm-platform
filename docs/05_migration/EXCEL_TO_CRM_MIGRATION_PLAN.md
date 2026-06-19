# Excel to CRM Migration Plan

## Objective

Move Rahma Traveler's historical Excel data into the CRM database so the CRM becomes the single source of truth for users and the AI agent.

After migration:

- CRM users should manage travelers, trips, bookings, and community events from the CRM only.
- The AI agent should read traveler identity, trip availability, and booking state from the CRM/API, not from Excel.
- Excel should become an archived reference or controlled import/export file, not an operational database.

## Source File Inspected

Workbook:

`RT - Travelers Database.xlsx`

Location:

`C:\Users\Mo\Desktop\nanovate tech\Projects\Rahma Traveler\RT - Travelers Database.xlsx`

Observed workbook size:

`3,224,288 bytes`

Observed sheets:

- `Needs update Read Me`
- `Steps`
- `Trips`
- `Travelers`
- `Trip Bookings`
- `Community Events`
- `CE Bookings`
- `uncleaned data - reference`

## Current CRM Database Inspected

CRM database:

`apps/api/instance/rahma_traveler_dev.db`

Current table counts observed:

| Table | Rows |
|---|---:|
| travelers | 2 |
| trips | 1 |
| trip_bookings | 1 |
| leads | 2 |
| interactions | 2 |
| booking_event_trail | 21 |
| booking_status_history | 16 |
| handoff_queue | 0 |
| community_events | 0 |
| ce_bookings | 0 |
| users | 0 |

This confirms the CRM database and Excel workbook are not currently aligned. The CRM has only demo/runtime records while Excel contains the historical business data.

## Workbook Data Profile

### Travelers

Observed rows:

`773`

Observed headers:

- `Status`
- `Traveler ID`
- `Full Name`
- `First Name`
- `Last Name`
- `Birthday`
- `Gender`
- `Nationality`
- `Code`
- `WhatsApp`
- `Email`
- `Community Whatsapp`
- `Residence`
- `Loc. Trips`
- `Int. Trips`
- `Total trips`
- `Comm. Events`
- `Lifetime Revenue`
- `Notes`
- `Introduce yourself`
- `Emergency Contact`
- `Emergency Phone`
- `Medical Notes`
- `Room Preference`
- `Rating`

Observed data issues:

| Issue | Count |
|---|---:|
| Missing Traveler ID | 167 |
| Duplicate Traveler ID values | 2 |
| Missing phone identity | 269 |
| Duplicate phone identity values | 20 |

Duplicate Traveler ID examples:

- `TR00397`
- `TR00398`

Duplicate phone identity examples:

- `563730780` appears 3 times
- `1055756130` appears 3 times
- `1277445335` appears 2 times
- `554421909` appears 2 times
- `1159280528` appears 2 times

Observed status distribution:

| Status | Count |
|---|---:|
| Blank | 688 |
| Blackllisted | 36 |
| Repeat | 20 |
| VIP | 15 |
| Cancelled | 14 |

Important note:

The `Travelers` sheet contains formula-derived fields. Observed formula-heavy columns include `First Name`, `Last Name`, `Loc. Trips`, `Int. Trips`, `Total trips`, and `Comm. Events`.

These formula values should not become the permanent source of truth. The CRM should import identity/profile fields, then recalculate trip counts and revenue from bookings.

### Trips

Observed rows:

`48`

Important structure:

`Trips` uses row 1 as grouped visual headers and row 2 as the real import header.

Observed headers:

- `Trip ID`
- `Trip Name`
- `Type`
- `Year`
- `Trip Leader`
- `Start Date`
- `End Date`
- accommodation total fields
- accommodation remaining fields
- EGP price fields
- USD price fields
- revenue fields

Observed data issues:

| Issue | Count |
|---|---:|
| Missing Trip ID | 0 |
| Missing Trip Name | 0 |
| Duplicate Trip ID values | 3 |

Duplicate Trip ID examples:

- `RT-INT-25-002`
- `RT-INT-25-003`
- `RT-INT-25-004`

Observed trip type distribution:

| Type | Count |
|---|---:|
| Local | 30 |
| International | 18 |

Important note:

The sheet contains room totals, room remaining, prices, flight prices, and revenue. These need explicit field mapping before import because the current CRM trip model has structured fields for:

- `single_total`
- `double_total`
- `triple_total`
- `single_remaining`
- `double_remaining`
- `triple_remaining`
- `boys_double`
- `girls_double`
- `boys_triple`
- `girls_triple`

The Excel sheet does not clearly expose boys/girls room split in the first visible trip headers inspected. That split may need to be created from current CRM logic or manually filled during cleanup.

### Trip Bookings

Observed rows:

`850`

Important structure:

`Trip Bookings` uses row 1 as grouped visual headers and row 2 as the real import header.

Observed headers:

- `Booking ID`
- `Trip ID`
- `Trip Name`
- `Traveler ID`
- `Traveler Name`
- `Room Type`
- first deposit fields
- second deposit fields
- payment method fields

Observed data issues:

| Issue | Count |
|---|---:|
| Missing Booking ID | 0 |
| Duplicate Booking ID values | 1 |
| Missing Traveler ID | 225 |
| Missing Trip ID | 224 |

Duplicate Booking ID example:

- `0-L-000` appears 224 times

Observed room type distribution:

| Room Type | Count |
|---|---:|
| Blank | 764 |
| Double | 45 |
| Single | 31 |
| Triple | 9 |
| USD | 1 |

Observed currency distribution:

| Currency | Count |
|---|---:|
| Blank | 816 |
| EGP | 27 |
| USD | 6 |
| EUR | 1 |

Important note:

The booking sheet has many incomplete rows and placeholder booking IDs. It must not be imported directly into `trip_bookings` without staging, validation, and quarantine.

### Community Events

Observed rows:

`6`

Observed headers:

- `Event ID`
- `Event Name`
- `Type`
- `Year`

Current CRM `community_events` table supports:

- `event_id`
- `event_name`
- `date`
- `location`
- `status`
- `description`
- `notes`

The workbook has less detail than the CRM model. Missing CRM fields should remain null or be filled manually later.

### CE Bookings

Observed rows:

`49`

Observed headers:

- `Booking ID`
- `CE ID`
- `Community Event`
- `Traveler ID`
- `Traveler Name`
- `Total Price`

Observed data issue:

| Issue | Count |
|---|---:|
| Missing Traveler ID | 27 |

Important note:

CE bookings with missing Traveler ID need matching by name/phone if available elsewhere, or they should be imported into a quarantine/review file instead of creating weak CRM records.

### Uncleaned Data Reference

Observed rows:

`681`

This sheet appears to be historical/raw reference data. It includes fields such as:

- trip number
- trip ID
- traveler name
- contact number
- email
- nationality
- residence
- gender
- room type
- flight
- payment fields
- passport/ID reference
- WhatsApp group status
- payment method
- registered by
- status
- comments

Decision:

Do not import this sheet directly into production tables.

Use it only as a reconciliation source when the clean `Travelers`, `Trips`, and `Trip Bookings` sheets are missing important information.

## Target CRM Tables

The current CRM already has models for:

- `travelers`
- `trips`
- `trip_bookings`
- `community_events`
- `ce_bookings`
- `leads`
- `interactions`
- `handoff_queue`
- `booking_status_history`
- `booking_event_trail`

The first migration should focus on historical master data:

1. Travelers
2. Trips
3. Trip bookings
4. Community events
5. Community event bookings

Do not backfill leads from Excel unless a reliable lead source exists. The inspected workbook does not contain a clean lead pipeline sheet.

## Recommended Migration Strategy

### Do Not Import Directly

The workbook contains:

- duplicate traveler IDs
- duplicate phone identities
- missing traveler IDs
- missing phone identities
- duplicate trip IDs
- duplicate placeholder booking IDs
- bookings missing traveler/trip references
- formula-derived values
- raw uncleaned reference data

A direct import would create duplicate travelers, incorrect booking links, wrong inventory, and broken agent identity matching.

### Use a Staging Migration

Recommended stages:

1. Freeze the Excel file.
2. Create a file checksum and backup copy.
3. Backup the current CRM SQLite database.
4. Build a staging import database or staging tables.
5. Load Excel rows into staging exactly as-is.
6. Normalize and validate staged rows.
7. Produce a quarantine report for bad rows.
8. Import only clean records into CRM tables.
9. Recalculate derived CRM values from imported bookings.
10. Run reconciliation checks.
11. Switch the AI agent to CRM/API as the source of truth.
12. Archive Excel as read-only historical reference.

## Migration Order

### Step 1: Backup

Create backups before touching data:

- Backup `RT - Travelers Database.xlsx`
- Backup `apps/api/instance/rahma_traveler_dev.db`

No production import should run without a rollback copy.

### Step 2: Traveler Import

Import travelers first because bookings and CE bookings depend on traveler IDs.

Rules:

- Preserve valid existing `Traveler ID` values.
- Generate new CRM traveler IDs only for rows with no Traveler ID after deduplication.
- Normalize phone numbers using the CRM phone normalization logic.
- Store:
  - raw phone
  - normalized E.164 phone if possible
  - phone lookup key
- Never create two travelers with the same confirmed phone identity unless they are manually approved.
- Put duplicate phone records into quarantine.
- Put duplicate traveler IDs into quarantine.
- Put rows with no name and no phone into quarantine.

Recommended status mapping:

| Excel Status | CRM Status |
|---|---|
| Blank | Active |
| Repeat | Repeat |
| VIP | VIP |
| Blackllisted | Inactive or Blacklisted review |
| Cancelled | Inactive or Cancelled review |

Decision required:

The current CRM does not clearly separate `Inactive`, `Cancelled`, and `Blacklisted` as controlled business states. Before final import, choose whether blacklisted travelers should be:

- imported as `Inactive` with notes, or
- added as a controlled CRM status.

### Step 3: Trip Import

Import trips after traveler master data.

Rules:

- Preserve existing `Trip ID` values where unique.
- Quarantine duplicate Trip IDs:
  - `RT-INT-25-002`
  - `RT-INT-25-003`
  - `RT-INT-25-004`
- Import `Trip Name`, `Type`, `Year`, `Trip Leader`, `Start Date`, `End Date`.
- Map accommodation totals and remaining values carefully because Excel uses grouped headers.
- Do not invent boys/girls split if the Excel source does not contain it.
- If boys/girls room split is missing, set those fields to `0` and flag the trip for manual inventory completion.

Recommended trip status mapping:

| Excel Condition | CRM Sales Status |
|---|---|
| Future/open sale trip | Open |
| Completed historical trip | Closed |
| Cancelled trip | Cancelled |
| Unknown | Review |

### Step 4: Trip Booking Import

Import trip bookings only after travelers and trips are available.

Rules:

- Preserve valid unique `Booking ID` values.
- Do not import placeholder booking ID `0-L-000` as real repeated booking IDs.
- Generate new booking IDs only after source row is matched to a valid traveler and trip.
- Link bookings by `Traveler ID` and `Trip ID`.
- If Traveler ID is missing, try controlled matching using:
  - exact traveler name
  - phone number from uncleaned reference if available
  - trip context
- If matching is not confident, quarantine the row.
- If Trip ID is missing, try controlled matching using:
  - trip name
  - trip date/year
  - source row context
- If matching is not confident, quarantine the row.

Recommended booking status mapping:

| Excel Condition | CRM Booking Status |
|---|---|
| historical completed/paid booking | Completed or Paid |
| confirmed traveler on trip | Confirmed |
| deposit exists but not fully paid | Payment Pending |
| cancelled source status | Cancelled |
| uncertain status | Pending Confirmation or Review quarantine |

Important:

The approved CRM lifecycle is:

- Draft
- Waiting Customer
- Pending Confirmation
- Confirmed
- Payment Pending
- Paid
- Completed
- Cancelled

Historical bookings should not all become `Draft`. Completed past trips should be imported as `Completed` or `Paid` based on payment evidence.

### Step 5: Community Events Import

Import community events after travelers.

Rules:

- Preserve `Event ID`.
- Import `Event Name`, `Type`, and `Year`.
- Leave missing `date`, `location`, `description`, and `notes` blank unless found in another trusted source.

### Step 6: CE Booking Import

Rules:

- Preserve valid CE booking IDs.
- Link by `Traveler ID` where available.
- Quarantine rows with missing Traveler ID unless a confident match is possible.
- Do not create new travelers from CE booking name only.

### Step 7: Recalculate Derived Values

After importing bookings:

- Recalculate traveler local trip count.
- Recalculate traveler international trip count.
- Recalculate traveler total trips.
- Recalculate community events count.
- Recalculate lifetime revenue where payment data is reliable.
- Recalculate trip remaining inventory from confirmed/paid/completed bookings.

Do not trust Excel formula-derived counts as final CRM truth.

## CRM and Agent Source-of-Truth Decision

Final decision:

The CRM database must become the operational source of truth.

Excel should be used only for:

- historical archive
- migration source
- emergency audit reference
- controlled export

The AI agent should not read operational traveler identity from Excel after migration.

Required end state:

- CRM UI reads from CRM database.
- Agent reads from CRM/API.
- Booking creation writes to CRM database.
- Trip availability is calculated from CRM trips and CRM bookings.
- Excel is not part of the live identity/booking workflow.

## Field Mapping Summary

### Travelers

| Excel Field | CRM Field | Rule |
|---|---|---|
| Traveler ID | traveler_id | Preserve if unique |
| Status | status | Map controlled values |
| Full Name | full_name | Required unless quarantined |
| First Name | first_name | Import value, but can be recalculated |
| Last Name | last_name | Import value, but can be recalculated |
| Birthday | birthday | Parse date |
| Gender | gender | Normalize controlled values |
| Nationality | nationality | Import raw, clean later |
| Code | phone_code | Preserve raw code |
| WhatsApp | whatsapp_raw | Preserve raw number |
| Email | email | Lowercase/trim if valid |
| Community Whatsapp | community_whatsapp | Preserve |
| Residence | residence | Preserve |
| Loc. Trips | local_trips_count | Recalculate after bookings |
| Int. Trips | international_trips_count | Recalculate after bookings |
| Total trips | total_trips | Recalculate after bookings |
| Comm. Events | community_events_count | Recalculate after CE bookings |
| Lifetime Revenue | lifetime_revenue | Recalculate if payment data is reliable |
| Notes | notes | Preserve |
| Introduce yourself | introduce_yourself | Preserve |
| Emergency Contact | emergency_contact | Preserve |
| Emergency Phone | emergency_phone | Normalize if possible |
| Medical Notes | medical_notes | Preserve |
| Room Preference | room_preference | Preserve |
| Rating | rating | Parse 1-5 if valid |

### Trips

| Excel Field | CRM Field | Rule |
|---|---|---|
| Trip ID | trip_id | Preserve if unique |
| Trip Name | trip_name | Required |
| Type | type | Local/International |
| Year | year | Parse integer |
| Trip Leader | trip_leader | Preserve |
| Start Date | start_date | Parse date |
| End Date | end_date | Parse date |
| Accommodation Total Single | single_total | Map from grouped header |
| Accommodation Total Double | double_total | Map from grouped header |
| Accommodation Total Triple | triple_total | Map from grouped header |
| Accommodation Remaining Single | single_remaining | Recalculate after bookings where possible |
| Accommodation Remaining Double | double_remaining | Recalculate after bookings where possible |
| Accommodation Remaining Triple | triple_remaining | Recalculate after bookings where possible |
| Prices | public_price / notes | Requires explicit mapping |

### Trip Bookings

| Excel Field | CRM Field | Rule |
|---|---|---|
| Booking ID | booking_id | Preserve if unique; replace placeholder only after validation |
| Trip ID | trip_id | Must match imported trip |
| Trip Name | trip_name | Preserve for audit |
| Traveler ID | traveler_id | Must match imported traveler |
| Traveler Name | traveler_name | Preserve for audit |
| Room Type | room_type | Normalize Single/Double/Triple |
| Deposit Currency | currency | Use payment data carefully |
| Deposit Amounts | payment_status / notes | Current model does not fully support payments |
| Payment Method | booking_notes | Preserve until payment model exists |

## Quarantine Rules

Rows must go to quarantine when:

- Traveler ID is duplicated.
- Trip ID is duplicated.
- Booking ID is duplicated and not clearly a placeholder.
- Traveler has no usable name and no phone.
- Traveler phone matches another traveler with conflicting name.
- Booking has no valid traveler match.
- Booking has no valid trip match.
- Room type is not one of Single, Double, Triple, or another approved CRM value.
- Currency is not one of EGP, USD, EUR unless approved.
- Date parsing fails for important lifecycle dates.

Quarantine output should include:

- source sheet
- source row number
- source key
- error category
- raw row values
- recommended manual action

## Validation Checklist

Before migration is accepted:

- Source traveler count equals imported travelers plus quarantined traveler rows.
- Source trip count equals imported trips plus quarantined trip rows.
- Source booking count equals imported bookings plus quarantined booking rows.
- No duplicate CRM traveler IDs.
- No duplicate CRM trip IDs.
- No duplicate CRM booking IDs.
- No duplicate confirmed phone lookup keys unless manually approved.
- Every imported trip booking has a valid traveler.
- Every imported trip booking has a valid trip.
- Every imported CE booking with a traveler ID points to a valid traveler.
- Agent lookup for `+447493723281` resolves to `TR00001 Nada Adel`.
- CRM `/travelers/` count matches imported traveler count.
- CRM trip inventory reflects imported bookings.
- Agent no longer depends on Excel runtime workbook for traveler lookup.

## Rollback Plan

Rollback must be simple:

1. Stop CRM and agent services.
2. Restore the backed-up CRM database file.
3. Restore the previous environment configuration if agent source was changed.
4. Restart services.
5. Confirm CRM counts return to pre-migration values.

Do not run a destructive cleanup script as rollback. File-level database restore is safer for the current SQLite setup.

## Recommended Implementation Phases

### Phase 1: Data Audit and Mapping

Deliverables:

- final field mapping
- duplicate report
- quarantine rules
- source checksum
- sample import report

No CRM data should be changed in this phase.

### Phase 2: Staging Import Tool

Deliverables:

- read Excel
- load staging rows
- normalize phone numbers
- validate IDs
- produce quarantine CSV/JSON

Still no production CRM replacement.

### Phase 3: Staging Reconciliation

Deliverables:

- imported staging counts
- rejected row counts
- duplicate analysis
- traveler/booking/trip relationship report
- sample UI checks

### Phase 4: CRM Import

Deliverables:

- clean travelers imported
- clean trips imported
- clean bookings imported
- community events imported
- CE bookings imported where valid
- derived counts recalculated

### Phase 5: Agent Source Switch

Deliverables:

- agent reads identity from CRM/API
- agent no longer relies on Excel workbook for live traveler lookup
- booking draft creation writes into CRM source of truth
- regression tests confirm returning traveler lookup works

### Phase 6: Business Review

Deliverables:

- quarantine review
- duplicate merge decisions
- blacklisted/cancelled status decisions
- missing room inventory decisions
- final acceptance sign-off

## CTO Recommendation

Do not try to make the CRM use the Excel sheet live.

The best solution is:

1. Treat Excel as historical source data.
2. Import it through a controlled staging pipeline.
3. Quarantine bad records instead of guessing.
4. Recalculate CRM totals from CRM bookings.
5. Make the CRM database the only operational source.
6. Make the AI agent read from CRM/API only.

This avoids the current problem where the CRM shows only a few database travelers while the agent sees hundreds from Excel. One system must own the truth, and for client delivery that system should be the CRM database.

## CRM Logic That Must Exist Before Migration

Before any import script is written, the CRM must enforce these rules.

### 1. Traveler Identity Logic

CRM traveler identity must be resolved in this order:

1. normalized phone lookup key
2. normalized WhatsApp number
3. existing `traveler_id`
4. manual review

Rules:

- Do not match by name only.
- Do not create a new traveler if the phone already belongs to another traveler.
- If one phone maps to multiple travelers, send the row to duplicate review.
- If a traveler has no phone but has a valid old `Traveler ID`, import only if the ID is unique.
- If a traveler has no ID and no phone, quarantine the row.

### 2. Traveler Status Logic

CRM must control traveler statuses instead of importing messy status text directly.

Recommended CRM statuses:

- Active
- New
- Repeat
- VIP
- Inactive
- Blacklisted
- Cancelled Review
- Duplicate Review

Mapping:

| Excel Value | CRM Value |
|---|---|
| Blank | Active |
| Repeat | Repeat |
| VIP | VIP |
| Blackllisted | Blacklisted |
| Cancelled | Cancelled Review |
| Unknown value | Manual Review |

### 3. Trip Identity Logic

Trip identity must be based on `trip_id`.

Rules:

- Preserve existing Excel Trip IDs only when unique.
- Duplicate Trip IDs must be fixed before import.
- Do not merge trips only because names are similar.
- Do not auto-generate new Trip IDs for duplicated historical trips until the duplicate is reviewed.

Known duplicate Trip IDs found in the inspected workbook:

- `RT-INT-25-002`
- `RT-INT-25-003`
- `RT-INT-25-004`

### 4. Trip Inventory Logic

CRM must own room inventory.

Required inventory fields:

- single total
- double total
- triple total
- single remaining
- double remaining
- triple remaining
- boys double remaining
- girls double remaining
- boys triple remaining
- girls triple remaining

Rules:

- Remaining rooms must be calculated from CRM bookings where possible.
- Excel formula values can be used for comparison only.
- If the Excel source does not clearly contain boys/girls room split, do not invent it.
- Missing boys/girls split must be marked for manual CRM completion.

### 5. Booking Identity Logic

Every booking must have:

- one unique booking ID
- one valid traveler
- one valid trip
- one room type
- one booking status

Rules:

- Do not import booking rows with missing traveler reference unless confidently matched.
- Do not import booking rows with missing trip reference unless confidently matched.
- Do not import repeated placeholder booking ID `0-L-000` as real bookings.
- Generate replacement booking IDs only after the row has a valid traveler and trip.

### 6. Booking Lifecycle Logic

CRM must use the approved booking lifecycle:

- Draft
- Waiting Customer
- Pending Confirmation
- Confirmed
- Payment Pending
- Paid
- Completed
- Cancelled

Migration mapping must be conservative:

- confirmed historical participant -> Confirmed
- fully paid historical participant -> Paid or Completed
- past completed trip participant -> Completed
- deposit exists but not fully paid -> Payment Pending
- cancelled source row -> Cancelled
- unclear source row -> quarantine

### 7. Lead Logic

Old Excel travelers must not become new leads.

Rules:

- Travelers are customer master data.
- Leads are new sales opportunities.
- Existing traveler with new interest should create a linked lead/opportunity or booking draft.
- CRM must preserve the existing `traveler_id`.
- Never create duplicate travelers from a new inquiry.

### 8. Agent Logic

After migration, the agent must use CRM/API as the source of truth.

Agent must read from CRM:

- traveler identity
- traveler profile
- trip availability
- room availability by boys/girls where available
- booking status
- lead status

Agent must not use Excel as the live traveler database after migration.

## Professional Implementation Prompt

Use this prompt when starting the actual migration implementation.

```text
Act as a Senior Data Engineer, CRM Architect, ERP Consultant, QA Lead, and Technical Lead.

Goal:
Migrate Rahma Traveler historical Excel data into the CRM safely and make the CRM database the single source of truth.

Source file:
RT - Travelers Database.xlsx

Current inspected facts:
- Workbook contains sheets: Travelers, Trips, Trip Bookings, Community Events, CE Bookings, uncleaned data - reference, Steps, Needs update Read Me.
- Travelers sheet has 773 rows.
- Trips sheet has 48 rows using row 2 as the real header.
- Trip Bookings sheet has 850 rows using row 2 as the real header.
- Community Events sheet has 6 rows.
- CE Bookings sheet has 49 rows.
- Current CRM SQLite database has only 2 travelers, 1 trip, and 1 trip booking.
- Traveler data contains duplicate IDs, missing IDs, missing phone identities, and duplicate phone identities.
- Trip data contains duplicate Trip IDs.
- Trip Booking data contains missing traveler IDs, missing trip IDs, and repeated placeholder booking ID 0-L-000.

Rules:
- Do not guess missing business data.
- Do not import dirty rows directly into production tables.
- Do not match travelers by name only.
- Do not create duplicate travelers when phone identity already exists.
- Do not import duplicate Trip IDs without review.
- Do not import booking rows unless traveler and trip are valid.
- Do not treat Excel formulas as CRM source of truth.
- Do not make Excel the live database.
- CRM database must become the source of truth.
- Agent must eventually read from CRM/API, not Excel.

Required implementation sequence:

Task 1: Inspect current code and schema
- Read CRM models for travelers, trips, bookings, leads, events, and booking history.
- Read existing phone normalization and identity matching code.
- Read current import/sync scripts if any exist.
- Do not modify code yet.

Task 2: Create staging profile command
- Read RT - Travelers Database.xlsx.
- Detect correct header rows:
  - Travelers: row 1
  - Trips: row 2
  - Trip Bookings: row 2
  - Community Events: row 1
  - CE Bookings: row 1
- Produce row counts, duplicate counts, missing key counts, and formula warnings.
- Output a migration audit report.
- Do not write CRM data.

Task 3: Create quarantine rules
- Quarantine duplicate traveler IDs.
- Quarantine duplicate phone identities.
- Quarantine travelers with no ID and no phone.
- Quarantine duplicate Trip IDs.
- Quarantine bookings with missing or invalid traveler.
- Quarantine bookings with missing or invalid trip.
- Quarantine repeated placeholder booking ID 0-L-000 unless safely regenerated after validation.

Task 4: Build traveler staging import
- Preserve unique Traveler IDs.
- Normalize phone numbers.
- Save raw phone, normalized phone, and phone lookup key.
- Map statuses conservatively.
- Do not create duplicates.
- Produce imported count and quarantine count.

Task 5: Build trip staging import
- Preserve unique Trip IDs.
- Import trip name, type, year, leader, dates.
- Map room totals and remaining fields from grouped Excel headers.
- Do not invent boys/girls room availability if missing.
- Flag trips requiring manual boys/girls inventory completion.

Task 6: Build booking staging import
- Link bookings to staged/imported travelers.
- Link bookings to staged/imported trips.
- Normalize room type.
- Map payment/status data into approved booking lifecycle.
- Generate booking IDs only for rows that pass validation.
- Create booking history rows for imported booking status where required.

Task 7: Build community event staging import
- Import Community Events.
- Import CE Bookings only when traveler linkage is valid.
- Quarantine CE Bookings with missing traveler ID unless confidently matched.

Task 8: Recalculate CRM-derived values
- Recalculate traveler local trip count.
- Recalculate traveler international trip count.
- Recalculate traveler total trips.
- Recalculate community event count.
- Recalculate lifetime revenue only from reliable payment data.
- Recalculate trip remaining room inventory from bookings.

Task 9: Run validation
- Verify source count = imported count + quarantined count for each sheet.
- Verify no duplicate CRM primary IDs.
- Verify no duplicate confirmed phone lookup keys.
- Verify every booking has valid traveler and trip.
- Verify agent test number +447493723281 resolves to TR00001 Nada Adel after import.
- Verify CRM /travelers/ count matches imported clean traveler count.
- Verify trip remaining availability changes when booking records exist.

Task 10: Switch source of truth
- Update agent lookup only after CRM import is validated.
- Agent must use CRM/API for traveler identity and trip availability.
- Excel becomes archive/reference only.

Final output required:
- Files changed.
- Migration audit counts.
- Imported counts.
- Quarantine counts.
- Validation results.
- Any unresolved manual review items.
- Rollback instructions.

Do not hallucinate.
If a field is missing, say it is missing.
If a row cannot be matched safely, quarantine it.
If business meaning is unclear, stop and document the decision required.
```

## Small-Step Execution Plan

### Step 1: Lock the Source

Make a read-only backup of:

- `RT - Travelers Database.xlsx`
- `apps/api/instance/rahma_traveler_dev.db`

Result:

No one changes the source while migration is being prepared.

### Step 2: Build Audit Only

Create an audit process that reads Excel and reports:

- total rows
- bad rows
- duplicate IDs
- missing phones
- missing traveler links
- missing trip links

Result:

No CRM data changes yet.

### Step 3: Build Quarantine

Create a quarantine output for rows that cannot be trusted.

Result:

Bad data is visible and reviewable instead of silently imported.

### Step 4: Import Travelers

Import only clean travelers.

Result:

CRM starts owning customer master data.

### Step 5: Import Trips

Import only clean trips.

Result:

CRM owns trip catalog and inventory.

### Step 6: Import Bookings

Import only bookings linked to valid travelers and valid trips.

Result:

CRM owns historical booking records.

### Step 7: Recalculate Counts

CRM recalculates:

- traveler trip totals
- community event totals
- trip remaining rooms
- revenue where reliable

Result:

CRM values come from CRM data, not Excel formulas.

### Step 8: Validate

Check:

- no duplicate traveler IDs
- no duplicate booking IDs
- no duplicate trip IDs
- every booking has traveler and trip
- known old traveler phone lookup works
- CRM traveler count is correct

Result:

Migration is accepted only if checks pass.

### Step 9: Switch Agent

Agent reads CRM/API only.

Result:

CRM UI and agent see the same travelers and trips.

### Step 10: Archive Excel

Excel becomes historical archive only.

Result:

No more split-brain data between CRM and agent.
