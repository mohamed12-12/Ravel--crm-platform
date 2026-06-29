# Instagram Production Integration Plan

## Goal

Connect the Rahma Traveler AI sales agent to a real Instagram Professional account in a production-safe way.

## Current Repo Status

The project already includes:

- Meta webhook signature validation in `services/instagram/webhooks.py`
- Webhook verification and receive endpoints in `services/ai_agent/ai_agent_app/server.py`
- Meta configuration fields in `services/ai_agent/ai_agent_app/config.py`

The current webhook receiver only validates and logs events. It does not yet:

- parse real Instagram messaging payloads
- route them into the AI conversation flow
- send replies back through Meta Graph API
- store webhook idempotency keys
- persist Instagram scoped user identity

## Production Architecture

```text
Meta Instagram Webhook
    -> services.instagram.payload_parser
    -> services.instagram.channel_bridge
    -> SessionFlowManager
    -> AI decision / CRM write path
    -> services.instagram.meta_client
    -> Meta Graph API reply
```

## Phase 1: Meta Business and App Setup

1. Convert the Instagram account to a Professional account.
2. Connect the Instagram account to the correct Facebook Page.
3. Create or reuse a Meta App in the Meta developer console.
4. Add the Instagram messaging / Messenger API for Instagram product.
5. Configure:
   - `META_APP_SECRET`
   - `META_VERIFY_TOKEN`
   - `META_PAGE_ACCESS_TOKEN`
   - `META_GRAPH_API_VERSION`
   - `PUBLIC_WEBHOOK_URL`
6. Expose the webhook over HTTPS on a stable public domain.

## Phase 2: Inbound Webhook Processing

1. Keep `GET /webhook` for verification.
2. Upgrade `POST /webhook` from log-only to real event intake.
3. Parse Meta payloads into normalized internal events.
4. Ignore unsupported events safely.
5. Add idempotency storage using Meta message IDs.
6. Return `200 OK` quickly and move heavy work off the request path.

## Phase 3: Channel Bridge

1. Introduce a dedicated Instagram channel bridge layer.
2. Convert parsed webhook events into:
   - customer identity context
   - inbound text / attachment payload
   - session lookup keys
3. Reuse `SessionFlowManager` instead of building a second AI flow.
4. Add mapping from Instagram sender ID to traveler/lead identity.
5. Link a traveler record once the customer shares phone data.

## Phase 4: Outbound Messaging

1. Build a Meta Graph API client for outbound replies.
2. Start with outbound text support.
3. Add outbound media / attachment acknowledgment later if needed.
4. Centralize retries, error handling, and Meta error logging.
5. Track outbound message IDs for audit and diagnostics.

## Phase 5: Data Persistence

1. Persist inbound Instagram messages in the interaction trail.
2. Store channel values as `Instagram`.
3. Add scoped Instagram sender ID support in conversation/session persistence.
4. Add webhook event dedupe storage.
5. Add delivery / failure logging for operational follow-up.

## Phase 6: Security and Reliability

1. Enforce `X-Hub-Signature-256` validation on every webhook call.
2. Add replay protection and duplicate event suppression.
3. Add structured audit logs for accepted and rejected webhooks.
4. Add rate limiting and alerting for repeated failures.
5. Move tokens and app secret to deployment secrets management.

## Phase 7: Meta App Review and Launch

1. Prepare App Review using the exact permissions required by the current Meta dashboard.
2. Record a demo flow showing:
   - incoming Instagram DM
   - webhook receipt
   - AI reply
   - CRM write
   - handoff creation when needed
3. Verify subscription fields and page connection in production.
4. Run staged rollout with monitoring before full launch.

## Implementation Tasks For This Repo

### Backend

1. Implement `services/instagram/payload_parser.py`
2. Implement `services/instagram/channel_bridge.py`
3. Implement `services/instagram/meta_client.py`
4. Update `services/ai_agent/ai_agent_app/server.py` webhook POST route
5. Add outbound send integration after AI response is generated

### Persistence

1. Add Instagram sender ID storage strategy
2. Add webhook idempotency table or equivalent durable store
3. Extend interaction persistence for Instagram metadata

### Testing

1. Signature validation tests using Meta sample payloads
2. Webhook parsing tests
3. Duplicate webhook idempotency tests
4. End-to-end inbound DM to AI reply tests
5. Handoff path tests from Instagram channel

## Deployment Checklist

- [ ] Instagram Professional account connected to Facebook Page
- [ ] Meta App configured
- [ ] Webhook publicly reachable over HTTPS
- [ ] Verify token configured
- [ ] App secret configured
- [ ] Page access token configured
- [ ] Inbound parser implemented
- [ ] Outbound sender implemented
- [ ] Idempotency implemented
- [ ] Logging and alerts enabled
- [ ] App Review approved

## Recommended Order

1. Build inbound parser and bridge
2. Add outbound sender
3. Add idempotency and persistence
4. Test against Meta sandbox / connected test account
5. Submit for App Review
6. Roll out to production
