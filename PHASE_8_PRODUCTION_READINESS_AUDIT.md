# Phase 8 - Production Readiness Audit

Date: 2026-08-12

## Request Path

```text
HTTP /api/session
  -> create_app() loads Settings and validates production config
  -> SessionFlowManager or ToolCallingSessionRuntime is selected by AI_AGENT_MODE
  -> ToolCallingSessionRuntime.create_session()
  -> in-process SessionState stored in runtime._sessions

HTTP /api/session/<id>/message
  -> Flask route validates session id and JSON text
  -> session mode is checked against app AI_AGENT_MODE
  -> ToolCallingSessionRuntime.handle_message()
  -> deterministic identity / post-booking / confirmation / navigation / handoff gates
  -> deterministic required-step capture
  -> privacy guard
  -> deterministic hint extraction, trip selection/switch checks, trip lookup
  -> ConversationWorkflowPolicy.evaluate()
  -> backend-owned collection steps or deterministic writes
  -> off-script classifier only after capture/interruption gates fail
  -> GeminiAgent.respond() conversational/tool path when needed
  -> tool registry -> safety -> route audit -> allowed_tools -> ActionValidator -> write executor
  -> CRM API bridge in production or shared service in local/test
  -> write result contract / response guard / reply checkpoint
  -> serialized session JSON response
```

```text
POST /rahma-agent/webhook
  -> Meta signature validation
  -> configured page filtering
  -> parse_instagram_webhook()
  -> UnifiedCRMService.record_inbound_channel_event()
  -> message_key dedupe on channel + Meta message id
  -> optional Meta outbound canned reply
  -> JSON receipt with persisted / duplicates / reply counts
```

## Boundary Findings

- Input validation: JSON/session/text validation exists for web chat; Meta signature/page/message-id validation exists for Instagram webhooks.
- Output: web chat returns serialized in-memory session; webhook returns receipt counts.
- State mutation: customer workflow state mutates in `SessionState`; CRM mutations go through `GeminiWriteToolExecutor`.
- Failure behavior: provider/tool failures return safe fallback or blocked write contracts. Web chat route adds a recovery assistant message on unhandled exceptions.
- Retry behavior: Gemini retries once by default for retryable/timeout failures; CRM API client has a 15s timeout but no client-side retry; write services use idempotency and duplicate checks for high-impact writes.
- Logging: tool arguments and write audits are sanitized; classifier/provider logs include request/status metadata but not raw secrets.
- Timeout behavior: gunicorn timeout is 60s; Gemini timeout defaults to 20s per attempt; CRM API timeout defaults to 15s; webhook rate limit defaults to 300/min.

## Session And Recovery

- Same customer consecutive messages work only while the same process-local session exists.
- Process restart between turns loses `runtime._sessions`; `/api/session/<id>` returns missing session rather than recovering state from CRM.
- Partial workflow state is not persisted outside memory.
- Stale selected-trip risk is mitigated before booking by fresh traveler/trip validation and CRM duplicate/capacity checks, but the conversational selected-trip cache itself is in memory.
- Tool success followed by response generation failure is partly mitigated by deterministic writes and write-result contracts; response persistence is still session-memory only.
- Response generation success followed by session persistence failure is equivalent to process-memory loss because there is no durable session persistence.
- Duplicate Instagram webhook delivery is deduped by message id. Duplicate web-chat POST has no message id dedupe, but booking/lead/handoff writes have idempotency and duplicate checks.

## One Transition Per Turn

New Phase 8 tests verify bounded state changes for normal capture, invalid capture, side question, mixed-language capture, provider failure, rejected registered writes, deterministic write-tool rejection, classifier malformed output, and duplicate webhook delivery.

Observed behavior: backend-owned required-step turns capture at most the expected field plus the resulting stage/message update. No LLM path was shown to capture a field, switch trip/type, and execute a second write transition in the same turn.

## Tool Safety

LLM-callable write tools:

- `create_lead`
- `update_lead_stage`
- `create_booking_draft`
- `create_handoff`

Verified path:

```text
LLM tool request
  -> registered tool check
  -> input schema validation
  -> safety layer
  -> router audit / write-only enforcement
  -> workflow allowed_tools block
  -> GeminiWriteToolExecutor
  -> ActionValidator
  -> CRM service/API execution
  -> write_result_contract and response guard
```

Deterministic write-executor tools:

- `create_traveler`
- `update_traveler`
- `update_booking`
- `upload_passport`

Finding: these are not in the LLM write-tool registry and cannot be directly reached by an LLM conversational/off-script turn. Phase 8 tests pin this.

## Provider Failure

- Provider timeout/HTTP errors are converted to `GeminiProviderError`.
- Classifier provider failure/malformed JSON returns `{"category": "unclear", "confidence": 0.0}`.
- Conversational LLM failure returns a safe refusal/fallback and no tool execution.
- Unexpected tool/request structures are rejected before execution.
- Sensitive provider data is not intentionally logged; Google error logs use status/message/invalid field metadata.

## Webhook And Retry

- Instagram webhook dedupe exists on `channel + message_key`, where `message_key` is Meta `message.mid`.
- Duplicate delivery creates no second inbound interaction and no second outbound reply because outbound reply is only attempted on `created=True`.
- Concurrent duplicate delivery still has a race window in SQLite/shared-service mode because dedupe is read-then-insert without a unique DB constraint. Production CRM API/Postgres write paths rely on application-level idempotency checks, but schema-level uniqueness is not present in the inspected models.
- This is a production blocker for exactly-once webhook persistence under concurrent duplicate delivery at scale.

## Concurrency

- Agent sessions are process-local. The tracked PM2 config keeps `rahma-agent` at one eventlet worker specifically because session state is not shared.
- Within one eventlet worker, two concurrent messages for the same session can still race on the same mutable `SessionState` object.
- Booking/lead/handoff CRM writes have idempotency/duplicate checks, so duplicate high-impact records are mitigated.
- Workflow state regression/lost in-memory fields remain possible under concurrent same-session delivery or process restart.

## Timeout And Latency

- Webhook/app gunicorn timeout: 60s in PM2.
- Gemini provider timeout: 20s per attempt, 1 retry by default.
- CRM API client timeout: 15s.
- Meta outbound send timeout: 6s in the gateway/client path.
- Database operations use ORM/sqlite calls without explicit per-query timeout in the audited path.

Risk: a slow dependency can consume most of the 60s worker request window. High-impact writes are idempotent, but session-memory response state can be lost if the request is killed after a write.

## Configuration

Production requires:

```text
APP_ENV=production
AI_AGENT_MODE=tool_calling
```

Also required by validation or deployment:

- strong `APP_SECRET_KEY`
- `AI_PROVIDER=gemini` with `GEMINI_API_KEY` and `GEMINI_MODEL`
- `AGENT_WRITE_TOOL_ENFORCEMENT=true`
- `DATABASE_URL`
- `CRM_ACCESS_MODE=api`
- `CRM_API_BASE_URL`
- `CRM_API_TOKEN`
- `DEMO_RESET_ON_START=false`
- `APP_DEBUG=false`
- `APP_USE_RELOADER=false`
- `META_PAGE_ID` when Meta credentials are configured
- single `rahma-agent` worker unless/until sessions move to shared storage

Implicit/default values to make explicit in production: `AI_MAX_TOOL_ROUNDS`, `WEBHOOK_RATE_LIMIT`, `GEMINI_MODEL`, `META_GRAPH_API_VERSION`, `DEFAULT_COUNTRY_CODE`, gunicorn timeout, and CRM API timeout.

## Transcript Matrix

Covered by existing golden/regression suites plus Phase 8 tests:

- A normal English booking: existing golden and conversation reliability suites.
- B normal Arabic booking: existing golden and conversation reliability suites.
- C mixed Arabic/English: new Phase 8 mixed-language bounded transition test.
- D side question: new Phase 8 side-question bounded transition test.
- E trip correction: existing Phase 3A/3B classifier and trip-switch tests.
- F trip-type correction: existing restart/switch tests.
- G invalid then valid answer: existing field-validation tests and new invalid-capture test.
- H multiple conversational interruptions: existing conversation reliability tests.
- I classifier failure: new Phase 8 malformed classifier test.
- J provider failure: new Phase 8 provider failure test.
- K rejected write tool: new Phase 8 allowed-tools and deterministic-write-tool tests.
- L booking continuation after interruption: existing conversation reliability tests.
- M duplicate webhook/message: new Phase 8 duplicate webhook test plus existing Phase 11 test.
- N process/session recovery: audited as not supported by durable storage.

## Production Blockers

1. Durable session recovery is not implemented. A process restart loses active booking workflow state.
2. Same-session concurrent message handling can race because `SessionState` is a mutable in-process object with no per-session lock.
3. Concurrent duplicate webhook delivery has no schema-level unique constraint; sequential duplicate delivery is handled.
4. Full `pytest tests/` did not complete within 20 minutes in this audit environment, so a clean full-suite result was not established.

## Non-Blocking Technical Debt

- Eventlet deprecation warning appears in relevant suites.
- Several production-critical timeout values are implicit defaults rather than explicit production env settings.
- The current observed baseline includes `test_passport_country_mismatch_with_stated_nationality_does_not_block` expecting confirmation while current workflow asks for currency first. This was not changed in Phase 8.

## Files Changed

Production files changed: none.

Tests added:

- `tests/test_phase8_production_readiness_audit.py`

Audit report added:

- `PHASE_8_PRODUCTION_READINESS_AUDIT.md`

## Verification

- New Phase 8 tests: `12 passed`.
- `test_golden_transcript_regressions.py`: `190 passed`.
- Phase 3A-7 target suites: `132 passed, 1 failed, 24 subtests passed`; failure was `tests/test_tier2_field_validation.py::test_passport_country_mismatch_with_stated_nationality_does_not_block`.
- Broader relevant sweep: `200 passed`.
- Full `pytest tests/`: timed out after 15 minutes before changes and after 20 minutes after changes.
- Collection: `978 tests collected`.
- Warnings: Eventlet deprecation warning in suites importing `engineio.async_drivers.eventlet`.

## Recommendation

NO-GO for claiming the AI booking system is fully production-ready.

Conditional limited operation is acceptable only under the current single-agent-worker deployment, with the known limitation that active chat sessions are not restart-recoverable and concurrent same-session delivery is not serialized.

Stop after Phase 8. Do not begin Phase 9.
