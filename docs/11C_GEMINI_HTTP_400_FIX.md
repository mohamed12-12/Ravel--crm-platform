# Gemini HTTP 400 Fix

## Exact Google Error

Observed validation message:

- `Invalid JSON payload received. Unknown name "text" at 'contents[0]': Cannot find field.`
- In the same failure class, Google also reported the same issue for `contents[1]` when conversation history was included.

## Root Cause

The Gemini client was sending `contents` as legacy message dictionaries containing a top-level `text` field.

That shape is not valid for the Gemini `generateContent` REST payload. Gemini expects each turn to be a `Content` object with a `parts` array, for example:

```json
{ "role": "user", "parts": [{ "text": "hello" }] }
```

The old request shape looked like:

```json
{ "role": "user", "text": "hello" }
```

That invalid field is what caused the HTTP 400.

## Invalid Request Field

- `contents[0].text`
- `contents[1].text` when history was present

## Payload Before

Before the fix, the provider forwarded messages directly into `contents`, which could include:

- `role`
- `text`
- occasional `parts`

Some turns were not normalized to Gemini `parts`.

## Payload After

The provider now normalizes every turn before sending:

- `system_instruction.parts[0].text`
- `contents[].role` set to `user` or `model`
- `contents[].parts[]` with only Gemini-compatible part shapes
- function responses represented with `functionResponse` parts
- empty parts are removed

## Tool Schema Issue

No tool-schema incompatibility was identified in this fix. The registered read-only tool schemas already use a Gemini-compatible object/property shape.

## Files Changed

- `services/ai_agent/llm/gemini_provider.py`
- `tests/test_phase1_gemini_readonly_agent.py`

## Tests Added

- Payload normalization test for Gemini request contents
- Safe parsing test for HTTP 400 response bodies
- Tool schema compatibility test

## Test Commands

```bash
python -m pytest tests/test_phase1_gemini_readonly_agent.py tests/test_phase2_gemini_tool_loop.py tests/test_phase1_agent_runtime.py -q
python -m pytest tests/test_phase3_gemini_live_session.py tests/test_phase11_demo_features.py -q
```

## Test Results

- `28 passed`
- `59 passed`

## Live Smoke Test

Not run in this workspace because no Gemini API key is configured in the environment.

## Known Limitations

- Live Gemini verification still depends on a configured `GEMINI_API_KEY`.
- This fix addresses the request payload shape and safe 400 diagnostics, not Phase 2 write tools.
