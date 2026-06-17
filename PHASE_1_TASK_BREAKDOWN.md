# Phase 1 Task Breakdown

Date: 2026-06-17

## Task 1

Phone Normalization and CRM Identity Contract Cleanup

Estimated effort:

- Medium

Dependencies:

- none outside the current shared normalization contract
- existing regression tests for phone and identity paths

Files affected:

- `services/crm/system_services/phone_normalization.py`
- `services/crm/system_services/unified_service.py`
- `services/ai_agent/ai_agent_app/agent/session_flow.py`
- `services/ai_agent/ai_agent_app/server.py`
- `services/ai_agent/ai_agent_app/system_bridge.py`
- `services/ai_agent/ai_agent_app/sheets/excel_gateway.py`
- `services/ai_agent/ai_agent_app/web/static/app.js`
- `services/ai_agent/ai_agent_app/web/templates/index.html`
- `apps/api/app/routes/leads.py`
- `apps/api/app/routes/travelers.py`

Tests required:

- `tests/test_phone_normalization.py`
- `tests/test_phase1_readonly_agent.py`
- `tests/test_phase2_controlled_agent.py`
- `tests/test_phase3_demo_web.py`
- `tests/test_phase4_traveler_management.py`
- `tests/test_phase5_manual_lead_agent_logic.py`
- `tests/test_phase8_demo_alignment.py`
- `tests/test_phase10_full_logic_lock.py`
- `tests/test_phase11_demo_features.py`

## Task 2

Booking Unsupported Media Type Bug

Estimated effort:

- Small to Medium

Dependencies:

- clear request-payload contract between UI and booking endpoints
- confirmation of the exact error path during manual and demo booking submission

Files affected:

- `apps/api/app/routes/bookings.py`
- `apps/api/app/templates/bookings/index.html`
- `apps/api/app/templates/bookings/detail.html`
- `services/ai_agent/ai_agent_app/server.py`
- `services/ai_agent/ai_agent_app/web/api_routes.py`
- `services/ai_agent/ai_agent_app/web/static/app.js`
- possibly `services/ai_agent/ai_agent_app/web/static/crm_api.js`

Tests required:

- `tests/test_phase3_booking_write_through.py`
- `tests/test_phase8_demo_alignment.py`
- `tests/test_phase10_full_logic_lock.py`
- `tests/test_phase11_demo_features.py`

## Task 3

Agent Session Premature Close Audit/Fix

Estimated effort:

- Medium

Dependencies:

- session close semantics must be defined before any future agent or handoff redesign
- the shared phone contract from Task 1 should already be stable

Files affected:

- `services/ai_agent/ai_agent_app/agent/session_flow.py`
- `services/ai_agent/ai_agent_app/server.py`
- `services/ai_agent/ai_agent_app/web/static/app.js`
- `services/ai_agent/ai_agent_app/web/templates/index.html`

Tests required:

- `tests/test_phase1_readonly_agent.py`
- `tests/test_phase3_demo_web.py`
- `tests/test_phase8_demo_alignment.py`
- `tests/test_phase10_full_logic_lock.py`
- `tests/test_phase11_demo_features.py`

## Task 4

Regression Protection Tests

Estimated effort:

- Medium

Dependencies:

- Task 1 and Task 3 should be functionally defined before the final regression lock is written
- Task 2 should be defined enough that tests can assert the intended request behavior

Files affected:

- `tests/test_phone_normalization.py`
- `tests/test_phase1_readonly_agent.py`
- `tests/test_phase2_controlled_agent.py`
- `tests/test_phase3_demo_web.py`
- `tests/test_phase3_booking_write_through.py`
- `tests/test_phase4_traveler_management.py`
- `tests/test_phase5_manual_lead_agent_logic.py`
- `tests/test_phase8_demo_alignment.py`
- `tests/test_phase10_full_logic_lock.py`
- `tests/test_phase11_demo_features.py`

Tests required:

- the entire Phase 1 regression set
- full suite rerun after the lock is in place

## Recommended Order

1. Task 1
2. Task 2
3. Task 3
4. Task 4

Reason:

- phone normalization and identity contract work first because every other Phase 1 item depends on it
- booking request-contract cleanup should be fixed before we lock tests around it
- agent close semantics should be adjusted after the identity contract is stable
- regression tests should be finalized last so they lock the finished behavior, not a moving target

