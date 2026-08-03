# Lead Duplicate WriteResult Contract Fix

Date: 2026-08-03

## Summary

Duplicate `create_lead` calls are now returned with a structured `write_result_contract`, matching the booking duplicate/reuse pattern.

The behavior remains safe:

- no duplicate lead row is created
- the response includes the existing lead ID in structured fields
- customer-facing text uses already-recorded language
- SQLite/dev and PostgreSQL bridge paths both keep working

## Code Changes

- Updated `services/ai_agent/ai_agent_app/agent/write_tool_executor.py`
  - Added duplicate lead detection for rejected `create_lead` validations that identify an existing open lead.
  - Added structured contract fields:
    - `status: duplicate`
    - `executed: false`
    - `reused: true`
    - `record_type: lead`
    - `record_id: <existing lead id>`
    - `idempotency_key` when available from the lead row
    - `safe_customer_message_key: lead.duplicate_open`
  - Kept backwards-compatible response fields:
    - `result_id`
    - `lead_update`
    - `write_result.lead_update`
    - `write_result.write_result_contract`
    - top-level `write_result_contract`
    - `validation`
    - `audit`

## Tests Added

- Updated `tests/test_postgres_agent_bridge.py`
  - repeated `create_lead` returns structured existing lead ID in PostgreSQL bridge mode
  - no duplicate lead row is created in PostgreSQL bridge mode
  - duplicate customer message does not claim fresh creation
  - repeated `create_lead` returns structured existing lead ID in SQLite/dev mode
  - no duplicate lead row is created in SQLite/dev mode

- Updated `tests/test_port2_write_result_response_gating.py`
  - duplicate/reused lead messages use already-recorded language
  - duplicate/reused lead messages do not claim fresh creation

## Verification

Command:

```bash
python -m pytest tests/test_postgres_agent_bridge.py tests/test_data_authority.py tests/test_port1_write_safety_idempotency.py tests/test_port2_write_result_response_gating.py tests/test_port3_response_guard.py -q
```

Result:

```text
48 passed
```

## Acceptance Status

- Duplicate lead response is structured: passed
- No duplicate lead rows: passed
- Booking behavior unchanged: covered by existing booking safety tests
- Handoff behavior unchanged: covered by existing bridge/safety tests
- Staging smoke remains valid: yes; this change normalizes response shape and does not require a schema/data change

## Notes

The live staging agent-path smoke already verified that repeated lead calls do not create duplicate rows. This fix improves the response contract for that same safe condition so downstream consumers no longer need to parse warning text to find the existing lead ID.
