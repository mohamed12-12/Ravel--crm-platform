# Gemini Thought Signature Fix

## Reproduced Sequence

1. Gemini receives a user message.
2. Gemini returns a `functionCall` for `search_available_trips`.
3. The backend executes the CRM tool successfully.
4. Gemini rejects the next request if the original model part is reconstructed without its thought metadata.

## Exact Google Error

- `Function call is missing a thought_signature in functionCall parts. This is required for tools to work correctly.`

## Root Cause

The Gemini provider normalized outbound conversation history by rebuilding function-call parts from reduced internal data. That reconstruction dropped opaque Gemini metadata attached to the original model part.

## Exact Field That Was Dropped

- `thoughtSignature` on the model `functionCall` part
- `id` / call ID metadata when present

## Payload Before

The outgoing history was being rebuilt into simplified parts that kept only the tool name and args.

## Payload After

The provider now preserves the original Gemini part fields when normalizing outbound contents:

- `functionCall`
- `thoughtSignature`
- `id` / `callId`
- `functionResponse`

The tool loop also returns the function response using the same call ID when Gemini supplies one.

## Signature Lifecycle

- Captured from the raw Gemini response part
- Preserved in the stored model content
- Sent back unchanged in the next `generateContent` request
- Never exposed to the UI, logs, or CRM data

## Function-Call ID Lifecycle

- Preserved from the raw Gemini `functionCall` part
- Reused on the corresponding `functionResponse`
- Not regenerated

## Files Changed

- `services/ai_agent/llm/gemini_provider.py`
- `services/ai_agent/ai_agent_app/agent/gemini_agent.py`
- `tests/test_phase2_gemini_tool_loop.py`
- `docs/11E_GEMINI_THOUGHT_SIGNATURE_FIX.md`

## Tests Added

- Tool-call response preserves thought signature
- Tool-call response preserves function-call ID
- Missing signature fails safely
- Gemini content normalization keeps Gemini part metadata

## Test Commands

```bash
python -m pytest tests/test_phase2_gemini_tool_loop.py tests/test_phase1_gemini_readonly_agent.py tests/test_phase1_agent_runtime.py -q
python -m pytest tests/test_phase3_gemini_live_session.py tests/test_phase11_demo_features.py -q
```

## Test Results

- `30 passed`
- `59 passed`

## Live Smoke-Test Result

Not run in this workspace because no Gemini API key is configured.

## Known Limitations

- Live Gemini verification still depends on a configured API key.
- Phase 2 write tools were not added.
