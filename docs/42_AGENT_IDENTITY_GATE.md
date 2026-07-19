# Agent Identity Gate

Date: 2026-07-15

The tool-calling runtime now starts in `identity_required`.

## Behavior

1. New session asks for WhatsApp naturally.
2. Chat input remains enabled.
3. Customer-provided trip preferences are preserved.
4. When a phone number is supplied, the backend normalizes it with `normalize_phone_input`.
5. The backend calls `ReadOnlyCRMTools.find_traveler_by_phone`.
6. Only verified CRM facts are stored in `session.preview.workflow` and `session.preview.traveler`.
7. Trip tools remain blocked until identity is verified.

## Frontend Safety

CRM Snapshot renders only verified CRM traveler previews. Trip Matching Engine does not render personalized trip matches before identity verification.
