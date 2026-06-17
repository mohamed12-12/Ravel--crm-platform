# Instagram / Meta Integration Notes

The current MVP has placeholder webhook routes and signature verification helpers. It does not yet process real Instagram messages.

## Current State

- `services/ai_agent/ai_agent_app/server.py` exposes `GET /webhook` and `POST /webhook`.
- `services/instagram/webhooks.py` contains Meta verify-token and `X-Hub-Signature-256` validation helpers.
- Webhook POST currently logs the event and returns success.
- Agent logic is currently driven by the web demo session flow, not by real Instagram conversations.

## Production Work Required

- TODO: implement real Instagram webhook verification for the configured Meta app and page.
- TODO: store and rotate Meta page access tokens through a secret manager.
- TODO: validate `X-Hub-Signature-256` against the raw request body before parsing JSON.
- TODO: add replay protection and message idempotency by Meta event ID.
- TODO: add rate limiting per sender, page, and route.
- TODO: enqueue inbound events for durable retry instead of doing work inside the webhook request.
- TODO: add outbound send retry queues with dead-letter handling.
- TODO: persist inbound and outbound messages in an audit log.
- TODO: connect ambiguous, blocked, duplicate, or high-risk conversations to the human handoff workflow.
- TODO: monitor webhook failures, queue latency, outbound send errors, and signature validation failures.

## Suggested Event Pipeline

1. Meta sends webhook event.
2. Flask validates token/signature and request shape.
3. Event is stored with an idempotency key.
4. A worker normalizes sender identity and channel metadata.
5. Deterministic workflow logic decides whether to respond, ask a follow-up, or hand off.
6. Outbound message is sent through Meta Graph API using the page access token.
7. Final state is written to the CRM database and audit logs.
