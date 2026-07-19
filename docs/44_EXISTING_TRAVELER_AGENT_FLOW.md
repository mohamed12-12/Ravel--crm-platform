# Existing Traveler Agent Flow

Date: 2026-07-15

When CRM lookup returns a verified traveler:

1. Store safe CRM fields only: traveler ID, name, status, WhatsApp fields.
2. Apply traveler status policy.
3. Continue only if status permits.
4. Preserve already-collected trip preferences.
5. Search trips only after verification.

Duplicate and restricted statuses do not proceed to trip search. They return safe review language and avoid exposing sensitive internal reasons.
