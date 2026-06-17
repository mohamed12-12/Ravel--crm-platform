# Fix Priority Plan

Date: 2026-06-16
Branch: `cleanup/mvp-to-product-structure`

## Status

P0 and P1 items have been fixed in this stabilization pass.

## P0

1. System crashes and startup failures from `SystemServiceSettings` contract drift.
2. Broken booking-sheet sync caused by string path handling in manual test settings.
3. Other startup blockers tied to manual instantiation of CRM settings.

Status: completed

## P1

1. AI/CRM identity resolution failing to match legacy phone lookup keys.
2. Manual lead handoff flow rejecting blacklisted leads before writing the blocked lead.
3. Legacy `record_agent_outcome` call shapes used by sync-safety coverage.

Status: completed

## P2

1. Phase 1 readonly-agent date/availability expectation mismatch.
2. Phase 8 demo session stage expectation mismatch.
3. Any other test/fixture mismatches that do not change runtime behavior.

Status: deferred for product decision

## P3

1. Lint tooling.
2. Code-quality cleanup.
3. Documentation polish.

Status: deferred

## Recommended Next Steps

1. Rebaseline the two remaining P2 tests with product approval if the current workflow is correct.
2. Add a small compatibility test matrix for legacy phone lookup keys so future cleanup does not reintroduce drift.
3. Address lint and dependency audit work in a separate standards pass.
