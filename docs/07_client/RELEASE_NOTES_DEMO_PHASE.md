# Release Notes: Demo Phase

Date: 2026-06-16
Branch: `cleanup/mvp-to-product-structure`

## New Features

- Agent persona name support is exposed through config and demo greeting copy.
- Arabic/English language detection is active in the session flow.
- Numbered choice replies work for the demo trip-selection flow.
- Typed-word trip-type replies now preserve the lowercase session contract expected by the CRM bridge.
- Website intent is recognized and can return the configured company URL.
- Visa placeholder responses are table-based and always include a disclaimer.
- Passport collection flow is available for international trips.
- Passport attachment metadata support is available in the demo path.
- Trip notes can supply discount notes without adding pricing formulas.
- Post-trip handoff can be enabled through configuration and defaults off.

## Behavior Changes

- Trip type state now remains lowercase (`local`, `international`) after resolution, matching the existing session and CRM contract.
- The demo session flow continues to advance through intake, trip selection, confirmation, room type, flight, and currency without changing the broader booking behavior.

## Configuration Options

- `AGENT_PERSONA_NAME`
- `WEBSITE_URL`
- `POST_TRIP_HANDOFF_ENABLED`
- `POST_TRIP_HANDOFF_RESPONSIBLE_EMPLOYEE`
- `META_VERIFY_TOKEN`
- `META_APP_SECRET`
- `META_PAGE_ACCESS_TOKEN`
- `META_GRAPH_API_VERSION`
- `PUBLIC_WEBHOOK_URL`

## Backward Compatibility Notes

- Existing traveler matching and Traveler ID preservation remain intact.
- Existing CRM lead, booking, and handoff flows remain intact.
- The visa feature is still a placeholder and does not perform live web search.
- Instagram/Meta production integration has not been started.
- No discount formulas, cloud attachment storage, or production webhook processing were added.
