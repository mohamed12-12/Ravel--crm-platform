# Phase 12 — Booking State Correctness: Group/Room Composition Audit

Date: 2026-08-13

## 1. Executive Summary

Production reported that "احنا جروب 2 بنات و2 رجال فعايزين غرفتين دابل بنات
وبرضو رجال" (we are a group of 2 girls and 2 men, so we want two double
rooms, girls and also men) collapsed to a single room requirement: "الغرفة:
المطلوب: 1 غرفة Double بنات" — the boys' room, and half the group, vanished.

Tracing the actual code (not the summary/payload/pricing layers, which
already correctly consume a multi-entry `room_requirements["requirements"]`
list) found the bug entirely in the opportunistic free-text extraction layer:

1. **"رجال" (men) was absent from every boys/male term list** in both
   `_extract_room_requirements` and `_extract_mixed_people_counts` — a
   message naming "بنات" and "رجال" always produced a girls-only result,
   regardless of anything else.
2. **A real, independent regex bug**: Python's `\b` treats an Arabic letter
   and a following digit as the same "word" character class, so `\b(2)`
   never matched inside "و2" ("and 2") at all — an extremely common,
   ordinary way to glue the Arabic connector to a number. "2 بنات و2 رجال"
   only ever saw the *first* "2"; the second was invisible to the regex
   regardless of the "رجال" gap.
3. Arabic number **words** ("اتنين"/"اثنين") were not part of these
   extractors' own number patterns at all (digits and English words only).
4. **`_merge_hints` replaced `session.room_requirements` wholesale every
   turn** — a second turn adding one more room for a different gender
   silently discarded whatever was recorded on an earlier turn. This is a
   distinct, real defect from #1–3, only visible across multiple turns
   (Task 4), not in the single-message reported transcript.

All four are fixed. No redesign: the existing `room_requirements =
{"requirements": [...], "boys_rooms_requested": N, "girls_rooms_requested":
N}` structure (already a list-of-dicts, already correctly consumed by the
summary, booking payload, and CRM write path) is reused unchanged — only the
extraction and merge steps that feed it were fixed.

**14 new tests added, all passing. Full suite: 1021 passed, 5 failed — all 5
confirmed pre-existing/order-dependent, none touching any file this phase
changed. Recommendation: GO.**

## 2. Exact Root Cause

Traced end to end with the actual reported message:

```text
"احنا جروب 2 بنات و2 رجال فعايزين غرفتين دابل بنات وبرضو رجال"
  -> _extract_hints(text, stage=...)
       -> _extract_mixed_people_counts(text)
            girls terms include "بنات" -> matched "2 بنات" -> girls=2
            boys terms did NOT include "رجال" at all -> boys=0
            (even after adding "رجال": "و2 رجال" still failed on its own --
             see the \b/glued-connector bug below)
       -> _extract_room_requirements(text, default_room_type="")
            room_word_present: "غرفتين" present -> True
            room_type: "double"/"triple"/"single" (ENGLISH only) checked --
              "دابل" (the actual word used) matched NONE of them -> room_type
              stayed "" (later defaulted to "Double" per-item anyway)
            primary per-count regex loop: English "rooms?" literal required
              adjacent to the number -- never matches Arabic phrasing at all,
              requirements stayed []
            fallback: "if not requirements and (has_boys or has_girls)"
              has_boys = "boys"/"شباب"/"اولاد"/"أولاد" in text -- "رجال" NOT
              checked -> has_boys = False
              has_girls = "بنات" in text -> True
              -> requirements = [{"room_type": "Double", "room_group": "girls", "rooms": 1}]
  -> _merge_hints: session.room_requirements = dict(room_requirements)  # wholesale replace
  -> _room_requirements_summary(session): iterates the (single-entry) list
       -> "المطلوب: 1 غرفة Double بنات"  <-- exactly the reported output
```

The summary function itself was never the problem — it already renders
every entry in the list; there was simply only ever one entry to render.

## 3. Current State Model (as found, unchanged in shape)

`SessionState` (`session_flow.py`) already has, and this phase keeps:

```python
room_type: str = ""                              # dominant/first-set type, display convenience
room_group: str = ""                              # "boys" | "girls" | "mixed" | ""
room_requirements: dict[str, Any] = field(default_factory=dict)
```

`room_requirements`'s shape (already existed, confirmed via
`test_mixed_room_request_is_stored_structurally`, a pre-existing test this
phase does not change):

```python
{
    "requirements": [
        {"room_type": "Double", "room_group": "boys", "rooms": 1},
        {"room_type": "Double", "room_group": "girls", "rooms": 1},
    ],
    "boys_rooms_requested": 1,
    "girls_rooms_requested": 1,
}
```

This is already the list-of-dicts structure Task 2 asked to verify exists —
confirmed, and no competing/duplicate representation was introduced.
`source: "derived_from_group_size"` is a third, pre-existing key set only by
`_ensure_room_requirements_for_group` (deriving a room count from group size
alone, when the customer never specified rooms explicitly) — untouched by
this phase; it already refuses to overwrite a customer-specified
`requirements` list (checked via that same key).

## 4. Room Data Model / Parsing Flow (traced)

```text
user message
  -> handle_message(): hints = _extract_hints(clean_text, stage=capture_stage)
       -> _extract_mixed_people_counts(text)   [people/gender counts]
       -> _extract_room_requirements(text, default_room_type)  [room list]
  -> _merge_hints(session, hints, clean_text)
       -> [NEW] _merge_room_requirements(session, candidate_room_requirements, clean_text)
            -> addition / replacement / global-correction (section 5)
       -> session.room_requirements = <merged result>
  -> session.room_group = "mixed" if both boys_rooms_requested and girls_rooms_requested
  -> workflow_policy.evaluate() / _room_requirements_summary() / booking payload
     all read session.room_requirements["requirements"] -- unchanged, already correct
```

Nothing in the classifier layer is involved in room/group parsing at all —
this is, and remains, a purely deterministic opportunistic-hint pipeline,
matching Task 2's "do not introduce duplicate competing representations."

## 5. Replacement vs. Addition Semantics (Task 4, implemented)

New `_merge_room_requirements(session, new_requirements, clean_text)`,
called from `_merge_hints`, replacing the previous wholesale overwrite:

1. **Global correction** — "الغرفتين شباب"/"كل الغرف بنات"/"both rooms..."
   (new `_global_room_gender_correction`): every *existing* requirement's
   gender is corrected to the one stated; nothing is added. ("لا، الغرفتين
   شباب" after 1 boys + 1 girls room → both become boys, consolidated.)
2. **Per-gender replacement** — a new requirement whose gender already has
   an existing entry is applied only if this turn carries an explicit
   correction signal (new `_is_room_correction_signal`: reuses
   `_is_explicit_correction_signal`'s existing marker set — "instead"/
   "بدل"/"خليها"/... — plus a bare leading "لا", which that shared check
   doesn't cover on its own but is how this specific correction is
   naturally phrased). Without a correction signal, an identical
   restatement is a no-op and a *conflicting* one is **ignored**, not
   silently applied — the same discipline `_merge_hints` already uses for a
   conflicting `trip_type` hint.
3. **Addition** — a new requirement whose gender has no existing entry is
   always appended. No signal is required to add a brand-new gender: there
   is no ambiguity to protect against (nothing existing is discarded either
   way).

All three funnel through a new `_consolidate_room_requirements`, which sums
`rooms` for identical `(room_type, room_group)` pairs and recomputes
`boys_rooms_requested`/`girls_rooms_requested` from the result — the single
place this can never drift out of sync with the list itself.

## 6. Booking Payload / Summary (verified correct, not changed)

- `_execute_booking_draft`'s payload already passes
  `"room_requirements": dict(requirements)` (the full structure) plus
  `boys_rooms_requested`/`girls_rooms_requested` separately — confirmed via
  `test_final_booking_payload_contains_both_rooms` and
  `test_production_transcript_reproduction`, asserting on the actual
  `runtime._write_executor.execute.call_args.kwargs["payload"]`, not
  displayed text.
- `_booking_confirmation_summary` already calls
  `_room_requirements_summary(session)`, which iterates the full list —
  confirmed it renders both rooms once the list actually has both, via
  `test_final_confirmation_summary_contains_both_rooms`.

No change was needed at either layer — both were already correct; Task 5's
payload-level assertions above lock that in as regression coverage that
didn't exist before.

## 7. Pricing Impact (Task 6)

Traced `workflow_policy.py::_pricing_breakdown_text`/`_pricing_counts_for_context`:
pricing here is per-**traveler-nationality** (Egyptian EGP vs. foreigner
USD), multiplied by a **single** `room_type` price lookup
(`price_for_room_and_currency`). It does **not** vary by room *gender* at
all — a Double-boys room and a Double-girls room cost the same per person.

For the reported case (both rooms are the same type, Double), this means
there is **no pricing defect**: 4 travelers × the Double-room per-person
rate, split by nationality, is correct regardless of the boys/girls split.
Verified directly — `_ready_for_confirmation_session`'s summary and payload
tests use this exact composition and assert group_size=4 with both rooms
present.

**Known, pre-existing limitation, not introduced or fixed by this phase**:
if the two rooms have *different* room **types** (e.g. boys=Double,
girls=Triple), `session.room_type` (the scalar `_pricing_breakdown_text`
actually reads) only ever reflects whichever room was recorded *first* —
`_merge_hints` sets it once and never updates it from a later, different-
type addition. Pricing would then apply that one type's per-person rate to
the *entire* group, including travelers actually in the other room type.
This is a genuine gap, but it requires a per-room-type-weighted pricing
model change in `workflow_policy.py` — out of this phase's "do not redesign
the booking architecture" scope, and not what the reported transcript
exercised (both rooms were the same type). Recorded as remaining debt
(section 11).

## 8. State Transitions (Task 7, traced and tested)

`trip selected → group size=4 → 2 boys+2 girls → choose room → add second
room → unrelated question → resume booking → correct room → summary →
confirmation` — covered by
`test_group_composition_and_rooms_together`,
`test_adding_a_second_room_on_a_later_turn`,
`test_interruption_between_room_selections_preserves_both`,
`test_unrelated_question_between_room_selections_preserves_both`,
`test_replacing_an_existing_room_with_a_correction_signal`, and the two
summary/payload tests together. At every step: active trip, group size,
gender composition, and room requirements were confirmed to survive an
interruption or an unrelated question untouched — no interruption handler
in this codebase touches `room_requirements`, so this was mostly a
verification exercise, not a fix (the fix was the extraction/merge layer
itself, which those turns also exercise). `test_trip_change_after_room_selection_resets_requirements`
confirms the one case where room requirements *should* reset — an actual
trip change — via the existing, unchanged `_clear_booking_dependent_state`.

## 9. Tests Added

`tests/test_phase12_booking_state.py` — 14 tests, all passing, using real
turn sequences via `_send()` against a custom trip with mixed
boys/girls-double/triple inventory (not hand-constructed
`room_requirements` dicts):

1. `test_two_different_gender_double_rooms_in_one_message`
2. `test_two_room_requirements_are_not_collapsed_to_one`
3. `test_adding_a_second_room_on_a_later_turn`
4. `test_replacing_an_existing_room_with_a_correction_signal`
5. `test_correcting_one_room_leaves_the_other_untouched`
6. `test_correcting_both_rooms_at_once_to_a_single_gender` (global correction)
7. `test_group_composition_and_rooms_together`
8. `test_interruption_between_room_selections_preserves_both`
9. `test_unrelated_question_between_room_selections_preserves_both`
10. `test_trip_change_after_room_selection_resets_requirements`
11. `test_final_confirmation_summary_contains_both_rooms`
12. `test_final_booking_payload_contains_both_rooms` (asserts on the actual
    write-executor payload, not text)
13. `test_no_room_requirement_silently_overwritten_without_a_correction_signal`
14. `test_production_transcript_reproduction` (Task 10 — the exact reported
    phrase, through to a simulated confirmed booking payload with both
    rooms)

## 10. Task 9 — Compound Answer Parsing Relevance

Reviewed whether Phase 11's known "تمام، شباب"-style compound-answer gap
(a deliberately strict whole-message match in `_apply_required_step_capture`)
is the root cause here. **It is not.** Group/room extraction runs entirely
through `_extract_hints`/`_merge_hints`'s opportunistic pipeline, which scans
the *full free-text message* for keywords/numbers on every turn regardless
of the currently-required step or how the sentence is phrased — it was
never gated by a strict per-step match at all. The actual root causes
(sections 2, 5) are unrelated to compound-answer parsing. No change was made
to `_apply_required_step_capture`, and no free-form sentence was made to
automatically satisfy a required field as a result of this phase's changes.

## 11. Regression Results

Run in the specified order:

| Step | Result |
|---|---|
| New Phase 12 tests | 14 passed |
| Phase 11 tests + `test_agent_conversation_reliability.py` | 129 passed |
| `test_golden_transcript_regressions.py` | 190 passed |
| Phase 3A–12 target suites (13 files) | 429 passed, 1 failed (pre-existing) |
| Broader production-agent sweep | 559 passed, 2 failed (pre-existing) |
| Full `pytest tests/` | **1021 passed, 5 failed**, 55 subtests passed, in 2543.11s (0:42:23), clean exit |

Every failure, classified:

- `test_phase4_traveler_management.py::...test_detail_shows_related_records_and_update_syncs_back` — established pre-existing baseline (Phase 3D), Flask/SQLAlchemy test-ordering artifact.
- `test_phase6_passport_attachments.py::...test_payment_screenshot_upload_does_not_update_passport_fields` — established pre-existing baseline (Phase 3D), same artifact family.
- `test_tier2_field_validation.py::test_passport_country_mismatch_with_stated_nationality_does_not_block` — established pre-existing baseline (Phase 3D), unrelated stage-transition assertion bug.
- `test_phase8_production_readiness_audit.py::test_phase8_duplicate_instagram_webhook_delivery_is_deduplicated` — documented order-dependent artifact (Phase 10/11), passes in isolation, touches no file this phase changed.
- `test_rate_limiting.py::AgentApiRateLimitTests::test_session_creation_is_rate_limited` — same family (Phase 10/11), Flask-Limiter shared counters, touches no file this phase changed.

Total failure count (5) exactly matches the established baseline from every
prior full-suite run. No file this phase edited
(`tool_calling_runtime.py`, the new test file) is implicated by any of the 5.
Not fixed, per established discipline and this phase's explicit scope.

## 12. Files Changed

Production:

- `services/ai_agent/ai_agent_app/agent/tool_calling_runtime.py`:
  - `_split_glued_arabic_connector` (new) — fixes the "و2" word-boundary bug.
  - `_extract_mixed_people_counts` — added "رجال"/"رجالة"/"ولاد" to boys
    terms; added Arabic number words to its number pattern; applies the
    glued-connector fix.
  - `_extract_room_requirements` — added Arabic room-type words
    (دابل/دبل/تريبل/سنجل/فردي); added "رجال"/"رجالة"/"ولاد" to boys terms
    (both the regex-loop tuple and the `has_boys` fallback check); applies
    the glued-connector fix.
  - `_consolidate_room_requirements`, `_is_room_correction_signal`,
    `_global_room_gender_correction`, `_merge_room_requirements` (all new)
    — addition/replacement/global-correction semantics, section 5.
  - `_merge_hints` — now takes `clean_text` (one new parameter, one call
    site updated) and calls `_merge_room_requirements` instead of
    overwriting `session.room_requirements` wholesale.

Tests:

- `tests/test_phase12_booking_state.py` (new) — 14 tests.

No change to `session_flow.py`, `gemini_agent.py`, `workflow_policy.py`,
`lexicon.py`, `server.py`, ActionValidator, provider APIs, database schema,
webhook idempotency, or durable session persistence/locking.

## 13. Remaining Debt

- **Mixed room *types* pricing** (section 7): if two rooms of genuinely
  different types (e.g. Double + Triple) are both requested, the pricing
  breakdown's single `room_type` scalar only reflects whichever was
  recorded first, potentially mispricing travelers in the other room type.
  Real, but not the reported defect (which used one uniform type); requires
  a `workflow_policy.py` pricing-model change, out of this phase's scope.
- **Compound acknowledgment-prefixed answers** ("تمام، شباب") remain a
  separate, Phase-11-documented limitation in the strict per-step capture
  path — confirmed unrelated to this phase's bug (section 10), not touched.
- The primary regex-based per-count loop in `_extract_room_requirements`
  (explicit "N rooms for gender" phrasing) remains English-phrasing-biased;
  Arabic phrasing is handled entirely by the "1 room per mentioned gender"
  fallback plus the now-fixed gender-term/glued-connector detection. This
  is sufficient for every case in Task 3's minimum list (verified) but would
  not correctly extract an explicit Arabic per-gender count like "3 غرف
  للبنات وغرفة واحدة للشباب" (3 girls rooms, 1 boys room) — it would still
  default to 1-each via the fallback. Not reported in production, not in
  Task 3's required minimum; flagged for a future phase if it surfaces.
- The pre-existing test-ordering flakiness family (5 established failures)
  is unchanged and was not investigated further or fixed, per explicit
  scope.

## 14. GO / NO-GO Recommendation

## GO

- All 14 new tests pass against real turn sequences and the actual booking
  payload sent to the write executor, not mocked-away mechanisms or
  displayed-text-only assertions.
- Zero regressions in Phase 11's own test suite, the golden transcript
  suite, the Phase 3A–12 target suites, or the broader production-agent
  sweep.
- Full suite: 1021 passed, 5 failed — failure count matches the established
  baseline exactly, and every failure was individually confirmed
  pre-existing and unrelated to any file this phase touched.
- The fix is additive and narrowly scoped: no redesign of the booking
  architecture, no new competing data representation, no change to
  ActionValidator, provider APIs, database schema, webhook idempotency, or
  durable session persistence/locking. The existing `room_requirements`
  list-of-dicts structure, summary renderer, and booking payload were all
  confirmed already correct and are unchanged.
- The one demonstrated defect class (silent overwrite) is now structurally
  prevented by `_merge_room_requirements`'s explicit addition/replacement/
  global-correction rules, with a regression test for each rule.

Stop after Phase 12. Do not automatically begin Phase 13.
