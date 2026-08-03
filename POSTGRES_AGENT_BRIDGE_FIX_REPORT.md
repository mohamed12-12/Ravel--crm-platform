# PostgreSQL Agent Bridge Fix Report

Date: 2026-08-03

## Goal

Enable the AI agent CRM bridge to use the same PostgreSQL-backed source of truth as the dashboard when the app is configured for PostgreSQL, while preserving the existing SQLite/dev path.

## What Changed

- Added PostgreSQL-aware agent bridge service in `apps/api/app/services/agent_crm_bridge.py`.
- Updated `apps/api/app/routes/crm.py` so:
  - SQLite app configs still use `UnifiedCRMService`.
  - Non-SQLite app configs use the new SQLAlchemy-backed PostgreSQL bridge.
- Added ORM mapping for `idempotency_key` in:
  - `apps/api/app/models/lead.py`
  - `apps/api/app/models/booking.py`
  - `apps/api/app/models/handoff.py`
- Added bridge regression tests in `tests/test_postgres_agent_bridge.py`.

## PostgreSQL Bridge Coverage

Implemented for PostgreSQL-backed mode:

- traveler lookup
- traveler profile
- traveler trip history
- trip search
- trip availability search
- trip details
- trip media
- lead lookup
- booking status lookup
- passport status lookup
- passport metadata save
- lead create/upsert
- lead stage update
- booking draft create
- handoff create/reuse

## Safety Outcomes

- SQLite/dev mode remains available.
- Agent bridge backend selection is config-driven, not hard-coded.
- Booking writes preserve idempotency/duplicate protection behavior.
- Direct booking bypass remains blocked.
- Validator rules still gate agent writes before execution.
- Write result contracts remain present on write responses.
- No change was made to global `AGENT_TOOL_ROUTER_MODE`; safe flags remain compatible.

## Tests Run

Passed:

- `python -m pytest tests/test_postgres_agent_bridge.py tests/test_data_authority.py tests/test_port1_write_safety_idempotency.py -q`

Result:

- `28 passed`

Bridge-specific coverage now includes:

- agent read bridge uses PostgreSQL mode when configured
- returning traveler lookup works on PostgreSQL mode
- trip search works on PostgreSQL mode
- handoff write works on PostgreSQL mode
- booking draft confirmation writes safely
- repeated confirmation does not create a duplicate booking
- SQLite mode still works
- direct booking bypass remains blocked

## Current Verification Status

Confirmed in local automated verification:

- PostgreSQL-mode route selection works
- SQLAlchemy-backed reads work through `/api/crm/agent/read`
- SQLAlchemy-backed writes work through `/api/crm/agent/write`
- Duplicate booking protection prevents a second booking record
- SQLite path still responds successfully

## Staging Note

This turn focused on implementation and automated regression coverage. A fresh full staging smoke rerun was not executed in this turn, so staging-specific agent-path confirmation should be rerun before production cutover.

Recommended staging smoke after this patch:

1. agent traveler lookup
2. agent trip search
3. agent booking confirmation flow
4. repeated confirmation duplicate protection
5. handoff flow
6. confirm staging PostgreSQL receives writes
7. confirm SQLite file remains unchanged

## Acceptance Summary

- Agent CRM bridge works in PostgreSQL mode: `implemented and locally verified`
- SQLite/dev mode remains working: `verified`
- No unsafe write or duplicate booking introduced: `verified in tests`
- Safe demo flags remain active-compatible: `verified by design`
- Staging PostgreSQL smoke rerun: `still recommended before cutover`
