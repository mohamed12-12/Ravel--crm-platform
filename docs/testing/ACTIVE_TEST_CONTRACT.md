# Active Test Contract

Legacy deterministic integration tests must set `AI_AGENT_MODE=deterministic` explicitly. Tool-calling tests must assert the runtime descriptor (`runtime_mode`, `customer_status`, `chat_enabled`) and must not infer mode from old stage names.

Gemini function-call fixtures must include `functionCall.name`, `functionCall.args`, a call ID when returned by the provider, and `thoughtSignature`. The missing-signature negative test must continue to fail safely.

Current remediation result: all 289 active tests pass; archived scratch files are not part of default collection.
