# Tool Inventory and Contracts

Scope: Rahma/Ravel Traveler CRM + AI agent, promoted main commit `d306eb2`.

Safe demo mode remains:

```env
AGENT_TOOL_ROUTER_MODE=dry_run
AGENT_WRITE_TOOL_ENFORCEMENT=true
```

Global router enforcement is not enabled.

## Contract Rules

All tool calls must pass the runtime contract boundary in `services/ai_agent/ai_agent_app/agent/tool_contracts.py`.

- Inputs must be JSON objects.
- Registry `required` fields must be present and non-empty.
- Values must match the registry JSON-schema type when a type is declared.
- Tool outputs must be JSON objects.
- Write tool outputs must include a valid `write_result_contract`.
- Successful write contracts must include a record id.
- Tool exceptions are normalized to structured safe results.
- Raw exceptions, raw JSON payloads, internal tool names, router state, schema names, and trace terms are blocked from customer-facing replies by the response guard.
- Customer-visible CRM facts must be grounded in validated tool results or persisted session state.

## Inventory

### `search_traveler`

- Group: `identity_read`
- Access: read
- Input schema: `raw_phone` required string; optional `country_code` string
- Output schema: object with lookup phone, match status, handoff flags, actions, optional traveler
- Allowed states: `identity_required`, `new_traveler_profile_required`, `traveler_verified`, `trip_discovery`, `trip_selected`, `human_review`
- Preconditions: customer provides a usable WhatsApp/phone value
- Failure modes: CRM lookup unavailable, malformed phone, duplicate phone, no match
- Customer-safe fallback: ask for a valid WhatsApp number or offer human review

### `find_traveler_by_phone`

- Group: `identity_read`
- Access: read
- Input schema: `raw_phone` required string; optional `country_code` string
- Output schema: object with `status`, safe traveler fields, warnings
- Allowed states: same as `search_traveler`
- Preconditions: valid phone
- Failure modes: not found, duplicate, incomplete traveler fields, CRM error
- Customer-safe fallback: collect missing traveler details or route duplicate/incomplete identity to handoff

### `get_traveler_profile`

- Group: `traveler_read`
- Access: read
- Input schema: optional `traveler_id`, `raw_phone`, `country_code`
- Output schema: object with optional traveler and lookup mode
- Allowed states: `traveler_verified`, `trip_discovery`, `trip_selected`, `booking_draft`, `booking_confirmation_required`, `booking_created`, `handoff_pending`, `human_review`, `post_booking_support`
- Preconditions: verified traveler id or phone
- Failure modes: missing lookup key, not found, CRM unavailable
- Customer-safe fallback: ask for WhatsApp/traveler confirmation before speaking profile facts

### `get_traveler_trip_history`

- Group: `traveler_read`
- Access: read
- Input schema: optional `traveler_id`, `raw_phone`, `country_code`
- Output schema: object with `status`, summary, history, optional traveler
- Allowed states: same as `get_traveler_profile`
- Preconditions: verified traveler identity
- Failure modes: missing traveler, DB read failure
- Customer-safe fallback: say history could not be verified and offer human support

### `lookup_lead`

- Group: `traveler_read`
- Access: read
- Input schema: optional `lead_id`, `traveler_id`, `raw_phone`, `country_code`
- Output schema: object with `leads`
- Allowed states: traveler/profile and support states
- Preconditions: lead id, traveler id, or phone
- Failure modes: no match, CRM unavailable
- Customer-safe fallback: avoid lead status claims and ask for identifying details

### `search_trips`

- Group: `trip_read`
- Access: read
- Input schema: optional `trip_type`, `query`
- Output schema: object with trip buckets and combined `trips`
- Allowed states: `traveler_verified`, `trip_discovery`, `trip_selected`, `booking_draft`, `booking_confirmation_required`, `booking_created`, `human_review`, `post_booking_support`
- Preconditions: trip type or trip intent; identity may still be required by workflow policy
- Failure modes: unsupported trip type, CRM trip search failure, no trips
- Customer-safe fallback: ask for trip type/destination/date or offer human handoff

### `search_available_trips`

- Group: `trip_read`
- Access: read
- Input schema: optional `trip_type`, `destination`, `query`, `preferred_date`, `travelers`, `flight_option`, `room_type`
- Output schema: object with `status`, filters, `trips`, `open_trips`, `date_tbd_trips`
- Allowed states: same as `search_trips`
- Preconditions: verified traveler where workflow requires CRM-safe sales flow; valid trip filters
- Failure modes: no match, capacity filter excludes all options, CRM unavailable
- Customer-safe fallback: ask for alternative preferences or route capacity issues to human review

### `get_trip_details`

- Group: `trip_read`
- Access: read
- Input schema: `trip_id` required string
- Output schema: object with `status` and optional `trip`
- Allowed states: trip/search/booking/support states
- Preconditions: selected or supplied trip id
- Failure modes: missing trip id, trip not found, CRM unavailable
- Customer-safe fallback: ask customer to choose a trip from verified options

### `get_trip_media`

- Group: `trip_read`
- Access: read
- Input schema: `trip_id` required string
- Output schema: object with `status`, cover, gallery, media
- Allowed states: `trip_selected`, booking/support states where selected trip exists
- Preconditions: selected trip and verified media in CRM
- Failure modes: missing trip, no verified media, media table unavailable
- Customer-safe fallback: say no verified official images are available for that trip

### `get_booking_status`

- Group: `booking_read`
- Access: read
- Input schema: optional `booking_id`, `traveler_id`, `lead_id`
- Output schema: object with `bookings`
- Allowed states: `booking_draft`, `booking_confirmation_required`, `booking_created`, `handoff_pending`, `human_review`, `post_booking_support`
- Preconditions: booking id, traveler id, or lead id
- Failure modes: no booking, CRM unavailable
- Customer-safe fallback: say booking status could not be verified and ask for booking/customer details

### `get_passport_status`

- Group: `passport_read`
- Access: read
- Input schema: optional `traveler_id`, `raw_phone`, `country_code`
- Output schema: object with traveler, documents, passport flags
- Allowed states: `trip_selected`, `booking_draft`, `booking_confirmation_required`, `human_review`
- Preconditions: verified traveler or phone
- Failure modes: missing traveler, table/column unavailable, no passport data
- Customer-safe fallback: ask customer to upload passport only when workflow/trip requires it

### `validate_business_action`

- Group: `validation`
- Access: read
- Input schema: `action` required; optional action-specific fields
- Output schema: validation result object with decision, reasons, warnings, missing information, metadata
- Allowed states: all states where future writes may be considered
- Preconditions: action name in supported validation actions
- Failure modes: unsupported action, missing required business data, restricted traveler, duplicate booking, unavailable capacity/passport
- Customer-safe fallback: ask for missing information or route rejected action to human review

### `create_lead`

- Group: `lead_write`
- Access: write
- Input schema: lead/customer/trip/context fields; business-required fields may come from session context
- Output schema: object with `write_result_contract`, lead update, optional traveler, session update
- Allowed states: `new_traveler_profile_required`, `traveler_verified`, `trip_discovery`, `trip_selected`, `booking_draft`, `booking_confirmation_required`
- Preconditions: validation approval, safe identity state, sufficient customer/contact details
- Failure modes: validation rejected, lead write failure, malformed result, idempotent reuse
- Customer-safe fallback: say the request could not be saved and ask to retry or connect to a human

### `update_lead_stage`

- Group: `lead_write`
- Access: write
- Input schema: `lead_id`, `requested_stage`, optional status/follow-up/context fields
- Output schema: object with `write_result_contract`, lead update, session update
- Allowed states: same lead-write states
- Preconditions: existing lead, approved stage transition
- Failure modes: missing lead, unsupported stage, CRM write failure
- Customer-safe fallback: do not claim the stage changed; ask to retry or hand off

### `create_booking_draft`

- Group: `booking_write`
- Access: write
- Input schema: traveler/trip/room/flight/currency/passport/session fields
- Output schema: object with `write_result_contract`, booking draft, optional lead update, session update
- Allowed states: `booking_confirmation_required` only for normal write flow
- Preconditions: verified traveler, selected open trip, room type, explicit confirmation, no active duplicate booking, passport when required, capacity available
- Failure modes: wrong state, no confirmation, duplicate booking, capacity unavailable, missing passport, CRM write failure, malformed result
- Customer-safe fallback: no fake booking success; ask for confirmation/details, suggest another room, or hand off

### `create_handoff`

- Group: `handoff_write`
- Access: write
- Input schema: traveler/lead/trip/reason/priority/channel/summary fields
- Output schema: object with `write_result_contract`, handoff case, session update
- Allowed states: `booking_created`, `handoff_pending`, `post_booking_support`; also `human_review` only with controlled handoff precondition
- Preconditions: approved handoff trigger such as duplicate identity, capacity review, explicit human request, restricted traveler, or post-booking support
- Failure modes: wrong state, no handoff trigger, CRM write failure, duplicate open handoff reuse, malformed result
- Customer-safe fallback: no fake handoff success; say the request could not be submitted and provide retry/direct-contact language
