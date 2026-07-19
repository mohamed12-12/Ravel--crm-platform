# AI Agent Audit

Reviewed: server, tool-calling runtime, workflow policy, Gemini provider, registries/executors, CRM bridge, sheets, session state and frontend payloads.

Positive evidence: tool-round cap, thought-signature guard, unsupported-tool validation, workflow order, CRM grounding, passport gate, memory and deterministic-mode coverage.

| ID | Severity | Status | Recommendation |
|---|---|---|---|
| AI-01 | High | Confirmed | Version a session contract using `runtime_mode`, `customer_status`, and `chat_enabled`; stop tests/UI from depending on legacy stage strings. |
| AI-02 | High | Confirmed | Explicitly set agent mode in each test fixture. |
| AI-03 | High | Likely | Add fact provenance and one source-of-truth rule across CRM/workbook/gateways. |
| AI-04 | Medium | Confirmed | Add opt-in secret-gated live Gemini contract tests. |
| AI-05 | Medium | Confirmed | Add passport access/retention authorization coverage. |
