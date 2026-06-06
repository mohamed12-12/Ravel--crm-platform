# Phase 4 Sales Intelligence

Date: 2026-05-11

## What Phase 4 Adds

Phase 4 adds a sales-intelligence layer on top of the CRM and demo:

- trip interest tracking
- lead stage tracking
- follow-up reminders
- pipeline reporting
- repeat and VIP personalization

## Workbook Changes

Phase 4 introduces a `Leads` sheet.

Each lead row stores:

- customer identity and phone lookup fields
- linked traveler ID when available
- lead stage
- customer tier
- trip interest
- priority
- follow-up status and due date
- interaction count
- handoff reason

## Lead Stages

Current implemented stages:

- `Blocked`
- `Needs Review`
- `VIP Priority`
- `VIP Follow Up`
- `Repeat Priority`
- `Repeat Follow Up`
- `Qualified`
- `Follow Up Needed`
- `Existing Traveler`
- `New Lead`
- `New Inquiry`

## Follow-Up Logic

- `Blocked` -> `Do Not Contact`
- `Needs Review` -> `Urgent`
- `Qualified` -> `Follow Up Soon`
- `Follow Up Needed` -> `Awaiting Dates`
- `New Lead` -> `Qualify Lead`
- default -> `Monitor`

## Demo Impact

The web demo now shows:

- total leads
- qualification rate
- urgent reminders
- due-today reminders
- lead stage counts
- recent lead activity
- lead result per completed session

## Main Files

- `scripts/phase4_sales_intelligence.py`
- `scripts/phase4_prepare_workbook.py`
- `demo_web/app.py`
- `demo_web/static/app.js`
- `demo_web/templates/index.html`
