# Plan Overview - Confirmed Client Change Requests

Source note: this plan is based on the provided implementation task attachment. A committed `client_change_requests_2026-08-09.md` was searched for but not found in the active repo pass, so confirmed requirements below cite the attachment rather than an unavailable local file.

Important constraint for this documentation pass: no application code was changed. Tests and live verification are therefore marked "not run" in the phase documents.

## Phase List

1. Phase 1 - Payment preference question
2. Phase 2 - Refund statuses and refund amount
3. Phase 3 - Payment screenshot upload on traveler profile
4. Phase 4 - Auto-assign new leads to available Sales
5. Phase 5 - Voice messages, transcription, and agent reply
6. Phase 6 - Full trip knowledge and open-ended Q&A
7. Phase 7 - Room-type-dependent pricing and multi-currency
8. Phase 8 - Cross-cutting testing, documentation, and rollout sequencing

## Dependencies

- Phase 1 should be resolved before any payment-flow implementation because the code currently supports payment currency, while "payment method" is not represented as a separate field.
- Phase 2 can be implemented after Phase 1's terminology decision because refund status is payment-adjacent but uses booking payment-status paths.
- Phase 3 can be implemented independently of Phases 1 and 2 because it extends the traveler document upload pattern.
- Phase 4 can be implemented independently, but should preserve manual assignment and RBAC behavior already present in leads/bookings.
- Phase 5 should be implemented after the current typed-message path is understood because voice transcription must re-enter the same protected pipeline.
- Phase 6 should happen before Phase 7 only if trip-program content influences room/pricing UI copy; otherwise they can be parallelized at the schema/design level.
- Phase 7 depends on the existing trip inventory and booking draft pricing fields, and must not break room capacity checks.
- Phase 8 follows every implementation phase and records actual test/live-verification results.

## Complexity And Risk

| Phase | Complexity | Risk | Reason |
| --- | --- | --- | --- |
| 1 | Low | Medium | Small surface area, but the wording is semantically ambiguous because the current system asks currency, not payment method. |
| 2 | Medium | High | Touches payment status transitions, booking persistence, RBAC, and agent-visible booking status. |
| 3 | Medium | Medium | Existing traveler document upload can be reused, but adding a new CRM-facing document type must avoid passport-specific side effects. |
| 4 | Medium | Medium | Manual assignment already exists; auto round-robin must be additive and deterministic under concurrency. |
| 5 | High | High | Adds audio ingestion/transcription and must not create a parallel unsafe conversation path. |
| 6 | High | High | Adds broader trip knowledge and open-ended answers; anti-hallucination constraints are critical. |
| 7 | High | High | Requires pricing data-model changes and agent behavior changes without breaking room inventory/draft booking logic. |
| 8 | Medium | Medium | Mostly coordination, but depends on complete evidence from tests and live verification across all prior phases. |

## Proposed Safe Implementation Order

1. Resolve Phase 1 ambiguity first.
2. Implement Phase 3 if a low-risk early win is wanted after Phase 1.
3. Implement Phase 2 before any broader payment/pricing work.
4. Implement Phase 4 once assignment behavior and employee data are verified in the target environment.
5. Implement Phase 6 trip knowledge fields and read-only retrieval.
6. Implement Phase 7 room-type pricing after Phase 6 schema/read-tool patterns are settled.
7. Implement Phase 5 in smaller sub-steps because it touches external media, transcription, Instagram, and the agent pipeline.
8. Keep Phase 8 active throughout, then close it with final test/deploy/live-verification evidence.

