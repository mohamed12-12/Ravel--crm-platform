# Rahma Traveler Sales Agent System Redesign Plan

Date: 2026-05-12

## 1. Purpose

Redesign the current local prototype into a cleaner, efficient sales-agent system that can:

- Run a professional web chat demo for the client.
- Use an AI agent for natural conversation.
- Read and write customer, trip, lead, and booking data through controlled tools.
- Integrate with the current workbook first, then Google Sheets without rewriting the agent.
- Keep secrets in environment variables.
- Remove generated and duplicate files only after the cleanup list is reviewed.

This is a planning document. No production behavior should be changed until the plan is approved.

## 2. Current Facts From The Repository

Known project files:

- `SALES_AGENT_IMPLEMENTATION_PLAN.md` defines the original phased build.
- `scripts/phase0_cleanup.py` through `scripts/phase5_booking_automation.py` contain useful business logic.
- `demo_web/app.py` currently mixes Flask routes, workbook setup, deterministic demo flow, Gemini setup, and booking routes.
- `agent/` contains a newer Gemini experiment.
- `demo_web/templates/index.html` is the structured dashboard demo.
- `demo_web/templates/chat.html` is a separate chat-only demo.
- `.env` currently exists and contains only the Gemini key name/value.
- There is no `.gitignore` yet.
- The server at `127.0.0.1:5001` was not running during inspection, which matches the browser message.

Important issue found:

- The AI prompt mentions tools named `get_available_trips` and `log_interaction`, but only `lookup_customer` and `create_booking_draft` are registered.
- The AI chat route sets the session stage to `ai_chat`, but the older UI still expects structured session fields such as `finalResult`. This makes the current AI web demo unreliable.

## 3. Target Architecture

The redesigned app should separate responsibilities clearly:

```text
web app
  -> API routes
  -> session store
  -> agent orchestrator
  -> controlled tools
  -> sheet gateway
  -> Excel adapter now
  -> Google Sheets adapter later
```

Recommended final structure:

```text
app/
  server.py
  config.py
  web/
    templates/
    static/
  agent/
    orchestrator.py
    prompt.py
    memory.py
    schemas.py
  tools/
    customer_tools.py
    trip_tools.py
    lead_tools.py
    booking_tools.py
  sheets/
    gateway.py
    excel_adapter.py
    google_sheets_adapter.py
  services/
    phone_service.py
    policy_service.py
    audit_service.py
docs/
  implementation/
  archive/
scripts/
  migration/
tests/
```

Current phase scripts should be reused as source logic during the refactor, not deleted immediately.

## 4. Agent Design

The AI must not write directly to the sheet. It should call controlled tools only.

Core loop:

1. User message arrives from web demo.
2. Agent orchestrator updates conversation state.
3. Agent asks for missing required data: full name, WhatsApp, trip type.
4. Tools perform CRM lookup, trip search, lead update, interaction log, or booking draft.
5. Agent replies using only data returned by tools.
6. Every write is logged.

Required tools:

- `normalize_phone`
- `find_traveler_by_phone`
- `create_traveler`
- `get_upcoming_trips`
- `log_interaction`
- `upsert_lead`
- `create_booking_draft`
- `create_handoff`

Safety rules:

- Do not reveal blacklist status to the customer.
- Do not invent trip dates, prices, capacity, or booking confirmation.
- Do not book if trip data is missing or capacity is unclear.
- If duplicate customer records exist, hand off to a human.
- If a customer asks about payment, refund, complaint, cancellation, or custom trip, hand off.

## 5. Sheet Integration Plan

Phase A: Keep Excel working.

- Use the existing phase 5 workbook as the demo source.
- Keep runtime workbook separate from source workbook.
- Build a `SheetGateway` interface so the agent does not know whether data comes from Excel or Google Sheets.

Phase B: Add Google Sheets.

- Add a Google Sheets adapter with the same gateway methods.
- Use service account credentials from `.env`.
- Share the Google Sheet with the service account email.
- Test read-only first, then controlled writes.

Gateway methods:

- `get_traveler_by_phone(phone_key)`
- `create_traveler(payload)`
- `get_upcoming_trips(trip_type, today)`
- `log_interaction(payload)`
- `upsert_lead(payload)`
- `create_booking_draft(payload)`
- `get_dashboard_stats()`

## 6. Web App Plan

Use one professional web experience, not two separate demos.

Keep:

- Main dashboard shell from `demo_web/templates/index.html`.
- Inspector panels for CRM, trips, writes, leads, and booking.

Merge:

- Chat-only page behavior from `demo_web/templates/chat.html` into the main dashboard if needed.

Remove later:

- Duplicate chat-only route after the unified demo is stable.

Recommended pages:

- `/` client demo dashboard.
- `/api/chat/session` create session.
- `/api/chat/message` send message.
- `/api/health` health check.
- `/api/demo/reset` reset runtime data.

## 7. Professional Design System

Design direction:

- Premium travel operations dashboard.
- Clean, calm, trustworthy.
- No oversized marketing hero.
- Dense but readable panels for client demo and internal audit.

Color tokens:

```css
:root {
  --color-bg: #f7f8fb;
  --color-surface: #ffffff;
  --color-surface-muted: #f1f5f9;
  --color-ink: #172033;
  --color-muted: #667085;
  --color-line: #d9e2ec;
  --color-primary: #0f766e;
  --color-primary-strong: #115e59;
  --color-accent: #2563eb;
  --color-gold: #b7791f;
  --color-success: #15803d;
  --color-warning: #b45309;
  --color-danger: #b91c1c;
}
```

Component rules:

- Use 8px radius for panels, inputs, and buttons.
- Use icon buttons for reset, send, copy, and refresh actions.
- Use tabs or segmented controls for `Demo`, `CRM`, `Leads`, and `Bookings`.
- Chat bubbles must never overlap inspector content.
- Status labels must use consistent colors: success, warning, danger, neutral.
- Mobile layout should become one column: chat first, inspector after.

Required UI states:

- Server offline.
- AI provider missing key.
- Sheet unavailable.
- Customer found.
- Customer not found.
- Duplicate records.
- Human handoff.
- Trips available.
- No confirmed trips.
- Booking draft created.
- Booking blocked.

## 8. Cleanup Plan

Do not delete the original workbook or phase 5 source workbook.

Keep:

- `RT - Travelers Database.xlsx`
- `RT - Travelers Database.phase5.ready.xlsx`
- `SALES_AGENT_IMPLEMENTATION_PLAN.md`
- `SYSTEM_REDESIGN_PLAN.md`
- `TRIP_DATE_HANDLING_RULES.md`
- `scripts/`
- `tests/`
- `agent/` until the new orchestrator replaces it
- `demo_web/` until the unified app is built

Can delete after approval:

- `__pycache__/`
- `*.pyc`
- `~$*.xlsx`
- `.history/`
- old runtime files: `RT - Travelers Database.phase3.demo.xlsx`, `RT - Travelers Database.phase4.demo.xlsx`, `RT - Travelers Database.phase5.demo.xlsx`

Can archive after approval:

- `PHASE0_CLEANUP_REPORT.md`
- `PHASE1_READONLY_AGENT_USAGE.md`
- `PHASE2_CONTROLLED_AGENT_USAGE.md`
- `PHASE4_SALES_INTELLIGENCE_USAGE.md`
- `PHASE5_BOOKING_AUTOMATION_USAGE.md`
- `project_discovery_and_demo_plan.md.resolved`
- `instagram_agent_roadmap.md.resolved`
- `Chat Flow for Automation.pdf`
- `RT - Travelers Database - Travelers.pdf`
- older generated workbooks: `phase0.cleaned`, `phase0.ready`, `phase2.prototype`, `phase4.ready`

Only delete/archive after confirming there is a backup.

## 9. Required Environment Keys

Use `.env.example` as the template. Real secrets stay in `.env`.

Core:

- `APP_ENV`
- `APP_HOST`
- `APP_PORT`
- `APP_SECRET_KEY`
- `LOG_LEVEL`

AI:

- `AI_PROVIDER`
- `GEMINI_API_KEY`
- `GEMINI_MODEL`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`

Sheet:

- `SHEET_BACKEND`
- `EXCEL_SOURCE_WORKBOOK`
- `EXCEL_RUNTIME_WORKBOOK`
- `GOOGLE_SHEET_ID`
- `GOOGLE_SERVICE_ACCOUNT_JSON`
- `GOOGLE_APPLICATION_CREDENTIALS`

Meta/Instagram later:

- `META_VERIFY_TOKEN`
- `META_PAGE_ACCESS_TOKEN`
- `META_APP_SECRET`
- `META_GRAPH_API_VERSION`

Operations:

- `HUMAN_HANDOFF_PHONE`
- `HUMAN_HANDOFF_EMAIL`
- `ADMIN_ALERT_WEBHOOK_URL`
- `DEMO_RESET_ON_START`

## 10. Implementation Order

1. Stabilize current demo server so `/` and `/chat` do not show connection errors.
2. Create a unified app structure and move configuration into `.env`.
3. Build the `SheetGateway` with Excel adapter first.
4. Replace mixed AI/state-machine logic with one agent orchestrator.
5. Register all real tools used in the prompt.
6. Redesign the UI using the design system above.
7. Add tests for the web API and agent tool calls.
8. Run cleanup after approval.
9. Add Google Sheets adapter.
10. Add Instagram integration after the web demo and sheet gateway are stable.

## 11. Acceptance Criteria For The Redesign

- The web demo starts with one command.
- Missing env keys produce clear errors, not silent failures.
- The agent can complete the required sales flow in the browser.
- The agent uses sheet tools only, never direct free-form writes.
- The UI shows what the agent read and wrote.
- Runtime files can be regenerated.
- Generated files and secrets are ignored by git.
- Tests cover lookup, trip recommendation, controlled writes, handoff, and booking draft.
