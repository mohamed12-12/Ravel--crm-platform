# Phase 3 - Payment screenshot upload on traveler profile

## What this phase delivers

Traveler profiles can store and display payment screenshot uploads using the existing traveler document upload pattern, separate from passport documents.

## Current state (investigated, not assumed)

- Traveler documents already support a generic `category`, file metadata, `verification_status`, and notes (`apps/api/app/models/traveler_document.py:15-31`).
- The traveler detail route loads all documents, then separately identifies documents whose category is `passport` (`apps/api/app/routes/travelers.py:328-347`).
- The current upload route accepts a posted file, validates extension/MIME type, enforces a 10 MB max, stores it under the traveler upload root, and creates a `TravelerDocument` row (`apps/api/app/routes/travelers.py:601-647`).
- The upload route defaults `category` to `passport` and writes passport fields back to the traveler record regardless of category (`apps/api/app/routes/travelers.py:630-652`).
- The traveler detail template has a hidden `category=passport` upload form and a separate all-documents list (`apps/api/app/templates/travelers/detail.html:145-172`).
- The AI web chat also has a separate passport attachment endpoint and upload UI (`services/ai_agent/ai_agent_app/server.py:2146-2226`, `services/ai_agent/ai_agent_app/web/static/app.js:250-262`, `services/ai_agent/ai_agent_app/web/static/app.js:481-515`).

## Confirmed requirements this phase must satisfy

From the provided task attachment: add payment screenshot upload on traveler profile and reuse the existing passport-document-upload pattern after confirming it in code.

## Working assumptions (for Phases 6 and 7 specifically)

Not applicable.

## Design approach

- Add a traveler-detail upload section with `category=payment_screenshot`.
- Reuse the existing `TravelerDocument` table rather than adding a new screenshot table.
- Adjust `upload_document` so passport-specific traveler field updates only run for category `passport`.
- Add a filtered payment-screenshot list or badge on traveler detail.
- Add route/template tests proving passport uploads still update passport fields and payment screenshots do not.

## Dependencies on other phases

No strict dependency. It can be implemented before or after Phase 2.

## Risks specific to this phase

The existing upload route currently updates passport fields after every document upload (`apps/api/app/routes/travelers.py:648-652`). A payment screenshot must not overwrite passport metadata or `passport_attachment_ref`.

## Tests

Not run. No code was changed in this documentation-only pass.

## Live verification

Not run. No deployed app behavior was changed or verified in this documentation-only pass.

