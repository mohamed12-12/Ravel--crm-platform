# Phase 5 - Voice messages, transcription, and agent reply

## What this phase delivers

Customers can send voice messages in Arabic, English, or Franco-Arabic. The system transcribes the audio, feeds the transcript through the same agent conversation path as typed text, and replies through the normal agent pipeline.

## Current state (investigated, not assumed)

- The normal message endpoint routes text into the active session runtime: tool-calling, Gemini, or deterministic (`services/ai_agent/ai_agent_app/server.py:2020-2038`).
- The Instagram webhook parser preserves attachments with `attachment_type`, URL, and payload (`services/instagram/payload_parser.py:7-11`, `services/instagram/payload_parser.py:40-58`).
- The current Instagram reply engine treats any attachment without text as a generic attachment/passport prompt and does not transcribe audio (`services/instagram/reply_engine.py:20-39`).
- The AI server imports Instagram parser/webhook helpers (`services/ai_agent/ai_agent_app/server.py:38-40`).
- Gemini provider currently sends JSON `generateContent` requests with text/tool payloads; no audio-part or transcription helper was found in the inspected provider lines (`services/ai_agent/llm/gemini_provider.py:82-93`).
- The agent prompt already supports Arabic/English conversation and instructs one-question-at-a-time workflow behavior (`services/ai_agent/ai_agent_app/prompts/gemini_agent_system.md:56`, `services/ai_agent/ai_agent_app/prompts/gemini_agent_system.md:132`).

## Confirmed requirements this phase must satisfy

From the provided task attachment: investigate Gemini audio input first, ensure transcribed messages re-enter the exact same conversation pipeline as typed messages, add Instagram audio support, and handle Franco transcription limitations with a confidence/fallback behavior rather than pretending accuracy is solved.

## Working assumptions (for Phases 6 and 7 specifically)

Not applicable.

## Design approach

- Verify the current Gemini SDK/API capability before choosing a transcription strategy.
- Add an inbound audio normalization step that fetches/stores the audio safely, transcribes it, and produces a transcript plus confidence/quality metadata.
- Submit only the transcript text into the existing message handler path so workflow policy, RBAC, validation, and write guards remain unchanged.
- For Instagram, detect audio attachments by attachment type/MIME/content metadata and route them through transcription before reply generation.
- If confidence is low or transcription fails, ask the traveler to confirm/retype instead of continuing with uncertain content.
- Store transcript metadata in logs/audit only as needed and avoid storing raw audio unless a retention policy is explicitly approved.

## Dependencies on other phases

No hard dependency, but safest after Phase 1 because payment collection language must be stable before new voice inputs are introduced.

## Risks specific to this phase

The main risk is accidentally creating a second, less-protected message path. The current text path has explicit runtime routing and error handling (`services/ai_agent/ai_agent_app/server.py:2020-2055`); voice must converge into that path after transcription.

## Tests

Not run. No code was changed in this documentation-only pass.

## Live verification

Not run. No deployed app behavior was changed or verified in this documentation-only pass.

