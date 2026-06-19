# Master Redesign Decision

Date: 2026-06-17

Purpose: define the final product architecture decisions before implementation begins.

Position: this document is intentionally opinionated. It assumes we are preparing Rahma Traveler for real client delivery, not extending the demo casually.

## Non-Negotiable Platform Decisions

These decisions apply to every area below.

### 1. Source Of Truth

Decision:

- The operational source of truth will be the application database, not Excel and not Google Sheets.
- Sheets will become import/export and reporting interfaces only.

Why:

- Any major workflow redesign built on top of DB-plus-sheet dual writes will create rework.

### 2. Persistence Model

Decision:

- One persistence layer must own business writes.
- `UnifiedCRMService` remains the business workflow boundary in the short term, but write logic must converge on one transaction model and one authoritative schema.

Why:

- Mixed SQLAlchemy and direct `sqlite3` writes are a structural defect, not a scaling detail.

### 3. Delivery Scope

Decision:

- The Flask CRM is the active product.
- The React admin and Prisma schema are not implementation targets until the core CRM is stabilized.

Why:

- Building new workflow logic into the prototype stack now would split the product even further.

### 4. Handoff Philosophy

Decision:

- Handoffs will be treated as cases with automation lock, not just queue rows.

Why:

- A queue item without automation stop is not a true human takeover workflow.

### 5. Booking Philosophy

Decision:

- Draft booking, hold, deposit pending, confirmed, cancelled, refunded must become distinct lifecycle states.

Why:

- The current "Confirmed" toggle is not commercially safe.

### 6. Passport And Attachments

Decision:

- Passport and attachment work will not be expanded until a secure document model is defined.

Why:

- Any earlier implementation creates guaranteed rework and compliance risk.

## 1. Travelers

### Current Workflow

- Travelers are the customer master record today.
- They hold identity, phone, profile data, trip counters, notes, emergency and medical info, lead/booking pointers, and passport fields.
- New travelers are created during lead outcome processing or manually from the CRM.
- Duplicate detection is driven primarily by normalized phone logic and `phone_lookup_key`.
- Admin can edit travelers, mark them inactive, export them, and merge duplicates manually.

### Desired Workflow

- Traveler becomes a clean customer master record.
- Customer identity, contact methods, risk flags, tier, consent, and documents are separated conceptually even if implemented incrementally.
- Traveler record remains stable across all future leads, bookings, handoffs, and channels.
- Duplicate resolution becomes a governed identity process with audit.

### Keep

- Preserve `traveler_id` as the stable customer-facing identifier.
- Keep strong phone-based identity lookup as the first matching layer.
- Keep blacklist and protected-customer safety behavior.
- Keep the traveler 360 concept in the CRM.

### Change

- Split overloaded `status` semantics into:
  - lifecycle
  - commercial tier
  - risk/protection flags
- Stop using traveler record as a dumping ground for workflow/session state.
- Move toward normalized contact methods and audited profile changes.
- Stop treating imported counters as authoritative financial/behavioral truth.

### Delete

- Delete the assumption that traveler status alone can represent VIP, blacklist, repeat behavior, merge status, and operational restrictions.
- Delete dependence on sheet-driven traveler stats as the truth layer.

### Risks

- Traveler redesign touches almost every module.
- If done after leads/bookings are redesigned, field mapping work will be repeated.
- Sensitive-field access becomes a production blocker if postponed too long.

### Dependencies

- Source-of-truth decision.
- Unified schema direction.
- Auth and audit design.
- Phone normalization contract.

### Priority

- `P0`

## 2. Leads

### Current Workflow

- Lead creation happens through manual CRM entry or agent-confirmed trip interest.
- `record_agent_outcome` creates or updates the lead.
- Existing leads may be upserted by phone/traveler rather than creating a new opportunity.
- Lead stage, priority, and follow-up are partly auto-derived from traveler status and trip availability.
- Leads can be manually advanced, marked lost, or handed off.

### Desired Workflow

- A lead should represent an inquiry intake event.
- An opportunity should represent a sales cycle for a trip/package.
- Follow-up should be task/activity driven, not embedded as loose fields only.
- Handoff should be a case/escalation linked to the lead or opportunity.

### Keep

- Keep automatic identity-safe lead creation.
- Keep derived safety outcomes for duplicate, blacklist, and phone-name conflict.
- Keep follow-up urgency logic conceptually, but move it into a better task model later.

### Change

- Redesign lead so new inquiries do not overwrite prior sales cycles.
- Separate inquiry from opportunity.
- Add owner, SLA, next action, and auditable stage transitions.
- Replace comma-separated trip ID fields with real relationships or normalized structures.

### Delete

- Delete lead upsert behavior as the default for all future opportunities.
- Delete the idea that one lead row should carry inquiry history, opportunity status, automation state, and follow-up task forever.

### Risks

- This redesign affects booking linkage, dashboard reporting, agent flow, and handoff logic.
- If booking redesign happens first, we will likely rework the lead-booking contract twice.

### Dependencies

- Traveler identity model.
- Handoff case model.
- Booking lifecycle design.
- CRM reporting definitions.

### Priority

- `P0`

## 3. Trips

### Current Workflow

- Trips act as both product and departure.
- They store name, type, dates, leader, sales status, simple inventory counters, price text, description, and notes.
- Recommendation logic filters by trip type, status, date, and room availability.
- Draft holds are stored on the trip row.

### Desired Workflow

- Trip product and trip departure should be separate concepts.
- Pricing, inventory, requirements, and content should be structured.
- Trip availability should be computed from inventory transactions and holds, not manually trusted counters.

### Keep

- Keep the current Local vs International business distinction.
- Keep capacity-aware recommendation logic.
- Keep the ability to offer future-dated and date-TBD trips.

### Change

- Separate product/package from dated departure.
- Replace text-only pricing and discount notes with structured commercial fields over time.
- Introduce inventory ledger and hold model.
- Add trip requirements: passport needed, visa guidance, flight-included policy, cancellation policy.

### Delete

- Delete reliance on `sales_notes` and free-text pricing as business logic carriers.
- Delete draft-hold counters as the final architecture.

### Risks

- Trip redesign before booking redesign is dangerous if the booking model still assumes trip row counters.
- If we change trip IDs too early, we can break existing links.

### Dependencies

- Booking lifecycle redesign.
- Inventory model decision.
- Reporting/source-of-truth decision.

### Priority

- `P1`

## 4. Bookings

### Current Workflow

- Booking creation creates a draft directly.
- Capacity is checked against remaining minus draft holds.
- Booking updates mostly mean manual status/payment changes.
- A "confirmed" booking is currently just a field value.

### Desired Workflow

- Booking becomes a governed lifecycle:
  - Quote or inquiry context
  - Hold
  - Deposit pending
  - Confirmed
  - Fully paid
  - Cancelled
  - Refunded
- Inventory reservation, deposit evidence, confirmation, and cancellation all become auditable transitions.

### Keep

- Keep room-type capacity checking.
- Keep booking event trail concept.
- Keep lead-to-booking commercial progression.

### Change

- Introduce hold expiry and release logic.
- Separate booking draft from confirmed booking.
- Add payment transaction model and confirmation rules.
- Add document completion and operations readiness checkpoints.

### Delete

- Delete the current manual "Confirmed" as acceptable final architecture.
- Delete direct dependence on trip row counters as the only inventory control.

### Risks

- If passport/attachments are implemented first, booking document requirements will be reworked.
- If trip inventory is not redesigned first, booking confirmation logic will be fragile.

### Dependencies

- Trip/departure/inventory direction.
- Lead/opportunity contract.
- Passport/document model.

### Priority

- `P0`

## 5. Handoffs

### Current Workflow

- Handoffs are queue items with reason, priority, status, and optional owner/assignee.
- They are created from some safety flows and manually from leads.
- Admin board supports Pending, In Progress, Resolved.
- Not all handoff-worthy situations create a queue item.
- No automation lock is guaranteed.

### Desired Workflow

- Handoffs become cases.
- Every active handoff pauses automation for the relevant traveler/channel/session.
- Cases carry owner, SLA, reason taxonomy, transcript context, and resolution history.

### Keep

- Keep duplicate, blacklist, and conflict as mandatory escalation triggers.
- Keep visible handoff board and notifications.
- Keep priority concept.

### Change

- Make case creation mandatory for all true human-takeover paths.
- Add automation lock.
- Add structured reasons and status transitions.
- Add conversation/session context packaging.

### Delete

- Delete the assumption that a lead flag alone equals real handoff.
- Delete separate "owner" and "assigned_to" ambiguity in the long-term model.

### Risks

- If agent redesign happens before handoff case model, session work will be redone.
- If Instagram arrives before automation lock exists, takeover failures are likely.

### Dependencies

- Agent session redesign.
- Lead/opportunity redesign.
- Auth/user ownership model.

### Priority

- `P0`

## 6. AI Agent

### Current Workflow

- Deterministic in-memory state machine.
- Phone -> trip type -> trip confirmation -> passport if international -> room -> flight -> currency -> booking draft.
- Sessions complete after handoff, decline, error, or draft booking creation.

### Desired Workflow

- Durable channel-aware conversation engine with explicit state transitions.
- Consent, identity, interest qualification, waitlist, document review, payment review, and human takeover become first-class states.
- Agent should stop cleanly when human case is active and resume only under policy.

### Keep

- Keep deterministic workflow philosophy for critical sales steps.
- Keep language detection and numbered-choice support.
- Keep human escalation for risky identity cases.

### Change

- Persist sessions.
- Fix weak states like `awaiting_country_code`, `awaiting_flight`, and `awaiting_currency`.
- Separate completion states:
  - handoff
  - abandoned
  - booking draft created
  - booking confirmed
  - failed
- Make agent write against the final CRM model, not a temporary demo contract.

### Delete

- Delete in-memory-only sessions as a production approach.
- Delete the current practice of ending the session immediately after draft creation while implying the booking can now be confirmed.

### Risks

- Agent redesign too early will be thrown away if lead/booking/handoff contracts are not settled first.
- Document collection states will have to change if passport/attachments are not designed before final agent implementation.

### Dependencies

- Lead redesign.
- Handoff case model.
- Booking lifecycle.
- Passport/document model.
- Source-of-truth decision.

### Priority

- `P1`

## 7. Passport Management

### Current Workflow

- International trip flow collects passport fields sequentially in session state.
- SQLite has traveler passport columns.
- No robust persistence or review workflow exists.

### Desired Workflow

- Passport management becomes a controlled document requirement workflow.
- Data should be stored as structured passport metadata plus secure document reference.
- Passport completion/review status should be visible against the traveler and booking.

### Keep

- Keep the business rule that international bookings require passport information.
- Keep the initial required field set:
  - full name
  - passport number
  - nationality
  - expiry date
  - passport image

### Change

- Move passport flow out of session-only temporary state.
- Make passport requirement attach to booking/trip requirement logic.
- Add review status and validation rules.

### Delete

- Delete the idea that traveler-level nullable columns alone are enough for passport management.
- Delete `skip` as an acceptable final-production path for required international documentation.

### Risks

- If implemented before secure attachments, we will build an unsafe document flow and redo it.
- If implemented before booking lifecycle, passport completion may attach to the wrong business entity.

### Dependencies

- Attachment/document model.
- Booking lifecycle.
- Trip requirements.
- Security/compliance controls.

### Priority

- `P1`

## 8. Attachments

### Current Workflow

- Local upload endpoint accepts a passport file and saves it under `uploads/passport/<session_id>`.
- Session stores relative file reference.
- No attachment model exists.

### Desired Workflow

- Attachments become a secure document service used by passport management and future CRM documents.
- Each file gets metadata, owner entity, type, size, uploader, status, and audit history.

### Keep

- Keep allowed business file types aligned with product requirements.
- Keep file-size enforcement.

### Change

- Move from local demo storage to secure managed storage.
- Add attachment entity and ownership links.
- Add validation, malware scan, access policy, and retention/deletion handling.

### Delete

- Delete local filesystem document storage as the production design.
- Delete session-only attachment references.
- Delete unsupported extra formats from final client scope unless the business explicitly wants them.

### Risks

- This area has the highest compliance exposure.
- Any partial implementation before access model and entity ownership are decided will create rework.

### Dependencies

- Security architecture.
- Passport/document model.
- Traveler and booking ownership design.

### Priority

- `P0`

## 9. Phone Normalization

### Current Workflow

- Shared normalization handles country detection, Egyptian defaults, lookup variants, and country-confirmation fallback.
- Identity resolution uses normalized values plus lookup variants.
- Traveler and lead creation/update rely on this logic.

### Desired Workflow

- Phone normalization remains a central shared service contract across CRM, agent, middleware, and future Instagram ingestion.
- It becomes one of the few foundational pieces we should stabilize first and keep stable.

### Keep

- Keep centralized normalization.
- Keep variant generation for backward compatibility.
- Keep country-confirmation behavior for ambiguous numbers.
- Keep blacklist/duplicate safety behavior on normalized lookup.

### Change

- Formalize normalized-phone contract for all modules.
- Add verified-phone state in the future customer model.
- Ensure all flows handle ambiguous-country cases consistently.

### Delete

- Delete any future duplicate implementations of phone parsing in separate stacks.
- Delete route-specific drift from the shared normalization rules.

### Risks

- If changed after lead/traveler redesign starts, matching and dedupe bugs will spread into every migration path.
- Over-aggressive normalization changes could break backward lookup to historical records.

### Dependencies

- None structurally. This is an early foundational layer.

### Priority

- `P0`

## 10. CRM Dashboard

### Current Workflow

- Flask dashboard shows headline KPIs for travelers, leads, trips, handoffs, and draft bookings.
- Demo dashboard shows workbook-derived stats and recent leads.
- React admin has a prototype shell.

### Desired Workflow

- Dashboard becomes a management surface based on the final CRM model.
- It should reflect:
  - inquiry volume
  - active opportunities
  - follow-up SLA
  - booking pipeline
  - handoff queue health
  - trip inventory health
  - payment pipeline

### Keep

- Keep a concise executive dashboard concept.
- Keep operational visibility into handoffs and draft bookings.

### Change

- Rebuild dashboard only after lead, booking, and handoff models stabilize.
- Remove workbook-derived KPIs from any production-facing dashboard.

### Delete

- Delete prototype dashboard assumptions as a data contract.
- Delete any metrics that depend on overloaded current lead stages once the new pipeline is defined.

### Risks

- Dashboard implemented too early will be rewritten after lead and booking redesign.

### Dependencies

- Lead redesign.
- Booking redesign.
- Handoff case redesign.
- Source-of-truth decision.

### Priority

- `P2`

## Rework Warnings

These are the decisions most likely to cause expensive rework if implemented too early.

### Do Not Implement Before Source-Of-Truth Is Settled

- CRM dashboard rebuild
- React admin expansion
- sheet-sync-dependent workflow automation

### Do Not Implement Before Booking Lifecycle Is Settled

- passport completion logic
- document-required booking gates
- operations checklist
- payment confirmation UI

### Do Not Implement Before Handoff Case Model Is Settled

- production agent session redesign
- Instagram human takeover
- queue ownership reporting

### Do Not Implement Before Attachment Model Is Settled

- passport uploads beyond demo
- customer document center
- document review workflows

## Final Implementation Order

This is the implementation sequence I would approve as CTO.

### Phase 1: Foundation Stabilization

- Source-of-truth decision and write-path consolidation plan
- Phone normalization contract freeze
- Duplicate and blacklist safety preservation
- Remove prototype ambiguity: Flask CRM is the active product
- Basic auth/audit architecture decision
- Booking status bug and terminology cleanup so draft is never implied as confirmed
- Agent session close-state clarification for demo safety

Reason:

- Everything else depends on not building on top of split truth and misleading status semantics.

### Phase 2: Core CRM Model Redesign

- Traveler model cleanup
- Lead redesign into inquiry plus opportunity direction
- Handoff redesign into case plus automation lock
- Controlled enums/taxonomies for statuses and reasons
- Merge audit design

Reason:

- This phase sets the commercial operating model the rest of the product must follow.

### Phase 3: Trip And Inventory Redesign

- Trip product vs departure decision
- Auto Trip ID policy if needed
- Room inventory redesign
- Hold model and inventory ledger direction
- Trip requirements model

Reason:

- Booking redesign cannot be done correctly until trip/inventory rules are final.

### Phase 4: Booking Workflow Redesign

- Draft vs hold vs deposit pending vs confirmed lifecycle
- Payment transaction design
- Cancellation/refund flow
- Booking event/state history
- Operations handoff points

Reason:

- This is the biggest commercial-risk area in the current system.

### Phase 5: Passport And Attachments

- Secure attachment model
- Passport metadata model
- Passport review and completion workflow
- Booking/trip requirement enforcement for international travel

Reason:

- This must be done after booking and trip requirements are clear, not before.

### Phase 6: AI Agent Workflow Redesign

- Durable sessions
- state-machine rewrite against final CRM contracts
- waitlist and follow-up logic
- human takeover integration
- document and payment-aware conversation states

Reason:

- The agent should be rebuilt once the business workflow is stable, not used to define it.

### Phase 7: CRM Dashboard And Operator UX

- Rebuild dashboard on final metrics
- owner queues
- SLA views
- booking pipeline views
- handoff case views

Reason:

- Dashboards should report the final business model, not the transitional one.

### Phase 8: Client Validation

- UAT scenarios
- seeded acceptance data
- staff workflow validation
- sign-off on scope

Reason:

- Validate after the operating model is coherent, not while it is still shifting.

### Phase 9: Instagram Integration

- Real Meta webhook ingestion
- idempotency and retry
- channel identity mapping
- human inbox and takeover

Reason:

- Channel integration should land after CRM, handoff, and agent rules are stable.

## Final CTO Call

If we try to implement passport management, attachments, or agent improvements before fixing the CRM core contracts, we will build the same product twice.

The safest delivery path is:

1. Freeze identity and source-of-truth decisions.
2. Redesign customer, lead, case, trip, and booking contracts.
3. Only then build documents, agent durability, dashboard reporting, and Instagram.

That order minimizes rework and gives the client a product that can actually survive production use.

