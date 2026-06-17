# Root Cause Analysis

Date: 2026-06-16
Branch: `cleanup/mvp-to-product-structure`

## Executive Summary

The original regression run had 19 failures. After stabilization work, 17 of those are fixed. The remaining 2 failures are P2 contract mismatches, not runtime crashes.

The failures clustered into four root causes:

1. CRM settings contract drift between manual tests and `SystemServiceSettings`.
2. Phone lookup-key and blacklist matching drift in lead/identity flows.
3. Phase 1 trip-visibility expectation mismatch with current date filtering.
4. Phase 8 demo-session stage expectation mismatch with current session bootstrap.

## Root Cause Groups

### 1. `SystemServiceSettings` contract drift

Severity: P0

Root cause:

`SystemServiceSettings` required Google Sheets fields and assumed path-like objects, but multiple tests instantiate it manually with only local Excel settings and plain strings. That caused constructor crashes in phase 9 and phase 10, and prevented booking sheet mirroring from being verified.

Fixed:

- Added safe defaults for Google fields.
- Added path coercion in `__post_init__` so string paths become `Path` objects.

### 2. Phone identity and blacklist contract drift

Severity: P1

Root cause:

Identity matching used the normalized lookup-key form, while several fixtures and live workbook rows still used the legacy `20:XXXXXXXXXX` key shape. The manual lead route also short-circuited blacklisted leads before the service could create the blocked lead + handoff record expected by the tests.

Fixed:

- Added lookup-key variant matching for canonical and legacy keys.
- Normalized lookup keys to the prefixed workbook form.
- Accepted legacy `customer_name` / `status_snapshot` / `intent` call shapes in `record_agent_outcome`.
- Removed the premature manual-lead blacklist rejection so the service can write the blocked lead and handoff.

### 3. Phase 1 trip visibility expectation mismatch

Severity: P2

Root cause:

The phase 1 fixture uses a trip dated `2026-06-01`, while the current selection logic excludes trips that depart before `TODAY` (`2026-06-16` in this environment). The code is behaving consistently with its current date filter.

Not fixed:

- This is a product/test contract question, not a crash.

### 4. Phase 8 demo bootstrap stage mismatch

Severity: P2

Root cause:

The demo session currently boots into `awaiting_phone`, while the phase 8 test expects `awaiting_intake`. The current implementation is internally consistent, but the test and the runtime contract disagree.

Not fixed:

- This is a UX/workflow contract mismatch, not a runtime failure.

## Failure Inventory

| File | Test | Root Cause | Severity | Effort | Risk |
| --- | --- | --- | --- | --- | --- |
| `tests/test_phase10_full_logic_lock.py` | `test_agent_booking_creates_lead_and_booking_in_db_and_sheet` | Settings contract drift | P0 | M | Low |
| `tests/test_phase10_full_logic_lock.py` | `test_blacklisted_traveler_blocks_lead_and_sets_critical_priority` | Settings contract drift + phone-key drift | P1 | M | Low |
| `tests/test_phase10_full_logic_lock.py` | `test_cancelled_trip_never_shown_to_agent` | Settings contract drift | P0 | S | Low |
| `tests/test_phase10_full_logic_lock.py` | `test_create_booking_draft_rejects_cancelled_trip` | Settings contract drift | P0 | S | Low |
| `tests/test_phase10_full_logic_lock.py` | `test_create_booking_draft_rejects_sold_out_room` | Settings contract drift | P0 | S | Low |
| `tests/test_phase10_full_logic_lock.py` | `test_create_booking_draft_succeeds_with_capacity` | Settings contract drift | P0 | S | Low |
| `tests/test_phase10_full_logic_lock.py` | `test_date_tbd_trip_in_tbd_list` | Settings contract drift | P0 | S | Low |
| `tests/test_phase10_full_logic_lock.py` | `test_duplicate_phone_triggers_handoff_no_new_traveler` | Settings contract drift + phone-key drift | P1 | M | Low |
| `tests/test_phase10_full_logic_lock.py` | `test_empty_db_first_traveler_is_tr00001` | Settings contract drift | P0 | S | Low |
| `tests/test_phase10_full_logic_lock.py` | `test_existing_traveler_is_matched_not_duplicated` | Settings contract drift + phone-key drift | P1 | M | Low |
| `tests/test_phase10_full_logic_lock.py` | `test_name_conflict_triggers_phone_name_conflict_handoff` | Settings contract drift + phone-key drift | P1 | M | Low |
| `tests/test_phase10_full_logic_lock.py` | `test_new_open_trip_visible_to_agent` | Settings contract drift | P0 | S | Low |
| `tests/test_phase10_full_logic_lock.py` | `test_new_traveler_gets_next_sequential_tr_id` | Settings contract drift | P0 | S | Low |
| `tests/test_phase10_full_logic_lock.py` | `test_tr_id_skips_above_gap` | Settings contract drift | P0 | S | Low |
| `tests/test_phase9_sync_safety.py` | `test_sync_failure_does_not_corrupt_db_and_queues_issue` | Settings contract drift + legacy caller aliases | P0 | M | Low |
| `tests/test_phase5_manual_lead_agent_logic.py` | `test_manual_lead_blacklist_creates_handoff_and_blocks` | Phone-key drift + premature blacklist rejection | P1 | M | Medium |
| `tests/test_phase5_manual_lead_agent_logic.py` | `test_manual_lead_conflict_creates_handoff` | Phone-key drift | P1 | M | Low |
| `tests/test_phase1_readonly_agent.py` | `test_build_agent_response_handles_phase1_cases` | Date-filter/test contract mismatch | P2 | S | Low |
| `tests/test_phase8_demo_alignment.py` | `test_demo_web_calls_crm_services_safely_and_creates_records` | Session-stage test contract mismatch | P2 | S | Low |

## Notes

- P0 and P1 issues were fixed in this pass.
- The remaining failures are intentionally left untouched because they are P2 contract mismatches, not broken runtime behavior.
