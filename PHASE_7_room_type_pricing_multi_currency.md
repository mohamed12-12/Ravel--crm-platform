# Phase 7 - Room-type-dependent pricing and multi-currency

## What this phase delivers

Trip pricing supports independent prices per room type, and the agent shows the price for the room type being discussed instead of dumping all room prices upfront.

## Current state (investigated, not assumed)

- `Trip` currently has one `public_price` string and no independent single/double/triple price fields (`apps/api/app/models/trip.py:48-50`).
- Trip Inventory create/update writes only `public_price` for pricing (`apps/api/app/routes/trips.py:270`, `apps/api/app/routes/trips.py:331`).
- Trip list/detail templates display `trip.public_price` as a single value (`apps/api/app/templates/trips/index.html:174`, `apps/api/app/templates/trips/detail.html:85`).
- Booking has `room_type`, `room_group`, and `currency`, but no stored room price field in the inspected model (`apps/api/app/models/booking.py:23-31`).
- Booking draft creation passes `room_type` and `currency` to the service (`apps/api/app/services/agent_crm_bridge.py:627-645`) and capacity logic uses room type for holds (`apps/api/app/services/agent_crm_bridge.py:677-725`).
- Agent trip search filters by room availability, not price per room type (`services/ai_agent/ai_agent_app/agent/read_only_tools.py:389-400`, `apps/api/app/services/agent_crm_bridge.py:1035-1048`).
- Existing currency support found in the active flow is EGP/USD (`services/ai_agent/ai_agent_app/agent/workflow_policy.py:611-617`, `services/ai_agent/ai_agent_app/web/static/app.js:244`).
- No additional currency name was found in the provided task attachment.

## Confirmed requirements this phase must satisfy

From the provided task attachment: build independent price per room type, not base-plus-adjustment. Extend beyond USD/EGP only if a specific additional currency was named in the client follow-up; otherwise keep existing EGP/USD. The agent should show the price for the room type being discussed and should not dump all room-type prices upfront.

## Working assumptions (for Phases 6 and 7 specifically)

Confirmed working assumption from the attachment: independent price per room type. No additional currency beyond EGP/USD was named in the provided attachment, so the plan keeps EGP/USD.

## Design approach

- Add structured room-price storage. Prefer a child table keyed by `trip_id`, `room_type`, and `currency` to avoid multiplying columns as future currencies are added.
- Keep legacy `public_price` during migration for existing display and backward compatibility until all consumers use structured room prices.
- Update Trip Inventory forms to edit Single/Double/Triple prices for EGP and USD.
- Update read-only trip details and trip search responses to include room-price maps.
- Update agent logic to select the price matching the current/mentioned room type and current payment currency.
- Add fallback behavior when a room price is missing: do not quote a price; ask human/team review or say CRM has no verified price for that room/currency.

## Dependencies on other phases

Depends on Phase 1's terminology if payment currency collection changes. Coordinates with Phase 6 if both change Trip Inventory forms/read tools.

## Risks specific to this phase

The current booking draft and capacity system already relies on room type (`apps/api/app/services/agent_crm_bridge.py:677-725`). Pricing changes must not alter room availability/hold behavior. The agent prompt also explicitly forbids unverified price claims (`services/ai_agent/ai_agent_app/prompts/gemini_agent_system.md:44`, `services/ai_agent/ai_agent_app/prompts/gemini_agent_system.md:105`).

## Tests

Not run. No code was changed in this documentation-only pass.

## Live verification

Not run. No deployed app behavior was changed or verified in this documentation-only pass.

