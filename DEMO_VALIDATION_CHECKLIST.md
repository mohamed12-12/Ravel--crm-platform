# Demo Validation Checklist

Date: 2026-06-16
Branch: `cleanup/mvp-to-product-structure`

## Persona Tests

- [x] Greeting includes the configured agent persona name.
- [x] `/api/agent-config` exposes the persona name.
- [ ] Confirm persona copy in Arabic and English once the client finalizes the exact persona text.

## Language Tests

- [x] Arabic text is detected as Arabic.
- [x] English text is detected as English.
- [x] Mixed short Arabic text still resolves to Arabic.

## Booking Tests

- [x] Numbered trip selection routes into confirmation.
- [x] Typed-word trip selection routes into confirmation.
- [x] Flight choice is captured in booking draft flow.
- [x] Existing traveler matching remains intact.
- [x] Traveler ID preservation remains intact.

## Passport Tests

- [x] International trips trigger passport-name collection.
- [x] Passport fields are collected in order.
- [x] Passport attachment metadata endpoint is present.
- [x] Existing travelers table migration adds passport columns safely.

## Attachment Tests

- [x] Passport attachment endpoint accepts metadata/file submission flow.
- [ ] Confirm allowed file types, size limits, and retention policy before any real customer rollout.

## Handoff Tests

- [x] Post-trip handoff flag is configurable and defaults off.
- [x] Identity conflict and blacklist handoff behavior still works.
- [x] CRM handoff queue remains available.

## Visa Tests

- [x] Table-based visa responses return a disclaimer.
- [x] Known destinations return deterministic placeholder answers.
- [x] Unknown destinations recommend human review instead of guessing.

## CRM Tests

- [x] Traveler lead/booking write-through still passes.
- [x] Manual lead logic still passes.
- [x] Sync safety still passes.
- [x] Full logic lock suite still passes.

## Demo Scenarios For Client Review

1. New customer enters phone number, then completes intake, then selects a local trip by number.
2. New customer enters phone number, then completes intake, then selects an international trip and reaches passport collection.
3. Existing traveler triggers website intent and receives the website link without advancing the workflow.
4. Visa lookup is requested for a known country and returns a disclaimer-based placeholder response.
5. Traveler with a completed trip hits the post-trip handoff branch when the flag is enabled.
6. CRM operator opens the handoff queue and reviews a record created by identity conflict or blacklist logic.
7. Attachment upload is tested in a local demo path only, not against cloud storage.

## Client Review Notes

- Review the exact persona text before demoing it externally.
- Confirm which prompts should always render numbered choices.
- Confirm whether the trip-type value should be displayed as `local`/`international` in API state, which is now the validated contract.
- Keep the demo on local workbook/database storage until production storage rules are approved.
