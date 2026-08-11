# Phase 6 - Full trip knowledge and open-ended Q&A

## What this phase delivers

The agent can answer open-ended traveler questions about a selected trip, such as day-by-day inclusions, using only trip details stored in the CRM.

## Current state (investigated, not assumed)

- `Trip` currently stores core trip fields, room capacity, one `public_price` string, one `public_description` text field, and `sales_notes` (`apps/api/app/models/trip.py:12-51`).
- `Trip.to_dict()` exposes `public_price` and `public_description` but no day-program/itinerary fields in the inspected model (`apps/api/app/models/trip.py:125-158`).
- Trip create/update routes write `public_price`, `public_description`, and `sales_notes` from the Trip Inventory forms (`apps/api/app/routes/trips.py:251-272`, `apps/api/app/routes/trips.py:315-333`).
- Read-only trip details currently fetch `SELECT * FROM trips WHERE trip_id = ?` and return the row as a trip dict (`services/ai_agent/ai_agent_app/agent/read_only_tools.py:405-416`).
- Agent CRM bridge search includes `public_description` in text matching (`apps/api/app/services/agent_crm_bridge.py:924-932`).
- Verified trip media is already a separate read-only tool that only returns active, verified media (`services/ai_agent/ai_agent_app/agent/read_only_tools.py:435-486`).
- The prompt contains explicit anti-hallucination rules: do not confirm prices/inclusions unless CRM/tool output provides them, treat CRM/tool results as source of truth, never claim tool results unless they came from a tool, and pause on conflicts (`services/ai_agent/ai_agent_app/prompts/gemini_agent_system.md:44`, `services/ai_agent/ai_agent_app/prompts/gemini_agent_system.md:105-112`).

## Confirmed requirements this phase must satisfy

From the provided task attachment: the client confirmed the larger-scope interpretation. The agent should answer open-ended questions about trip details, not just show a fixed summary. New fields should be added on the existing Trip Inventory page if confirmed as the right place. The agent must only state trip details actually present in the CRM trip record and never invent plausible details.

## Working assumptions (for Phases 6 and 7 specifically)

Confirmed working assumption from the attachment: build open-ended Q&A over full trip details, not a fixed summary only.

## Design approach

- Add structured trip-program fields to the Trip data model. Prefer a structured child table or JSON field for day/title/details/inclusions/exclusions rather than forcing everything into `public_description`.
- Add Trip Inventory create/edit controls for those fields.
- Extend read-only trip details to return the full program in a clearly named `trip_program`/`itinerary` structure.
- Update agent prompt/tool instructions so open-ended trip answers must cite only fields present in the selected trip detail result.
- Add fallback wording for missing CRM details, for example saying the CRM does not list breakfast for that day rather than guessing.
- Add tests for direct Q&A, missing-data refusal, Arabic/English questions, and media/program separation.

## Dependencies on other phases

Can be implemented before Phase 7. If Phase 7 also changes Trip Inventory forms, coordinate schema/form migrations to avoid merge conflicts.

## Risks specific to this phase

The largest risk is hallucinated trip detail. The existing prompt already has CRM-only guardrails (`services/ai_agent/ai_agent_app/prompts/gemini_agent_system.md:105-112`); implementation must strengthen that pattern rather than bypass it with free-form model knowledge.

## Tests

Not run. No code was changed in this documentation-only pass.

## Live verification

Not run. No deployed app behavior was changed or verified in this documentation-only pass.

