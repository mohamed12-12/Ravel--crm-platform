# Gap Analysis

Date: 2026-06-16
Source requirement file: `client's latest requirement .md`

## Baseline

The stabilized MVP has a green backend regression suite and passing npm build/typecheck/test according to `FIX_REPORT.md`. The older `QA_REPORT.md`, `RUN_GUIDE.md`, and `FINAL_SUMMARY.md` still document the pre-fix state and should be treated as historical context unless updated in a later documentation pass.

## Feature Gaps

| Requirement | Current behavior | Required behavior | Gap | Affected files/modules | Complexity | Risk |
| --- | --- | --- | --- | --- | --- | --- |
| Agent persona customization | Agent uses hard-coded fallback copy plus optional copy lookup through the gateway. No explicit persona profile exists. | Client-provided persona can be configured for the agent. | Need persona contract, storage/configuration, defaults, and tests while preserving current responses until enabled. | `services/ai_agent/ai_agent_app/agent/session_flow.py`, copy lookup gateway paths, `.env.example`, CRM copy library if used. | Medium | Medium |
| Numbered choice responses | Trip recommendation lists are numbered and numeric selection works for trip choice. Other prompts use plain text choices. | All multiple-option questions should display numbered options and accept numbers. | Need define scope, then update prompt rendering and numeric parsing for trip type, room type, flight, currency, and future choices. | `services/ai_agent/ai_agent_app/agent/session_flow.py`, `services/ai_agent/ai_agent_app/web/static/app.js`, tests under `tests/test_phase*_demo*`. | Medium | Medium |
| Human support after trip completion | Handoff exists for risk cases. No post-trip-completion trigger found. | If trip ended and assistance is needed, transfer to human agent. | Need business rule for ended trip detection and support intents. | CRM trip/booking models, `services/crm/system_services/unified_service.py`, `apps/api/app/routes/handoffs.py`, agent session flow. | Medium | High |
| Flights/no-flights business model | Booking flow asks if the customer wants flights and records `With Flight` or `Without Flight`. | Agent should know trips may include flights but most are without flights. | Need approved copy and possibly trip-level flight availability/source data. | `session_flow.py`, trip schema/model fields, booking flow, copy library. | Low to Medium | Medium |
| VIP discounts | VIP status affects lead priority/stage. No discount calculation found. | Recognize and apply VIP discounts. | Need discount policy, pricing model, approval, audit, and customer messaging. | `services/crm/system_services/unified_service.py`, booking model/routes/templates, tests, possible database migration. | High | High |
| Group discounts | No active group discount workflow found. Existing docs mention future group/family logic. | Recognize and apply group booking discounts. | Need group-size capture, discount tiers, booking data model, and shared-phone policy. | Agent flow, CRM booking/lead models, admin UI, tests, possibly migrations. | High | High |
| Traveler ID handling | Identity logic preserves/reuses existing Traveler IDs and creates handoff for uncertain matches. | Traveler IDs must remain unchanged and match sheets/database. | Mostly satisfied. Need keep as non-regression requirement. | `services/crm/system_services/unified_service.py`, `apps/api/app/services/identity.py`, tests. | Low | Low |
| Trip ID handling | Trip IDs exist in trips/bookings. Manual admin trip routes exist. No explicit regeneration/alias policy. | Trip IDs may be modified or regenerated as needed. | Need clarify whether regeneration is manual, automated, or migration-only, and how linked records stay consistent. | `apps/api/app/routes/trips.py`, trip/booking/lead models, database migrations, sheet sync. | Medium to High | High |
| Passport information | Intake captures name, birthday, gender, nationality, phone. No passport fields found. | International trip booking must request passport information. | Need fields, validation, storage, privacy, and international-only flow branch. | Agent session state/flow, traveler/booking models, templates, migrations, tests. | High | High |
| Attachments support | No customer attachment upload/storage workflow found. CSV export uses "attachment" only as a response header. | Customers can upload attachments; CRM can access/manage them. | Need file upload API, storage, scanning, metadata model, permissions, CRM UI, retention. | `apps/api`, `services/ai_agent`, future Instagram media handlers, database migrations, admin templates. | High | High |
| Handoff functionality | CRM handoff queue exists, manual creation/update exists, agent pauses for identity risk cases. | Seamless transfer from AI agent to human agent when required. | Need define "seamless", live channel handoff behavior, agent pause/resume, assignment, operator replies, and customer-visible status. | `apps/api/app/routes/handoffs.py`, templates, `session_flow.py`, middleware, future Instagram service. | Medium to High | High |
| Website link sharing | No explicit website-link intent found. | Agent shares company website link on request. | Need official URL and language/campaign tracking rules. | `session_flow.py`, copy library, tests. | Low | Low |
| Contact page and human-agent evidence | Existing demo web chat and CRM/admin pages exist. No confirmed Contact page workflow or screenshot artifact. | Screenshot showing customer through Contact page and human agent response after handoff. | Need clarify target page/environment and build/test the scenario after handoff behavior is implemented. | Demo/admin web, possible `apps/admin-web`, `services/ai_agent` web templates, documentation/evidence folder. | Medium | Medium |
| Visa information lookup | No visa web lookup or browsing tool integration in product code found. | Agent searches web and provides visa requirements by destination. | Need trusted source, nationality inputs, disclaimer, caching/freshness, and escalation rules. | New service boundary likely under `services/ai_agent` or `services/crm`; tests; possibly external API integration. | High | High |
| Instagram real account integration | Placeholder webhook helpers and docs exist; production integration intentionally not implemented. | Client may expect eventual live customer-channel support. | Must remain separate phase with Meta token handling, signature validation, queues, rate limiting, audit, monitoring. | `services/instagram/webhooks.py`, `services/ai_agent/ai_agent_app/server.py`, middleware, database/audit models. | High | High |

## Cross-Cutting Gaps

- Product contracts need confirmation before changing agent behavior, especially because current tests are green.
- Sensitive data work is required for passport and attachments.
- Pricing/discount features need a finance-approved source of truth.
- Current frontend and middleware automated tests are placeholders, so UI-heavy changes will require new test coverage.
- Production readiness remains limited by no real Meta integration, no durable queues, no rate limiting baseline, no production monitoring, and no audit-log system.
