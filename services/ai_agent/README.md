# AI Agent Service

The customer-facing conversational agent lives in `ai_agent_app/`.

## Two runtimes

`AI_AGENT_MODE` selects which one `server.py`'s `create_app()` wires up:

- `tool_calling` (the production path) -- `agent/tool_calling_runtime.py`'s
  `ToolCallingSessionRuntime`. A deterministic workflow policy
  (`agent/session_flow.py`'s `ConversationWorkflowPolicy`) drives most of the
  conversation; the model is only consulted for free-text steps it doesn't
  own outright.
- `deterministic` / `gemini` (the original spreadsheet-backed MVP demo path)
  -- `agent/session_flow.py`'s `SessionFlowManager`. Kept for the demo UI and
  older regression tests; preserve its behavior during cleanup-only changes.

## The write-status boundary

Every controlled write (`create_traveler`, `create_lead`, `create_handoff`,
`create_booking_draft`, ...) goes through `agent/write_tool_executor.py`'s
`GeminiWriteToolExecutor.execute()` and comes back with a
`write_result_contract` (`status`: `created`/`reused`/`blocked`/`failed`,
plus `executed`/`reused` booleans) -- see `write_result.py` and
`agent/write_response_gating.py`. This is the single point every caller
checks before ever telling a customer a write succeeded; nothing downstream
is allowed to infer success from a raw `executed` flag alone (a
`deduplicate_open` reuse is `executed=False` by design, not a failure).

## Dual-backend support (`CRM_ACCESS_MODE`)

`write_tool_executor.py`/`read_only_tools.py` branch on
`settings.crm_access_mode`:

- `shared_service` -- calls `services/crm/system_services/unified_service.py`
  in-process (SQLite).
- `api` -- calls the CRM Flask app's `/api/crm/agent/read` and
  `/api/crm/agent/write` over HTTP (`agent/crm_api_client.py`), which itself
  dispatches to either `UnifiedCRMService` (SQLite) or
  `apps/api/app/services/agent_crm_bridge.py`'s `PostgresAgentBridgeService`
  (Postgres) depending on that app's own configured database.

## Exploratory-question gating

`_merge_hints()`/`_is_exploratory_question()` in `tool_calling_runtime.py`
stop a hypothetical or conditional mention ("what if it were international?")
from being captured as a real decision for trip type, room type, flight
option, etc. -- free text defaults to read-only unless it carries an
unambiguous decision signal. See the inline comments at those functions for
the specific live-transcript bug this closed.

## Running it locally

See [`RUN_GUIDE.md`](../../RUN_GUIDE.md) at the repo root.
