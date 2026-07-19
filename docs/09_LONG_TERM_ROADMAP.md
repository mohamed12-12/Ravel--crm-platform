# 09 Long Term Roadmap

## Executive Summary

The long-term roadmap is to transform the current demo workflow into a production sales-agent platform: tool-using, memory-aware, auditable, safe, multi-channel, and deeply integrated with the CRM. This should happen by evolving current services, not replacing them.

## Findings

The strongest foundation pieces are:

- Operational CRM models and routes.
- `UnifiedCRMService` as the CRM service layer.
- Gemini provider and function-calling loop.
- Validation and controlled write executor.
- Regression test suite.

The missing long-term pieces are:

- Durable agent memory.
- Persisted decision audit.
- Centralized context sanitizer.
- Provider-neutral model abstraction.
- Unified channel orchestration.
- Agent evaluation suite.
- Human-agent collaboration UI.

## Evidence

- `services/ai_agent/llm/llm_factory.py` is prepared for provider construction but only returns `GeminiProvider`.
- `Settings` contains OpenAI fields, but no OpenAI provider implementation exists.
- `services/instagram/reply_engine.py` is separate and deterministic rather than using the AI agent orchestrator.
- `apps/api/app/models/handoff.py` and handoff routes exist, which can support human-in-the-loop escalation.
- Tests cover specific tool and flow behaviors, but there is no conversation evaluation corpus or agent benchmark.

## Root Causes

- The codebase evolved from CRM and demo automation toward AI, not from an agent platform downward.
- Production-grade agent capabilities require explicit data models and operational processes that are not yet present.

## Impact

Without a long-term roadmap, the codebase will keep accumulating parallel deterministic and AI paths. That increases maintenance cost and makes user behavior inconsistent across web demo, Instagram, and future WhatsApp integrations.

## Recommended Solution

### Milestone 1: Production orchestrator

- Single turn contract.
- Central model/tool/memory/policy/audit ownership.
- Deterministic fallback kept behind the same interface.

### Milestone 2: Durable memory

- Store session summaries.
- Store traveler preferences and unresolved intents.
- Link memory to traveler, lead, booking, and channel identities.
- Add retention/deletion controls.

### Milestone 3: Decision audit and evaluation

- Persist prompt ids, response ids, tool calls, validations, writes, fallbacks, and latency.
- Add agent replay tests.
- Add golden conversation datasets for Arabic and English.

### Milestone 4: Context safety

- Sanitize CRM notes, sales notes, uploaded document metadata, and customer text.
- Add prompt-injection tests.
- Add context size limits and evidence citations inside tool outputs.

### Milestone 5: Multi-provider model layer

- Define provider interface.
- Keep Gemini as first provider.
- Add a second provider only after tests prove portability.

### Milestone 6: Channel unification

- Route web demo, Instagram, and WhatsApp through the same orchestrator.
- Keep channel-specific adapters for payload parsing, media handling, and reply delivery.
- Add retry queues and idempotency.

### Milestone 7: Human collaboration

- Improve handoff queue with conversation summaries, tool traces, next best action, and linked CRM records.
- Let humans resolve identity conflicts and resume automation safely.

## Priority

Medium to high. Orchestrator, memory, audit, and safety should happen before broad customer exposure.

## Estimated Complexity

High across the full roadmap, but each milestone can be delivered incrementally.

## Dependencies

- Production database migrations.
- Channel API decisions for WhatsApp/Instagram.
- Privacy and retention policy.
- Test/evaluation data.
- Stable CRM service contracts.

## Risks

- Multi-provider work before orchestration cleanup could add abstraction without benefit.
- Memory without retention policy could create compliance risk.
- Unified channels need strong idempotency to avoid duplicate interactions or messages.

