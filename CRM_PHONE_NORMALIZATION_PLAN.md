# CRM Phone Normalization Plan

Date: 2026-06-16
Scope: implementation plan only. No code changes in this document.

## Current Behavior

The demo agent asks for a WhatsApp number at `awaiting_phone` and stores the raw typed text in `SessionState.raw_phone`.

Observed current flow:

- `services/ai_agent/ai_agent_app/agent/session_flow.py` passes `session.raw_phone` and `session.country_code` to `gateway.preview_customer()`.
- `SessionState.country_code` starts as an empty string.
- `services/crm/system_services/unified_service.py::normalize_phone()` has a default `country_code="20"`.
- If the country code is empty or missing, several call sites still fall back to Egypt/`20`.
- The demo intake form in `services/ai_agent/ai_agent_app/web/templates/index.html` hardcodes `Nationality` to `Egyptian` and `Country Code` to `20`.
- The frontend prefill in `services/ai_agent/ai_agent_app/web/static/app.js` only copies `session.rawPhone` into the phone input. It does not prefill detected country code or safe nationality.
- Manual CRM lead and traveler routes also call `normalize_phone(..., "20")` in some places.

Root issue:

The system treats an unknown or non-Egyptian number as if it belongs to default country code `20`, causing CRM lookup misses and false "new customer" classification for numbers such as `+9665xxxxxxx`.

## Required Behavior

The system should preserve and understand the customer-entered WhatsApp number before CRM lookup.

Expected examples:

- `+201234567890` -> country code `20`, local number `1234567890`, normalized phone `+201234567890`
- `201234567890` -> country code `20`, normalized phone `+201234567890`
- `+9665xxxxxxx` -> country code `966`, normalized phone `+9665xxxxxxx`, no Egypt nationality default
- `01012345678` -> infer Egypt/`20` only when `DEFAULT_COUNTRY_CODE=20` is configured and local-number rules match safely
- unknown country code or ambiguous input -> ask the user to confirm country code instead of forcing `20`

CRM lookup must use safe variants:

- exact normalized phone with plus
- normalized phone without plus
- `country_code:local_number`
- legacy lookup keys already present in old sheets/databases
- local number only when it is safe and not ambiguous

Traveler ID preservation must remain unchanged. Matching a phone to an existing traveler must continue to reuse the existing Traveler ID, and uncertain matches must still trigger review/handoff instead of creating or overwriting the wrong profile.

## Affected Files And Modules

- `services/ai_agent/ai_agent_app/agent/session_flow.py`
  - Phone intake stage
  - New country-code confirmation stage
  - Session state fields for detected phone metadata
  - Intake handoff into `gateway.preview_customer()`

- `services/ai_agent/ai_agent_app/server.py`
  - Session serialization fields exposed to frontend
  - Agent config exposure if default country code metadata is needed

- `services/ai_agent/ai_agent_app/web/static/app.js`
  - Intake form prefill for phone, country code, and nationality
  - Country code confirmation UX
  - Prevent overwriting non-Egyptian nationality with Egyptian default

- `services/ai_agent/ai_agent_app/web/templates/index.html`
  - Remove hardcoded `Egyptian` and `20` as unconditional field values
  - Keep defaults only when confidence is high

- `services/crm/system_services/unified_service.py`
  - Shared phone parser/normalizer
  - Lookup-key variant generation
  - Identity resolution query parameters
  - Traveler creation/update phone fields

- `services/ai_agent/ai_agent_app/system_bridge.py`
  - Ensure the detected country code is passed from agent to CRM service

- `services/ai_agent/ai_agent_app/sheets/excel_gateway.py`
  - Preview/write-through calls that forward `raw_phone` and `country_code`

- `services/ai_agent/ai_agent_app/sheets/google_sheets_api_gateway.py`
  - Same contract if Google-backed demo is enabled later

- `apps/api/app/routes/leads.py`
  - Manual lead create/update currently assumes `20`

- `apps/api/app/routes/travelers.py`
  - Manual traveler create/update defaults to `20`

- Tests:
  - `tests/test_phase0_cleanup.py`
  - `tests/test_phase1_readonly_agent.py`
  - `tests/test_phase2_controlled_agent.py`
  - `tests/test_phase3_demo_web.py`
  - `tests/test_phase4_traveler_management.py`
  - `tests/test_phase5_manual_lead_agent_logic.py`
  - `tests/test_phase8_demo_alignment.py`
  - `tests/test_phase10_full_logic_lock.py`
  - `tests/test_phase11_demo_features.py`

## Data Flow From Agent Phone Input To CRM Lookup

Current:

1. User types phone in chat.
2. `SessionFlowManager.handle_message()` stores text in `session.raw_phone`.
3. Agent calls `gateway.preview_customer(raw_phone=session.raw_phone, country_code=session.country_code)`.
4. Gateway calls CRM/system bridge.
5. `UnifiedCRMService.resolve_identity()` calls `normalize_phone(raw_phone, country_code)`.
6. Empty/missing country code falls into default `20`.
7. CRM lookup runs against normalized phone, local phone, and lookup key variants.
8. If not matched, session moves to intake form.
9. Intake form currently displays `Egyptian` and `20` by default.

Proposed:

1. User types phone in chat.
2. Agent parses phone into a structured `PhoneNormalizationResult`.
3. If country code is confidently detected, store it in session and use it immediately for CRM preview.
4. If the number is local and `DEFAULT_COUNTRY_CODE` rules are configured, infer the default country only for safe local patterns.
5. If country code is ambiguous, set session to `awaiting_country_code` and ask for confirmation.
6. After confirmation, normalize again and run CRM preview.
7. Intake form receives raw input, normalized phone, detected country code, and optional nationality hint.
8. CRM lookup uses all safe current and legacy variants.
9. Existing traveler matches preserve Traveler ID; ambiguous matches remain human review.

## Proposed Phone Normalization Strategy

Create or extend a shared normalizer in `UnifiedCRMService` so both agent and CRM UI paths use one implementation.

Recommended return shape:

```python
{
    "raw_input": "+966512345678",
    "country_code": "966",
    "local_number": "512345678",
    "normalized_whatsapp": "+966512345678",
    "digits": "966512345678",
    "lookup_key": "966:512345678",
    "legacy_lookup_keys": ["966512345678", "512345678"],
    "confidence": "high",
    "needs_country_code_confirmation": False,
    "country_hint": "Saudi Arabia",
    "nationality_hint": "",
}
```

Keep `normalize_phone()` backward compatible by either:

- extending it with optional arguments and keeping existing keys, or
- adding `parse_phone()` / `normalize_phone_with_detection()` and making old `normalize_phone()` delegate to it.

Recommended approach:

- Add a new structured parser first.
- Keep existing `normalize_phone(raw_phone, country_code="20")` behavior only where tests explicitly require old Egyptian defaults.
- Migrate agent and CRM lookup paths to the safer parser after tests are added.

## Country Code Detection Rules

Rules should be deterministic and conservative.

1. Strip spaces, hyphens, parentheses, and WhatsApp suffixes like `@c.us`.
2. Preserve a leading `+` if present.
3. Convert `00` international prefix to `+`.
4. If input starts with `+`, detect country code using a known country-code table.
5. If input starts without `+` but begins with a known country code and has plausible length, detect that country code.
6. If input starts with a local trunk prefix such as `0`, infer the configured default country only if:
   - `DEFAULT_COUNTRY_CODE` is set,
   - the local format matches known default-country rules,
   - the input does not also look like a different international number.
7. If input is digits only and cannot be safely split into country code/local number, ask for country code confirmation.
8. Do not assume Egypt/`20` for unknown or ambiguous international-looking input.

Initial country-code table:

- Include only common markets needed for demo and tests: Egypt `20`, Saudi Arabia `966`, UAE `971`, Kuwait `965`, Qatar `974`, Bahrain `973`, Oman `968`, Jordan `962`, Lebanon `961`, United Kingdom `44`, United States/Canada `1`.
- Put the table in code or config as a small explicit mapping, not as a live web dependency.
- Longer country codes must be checked before shorter ones.

## Nationality Inference Rules

Nationality is not the same as phone country. Treat it as a weak hint.

Rules:

- For `+20` or safe Egyptian local format, country hint can be Egypt and nationality hint can be `Egyptian` only if the demo/product owner approves this behavior.
- For `+966`, country hint can be Saudi Arabia, but nationality should not be auto-filled as Saudi unless the business explicitly wants phone-country-to-nationality inference.
- For non-Egypt country codes, leave nationality blank or `Unknown` and editable by the user.
- For unknown country codes, leave nationality blank and ask for country code confirmation.
- Never overwrite a nationality already provided by an existing traveler record.

Recommended demo behavior:

- `countryCode` prefill: detected code when confidence is high.
- `nationality` prefill: only `Egyptian` for safe Egyptian local or `+20` input; otherwise blank.
- Intake form should keep nationality editable and required only if product wants it mandatory.

## Backward Compatibility With Old CRM Records

Existing records may store phones in several formats:

- `integrated_whatsapp = +201012345678`
- `normalized_whatsapp = +201012345678`
- `phone_lookup_key = 20:1012345678`
- legacy `phone_lookup_key = 201012345678`
- legacy `phone_lookup_key = 1012345678`
- `whatsapp_raw = 01012345678`
- `whatsapp_raw = 1012345678`
- `whatsapp_raw = 201012345678`

Lookup should generate variants and query:

- normalized with plus
- normalized without plus
- country-code-prefixed lookup key
- digits-only full international number
- local number without trunk prefix when safe
- original raw input for exact legacy rows

Important guard:

Local-number-only matches should be constrained. If a local number appears under multiple country codes or multiple traveler records, return `multiple_matches` / handoff rather than selecting one.

## Risks

- False positive matching if local number-only lookup is too broad.
- Breaking existing Egyptian-number behavior if old `20` defaults are removed too aggressively.
- UI confusion if country code is detected but nationality remains blank.
- Existing tests may encode old assumptions that every new customer is Egyptian.
- Country code detection without a full libphonenumber dependency may miss edge cases.
- Introducing a dependency such as `phonenumbers` could improve correctness but changes install/package surface.

## Edge Cases

- `+201234567890`
- `201234567890`
- `00201234567890`
- `01012345678`
- `1012345678`
- `+966512345678`
- `966512345678`
- `00966512345678`
- phone with spaces: `+966 5 123 45678`
- phone with punctuation: `(+20) 101-234-5678`
- WhatsApp JID: `201012345678@c.us`
- too short input: `123`
- unknown country code: `+999123456789`
- ambiguous digits without plus: `441234567890`
- same local number under Egypt and Saudi records
- existing traveler with legacy `phone_lookup_key` only
- blacklisted traveler with non-Egypt country code
- duplicate phone across multiple traveler records

## Test Plan

Add focused unit tests before behavior changes.

Phone parser tests:

- `+201234567890` detects `20`, local `1234567890`, normalized `+201234567890`.
- `201234567890` detects `20`, normalized `+201234567890`.
- `00201234567890` detects `20`.
- `01012345678` infers `20` only when default country is configured.
- `+966512345678` detects `966`, normalized `+966512345678`, no Egyptian nationality default.
- unknown/ambiguous input sets `needs_country_code_confirmation=True`.

CRM identity tests:

- Existing Egyptian traveler still matches with `010...`, `20...`, and `+20...`.
- Existing Saudi traveler matches with `+966...` and `966...`.
- Legacy lookup keys still match existing Traveler ID.
- Duplicate local number across country codes returns handoff/multiple match.
- Blacklisted non-Egypt traveler still blocks safely.
- New non-Egypt number creates new lead/traveler with detected country code, not `20`.

Agent/session tests:

- Phone intake stores detected country code in session.
- Ambiguous phone moves to `awaiting_country_code`.
- After country code confirmation, CRM preview runs with confirmed code.
- Intake form prefill uses detected country code.
- Intake form does not default nationality to Egyptian for `+966`.
- Existing Traveler ID preservation still passes full logic lock tests.

Regression tests:

- `python -m pytest tests -x -q`
- `python -m pytest tests`
- `npm test`
- `npm run build`
- `npm run typecheck`
- `python -m compileall apps services scripts tests`

## Step-By-Step Implementation Tasks

1. Add a shared phone parser contract.
   - Define `PhoneNormalizationResult` as a dataclass or dict contract in `services/crm/system_services/unified_service.py` or a small shared helper.
   - Include raw input, country code, local number, normalized phone, lookup variants, confidence, and confirmation flag.

2. Add conservative country code detection.
   - Support `+`, `00`, and no-plus international formats.
   - Check longer country codes before shorter country codes.
   - Keep `DEFAULT_COUNTRY_CODE` only for safe local formats.

3. Update `UnifiedCRMService.normalize_phone()`.
   - Preserve current return keys.
   - Internally use the new parser where possible.
   - Avoid defaulting to `20` when the raw input clearly contains another country code.

4. Expand `lookup_key_variants()`.
   - Include normalized without plus, full international digits, legacy `country:local`, and safe local variants.
   - Avoid unsafe broad local matching when country code is unknown.

5. Update `resolve_identity()`.
   - Use the normalized result and variants.
   - Add exact raw input comparison for legacy storage.
   - Return multiple match/handoff for ambiguous cross-country local matches.

6. Update agent phone intake.
   - Parse phone before preview.
   - Store `session.country_code`, `session.raw_phone`, and normalized metadata.
   - Add `awaiting_country_code` stage for ambiguous inputs.
   - After country-code confirmation, rerun preview.

7. Update session serialization.
   - Expose detected `countryCode`, raw phone, normalized phone, country hint, nationality hint, and confirmation-needed flag.

8. Update frontend intake prefill.
   - Fill WhatsApp from normalized or raw session phone.
   - Fill country code only when detected or confirmed.
   - Remove unconditional `Egyptian` and `20` defaults from the HTML.
   - Prefill nationality only when confidently known; otherwise leave editable and blank/unknown.

9. Update manual CRM routes.
   - Replace hardcoded `"20"` calls in lead/traveler create/update with parser-detected country code.
   - Keep current Egyptian behavior for local Egyptian input.

10. Add tests.
   - Start with parser tests.
   - Add CRM identity tests for non-Egypt numbers and legacy records.
   - Add agent/session tests for confirmation and prefill.
   - Re-run full regression stack.

11. Update documentation.
   - Add phone normalization behavior to demo validation checklist and release notes.
   - Document assumptions around nationality inference.

## Assumptions

- `DEFAULT_COUNTRY_CODE=20` remains valid for local Egyptian demo numbers.
- The business wants phone country as a contact-routing hint, not as definitive nationality.
- No live external phone-validation API should be added in this phase.
- No Instagram/Meta integration is part of this change.
- Existing Traveler ID preservation is more important than aggressive automatic matching.
