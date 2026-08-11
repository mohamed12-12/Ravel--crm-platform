# Phase 8 - Testing, documentation, and rollout sequencing

## What this phase delivers

Each client-requested phase is implemented, tested, documented, committed, deployed, and live-verified in a safe sequence with concrete evidence recorded.

## Current state (investigated, not assumed)

- The repo has a broad pytest suite under `tests/`, including booking write verification, write-result boundaries, pipeline integrity, Instagram webhook tests, trip media tests, UI layout regressions, and RBAC/IDOR tests.
- The project already maintains `TEST_REPORT.md` and `ravel_agent_master_discovery_report.md` in the repo root.
- The current worktree had unrelated dirty import-staging JSON files before this documentation pass; those were not touched.
- The existing discovery report documents prior production/live verification practice and warns against inferred claims without source reading (`ravel_agent_master_discovery_report.md:323-325`, `ravel_agent_master_discovery_report.md:718-722`).

## Confirmed requirements this phase must satisfy

From the provided task attachment: determine and follow safe implementation order, test and live-verify each phase against the real deployed app, report after each phase, commit and push after each phase, and update `ravel_agent_master_discovery_report.md` with built work and findings.

## Working assumptions (for Phases 6 and 7 specifically)

Not applicable.

## Design approach

- Treat Phase 8 as an ongoing checklist, not a single final code change.
- For each implementation phase, record: code touched, migrations run, focused tests, full suite result, deployment command/result, and live verification result.
- Do not mark any phase complete until its own phase document has real pass/fail test results and live deployed evidence.
- Keep unrelated dirty files out of commits.
- If production verification cannot be performed, mark the phase incomplete or explicitly blocked rather than calling it done.

## Dependencies on other phases

Depends on all implementation phases for final completion. Starts immediately as documentation discipline.

## Risks specific to this phase

The main risk is documentation drift: claiming tests/deploy/live verification that did not happen. This documentation pass intentionally leaves those sections as "not run" because no code was changed and no deployment was performed.

## Tests

Not run. No code was changed in this documentation-only pass.

## Live verification

Not run. No deployed app behavior was changed or verified in this documentation-only pass.

