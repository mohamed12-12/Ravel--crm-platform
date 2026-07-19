# Agent Conversation Reliability Root Cause

Date: 2026-07-18

## Scope

This note documents the root cause before implementation changes for the Rahma Traveler tool-calling sales agent reliability pass.

## Affected files

- `services/ai_agent/ai_agent_app/agent/tool_calling_runtime.py`
- `services/ai_agent/ai_agent_app/agent/read_only_tools.py`
- `services/ai_agent/ai_agent_app/agent/workflow_policy.py`
- `services/ai_agent/ai_agent_app/server.py`
- `services/ai_agent/ai_agent_app/agent/session_flow.py`
- Scenario tests under `tests/`

## Root cause

1. Missing `_normalize_trip_reference`

   `ToolCallingSessionRuntime._apply_trip_selection_from_text()` calls `self._normalize_trip_reference(...)`, but the class currently defines `_normalize_public_trip_reference(...)` only. This is a refactor regression: the normalization behavior was moved/renamed for public trip references, while the existing selected-trip matching code was left calling the older private method name. The method was not moved to another class and is not defined in a base class.

2. Trip-reference recognition is too narrow

   Direct trip matching is split across `_apply_trip_selection_from_text()`, `_run_trip_reference_lookup_if_ready()`, and `_resolve_public_trip_reference()`. The matching relies on simple substring checks against `trip_name` and `trip_id`, plus repeated `search_trips()` calls. `ReadOnlyCRMTools.search_trips()` itself only performs casefolded substring matching. It does not normalize punctuation, Arabic variants, hyphen spacing, common abbreviations such as `int.`, or score ambiguity. As a result, direct references like `Demo trip`, partial names, Arabic/English mixed text, and IDs with punctuation can fail or become ambiguous.

3. State repetition

   The workflow policy correctly uses `collection_state`, but the runtime does not consistently mark direct-trip reference resolution as confirmed, does not preserve enough selected-trip details after a direct reference, and can ask for trip type before resolving a known named trip. This makes the planner repeat questions or ask local/international unnecessarily.

4. Language inconsistency

   Language updates currently switch to Arabic on Arabic input, and switch back to English whenever the session is Arabic and the new message is longer than 10 characters. This is too coarse for mixed CRM terms, trip names, IDs, and short workflow replies. It causes unexpected language shifts.

5. Customer-visible technical errors

   `server.py` returns `{"error": str(e)}` from the session message route on unexpected failures. The frontend then displays that text. That leaks Python exceptions such as missing attributes and can expose raw backend details to customers.

6. Booking scenario drift

   The intended flow is encoded across identity policy, workflow policy, tool registry, and write validators, but direct trip reference resolution is outside one consistent matcher. When it misses a reference, the conversation falls back to generic trip-type collection or model behavior, which can drift from the sales and booking scenario.

7. Backend-owned prompts override natural conversation turns

   After the workflow policy enters required collection states such as `traveler_gender_required`, `ToolCallingSessionRuntime.handle_message()` returns the fixed backend prompt immediately for `_BACKEND_OWNED_COLLECTION_STEPS`. This preserves booking guardrails, but it also bypasses normal conversational handling for customer interruptions like "who are you", frustration, or "tell me more details for this trip". Because the same field remains missing, every off-track message receives the exact same prompt again. The root cause is not the model being weak; it is the runtime routing layer short-circuiting before the agent response layer can acknowledge the message and then guide the customer back to the required booking detail.

## Architectural direction

- Restore `_normalize_trip_reference` inside `ToolCallingSessionRuntime` as the runtime-level canonical matcher helper, backed by stronger normalization rather than a temporary alias.
- Centralize trip-reference matching in the tool-calling runtime so exact, partial, ID, normalized, Arabic/English, and abbreviation matching produce a scored result.
- Keep CRM as the source of truth. Matching must only choose from `ReadOnlyCRMTools.search_trips()`/CRM inventory results.
- Preserve workflow policy and write validators. No lead or booking write should happen before explicit confirmation.
- Convert unexpected runtime errors into safe customer messages while logging full technical details.
- Keep backend-owned required steps, but wrap them in runtime-level conversational recovery so interruptions are answered naturally without relaxing CRM or booking rules.
